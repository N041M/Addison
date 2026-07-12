"""ModelRouter — multiple local models (item B) and capability flags (item A).

Covers the v1 substrate that v2 auto-routing will build on: several local models
configured at once, an explicit per-message pick, and the vision capability flag
that gates the image path. All selection here is explicit — no auto-routing.
"""

import pytest

from agent_core.providers.base import ModelProvider, ModelRole, ProviderCapabilities
from agent_core.providers.router import ModelRouter


class _FakeProvider:
    """Minimal ModelProvider stand-in tagged so tests can tell instances apart."""

    def __init__(self, tag: str, vision: bool = False, off_device: bool = False):
        self.tag = tag
        self._vision = vision
        self._off_device = off_device

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=False,
            max_context_tokens=8_192,
            supports_streaming=True,
            runs_off_device=self._off_device,
            vision=self._vision,
        )

    def send(self, messages, tools):  # pragma: no cover - not exercised here
        raise NotImplementedError


def test_fake_provider_satisfies_protocol():
    assert isinstance(_FakeProvider("x"), ModelProvider)


def test_resolve_defaults_to_primary():
    primary = _FakeProvider("cloud")
    router = ModelRouter(configured={ModelRole.PRIMARY: primary})
    assert router.resolve().tag == "cloud"


def test_multiple_local_models_explicit_pick():
    # Mirrors a real setup: a 14B vision model + an 8B text-only model.
    vision_14b = _FakeProvider("ministral-14b", vision=True, off_device=True)
    text_8b = _FakeProvider("deepseek-8b", vision=False, off_device=True)
    router = ModelRouter(
        configured={ModelRole.PRIMARY: _FakeProvider("cloud")},
        local_models={"ministral-14b": vision_14b, "deepseek-8b": text_8b},
    )

    # First-added local model is the default selection.
    assert router.resolve(ModelRole.LOCAL).tag == "ministral-14b"
    # Explicit per-message pick of a specific local model (item B).
    assert router.resolve(ModelRole.LOCAL, model_name="deepseek-8b").tag == "deepseek-8b"
    # LOCAL shows up as an available role once local models exist.
    assert ModelRole.LOCAL in router.available_roles()
    assert set(router.available_local_models()) == {"ministral-14b", "deepseek-8b"}


def test_select_local_model_switches_default():
    router = ModelRouter(configured={}, local_models={"a": _FakeProvider("a"), "b": _FakeProvider("b")})
    router.select_local_model("b")
    assert router.resolve(ModelRole.LOCAL).tag == "b"
    with pytest.raises(KeyError):
        router.select_local_model("missing")


def test_vision_capability_flag_present_for_gating():
    # The flag the orchestrator reads to gate the image path (item A).
    vision_model = _FakeProvider("v", vision=True)
    text_model = _FakeProvider("t", vision=False)
    assert vision_model.capabilities().vision is True
    assert text_model.capabilities().vision is False
