"""OpenAIProvider — native tool-calling against the OpenAI Chat Completions API.

Talks to ``/v1/chat/completions`` over ``httpx`` (the declared HTTPS dependency —
no vendored SDK), the same house pattern as ``anthropic_provider.py``. It
translates Addison's ``ToolDefinition`` list into OpenAI ``tools`` blocks and maps
``tool_calls`` in the response back to ``ToolCallRequest``. The API key is fetched
from the OS keychain at call time via the shell (§5) and used for one request only
— it is never stored on the instance or anywhere longer-lived (§8.3).

This same class also backs the **OpenAI-compatible custom server** (a user's own
LAN model host): pass ``base_url`` to point it elsewhere and ``require_key=False``
so a keyless server sends no ``Authorization`` header. That base URL is the ONE
permitted plain-``http://`` case (localhost/LAN) — it is validated http(s):// at
connect time (``rpc/providers.py::_valid_http_url``) and, like every cloud
provider, is never proxied through the webview.

Note the module-boundary rule (CLAUDE.md §2): ``providers/`` must not import from
``tools/``. Tool definitions are duck-typed — send() only reads ``.id``,
``.description`` and ``.parameters_schema`` off each tool.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from agent_core import net_vetting
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderAuthFailed,
    ProviderCapabilities,
    ProviderUnavailable,
    ToolCallRequest,
    Usage,
    effective_timeout,
    error_message_from_body,
    exception_for_http_status,
    iter_sse_json,
    open_stream,
    request_with_retry,
)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_MAX_TOKENS = 4096
_TIMEOUT_SECONDS = 60.0
# The validating GET's own budget + hop limits (list_models). Short: it runs on a
# connect card the person is waiting on, so it gives up quickly.
_LIST_TIMEOUT_SECONDS = 10.0
# ...and a budget for the WHOLE pinned walk, because the per-socket timeout is not
# one: the walk tries up to MAX_ADDRESS_ATTEMPTS addresses per hop across
# _LIST_MAX_REDIRECTS + 1 hops, so 10s per socket is two minutes of waiting, and
# the one idempotent retry made it four. Someone is watching a connect card.
_LIST_TOTAL_SECONDS = 15.0
_LIST_MAX_REDIRECTS = 3
_LIST_MAX_URL_CHARS = 2048

# Plain sentences for the SSRF-pinned validation GET (step 4, D1/R1). The custom
# server is the user's OWN LAN model host, so the pin uses ``allow_private=True``
# and any port; these are the words shown when it can't be reached — the network
# string ("Couldn't reach that server") is the one the existing list_models tests
# pin, so it must stay exact. The status-code messages (bad key, refused) live in
# ``_list_on_final``, where the response is in hand.
_COULD_NOT_REACH_SERVER = "Couldn't reach that server. Check the address and that it's running."


class _RetryableReach(httpx.ConnectError):
    """A transient pin failure, wearing the httpx type ``request_with_retry`` retries
    on while still carrying its OWN plain sentence.

    Without the sentence, every transient outcome collapsed to "check the address
    and that it's running" — so a person whose server was merely SLOW was told to
    check the one thing that was correct, and ``_LIST_SENTENCES.took_too_long`` was
    unreachable copy. A sentence that can never be shown is a guard that defends
    nothing."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence
_LIST_SENTENCES = net_vetting.Sentences(
    no_url=_COULD_NOT_REACH_SERVER,
    not_a_web_link=_COULD_NOT_REACH_SERVER,
    not_allowed=_COULD_NOT_REACH_SERVER,
    odd_web_address=_COULD_NOT_REACH_SERVER,
    could_not_find_site=_COULD_NOT_REACH_SERVER,
    could_not_open="That server didn't accept the request. Check the address and try again.",
    could_not_reach=_COULD_NOT_REACH_SERVER,
    took_too_long="That server took too long to answer. Try again in a moment.",
    too_many_redirects="That server kept sending the request elsewhere, so Addison stopped.",
    dropped_secure_link=(
        "That server tried to switch to an unsecured connection, so Addison stopped."
    ),
)

_NO_KEY_MESSAGE = (
    "No API key is set up yet. Add your OpenAI API key in Settings to start chatting."
)
_MALFORMED_KEY_MESSAGE = (
    "Your API key has a stray character in it — that can happen when copying. "
    "Open Settings and paste the whole key again."
)


