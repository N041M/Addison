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

import pathlib

from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
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
        "Stripe key": ("sk_live_", "0123456789abcdefghijklmn"),
        "GitLab token": ("glpat-", "0123456789abcdefghij"),
        "JSON web token": (
            "eyJ",
            "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz012345",
        ),
    }.items()
}

# The 40-character half that actually signs an AWS request. It is NOT in the dict
# above because it cannot stand alone: with no vendor prefix it is
# indistinguishable from a git SHA, so the rule anchors on the assignment and the
# fixture has to carry one too. AWS's own published example value.
_AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY"

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


def test_the_aws_secret_half_is_removed_and_its_variable_name_survives():
    """The rule that was missing for weeks while its neighbour matched `AKIA…`.

    An access key ID is an identifier; THIS is the credential. Both spellings the
    real world uses are covered, and the variable name is deliberately kept — a
    redactor that deletes `AWS_SECRET_ACCESS_KEY=` along with the value produces
    output that reads like corruption, and a model looking at corruption re-runs
    the command."""
    for line in (
        f"AWS_SECRET_ACCESS_KEY={_AWS_SECRET}",
        f"aws_secret_access_key: {_AWS_SECRET}",
        f'aws_secret_access_key = "{_AWS_SECRET}"',
    ):
        result = redact(f"env dump\n{line}\ndone")
        assert _AWS_SECRET not in result.text, line
        assert "[redacted: AWS secret key]" in result.text, line
        assert "AWS secret key" in result.kinds
        # The name survives; only the value goes.
        assert "secret_access_key" in result.text.lower(), line
        # ...and the surviving name must not re-trigger the rule on the next pass.
        # The send boundary re-walks the whole history every round, so a `keep`
        # group is the one construct here that could plausibly build a string
        # matching its own pattern. It does not — pinned rather than assumed.
        assert redact(result.text).text == result.text, line
        assert redact(result.text).kinds == (), line


def test_a_bare_forty_character_secret_is_NOT_redacted_and_that_is_the_trade():
    """Stated as a test so it is a decision rather than a surprise.

    With no `aws_secret_access_key=` in front of it, the value is 40 characters of
    base64 alphabet — the same shape as a git SHA. Matching it unanchored would
    shred ordinary `git log` output, so the rule stays anchored and this case
    passes. That is the backstop-not-a-boundary rule being honest, and anyone
    tempted to "fix" it should read the git-SHA assertion in
    `test_ordinary_output_is_left_alone` first."""
    assert redact(f"loose value {_AWS_SECRET} here").kinds == ()


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
        self.seen: list[list[str]] = []

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


class _CommandShapedTool:
    """``run_command``'s SHAPE, minus the shell: a tool whose ``permission_detail``
    is the argument text itself.

    This is the tool the audit trail actually has to survive. Every other tool's
    detail is narrow by construction (``read_web_page`` gives a host), so a test
    written against one of those asserts nothing about `detail` — the field is
    None and the assertion passes over an empty string. A command's detail IS its
    arguments, and arguments are where a secret enters."""

    definition = ToolDefinition(
        id="run_command_shape", label="Run a command", description="carries its args",
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )

    def permission_detail(self, args) -> str | None:
        # Byte-for-byte what RunCommandTool.permission_detail does, cap included.
        command = str(args.get("command", "")).strip()
        return command or None

    def execute(self, args, context) -> ToolResult:
        return ToolResult(success=True, content="deployed")


class _FakeStore:
    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        pass


def _run_turn(audit_sink=None, tool=None, args=None):
    tool = tool if tool is not None else _LeakyTool()
    registry = ToolRegistry()
    registry.register(tool)
    provider = _RecordingProvider([
        ModelResponse(
            text=None,
            tool_calls=[
                ToolCallRequest(id="c1", tool_id=tool.definition.id, args=args or {})
            ],
        ),
        ModelResponse(text="done", tool_calls=[]),
    ])
    orch = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED),
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


