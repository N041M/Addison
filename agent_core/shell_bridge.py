"""IPC-backed ShellBridge — the Agent Core's only route to OS-level effects.

The Agent Core has no OS permissions of its own (engineering-spec §1.3): every
filesystem/clipboard/external-app/draft effect crosses back to the Rust shell as
a Core -> Shell JSON-RPC request (the ``Method.SHELL_*`` / ``KEYCHAIN_*``
constants). This class turns each ``ShellBridge`` Protocol call (tools/base.py)
into one such request, sends it over the server's single outgoing channel, and
blocks the calling worker thread until the matching response frame comes back.

Correlation is by JSON-RPC ``id``: ``_call`` parks a ``threading.Event`` in a
pending map keyed by the request id, and the server's read loop hands each
response frame to ``resolve_response`` which wakes the waiter. A shell error or a
timeout becomes a ``RuntimeError`` carrying a plain-language message (CLAUDE.md —
no stack traces reach the user).

Key handling (§5, §8.3): ``get_provider_key`` fetches the API key from the OS
keychain via the shell at the moment of use only; it is returned to the caller
and NEVER stored on this bridge.
"""

from __future__ import annotations

import os
import threading

from typing import Protocol

from agent_core.protocol import Method
from agent_core.tools.base import ShellBridge


class ServerShellBridge(ShellBridge, Protocol):
    """What the SERVER needs from a bridge: every tool-facing method, plus the ones
    it calls itself (``bind_sender``, ``get_app_build_ref``, ``list_armed``,
    ``resolve_response``, and the review surface's two read paths).

    ``ShellBridge`` (tools/base.py) is the TOOL-facing contract and deliberately
    stops there — a tool has no business binding the server's writer. But the
    server also calls ``bind_sender`` and ``get_app_build_ref``, so annotating its
    parameter with the tool Protocol would not typecheck, and annotating it with
    the concrete ``IpcShellBridge`` (which is what it said) means no test double
    can ever satisfy it: pyright rejected three of them, and the fakes were right
    — a fake that implements the whole contract IS a valid bridge.

    So: the contract the server actually depends on, named. Structural, so
    ``IpcShellBridge`` satisfies it without declaring anything."""

    def bind_sender(self, send) -> None: ...

    def get_app_build_ref(self) -> dict: ...

    def list_armed(self) -> dict: ...

    def resolve_response(self, req_id, result, error) -> bool: ...

    # The review surface's read paths (Phase-3 plan Build §1). They are declared HERE
    # and not on ``ShellBridge`` for the reason that Protocol's own docstring gives —
    # it is "exactly the surface the v1 tools need", and no tool may ever have these.
    # A browse is a person's click, answered by an RPC handler; a tool that could list
    # a directory would be the ``list_directory`` capability this design exists to
    # avoid handing the model.
    def list_workspace_directory(self, path: str) -> dict: ...

    def read_workspace_file_for_view(self, path: str) -> dict: ...

    # The review surface's revert half (Build §2/§3), declared here for the same
    # reason: no tool may ask either question. One is about Addison's own session
    # ledger and the other is about files the model never named.
    def can_restore_workspace_files(self, paths: list[str]) -> dict: ...

    def digest_workspace_files(self, paths: list[str]) -> dict: ...

# How long a single Core -> Shell request may wait before we give up on it. The
# shell answers a file/clipboard/draft call from its own process, so a stall this
# long means the shell is wedged: surface a retry rather than hang the turn forever.
_DEFAULT_TIMEOUT = 60.0

# ...but a ``keychain.*`` call is not the shell's own answer to give. The OS may put
# a password dialog in front of the person first, and a person is not a process:
# they may be away from the keyboard, or reading the dialog. Abandoning the request
# at sixty seconds does not cancel that dialog — it only guarantees that the
# password they eventually type lands on a request nobody is waiting on, so the turn
# fails anyway AND the shell's answer is thrown away. Human-paced, therefore.
_KEYCHAIN_TIMEOUT = 600.0

