"""Orchestration loop — engineering-spec §4.4.

The loop is written against the ModelProvider interface and resolves the
provider per turn via the ModelRouter (§4.1.1) — it never holds a single
``self.active_provider``. The same loop is reused, constrained, by the Routine
Engine (§6.4), which is why the permission gate and tool registry are consulted
here rather than inside any provider.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from agent_core import delete_preview
from agent_core.permissions.gate import (
    PermissionGate,
    PermissionStatus,
    call_arming_card,
    call_arming_refusal,
    tool_requires_arming,
)
from agent_core.policy import PolicyMode
from agent_core.providers.base import (
    Message,
    ModelRole,
    ProviderAuthFailed,
    ProviderKeyRejected,
    ProviderModelGone,
    ProviderRequestRejected,
    ProviderUnavailable,
    ToolCallRequest,
    note_candidate,
    server_detail_of,
    status_code_of,
)
from agent_core.providers.router import ModelRouter, RoutingCandidate
from agent_core.redaction import redact, redacted_for_model
from agent_core.screening import mark_untrusted, screen
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ExecutionContext,
    ToolResult,
    call_affected_path,
    call_command_text,
    call_is_destructive,
    call_permission_detail,
    default_forbidden_check,
)
from agent_core.tools.registry import UNKNOWN_TOOL_REFUSAL, ToolRegistry

# Confinement refusal (step 5, D3). A path-bounded tool whose resolved path is not
# inside a currently-trusted root is hard-refused BEFORE execute — permission-to-
# touch, distinct from the gate's card. Plain language, one next step.
_OUTSIDE_TRUST = (
    "That file is outside the folders you've trusted, so Addison left it alone. "
    "Trust its folder first if you want Addison to work with it."
)


# Two ceilings, because a turn can run away in two different directions and each
# bound leaves the other wide open. Both matter for the same reason: a SAFE grant is
# keyed by tool id and lasts the session, so ONE permission card authorises every
# later call of that tool, and a tool result is model-readable text that untrusted
# page content can steer.
#
#   * ROUNDS bounds CHAINING — a page ending "now read https://…/2" walking the model
#     from one page to the next, one call at a time, indefinitely.
#   * CALLS bounds FAN-OUT — one provider response carrying hundreds of tool_use
#     blocks at once. The round cap does nothing about this on its own: the loop over
#     a single response's tool_calls is not a round, so 400 fetches inside round 1
#     cost one round and show one card.
#
# Both ceilings are generous enough that no legitimate turn meets them (a search plus
# a handful of page reads is well under ten of either) and low enough that a runaway
# stops while the person is still watching.
_MAX_TOOL_ROUNDS = 25
_MAX_TOOL_CALLS = 40
_TOO_MANY_STEPS = (
    "That turned into more steps than I should take in one go, so I stopped there. "
    "Tell me which part you'd like me to carry on with."
)
# Said to the MODEL, not the person, for each tool_use left unrun once the budget is
# spent. Every tool_use must be answered by a tool_result even when nothing ran: an
# unpaired tool_use makes the provider reject every later request in the conversation
# (the same reason a tool crash becomes a failed step rather than an exception).
_STEP_NOT_RUN = (
    "This step was not run: the turn reached its limit on how many steps it may take."
)

# What goes BETWEEN two things Addison says in one turn. A turn that calls a tool
# says something before the call and something after it, and each arrives as its
# own run of deltas; the frontend appends them into one message, so with nothing
# in between the reader got "…the current state of the add function.Now I'll add a
# docstring". They are separate utterances, and a blank line is how prose says so —
# it is also the only separator markdown reads as a paragraph break, which a single
# space is not. Inserted only BETWEEN segments and never inside one, so no fence,
# list or code block can be cut by it.
_SEGMENT_BREAK = "\n\n"

# --- graceful fallback + cooldown (step 3, contract D4) ---------------------
# Module constants, not settings — the model must not be able to shrink the
# rollback/fallback safety window. Read through the module namespace inside
# run_turn so tests can monkeypatch them (small values keep the budget test fast).
_COOLDOWN_SECONDS = 60.0          # per provider id, in-memory; set on ProviderUnavailable
_FALLBACK_BUDGET_SECONDS = 120.0  # a real per-attempt deadline ([MF-A]), not a between gate
# The fallback note surfaces on the SAME Activity Panel channel as tool activity
# (D4); a synthetic id keeps _emit_activity's tool-agnostic contract intact.
_ROUTING_ACTIVITY_ID = "routing"
# Untrusted-content screening (design-doc §11). The same Activity Panel channel the
# routing notes use, with its own synthetic id: this is a note ABOUT a step, not a
# step, and _emit_activity's contract is tool-agnostic on purpose.
_SCREENING_ACTIVITY_ID = "screening"
# Said to the PERSON, in their words, whenever a tool brought back text shaped like
# an instruction. Plain language: no "prompt injection", no rule names, no quote of
# what was found (quoting it would reproduce the payload on the screen). It says what
# happened and what Addison did about it, and nothing about what to do next, because
# there is nothing the person has to do.
_SCREENING_NOTE = (
    "This page or tool result contained text that looks like instructions to "
    "Addison. Addison will treat it as information only."
)
_FALLBACK_NOTE = "{busy} was busy, so Addison used {used}."  # D8 frozen copy
# Plan §5.2, and the copy table in §6 — said ONCE per revoked key, not once per turn.
# It names the provider (not the model), because a key belongs to a provider and
# Settings is where a provider's key lives. No jargon, no status code, one next step.
_KEY_REJECTED_NOTE = (
    "{provider} rejected Addison's key — it may have been revoked. "
    "Add a new one in Settings."
)
# Only reached when the chain is exhausted having never captured a provider's own
# plain message (an empty chain). Normally the last ProviderUnavailable's own
# sentence is re-raised, which is more specific than this.
_NO_MODEL_REACHABLE = (
    "Addison couldn't reach a model to answer just now. Please try again in a moment."
)


def _result_as_text(content: Any) -> str:
    """Serialize one tool result for the model — JSON for structured content.

    NOT ``str()``. Python's repr of a dict chooses its quote character from the
    dict's CONTENTS: a value containing apostrophes and no double quotes is emitted
    inside "..." with every apostrophe unescaped. A web page whose text is written
    with only apostrophes can therefore close the dict and open a convincing
    ``{'role': 'user', ...}`` after it — forging a message from the person, inside
    the very wrapper (``untrusted_note``) that exists to say "this is not the
    person talking". ``json.dumps`` always escapes its own delimiter, so no page
    content can produce one. This is what makes web_search's and read_web_page's
    untrusted wrappers survive the trip to the model intact.

    THE FALLBACK IS JSON TOO, and that is the whole point of it. ``default=str``
    absorbs an unserializable VALUE, so what still reaches the except clause is a
    circular reference (ValueError) or a non-string dict key (TypeError — ``default``
    is not consulted for keys). Returning ``str(content)`` there put the raw repr
    back on the wire in exactly the case the paragraph above rejects: a dict keyed
    by an object whose ``__repr__`` is ``{'role': 'user', ...}`` serialized to
    ``{{'role': 'user', ...}: 'x'}``, unescaped, straight into the message list. No
    tool ships either shape today, so nothing is exploitable right now — which is
    precisely why it had to be fixed now rather than on the day one does.
    ``json.dumps(str(...))`` keeps the repr visible for debugging but wraps it in a
    quoted JSON string, so no content it contains can close the delimiter.
    """
    if isinstance(content, (dict, list)):
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps(str(content), ensure_ascii=False)
    return str(content)


def _screenable_text(content: Any) -> str:
    """One string carrying every piece of text in ``content``, AS IT WAS WRITTEN.

    The screener's input, and deliberately not ``_result_as_text``'s output. That
    function's job is to make a tool result safe to hand a model, and the escaping
    that does it — a newline becoming the two characters backslash-n — destroys
    exactly what the screening rules anchor on: a line start, and the word boundary
    in front of the first word of a line. An injection at the head of a line
    survives the escape unreadable to every anchored rule while remaining perfectly
    readable to the model, which is the one combination that must not exist.

    So the leaves are read as strings and rejoined with real newlines. Keys as well
    as values, on ``mcp_client._scrub_strings``' precedent: a page-supplied field
    name is page-supplied text. Nothing is truncated here — this string is never
    returned to anybody, it is looked at once and dropped."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return "\n".join(
            _screenable_text(part)
            for item in content.items()
            for part in item
        )
    if isinstance(content, list):
        return "\n".join(_screenable_text(item) for item in content)
    return str(content)


