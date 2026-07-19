"""OllamaProvider — local models, available post-setup (§4.1.2).

Talks to a local Ollama instance over HTTP (default ``http://127.0.0.1:11434``,
overridable via ``ADDISON_OLLAMA_URL``). One provider instance per model. Unlike
the cloud providers, capabilities are QUERIED from the loaded model's metadata
(``POST /api/show``) rather than assumed: ``native_tool_calling`` and ``vision``
come from Ollama's declared ``capabilities`` list, so a text-only 8B reports
``vision=False`` (which gates the image path, §4.1.1 item A) while a vision model
reports ``vision=True``. Models without native tool calling fall back to the
shared prompt-based tool-call parser (design-doc §7.3.2, "Basic tool support").

``runs_off_device=True`` for every local model — that flag is what privacy-
sensitive routing keys off (base.py). Built LAST in v1 (spec §11 step 10): it
only makes sense once a PRIMARY path already works, and its setup (hardware check
→ download → verify) is a separate, user-initiated flow, never active during the
Setup Assistant conversation.

This module also exposes the stateless HTTP helpers the local-setup flow in
``main.py`` drives — reachability (``is_running``), the streaming pull
(``pull_model``), and the plain-language sizing estimate (``approx_requirements``)
— so ``main.py`` stays focused on wiring, notifications, and the OS checks.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Iterator

import httpx

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
    ToolCallRequest,
    Usage,
    request_with_retry,
)
from agent_core.providers.tool_call_parser import build_tool_instructions, parse_tool_call

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

_TIMEOUT_SECONDS = 120.0        # local generation can be slow on modest hardware
_PING_TIMEOUT_SECONDS = 5.0
_DEFAULT_CONTEXT_TOKENS = 8_192  # a sane default when metadata omits context length

# Plain-language, never-a-stack-trace messages (CLAUDE.md).
_NOT_RUNNING_MESSAGE = (
    "Ollama isn't running on this computer. Start Ollama, then try again."
)


def default_base_url() -> str:
    """Ollama's HTTP base URL, from ``ADDISON_OLLAMA_URL`` or the local default."""
    return os.environ.get("ADDISON_OLLAMA_URL", DEFAULT_BASE_URL)


class OllamaProvider:
    def __init__(self, model: str, base_url: str | None = None, client=None) -> None:
        self._model = model
        self._base_url = (base_url or default_base_url()).rstrip("/")
        # Optional injected httpx.Client (tests wire one to a MockTransport). When
        # None, each request creates and closes its own client — the house pattern
        # from anthropic_provider.py.
        self._client = client
        # /api/show metadata is static per model, so cache it after the first
        # successful fetch. It is NOT a key — caching is fine (spec §4.1.2).
        self._metadata_cache: dict | None = None

    # --- capabilities (queried, not assumed) ------------------------------
    def capabilities(self) -> ProviderCapabilities:
        meta = self._metadata()
        declared = meta.get("capabilities") or []
        return ProviderCapabilities(
            native_tool_calling="tools" in declared,
            max_context_tokens=_context_length(meta),
            supports_streaming=True,
            # True for every local model — this is the flag privacy-sensitive
            # routing relies on (base.py); "off device" == runs on this machine.
            runs_off_device=True,
            vision="vision" in declared,
        )

    def _metadata(self) -> dict:
        """Model metadata from ``POST /api/show``, cached after first success.

        Degrades gracefully: if Ollama is unreachable or errors, return {} (which
        yields conservative caps — no native tools, no vision) rather than raising,
        so ``capabilities()`` never crashes a turn. The failure is NOT cached, so a
        later call can still populate once Ollama is up."""
        if self._metadata_cache is not None:
            return self._metadata_cache
        try:
            response = self._post("/api/show", {"model": self._model})
        except RuntimeError:
            return {}
        if response.status_code >= 400:
            return {}
        data = response.json()
        self._metadata_cache = data
        return data

    # --- send -------------------------------------------------------------
    def send(
        self, messages: list[Message], tools: list, effort: str | None = None
    ) -> ModelResponse:
        # ``effort`` is a cloud-model "answer style" (§4.1.1); local models have no
        # such control, so it is accepted and ignored for a uniform provider call.
        native = self.capabilities().native_tool_calling
        history = _translate_history(messages)
        body: dict = {"model": self._model, "messages": history, "stream": False}

        if tools and native:
            body["tools"] = _translate_tools(tools)
        elif tools:
            # Fallback path: the model can't do native tool calls, so coax a fenced
            # JSON block by appending the instruction block to the system prompt.
            body["messages"] = _with_tool_instructions(history, tools)

        response = self._post("/api/chat", body)
        if response.status_code >= 400:
            raise RuntimeError(_chat_error_message(response.status_code))

        payload = response.json() or {}
        message = payload.get("message") or {}
        # Ollama carries token counts at the TOP level of the /api/chat response
        # (prompt_eval_count / eval_count), not inside ``message`` — populate when
        # present, else None (a modest local model may omit them).
        usage = _translate_usage(payload)
        if native:
            return _translate_native_response(message, usage)
        return _translate_fallback_response(message, usage)

    # --- HTTP -------------------------------------------------------------
    def _post(self, path: str, body: dict) -> httpx.Response:
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=_TIMEOUT_SECONDS)
        try:
            # POST: retry only on a connection failure. On localhost that is
            # connection-refused (ConnectError) — a single harmless retry rides
            # out a race with an Ollama that is just finishing starting up.
            return request_with_retry(
                lambda: client.post(f"{self._base_url}{path}", json=body),
                idempotent=False,
            )
        except httpx.HTTPError:
            # Connection refused / timeout: almost always "Ollama isn't running".
            # No chained exception, so nothing about the request can leak.
            raise RuntimeError(_NOT_RUNNING_MESSAGE) from None
        finally:
            if injected is None:
                client.close()


