"""AnthropicProvider — native tool-calling, primary v1 target for PRIMARY (§4.1).

This is the ONLY cloud provider built in the first pass (engineering-spec §10).
OpenAI/Google adapters come later (design-doc Phase 4).

Talks to the Anthropic Messages API over ``httpx`` (the declared HTTPS
dependency — no vendored SDK). It translates Addison's ``ToolDefinition`` list
into ``tools`` blocks and maps ``tool_use`` response blocks back to
``ToolCallRequest``. The API key is fetched from the OS keychain at call time
via the shell (§5) and used locally for one request only — it is never stored
on the instance or anywhere longer-lived (§8.3).

Note the module-boundary rule (CLAUDE.md §2): ``providers/`` must not import
from ``tools/``. Tool definitions are therefore duck-typed here — send() only
reads ``.id``, ``.description`` and ``.parameters_schema`` off each tool.
"""

from __future__ import annotations

import json

import httpx

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

_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_MAX_TOKENS = 4096
_TIMEOUT_SECONDS = 60.0

# Plain-language, never-contains-the-key message for a missing/unset key.
_NO_KEY_MESSAGE = (
    "No API key is set up yet. Add your Anthropic API key in Settings to start chatting."
)
# A key with a character that can't go in an HTTP header (a smart quote, an
# ellipsis from a truncated copy, a non-breaking space). Without this check it
# would crash header encoding below as an exception with no plain message.
_MALFORMED_KEY_MESSAGE = (
    "Your API key has a stray character in it — that can happen when copying. "
    "Open Settings and paste the whole key again."
)


class AnthropicProvider:
    def __init__(
        self,
        model: str = "claude-opus-4-8",
        api_key_getter=None,
        client=None,
        adaptive_thinking: bool = False,
        supported_effort=(),
    ) -> None:
        self._model = model
        self._api_key_getter = api_key_getter  # callable -> str, hits the shell/keychain
        # Optional injected httpx.Client (tests pass one wired to a MockTransport).
        # When None, send() creates and closes a client per request.
        self._client = client
        # Catalog-driven per-model knobs (models_catalog.py). ``adaptive_thinking``
        # adds ``thinking: {"type": "adaptive"}``; ``supported_effort`` is the set of
        # effort ids this model accepts — an effort NOT in it is silently dropped so
        # an unsupported ``output_config`` is never sent (which would error).
        self._adaptive_thinking = adaptive_thinking
        self._supported_effort = tuple(supported_effort)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=200_000,
            supports_streaming=True,
            runs_off_device=False,
            vision=True,        # Claude models can analyze images
            # The Messages API's own word for "I ran out of output room", passed
            # through verbatim by both response paths below (``stop_reason``).
            truncation_finish_reasons=("max_tokens",),
        )

    def send(
        self,
        messages: list[Message],
        tools: list,
        effort: str | None = None,
        timeout: float | None = None,
        on_delta=None,
    ) -> ModelResponse:
        # Fetch the key fresh for THIS request; keep it in a local only (§5, §8.3).
        api_key = self._resolve_key()

        body: dict = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "messages": _translate_history(messages),
        }
        # Per-model reasoning knobs (models_catalog.py). Adaptive thinking is a fixed
        # per-model choice; the effort "answer style" is per-message but ONLY sent to
        # a model that supports it — an unsupported effort is dropped, never sent.
        if self._adaptive_thinking:
            body["thinking"] = {"type": "adaptive"}
        if effort is not None and effort in self._supported_effort:
            body["output_config"] = {"effort": effort}
        # A leading role="system" message maps to the Messages API top-level
        # `system` param, not into the messages list (§4.6 injects the Setup
        # Assistant prompt this way for a turn). Omitted entirely when absent, so
        # the no-system path is byte-for-byte unchanged.
        system = _extract_system(messages)
        if system:
            body["system"] = system
        tool_blocks = _translate_tools(tools)
        if tool_blocks:  # omit the key entirely when there are no tools
            body["tools"] = tool_blocks

        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        if on_delta is not None:
            return self._send_streaming(headers, body, timeout, on_delta)

        response = self._post(headers, body, timeout)

        if response.status_code >= 400:
            # Never echo the response body or the key — just a plain next step. The
            # message is byte-identical to before; only the exception TYPE is new,
            # so the loop can tell "busy, try another" from "bad request" (D4).
            raise exception_for_http_status(
                response.status_code, _http_error_message(response.status_code),
                error_message_from_body(response),
            )

        return _translate_response(response.json())

    def _send_streaming(self, headers: dict, body: dict, timeout, on_delta) -> ModelResponse:
        """The same request with ``stream: true``, relaying text as it arrives.

        Returns the SAME ``ModelResponse`` the non-streaming path would: the
        orchestrator's tool loop, usage recording and fallback chain cannot tell
        the two apart, which is what keeps streaming a transport detail.
        """
        body = {**body, "stream": True}
        deadline = effective_timeout(timeout, _TIMEOUT_SECONDS)
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=deadline)
        try:
            with open_stream(
                client,
                _API_URL,
                headers=headers,
                body=body,
                deadline=deadline,
                # [MF-A] as on the non-streaming path: a caller-supplied deadline
                # means the attempt loop is driving, and its chain is the retry.
                allow_retry=timeout is None,
            ) as response:
                if response.status_code >= 400:
                    raise exception_for_http_status(
                        response.status_code, _http_error_message(response.status_code),
                        error_message_from_body(response),
                    )
                return _translate_stream(iter_sse_json(response), on_delta)
        except httpx.HTTPError:
            # Identical wording to _post: a stream that drops mid-answer is the
            # same problem to the reader as one that never opened.
            raise ProviderUnavailable(
                "Couldn't reach the Anthropic service. "
                "Check your internet connection and try again."
            ) from None
        finally:
            if injected is None:
                client.close()

    def _resolve_key(self) -> str:
        getter = self._api_key_getter
        if getter is None:
            raise ProviderAuthFailed(_NO_KEY_MESSAGE)
        api_key = getter()
        if api_key:
            api_key = api_key.strip()
        if not api_key:
            raise ProviderAuthFailed(_NO_KEY_MESSAGE)
        if not api_key.isascii() or not api_key.isprintable():
            raise ProviderAuthFailed(_MALFORMED_KEY_MESSAGE)
        return api_key

    def _post(self, headers: dict, body: dict, timeout: float | None = None) -> httpx.Response:
        deadline = effective_timeout(timeout, _TIMEOUT_SECONDS)
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=deadline)
        try:
            # POST: retry only when the request never reached the server (§8.3). The
            # per-call ``deadline`` is applied to the request itself so it holds even
            # for an injected client (tests / the fallback budget, [MF-A]).
            return request_with_retry(
                lambda: client.post(_API_URL, headers=headers, json=body, timeout=deadline),
                idempotent=False,
                # [MF-A] a caller-supplied deadline means the attempt loop is
                # driving; its chain is the retry — never double the budget here.
                allow_retry=timeout is None,
            )
        except httpx.HTTPError:
            # Network/timeout failure. Raise a clean message with no chained
            # exception so nothing about the request (headers included) leaks.
            raise ProviderUnavailable(
                "Couldn't reach the Anthropic service. "
                "Check your internet connection and try again."
            ) from None
        finally:
            if injected is None:
                client.close()


