"""The Core -> Shell channel's failure edges: a wedged shell, and a shell that says no.

``IpcShellBridge`` is the Agent Core's only route to an OS effect (spec §1.3) and
the only route an API key travels (§5, G1). Its happy path has a round-trip test
(tests/test_ipc_server.py), but everything AROUND that path was unexercised: no
test had ever fed it an error frame, let a request time out, or driven
``get_provider_key``. Those are exactly the branches where a wrong edit is
silent — a timeout that returns ``{}`` looks like a successful delete, and an
error frame read as success looks like a key the shell never handed over.

So the properties pinned here are the ones a user would feel: Addison never
reports an OS effect it did not get confirmation for, it repeats the shell's own
words instead of inventing success, and a key fetch asks for the provider that
was named and hands back the key itself — nothing more, and nothing kept.

The shell double below is transport ONLY: every branch under test belongs to the
real bridge.
"""

from __future__ import annotations

import pytest

from agent_core.protocol import Method
from agent_core.shell_bridge import _EXEC_SLACK_MS, _KEYCHAIN_TIMEOUT, IpcShellBridge

# A stand-in secret. Long and distinctive so a "did this leak?" scan cannot pass
# by accident on a substring of something else.
_KEY = "sk-test-not-a-real-key-8f2c1a"


class _Shell:
    """The Rust shell on the other end of the pipe, reduced to a reply.

    It records the frame the bridge sent and hands the answer back through the
    bridge's own ``resolve_response`` — the same call the server's read loop
    makes — so nothing here re-implements the bridge's correlation or unwrapping.

    Answering inline is faithful rather than a shortcut: ``_call`` parks its
    waiter BEFORE it sends, so a reply that lands "early" is simply a reply that
    lands fast. ``silent=True`` is the wedged shell that never answers at all.
    """

    def __init__(self, result=None, error=None, *, silent: bool = False,
                 timeout: float = 5.0) -> None:
        self.result = result
        self.error = error
        self.silent = silent
        self.frames: list[dict] = []
        self.bridge = IpcShellBridge(timeout=timeout)
        self.bridge.bind_sender(self)

    def __call__(self, frame: dict) -> None:
        self.frames.append(frame)
        if not self.silent:
            self.bridge.resolve_response(frame["id"], self.result, self.error)

    @property
    def sent(self) -> dict:
        """The one frame the bridge sent — asserted to be exactly one."""
        assert len(self.frames) == 1, f"expected one outgoing frame, got {self.frames}"
        return self.frames[0]


def test_an_os_effect_that_never_came_back_is_never_reported_as_done():
    """A wedged shell must end the call in a plain refusal, not a quiet success.

    ``delete_file`` returns None on success, so a timeout that falls through
    returns None too — indistinguishable from "your file is gone". The user's
    file is still there; Addison must say so and ask them to try again.
    """
    shell = _Shell(silent=True, timeout=0.05)

    with pytest.raises(RuntimeError) as raised:
        shell.bridge.delete_file("/Users/mira/Documents/notes.txt")

    assert str(raised.value) == "Addison couldn't finish that just now. Please try again."
    assert shell.sent["method"] == Method.SHELL_DELETE_FILE


def test_a_reply_after_the_timeout_finds_no_call_left_to_answer():
    """The abandoned request is dropped, so a late shell reply resolves nothing.

    Two reasons this matters: a request that is never cleared out leaks for the
    life of the process, and — worse — a reply arriving after the user was told
    the action failed must not quietly complete it later.
    """
    shell = _Shell(silent=True, timeout=0.05)

    with pytest.raises(RuntimeError):
        shell.bridge.read_clipboard()

    # resolve_response reports whether the id matched something still waiting.
    assert shell.bridge.resolve_response(shell.sent["id"], {"text": "late"}, None) is False


def test_a_shell_refusal_is_repeated_in_the_shells_own_words_not_read_as_success():
    """An error frame must raise, carrying the message the shell actually sent.

    ``get_device_key`` hands its result dict straight back, so an error read as
    success returns ``{}`` — the caller sees an identity with no device id rather
    than the reason it failed, and the reason is the only thing that helps here.
    """
    shell = _Shell(error={"code": -32001, "message": "Keychain access was denied."})

    with pytest.raises(RuntimeError) as raised:
        shell.bridge.get_device_key()

    assert str(raised.value) == "Keychain access was denied."