class OpenAIProvider:
    def __init__(
        self,
        model: str,
        api_key_getter=None,
        base_url: str = _DEFAULT_BASE_URL,
        client=None,
        *,
        require_key: bool = True,
        service_label: str = "OpenAI",
    ) -> None:
        self._model = model
        self._api_key_getter = api_key_getter  # callable -> str, hits the shell/keychain
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        # Optional injected httpx.Client (tests wire one to a MockTransport). When
        # None, send() creates and closes a client per request.
        self._client = client
        # An OpenAI-compatible custom server may run without a key (require_key=False):
        # a missing key then simply omits the Authorization header rather than erroring.
        self._require_key = require_key
        # Names the service in plain-language network errors ("Couldn't reach OpenAI"
        # / "Couldn't reach the server").
        self._service_label = service_label

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=128_000,
            # Honoured: send() streams when the caller passes ``on_delta``, and
            # falls back to a single POST when it does not.
            supports_streaming=True,
            runs_off_device=False,
            vision=True,        # modern GPT-class models can analyze images
            # chat.completions says "length" when the answer hit ``max_tokens``,
            # and both response paths below keep that word as it arrived. The same
            # adapter serves the custom OpenAI-compatible server (a different base
            # URL and label, one class), so a compatible server that reports the
            # standard word is read correctly too.
            truncation_finish_reasons=("length",),
        )

    def send(
        self,
        messages: list[Message],
        tools: list,
        effort: str | None = None,
        timeout: float | None = None,
        on_delta=None,
    ) -> ModelResponse:
        # ``effort`` is an Anthropic "answer style" (§4.1.1); OpenAI has no such
        # per-message control here, so it is accepted and ignored for a uniform call.
        api_key = self._resolve_key()

        body: dict = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "messages": _translate_history(messages),
        }
        tool_blocks = _translate_tools(tools)
        if tool_blocks:
            body["tools"] = tool_blocks

        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"

        if on_delta is not None:
            return self._send_streaming(headers, body, timeout, on_delta)

        response = self._post(headers, body, timeout)
        if response.status_code >= 400:
            # Never echo the response body or the key — just a plain next step. Same
            # message as before; the new exception TYPE only lets the loop tell
            # "busy, try another" from "bad request" / "bad key" (D4).
            raise exception_for_http_status(
                response.status_code, self._http_error_message(response.status_code),
                error_message_from_body(response),
            )
        return _translate_response(response.json())

    def _send_streaming(self, headers: dict, body: dict, timeout, on_delta) -> ModelResponse:
        """The same request with ``stream: true``, relaying text as it arrives.

        ``stream_options.include_usage`` is what keeps the ``usage_log`` row honest:
        without it a streamed completion reports no token counts at all, and §4.8's
        substrate would quietly lose every streamed turn. A server that ignores the
        option (an OpenAI-compatible custom endpoint may) simply yields no usage, and
        ``ModelResponse.usage`` stays None — never a guess.
        """
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        deadline = effective_timeout(timeout, _TIMEOUT_SECONDS)
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=deadline)
        url = f"{self._base_url}/chat/completions"
        try:
            with open_stream(
                client,
                url,
                headers=headers,
                body=body,
                deadline=deadline,
                allow_retry=timeout is None,
            ) as response:
                if response.status_code >= 400:
                    raise exception_for_http_status(
                        response.status_code, self._http_error_message(response.status_code),
                        error_message_from_body(response),
                    )
                return _translate_stream(iter_sse_json(response), on_delta)
        except httpx.HTTPError:
            raise ProviderUnavailable(
                f"Couldn't reach {self._service_label}. "
                "Check your internet connection and try again."
            ) from None
        finally:
            if injected is None:
                client.close()

    def _resolve_key(self) -> str:
        getter = self._api_key_getter
        api_key = getter() if getter is not None else ""
        if api_key:
            api_key = api_key.strip()
        if not api_key:
            if self._require_key:
                raise ProviderAuthFailed(_NO_KEY_MESSAGE)
            return ""   # keyless custom server: no Authorization header
        if not api_key.isascii() or not api_key.isprintable():
            raise ProviderAuthFailed(_MALFORMED_KEY_MESSAGE)
        return api_key

    def _post(self, headers: dict, body: dict, timeout: float | None = None) -> httpx.Response:
        deadline = effective_timeout(timeout, _TIMEOUT_SECONDS)
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=deadline)
        url = f"{self._base_url}/chat/completions"
        try:
            # POST: retry only when the request never reached the server (§8.3). The
            # per-call ``deadline`` rides on the request so it holds for an injected
            # client too ([MF-A]).
            return request_with_retry(
                lambda: client.post(url, headers=headers, json=body, timeout=deadline),
                idempotent=False,
                # [MF-A] a caller-supplied deadline means the attempt loop is
                # driving; its chain is the retry — never double the budget here.
                allow_retry=timeout is None,
            )
        except httpx.HTTPError:
            # Network/timeout failure. Raise a clean message with no chained
            # exception so nothing about the request (headers included) leaks.
            raise ProviderUnavailable(
                f"Couldn't reach {self._service_label}. "
                "Check your internet connection and try again."
            ) from None
        finally:
            if injected is None:
                client.close()

    def _http_error_message(self, status_code: int) -> str:
        if status_code in (401, 403):
            return "That key doesn't work. Check it and try again."
        if status_code == 429:
            return f"{self._service_label} is busy right now (too many requests). Wait a moment and try again."
        if status_code >= 500:
            return f"The {self._service_label} service had a problem. Please try again in a moment."
        return f"The request to {self._service_label} failed (status {status_code}). Please try again."