def _translate_tools(tools: list) -> list[dict]:
    return [
        {"name": d.id, "description": d.description, "input_schema": d.parameters_schema}
        for d in tools
    ]


def _extract_system(messages: list[Message]) -> str | None:
    """Pull role="system" messages out to the API's top-level `system` string.

    Returns None when there are none, so ``send()`` can omit the key and leave
    the existing (system-free) request shape untouched. Multiple system messages
    are joined, though in practice §4.6 injects exactly one."""
    parts = [m.content for m in messages if m.role == "system" and m.content]
    return "\n\n".join(parts) if parts else None


def _translate_history(messages: list[Message]) -> list[dict]:
    """Map Addison's flat message list to Anthropic's alternating turns.

    Assistant turns that requested tools become a text block (if any) followed
    by one ``tool_use`` block per call. Consecutive ``tool`` messages are the
    results for a single assistant turn, so they MUST merge into one ``user``
    message carrying every ``tool_result`` block — the API rejects them split
    across messages.
    """
    api_messages: list[dict] = []
    pending_results: list[dict] = []

    def flush_results() -> None:
        if pending_results:
            api_messages.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        if m.role == "system":
            # Carried by the top-level `system` param (_extract_system), never a
            # messages-list entry — the API rejects "system" as a message role.
            continue
        if m.role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": str(m.content),
                }
            )
            continue

        # Any non-tool message closes off a run of tool results.
        flush_results()

        if m.role == "user":
            api_messages.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            if m.tool_calls:
                content: list[dict] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for c in m.tool_calls:
                    content.append(
                        {"type": "tool_use", "id": c.id, "name": c.tool_id, "input": c.args}
                    )
                api_messages.append({"role": "assistant", "content": content})
            else:
                api_messages.append({"role": "assistant", "content": m.content})

    flush_results()
    return api_messages