# --- request/response translation (Ollama /api/chat shape) -----------------
def _translate_tools(tools: list) -> list[dict]:
    # Ollama's tool schema mirrors OpenAI's function-calling shape. Duck-typed —
    # providers/ must not import tools/ (module-boundary rule).
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
    """Map Addison's flat message list to Ollama's chat ``messages`` array.

    Ollama uses distinct ``system``/``user``/``assistant``/``tool`` roles inline
    (no top-level system field, unlike Anthropic). Assistant turns that requested
    tools carry a ``tool_calls`` array; tool results are ``tool`` messages.
    """
    out: list[dict] = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            entry: dict = {"role": "assistant", "content": m.content or ""}
            entry["tool_calls"] = [
                {"function": {"name": c.tool_id, "arguments": c.args}} for c in m.tool_calls
            ]
            out.append(entry)
        elif m.role == "tool":
            out.append({"role": "tool", "content": str(m.content)})
        else:
            # system / user / plain assistant
            out.append({"role": m.role, "content": m.content or ""})
    return out


def _with_tool_instructions(history: list[dict], tools: list) -> list[dict]:
    """Append the fenced-JSON tool instructions to the system message (creating
    one if absent) so a non-native model knows the exact shape to emit."""
    block = build_tool_instructions(tools)
    if not block:
        return history
    updated = list(history)
    for i, entry in enumerate(updated):
        if entry.get("role") == "system":
            merged = (entry.get("content") or "").rstrip()
            merged = f"{merged}\n\n{block}" if merged else block
            updated[i] = {**entry, "content": merged}
            return updated
    return [{"role": "system", "content": block}, *updated]


def _translate_native_response(message: dict, usage: Usage | None = None) -> ModelResponse:
    tool_calls: list[ToolCallRequest] = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        args = fn.get("arguments")
        if not isinstance(args, dict):
            args = {}
        tool_calls.append(ToolCallRequest(id=_call_id(), tool_id=name, args=args))
    text = message.get("content") or None
    if tool_calls:
        return ModelResponse(
            text=text, tool_calls=tool_calls, finish_reason="tool_use", usage=usage
        )
    return ModelResponse(text=text, tool_calls=[], finish_reason="stop", usage=usage)


def _translate_fallback_response(message: dict, usage: Usage | None = None) -> ModelResponse:
    text = message.get("content") or ""
    tool_call = parse_tool_call(text, id_prefix="ollama")
    if tool_call is not None:
        return ModelResponse(
            text=None, tool_calls=[tool_call], finish_reason="tool_use", usage=usage
        )
    return ModelResponse(text=text or None, tool_calls=[], finish_reason="stop", usage=usage)