# ...and a ``shell.runCommand`` waits on the COMMAND's budget, not the shell's own
# responsiveness. The shell kills the child at the timeout it was given and answers,
# so this waiter only needs enough headroom on top to cover spawning sandbox-exec
# and draining the pipes. Too little and a slow-but-legal command is reported as a
# stalled shell while it keeps running unattended.
_EXEC_SLACK_MS = 15_000

# --- keychain trace (diagnostic, opt-in) -----------------------------------
#
# THE HALF THE SHELL CANNOT SEE. `keychain.rs` records what the OS was touched for
# — which is what costs a password dialog — but it cannot say WHO asked, and that
# is the open question: two dialogs for one item at launch means two callers, and
# the candidates (`_maybe_load_live_catalog` via availableRoles, `_provider_key_present`
# via provider.list and stats.get, the per-turn probe) are indistinguishable at the
# wire. So this side prints the core call site and the shell side prints the OS
# touch, both to stderr — which `agent_process.rs` inherits, so one launch produces
# a single interleaved, ordered trace in the terminal.
#
# Same switch as the shell (`ADDISON_KEYCHAIN_TRACE=1`), read per call rather than
# at import so it can be flipped without a rebuild of anything.
#
# **NEVER A KEY.** This prints the CALLER and the provider id, and returns before
# the result is in hand — there is deliberately no "-> found" line on this side,
# because the value is in scope here and a trace that reports on it is one edit away
# from printing it. The shell already reports the outcome, from the one place where
# the variant can be named without the value.
_TRACE_ENV = "ADDISON_KEYCHAIN_TRACE"

# Frames belonging to the plumbing rather than to a caller — walking past these is
# what turns a stack into the answer ("_maybe_load_live_catalog") instead of the
# question ("get_provider_key").
#
# Matched on the BASENAME, not with ``endswith`` on the path: ``endswith`` also
# swallows ``test_shell_bridge.py`` (and any other ``*shell_bridge.py``), so the
# walk skipped straight past the real caller and reported pytest's internals. A
# suffix test on a filename is a substring test wearing a hat.
_TRACE_SKIP = {"shell_bridge.py"}


# How many core frames to print. ONE IS NOT ENOUGH, and the first live trace proved
# it: every line came back `main.py:1817 getter()` — the key-fetch CLOSURE, which is
# the nearest non-plumbing frame for every caller alike, so the trace answered
# "something asked" for all of them. The interesting name is one or two frames
# further out (`_provider_key_present`, `_maybe_load_live_catalog`), and a chain
# beats a longer skip list: skipping is a guess about which frames are boring, and a
# guess that is wrong hides the answer instead of adding noise.
_TRACE_FRAMES = 3


def _trace_caller() -> str:
    """The nearest few agent_core frames outside this module — i.e. who wanted a key,
    innermost first, e.g. ``getter() <- _provider_key_present() <- _provider_list()``.

    Best-effort by construction: a diagnostic must never be able to break the call
    it is diagnosing, so any failure to read the stack answers "unknown" rather
    than raising into a keychain fetch."""
    try:
        import traceback

        chain: list[str] = []
        for frame in reversed(traceback.extract_stack()[:-2]):
            name = frame.filename.rsplit("/", 1)[-1]
            if name in _TRACE_SKIP:
                continue
            chain.append(f"{name}:{frame.lineno} {frame.name}()")
            if len(chain) == _TRACE_FRAMES:
                break
        if chain:
            return " <- ".join(chain)
    except Exception:
        pass
    return "unknown"


def _trace(what: str) -> None:
    if os.environ.get(_TRACE_ENV) != "1":
        return
    import sys

    # stderr: stdout is the JSON-RPC channel to the shell and a stray line on it
    # would corrupt a frame.
    print(f"[keychain            ] core   {what:<16} {_trace_caller()}", file=sys.stderr, flush=True)


