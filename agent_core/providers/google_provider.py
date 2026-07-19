"""GoogleProvider — native tool-calling against the Gemini API (v1beta).

Talks to ``.../v1beta/models/{model}:generateContent`` over ``httpx`` (the
declared HTTPS dependency — no vendored SDK), the same house pattern as
``anthropic_provider.py``. It translates Addison's ``ToolDefinition`` list into
Gemini ``functionDeclarations`` and maps ``functionCall`` response parts back to
``ToolCallRequest``. The API key is fetched from the OS keychain at call time via
the shell (§5), sent in the ``x-goog-api-key`` header, and used for one request
only — never stored on the instance or anywhere longer-lived (§8.3).

Note the module-boundary rule (CLAUDE.md §2): ``providers/`` must not import from
``tools/``. Tool definitions are duck-typed — send() only reads ``.id``,
``.description`` and ``.parameters_schema`` off each tool.
"""

from __future__ import annotations

import uuid

import httpx

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
    ToolCallRequest,
)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_TIMEOUT_SECONDS = 60.0

_NO_KEY_MESSAGE = (
    "No API key is set up yet. Add your Google API key in Settings to start chatting."
)
_MALFORMED_KEY_MESSAGE = (
    "Your API key has a stray character in it — that can happen when copying. "
    "Open Settings and paste the whole key again."
)


class GoogleProvider:
    def __init__(self, model: str, api_key_getter=None, client=None) -> None:
        self._model = model
        self._api_key_getter = api_key_getter  # callable -> str, hits the shell/keychain
        self._client = client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=1_000_000,
            supports_streaming=True,
            runs_off_device=False,
            vision=True,        # Gemini models can analyze images
        )

    def send(
        self, messages: list[Message], tools: list, effort: str | None = None
    ) -> ModelResponse:
        # ``effort`` is an Anthropic "answer style" (§4.1.1); Gemini has no such
        # per-message control here, so it is accepted and ignored for a uniform call.
        api_key = self._resolve_key()

        body: dict = {"contents": _translate_history(messages)}
        system = _extract_system(messages)
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        tool_blocks = _translate_tools(tools)
        if tool_blocks:
            body["tools"] = tool_blocks

        headers = {"x-goog-api-key": api_key, "content-type": "application/json"}
        response = self._post(headers, body)
        if response.status_code >= 400:
            raise RuntimeError(_http_error_message(response.status_code))
        return _translate_response(response.json())

    def _resolve_key(self) -> str:
        getter = self._api_key_getter
        if getter is None:
            raise RuntimeError(_NO_KEY_MESSAGE)
        api_key = getter()
        if api_key:
            api_key = api_key.strip()
        if not api_key:
            raise RuntimeError(_NO_KEY_MESSAGE)
        if not api_key.isascii() or not api_key.isprintable():
            raise RuntimeError(_MALFORMED_KEY_MESSAGE)
        return api_key

    def _post(self, headers: dict, body: dict) -> httpx.Response:
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=_TIMEOUT_SECONDS)
        url = f"{_BASE_URL}/models/{self._model}:generateContent"
        try:
            return client.post(url, headers=headers, json=body)
        except httpx.HTTPError:
            raise RuntimeError(
                "Couldn't reach Google. Check your internet connection and try again."
            ) from None
        finally:
            if injected is None:
                client.close()


# --- request/response translation (Gemini generateContent shape) -----------
def _translate_tools(tools: list) -> list[dict]:
    # Duck-typed — providers/ must not import tools/ (module-boundary rule).
    declarations = [
        {"name": d.id, "description": d.description, "parameters": d.parameters_schema}
        for d in tools
    ]
    return [{"functionDeclarations": declarations}] if declarations else []


def _extract_system(messages: list[Message]) -> str | None:
    parts = [m.content for m in messages if m.role == "system" and m.content]
    return "\n\n".join(parts) if parts else None