def _translate_usage(payload: dict) -> Usage | None:
    """Map Ollama's top-level ``prompt_eval_count``/``eval_count`` to ``Usage``.
    None when either is missing — a small local model may not report them."""
    inp = payload.get("prompt_eval_count")
    out = payload.get("eval_count")
    if isinstance(inp, int) and isinstance(out, int):
        return Usage(input_tokens=inp, output_tokens=out)
    return None


def _context_length(meta: dict) -> int:
    """Pull the context window from ``model_info`` when present.

    Ollama keys it by architecture (e.g. ``llama.context_length``), so match on
    the suffix rather than a fixed key. Falls back to a sane default."""
    info = meta.get("model_info") or {}
    for key, value in info.items():
        if key.endswith("context_length") and isinstance(value, int):
            return value
    return _DEFAULT_CONTEXT_TOKENS


def _call_id() -> str:
    return f"ollama-{uuid.uuid4().hex[:8]}"


def _chat_error_message(status_code: int) -> str:
    if status_code == 404:
        # The named model isn't pulled — Ollama returns 404 for an unknown model.
        return (
            "That local model isn't installed. Set it up again from Settings, "
            "then try once more."
        )
    if status_code >= 500:
        return "The local model had a problem. Please try again in a moment."
    return "The local model couldn't answer just now. Please try again."


# --- local-setup HTTP helpers (driven by main.py, §4.1.2 steps 1 & 3) ------
def is_running(base_url: str | None = None, client=None) -> bool:
    """Reachability check: is Ollama itself up? A 200 from ``GET /api/tags`` says
    yes. Any connection/HTTP error means no (Addison does NOT install Ollama in
    v1 — it tells the user to start it)."""
    base = (base_url or default_base_url()).rstrip("/")
    injected = client
    c = injected if injected is not None else httpx.Client(timeout=_PING_TIMEOUT_SECONDS)
    try:
        return c.get(f"{base}/api/tags").status_code == 200
    except httpx.HTTPError:
        return False
    finally:
        if injected is None:
            c.close()


def pull_model(model_name: str, base_url: str | None = None, client=None) -> Iterator[dict]:
    """Stream ``POST /api/pull`` and yield each NDJSON status object.

    Ollama emits lines like ``{"status": "pulling manifest"}`` and
    ``{"status": "downloading ...", "total": N, "completed": M}`` ending with
    ``{"status": "success"}``. The caller turns these into plain-language
    ``model.localSetupProgress`` notifications. Raises a plain RuntimeError if
    Ollama is unreachable or the pull fails."""
    base = (base_url or default_base_url()).rstrip("/")
    injected = client
    # No read timeout: a multi-gigabyte pull legitimately takes a long time.
    c = injected if injected is not None else httpx.Client(timeout=None)
    try:
        with c.stream(
            "POST", f"{base}/api/pull", json={"model": model_name, "stream": True}
        ) as response:
            if response.status_code >= 400:
                raise RuntimeError(_pull_error_message(response.status_code))
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except httpx.HTTPError:
        raise RuntimeError(_NOT_RUNNING_MESSAGE) from None
    finally:
        if injected is None:
            c.close()


def _pull_error_message(status_code: int) -> str:
    if status_code == 404:
        return (
            "Couldn't find that model to download. Check the name and try again."
        )
    return "The download couldn't start just now. Please try again."


# --- plain-language sizing (§4.1.2 step 2, design-doc §7.3.2) ---------------
_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def approx_requirements(model_name: str) -> dict:
    """Best-effort disk/RAM needs for a model NOT yet downloaded, in GB.

    There is no live source for an un-pulled model's footprint, so estimate from
    the parameter count in the name (``:8b``, ``:14b``, ``:70b``): a Q4-quantized
    model is roughly ~0.75 GB/billion-params on disk and needs ~1 GB/billion in
    memory plus a couple GB of overhead. When the size can't be read, fall back to
    a middling estimate so the sizing message still names real numbers rather than
    guessing wildly either way (design-doc §7.3.2 wants plain sizes, not parameter
    counts, shown to the user)."""
    params_b = _params_billions(model_name)
    if params_b is None:
        return {"params_b": None, "disk_gb": 8.0, "ram_gb": 8.0}
    return {
        "params_b": params_b,
        "disk_gb": round(params_b * 0.75, 1),
        "ram_gb": round(params_b * 1.0 + 2.0, 1),
    }


def _params_billions(model_name: str) -> float | None:
    match = _PARAM_RE.search(model_name or "")
    return float(match.group(1)) if match else None
