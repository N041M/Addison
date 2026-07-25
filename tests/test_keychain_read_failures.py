"""A keychain read that FAILS must never send the conversation off this machine.

The routing-privacy rule, in one sentence: "no key is saved" and "the key could
not be read" are different facts, and only the first one is allowed to reach the
Setup Assistant relay. Collapsed into a single ``False``, a dismissed macOS
password dialog routed a Simple turn to an EXTERNAL service while the person's own
key sat unread in their keychain — and nothing on screen said so.

Every other test of this seam stands in for one side of it: a probe that raises, a
bridge with a fake sender. This file drives the WHOLE chain from the wire, because
the chain is where the bug lived — the pieces were each defensible on their own.
It spawns the real ``python -m agent_core.main`` over real pipes and plays the Rust
shell (docs/HANDOFF.md, "the live-driver pattern"), so what is under test is the
production wiring nothing in-process can reach: ``main()``'s per-provider key
getter, its ``_primary_key_available`` probe, and its ``_connect_provider`` — all
closures inside ``main()``.

The protocol seam being pinned, both halves of it (the Rust side is pinned by
``shell/src-tauri/src/keychain.rs``'s own tests):

- a key                -> ``{"key": "<value>"}``
- NOTHING SAVED        -> ``{"key": ""}``   — a normal result, not an error
- THE READ FAILED      -> a JSON-RPC error frame

Two things make the "nothing left the machine" assertions real rather than
inferred. The relay is a recording HTTP stub on loopback (``ADDISON_RELAY_URL``),
so a turn that relays is a request this file can count and a turn that does not is
an empty list. And the subprocess environment is built from scratch — no
``ANTHROPIC_API_KEY`` — because ``main()``'s getter falls back to that variable
when the shell has nothing, which would quietly answer the very question under
test.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agent_core.main import _BYOK_ONBOARDING_MESSAGE
from agent_core.memory.store import Store
from agent_core.protocol import Method
from agent_core.rpc.constants import _KEY_UNREADABLE_MESSAGE

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The sentence the Rust shell sends when the read itself failed (keychain.rs). It
# is the shell's words, not the core's: the core repeats what it was told rather
# than inventing a reason it does not know.
_SHELL_UNREADABLE = "Couldn't read your saved key from the keychain."

# A hung subprocess must fail the test, not the suite. Generous because it only
# ever elapses when something is genuinely wedged.
_WATCHDOG_SECONDS = 20.0


class _RelayStub:
    """The Setup Assistant relay, on loopback, recording every request body.

    A recording stub rather than a dead port: "the relay was never called" has to
    be an assertion about a list, not about which error message came back. It also
    keeps the test honest in the other direction — the missing-key turn genuinely
    completes through it, so the two cases differ by the keychain answer alone.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        stub = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's name)
                length = int(self.headers.get("content-length") or 0)
                stub.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                body = json.dumps({"text": "setup reply"}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass   # keep pytest output clean

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1/chat"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _AppUnderDriver:
    """The real app in a subprocess, with this test playing the Rust shell.

    ``keychain.getProviderKey`` is answered according to ``key_answer``, which the
    test flips between turns — that is the single variable these tests turn on.
    ``getDeviceKey``/``signRelayRequest`` are answered the way the shell does (the
    public half and a signature; no private key ever crosses the pipe, G1), so a
    relayed turn can actually complete.
    """

    def __init__(self, db_path: Path, relay: _RelayStub) -> None:
        self.relay = relay
        self.key_answer: object = ""     # "" | a key string | _READ_FAILED
        self.frames: list[dict] = []
        self._responses: dict[object, dict] = {}
        self._lock = threading.Lock()
        self._arrived = threading.Event()
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "agent_core.main"],
            cwd=_REPO_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            # Built from scratch: no ANTHROPIC_API_KEY (main()'s dev fallback would
            # answer the question under test), and the relay pointed at the stub so
            # no request can leave the machine even if a turn tries to relay.
            env={
                "PATH": "/usr/bin:/bin",
                "ADDISON_DB_PATH": str(db_path),
                "ADDISON_RELAY_URL": relay.url,
            },
        )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._watchdog = threading.Timer(_WATCHDOG_SECONDS, self._proc.kill)
        self._watchdog.start()
        self._pump = threading.Thread(target=self._read_loop, daemon=True)
        self._pump.start()

    # --- playing the shell ------------------------------------------------
    def _send(self, frame: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(frame) + "\n")
        self._proc.stdin.flush()

    def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            frame = json.loads(line)
            with self._lock:
                self.frames.append(frame)
            method = frame.get("method")
            if method == Method.KEYCHAIN_GET_PROVIDER_KEY:
                self._answer_key_read(frame["id"])
            elif method == Method.KEYCHAIN_GET_DEVICE_KEY:
                self._send({"jsonrpc": "2.0", "id": frame["id"],
                            "result": {"deviceId": "dev-under-test", "publicKey": "pub"}})
            elif method == Method.KEYCHAIN_SIGN_RELAY_REQUEST:
                self._send({"jsonrpc": "2.0", "id": frame["id"],
                            "result": {"signature": "sig", "deviceId": "dev-under-test"}})
            elif method is None and frame.get("id") is not None:
                with self._lock:
                    self._responses[frame["id"]] = frame
                self._arrived.set()

    def _answer_key_read(self, frame_id) -> None:
        if self.key_answer is _READ_FAILED:
            self._send({"jsonrpc": "2.0", "id": frame_id,
                        "error": {"code": -32001, "message": _SHELL_UNREADABLE}})
        else:
            self._send({"jsonrpc": "2.0", "id": frame_id,
                        "result": {"key": self.key_answer}})

    # --- driving the app --------------------------------------------------
    def rpc(self, request_id, method: str, params: dict | None = None) -> dict:
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method,
                    "params": params or {}})
        deadline = time.monotonic() + _WATCHDOG_SECONDS
        while time.monotonic() < deadline:
            with self._lock:
                if request_id in self._responses:
                    return self._responses[request_id]
            self._arrived.wait(0.05)
            self._arrived.clear()
        raise AssertionError(f"no answer to {method}; frames so far: {self.frames}")

    def keychain_methods(self) -> list[str]:
        """Every ``keychain.*`` request the app has made since the last reset."""
        with self._lock:
            return [f["method"] for f in self.frames
                    if str(f.get("method") or "").startswith("keychain.")]

    def forget_frames(self) -> None:
        with self._lock:
            self.frames.clear()

    def close(self) -> None:
        self._watchdog.cancel()
        self._proc.kill()
        self._proc.wait(timeout=5)