def _translate_response(data: dict) -> ModelResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    for block in data.get("content", []):
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCallRequest(
                    id=block["id"],
                    tool_id=block["name"],
                    args=block.get("input", {}),
                )
            )
    text = "".join(text_parts) if text_parts else None
    return ModelResponse(
        text=text,
        tool_calls=tool_calls,
        finish_reason=data.get("stop_reason", "stop"),
        usage=_translate_usage(data.get("usage")),
    )


def _translate_stream(frames, on_delta) -> ModelResponse:
    """Fold the Messages API event stream into one ``ModelResponse``.

    The events that matter, and nothing else (an unknown event type is skipped,
    so a new one the API adds can never derail an answer in flight):

    * ``message_start``     — carries ``usage.input_tokens``
    * ``content_block_start``/``_delta``/``_stop`` — text and ``tool_use`` blocks,
      identified by ``index`` because they interleave
    * ``message_delta``     — the real ``stop_reason`` and final ``output_tokens``

    ``text_delta`` is the ONLY thing handed to ``on_delta``. A ``tool_use``
    block's ``input_json_delta`` is machinery — half-built JSON that means nothing
    to a reader — so it is accumulated silently and parsed when its block closes.
    """
    text_parts: list[str] = []
    # Keyed by block index: tool_use blocks arrive interleaved with text and are
    # completed out of order relative to their own start event.
    tool_blocks: dict[int, dict] = {}
    stop_reason = "stop"
    input_tokens: int | None = None
    output_tokens: int | None = None

    for frame in frames:
        event = frame.get("type")
        if event == "message_start":
            usage = (frame.get("message") or {}).get("usage") or {}
            if isinstance(usage.get("input_tokens"), int):
                input_tokens = usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), int):
                output_tokens = usage["output_tokens"]
        elif event == "content_block_start":
            block = frame.get("content_block") or {}
            if block.get("type") == "tool_use":
                tool_blocks[frame.get("index")] = {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "json": "",
                }
        elif event == "content_block_delta":
            delta = frame.get("delta") or {}
            kind = delta.get("type")
            if kind == "text_delta":
                piece = delta.get("text")
                if isinstance(piece, str) and piece:
                    text_parts.append(piece)
                    on_delta(piece)
            elif kind == "input_json_delta":
                block = tool_blocks.get(frame.get("index"))
                if block is not None and isinstance(delta.get("partial_json"), str):
                    block["json"] += delta["partial_json"]
            # Any other delta type (a thinking block among them) is deliberately
            # dropped: it is not part of the answer, so it must not be shown and
            # must not join the text the response returns.
        elif event == "message_delta":
            reason = (frame.get("delta") or {}).get("stop_reason")
            if isinstance(reason, str) and reason:
                stop_reason = reason
            usage = frame.get("usage") or {}
            if isinstance(usage.get("output_tokens"), int):
                output_tokens = usage["output_tokens"]
        elif event == "error":
            # The stream itself reported a failure partway through. Transient by
            # nature (overloaded_error is the common one), and the plain sentence
            # is the same one an HTTP 5xx gets.
            raise ProviderUnavailable(_http_error_message(500))

    tool_calls = [
        ToolCallRequest(
            id=block["id"],
            tool_id=block["name"],
            args=_parse_tool_input(block["json"]),
        )
        for _index, block in sorted(tool_blocks.items(), key=lambda kv: kv[0])
        if block.get("id") and block.get("name")
    ]
    text = "".join(text_parts) if text_parts else None
    usage = (
        Usage(input_tokens=input_tokens, output_tokens=output_tokens)
        if isinstance(input_tokens, int) and isinstance(output_tokens, int)
        else None
    )
    return ModelResponse(
        text=text, tool_calls=tool_calls, finish_reason=stop_reason, usage=usage
    )


def _parse_tool_input(raw: str) -> dict:
    """A streamed ``tool_use`` input, reassembled from its json deltas. An empty
    string is the wire's way of saying "no arguments"; anything unparseable is
    treated the same way rather than raising, so one malformed call cannot lose an
    answer that has already been shown — the tool then reports the missing
    argument through the ordinary result path."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _translate_usage(usage) -> Usage | None:
    """Map the Messages API ``usage`` block ({input_tokens, output_tokens}) to
    Addison's ``Usage``. None when absent or unreadable — never guessed."""
    if not isinstance(usage, dict):
        return None
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    if isinstance(inp, int) and isinstance(out, int):
        return Usage(input_tokens=inp, output_tokens=out)
    return None


def _http_error_message(status_code: int) -> str:
    if status_code in (401, 403):
        return "Your Anthropic API key was rejected. Check that it's entered correctly in Settings."
    if status_code == 429:
        return "Anthropic is busy right now (too many requests). Wait a moment and try again."
    if status_code >= 500:
        return "The Anthropic service had a problem. Please try again in a moment."
    return f"The request to Anthropic failed (status {status_code}). Please try again."