def test_no_audit_row_ever_carries_a_secret(tmp_path):
    """G1's rule applied to history, and it is asserted WHERE THE ROW LANDS.

    Two things this test got wrong until 2026-08-01, both of which made it pass
    while the leak was live:

      * it ran a tool with no ``permission_detail`` at all, so the `detail` field
        under test was always None. Nothing it asserted could ever have failed.
        It now runs a run_command-shaped call whose detail IS the command text —
        `export TOKEN=… && ./deploy.sh`, the exact shape the harness produces.
      * it asserted on the dict handed to the sink. The redaction that makes this
        true happens inside ``Store.insert_tool_audit`` (one site, so a fourth
        dispatch site cannot forget), and durability is the whole reason the field
        matters: `tool_audit` is excluded from snapshots and never pruned. So the
        assertion is on rows READ BACK OUT OF SQLITE.
    """
    from agent_core.memory.store import Store

    store = Store(tmp_path / "audit.sqlite3")
    secret = _SECRETS["GitHub token"]
    _run_turn(
        audit_sink=lambda row: store.insert_tool_audit(**row),
        tool=_CommandShapedTool(),
        args={"command": f"export TOKEN={secret} && ./deploy.sh"},
    )

    rows = store.list_tool_audit()
    assert rows, "the call must have left a row — otherwise this proves nothing"
    # Not vacuous in the other direction either: the row describes THIS call.
    assert any("./deploy.sh" in (row["detail"] or "") for row in rows)
    for row in rows:
        blob = " ".join(str(v) for v in row.values())
        for known in _SECRETS.values():
            assert known not in blob
        assert "AWS_SECRET" not in blob      # nor the surrounding tool output
        # A partial key is still a key: assert no prefix of it survived either.
        assert secret[:20] not in blob


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
        permission_gate=PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED),
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


def test_a_throwing_sink_never_breaks_a_ROUTINE_either(tmp_path):
    """The same best-effort contract, proven at the second of the three sites.

    `main._record_tool_audit` calls `insert_tool_audit(**row)` bare — nothing in
    it is defensive. What makes a broken audit store survivable is entirely the
    CALLER's blanket `except`, and until now only the live loop had a test saying
    so. A routine that died because its history could not be written would be the
    audit trail costing the person the work it exists to describe."""
    from agent_core.memory.store import Store
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep

    def _explode(_row):
        raise RuntimeError("audit backend is down")

    registry = ToolRegistry()
    registry.register(_LeakyTool())
    store = Store(tmp_path / "r.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="safe",
    )
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        store=store,
        on_tool_audit=_explode,
    )
    result = engine.run(
        Routine(id="r-1", name="T", description="", variables=[],
                steps=[RoutineStep("s1", "calculator", {})]),
        {}, mode=PolicyMode.SAFE,
    )
    # The step ran and the run completed — the sink's failure stayed local.
    assert result is not None


class _VeryLeakyTool:
    """Output carrying the SAME secret kind many times over — a build script that
    prints a key table, or a `env`-shaped dump. This is what turned the `redacted`
    column from cosmetic into a size problem: the redactor reports one entry per
    MATCH, and the row is durable, excluded from snapshots and never pruned."""

    definition = ToolDefinition(
        id="dump_env", label="Dump", description="prints many keys",
        risk_tier=RiskTier.LOW, parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args, context) -> ToolResult:
        lines = [f"KEY_{n}={_SECRETS['AWS access key']}" for n in range(200)]
        lines.append(f"GH_TOKEN={_SECRETS['GitHub token']}")
        return ToolResult(success=True, content="\n".join(lines))