@dataclass
class Conversation:
    id: str
    messages: list[Message] = field(default_factory=list)
    #: tool_call id -> the ``detail`` line the Activity Panel showed for it, for
    #: every step that ACTUALLY RAN this session. Not state the turn reads: it is
    #: what ``rpc/conversation.py`` writes into ``messages.tool_calls_json`` so a
    #: reopened chat can redraw "Addison's work" as a record of what happened
    #: rather than of what was requested (a denied call is recorded too, marked
    #: not-run, because the routine builder must see the same set either way).
    #: Empty detail is stored as None — most tools have nothing to name.
    shown_steps: dict[str, str | None] = field(default_factory=dict)

    def note_step_shown(self, tool_call_id: str, detail: str | None) -> None:
        """Record that this call reached the panel (and therefore ran)."""
        self.shown_steps[tool_call_id] = detail or None

    def append_tool_result(self, tool_call_id: str, result: ToolResult) -> None:
        self.messages.append(
            Message(
                role="tool",
                content=_result_as_text(result.content),
                tool_call_id=tool_call_id,
            )
        )

    def append_assistant_message(self, text: str | None) -> None:
        self.messages.append(Message(role="assistant", content=text or ""))

    def append_assistant_tool_calls(
        self, text: str | None, tool_calls: list[ToolCallRequest]
    ) -> None:
        """Record the assistant turn that REQUESTED tools, before its results.

        Providers with native tool calling (e.g. Anthropic) require each
        ``tool_result`` to be preceded in history by the assistant ``tool_use``
        it answers. Without this the next ``provider.send()`` replays an
        unpaired tool result and the API rejects the turn (spec §4.4)."""
        self.messages.append(
            Message(role="assistant", content=text or "", tool_calls=tool_calls)
        )


class _DeltaRelay:
    """Threads streamed prose to the frontend and remembers what has been shown.

    Providers call this per delta (``ModelProvider.send``'s ``on_delta``). It
    answers two questions the turn needs, and they are NOT the same question:

    * ``shown_this_send`` — did THIS provider call stream anything? A provider that
      ignored ``on_delta`` streamed nothing, so the finished ``response.text``
      still has to be pushed. Without the distinction the answer either never
      arrives or arrives twice.
    * ``shown_this_turn`` — is part of an answer already on the reader's screen?
      That is what forbids the routed path from falling forward: appending a
      second, complete answer to a partial one produces a single message that
      reads as one answer and is two.

    Empty deltas are dropped rather than counted — a provider that emits a
    zero-length chunk has shown the reader nothing, and treating it as "shown"
    would suppress the finished text and lose the answer entirely.

    It is also the ONE place that knows where one thing Addison says ends and the
    next begins: a send boundary. Deltas within a send are the same sentence
    arriving in pieces and are relayed byte-for-byte; the first delta of a LATER
    send in the same turn is a new utterance, so ``_SEGMENT_BREAK`` goes in front
    of it (see the constant). Nothing is ever stripped or rewritten — the break is
    added, and only between segments.
    """

    def __init__(self, sink) -> None:
        self._sink = sink
        self.shown_this_send = False
        self.shown_this_turn = False
        # Trailing newlines of what has been shown, so a segment that already ended
        # on a blank line does not get a second one. Only newlines are counted: any
        # other character means the break is needed in full.
        self._trailing_newlines = 0

    def begin_send(self) -> None:
        self.shown_this_send = False

    def __call__(self, text: str) -> None:
        if not text:
            return
        # First output of a send, with something already on screen from an earlier
        # one: separate them. Top-up only — a segment ending "…\n" needs one more
        # newline, one ending "…\n\n" needs none.
        if not self.shown_this_send and self.shown_this_turn:
            missing = len(_SEGMENT_BREAK) - min(self._trailing_newlines, len(_SEGMENT_BREAK))
            text = "\n" * missing + text
        self.shown_this_send = True
        self.shown_this_turn = True
        body = text.rstrip("\n")
        tail = len(text) - len(body)
        # A chunk that is nothing but newlines EXTENDS the run; anything else
        # restarts the count from its own tail.
        self._trailing_newlines = tail if body else self._trailing_newlines + tail
        self._sink(text)