# --- request/response translation (OpenAI chat.completions shape) ----------
def _translate_tools(tools: list) -> list[dict]:
    # Duck-typed — providers/ must not import tools/ (module-boundary rule).
    return [
        {
            "type": "function",
            "function": {
                "name": d.id,
                "description": d.description,
                "parameters": d.parameters_schema,
            },
        }
        for d in tools
    ]


def _translate_history(messages: list[Message]) -> list[dict]:
    """Map Addison's flat message list to OpenAI's ``messages`` array.

    OpenAI uses inline ``system``/``user``/``assistant``/``tool`` roles. An
    assistant turn that requested tools carries a ``tool_calls`` array (arguments
    are a JSON *string*); each tool result is a ``tool`` message keyed by
    ``tool_call_id`` — the same id the assistant's ``tool_calls`` entry used.
    """
    out: list[dict] = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            entry: dict = {"role": "assistant", "content": m.content or ""}
            entry["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.tool_id, "arguments": json.dumps(c.args)},
                }
                for c in m.tool_calls
            ]
            out.append(entry)
        elif m.role == "tool":
            out.append(
                {"role": "tool", "tool_call_id": m.tool_call_id, "content": str(m.content)}
            )
        else:
            # system / user / plain assistant
            out.append({"role": m.role, "content": m.content or ""})
    return out


def _translate_response(data: dict) -> ModelResponse:
    choices = data.get("choices") or []
    choice = (choices[0] if choices else None) or {}
    message = choice.get("message") or {}
    text = message.get("content") or None
    tool_calls: list[ToolCallRequest] = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        tool_calls.append(
            ToolCallRequest(
                id=raw.get("id") or name,
                tool_id=name,
                args=_parse_arguments(fn.get("arguments")),
            )
        )
    usage = _translate_usage(data.get("usage"))
    if tool_calls:
        return ModelResponse(
            text=text, tool_calls=tool_calls, finish_reason="tool_use", usage=usage
        )
    # The choice's own ``finish_reason``, kept as it arrived — "length" is how this
    # API says the answer ran out of output room, and collapsing it to "stop" (which
    # this path did until 2026-08-22) threw that away before anyone could read it.
    # The streamed path already kept it; the two now agree.
    reason = choice.get("finish_reason")
    if not (isinstance(reason, str) and reason):
        reason = "stop"
    return ModelResponse(text=text, tool_calls=[], finish_reason=reason, usage=usage)