def test_get_provider_key_asks_for_the_provider_the_caller_named():
    """The provider id is the whole request: it picks WHICH key comes back.

    Drop it and the shell answers for the default (Anthropic), so an OpenAI turn
    authenticates with the wrong provider's key — a confusing failure at best,
    and one provider's secret sent to another's endpoint at worst.
    """
    shell = _Shell(result={"key": _KEY})

    shell.bridge.get_provider_key("openai")

    assert shell.sent["method"] == Method.KEYCHAIN_GET_PROVIDER_KEY
    assert shell.sent["params"] == {"provider": "openai"}


def test_get_provider_key_returns_the_key_itself_never_the_frame_around_it():
    """Callers hand the return value straight to a provider as the credential.

    Anything other than the key string — the result dict, the whole frame — is
    not a credential, and the frame in particular is a structure whose other
    fields have no business travelling to a provider endpoint.
    """
    shell = _Shell(result={"key": _KEY})

    assert shell.bridge.get_provider_key("openai") == _KEY


def test_nothing_saved_and_could_not_read_are_two_different_answers():
    """The empty key is a normal result; a failed read is an error, and must raise.

    Everything downstream turns on this one distinction. ``""`` means the person has
    not added a key yet, which is onboarding. A read that FAILED — a dismissed OS
    password dialog, a locked keychain — means a key may well be sitting there
    unread, and the caller that cannot tell the two apart routes a turn to the Setup
    Assistant relay on the strength of an unanswered dialog.
    """
    saved_nothing = _Shell(result={"key": ""})
    assert saved_nothing.bridge.get_provider_key("anthropic") == ""

    denied = _Shell(
        error={"code": -32001, "message": "Couldn't read your saved key from the keychain."}
    )
    with pytest.raises(RuntimeError) as raised:
        denied.bridge.get_provider_key("anthropic")
    assert str(raised.value) == "Couldn't read your saved key from the keychain."


def test_a_keychain_call_waits_at_a_persons_pace_not_the_shells(monkeypatch):
    """Keychain calls get their own, much longer budget than every other request.

    A ``shell.*`` call is the shell answering from its own process, so sixty seconds
    means it is wedged. A ``keychain.*`` call may be sitting behind an OS password
    dialog, and the person on the other side of it may be away from the desk.
    Abandoning the request does not dismiss the dialog: it only guarantees that the
    password they eventually type answers a request nobody is waiting on any more.
    """
    import time

    import agent_core.shell_bridge as bridge_module

    monkeypatch.setattr(bridge_module, "_KEYCHAIN_TIMEOUT", 0.30)
    # An instance timeout far below the keychain budget: a keychain call that used
    # it would come back almost at once, which is exactly the bug.
    shell = _Shell(silent=True, timeout=0.02)

    started = time.monotonic()
    with pytest.raises(RuntimeError):
        shell.bridge.get_provider_key("anthropic")
    assert time.monotonic() - started >= 0.2

    # ...and the ordinary calls are untouched: still the instance/default budget.
    other = _Shell(silent=True, timeout=0.02)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        other.bridge.read_clipboard()
    assert time.monotonic() - started < 0.2