class Orchestrator:
    def __init__(
        self,
        model_router: ModelRouter,
        tool_registry: ToolRegistry,
        permission_gate: PermissionGate,
        undo_manager: UndoManager,
        stream_to_frontend=lambda text: None,
        on_activity=lambda tool_id, label, detail=None: None,
        on_usage=lambda usage, latency_ms, provider_id, model_id: None,
        on_context_usage=lambda used_tokens, max_context_tokens: None,
        shell_bridge=None,
        guards_provider=lambda: None,
        routing_chain=lambda requested_role, model_name: None,
        on_answered=lambda model_id, label, free, routed: None,
        model_label=lambda model_id: model_id,
        on_auth_rejected=lambda provider_id: False,
        provider_label=lambda provider_id: provider_id,
        trust_check=lambda path: False,
        forbidden_check=None,
        trusted_roots=None,
        on_tool_audit=None,
        on_provider_attempt=None,
    ) -> None:
        self.model_router = model_router
        self.tool_registry = tool_registry
        self.permission_gate = permission_gate
        self.undo_manager = undo_manager
        self.stream_to_frontend = stream_to_frontend
        # Resolves the effective GuardConfig for THIS turn (Custom profile, D3), or
        # None for the fixed defaults (Simple/Developer — byte-for-byte today). A
        # zero-arg callable, wired like the other callbacks and reading the server's
        # one resolution function; None here (CLI/tests) means the unguarded gate.
        self._guards_provider = guards_provider
        # Emitted right before each tool runs so the shell can drive the Activity
        # Panel (tool.activityUpdate, §7). Called as (tool_id, label, detail), where
        # detail is the tool's own permission_detail for THIS call — None for the
        # tools that have nothing to name. The shell_bridge is the tools' only
        # route to OS effects (§1.3); None in CLI/test mode.
        self.on_activity = on_activity
        # Called after EACH provider call with its token usage (or None), the
        # wall-clock latency, and the RESOLVED (provider_id, model_id) of the
        # candidate that produced THAT call (D5 [N1] — fixes routed-turn
        # mis-attribution). Orchestrator machinery, the single choke point every
        # turn's model calls pass through — NEVER a registry tool.
        self.on_usage = on_usage
        # The Context Budget Manager's measurement half (§4.8 item 1), wired exactly
        # like on_usage and for the same reason: the orchestrator watches, the server
        # decides what to do with what it saw. Called after each provider call with
        # (used_tokens, max_context_tokens), the second value taken from the RESOLVED
        # provider's own ProviderCapabilities, never from a table anywhere, and None
        # when that provider reports no window (then the caller does nothing at all,
        # silently). Machinery, NEVER a registry tool: the model neither sees this nor
        # can ask for it.
        self.on_context_usage = on_context_usage
        self.shell_bridge = shell_bridge
        # The ordered fallback chain for a turn (D4), built by the server from the
        # active strategy + catalog + router pools (resolve_chain). Returns None when
        # unwired (CLI/tests) — then run_turn keeps today's single-provider path,
        # byte-for-byte. A wired-but-EMPTY list means "no candidate" (e.g. local_only
        # with no locals) and fails plainly; it never silently falls to a cloud call.
        self._routing_chain = routing_chain
        # Reports the answering candidate so the reply can carry answeredWith (D5): the
        # chip renders on ``free && routed``. ``model_label`` maps a model_id to its
        # human label for that chip and the fallback note.
        self.on_answered = on_answered
        self._model_label = model_label
        # Plan §5.2. Called with a provider id when THAT PROVIDER answered a send with
        # 401/403 — never on a 429, a 5xx, a timeout or a connection error. Returns
        # True only the first time, and that return is the whole idempotency
        # mechanism: a revoked key fails every turn, and the person is told once.
        # Wired like on_usage, for the same reason — the orchestrator must not learn
        # about SQLite. Unwired (CLI/tests) it records nothing and says nothing.
        self._on_auth_rejected = on_auth_rejected
        # A provider id -> the plain name a person would recognise ("Claude"), for the
        # sentence above. Alongside ``model_label`` because they are different nouns:
        # a key belongs to a provider, an answer comes from a model.
        self._provider_label = provider_label
        # Workspace-trust confinement (step 5, D3). Given a RESOLVED absolute path,
        # returns whether it may be touched right now (under a trusted root AND past
        # the data-dir floor). Store-backed, so it is wired in by the server
        # (rpc/workspace._is_trusted_path); the default refuses everything, so in
        # CLI/tests a path-bounded tool is confined to nothing until trust is wired —
        # the safe default. run_command has no affected_path, so this never governs it.
        # ``or`` a refuse-everything default, matching RoutineEngine: an explicit
        # None from a caller used to raise TypeError mid-turn rather than confine
        # to nothing, and the two call sites must not disagree about that.
        self._trust_check = trust_check or (lambda path: False)
        # The hardline denylist (step 5.5, item 3), wired the same way trust_check
        # is and for the same reason: it needs the LIVE data directory, which only
        # the server knows (rpc/workspace._is_forbidden_call). The default re-derives
        # it — correct only for a store-free construction, which is why it is a
        # named fallback rather than an inline expression.
        self._forbidden_check = forbidden_check or default_forbidden_check
        # The live trusted roots, as a zero-arg callable (step 5.5, item 2) — the
        # sandbox's write allowlist. Wired by the server (rpc/workspace._trusted_roots);
        # None here means no roots, which is the safe reading, not an open one.
        self._trusted_roots = trusted_roots
        # The tool-call audit sink (step 5.5, item 4): called with one row per tool
        # DECISION, on every branch including the refusals that never ran. Wired by
        # the server to the store; None (CLI/tests) simply records nothing. It is a
        # callback rather than a store handle for the same reason on_usage is: the
        # orchestrator must not learn about SQLite, and machinery — never a registry
        # tool — is the only thing allowed to write history.
        self._on_tool_audit = on_tool_audit
        # Same shape, same reason, for the other decision made on the person's
        # behalf: which model answered, and what went wrong when one did not.
        self._on_provider_attempt = on_provider_attempt
        # In-memory cooldown, per provider id: expiry monotonic timestamps. Advice,
        # never a lock ([S-a]) — an all-cooled chain is still tried in normal order.
        self._cooldowns: dict[str, float] = {}
        # Per-MODEL cooldown, for a candidate the provider says is not there
        # (ProviderModelGone / 404). Distinct from the provider map above because
        # "this model is retired" says nothing about its siblings: cooling all of
        # Google for a dead Gemini 2.5 would take Gemini 3 out with it.
        self._model_cooldowns: dict[str, float] = {}

    def run_turn(
        self,
        conversation: Conversation,
        requested_role: ModelRole | None = None,
        model_name: str | None = None,
        effort: str | None = None,
        mode: PolicyMode = PolicyMode.SAFE,
    ) -> None:
        # Per-turn resolution (§4.1.1). ``model_name`` is an EXPLICIT pick — among
        # several LOCAL models (item B) or several cloud models (§6.8) — a user toggle
        # or a Routine step's model_id; never a choice Addison makes in v1. ``effort``
        # is the per-message "answer style"; providers that don't support it ignore it.
        # ``mode`` (policy.py) is derived from the active profile: SAFE (default) is
        # the historical behaviour; OPEN surfaces dev-only tools and thins the gate.
        #
        # The guard posture for this whole turn (Custom profile, D3), resolved once:
        # a settings change lands on the worker thread serialised with the turn, so
        # it cannot shift mid-turn. None ≡ the fixed defaults ≡ today's gate.
        guards = self._guards_provider()
        # A "Not now" from an earlier turn must not silently deny this one:
        # each new user message may ask again (grants, by contrast, persist).
        self.permission_gate.clear_denials()
        context = ExecutionContext(
            conversation_id=conversation.id,
            shell_bridge=self.shell_bridge,
            policy_mode=mode,
            trusted_roots=self._trusted_roots,
        )
        chain = self._routing_chain(requested_role, model_name)
        if chain is None:
            # Unwired (CLI/tests): today's single-provider path, byte-for-byte —
            # one resolution, no fallback, no per-call timeout (existing fake
            # providers accept no ``timeout`` kwarg, and a healthy turn is identical).
            self._run_single(conversation, context, guards, mode, requested_role, model_name, effort)
        else:
            # The routed path (D4): walk the ordered chain, falling forward on
            # ProviderUnavailable within the per-turn budget, and report the
            # answering candidate (answeredWith, D5).
            self._run_with_fallback(
                conversation, context, guards, mode, chain, requested_role, model_name, effort
            )

    # --- single-provider path (freeze: CLI/tests, no routing chain) ---------
    def _run_single(
        self, conversation, context, guards, mode, requested_role, model_name, effort
    ) -> None:
        provider = self.model_router.resolve(requested_role, model_name)
        provider_id, model_id = self._single_identity(requested_role, model_name)
        # Bounded, not ``while True``: see _MAX_TOOL_ROUNDS and _MAX_TOOL_CALLS. The
        # loop is driven by the model, and what the model reads between rounds
        # includes untrusted page text, so neither "how many times round" nor "how
        # many at once" may be the page's decision.
        calls_made = 0
        budget_spent = False
        relay = _DeltaRelay(self.stream_to_frontend)
        for _round in range(_MAX_TOOL_ROUNDS):
            started = time.monotonic()
            relay.begin_send()
            # REDACTION (step 5.5, item 4) at the one place every provider is fed.
            # A throwaway view goes on the wire; conversation.messages — the
            # person's own record, and the SQLite rows behind it — keeps the real
            # bytes. Doing this here rather than in each provider's
            # _translate_history means a provider added later cannot miss it.
            outbound, _ = redacted_for_model(conversation.messages)
            response = provider.send(
                messages=outbound,
                # The model only ever sees the tools visible in this mode — SAFE
                # hides every dev-only tool, so it can't even request run_command.
                tools=self.tool_registry.visible_tools(mode),
                effort=effort,
                on_delta=relay,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            # Record this call's usage + latency at the single choke point (§4.8).
            self.on_usage(response.usage, latency_ms, provider_id, model_id)
            # The Context Budget Manager's measurement, at the same choke point and
            # against the RESOLVED provider's own window (§4.8 item 1).
            self._report_context_usage(response.usage, provider)
            if response.tool_calls:
                calls_made, budget_spent = self._run_tool_calls(
                    conversation, response, context, guards, mode, provider, calls_made
                )
                if budget_spent:
                    break
                continue
            conversation.append_assistant_message(response.text)
            # Already relayed as it arrived, unless this provider ignored on_delta —
            # then the finished text is the reader's only copy of the answer.
            if not relay.shown_this_send:
                # Through the relay, not past it: this provider ignored on_delta, so
                # the finished text is a SEGMENT like any other and needs the same
                # break in front of it when the turn has already said something.
                # (``or ""`` for the None a tool-call-only response carries — the
                # relay drops empty text, so nothing is shown and no break is minted.)
                relay(response.text or "")
            # A single-path answer is the model the caller picked, so it is not routed.
            self.on_answered(model_id, self._model_label(model_id), False, False)
            break
        else:
            # Rounds exhausted. Close the turn honestly rather than leaving the
            # transcript ending on tool results with nothing said to the person.
            budget_spent = True
        if budget_spent:
            self._finish_over_budget(conversation, relay)

    # --- routed path with graceful fallback + cooldown (D4) -----------------
    def _run_with_fallback(
        self, conversation, context, guards, mode, chain, requested_role, model_name, effort
    ) -> None:
        turn_started = time.monotonic()
        # Cooldown-filter the chain, but never lock: if EVERYTHING is cooled, try the
        # whole chain anyway, in normal (preferred-first) order ([S-a]).
        active = [
            c for c in chain
            if not self._is_cooled(c.provider_id) and not self._is_model_cooled(c.model_id)
        ] or list(chain)
        # ``preferred`` is the PRE-filter head: what the user's settings say should
        # answer. A head cooled by a previous turn still counts as "what you
        # expected" — without this, a cooled head silently hands the turn to a
        # weaker model with NO note, the exact quiet substitution the note exists
        # to surface (post-build adversarial pass, 2026-07-24).
        preferred = chain[0] if chain else None
        idx = 0
        committed: str | None = None   # provider id locked once a tool round completes
        noted = False
        # Set when the chain head — what the person's settings say should answer — was
        # skipped because it rejected Addison's key. The rejection sentence already
        # explains the substitution, and explains it better than "was busy", which
        # would be a plain falsehood about a revoked key (§5.2).
        preferred_rejected = False
        last_unavailable: RuntimeError | None = None
        answered: RoutingCandidate | None = None
        calls_made = 0
        budget_spent = False
        relay = _DeltaRelay(self.stream_to_frontend)

        for _round in range(_MAX_TOOL_ROUNDS):
            response = None
            candidate: RoutingCandidate | None = None
            provider = None
            latency_ms = 0
            # Walk the chain for THIS send. Advance ONLY on ProviderUnavailable;
            # Rejected/AuthFailed propagate immediately (the next provider gets the
            # same bad request / bad key — no walk). Continuation, never restart:
            # conversation state is intact and only the provider changes.
            while True:
                remaining = _FALLBACK_BUDGET_SECONDS - (time.monotonic() - turn_started)
                if remaining <= 0 or idx >= len(active):
                    # Budget spent, or the chain is exhausted -> fail plainly with the
                    # last provider's own sentence (more specific than the generic).
                    raise last_unavailable or ProviderUnavailable(_NO_MODEL_REACHABLE)
                cand = active[idx]
                if committed is not None and cand.provider_id != committed:
                    # Cross-provider mid-turn advance is forbidden (foreign tool_use
                    # history replayed into another vendor's translator is unverified):
                    # skip past other providers looking for a SAME-provider candidate
                    # (the two-Ollama case, [MF-E]); exhausting the list fails plainly.
                    idx += 1
                    continue
                provider = self.model_router.resolve(cand.role, cand.model_id)
                started = time.monotonic()
                relay.begin_send()
                try:
                    outbound, _ = redacted_for_model(conversation.messages)
                    response = provider.send(
                        messages=outbound,
                        tools=self.tool_registry.visible_tools(mode),
                        effort=effort,
                        # [MF-A] a real per-attempt deadline: the provider clamps this
                        # to its own default, so a healthy first send is byte-identical
                        # to today, and no single hanging candidate can blow the budget.
                        timeout=remaining,
                        on_delta=relay,
                    )
                except ProviderKeyRejected as exc:
                    # §5.2. The provider ANSWERED, and said the key is no good — the
                    # one failure that is definitive evidence about a stored key. Two
                    # things follow, in this order:
                    #
                    #   1. Record it, once. The callback returns True only the first
                    #      time, so a key that has been revoked for a week does not
                    #      produce a fresh notice on every turn.
                    #   2. Degrade EXACTLY as an unavailable provider does — cool it,
                    #      advance the index — because that is the mechanism that
                    #      already exists and another provider holds a different key.
                    #      The original "no walk" rule (base.py) reasoned that the next
                    #      provider gets the same bad key; that is true of a MISSING
                    #      key and false of a rejected one, which is why only the
                    #      narrow subclass walks and plain ProviderAuthFailed still
                    #      propagates untouched.
                    self._record_attempt(conversation, cand, "key_rejected", exc)
                    if self._on_auth_rejected(cand.provider_id):
                        self.on_activity(
                            _ROUTING_ACTIVITY_ID,
                            _KEY_REJECTED_NOTE.format(
                                provider=self._provider_label(cand.provider_id)
                            ),
                        )
                    if preferred is not None and cand.provider_id == preferred.provider_id:
                        preferred_rejected = True
                    if relay.shown_this_turn:
                        raise
                    last_unavailable = exc
                    self._cool(cand.provider_id)
                    idx += 1
                    continue
                except ProviderUnavailable as exc:
                    # Text already on the reader's screen bars the walk, for the same
                    # reason `committed` bars a cross-provider advance: the next
                    # candidate would produce a COMPLETE answer, which appends to the
                    # partial one and yields a single message that reads as one answer
                    # and is two. There is no way to unsay what was already shown —
                    # the frontend's overlay may lag the truth but never rewinds it —
                    # so the honest move is to fail with this provider's own sentence.
                    # A stream that died before emitting anything showed nothing, so
                    # that case falls forward exactly as it always has.
                    # A model the provider says is NOT THERE cools that MODEL and
                    # leaves its siblings alone; anything else is about the provider.
                    gone = isinstance(exc, ProviderModelGone)
                    self._record_attempt(
                        conversation, cand, "model_gone" if gone else "unavailable", exc
                    )
                    if relay.shown_this_turn:
                        raise
                    last_unavailable = exc
                    if gone:
                        self._cool_model(cand.model_id)
                    else:
                        self._cool(cand.provider_id)
                    idx += 1
                    continue
                except (ProviderRequestRejected, ProviderAuthFailed) as exc:
                    # RECORD AND RE-RAISE — the control flow is deliberately
                    # unchanged. These two end the turn (D4): the next provider gets
                    # the identical bad request, or there was no key to send and
                    # another provider will not supply one.
                    #
                    # This clause exists ONLY so they leave a trace. Until it did,
                    # these were the failures with no record anywhere: they never
                    # reach `usage_log` (that is successes), they raise straight past
                    # the fallback note, and the error frame that carries them is
                    # gone the moment the person sends the next message. A real 404
                    # went undiagnosed for an evening because of exactly this gap.
                    #
                    # It must sit AFTER the ProviderKeyRejected clause: that class is
                    # a ProviderAuthFailed subclass, and Python takes the first match,
                    # so ordering is what keeps a rejected key on its own path.
                    self._record_attempt(
                        conversation,
                        cand,
                        "rejected" if isinstance(exc, ProviderRequestRejected) else "auth_failed",
                        exc,
                    )
                    raise
                latency_ms = int((time.monotonic() - started) * 1000)
                candidate = cand
                break

            # The inner loop only breaks with both set (every other path raises).
            assert candidate is not None and response is not None
            self.on_usage(response.usage, latency_ms, candidate.provider_id, candidate.model_id)
            # …and the same call measured against THIS candidate's own window (§4.8).
            # ``provider`` is the resolved provider of the candidate that answered, so
            # a turn that fell forward is judged by the window of the model that
            # actually ran, never the one the settings preferred.
            self._report_context_usage(response.usage, provider)
            # The fallback note, once: emitted when a candidate other than the one the
            # user expected (the preferred head) produced the answer (D4/D8).
            if (
                not noted
                and not preferred_rejected
                and preferred is not None
                and candidate.model_id != preferred.model_id
            ):
                self._emit_fallback_note(preferred, candidate)
                noted = True

            if response.tool_calls:
                calls_made, budget_spent = self._run_tool_calls(
                    conversation, response, context, guards, mode, provider, calls_made
                )
                # A tool round just completed against this candidate: from here on a
                # mid-turn failure may only advance within the same provider id.
                committed = candidate.provider_id
                if budget_spent:
                    break
                continue
            conversation.append_assistant_message(response.text)
            # Already relayed as it arrived, unless this provider ignored on_delta.
            if not relay.shown_this_send:
                # Through the relay, not past it: this provider ignored on_delta, so
                # the finished text is a SEGMENT like any other and needs the same
                # break in front of it when the turn has already said something.
                # (``or ""`` for the None a tool-call-only response carries — the
                # relay drops empty text, so nothing is shown and no break is minted.)
                relay(response.text or "")
            answered = candidate
            break
        else:
            budget_spent = True

        if budget_spent:
            self._finish_over_budget(conversation, relay)
            return
        if answered is not None:
            # [S-b] routed == (the answering model differs from the user's explicit
            # pick). No explicit pick (model_name None) -> routed True; an explicit
            # pick that ANSWERED -> False; one that FELL FORWARD -> True. The chip
            # renders on ``free && routed`` (a free answer the user did not choose).
            routed = answered.model_id != model_name
            self.on_answered(
                answered.model_id, self._model_label(answered.model_id), answered.free, routed
            )

    def _audit(
        self, conversation, tool_id, detail, mode, destructive, outcome, redacted=None,
        screened=None,
    ) -> None:
        """Record one tool decision. BEST-EFFORT, ALWAYS: a failure to write history
        must never be the reason a person's turn dies, so every exception is
        swallowed here rather than at each of the six call sites.

        ``auditing`` is public for the one caller that must not even BUILD its
        argument when there is no sink — see the granted branch."""
        if self._on_tool_audit is None:
            return
        try:
            self._on_tool_audit(
                {
                    "id": str(uuid.uuid4()),
                    "conversation_id": getattr(conversation, "id", None),
                    "tool_id": tool_id,
                    "detail": detail,
                    "mode": mode.value if hasattr(mode, "value") else str(mode),
                    "destructive": bool(destructive),
                    "outcome": outcome,
                    # KINDS, deduplicated — one entry per kind, never one per
                    # match. ``redact`` reports every hit, so a command that prints
                    # 2000 keys produced a 40KB string of the same three words
                    # repeated, in a table that is excluded from snapshots and
                    # never pruned. Sorted so the same set is always the same row.
                    "redacted": ", ".join(sorted(set(redacted))) if redacted else None,
                    # KINDS ONLY, exactly like ``redacted`` beside it and for the
                    # same reason: the row is durable and never pruned, so quoting
                    # what was found would persist somebody's payload forever.
                    # Deduplicated and sorted so one set is always one row value.
                    "screened": ", ".join(sorted(set(screened))) if screened else None,
                    "created_at": int(time.time()),
                }
            )
        except Exception:
            pass

    @property
    def auditing(self) -> bool:
        """Whether an audit sink is wired at all. Read by the granted branch so it
        can skip BUILDING the ``redacted`` argument — that argument is a full
        9-regex pass over up to _MAX_OUTPUT_CHARS of tool output, and as a plain
        argument it was evaluated before ``_audit``'s early-out could discard it,
        on every CLI and test turn."""
        return self._on_tool_audit is not None

    def _finish_over_budget(self, conversation, relay: "_DeltaRelay") -> None:
        # Same sentence for both ceilings: the person does not care which counter ran
        # out, only that Addison stopped and is saying so.
        conversation.append_assistant_message(_TOO_MANY_STEPS)
        # Also a segment, and a new one: a turn that stops here has usually already
        # said something before its first tool call, and the sentence must not fuse
        # onto it. ``begin_send`` is how the relay is told a fresh utterance starts —
        # this sentence is Addison's own, not the tail of the send that preceded it.
        relay.begin_send()
        relay(_TOO_MANY_STEPS)

    def _run_tool_calls(
        self, conversation, response, context, guards, mode, provider, calls_made
    ) -> tuple[int, bool]:
        """Run one response's tool_calls (shared by both turn paths). Returns the
        updated ``calls_made`` and whether the per-turn CALL budget was spent."""
        budget_spent = False
        # Record the assistant's tool-call turn BEFORE its results so that each
        # tool_result pairs with the tool_use it answers (§4.4).
        conversation.append_assistant_tool_calls(response.text, response.tool_calls)
        for call in response.tool_calls:
            if calls_made >= _MAX_TOOL_CALLS:
                # Budget spent. Answer this tool_use so the pairing holds, but run
                # nothing: the point of the ceiling is that no further request leaves
                # the machine, so the check sits ABOVE the gate and the tool.
                budget_spent = True
                conversation.append_tool_result(
                    call.id, ToolResult(success=False, content=_STEP_NOT_RUN)
                )
                continue
            calls_made += 1
            # AN ID NOTHING IS REGISTERED UNDER, refused rather than raised. A
            # tool_use can name a tool that existed when the model last saw the
            # list: every `mcp:` id leaves the registry on a refresh, a removal, a
            # failed check or a snapshot restore, and the transcript it was used in
            # survives all four. Crashing here is the one failure this whole path is
            # built to avoid — a tool_use with no tool_result, which the provider
            # rejects on every later request of the session (see the granted branch
            # below) — so an unknown id takes the same shape as every other refusal.
            #
            # `detail` is None and `destructive` False because there is no tool to
            # ask either question of: nothing about this call was examined and
            # nothing ran. The outcome is `not_callable`, the vocabulary's value for
            # "named a tool with no dispatch behind it", which an id belonging to
            # nothing also is.
            tool = self.tool_registry.find(call.tool_id)
            if tool is None:
                self._audit(
                    conversation, call.tool_id, None, mode, False, "not_callable"
                )
                conversation.append_tool_result(
                    call.id, ToolResult(success=False, content=UNKNOWN_TOOL_REFUSAL)
                )
                continue
            # SAFE-1 at dispatch: visible_tools hides dev-only tools from the model,
            # but a tool_use naming a hidden id still reaches here, and the gate does
            # not check dev-ness. Refuse BEFORE the gate and before execute, so the
            # boundary does not depend on each dev tool remembering to check the mode.
            dev_only_refusal = self.tool_registry.refuse_if_dev_only_outside_open(
                call.tool_id, mode
            )
            # Per-call destructiveness, resolved ONCE for every branch below.
            # Each refusal branch used to hard-code a literal here — True for
            # forbidden, False for confined_out and dev_only — which made the
            # column a description of the BRANCH rather than of the call, in
            # exactly the rows the log exists for. The tool already answers this
            # question (tools/base.call_is_destructive); ask it.
            destructive = call_is_destructive(tool, call.args)
            # THE PATH, RESOLVED ONCE FOR THE WHOLE CALL — the label's and the
            # boundary's, one answer (KNOWN-GAPS, closed 2026-08-08 with the review
            # surface's read paths). This used to sit down at the confinement branch,
            # so every audit row above it that named a file re-resolved the path
            # inside `permission_detail`, and a symlink swapped between the two could
            # put a name on the card that was true only when it was read. Confinement
            # still asks its own question below and still hands this value to
            # `execute` (R6); what moved is only WHERE the single resolution happens,
            # so that everything downstream of here can be handed it. Resolving costs
            # a realpath and cannot raise (`call_affected_path` never does), so the
            # refusal branches pay nothing for it that they were not already paying.
            # None for every tool without an `affected_path` — run_command included —
            # which is what leaves those tools completely unaffected.
            affected = call_affected_path(tool, call.args)
            if dev_only_refusal is not None:
                self._audit(conversation, call.tool_id, None, mode, destructive, "dev_only")
                conversation.append_tool_result(
                    call.id, ToolResult(success=False, content=dev_only_refusal)
                )
                continue
            # DISCOVERED BUT NOT WIRED: a tool registered so the person can see it,
            # with no dispatch behind it, refused here so nothing can run it. Above
            # the gate and above the denylist for the reason the dev-only check is —
            # nothing about this call should be examined, approved or reached over
            # the network.
            #
            # QUIET FOR MCP SINCE PHASE 3, and still the mechanism: MCP tools no
            # longer register `not_callable` (mcp_catalog.MCP_TOOLS_ARE_CALLABLE),
            # so this branch is what turning that constant back off operates
            # through, and what the next externally-sourced tool inherits. The audit
            # row is phase 3's too — the branch used to write nothing, because the
            # vocabulary had no value for it and widening a CHECK is a migration.
            not_callable_refusal = self.tool_registry.refuse_if_not_callable(call.tool_id)
            if not_callable_refusal is not None:
                self._audit(
                    conversation, call.tool_id, None, mode, destructive, "not_callable"
                )
                conversation.append_tool_result(
                    call.id, ToolResult(success=False, content=not_callable_refusal)
                )
                continue
            # THE HARDLINE DENYLIST (step 5.5, item 3), above the gate and above
            # confinement: a call naming Addison's own restore storage or the user's
            # credential stores does not happen, and is not offered as a card. It is
            # first because a forbidden call must never be shown to the person as
            # something they could approve — the gate is not consulted at all.
            forbidden = self._forbidden_check(tool, call.args)
            if forbidden is not None:
                # The one outcome with no card behind it — so the audit row is the
                # ONLY place a forbidden call is ever recorded (KNOWN-GAPS: it is
                # invisible outside the transcript today).
                self._audit(
                    conversation, call.tool_id,
                    call_permission_detail(tool, call.args, affected), mode, destructive,
                    "forbidden",
                )
                conversation.append_tool_result(
                    call.id, ToolResult(success=False, content=forbidden)
                )
                continue
            # THE ARMING DOOR (step 8 phase 3, G2), above the gate for the denylist's
            # reason and one of its own. A call that cannot succeed — no launchd on
            # this computer, a row that is not saved any more, a schedule nothing
            # could run — must never be shown to somebody as a thing they might
            # approve, and here that "thing" is a ceremony: reading a preview and
            # retyping a code. Answering None for every tool but the two arming ones
            # (`permissions.gate.call_arming_refusal`), so nothing else changes.
            #
            # The audit outcome is `forbidden`: the vocabulary's value for "refused
            # before the gate, with no card behind it", which is exactly what this
            # is. (`schema.sql` owns that vocabulary; widening it is a migration.)
            arming_refusal = call_arming_refusal(tool, call.args)
            if arming_refusal is not None:
                self._audit(
                    conversation, call.tool_id,
                    call_permission_detail(tool, call.args, affected), mode, destructive,
                    "forbidden",
                )
                conversation.append_tool_result(
                    call.id, ToolResult(success=False, content=arming_refusal)
                )
                continue
            # CONFINEMENT (step 5, D3): a path-bounded tool (non-None affected_path)
            # may only ever run INSIDE a currently-trusted root. Hard-refuse before
            # the gate and before execute if it is not trusted (permission-to-touch,
            # separate from the card). The path was resolved ONCE, above the refusal
            # branches, and this checks THAT value; it then rides on the context so
            # execute acts on the exact path checked — never a re-read of
            # args["path"] (R6, TOCTOU).
            trusted = bool(affected) and self._trust_check(affected)
            if affected is not None and not trusted:
                self._audit(
                    conversation, call.tool_id,
                    call_permission_detail(tool, call.args, affected), mode, destructive,
                    "confined_out",
                )
                conversation.append_tool_result(
                    call.id, ToolResult(success=False, content=_OUTSIDE_TRUST)
                )
                continue
            context.resolved_path = affected
            # Mode-aware authorization (policy.py): SAFE prompts for every
            # not-yet-granted tool; OPEN auto-allows non-destructive calls and prompts
            # PER INVOCATION for destructive ones (the card shows the exact command via
            # `detail`). A confined, trusted file edit passes `trusted=True` so the
            # gate auto-grants it card-free (§8.3). Destructiveness is per-call
            # (run_command and write_project_file classify their own; else HIGH).
            # ``destructive`` was resolved above the refusal branches, so the gate
            # and every audit row agree on one answer per call.
            # Asked once and used twice, on purpose: the permission card and the
            # Activity Panel must describe the SAME call. Calling the tool's
            # permission_detail a second time could describe a different one if it ever
            # stops being a pure read of args. `affected` rides along for the same
            # reason one step further down: a path tool's detail is made FROM the
            # resolved path, and this is the value confinement just approved.
            detail = call_permission_detail(tool, call.args, affected)
            # THE KEYWORD CARD'S PREVIEW (step 8 phase 3). None for every tool but
            # `arm_automation` — including `disarm_automation`, whose card is
            # ordinary because a tightening must never be something a code can trap
            # somebody out of. Non-None makes the gate take the arming path and
            # nothing else (gate.authorize), whatever the guards say.
            status = self.permission_gate.authorize(
                call.tool_id,
                mode=mode,
                destructive=destructive,
                detail=detail,
                guards=guards,
                trusted=trusted,
                arming=call_arming_card(tool, call.args),
                # Asked of the TOOL, so a preview that could not be built refuses
                # instead of quietly becoming an ordinary card (gate.py owns why).
                requires_arming=tool_requires_arming(tool),
                # THE DELETE PREVIEW (5.6, first form; delete_preview.py owns it and
                # its "when in doubt, say nothing" rule). One extra plain line under
                # the command ("About to delete 1,240 files in 12 folders.") for a
                # command this can confidently read as a delete with paths it can
                # name. Nothing is executed and no decision here changes: it is
                # None for every other call, which is every card the app has shown
                # until now. run_command is the only tool with `command_text`, and
                # it is dev_only, so this cannot reach Simple.
                preview=delete_preview.preview_for_command(
                    call_command_text(tool, call.args), context.shell_bridge
                ),
            )  # may block for UI
            if status == PermissionStatus.DENIED:
                self._audit(
                    conversation, call.tool_id, detail, mode, destructive, "denied"
                )
                # Steer the model past the refusal: "not now" declines the STEP, not
                # the request — anything already gathered (search results, a
                # calculation) should be delivered in chat.
                result = ToolResult(
                    success=False,
                    content=(
                        "User declined this step. Do not ask again this turn. "
                        "Finish the request without it — if you already found "
                        "the information, give it directly in your reply."
                    ),
                )
            else:
                # `detail` rides along so the panel can name the destination, not just
                # the step: a granted tool id is re-usable for the rest of the session,
                # so after the first "Allow" the panel is where the person is told
                # WHERE a call is going (§8, owner decision 2026-07-20 — visibility over
                # per-site grants). The routine engine emits the same three fields for
                # the same reason; these are the two places a tool call is announced,
                # and they must not diverge.
                #
                # Be precise about what this buys, because it is easy to over-read: it
                # names the SITE, never the payload. A read that carries data outward in
                # the path or query of an ordinary-looking host is indistinguishable
                # here from an honest read of that host. It catches an unfamiliar
                # destination, not a familiar one being misused. Bounding WHO can be
                # reached is a grant-scoping change and is still open.
                self.on_activity(call.tool_id, tool.definition.label, detail)
                # The same announcement, written down instead of emitted. A panel
                # that is gone the moment the app closes is what made a turn's
                # steps unsaveable after a reload (KNOWN-BUGS #5); this is the only
                # place that knows a step both ran and was described this way, so
                # it is the only place that can say so honestly.
                conversation.note_step_shown(call.id, detail)
                # A tool/bridge failure is a FAILED STEP, never a crashed turn:
                # crashing here would leave this tool_use with no tool_result, and the
                # provider then rejects every later request (API 400) until restart.
                try:
                    result = tool.execute(call.args, context)
                except RuntimeError as exc:
                    # Bridge refusals carry a plain user-ready sentence (e.g. "A file
                    # with that name is already there…").
                    result = ToolResult(success=False, content=str(exc))
                except Exception:
                    result = ToolResult(
                        success=False, content="That step didn't work, so it was skipped."
                    )
                else:
                    if result.snapshot:
                        result.snapshot.tool_call_id = call.id
                        self.undo_manager.record(result.snapshot)
                    result = self._gate_image_result(result, provider)
                # UNTRUSTED-CONTENT SCREENING (design-doc §11), the one place it
                # happens. ORDER, and it is the order the code reads in: a tool
                # cleans and trims its own output where it already does that
                # (mcp_client), THEN this screens what actually came back, THEN the
                # redact-classify below describes the same bytes for the audit row.
                # Screening before the cap would examine text the model never sees;
                # after the append it would be a note about a passage already read.
                #
                # Only ``content_origin == "external"`` is screened — a stranger's
                # writing. Addison's own sentences (a refusal, a calculator answer,
                # the denied-step steer above) are not screened for the same reason
                # the redactor's own markers are not: marking Addison's words as
                # untrusted teaches the model to discount the mark.
                #
                # The mark PREFIXES, never removes (screening.py owns why), and it
                # survives ``redacted_for_model``'s per-turn re-walk by
                # construction: that walk rewrites message text only where a
                # credential pattern matches, and the marker contains none.
                screened_kinds: tuple[str, ...] = ()
                if result.content_origin == "external":
                    as_text = _result_as_text(result.content)
                    # SCREENED ON THE STRINGS, MARKED ON THE SERIALIZATION. Both
                    # halves matter and the first one is not obvious: every rule in
                    # screening.py is anchored on a word boundary or a line start,
                    # and `json.dumps` turns a newline into the two characters
                    # backslash-n — which GLUES that "n" onto the next word. A page
                    # whose injection opens a line ("…Italy.\nIgnore all previous
                    # instructions") serializes to "…Italy.\\nIgnore…", where
                    # "nIgnore" is one token, no boundary precedes "Ignore", and the
                    # override rule stops matching. Every line-anchored rule is lost
                    # the same way, since the document becomes one line. So the
                    # screener reads the leaves as the page wrote them
                    # (`_screenable_text`) and the MARK goes in front of the text the
                    # model is actually handed.
                    found = screen(_screenable_text(result.content))
                    if found.flagged:
                        screened_kinds = found.kinds
                        # The MODEL's copy is the marked text. A dict result becomes
                        # its own JSON with the note in front — the same string
                        # ``append_tool_result`` would have produced, so nothing but
                        # the note changes about what is read.
                        result = replace(
                            result, content=mark_untrusted(as_text, found)
                        )
                        # The PERSON hears about it once per flagged step, on the
                        # channel the free-model and fallback notes already use.
                        self.on_activity(_SCREENING_ACTIVITY_ID, _SCREENING_NOTE)
                # The granted branch, audited AFTER execution so the row can name
                # what THIS call's output contained. Attributing it to the previous
                # outbound send was wrong by one round: a tool's output is scrubbed
                # on the NEXT send, so the send before it carried nothing of this
                # tool's. `redact` is re-run on the result purely to classify it —
                # the actual scrubbing still happens once, at the send boundary, so
                # every message is covered and not just tool results. Kinds only.
                #
                # Guarded by ``self.auditing`` because an ARGUMENT is evaluated
                # before the callee's early-out can discard it: with no sink wired
                # (every CLI turn, every test that does not pass one) this ran a
                # 9-regex pass over up to _MAX_OUTPUT_CHARS of output and threw the
                # answer away. The classification is only ever read by the row.
                #
                # TWO FIELDS THE RESULT MAY CARRY (step 7 phase 3), both None/empty
                # for every native tool, so no existing row changes shape:
                # `redacted_kinds` when a tool scrubbed its OWN output (re-running
                # the redactor over already-clean text finds nothing, and the row
                # would then deny that a credential came back), and `audit_outcome`
                # when the tool's own failure is a fact this vocabulary can express
                # and the gate's decision cannot — an MCP call the gate approved and
                # that never reached the server is 'failed', not 'granted'.
                self._audit(
                    conversation, call.tool_id, detail, mode, destructive,
                    result.audit_outcome or "granted",
                    redacted=(
                        result.redacted_kinds
                        or (
                            redact(_result_as_text(result.content)).kinds
                            if self.auditing
                            else None
                        )
                    ),
                    screened=screened_kinds or None,
                )
            conversation.append_tool_result(call.id, result)
        return calls_made, budget_spent

    def _is_model_cooled(self, model_id: str) -> bool:
        expiry = self._model_cooldowns.get(model_id)
        return expiry is not None and time.monotonic() < expiry

    def _cool_model(self, model_id: str) -> None:
        """Stand this MODEL down for a while. A retired model is not coming back in
        sixty seconds, but the map is in-memory and advisory ([S-a]) — the same
        stance as the provider cooldown, and an all-cooled chain is still tried in
        normal order rather than left with nothing."""
        self._model_cooldowns[model_id] = time.monotonic() + _COOLDOWN_SECONDS

    def _record_attempt(self, conversation, candidate, outcome: str, exc: BaseException) -> None:
        """One row for a provider call that failed. Best-effort, always.

        Swallowing here is the same rule the tool audit follows and it matters more
        on this path: every caller is already handling a failure, and an exception
        raised while recording one would replace a provider problem the person can
        act on with a crash they cannot. A missing row loses history; a raise here
        would lose the turn.

        The MESSAGE is the plain sentence the person saw, so the row and the screen
        cannot tell different stories, and the status code rides alongside it —
        `str(exc)` says "Google is busy right now", `status_code` says 404, and only
        together do they say the message was wrong.

        IT ALSO STAMPS THE EXCEPTION, before anything else and regardless of whether
        a sink is wired. The row is history; the exception is on its way to the
        person, and the Developer profile's "Technical details" fold has the same
        need this row does — it can read the status and the server's sentence off the
        exception already, and WHICH PROVIDER is the one fact that exists nowhere but
        here (``note_candidate`` says why). Every failure path in the walk above
        passes through this method, so stamping here covers all four rather than
        four near-copies at the ``except`` clauses."""
        note_candidate(exc, candidate.provider_id, candidate.model_id)
        if self._on_provider_attempt is None:
            return
        try:
            self._on_provider_attempt(
                {
                    "id": str(uuid.uuid4()),
                    "conversation_id": getattr(conversation, "id", None),
                    "provider": candidate.provider_id,
                    "model": candidate.model_id,
                    "outcome": outcome,
                    "status_code": status_code_of(exc),
                    "detail": str(exc) or None,
                    "server_detail": server_detail_of(exc),
                    "created_at": int(time.time()),
                }
            )
        except Exception:
            pass

    # --- cooldown + note helpers (D4) --------------------------------------
    def _is_cooled(self, provider_id: str) -> bool:
        expiry = self._cooldowns.get(provider_id)
        return expiry is not None and time.monotonic() < expiry

    def _cool(self, provider_id: str) -> None:
        self._cooldowns[provider_id] = time.monotonic() + _COOLDOWN_SECONDS

    def _emit_fallback_note(self, busy: RoutingCandidate, used: RoutingCandidate) -> None:
        note = _FALLBACK_NOTE.format(
            busy=self._model_label(busy.model_id), used=self._model_label(used.model_id)
        )
        self.on_activity(_ROUTING_ACTIVITY_ID, note)

    def _report_context_usage(self, usage, provider) -> None:
        """Hand one call's size and the answering model's window to the watcher (§4.8).

        Best-effort by design, and it is the FIRST rule of §4.8 that makes it so: a
        provider that cannot say how much it holds means "cannot tell", and cannot-tell
        means do nothing, silently. So a missing usage report, a provider whose
        ``capabilities()`` raises, and a window of None all end the same way, nothing
        reported, nothing decided, and the turn entirely unaffected.

        ``used_tokens`` is input plus output because that sum is what the NEXT request
        replays: this turn's prompt plus the answer just added to it."""
        if usage is None:
            return
        try:
            limit = provider.capabilities().max_context_tokens
        except Exception:
            return
        try:
            used = int(usage.input_tokens or 0) + int(usage.output_tokens or 0)
        except (TypeError, ValueError):
            return
        self.on_context_usage(used, limit)

    def _single_identity(self, requested_role, model_name) -> tuple[str, str]:
        """Best-effort (provider_id, model_id) for the unwired single path — used only
        by CLI/tests (production always wires ``routing_chain``, where the resolved
        candidate carries the true identity). Mirrors the old role-based mapping."""
        role = requested_role or ModelRole.PRIMARY
        if role is ModelRole.SETUP_ASSISTANT:
            return "setup_assistant", (model_name or "setup")
        if role is ModelRole.LOCAL:
            return "ollama", (model_name or "local")
        return "anthropic", (model_name or "default")

    def _gate_image_result(self, result: ToolResult, provider) -> ToolResult:
        """(A) Vision gate (§4.1.1 item A): don't feed a picture to a model that
        can't see it. When a tool result's content is an image (the shell reports
        ``{"kind": "image", ...}``) and the active provider reports
        ``vision=False``, replace the content with a plain-language notice and
        surface it — a WARNING plus an explicit manual switch, NEVER an automatic
        model change (that's v2). Any other result passes through untouched."""
        content = result.content
        if not (isinstance(content, dict) and content.get("kind") == "image"):
            return result
        if provider.capabilities().vision:
            return result
        notice = (
            "This file is a picture, and the model you're using can't look at "
            "pictures. Switch to a vision-capable model and try again."
        )
        self.stream_to_frontend(notice)
        return ToolResult(success=False, content=notice)