def test_the_redacted_column_names_each_kind_once(tmp_path):
    """One entry per KIND, not per MATCH, and in a stable order.

    201 hits of two kinds used to write "AWS access key, AWS access key, …" —
    roughly 3KB here and ~40KB for a command that prints a real key table — into a
    column whose entire content is two names. Sorted as well as deduplicated, so
    the same set of kinds is always the same row and two runs are comparable."""
    from agent_core.memory.store import Store

    store = Store(tmp_path / "audit.sqlite3")
    _run_turn(audit_sink=lambda row: store.insert_tool_audit(**row), tool=_VeryLeakyTool())

    granted = [r for r in store.list_tool_audit() if r["outcome"] == "granted"]
    assert granted, "the call must have left a row — otherwise this proves nothing"
    # Both kinds are named, each exactly once, alphabetically.
    assert granted[0]["redacted"] == "AWS access key, GitHub token"


def test_nothing_is_scanned_for_secrets_when_no_audit_sink_is_wired(monkeypatch):
    """The classification exists only to fill the ``redacted`` column, so with no
    sink it is pure cost: a 9-regex pass over up to _MAX_OUTPUT_CHARS of output, on
    every CLI turn and every test turn, thrown away. It was a plain ARGUMENT, and
    an argument is evaluated before the callee's early-out can discard it."""
    import agent_core.orchestrator as orchestrator_module

    scanned: list[str] = []
    real = orchestrator_module.redact

    def _counting(text):
        scanned.append(text)
        return real(text)

    monkeypatch.setattr(orchestrator_module, "redact", _counting)

    _run_turn()                                   # no sink
    assert scanned == [], "tool output was scanned with nothing to record it in"

    # Not vacuous: with a sink wired the very same turn DOES scan. (This is the
    # orchestrator's classification pass only — the scrubbing that protects the
    # wire is `redacted_for_model`, a different function, and it always runs.)
    _run_turn(audit_sink=[].append)
    assert scanned, "a wired sink must still get its classification"


# ===========================================================================
# The refusal branches — the rows the log actually exists for
# ===========================================================================


class _ExplodingGate(PermissionGate):
    """A gate that must never be consulted. Every refusal below sits ABOVE the
    gate by design, so reaching it is itself the failure.

    Subclasses the real gate rather than duck-typing it: the callers annotate a
    `PermissionGate`, and a stand-in that is not one only type-checks by accident.
    """

    def __init__(self) -> None:
        super().__init__()

    def authorize(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("a refusal must never reach the permission gate")


def test_a_routine_step_refused_as_dev_only_still_leaves_a_row(tmp_path):
    """A routine step naming a dev tool outside OPEN was the one refusal in the
    tree that recorded nothing at all — and a routine is the path that runs
    one-click, model-authored, with nobody watching. ``detail`` is None to match
    the live loop's dev_only row: nothing about the call was examined."""
    from agent_core.memory.store import Store
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep
    from agent_core.tools.run_command import RunCommandTool

    rows: list[dict] = []
    registry = ToolRegistry()
    registry.register(RunCommandTool(), dev_only=True)
    store = Store(tmp_path / "r.sqlite3")
    store.insert_routine(
        id="r-dev", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=_ExplodingGate(),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        store=store,
        on_tool_audit=rows.append,
    )
    run = engine.run(
        Routine(id="r-dev", name="T", description="", variables=[],
                steps=[RoutineStep("s1", "run_command", {}, command="echo hi")]),
        {}, mode=PolicyMode.SAFE,
    )

    assert run.status == "failed"
    assert [r["outcome"] for r in rows] == ["dev_only"]
    row = rows[0]
    assert row["tool_id"] == "run_command"
    assert row["conversation_id"] is None        # a routine is not a conversation
    assert row["detail"] is None
    assert row["mode"] == "safe"
    # Computed from the call, not from the branch: run_command always cards.
    assert row["destructive"] is True


def test_the_rails_safe_refusal_leaves_a_row(tmp_path):
    """The Run pill is the third site a command can start, and the only one a
    CLICK starts rather than a model. Driven through the real JSON-RPC server so
    the row is the one the live server writes, read back out of its own store."""
    from agent_core.memory.store import Store
    from agent_core.protocol import Method
    from tests.test_policy_modes import _artifact_server, _rpc, _shutdown

    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "simple")
    try:
        result = _rpc(reader, writer, 1, Method.WIDGET_RUN, {"id": "dev-w"})["result"]
        assert result["ok"] is False
        assert "waiting in Developer profile" in result["error"]
    finally:
        _shutdown(reader, thread)

    store = Store(db_path)
    rows = store.list_tool_audit()
    store.close()
    assert [r["outcome"] for r in rows] == ["dev_only"]
    assert rows[0]["tool_id"] == "run_command"
    assert rows[0]["mode"] == "safe"
    assert rows[0]["detail"] == "ls"           # the widget's own command
    assert rows[0]["destructive"] == 1


