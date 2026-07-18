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
connect time (main.py) and, like every cloud provider, is never proxied through
the webview.

Note the module-boundary rule (CLAUDE.md §2): ``providers/`` must not import from
``tools/``. Tool definitions are duck-typed — send() only reads ``.id``,
``.description`` and ``.parameters_schema`` off each tool.
"""

from __future__ import annotations

import json

import httpx

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
    ToolCallRequest,
)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_MAX_TOKENS = 4096
_TIMEOUT_SECONDS = 60.0

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
            # Matches AnthropicProvider: send() is a single non-streaming POST, but
            # the models themselves support streaming — the flag reports the model
            # capability, consistent across cloud providers.
            supports_streaming=True,
            runs_off_device=False,
            vision=True,        # modern GPT-class models can analyze images
        )

    def send(
        self, messages: list[Message], tools: list, effort: str | None = None
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

        response = self._post(headers, body)
        if response.status_code >= 400:
            # Never echo the response body or the key — just a plain next step.
            raise RuntimeError(self._http_error_message(response.status_code))
        return _translate_response(response.json())

    def _resolve_key(self) -> str:
        getter = self._api_key_getter
        api_key = getter() if getter is not None else ""
        if api_key:
            api_key = api_key.strip()
        if not api_key:
            if self._require_key:
                raise RuntimeError(_NO_KEY_MESSAGE)
            return ""   # keyless custom server: no Authorization header
        if not api_key.isascii() or not api_key.isprintable():
            raise RuntimeError(_MALFORMED_KEY_MESSAGE)
        return api_key

    def _post(self, headers: dict, body: dict) -> httpx.Response:
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=_TIMEOUT_SECONDS)
        url = f"{self._base_url}/chat/completions"
        try:
            return client.post(url, headers=headers, json=body)
        except httpx.HTTPError:
            # Network/timeout failure. Raise a clean message with no chained
            # exception so nothing about the request (headers included) leaks.
            raise RuntimeError(
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
    message = (choices[0].get("message") if choices else None) or {}
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
    if tool_calls:
        return ModelResponse(text=text, tool_calls=tool_calls, finish_reason="tool_use")
    return ModelResponse(text=text, tool_calls=[], finish_reason="stop")


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
def list_models(
    base_url: str, api_key_getter, client=None, *, require_key: bool = True
) -> list[str]:
    """``GET {base_url}/models`` — the "one tiny request" provider.connect makes to
    validate an OpenAI (or OpenAI-compatible) key/server, doubling as the model
    list for a custom server.

    Returns the model ids from the response ``data`` array. Raises a plain-language
    ``RuntimeError`` on a bad key, an unreachable host, or an unreadable body — the
    caller turns that into the card's error line. The key is fetched ONCE here and
    used only in the request header, never retained (§8.3).
    """
    key = _resolve_list_key(api_key_getter, require_key=require_key)
    headers = {}
    if key:
        headers["authorization"] = f"Bearer {key}"
    url = f"{(base_url or _DEFAULT_BASE_URL).rstrip('/')}/models"
    injected = client
    http = injected if injected is not None else httpx.Client(timeout=10.0)
    try:
        response = http.get(url, headers=headers)
    except httpx.HTTPError:
        raise RuntimeError(
            "Couldn't reach that server. Check the address and that it's running."
        ) from None
    finally:
        if injected is None:
            http.close()
    if response.status_code in (401, 403):
        raise RuntimeError("That key doesn't work. Check it and try again.")
    if response.status_code >= 400:
        raise RuntimeError("That server didn't accept the request. Check the address and try again.")
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