def _translate_stream(frames, on_delta) -> ModelResponse:
    """Fold a chat.completions SSE stream into one ``ModelResponse``.

    Each frame carries ``choices[0].delta``. Text arrives as ``delta.content``;
    tool calls arrive as ``delta.tool_calls`` entries keyed by ``index``, whose
    ``function.arguments`` is a JSON string built up a fragment at a time (and
    whose ``id``/``name`` appear only on the first fragment, so they are recorded
    and not overwritten by the later ones). Only ``delta.content`` reaches the
    sink; argument fragments are machinery.

    Frames with an empty ``choices`` list are normal — that is how the usage-only
    final frame arrives when ``include_usage`` is on.
    """
    text_parts: list[str] = []
    pending: dict[int, dict] = {}
    finish_reason: str | None = None
    usage: Usage | None = None

    for frame in frames:
        frame_usage = _translate_usage(frame.get("usage"))
        if frame_usage is not None:
            usage = frame_usage
        choices = frame.get("choices") or []
        if not choices:
            continue
        choice = choices[0] or {}
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            finish_reason = reason
        delta = choice.get("delta") or {}
        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            text_parts.append(piece)
            on_delta(piece)
        for raw in delta.get("tool_calls") or []:
            if not isinstance(raw, dict):
                continue
            slot = pending.setdefault(
                raw.get("index") or 0, {"id": None, "name": None, "arguments": ""}
            )
            if isinstance(raw.get("id"), str) and raw["id"]:
                slot["id"] = raw["id"]
            fn = raw.get("function") or {}
            if isinstance(fn.get("name"), str) and fn["name"]:
                slot["name"] = fn["name"]
            if isinstance(fn.get("arguments"), str):
                slot["arguments"] += fn["arguments"]

    tool_calls = [
        ToolCallRequest(
            id=slot["id"] or slot["name"],
            tool_id=slot["name"],
            args=_parse_arguments(slot["arguments"]),
        )
        for _index, slot in sorted(pending.items(), key=lambda kv: kv[0])
        if slot["name"]
    ]
    text = "".join(text_parts) if text_parts else None
    if tool_calls:
        return ModelResponse(
            text=text, tool_calls=tool_calls, finish_reason="tool_use", usage=usage
        )
    return ModelResponse(
        text=text, tool_calls=[], finish_reason=finish_reason or "stop", usage=usage
    )


def _translate_usage(usage) -> Usage | None:
    """Map the chat.completions ``usage`` block ({prompt_tokens, completion_tokens})
    to Addison's ``Usage``. None when absent or unreadable — never guessed."""
    if not isinstance(usage, dict):
        return None
    inp = usage.get("prompt_tokens")
    out = usage.get("completion_tokens")
    if isinstance(inp, int) and isinstance(out, int):
        return Usage(input_tokens=inp, output_tokens=out)
    return None