def test_the_rails_refusal_records_even_when_run_command_is_not_registered(tmp_path):
    """THE LESSON FROM THE FIRST ATTEMPT AT THE ROW ABOVE, kept as a test.

    Resolving the tool in the handler so the refusal could be audited turned a
    clean SAFE refusal into a ``-32000`` error frame on every server whose registry
    has no ``run_command`` — which is every server built by ``conftest.build_server``
    and could be a real one mid-reconfiguration. Two properties, and the first
    matters more: **the refusal does not depend on registry contents**, and the
    audit still writes a row (id only, empty detail) rather than breaking the thing
    it observes."""
    import json as _json

    from agent_core.memory.store import Store
    from agent_core.protocol import Method
    from tests.conftest import IPC_DB_NAME, _shutdown, build_server

    db_path = tmp_path / IPC_DB_NAME
    seed = Store(db_path)
    seed.set_setting("widgets_seeded", "1")
    seed.insert_widget(
        id="cmd-w",
        spec_json=_json.dumps({"kind": "command", "command": "ls", "title": "List"}),
        pinned=True, position=0, created_at=1, created_in_mode="open",
    )
    seed.close()

    harness = build_server(tmp_path)          # registry carries the spy tool ONLY
    try:
        registry = harness.server.tool_registry
        assert "run_command" not in {d.id for d in registry.list_for_model()}
        harness.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_RUN,
             "params": {"id": "cmd-w"}}
        )
        frame = harness.writer.wait_for(lambda f: f.get("id") == 1)
        assert "error" not in frame, f"a refusal became an error frame: {frame}"
        assert frame["result"]["ok"] is False
        assert "waiting in Developer profile" in frame["result"]["error"]
    finally:
        _shutdown(harness.reader, harness.thread)

    store = Store(db_path)
    rows = store.list_tool_audit()
    store.close()
    assert [r["outcome"] for r in rows] == ["dev_only"]
    assert rows[0]["tool_id"] == "run_command"   # the id IS the record
    assert rows[0]["detail"] is None


# ===========================================================================
# THE ROUND TRIP — every outcome, through a real SQLite store
# ===========================================================================


class _DestructivePathTool:
    """A write-shaped, path-bounded tool. Confinement refuses it before the gate,
    which is the branch whose ``destructive`` column used to be a hard-coded
    False regardless of what the call would have done."""

    definition = ToolDefinition(
        id="write_shaped", label="Save a file", description="path-bounded write",
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    def affected_path(self, args) -> str:
        return str(pathlib.Path(str(args.get("path", ""))).resolve())

    def is_destructive(self, args) -> bool:
        return True                              # an overwrite is data loss

    def permission_detail(self, args) -> str | None:
        # The file's name, not the whole path: `call_permission_detail` caps what a
        # detail may be, and a tmp_path is long enough to hit the cap.
        return pathlib.Path(str(args.get("path", ""))).name or None

    def execute(self, args, context) -> ToolResult:  # pragma: no cover - must not run
        raise AssertionError("a confined-out call must never execute")


def _audited_turn(store, *, tool, args, mode, allow=True, dev_only=False):
    """One turn whose audit sink is a REAL store — no list, no fake."""
    registry = ToolRegistry()
    registry.register(tool, dev_only=dev_only)
    provider = _RecordingProvider([
        ModelResponse(
            text=None,
            tool_calls=[ToolCallRequest(id="c1", tool_id=tool.definition.id, args=args)],
        ),
        ModelResponse(text="done", tool_calls=[]),
    ])
    answer = PermissionStatus.GRANTED if allow else PermissionStatus.DENIED
    Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: answer),
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        # The default refuses every path, so a path-bounded tool is confined to
        # nothing — which is what makes the confined_out branch reachable here.
        on_tool_audit=lambda row: store.insert_tool_audit(**row),
    ).run_turn(_conversation(), mode=mode)