def _translate_history(messages: list[Message]) -> list[dict]:
    """Map Addison's flat message list to Gemini's ``contents`` array.

    Gemini uses ``user`` and ``model`` roles (no ``system``/``tool`` role inline:
    the system prompt rides on ``systemInstruction`` and tool results ride back as
    ``user`` turns carrying ``functionResponse`` parts). An assistant turn that
    requested tools becomes a ``model`` turn with ``functionCall`` parts.
    Consecutive tool results MUST merge into one ``user`` turn — Gemini pairs a
    ``functionResponse`` to the ``functionCall`` by tool NAME, so we carry the name
    forward from the assistant turn that requested it.
    """
    contents: list[dict] = []
    pending_results: list[dict] = []
    # tool_call_id -> function name, populated as assistant tool_calls are seen so a
    # later tool result can name the function it answers.
    call_names: dict[str, str] = {}

    def flush_results() -> None:
        if pending_results:
            contents.append({"role": "user", "parts": list(pending_results)})
            pending_results.clear()

    for m in messages:
        if m.role == "system":
            continue  # carried by systemInstruction
        if m.role == "tool":
            name = call_names.get(m.tool_call_id, m.tool_call_id or "tool")
            pending_results.append(
                {
                    "functionResponse": {
                        "name": name,
                        "response": {"result": str(m.content)},
                    }
                }
            )
            continue

        flush_results()

        if m.role == "user":
            contents.append({"role": "user", "parts": [{"text": m.content or ""}]})
        elif m.role == "assistant":
            parts: list[dict] = []
            if m.content:
                parts.append({"text": m.content})
            for c in m.tool_calls:
                call_names[c.id] = c.tool_id
                parts.append({"functionCall": {"name": c.tool_id, "args": c.args}})
            contents.append({"role": "model", "parts": parts})

    flush_results()
    return contents


def _translate_response(data: dict) -> ModelResponse:
    candidates = data.get("candidates") or []
    content = (candidates[0].get("content") if candidates else None) or {}
    text_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    for part in content.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if "functionCall" in part:
            fn = part.get("functionCall") or {}
            name = fn.get("name")
            if not name:
                continue
            args = fn.get("args")
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                ToolCallRequest(id=f"google-{uuid.uuid4().hex[:8]}", tool_id=name, args=args)
            )
        elif isinstance(part.get("text"), str):
            text_parts.append(part["text"])
    text = "".join(text_parts) if text_parts else None
    if tool_calls:
        return ModelResponse(text=text, tool_calls=tool_calls, finish_reason="tool_use")
    return ModelResponse(text=text, tool_calls=[], finish_reason="stop")


def _http_error_message(status_code: int) -> str:
    if status_code in (400, 401, 403):
        return "That key doesn't work. Check it and try again."
    if status_code == 429:
        return "Google is busy right now (too many requests). Wait a moment and try again."
    if status_code >= 500:
        return "The Google service had a problem. Please try again in a moment."
    return f"The request to Google failed (status {status_code}). Please try again."


# --- connect-time validation (main.py drives this) -------------------------
def list_models(api_key_getter, client=None) -> list[str]:
    """``GET .../v1beta/models`` — the "one tiny request" provider.connect makes to
    validate a Google key. Returns the bare model ids (``models/`` prefix stripped),
    raising a plain-language ``RuntimeError`` on a bad key or an unreachable host.
    The key is fetched ONCE here and used only in the request header (§8.3)."""
    key = api_key_getter() if api_key_getter is not None else ""
    key = key.strip() if key else ""
    if not key:
        raise RuntimeError(_NO_KEY_MESSAGE)
    if not key.isascii() or not key.isprintable():
        raise RuntimeError(_MALFORMED_KEY_MESSAGE)
    injected = client
    http = injected if injected is not None else httpx.Client(timeout=10.0)
    try:
        response = http.get(f"{_BASE_URL}/models", headers={"x-goog-api-key": key})
    except httpx.HTTPError:
        raise RuntimeError(
            "Couldn't reach Google. Check your internet connection and try again."
        ) from None
    finally:
        if injected is None:
            http.close()
    if response.status_code in (400, 401, 403):
        raise RuntimeError("That key doesn't work. Check it and try again.")
    if response.status_code >= 400:
        raise RuntimeError("Google didn't accept the request. Please try again.")
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Google's reply couldn't be read.") from None
    ids: list[str] = []
    for entry in (data.get("models") if isinstance(data, dict) else None) or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if isinstance(name, str) and name:
            ids.append(name.split("/", 1)[-1])
    return ids