# Plain-language, never-leaks-internals fallbacks (CLAUDE.md).
_TIMEOUT_MESSAGE = "Addison couldn't finish that just now. Please try again."
_GENERIC_ERROR = "Addison couldn't complete that action. Please try again."


class IpcShellBridge:
    """Implements the ``ShellBridge`` Protocol over JSON-RPC-to-the-shell."""

    def __init__(self, send=None, timeout: float = _DEFAULT_TIMEOUT) -> None:
        # ``send`` writes one frame dict to the server's outgoing channel. It is
        # bound late (``bind_sender``) because the server owns the locked writer
        # and is constructed after this bridge (the tools/provider need the
        # bridge first).
        self._send = send
        self._timeout = timeout
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._counter = 0

    def bind_sender(self, send) -> None:
        """Point the bridge at the server's locked frame writer."""
        self._send = send

    # --- request/response plumbing ----------------------------------------
    def _next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"core-req-{self._counter}"

    def _call(self, method: str, params: dict, timeout: float | None = None) -> dict:
        # ``timeout`` overrides the instance default for ONE call (the keychain
        # methods pass _KEYCHAIN_TIMEOUT); None keeps the default.
        if self._send is None:
            # No shell wired (e.g. CLI/dev). Callers translate this to a plain
            # "needs the desktop shell" message at the tool layer.
            raise RuntimeError(_GENERIC_ERROR)

        req_id = self._next_id()
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "result": None, "error": None}

        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        if not event.wait(timeout=self._timeout if timeout is None else timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise RuntimeError(_TIMEOUT_MESSAGE)

        with self._lock:
            record = self._pending.pop(req_id, None)
        if record is None:
            raise RuntimeError(_TIMEOUT_MESSAGE)
        if record["error"] is not None:
            raise RuntimeError(_error_message(record["error"]))
        return record["result"] or {}

    def resolve_response(self, req_id, result, error) -> bool:
        """Wake the ``_call`` waiting on ``req_id`` (server read-loop side).

        Returns True if the id matched a pending request — the server uses that
        to tell a response-to-us apart from anything else."""
        with self._lock:
            record = self._pending.get(req_id)
            if record is None:
                return False
            record["result"] = result
            record["error"] = error
            record["event"].set()
        return True

    # --- ShellBridge Protocol (tools/base.py) -----------------------------
    def save_new_file(self, filename: str, content: str) -> str:
        result = self._call(Method.SHELL_SAVE_NEW_FILE, {"filename": filename, "content": content})
        return result["path"]

    def delete_file(self, path: str) -> None:
        self._call(Method.SHELL_DELETE_FILE, {"path": path})

    def restore_file(self, path: str, content: str) -> None:
        # Redo of delete_file: the shell only honors paths it removed this session.
        self._call(Method.SHELL_RESTORE_FILE, {"path": path, "content": content})

    def open_draft(self, to: str, subject: str, body: str) -> str:
        result = self._call(
            Method.SHELL_OPEN_DRAFT, {"to": to, "subject": subject, "body": body}
        )
        return result["draftRef"]

    def discard_draft(self, draft_ref: str) -> None:
        self._call(Method.SHELL_DISCARD_DRAFT, {"draftRef": draft_ref})

    def read_clipboard(self) -> str:
        return self._call(Method.SHELL_READ_CLIPBOARD, {})["text"]

    def open_external(self, url: str) -> None:
        self._call(Method.SHELL_OPEN_EXTERNAL, {"url": url})

    def read_scoped_file(self, file_handle: str) -> dict:
        # The shell owns format extraction and hands back {"content", "kind"}.
        return self._call(Method.SHELL_READ_SCOPED_FILE, {"fileHandle": file_handle})

    # --- workspace-trust file surface (step 5, OPEN harness) ---------------
    def write_workspace_file(self, path: str, content: str) -> dict:
        # The shell captures prior state atomically and refuses binary/oversize/data
        # dir, so undo always round-trips: returns {"existed", "prior",
        # "newlineRestored"} — the last of which says the bytes on disk are `content`
        # plus the trailing newline this edit had dropped (tools/base.py's contract).
        return self._call(
            Method.SHELL_WRITE_WORKSPACE_FILE, {"path": path, "content": content}
        )

    def read_workspace_file(self, path: str) -> str:
        return self._call(Method.SHELL_READ_WORKSPACE_FILE, {"path": path})["content"]

    def pick_directory(self) -> str:
        # Native folder picker; raises (RuntimeError) if the user cancels.
        return self._call(Method.SHELL_PICK_DIRECTORY, {})["path"]

    def restore_workspace_file(self, path: str, prior_content: str | None) -> None:
        # undo of write_workspace_file: put prior bytes back, or DELETE when None (the
        # write created the file). The shell only honors a path it wrote this session.
        params = (
            {"path": path, "delete": True}
            if prior_content is None
            else {"path": path, "content": prior_content}
        )
        self._call(Method.SHELL_RESTORE_WORKSPACE_FILE, params)

    # --- the review surface's read paths (Phase-3 plan Build §1) -----------
    # NOT ON THE TOOL PROTOCOL, and that is the design rather than an oversight: these
    # answer a person's click through ``workspace.listDirectory`` / ``workspace.readFile``,
    # so they are declared on ``ServerShellBridge`` above and ``tools/base.py`` says why
    # at the Protocol they are absent from. A tool with these methods would be a
    # ``list_directory`` capability the model could reach for, and a permission card in
    # front of a folder somebody just opened.
    #
    # Each takes ONE argument: the path the CALLER already resolved and checked. The
    # handler never re-reads its raw parameter on the way here — that is the TOCTOU gap
    # step 5 closed for the file tools, and it is the same gap on a read.

    def list_workspace_directory(self, path: str) -> dict:
        """One level of ``path``: ``{"entries": [{"name", "kind", "size"}], "truncated"}``.

        Never recursive, and nothing is hidden. ``kind`` is read WITHOUT following
        links (a symlink is ``"symlink"``, never the kind of its target), and the
        shell caps the listing at 500 entries because that is where the bytes are —
        a 200k-entry folder is a multi-megabyte single line on this channel."""
        return self._call(Method.SHELL_LIST_WORKSPACE_DIRECTORY, {"path": path})

    def read_workspace_file_for_view(self, path: str) -> dict:
        """One file's text to SHOW: ``{"content", "bytes", "truncated"}``.

        Its own method rather than a flag on ``read_workspace_file`` because the two
        want OPPOSITE answers to an oversize file: the TOOL must refuse (a model that
        reads half a file and rewrites it from what it saw destroys the tail), the
        VIEWER truncates on a character boundary and says so. ``bytes`` is the file's
        size on disk, so a truncated view can say how much is not shown."""
        return self._call(Method.SHELL_READ_WORKSPACE_FILE_FOR_VIEW, {"path": path})

    # --- the review surface's revert half (Phase-3 plan Build §2/§3) -------
    # BOTH ARE BATCHES, and both answer a MAP keyed by the path rather than a list
    # positioned against the one sent. A positional answer couples two processes to an
    # ordering, and the failure when they stop agreeing is silent and precisely wrong:
    # a Revert offered for a file that cannot take one. A path missing from either map
    # reads as the cautious answer on this side (not restorable / cannot tell).

    def can_restore_workspace_files(self, paths: list[str]) -> dict:
        """Which of these paths the shell would let ``restore_workspace_file`` touch:
        ``{"restorable": {path: bool}}``.

        A PURE QUERY — it changes nothing and reads no file. It exists because the
        shell's write ledger is SESSION-scoped while ``action_snapshots`` rows are not,
        so after a restart every historic edit is describable and none is revertable.
        Asking first is what turns a button that always fails into a plain sentence."""
        return self._call(Method.SHELL_CAN_RESTORE_WORKSPACE_FILES, {"paths": list(paths)})

    def digest_workspace_files(self, paths: list[str]) -> dict:
        """What is on disk NOW, hashed: ``{"digests": {path: {"sha256", "missing"}}}``.

        Compared against the ``wrote_sha256`` the write recorded, this is how the
        surface tells "as Addison left it" from "edited since" — the difference between
        a diff that is honest and a Revert that quietly discards somebody's own work.
        ``sha256`` is ``None`` whenever the shell cannot judge (too big, unreadable,
        Addison's own data dir), which the surface says out loud rather than guessing.

        The bytes stay in the shell. Reading each file across this bridge to hash it
        here would ship megabytes for a payload that carries none of them — the very
        thing ``workspace.listEdits`` is metadata-only to avoid."""
        return self._call(Method.SHELL_DIGEST_WORKSPACE_FILES, {"paths": list(paths)})

    # --- OPEN-mode command execution (step 5.5, item 1) --------------------
    def run_command(self, command: str, timeout_ms: int, write_roots: list[str]) -> dict:
        """Run a command in the SHELL, under its seatbelt profile.

        The per-call timeout is the command's own budget plus ``_EXEC_SLACK_MS``,
        never the bridge default: the shell kills the child at ``timeoutMs`` and
        answers, so if this waiter gave up first the command would keep running
        with nobody to receive its output — and the turn would report a bridge
        stall for what is really a slow command. The slack covers spawning
        ``sandbox-exec`` and draining the pipes after the kill."""
        return self._call(
            Method.SHELL_RUN_COMMAND,
            {"command": command, "timeoutMs": timeout_ms, "writeRoots": list(write_roots)},
            timeout=(timeout_ms + _EXEC_SLACK_MS) / 1000.0,
        )

    # --- arming (step 8 phase 3) ------------------------------------------
    # THE NARROWNESS IS THE CONTRACT (plan §5.8). These three carry TYPED FIELDS
    # and never a document: the shell owns ``~/Library/LaunchAgents`` entirely —
    # it validates the ``com.addison.auto.`` label prefix, BUILDS the plist itself
    # from what is sent below, writes only ``<label>.plist`` in that one directory,
    # and refuses everything else. A method here that took XML (or a path, or a
    # directory) would be ``run_command`` with extra steps, and the core would have
    # gained the ability to write an arbitrary file into the one place where writing
    # a file IS arming.
    #
    # Nothing on this seam is a fallback for anything: there is no "install this
    # text", no "run launchctl", no second spelling. Three verbs, four fields, one
    # directory.

    def arm_automation(
        self, label: str, command: str, schedule_kind: str, schedule: dict
    ) -> dict:
        """Hand ONE job to launchd. Returns ``{"ok": bool, "error"?: str}``.

        ``schedule`` is the closed numeric field set of ``schedule_kind``
        (``automations.schedule_fields`` — ``{"minutes": n}`` or ``{"hour", "minute",
        "weekday"?}``), which is the same projection the person read on the card. The
        shell turns those numbers into ``StartInterval`` / ``StartCalendarInterval``
        and never sets ``RunAtLoad``, so arming can never cause an immediate run
        (plan §5.7) — the first execution happens on the OS's own schedule, which is
        what keeps "Addison never triggers itself" clean at the moment of
        installation."""
        return self._call(
            Method.SHELL_ARM_AUTOMATION,
            {
                "label": label,
                "command": command,
                "scheduleKind": schedule_kind,
                "schedule": dict(schedule),
            },
        )

    def disarm_automation(self, label: str) -> dict:
        """Take one job back out. Returns ``{"ok": bool, "error"?: str}``.

        IDEMPOTENT by contract: a label that is not installed is already in the
        state this asks for, so it answers ``ok`` rather than an error. Both callers
        depend on that — ``disarm_automation``'s tool (the person may have removed
        the file by hand) and ``arm_automation.undo`` (the person may have switched
        it off from the surface first)."""
        return self._call(Method.SHELL_DISARM_AUTOMATION, {"label": label})

    def list_armed(self) -> dict:
        """What launchd currently holds: ``{"armed": [label], "supported": bool}``.

        Reads; installs nothing. Asked on demand when a surface loads, never polled
        and never at startup (plan §5.6): armed truth lives in the OS, so a G3
        restore can put a ROW back and can never put a JOB back — and after a
        restore, a reinstall, or somebody deleting the file by hand, this is what
        makes the surface say what is actually true rather than what a row
        remembers."""
        return self._call(Method.SHELL_LIST_ARMED, {})

    # --- key fetch (§5) ---------------------------------------------------
    def get_provider_key(self, provider: str = "anthropic", fresh: bool = False) -> str:
        """Per-call API-key fetch from the OS keychain via the shell, keyed by
        PROVIDER id (``anthropic`` | ``openai`` | ``google`` | ``custom``).

        The key is returned to the caller for immediate one-request use and is
        never retained on this bridge (§8.3).

        Two outcomes, and they are NOT the same thing (the shell keeps them apart
        too): ``""`` means nothing is saved for this provider — a normal answer that
        onboarding acts on. A read that FAILED (the person dismissed the OS password
        dialog, the keychain errored) comes back as a JSON-RPC error and therefore
        raises here, because a key may well exist and Addison simply could not see
        it. Collapsing the two would route a turn to onboarding on the strength of a
        dialog nobody answered.

        ``fresh`` asks the shell to retry past a failure it remembered earlier this
        session. Sent ONLY by the per-turn probe: the person's own message may
        re-raise the dialog they dismissed, while the automatic pollers keep
        answering from the shell's memory."""
        _trace(f"want {provider}{' fresh' if fresh else ''}")
        params: dict = {"provider": provider}
        if fresh:
            params["fresh"] = True
        result = self._call(Method.KEYCHAIN_GET_PROVIDER_KEY, params, timeout=_KEYCHAIN_TIMEOUT)
        return result.get("key", "")

    # --- app build reference (G4) -----------------------------------------
    def get_app_build_ref(self) -> dict:
        """The running build, as ``{"version", "identifier"}``.

        Recorded on a G4 anchor so a later restore can SAY plainly that the app
        itself has moved on since the anchor was minted. A reference only — never
        bytes, never a path (a path goes stale on any move and usually embeds the
        user's account name, which would then land in a plaintext sidecar). This
        is also the ONE shell call the snapshot subsystem makes, and its failure
        is caught by the caller: an anchor mints with or without it, because
        undeletability is the floor and the build reference is the bonus."""
        return self._call(Method.SHELL_APP_BUILD_REF, {})

    # --- device identity & relay signing (§5) -----------------------------
    def get_device_key(self) -> dict:
        """Public device identity from the shell/keychain.

        Returns ``{"deviceId", "publicKey"}`` — the PUBLIC half ONLY. The private
        key never leaves the OS keychain and the core never sees it (§5)."""
        _trace("want device")
        return self._call(Method.KEYCHAIN_GET_DEVICE_KEY, {}, timeout=_KEYCHAIN_TIMEOUT)

    def sign_relay_request(self, payload: dict) -> dict:
        """Ask the shell to sign a Setup Assistant relay body with the device
        private key. Returns ``{"signature", "deviceId"}``.

        The core hands over bytes to sign and gets back a signature; the key
        material stays in the OS keychain and is never exposed here (§5, §8.4)."""
        _trace("want sign")
        return self._call(
            Method.KEYCHAIN_SIGN_RELAY_REQUEST, {"payload": payload}, timeout=_KEYCHAIN_TIMEOUT
        )


def _error_message(error) -> str:
    """A JSON-RPC error object -> a user-ready sentence, with no internals."""
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return _GENERIC_ERROR
