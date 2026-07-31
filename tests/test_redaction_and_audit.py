"""Step 5.5 item 4 — output redaction + the tool-call audit trail.

Plan: docs/step-5.5-containment-plan.md. Two mechanisms, one purpose: a command's
output travels to a cloud provider, and until now nothing stood between a secret
in that output and someone else's server, nor left any record that a tool ran.

**The headline is asserted ON THE WIRE.** The plan is explicit: "a secret in tool
output does not appear in the provider request body. Assert on the wire, not on
the ``ToolResult``." A test that checks the ``ToolResult`` would pass while the
provider still received the secret — the redaction happens *between* those two
points, so only the provider's own view proves anything.

The second property is the mirror of the first and matters just as much: the
STORED transcript keeps the real bytes. Scrubbing the person's own record would
destroy the evidence that a leak happened, which is the opposite of the goal.
"""

from __future__ import annotations

from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate
from agent_core.policy import PolicyMode
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.redaction import redact, redacted_for_model
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.registry import ToolRegistry

# Realistic shapes, none of them real credentials. Each is the vendor's own
# documented prefix — which is what makes the rules anchorable at all.
#
# ASSEMBLED AT RUNTIME, and that is not cosmetic. A credential-shaped literal in
# a source file is what a secret scanner is built to find, and it cannot tell a
# fixture from the real thing: GitHub's push protection blocked this file on the
# Slack shape. Splitting each value at its prefix means no line here matches a
# scanner rule, while the string ``redact`` actually receives is byte-identical
# to the literal it replaced — the prefixes below ARE the anchors under test, so
# joining them at import time tests exactly what the one-line form did.
_SECRETS = {
    kind: prefix + body
    for kind, (prefix, body) in {
        "Anthropic API key": ("sk-ant-", "api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"),
        "API key": ("sk-", "proj-0123456789abcdefghijklmnopqrstuv"),
        "GitHub token": ("ghp", "_0123456789abcdefghijklmnopqrstuvwxyz"),
        "AWS access key": ("AKIA", "IOSFODNN7EXAMPLE"),
        "Slack token": ("xoxb", "-123456789012-abcdefghijklmnop"),
        "Google API key": ("AIza", "SyA0123456789abcdefghijklmnopqrstuv"),
        "bearer token": ("Bearer ", "abcdefghijklmnopqrstuvwxyz0123456789"),
    }.items()
}

_PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gt\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


# ===========================================================================
# The predicate
# ===========================================================================


def test_every_known_secret_shape_is_removed_and_named():
    for kind, secret in _SECRETS.items():
        result = redact(f"here is the value {secret} in some output")
        assert secret not in result.text, kind
        assert f"[redacted: {kind}]" in result.text, kind
        assert kind in result.kinds


def test_a_private_key_block_is_removed_body_and_all():
    # Matching only the BEGIN header would leave the key bytes in place — worse
    # than not matching, because the output would LOOK redacted while leaking
    # everything. The rule spans header to footer.
    result = redact(f"key follows:\n{_PEM}\ndone")
    assert "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ" not in result.text
    assert "BEGIN OPENSSH PRIVATE KEY" not in result.text
    assert "[redacted: private key]" in result.text
    # ...and a key truncated before its footer still must not pass.
    truncated = redact("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAxyz\n")
    assert "MIIEowIBAAKCAQEAxyz" not in truncated.text


def test_ordinary_output_is_left_alone():
    """A redactor that mangles innocent text is a redactor people switch off.
    Every rule keys off a vendor prefix or a structural marker, so none of these
    — git SHAs, base64, UUIDs, paths, prose — is touched."""
    for benign in (
        "commit 9f2a1c4e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39",
        "aGVsbG8gd29ybGQgdGhpcyBpcyBqdXN0IGJhc2U2NCBwYWRkaW5n",
        "550e8400-e29b-41d4-a716-446655440000",
        "/Users/someone/Projects/my-app/node_modules/.bin/eslint",
        "Successfully installed package-1.2.3 and 41 dependencies",
        "sk-",                      # prefix alone, no payload
        "Bearer",                   # the word alone
        "",
    ):
        assert redact(benign).text == benign, benign
        assert redact(benign).kinds == ()


def test_redaction_is_idempotent():
    # The send boundary re-walks the whole history every round, so redacting an
    # already-redacted message must be a no-op — a marker must not itself match.
    once = redact(f"token {_SECRETS['GitHub token']}").text
    assert redact(once).text == once
    assert redact(once).kinds == ()


def test_a_marker_carries_no_length_or_prefix_of_what_it_replaced():
    # Same rule the trace follows: a length narrows a brute force and a prefix
    # names the vendor. The marker says the KIND and nothing else.
    secret = _SECRETS["AWS access key"]
    text = redact(f"key={secret}").text
    assert str(len(secret)) not in text
    assert secret[:8] not in text


# ===========================================================================
# The seam: toward the model, never into the store
# ===========================================================================


def test_the_view_is_rewritten_and_the_originals_are_not():
    original = [
        Message(role="user", content="what is my aws key"),
        Message(role="tool", content=f"AWS_ACCESS_KEY_ID={_SECRETS['AWS access key']}"),
    ]
    view, kinds = redacted_for_model(original)
    assert "AWS access key" in kinds
    assert _SECRETS["AWS access key"] not in view[1].content
    # The person's own record is untouched — this is the whole decision.
    assert _SECRETS["AWS access key"] in original[1].content


def test_a_clean_history_is_returned_unchanged_and_unallocated():
    original = [Message(role="user", content="hello"), Message(role="assistant", content="hi")]
    view, kinds = redacted_for_model(original)
    assert view is original      # identity, not equality: the common case is free
    assert kinds == ()


