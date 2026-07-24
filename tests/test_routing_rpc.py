"""Step-3 stages 3-4 — routing.get/set, the hook split, and the local_only
interlock (contract D6/D7, [MF-C]/[MF-D], verification #1/#7/#9/#10).

Driven through the real JsonRpcServer on fake pipes (conftest.build_server), so
the dispatch, worker serialisation, snapshot hook, and conversation interlock are
all exercised end to end.
"""

from __future__ import annotations

import pytest

from agent_core.protocol import Method
from agent_core.providers.base import ModelResponse
from tests.conftest import _ScriptedProvider, _shutdown, build_server


def _call(h, method, params, req_id):
    h.reader.feed({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
    return h.writer.wait_for(lambda f: f.get("id") == req_id and ("result" in f or "error" in f))


# --- routing.get -------------------------------------------------------------
def test_routing_get_defaults_to_quality_first_toggle(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        result = _call(h, Method.ROUTING_GET, {}, 1)["result"]
        assert result["strategy"] == "quality_first"     # absent key -> default
        # Closed vocab, and NO balanced (owner decision 2026-07-24).
        assert result["availableStrategies"] == [
            "quality_first", "cost_first", "local_only", "custom"
        ]
        assert "balanced" not in result["availableStrategies"]
        assert result["customChain"] == []
        assert result["surface"] == "toggle"             # Simple profile default
    finally:
        _shutdown(h.reader, h.thread)


def test_routing_get_surface_is_full_for_developer(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        _call(h, Method.PROFILE_SET, {"profileId": "developer"}, 1)
        result = _call(h, Method.ROUTING_GET, {}, 2)["result"]
        assert result["surface"] == "full"
    finally:
        _shutdown(h.reader, h.thread)


# --- routing.set validation --------------------------------------------------
def test_routing_set_persists_a_valid_strategy(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        result = _call(h, Method.ROUTING_SET, {"strategy": "cost_first"}, 1)["result"]
        assert result == {"ok": True, "strategy": "cost_first", "customChain": []}
        assert _call(h, Method.ROUTING_GET, {}, 2)["result"]["strategy"] == "cost_first"
    finally:
        _shutdown(h.reader, h.thread)


def test_routing_set_refuses_an_unknown_strategy(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        result = _call(h, Method.ROUTING_SET, {"strategy": "balanced"}, 1)["result"]
        assert result["ok"] is False and "recognises" in result["error"]
        # Nothing changed.
        assert _call(h, Method.ROUTING_GET, {}, 2)["result"]["strategy"] == "quality_first"
    finally:
        _shutdown(h.reader, h.thread)


def test_routing_set_refuses_a_custom_chain_with_an_unknown_id(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        h.server.model_router.register_primary_model("m1", h.provider)
        result = _call(
            h, Method.ROUTING_SET, {"strategy": "custom", "customChain": ["m1", "ghost"]}, 1
        )["result"]
        assert result["ok"] is False and "doesn't have" in result["error"]
        assert _call(h, Method.ROUTING_GET, {}, 2)["result"]["customChain"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_routing_set_persists_a_valid_custom_chain(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        h.server.model_router.register_primary_model("m1", h.provider)
        h.server.model_router.register_primary_model("m2", h.provider)
        result = _call(
            h, Method.ROUTING_SET, {"strategy": "custom", "customChain": ["m2", "m1"]}, 1
        )["result"]
        assert result["ok"] is True and result["customChain"] == ["m2", "m1"]
        assert _call(h, Method.ROUTING_GET, {}, 2)["result"]["customChain"] == ["m2", "m1"]
    finally:
        _shutdown(h.reader, h.thread)


# --- Verification #7: custom-chain overwrite refuses if the snapshot fails ----
def test_custom_chain_overwrite_refused_when_snapshot_fails(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        h.server.model_router.register_primary_model("m1", h.provider)
        h.server.model_router.register_primary_model("m2", h.provider)
        # Land a valid chain first (snapshot healthy).
        _call(h, Method.ROUTING_SET, {"strategy": "custom", "customChain": ["m1", "m2"]}, 1)
        # Now force the snapshot to fail; a customChain OVERWRITE must be refused,
        # leaving the old chain intact (user-authored content, [S1]).
        h.server._snapshot_auto = lambda reason: False
        result = _call(h, Method.ROUTING_SET, {"customChain": ["m2", "m1"]}, 2)["result"]
        assert result["ok"] is False and "restore point" in result["error"]
        assert _call(h, Method.ROUTING_GET, {}, 3)["result"]["customChain"] == ["m1", "m2"]
    finally:
        _shutdown(h.reader, h.thread)


def test_strategy_change_proceeds_even_when_snapshot_fails(tmp_path):
    # The hook split's other half: a pure strategy change is a recoverable enum, so
    # it proceeds-with-warning rather than refusing when the snapshot fails.
    h = build_server(tmp_path, responses=[])
    try:
        h.server._snapshot_auto = lambda reason: False
        result = _call(h, Method.ROUTING_SET, {"strategy": "cost_first"}, 1)["result"]
        assert result["ok"] is True and result["strategy"] == "cost_first"
    finally:
        _shutdown(h.reader, h.thread)


# --- Verification #9 [MF-D]: toggle round-trip == never-touched ---------------
def test_toggle_round_trip_matches_never_touched(tmp_path):
    h = build_server(tmp_path, responses=[])
    try:
        never_touched = _call(h, Method.ROUTING_GET, {}, 1)["result"]["strategy"]
        _call(h, Method.ROUTING_SET, {"strategy": "cost_first"}, 2)
        _call(h, Method.ROUTING_SET, {"strategy": "quality_first"}, 3)
        round_tripped = _call(h, Method.ROUTING_GET, {}, 4)["result"]["strategy"]
        assert round_tripped == never_touched == "quality_first"
    finally:
        _shutdown(h.reader, h.thread)


# --- Verification #1 & #10: the local_only interlock (zero cloud outbound) ----
def _spy_cloud():
    # A provider that raises if it is ever asked to send — the "zero outbound"
    # assertion made mechanical: a leak becomes a loud failure, not a silent send.
    class _NeverSend(_ScriptedProvider):
        def send(self, messages, tools, effort=None, timeout=None) -> ModelResponse:
            raise AssertionError("cloud provider was reached under local_only")
    return _NeverSend([])


def test_local_only_with_no_locals_refuses_without_reaching_cloud(tmp_path):
    # Verification #1: local_only + no local models -> plain error, zero outbound.
    h = build_server(tmp_path, provider=_spy_cloud())
    try:
        _call(h, Method.ROUTING_SET, {"strategy": "local_only"}, 1)
        frame = _call(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "hi"}, 2)
        assert "error" in frame
        assert "only models on this computer" in frame["error"]["message"]
        assert "aren't any" in frame["error"]["message"]
    finally:
        _shutdown(h.reader, h.thread)


def test_local_only_refuses_an_explicit_cloud_pick_without_reaching_cloud(tmp_path):
    # Verification #10: local_only + an explicit cloud/PRIMARY pick -> refused
    # plainly, zero outbound (the privacy invariant outranks the picker, [MF-C]).
    h = build_server(tmp_path, provider=_spy_cloud())
    try:
        # A local model exists, so this is unambiguously the explicit-cloud path,
        # not the empty-pool one.
        h.server.model_router.register_local_model("llama3", _ScriptedProvider([]))
        _call(h, Method.ROUTING_SET, {"strategy": "local_only"}, 1)
        frame = _call(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "hi", "role": "primary"}, 2
        )
        assert "error" in frame
        assert "only models on this computer" in frame["error"]["message"]
        assert "Change how models are picked" in frame["error"]["message"]
    finally:
        _shutdown(h.reader, h.thread)


@pytest.mark.parametrize("strategy", ["quality_first", "cost_first"])
def test_non_local_strategy_still_reaches_the_cloud(tmp_path, strategy):
    # The complement: a normal strategy DOES answer via the configured provider —
    # proof the interlock is scoped to local_only, not a blanket block.
    h = build_server(tmp_path, responses=[ModelResponse(text="answered", tool_calls=[])])
    try:
        _call(h, Method.ROUTING_SET, {"strategy": strategy}, 1)
        frame = _call(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "hi"}, 2)
        assert frame.get("result", {}).get("ok") is True
    finally:
        _shutdown(h.reader, h.thread)


def test_local_only_never_reaches_the_relay(tmp_path):
    """D6's NAMED invariant test (added in the post-build rigor pass): under
    local_only, no model call leaves the machine — the Setup Assistant relay
    included. The dangerous path: a Simple profile with NO key redirects a
    PRIMARY-bound turn to the SETUP_ASSISTANT role (rpc/conversation.py §4.6),
    and the router's resolve() falls through to the cloud PRIMARY when no relay
    is separately configured. The local_only interlock must force the LOCAL role
    BEFORE that branch, so neither the relay nor any cloud provider is reachable.
    Non-vacuous: with the interlock disabled, this exact setup redirects to
    SETUP_ASSISTANT, falls through to the raising cloud spy, and fails loudly."""
    h = build_server(tmp_path, provider=_spy_cloud())
    try:
        # Simple profile (default, setup_assistant onboarding) + keyless: the
        # relay redirect is armed for any default-role turn.
        h.server._primary_key_probe = lambda: False
        # One healthy local model, so the turn has a legitimate on-device answer.
        h.server.model_router.register_local_model(
            "llama3", _ScriptedProvider([ModelResponse(text="local answer", tool_calls=[])])
        )
        _call(h, Method.ROUTING_SET, {"strategy": "local_only"}, 1)
        frame = _call(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "hi"}, 2)
        # Answered ok — by the LOCAL model; any relay/cloud touch would have
        # raised inside the spy and errored the turn instead.
        assert frame.get("result", {}).get("ok") is True, frame
    finally:
        _shutdown(h.reader, h.thread)


def test_a_vanished_custom_chain_id_is_skipped_with_one_note(tmp_path):
    """D3's vanished-id NOTE (post-build rigor pass — the skip shipped, the note
    did not): a custom-chain model that has disappeared since the list was saved
    is skipped AND named in one Activity Panel note, because a fallback model the
    user chose silently vanishing is exactly the quiet change the panel exists to
    surface."""
    h = build_server(tmp_path, responses=[ModelResponse(text="ok", tool_calls=[])])
    try:
        h.server.model_router.register_primary_model("m1", h.provider)
        h.server.model_router.register_primary_model("m2", h.provider)
        _call(h, Method.ROUTING_SET, {"strategy": "custom", "customChain": ["m2", "m1"]}, 1)
        # m2 vanishes AFTER the save (a disconnect) — set-time validation can't help.
        h.server.model_router.unregister_primary_model("m2")
        # A real turn on the worker thread (store affinity); the chain builds there
        # and the note reaches the wire as a tool.activityUpdate frame.
        frame = _call(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "hi"}, 2)
        assert frame.get("result", {}).get("ok") is True, frame
        notes = [
            f["params"]
            for f in h.writer.frames
            if f.get("method") == Method.TOOL_ACTIVITY_UPDATE
            and f.get("params", {}).get("toolId") == "routing"
            and "m2" in f.get("params", {}).get("label", "")
        ]
        assert len(notes) == 1, notes
    finally:
        _shutdown(h.reader, h.thread)