# Every bridge method that makes a Core -> Shell request, with arguments that get
# it as far as ``_call``. Asserted EXHAUSTIVE below: a new method must be added
# here and therefore classified, instead of silently inheriting whichever budget
# its author happened to pass.
_BRIDGE_CALLS = (
    ("save_new_file", ("notes.txt", "body")),
    ("delete_file", ("/tmp/notes.txt",)),
    ("restore_file", ("/tmp/notes.txt", "body")),
    ("open_draft", ("mira@example.com", "Subject", "Body")),
    ("discard_draft", ("draft-1",)),
    ("read_clipboard", ()),
    ("open_external", ("https://example.com",)),
    ("read_scoped_file", ("handle-1",)),
    ("write_workspace_file", ("/tmp/project/a.py", "print()")),
    ("read_workspace_file", ("/tmp/project/a.py",)),
    ("restore_workspace_file", ("/tmp/project/a.py", "print()")),
    ("pick_directory", ()),
    ("get_app_build_ref", ()),
    ("get_provider_key", ("anthropic",)),
    ("get_device_key", ()),
    ("sign_relay_request", ({"messages": []},)),
    ("run_command", ("ls", 30_000, [])),
    # Arming (step 8 phase 3). All three sit on the DEFAULT budget deliberately:
    # each is the shell answering out of its own process — write a file, remove a
    # file, ask launchd what it holds — with no person and no child process in the
    # way, so a minute of silence means the shell is wedged and the turn should say
    # so rather than hang.
    (
        "arm_automation",
        ("com.addison.auto.tidy", "echo hi", "interval", {"minutes": 30}),
    ),
    ("disarm_automation", ("com.addison.auto.tidy",)),
    ("list_armed", ()),
    # The review surface's read paths (Phase-3 plan Build §1). DEFAULT budget, and
    # the classification is not a shrug: both are the shell answering out of its own
    # process, with no person and no child in the way, and both are BOUNDED by
    # construction — a listing is capped at 500 entries and a view read stops at
    # 256 KiB. A minute of silence from either means the shell is wedged, which is
    # exactly what the default budget is for.
    ("list_workspace_directory", ("/tmp/project",)),
    ("read_workspace_file_for_view", ("/tmp/project/a.py",)),
    # The review surface's revert half (Build §2/§3). DEFAULT budget for the same
    # reason, and these two are bounded harder than anything above: the first reads
    # NOTHING (a set lookup per path) and the second reads only as far as a 256 KiB
    # ceiling per file, for a list the core itself caps at 200. A minute of silence
    # from either is a wedged shell, not a slow answer.
    ("can_restore_workspace_files", (["/tmp/project/a.py"],)),
    ("digest_workspace_files", (["/tmp/project/a.py"],)),
    ("adopt_workspace_path", ("/tmp/project/a.py", "a" * 64)),
)

# The one call whose budget is neither the default nor the person-paced one: a
# command waits on the COMMAND's own deadline (step 5.5, item 1). The shell kills
# the child at ``timeoutMs`` and answers, so this waiter only needs that plus
# enough slack to spawn sandbox-exec and drain the pipes. Left on the default
# budget, a legal 45-second build would be reported as a wedged shell while it
# kept running with nobody to receive its output.
_COMMAND_BUDGET_SECONDS = (30_000 + _EXEC_SLACK_MS) / 1000.0

# Not requests: one binds the sender, the other is the read loop handing an answer
# back. Neither has a timeout to choose.
_NOT_REQUESTS = {"bind_sender", "resolve_response"}