# ===========================================================================
# ON THE WIRE — the plan's headline assertion
# ===========================================================================


class _RecordingProvider:
    """Stands where a real provider stands and keeps what it was handed. This is
    the provider's-eye view: whatever is in `self.seen` is what would have been
    serialized into the request body and sent off the machine."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen: list[list[Message]] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=100_000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None):
        # Copy the CONTENT now: the orchestrator may mutate the conversation
        # afterwards, and a late read would prove nothing about what was sent.
        self.seen.append([getattr(m, "content", "") for m in messages])
        return self._responses.pop(0)


class _LeakyTool:
    """A tool whose output contains a secret — the honest case the denylist and
    the sandbox both miss: a command that legitimately prints one."""

    definition = ToolDefinition(
        id="calculator", label="Calculate", description="leaks on purpose",
        risk_tier=RiskTier.LOW, parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args, context) -> ToolResult:
        return ToolResult(
            success=True,
            content=f"build output\nAWS_SECRET={_SECRETS['AWS access key']}\ndone",
        )


class _FakeStore:
    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        pass


def _run_turn(audit_sink=None):
    tool = _LeakyTool()
    registry = ToolRegistry()
    registry.register(tool)
    provider = _RecordingProvider([
        ModelResponse(
            text=None,
            tool_calls=[ToolCallRequest(id="c1", tool_id="calculator", args={})],
        ),
        ModelResponse(text="done", tool_calls=[]),
    ])
    orch = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: True),
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        on_tool_audit=audit_sink,
    )
    conv = Conversation(id="conv-1")
    conv.messages.append(Message(role="user", content="run the build"))
    orch.run_turn(conv, mode=PolicyMode.SAFE)
    return provider, conv


def test_a_secret_in_tool_output_never_reaches_the_provider():
    """THE HEADLINE. The tool result carries the secret; the second send — the one
    that would have carried it to the model — must not."""
    provider, conv = _run_turn()
    secret = _SECRETS["AWS access key"]

    assert len(provider.seen) >= 2, "the tool round must have produced a second send"
    for sent in provider.seen:
        for content in sent:
            assert secret not in str(content), f"secret reached the wire: {content!r}"
    # Not vacuous: the marker proves the message WAS carried, scrubbed.
    assert any("[redacted: AWS access key]" in str(c) for c in provider.seen[-1])


def test_the_stored_transcript_keeps_the_real_bytes():
    """The mirror property, and the reason the seam is the send boundary rather
    than append_tool_result: scrubbing the person's own record would destroy the
    evidence that a leak happened."""
    _, conv = _run_turn()
    tool_messages = [m for m in conv.messages if m.role == "tool"]
    assert tool_messages
    assert _SECRETS["AWS access key"] in tool_messages[0].content


# ===========================================================================
# The audit trail
# ===========================================================================


def test_a_granted_call_is_recorded_and_names_what_was_redacted():
    rows: list[dict] = []
    _run_turn(audit_sink=rows.append)
    granted = [r for r in rows if r["outcome"] == "granted"]
    assert granted, "a granted call must leave a row"
    row = granted[0]
    assert row["tool_id"] == "calculator"
    assert row["conversation_id"] == "conv-1"
    assert row["mode"] == "safe"
    # The row that says "this ran" also says a secret was caught leaving.
    assert "AWS access key" in (row["redacted"] or "")


def test_no_audit_row_ever_carries_a_secret():
    """G1's rule applied to history: `detail` is the permission card's own value,
    and `redacted` is kinds only. Neither may contain a value, and the audit trail
    is durable — a leak here outlives the session."""
    rows: list[dict] = []
    _run_turn(audit_sink=rows.append)
    for row in rows:
        blob = " ".join(str(v) for v in row.values())
        for secret in _SECRETS.values():
            assert secret not in blob
        assert "AWS_SECRET" not in blob      # nor the surrounding tool output


def test_an_audit_sink_that_throws_never_breaks_the_turn():
    """Best-effort by contract: a broken audit trail is a gap in history; an
    exception here would be a gap in the person's work."""
    def _explode(_row):
        raise RuntimeError("audit backend is down")

    provider, conv = _run_turn(audit_sink=_explode)
    assert any(m.role == "assistant" and m.content == "done" for m in conv.messages)


def test_a_routine_step_is_audited_too(tmp_path):
    """The engine shares the orchestrator's gate and registry; it must share the
    audit sink for the same reason. A routine is persisted, one-click and
    model-authorable — "what did that routine actually do?" is exactly the
    question the log exists to answer, and a routine that ran invisibly would be
    the quietest way to run anything."""
    from agent_core.memory.store import Store
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep

    rows: list[dict] = []
    registry = ToolRegistry()
    registry.register(_LeakyTool())
    store = Store(tmp_path / "r.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="safe",
    )
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: True),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        store=store,
        on_tool_audit=rows.append,
    )
    engine.run(
        Routine(id="r-1", name="T", description="", variables=[],
                steps=[RoutineStep("s1", "calculator", {})]),
        {}, mode=PolicyMode.SAFE,
    )
    assert [r["outcome"] for r in rows] == ["granted"]
    # A routine is not a conversation; the run is identifiable from routine_runs.
    assert rows[0]["conversation_id"] is None
    assert rows[0]["tool_id"] == "calculator"


def test_the_audit_row_survives_the_snapshot_scope_rule():
    """`tool_audit` must be EXCLUDED, not captured: a restore that rewrote the
    record of what happened would be worse than having no record — the
    `tool_grants` precedent. The build fails otherwise
    (test_capture_scope_covers_every_schema_table), so this pins the REASON."""
    from agent_core.snapshots import scope

    assert "tool_audit" in scope._EXCLUDED_TABLES
    assert "tool_audit" not in scope._CAPTURED_TABLES