def _conversation():
    conv = Conversation(id="conv-1")
    conv.messages.append(Message(role="user", content="go"))
    return conv


def test_every_outcome_survives_the_round_trip_into_sqlite(tmp_path):
    """THE TEST THE AUDIT TRAIL DID NOT HAVE.

    Every other test here hands ``_audit`` a ``list.append`` sink, and BOTH
    ``_audit`` implementations swallow every exception by contract. So a renamed
    column, a widened outcome, a violated CHECK — anything that makes the INSERT
    fail — would drop 100% of rows in the running app while this file stayed
    entirely green. That is the repo's own "passes when the mechanism never ran"
    trap, in the subsystem whose whole purpose is durability.

    So: a real ``Store``, all five outcomes, read back out with
    ``list_tool_audit``. It also pins ``destructive`` as a property of the CALL —
    the refusal branches each used to hard-code a literal, so the column described
    which branch was taken, in exactly the rows the log exists for."""
    from agent_core.memory.store import Store
    from agent_core.tools.run_command import RunCommandTool

    store = Store(tmp_path / "audit.sqlite3")

    # granted / denied — an ordinary LOW tool, gate says yes then no.
    _audited_turn(store, tool=_LeakyTool(), args={}, mode=PolicyMode.SAFE, allow=True)
    _audited_turn(store, tool=_LeakyTool(), args={}, mode=PolicyMode.SAFE, allow=False)
    # forbidden — the hardline denylist, above the gate (step 5.5 item 3).
    _audited_turn(
        store, tool=RunCommandTool(), args={"command": "rm -rf ~/.addison"},
        mode=PolicyMode.OPEN, dev_only=True,
    )
    # confined_out — a path-bounded write with no trusted root (step 5, D3).
    _audited_turn(
        store, tool=_DestructivePathTool(), args={"path": str(tmp_path / "x.txt")},
        mode=PolicyMode.SAFE,
    )
    # dev_only — a dev tool named outside OPEN.
    _audited_turn(
        store, tool=RunCommandTool(), args={"command": "ls"},
        mode=PolicyMode.SAFE, dev_only=True,
    )

    rows = store.list_tool_audit()
    store.close()
    by_outcome = {row["outcome"]: row for row in rows}
    assert set(by_outcome) == {
        "granted", "denied", "forbidden", "confined_out", "dev_only"
    }, f"rows that never reached SQLite: {sorted(rows, key=str)}"

    # DESTRUCTIVE IS THE CALL'S ANSWER, NOT THE BRANCH'S.
    assert by_outcome["forbidden"]["destructive"] == 1      # every command cards
    assert by_outcome["dev_only"]["destructive"] == 1       # ...even a refused one
    assert by_outcome["confined_out"]["destructive"] == 1   # a write, refused early
    # ...and it is not simply always 1: a LOW read-only tool is not destructive.
    assert by_outcome["granted"]["destructive"] == 0
    assert by_outcome["denied"]["destructive"] == 0

    # The rows describe THESE calls, so none of the above passed over empty fields.
    assert by_outcome["forbidden"]["detail"] == "rm -rf ~/.addison"
    assert by_outcome["confined_out"]["detail"] == "x.txt"
    assert by_outcome["dev_only"]["detail"] is None
    for row in rows:
        assert row["conversation_id"] == "conv-1"
        assert row["mode"] in ("safe", "open")