class _ReadFailed:
    """Sentinel: the shell answered the key read with an error frame."""


_READ_FAILED = _ReadFailed()


@pytest.fixture
def app(tmp_path, request):
    """The app under the driver, on the profile named by the test's marker.

    The profile is persisted BEFORE launch because the server resolves it from the
    store on its worker thread — the same route the desktop app takes.
    """
    profile = getattr(request, "param", "simple")
    db_path = tmp_path / "keychain-driver.sqlite3"
    store = Store(db_path)
    store.set_setting("active_profile", profile)
    store.close()

    relay = _RelayStub()
    driver = _AppUnderDriver(db_path, relay)
    try:
        yield driver
    finally:
        driver.close()
        relay.close()


# ============================================================================
# The rule itself: an unreadable key is answered here, a missing one onboards.
# ============================================================================
@pytest.mark.parametrize("app", ["simple"], indirect=True)
def test_an_unreadable_key_never_sends_a_simple_turn_to_the_relay(app):
    """Under Simple, "no key" is the relay's cue — so a failed read must not look
    like one.

    This is the whole defect in one test. The person has a working key; macOS asks
    for permission and the dialog is dismissed or times out; with the two states
    collapsed the turn was answered by an EXTERNAL service, and the only visible
    difference was a reply that seemed oddly unaware of their setup.

    The second half is the control: nothing about the app changes between the two
    turns except the shell's answer to one key read, and that alone decides whether
    the message leaves the machine. It also shows the turn was refused before
    anything was written — the relayed history carries ONLY the second message, so
    the refused turn never reached the transcript.
    """
    app.key_answer = _READ_FAILED
    answer = app.rpc(1, Method.CONVERSATION_SEND_MESSAGE, {"text": "unreadable turn"})

    # The privacy claim first, because it is the one with a victim: a turn that
    # relayed answers "ok" and would fail the message assertion below with nothing
    # said about where the message went.
    assert app.relay.requests == [], "a keychain failure sent the conversation off-machine"
    assert answer.get("error", {}).get("message") == _KEY_UNREADABLE_MESSAGE

    # Same app, same conversation — only the shell's answer differs.
    app.key_answer = ""
    app.forget_frames()
    relayed = app.rpc(2, Method.CONVERSATION_SEND_MESSAGE, {"text": "no key saved"})

    assert relayed["result"]["ok"] is True
    assert len(app.relay.requests) == 1
    assert app.relay.requests[0]["messages"] == [{"role": "user", "content": "no key saved"}]


