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

from agent_core.protocol import Method

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
        # dir, so undo always round-trips: returns {"existed", "prior"}.
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
