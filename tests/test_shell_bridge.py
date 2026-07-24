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
from agent_core.shell_bridge import IpcShellBridge

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
