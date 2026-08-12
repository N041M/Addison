"""ModelProvider protocol and shared model types — engineering-spec §3, §4.1.

The orchestrator is written entirely against ``ModelProvider`` and never
branches on the concrete provider. Capability differences are handled via
``capabilities()``, not ``isinstance`` checks.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

import httpx

from agent_core.redaction import redact

if TYPE_CHECKING:
    # Type-only, erased at runtime — the module-boundary rule (no runtime
    # providers -> tools import; tests/test_module_boundaries.py) still holds,
    # the same stance the frontend takes with allowTypeImports on lib/parse.ts.
    from agent_core.tools.base import ToolDefinition

# --- one conservative HTTP retry (shared by every provider) -----------------
_RETRY_SLEEP_SECONDS = 0.5  # a short fixed pause; no backoff/jitter — this codebase is minimal

# Connection-level failures where the request PROVABLY never reached the server,
# so replaying it can neither duplicate work nor double-bill. Safe to retry for
# any method, POST included.
_CONNECT_ERRORS: tuple[type[Exception], ...] = (httpx.ConnectError, httpx.ConnectTimeout)
# Read/transport hiccups that are safe to retry ONLY for an idempotent GET: a
# ReadTimeout means the request DID reach the server (it just didn't answer in
# time), so on a POST it may already have been processed/billed and must never be
# resent — but on a side-effect-free GET a resend is harmless.
_IDEMPOTENT_ERRORS: tuple[type[Exception], ...] = _CONNECT_ERRORS + (
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def request_with_retry(
    send: Callable[[], httpx.Response], *, idempotent: bool, allow_retry: bool = True
) -> httpx.Response:
    """Run ``send()`` (returns an ``httpx.Response``) with at most ONE retry after
    a short fixed pause. Invisible to callers: it returns the same Response or
    re-raises the same ``httpx`` error the caller's ``except httpx.HTTPError``
    already turns into a plain-language message — the only observable effect is
    latency.

    The idempotent split is the whole point (§8.3 no-double-billing reasoning):

    - ``idempotent=True`` — a GET (key validation / model listing) has no side
      effects, so retry it on any connection- OR read-level failure and on a 5xx
      (a transient server blip).
    - ``idempotent=False`` — a POST (send a message, cost tokens/money) is retried
      ONLY when the failure proves the request never arrived (ConnectError /
      ConnectTimeout). A ReadTimeout, a 5xx, or any received response means it MAY
      have been processed, so we never resend it.

    Both attempts reuse the caller's client, so the injected-client / per-call
    construct-and-close pattern at each call site is unchanged.

    ``allow_retry=False`` disables the internal retry entirely. Providers pass
    it when the caller supplied a per-call deadline ([MF-A]): a deadline means
    the routing attempt loop is driving, and THAT loop is the retry mechanism —
    a hidden second attempt here would double the per-turn budget on a
    ConnectTimeout (up to 2× deadline + the sleep), which is exactly the hole
    the budget exists to close (post-build adversarial pass, 2026-07-24).
    Standalone calls (no deadline) keep today's one-retry robustness unchanged.
    """
    retryable = _IDEMPOTENT_ERRORS if idempotent else _CONNECT_ERRORS
    if not allow_retry:
        return send()  # the chain retries by advancing candidates, never here
    try:
        response = send()
    except retryable:
        time.sleep(_RETRY_SLEEP_SECONDS)
        return send()  # second and final attempt; a re-raise propagates to the caller
    if idempotent and response.status_code >= 500:
        time.sleep(_RETRY_SLEEP_SECONDS)
        return send()
    return response


# --- provider failure hierarchy (step 3 stage 0, contract §"Stage 0") --------
# Three named failures the attempt loop (orchestrator, D4) branches on. All three
# subclass RuntimeError so every existing ``except RuntimeError`` still catches
# them (behaviour freeze) and each carries the SAME plain user-facing message the
# provider raised before this split — the type is new, the wording is byte-identical.
#
# The distinction the loop cares about is ONLY "may I try the next candidate?":
#   * ProviderUnavailable  -> transient; the loop MAY walk the chain.
#   * ProviderKeyRejected  -> definitive, and about THIS provider's key alone; the
#     loop walks (another provider has a different key) and marks it needs-attention.
#   * ProviderRequestRejected / plain ProviderAuthFailed -> fail the turn
#     immediately, no walk (the next provider gets the same bad request, or we had
#     no key to send in the first place and a different provider will not fix that).


class ProviderUnavailable(RuntimeError):
    """A transient failure — HTTP 429, any 5xx, or a network connect/read timeout.
    The attempt loop may fall forward to the next candidate on this (D4)."""


class ProviderRequestRejected(RuntimeError):
    """A 4xx other than 401/403/404/429 — the request itself is bad, so the next
    provider would reject it identically. The turn fails immediately (D4)."""


class ProviderModelGone(ProviderUnavailable):
    """HTTP 404 — the MODEL is not there, though the request was fine.

    Split out of ``ProviderRequestRejected`` on 2026-08-07, from a real failure
    this codebase could not previously express. Google's listing endpoint returned
    ``gemini-2.5-flash``, advertising ``generateContent``; the generate endpoint
    answered 404 with *"This model models/gemini-2.5-flash is no longer available
    to new users."* — a model retired for accounts created after some cutoff, and
    still in the catalogue.

    D4's rule for a rejected request is "do not walk, the next provider gets the
    same bad request". That is TRUE of a malformed body and FALSE here: nothing is
    wrong with the request, one model is gone, and every other candidate would
    answer it. Under the old class the chain gave up on the whole provider and
    jumped to a different vendor's model.

    A ``ProviderUnavailable`` subclass, so the existing walk applies with no new
    branch — but the orchestrator cools the MODEL rather than the provider, because
    "this one model is retired" says nothing about its siblings, and cooling all of
    Google for a dead Gemini 2.5 would take out Gemini 3 with it."""


class ProviderAuthFailed(RuntimeError):
    """A 401/403, or a missing/malformed API key — a credential problem the chain
    cannot route around for THIS candidate. The turn fails immediately (D4)."""


class ProviderKeyRejected(ProviderAuthFailed):
    """The provider ANSWERED, and its answer was 401/403: the key Addison holds was
    refused (plan §5.2).

    A subclass, so every existing ``except ProviderAuthFailed`` still catches it and
    nothing that reads the message changes. What the narrower type buys is the one
    distinction §5.2 turns on and the parent cannot express: a server that refused
    the key is DEFINITIVE evidence about that key, while ``ProviderAuthFailed`` is
    also raised locally when there is no key to send or the bytes are unusable —
    which says nothing about whether a saved key is any good, and must never produce
    "your key was rejected".

    It is deliberately NOT raised for a 429, a 5xx, a timeout or a connection error.
    Those are ``ProviderUnavailable``; treating a rate limit as a revoked key would
    tell somebody to replace a key that works."""


def exception_for_http_status(
    status_code: int, message: str, server_detail: str | None = None
) -> RuntimeError:
    """Classify a >=400 status into the hierarchy, carrying the caller's own plain
    message unchanged. 401/403 -> the key was rejected; 429 or 5xx -> unavailable;
    every other 4xx -> rejected. The order matters: auth is checked before the
    429/5xx band.

    This function is the single choke point every provider's ``send`` funnels an
    error status through, which is why §5.2 needs no per-provider edit — and why
    the 401/403 line is the one to mutate when checking that these tests can fail.

    The status is ATTACHED to the exception as well as classified by it. The class
    answers "may the loop try the next candidate?"; the number answers "what did
    the server actually say?", and only the second one is any use to somebody
    asking why a provider stopped working. Losing it is what made a real 404
    indistinguishable from a rate limit in the provider-attempt log."""
    exc: RuntimeError
    if status_code in (401, 403):
        exc = ProviderKeyRejected(message)
    elif status_code == 404:
        # Checked BEFORE the 429/5xx band and after auth, on the same reasoning as
        # the rest of this ladder: 404 is about the MODEL, not the request and not
        # the provider's health.
        exc = ProviderModelGone(message)
    elif status_code == 429 or status_code >= 500:
        exc = ProviderUnavailable(message)
    else:
        exc = ProviderRequestRejected(message)
    exc.status_code = status_code  # type: ignore[attr-defined]
    # The provider's own words, for the log only — never for the screen, which
    # keeps the plain sentence ``message`` already carries.
    exc.server_detail = server_detail  # type: ignore[attr-defined]
    return exc


def status_code_of(exc: BaseException) -> int | None:
    """The HTTP status behind a provider failure, or None when there wasn't one.

    None is the honest answer for a connect/read timeout or a DNS failure — those
    never reached a server, and recording a 0 or a 500 for them would invent a
    reply nobody sent."""
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


# What a provider's error body is allowed to contribute to the log. Long enough
# for the sentence that names the cause, short enough that a server which answers
# an error with a page of HTML cannot fill the table.
_MAX_SERVER_DETAIL = 400


def server_detail_of(exc: BaseException) -> str | None:
    """What the SERVER said, as opposed to what Addison told the person.

    These are different sentences and only one of them is diagnostic. Addison
    shows "The request to Google failed (status 404). Please try again." — plain
    language, no jargon, the house rule and the right thing on screen. Google said
    which model and which API version, and that is the sentence that ends an
    investigation. Recording only ours meant the log faithfully preserved our own
    guess about a failure we did not understand."""
    detail = getattr(exc, "server_detail", None)
    if not isinstance(detail, str):
        return None
    detail = detail.strip()
    return detail[:_MAX_SERVER_DETAIL] or None


def note_candidate(exc: BaseException, provider_id: str | None, model_id: str | None) -> None:
    """Stamp WHICH CANDIDATE failed onto the exception it raised.

    The status and the server's own sentence are attached where they are learned
    (``exception_for_http_status``, inside one provider's ``send``). The provider
    and model are not knowable there — a provider object does not carry the routing
    identity the chain resolved it under — so they are stamped by the only code that
    holds both at once: the attempt loop, at the moment it handles the failure.

    Best-effort and never raising: this runs on a path that is already handling a
    failure, and a diagnostic that can break the error it describes is worse than no
    diagnostic. Attributes only — the exception's TYPE and its plain ``str`` (what
    the person reads) are untouched, so nothing about control flow or the message
    changes."""
    try:
        if provider_id:
            exc.provider_id = provider_id  # type: ignore[attr-defined]
        if model_id:
            exc.model_id = model_id  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover — an exception that refuses attributes
        pass


def provider_of(exc: BaseException) -> str | None:
    """The provider id behind a failure, or None when nobody stamped one."""
    value = getattr(exc, "provider_id", None)
    return value if isinstance(value, str) and value else None


def model_of(exc: BaseException) -> str | None:
    """The model id behind a failure, or None when nobody stamped one."""
    value = getattr(exc, "model_id", None)
    return value if isinstance(value, str) and value else None


def technical_detail(exc: BaseException) -> str:
    """The Developer profile's "Technical details" fold, in full (§4.7).

    THE PLAIN SENTENCE ABOVE THE FOLD IS UNCHANGED and is not composed here — the
    house rule keeps jargon out of it (CLAUDE.md), and this is the one place where
    jargon is the point. What the fold used to hold was ``repr(exc)`` alone, i.e.
    that same plain sentence wrapped in a class name: it named no provider, no
    status and nothing the server said, so a misattributed 400 read exactly like a
    genuine bad key.

    Four facts, each omitted when it is not known rather than guessed at:

      * which provider (and model) the chain was talking to — stamped by
        ``note_candidate``;
      * the HTTP status, when a server answered at all. None is the honest answer
        for a timeout, and ``status_code_of`` owns why;
      * the provider's OWN error sentence, which is the one that ends an
        investigation (``server_detail_of``);
      * the exception itself, last, because it is the least informative of the four
        and it is what the other three exist to supplement.

    REDACTED ON THE WAY OUT (G1's neighbourhood, ``redaction``): a provider's error
    body and a stray exception repr are both text nobody vetted, and both have been
    seen carrying an URL with a token in it. This text goes to the WEBVIEW, which is
    the one process that may never see a key — so the same backstop that guards the
    model send guards this. It stays a backstop, not a boundary.

    With none of the three extra facts known, the answer is ``repr(exc)`` exactly as
    before, so every non-provider error's fold is byte-for-byte unchanged."""
    lines: list[str] = []
    provider = provider_of(exc)
    if provider:
        model = model_of(exc)
        lines.append(f"provider: {provider}" + (f" · {model}" if model else ""))
    status = status_code_of(exc)
    if status is not None:
        lines.append(f"http status: {status}")
    said = server_detail_of(exc)
    if said:
        lines.append(f"provider said: {said}")
    lines.append(repr(exc))
    return redact("\n".join(lines)).text


def error_message_from_body(response: httpx.Response) -> str | None:
    """The provider's own explanation, dug out of an error response.

    Anthropic, OpenAI and Google all wrap it as ``{"error": {"message": ...}}``,
    so one reader serves every provider rather than three that drift. Anything
    unreadable — HTML from a proxy, an empty body, a truncated stream — yields
    None, because a failure to explain a failure must never become a failure.

    ``read()`` first, and that is not belt-and-braces: on the STREAMING path the
    response arrives with its body deliberately unread (``open_stream`` returns as
    soon as the status line lands), so ``.json()`` alone raises ``ResponseNotRead``
    and every streamed failure — which is every failure a chat turn produces —
    would explain nothing. Reading an ERROR response costs nothing; it is small by
    construction and the stream is being abandoned either way."""
    try:
        if hasattr(response, "read"):
            response.read()
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    return None


# --- streaming primitives (shared by every streaming provider) --------------
# Per-token delivery. Each provider translates its OWN wire format, but opening
# the stream and splitting SSE frames are identical everywhere, so they live here
# rather than as four near-copies.

#: What a provider calls with each piece of assistant TEXT as it arrives. Prose
#: only — never a tool call's arguments, and never a reasoning/thinking block: the
#: sink's text is appended verbatim to what the reader is watching, so anything
#: that is not part of the answer must not reach it.
DeltaSink = Callable[[str], None]


@contextmanager
def open_stream(
    client: httpx.Client,
    url: str,
    *,
    headers: dict,
    body: dict,
    deadline: float,
    allow_retry: bool,
) -> Iterator[httpx.Response]:
    """POST ``body`` and yield the response with its body UNREAD, for streaming.

    ``client.send(request, stream=True)`` returns once the status line is in, so
    this composes with ``request_with_retry`` unchanged and keeps the no-double-
    billing rule intact: ``idempotent=False`` retries only the connection-level
    failures that prove the request never arrived, and those raise before any
    token has been generated or billed. Nothing here reads the body, so the
    ``>=500`` re-send inside the helper stays unreachable (it is idempotent-only).

    The caller checks ``status_code`` and iterates; the stream is always closed.
    """
    request = client.build_request("POST", url, headers=headers, json=body, timeout=deadline)
    response = request_with_retry(
        lambda: client.send(request, stream=True),
        idempotent=False,
        allow_retry=allow_retry,
    )
    try:
        yield response
    finally:
        response.close()


def iter_sse_json(response: httpx.Response) -> Iterator[dict]:
    """Yield each ``data:`` frame of an SSE response, JSON-decoded.

    Comments, blank separators, the ``event:`` lines and the ``[DONE]`` sentinel
    are skipped, as is any frame that will not parse — a stream is read as it
    arrives, so a truncated or malformed final frame is an ordinary way for one to
    end and must not raise in the middle of relaying an answer the reader is
    already looking at. Only dict frames are yielded, so callers can index safely.
    """
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            frame = json.loads(payload)
        except ValueError:
            continue
        if isinstance(frame, dict):
            yield frame


def effective_timeout(override: float | None, default: float) -> float:
    """The per-call timeout a provider actually uses ([MF-A]). ``None`` -> the
    provider's own default (freeze). A value only ever TIGHTENS the default, never
    extends it, so handing a provider the full remaining per-turn budget on a
    healthy first send resolves to exactly today's constant."""
    if override is None:
        return default
    return min(override, default)


class ModelRole(str, Enum):
    """Which job a configured provider is filling. Multiple roles may be
    configured and populated at once — this is NOT a single active-provider
    switch (see §4.1.1, ModelRouter)."""

    PRIMARY = "primary"                  # main conversation driver, typically a frontier cloud model
    LOCAL = "local"                      # self-hosted via Ollama, available once configured (§4.1.2)
    SETUP_ASSISTANT = "setup_assistant"  # onboarding-only free relay, unrelated to the above two


@dataclass
class ProviderCapabilities:
    native_tool_calling: bool
    max_context_tokens: int
    supports_streaming: bool
    runs_off_device: bool        # True only for local providers — informs privacy-sensitive routing
    vision: bool = False         # can analyze image input — gates the image path (§4.1.1, item A)
    audio: bool = False          # can analyze audio input
    # v2 auto-routing (§4.1.1) reads these flags to pick a capable model per task;
    # in v1 they only drive an explicit warning + manual switch, never an auto-switch.


@dataclass
class ToolCallRequest:
    id: str
    tool_id: str
    args: dict
    # Opaque per-provider crumbs captured off the wire and echoed back verbatim
    # when this call is replayed in history. Adapter-agnostic on purpose: the
    # ONLY code allowed to read a key is the adapter that wrote it, and every
    # other adapter ignores the dict entirely. Gemini 3 is why it exists — its
    # ``thoughtSignature`` must return unchanged with the functionCall part or
    # the next request is refused with a 400 — but nothing here is Google-shaped,
    # so a second provider with the same requirement needs no new field.
    provider_meta: dict = field(default_factory=dict)


@dataclass
class Message:
    role: str                    # 'user' | 'assistant' | 'tool'
    content: str
    tool_call_id: str | None = None
    # Set on assistant turns that requested tools. Providers with native tool
    # calling need the original tool_use blocks replayed in history so each
    # tool_call_id pairs with the tool_result that follows it.
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    # HISTORY ONLY — what this assistant turn asked for in a PREVIOUS session,
    # rebuilt by ``conversation.load`` from ``messages.tool_calls_json``.
    #
    # NO PROVIDER ADAPTER MAY READ THIS, and none does: every adapter replays
    # ``tool_calls`` alone. That separation is the whole point of the second field.
    # A persisted tool_use put back into ``tool_calls`` would be sent without the
    # tool_result that answered it — the pairing rule in §4.4 — and the provider
    # would then reject every later request of the session. The routine builder,
    # which is not talking to a model, reads it instead (KNOWN-BUGS #5: the steps
    # of a reopened chat are still saveable).
    past_tool_calls: list[ToolCallRequest] = field(default_factory=list)


@dataclass
class Usage:
    """Token counts for ONE provider call (§4.8 usage substrate). Populated from
    each API's own usage report and mapped honestly; a provider that reports no
    usage leaves ``ModelResponse.usage`` as None (no row is recorded for it)."""

    input_tokens: int
    output_tokens: int


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    # Token usage for the call that produced this response, when the provider
    # reports it (None otherwise). Orchestrator machinery records it into
    # ``usage_log`` — it is NEVER a registry tool (§4.8 precedent).
    usage: Usage | None = None


@runtime_checkable
class ModelProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    # ``effort`` is the per-message "answer style" (§4.1.1, models_catalog.py). Only
    # AnthropicProvider acts on it (and only for models that support it); every other
    # provider ACCEPTS and IGNORES it, so the orchestrator can pass it uniformly.
    #
    # ``timeout`` ([MF-A]) is an optional per-call deadline in seconds the attempt
    # loop threads down so a single hanging candidate can never blow the per-turn
    # budget. None means "use my own default" — byte-identical to today. A provider
    # never EXTENDS past its own default; the override only ever tightens it
    # (``effective_timeout``), so passing the whole remaining budget on the first,
    # healthy send resolves to today's constant exactly.
    #
    # ``on_delta`` is how an answer reaches the reader AS IT ARRIVES. A provider
    # that streams calls it with each piece of assistant prose and STILL returns
    # the same complete ``ModelResponse`` — so the tool loop, usage recording and
    # the fallback chain are unaffected by whether streaming happened. Like
    # ``effort``, a provider that cannot honour it ACCEPTS and IGNORES it; the
    # orchestrator then pushes the finished text itself, so the only difference is
    # whether the answer appears progressively. That is what keeps this
    # capability-driven rather than an ``isinstance`` branch (§2).
    #
    # Two rules bind every implementation:
    #   * Only assistant PROSE goes to the sink — never tool-call arguments and
    #     never a reasoning block. The sink's text is appended verbatim to what
    #     the reader is watching, so it may not contain machinery.
    #   * What is streamed must equal what is returned. The display may lag the
    #     ``ModelResponse``; it may never say something the response does not.
    def send(
        self,
        messages: list[Message],
        tools: list["ToolDefinition"],
        effort: str | None = None,
        timeout: float | None = None,
        on_delta: DeltaSink | None = None,
    ) -> ModelResponse: ...
