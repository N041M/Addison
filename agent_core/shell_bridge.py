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

import threading

from agent_core.protocol import Method

# How long a single Core -> Shell request may wait before we give up on it. The
# shell answers picker/keychain calls near-instantly; a stall this long means the
# shell is wedged, so surface a retry rather than hang the turn forever.
_DEFAULT_TIMEOUT = 60.0

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

    def _call(self, method: str, params: dict) -> dict:
        if self._send is None:
            # No shell wired (e.g. CLI/dev). Callers translate this to a plain
            # "needs the desktop shell" message at the tool layer.
            raise RuntimeError(_GENERIC_ERROR)

        req_id = self._next_id()
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "result": None, "error": None}

        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        if not event.wait(timeout=self._timeout):
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

    # --- key fetch (§5) ---------------------------------------------------
    def get_provider_key(self, provider: str = "anthropic") -> str:
        """Per-call API-key fetch from the OS keychain via the shell, keyed by
        PROVIDER id (``anthropic`` | ``openai`` | ``google`` | ``custom``).

        The key is returned to the caller for immediate one-request use and is
        never retained on this bridge (§8.3)."""
        result = self._call(Method.KEYCHAIN_GET_PROVIDER_KEY, {"provider": provider})
        return result.get("key", "")

    # --- device identity & relay signing (§5) -----------------------------
    def get_device_key(self) -> dict:
        """Public device identity from the shell/keychain.

        Returns ``{"deviceId", "publicKey"}`` — the PUBLIC half ONLY. The private
        key never leaves the OS keychain and the core never sees it (§5)."""
        return self._call(Method.KEYCHAIN_GET_DEVICE_KEY, {})

    def sign_relay_request(self, payload: dict) -> dict:
        """Ask the shell to sign a Setup Assistant relay body with the device
        private key. Returns ``{"signature", "deviceId"}``.

        The core hands over bytes to sign and gets back a signature; the key
        material stays in the OS keychain and is never exposed here (§5, §8.4)."""
        return self._call(Method.KEYCHAIN_SIGN_RELAY_REQUEST, {"payload": payload})


def _error_message(error) -> str:
    """A JSON-RPC error object -> a user-ready sentence, with no internals."""
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return _GENERIC_ERROR