@pytest.mark.parametrize("app", ["simple"], indirect=True)
def test_one_relayed_turn_asks_the_shell_for_the_device_key_exactly_once(app):
    """One relayed message costs three keychain requests, and no more.

    One is the routing probe that decided to relay at all; the other two are
    ``SetupAssistantProvider.send`` signing its body with the device key, which
    takes a ``getDeviceKey`` and then a ``signRelayRequest``. Both of those used to
    reach the OS keychain, so a single typed message raised two password dialogs
    back to back. The shell now keeps the device identity for the session and
    answers the second from memory — a cache sized against exactly this shape. If
    the count here grows, it is absorbing more than it was built for and the
    dialogs come back.
    """
    app.key_answer = ""
    app.rpc(1, Method.CONVERSATION_SEND_MESSAGE, {"text": "hello"})

    assert app.keychain_methods() == [
        Method.KEYCHAIN_GET_PROVIDER_KEY,     # the routing probe: is there a key?
        Method.KEYCHAIN_GET_DEVICE_KEY,       # who is this device?
        Method.KEYCHAIN_SIGN_RELAY_REQUEST,   # sign the body
    ]


@pytest.mark.parametrize("app", ["developer"], indirect=True)
def test_developer_says_which_failure_happened_rather_than_asking_for_a_new_key(app):
    """Developer never relays, so the risk here is a false sentence, not a leak.

    "No API key is set up yet" is untrue when the key IS set up and the read failed
    — it sends someone off to replace a key that was fine, and the real cause (a
    dialog they dismissed) goes unmentioned. The refusal has to name which of the
    two happened, so the second half pins the byte-for-byte onboarding message that
    a missing key still gets.
    """
    app.key_answer = _READ_FAILED
    unreadable = app.rpc(1, Method.CONVERSATION_SEND_MESSAGE, {"text": "unreadable turn"})

    assert unreadable["error"]["message"] == _KEY_UNREADABLE_MESSAGE
    assert unreadable["error"]["message"] != _BYOK_ONBOARDING_MESSAGE

    app.key_answer = ""
    missing = app.rpc(2, Method.CONVERSATION_SEND_MESSAGE, {"text": "no key saved"})

    assert missing["error"]["message"] == _BYOK_ONBOARDING_MESSAGE
    # BYOK-first means BYOK-only: neither profile's onboarding may relay a turn it
    # refused, and Developer's refusal never had a relay branch to begin with.
    assert app.relay.requests == []


# ============================================================================
# provider.connect: the connect card must not blame the key for the keychain.
# ============================================================================
@pytest.mark.parametrize("app", ["simple"], indirect=True)
def test_connect_says_the_key_could_not_be_read_not_that_it_is_wrong(app):
    """Every Anthropic connect failure used to read "That key doesn't work."

    For a wrong key that is true. For a keychain that would not open it is a false
    accusation with an expensive follow-up: the person deletes a good key, pastes it
    again, and hits the same locked keychain. The connect path reads the key once up
    front so this failure keeps its own words — the shell's, repeated verbatim,
    because the core does not know more than it was told.

    No network is involved: the read fails before the validating request, which is
    also why this test can assert on the message at all.
    """
    app.key_answer = _READ_FAILED
    answer = app.rpc(1, Method.PROVIDER_CONNECT, {"provider": "anthropic"})

    assert answer["result"]["ok"] is False
    assert answer["result"]["error"] == _SHELL_UNREADABLE
    assert answer["result"]["error"] != "That key doesn't work. Check it and try again."