class _RecordingBridge(IpcShellBridge):
    """Records what each method asks ``_call`` for, without any transport at all.

    The timeout is chosen at the CALL SITE (``get_provider_key`` and friends pass
    ``_KEYCHAIN_TIMEOUT``), so recording the argument is a direct reading of the
    rule — no clock, no sleeping, nothing that can flake on a loaded machine.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, float | None]] = []

    def _call(self, method: str, params: dict, timeout: float | None = None) -> dict:
        self.calls.append((method, timeout))
        # One dict that satisfies every caller's unwrapping.
        return {"path": "/tmp/x", "draftRef": "d", "text": "t", "content": "c", "key": "",
                "existed": False, "prior": None}


def test_only_the_keychain_calls_wait_at_a_persons_pace():
    """The long budget belongs to the calls a PERSON answers, and to no others.

    A ``shell.*`` request is the shell answering out of its own process, so a
    minute of silence means it is wedged and the turn should say so. A
    ``keychain.*`` request may be sitting behind an OS password dialog, where
    giving up achieves nothing: the dialog stays on screen, and the password
    eventually typed lands on a request nobody is waiting on any more.

    Both directions are pinned because both are failures. A keychain call left on
    the short budget is the bug this fixes; a file or clipboard call quietly given
    the long one turns a wedged shell into a ten-minute hang with no explanation.

    (The folder and file pickers also wait on a person. They were deliberately
    left on the default budget by this change — a separate call to make, and the
    table makes it visible rather than implied.)
    """
    named = {name for name, _ in _BRIDGE_CALLS}
    public = {
        name for name in dir(IpcShellBridge)
        if not name.startswith("_") and callable(getattr(IpcShellBridge, name))
    } - _NOT_REQUESTS
    assert named == public, "a bridge method is missing from the timeout table"

    bridge = _RecordingBridge()
    for name, args in _BRIDGE_CALLS:
        getattr(bridge, name)(*args)

    for method, timeout in bridge.calls:
        if method.startswith("keychain."):
            assert timeout == _KEYCHAIN_TIMEOUT, method
        elif method == Method.SHELL_RUN_COMMAND:
            assert timeout == _COMMAND_BUDGET_SECONDS, method
        else:
            assert timeout is None, method   # None = the instance/default budget


def test_a_fetched_key_is_not_retained_anywhere_on_the_bridge():
    """G1: the key is read at the moment of use and never kept (§8.3).

    The bridge is a long-lived object; a key cached on it would outlive the one
    request it was fetched for, widening where keys live beyond the keychain.

    Three places, not one. Checking only the INSTANCE would miss the mistake most
    likely to be made here: CLAUDE.md sanctions a session-lifetime key cache in the
    RUST SHELL (one keychain prompt per provider per launch), so the tempting error
    is to port that idea into the core — and a `type(self)._cache = …` or a
    module-level dict is exactly how someone would write it. Both are still Agent
    Core memory persisting beyond one request, which G1 forbids.
    """
    import agent_core.shell_bridge as bridge_module

    shell = _Shell(result={"key": _KEY})

    assert shell.bridge.get_provider_key("anthropic") == _KEY
    assert _KEY not in repr(vars(shell.bridge)), "key retained on the instance"
    assert _KEY not in repr(vars(type(shell.bridge))), "key retained on the class"
    assert _KEY not in repr(vars(bridge_module)), "key retained in module state"


# --- keychain trace (diagnostic, opt-in) -----------------------------------


def _drive_a_key_fetch(monkeypatch, enabled: bool, capsys):
    monkeypatch.setenv("ADDISON_KEYCHAIN_TRACE", "1" if enabled else "0")
    bridge = _RecordingBridge()
    bridge.get_provider_key("anthropic")
    return capsys.readouterr().err


def test_the_keychain_trace_names_the_caller(monkeypatch, capsys):
    """The whole point of the core-side half: the shell can say WHAT the OS was
    touched for, but only this side can say WHO asked. Two password dialogs for one
    keychain item means two callers, and at the wire they are indistinguishable."""
    err = _drive_a_key_fetch(monkeypatch, True, capsys)
    assert "want anthropic" in err
    # The CALLER, not the plumbing: a trace naming get_provider_key answers the
    # question with the question.
    assert "get_provider_key()" not in err
    assert "_drive_a_key_fetch()" in err
    # A CHAIN, not one frame. The first live trace reported `main.py:1817 getter()`
    # for every caller — the key-fetch closure is the nearest non-plumbing frame for
    # all of them, so a single frame answered "something asked" and named nobody.
    assert " <- " in err
    # The NEAREST frame is the caller — asserted on position, not on absence.
    # Outer frames in the chain are legitimately pytest's here; what must never
    # happen is the chain STARTING there, which is what the old skip-list bug did
    # ("test_shell_bridge.py".endswith("shell_bridge.py") is True, so it walked
    # straight past the real caller). A test written the same way agreed with it.
    chain = err.split("want anthropic")[1].strip()
    assert chain.split(" <- ")[0].endswith("_drive_a_key_fetch()")


def test_the_keychain_trace_is_silent_unless_asked_for(monkeypatch, capsys):
    # A keychain trace on by default is a keychain trace in someone's support-log
    # paste. Only an explicit "1" turns it on (the shell side agrees).
    assert _drive_a_key_fetch(monkeypatch, False, capsys) == ""
    monkeypatch.delenv("ADDISON_KEYCHAIN_TRACE", raising=False)
    bridge = _RecordingBridge()
    bridge.get_provider_key("anthropic")
    assert capsys.readouterr().err == ""


def test_the_core_side_trace_never_reports_the_key(monkeypatch, capsys):
    """G1. This side has the value in scope, so it prints the REQUEST and stops —
    there is deliberately no outcome line here. The shell reports the outcome from
    the one place the variant can be named without the value."""
    monkeypatch.setenv("ADDISON_KEYCHAIN_TRACE", "1")

    class _KeyBridge(_RecordingBridge):
        def _call(self, method, params, timeout=None):
            super()._call(method, params, timeout)
            return {"key": _KEY}

    assert _KeyBridge().get_provider_key("anthropic") == _KEY
    err = capsys.readouterr().err
    assert _KEY not in err
    assert "found" not in err   # no outcome word on this side at all