class _NonDestructiveCommandTool:
    """Declares command text — the denylist's one input — but classifies itself
    non-destructive.

    ``run_command`` is the only tool that declares ``command_text`` today and it
    cards unconditionally, so a forbidden row for it is True either way. This
    shape is what tells a COMPUTED column apart from the ``True`` the forbidden
    branch used to hard-code, and ``command_text`` is an open protocol any later
    tool may implement."""

    definition = ToolDefinition(
        id="peek", label="Peek at a file", description="reads, never writes",
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )

    def command_text(self, args) -> str:
        return str(args.get("command", ""))

    def permission_detail(self, args) -> str | None:
        return str(args.get("command", "")) or None

    def is_destructive(self, args) -> bool:
        return False

    def execute(self, args, context) -> ToolResult:  # pragma: no cover - must not run
        raise AssertionError("a forbidden call must never execute")


def test_a_forbidden_row_describes_the_call_not_the_branch(tmp_path):
    """The denylist refusal is the outcome with NO card behind it, so its row is
    the only place the attempt is ever recorded. A hard-coded ``destructive=True``
    there is a claim about the branch dressed as a fact about the call."""
    from agent_core.memory.store import Store

    store = Store(tmp_path / "audit.sqlite3")
    _audited_turn(
        store, tool=_NonDestructiveCommandTool(),
        args={"command": "cat ~/.ssh/id_rsa"}, mode=PolicyMode.SAFE,
    )
    rows = store.list_tool_audit()
    store.close()

    assert [row["outcome"] for row in rows] == ["forbidden"]
    assert rows[0]["detail"] == "cat ~/.ssh/id_rsa"
    assert rows[0]["destructive"] == 0


def test_a_routines_refusal_rows_describe_the_call_not_the_branch(tmp_path):
    """The same defect lived twice — the engine's refusal branches carried their
    own copies of the literal. The engine is the path that runs one-click and
    unattended, so its rows are the ones nobody is watching being written."""
    from agent_core.memory.store import Store
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep

    def _run(tool, args, rows):
        registry = ToolRegistry()
        registry.register(tool)
        store = Store(tmp_path / f"{tool.definition.id}.sqlite3")
        store.insert_routine(
            id="r", name="T", description="", plan_json={},
            created_from_conversation_id=None, created_at=1, created_in_mode="safe",
        )
        RoutineEngine(
            tool_registry=registry,
            permission_gate=_ExplodingGate(),
            undo_manager=UndoManager(store=store, tool_registry=registry),
            store=store,
            on_tool_audit=rows.append,
        ).run(
            Routine(id="r", name="T", description="", variables=[],
                    steps=[RoutineStep("s1", tool.definition.id, args)]),
            {}, mode=PolicyMode.SAFE,
        )
        store.close()

    # forbidden: a non-destructive read the denylist refuses anyway.
    forbidden: list[dict] = []
    _run(_NonDestructiveCommandTool(), {"command": "cat ~/.ssh/id_rsa"}, forbidden)
    assert [r["outcome"] for r in forbidden] == ["forbidden"]
    assert forbidden[0]["destructive"] is False

    # confined_out: a destructive write with no trusted root (the engine's default
    # trust check refuses every path).
    confined: list[dict] = []
    _run(_DestructivePathTool(), {"path": str(tmp_path / "x.txt")}, confined)
    assert [r["outcome"] for r in confined] == ["confined_out"]
    assert confined[0]["destructive"] is True


def test_the_audit_row_survives_the_snapshot_scope_rule():
    """`tool_audit` must be EXCLUDED, not captured: a restore that rewrote the
    record of what happened would be worse than having no record — the
    `tool_grants` precedent. The build fails otherwise
    (test_capture_scope_covers_every_schema_table), so this pins the REASON."""
    from agent_core.snapshots import scope

    assert "tool_audit" in scope._EXCLUDED_TABLES
    assert "tool_audit" not in scope._CAPTURED_TABLES