def _parse_arguments(raw) -> dict:
    """OpenAI tool-call ``arguments`` arrive as a JSON string. Parse with
    ``json.loads`` only — NEVER ``eval`` (§8.1) — and degrade a malformed or
    non-object payload to ``{}`` rather than raising mid-turn."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --- connect-time model listing (main.py drives this for validation) -------
def _list_parse(response: httpx.Response) -> list[str]:
    """Turn a validated 2xx model-listing response into its model ids. 4xx/5xx are
    handled by ``list_models`` (so the idempotent 5xx retry can see the status);
    this only ever runs on a response that already cleared that."""
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("That server's reply couldn't be read.") from None
    entries = data.get("data") if isinstance(data, dict) else None
    ids: list[str] = []
    for entry in entries or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
            ids.append(entry["id"])
    return ids


def _list_read_response(response: httpx.Response, _logical: str) -> httpx.Response:
    """The pin's ``on_final``: pull the body inside the stream context and hand the
    Response back so ``list_models`` can inspect its status (streamed responses need
    ``.read()`` before ``.json()``). ANY non-redirect status is returned — the
    status→message mapping and the idempotent 5xx retry both live in
    ``list_models``, not here."""
    response.read()
    return response


def list_models(
    base_url: str,
    api_key_getter,
    client=None,
    *,
    require_key: bool = True,
    resolve: Callable[[str], list[str]] | None = None,
) -> list[str]:
    """``GET {base_url}/models`` — the "one tiny request" provider.connect makes to
    validate an OpenAI (or OpenAI-compatible) key/server, doubling as the model
    list for a custom server.

    GLOBAL note (step 4, D1/R1): this request goes to a base URL a user (or, via
    the add-by-prompt card, a model-influenced utterance) can point anywhere, so it
    is issued through the SHARED SSRF-safe pin (``net_vetting.open_vetted``) — the
    exact mechanism ``read_web_page`` uses: resolve → vet → connect to the vetted
    IP with the hostname in Host + TLS SNI → follow no redirects → re-vet each hop.
    The custom server is legitimately on the LAN, so the pin runs with
    ``allow_private=True`` and any port (``require_default_port=False``); the pin
    still closes DNS rebinding (a public-looking hostname cannot swap to a different
    address between vet and connect) and the redirect gap. ``resolve`` is injectable
    so tests can stub DNS (MockTransport intercepts below name resolution).

    A GET has no side effects, so it keeps its one idempotent retry via
    ``request_with_retry``. What it does NOT keep is an unbounded thing to retry:
    the pinned walk itself loops over up to ``MAX_ADDRESS_ATTEMPTS`` addresses per
    hop across every redirect, so at 10s a socket it was already a two-minute wait
    and the retry made it four. ``total_timeout`` bounds the whole walk, so the
    retry now doubles a bounded number while a person watches a connect card.

    Returns the model ids; raises a plain-language ``RuntimeError`` on a bad key, an
    unreachable host, or an unreadable body. The key is fetched ONCE here and used
    only in the request header, never retained (§8.3)."""
    key = _resolve_list_key(api_key_getter, require_key=require_key)
    # The key rides in ``credential_headers``, NOT ``base_headers``: the pin drops
    # credential headers the moment a redirect leaves the origin they were aimed
    # at. Putting it in ``base_headers`` handed the user's key to whatever host a
    # hostile 302 named — that shipped, and this split is the fix.
    credentials = {"authorization": f"Bearer {key}"} if key else {}
    base = f"{(base_url or _DEFAULT_BASE_URL).rstrip('/')}"
    url = f"{base}/models"
    injected = client
    # trust_env=False ONLY for a user-supplied address, for the same reason
    # read_web_page uses it: a proxy would sit between the address that was vetted
    # and the address that is contacted, and that destination is user/model-chosen.
    # The stock OpenAI endpoint is a module CONSTANT, not user input, and it must
    # keep honouring the user's proxy environment — connecting an OpenAI key behind
    # a corporate proxy has to work, and ``send()`` honours it too. Disabling it
    # there would have been a silent freeze break, not a hardening.
    own_address = base != _DEFAULT_BASE_URL
    http = injected if injected is not None else httpx.Client(trust_env=not own_address)
    resolver = resolve if resolve is not None else net_vetting.resolve_host

    def _attempt() -> httpx.Response:
        try:
            return net_vetting.open_vetted(
                http,
                url,
                resolve=resolver,
                on_final=_list_read_response,
                sentences=_LIST_SENTENCES,
                base_headers={},
                credential_headers=credentials,
                allow_private=True,           # the user's own LAN model host is legitimate
                require_default_port=False,   # ...on whatever port it runs (:11434, :1234)
                max_url_chars=_LIST_MAX_URL_CHARS,
                max_redirects=_LIST_MAX_REDIRECTS,
                timeout=_LIST_TIMEOUT_SECONDS,
                total_timeout=_LIST_TOTAL_SECONDS,
            )
        except net_vetting.VettingError as exc:
            if exc.retryable:
                # A transient network failure — re-raise as the httpx error
                # request_with_retry gives its one idempotent retry to. Safe to
                # retry ONLY because ``total_timeout`` bounds a whole walk: without
                # it the walk itself was already MAX_ADDRESS_ATTEMPTS x hops
                # sockets, and the retry made that a two-minute wait into a
                # four-minute one, on a card the person is watching.
                raise _RetryableReach(str(exc)) from None
            # A settled refusal (blocked address, redirect loop, malformed URL) —
            # no retry could help, so surface the plain sentence at once.
            raise RuntimeError(str(exc)) from None

    try:
        response = request_with_retry(_attempt, idempotent=True)
    except httpx.HTTPError as exc:
        # The pin's own sentence when it had one (a slow server is told it was
        # slow); the generic reach failure otherwise.
        raise RuntimeError(getattr(exc, "sentence", _COULD_NOT_REACH_SERVER)) from None
    finally:
        if injected is None:
            http.close()
    if response.status_code in (401, 403):
        raise RuntimeError("That key doesn't work. Check it and try again.")
    if response.status_code >= 400:
        raise RuntimeError("That server didn't accept the request. Check the address and try again.")
    return _list_parse(response)


def _resolve_list_key(api_key_getter, *, require_key: bool) -> str:
    key = api_key_getter() if api_key_getter is not None else ""
    key = key.strip() if key else ""
    if not key:
        if require_key:
            raise RuntimeError(_NO_KEY_MESSAGE)
        return ""
    if not key.isascii() or not key.isprintable():
        raise RuntimeError(_MALFORMED_KEY_MESSAGE)
    return key
