"""Cloud-model catalog for the explicit picker (§4.1.1, §6.8).

The picker's menu is the live list of every model the configured PRIMARY key can
access — fetched from the Anthropic Models API (``fetch_cloud_catalog``) and shown
with the API's own names: each model's ``display_name`` and, for the "answer style"
control, the API's own effort ids (``low``/``medium``/``high``/``xhigh``/``max``).
Nothing is editorialised — no invented labels, no descriptions.

When the live list can't be fetched yet (no key, offline), a small built-in
fallback (``FALLBACK_CLOUD_MODELS``, real model names) keeps chat working; the
server swaps in the live list as soon as a fetch succeeds (main.py).

Two knobs per model, both fed straight to ``AnthropicProvider``:
  - ``adaptive_thinking`` — whether the model gets ``thinking: {"type": "adaptive"}``.
  - ``effort_levels``     — the "answer style" choices. An EMPTY tuple means the
                            model does not support the effort parameter at all, so
                            the picker hides the control and no ``output_config`` is
                            ever sent (sending it to such a model errors).

The wire shape (``to_wire``) is the contract the frontend renders against — see
``model.availableRoles`` in main.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import httpx


@dataclass(frozen=True)
class EffortLevel:
    """One "answer style" choice for a model. ``id`` is the value sent back as the
    ``effort`` param; ``label`` is what the user reads — the raw API id (low/medium/
    high/xhigh/max). Exactly one level per model (with any levels at all) is marked
    ``default`` so the picker can pre-select it."""

    id: str
    label: str
    default: bool = False


@dataclass(frozen=True)
class CloudModel:
    id: str                       # the API model id, e.g. "claude-opus-4-8"
    label: str                    # the model's display name shown in the picker
    description: str              # empty — editorial copy removed (raw names only)
    adaptive_thinking: bool = False
    effort_levels: tuple[EffortLevel, ...] = ()
    default: bool = False         # the catalog default — exactly one entry has this

    @property
    def supported_effort(self) -> tuple[str, ...]:
        """The effort ids this model accepts — what ``AnthropicProvider`` checks an
        incoming ``effort`` against before it sends ``output_config`` (empty = the
        model has no effort control, so effort is never sent)."""
        return tuple(level.id for level in self.effort_levels)

    def to_wire(self) -> dict:
        """The ``cloudModels`` entry shape the frontend renders (§4.1.1 contract):
        ``{id, label, description, effortLevels: [{id, label, default}], default}``.
        ``effortLevels`` is empty when the model has no answer-style control."""
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "effortLevels": [
                {"id": level.id, "label": level.label, "default": level.default}
                for level in self.effort_levels
            ],
            "default": self.default,
        }


# ---------------------------------------------------------------------------
# Built-in fallback — real model names, no editorial copy
# ---------------------------------------------------------------------------
# Effort levels are the API's own ids; "high" is the default answer style. Opus and
# Sonnet share the three levels; Haiku has no effort control (empty tuple).
_STANDARD_EFFORT: tuple[EffortLevel, ...] = (
    EffortLevel("low", "low"),
    EffortLevel("high", "high", default=True),
    EffortLevel("xhigh", "xhigh"),
)


# The minimal built-in list used until a live fetch succeeds. Real names, no
# descriptions. Exactly one entry is the default (opus); ADDISON_MODEL can move the
# default at runtime via ``load_cloud_catalog`` — the same dev/test knob main.py reads.
FALLBACK_CLOUD_MODELS: tuple[CloudModel, ...] = (
    CloudModel(
        id="claude-opus-4-8",
        label="Claude Opus 4.8",
        description="",
        adaptive_thinking=True,
        effort_levels=_STANDARD_EFFORT,
        default=True,
    ),
    CloudModel(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        description="",
        adaptive_thinking=True,
        effort_levels=_STANDARD_EFFORT,
    ),
    CloudModel(
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        description="",
        adaptive_thinking=False,
        effort_levels=(),   # no effort control — sending output_config errors
    ),
)


def load_cloud_catalog(model_override: str | None = None) -> list[CloudModel]:
    """The built-in fallback catalog, with ``ADDISON_MODEL`` (if set) forced default.

    ``ADDISON_MODEL`` is the same dev/test knob main.py reads for the shipped default
    model. When it names a fallback entry, that entry becomes the default (its effort
    levels and thinking flag preserved). When it names a model NOT in the fallback, a
    bare entry with no effort control is appended and made default — so a live test
    sweep can point at any model without editing this file. Otherwise the fallback's
    own default (opus) stands. Exactly one returned entry is always the default.

    ``model_override`` lets tests pass the value explicitly instead of via the env.
    """
    override = model_override if model_override is not None else os.environ.get("ADDISON_MODEL")
    override = (override or "").strip()

    if not override:
        return [replace(model) for model in FALLBACK_CLOUD_MODELS]

    if any(model.id == override for model in FALLBACK_CLOUD_MODELS):
        # A fallback model: move the default flag onto it, leave everything else intact.
        return [replace(model, default=(model.id == override)) for model in FALLBACK_CLOUD_MODELS]

    # Not in the fallback: keep the list (defaults cleared) and append a bare default.
    catalog = [replace(model, default=False) for model in FALLBACK_CLOUD_MODELS]
    catalog.append(
        CloudModel(
            id=override,
            label=override,
            description="",
            adaptive_thinking=False,
            effort_levels=(),
            default=True,
        )
    )
    return catalog


def default_cloud_model(catalog: list[CloudModel]) -> CloudModel:
    """The default entry (exactly one is marked ``default``); the first is a safe
    fallback if a caller hands over a catalog with none marked."""
    for model in catalog:
        if model.default:
            return model
    return catalog[0]


def find_cloud_model(catalog: list[CloudModel], model_id: str) -> CloudModel | None:
    """The catalog entry with this id, or None — used to validate an explicit pick."""
    for model in catalog:
        if model.id == model_id:
            return model
    return None


# ---------------------------------------------------------------------------
# Live fetch from the Anthropic Models API (GET /v1/models)
# ---------------------------------------------------------------------------
_MODELS_URL = "https://api.anthropic.com/v1/models"
_ANTHROPIC_VERSION = "2023-06-01"
# Short timeout: this runs on the picker's read path (main.py fetches it lazily on
# availableRoles), so it must give up quickly and fall back rather than hang.
_FETCH_TIMEOUT_SECONDS = 10.0
_PAGE_LIMIT = 1000
# Fixed display/priority order for the effort control; the API reports which of
# these each model actually supports.
_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
# Preferred default when present and no ADDISON_MODEL override is set.
_DEFAULT_MODEL_ID = "claude-opus-4-8"


class CatalogFetchError(Exception):
    """Raised by ``fetch_cloud_catalog`` for ANY failure — no key, network, HTTP
    status, or an unreadable/empty body. The single error the caller catches to fall
    back to ``FALLBACK_CLOUD_MODELS``; its message is internal and never user-facing."""


def fetch_cloud_catalog(api_key_getter, client=None) -> list[CloudModel]:
    """Every model the configured PRIMARY key can access, as ``CloudModel`` entries.

    ``GET /v1/models`` (paginated via ``after_id`` until ``has_more`` is false), the
    same house HTTP pattern as anthropic_provider.py: an injectable httpx client
    (tests wire a MockTransport), the key fetched from ``api_key_getter`` ONCE for
    this call and used only in the request headers — never retained (invariant §8.3).
    Labels are the raw API ``display_name``s and effort levels the API's own ids;
    nothing is editorialised. Newest models come first.

    Any network/HTTP/parse failure raises ``CatalogFetchError`` — the one error the
    caller catches to fall back to the built-in list. Never raises anything else.
    """
    api_key = _resolve_fetch_key(api_key_getter)
    dated: list[tuple[float, CloudModel]] = []
    injected = client
    http = injected if injected is not None else httpx.Client(timeout=_FETCH_TIMEOUT_SECONDS)
    try:
        after_id: str | None = None
        while True:
            page = _fetch_models_page(http, api_key, after_id)
            for raw in page.get("data") or []:
                parsed = _parse_model_entry(raw)
                if parsed is not None:
                    dated.append(parsed)
            if not page.get("has_more"):
                break
            after_id = page.get("last_id")
            if not after_id:   # has_more but no cursor — stop rather than loop forever
                break
    finally:
        if injected is None:
            http.close()

    if not dated:
        raise CatalogFetchError("The model list came back empty.")
    # Newest first by created_at.
    dated.sort(key=lambda pair: pair[0], reverse=True)
    models = [model for _, model in dated]
    return _apply_default_model(models)


def _resolve_fetch_key(api_key_getter) -> str:
    """The key for this fetch, from the getter — never stored anywhere (§8.3). A
    missing getter, an empty key, or a getter that fails all read as 'no key', so the
    caller falls back to the built-in list rather than crashing."""
    if api_key_getter is None:
        raise CatalogFetchError("No API key available for the model list.")
    try:
        key = api_key_getter()
    except Exception:
        raise CatalogFetchError("Couldn't read the API key for the model list.") from None
    if not key:
        raise CatalogFetchError("No API key available for the model list.")
    return key


def _fetch_models_page(http: httpx.Client, api_key: str, after_id: str | None) -> dict:
    """One page of ``GET /v1/models``; every failure becomes a ``CatalogFetchError``."""
    params: dict = {"limit": _PAGE_LIMIT}
    if after_id:
        params["after_id"] = after_id
    headers = {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION}
    try:
        response = http.get(_MODELS_URL, headers=headers, params=params)
    except httpx.HTTPError:
        raise CatalogFetchError("Couldn't reach the model list.") from None
    if response.status_code >= 400:
        raise CatalogFetchError(f"The model list request failed (status {response.status_code}).")
    try:
        data = response.json()
    except ValueError:
        raise CatalogFetchError("The model list came back unreadable.") from None
    if not isinstance(data, dict):
        raise CatalogFetchError("The model list came back in an unexpected shape.")
    return data


def _parse_model_entry(raw) -> tuple[float, CloudModel] | None:
    """One ``/v1/models`` entry -> ``(created_sort_key, CloudModel)``, or None if it
    has no usable id. Defensive throughout: any missing capabilities branch reads as
    'unsupported' rather than raising (older/odd entries may omit branches entirely)."""
    if not isinstance(raw, dict):
        return None
    model_id = raw.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    display = raw.get("display_name")
    label = display.strip() if isinstance(display, str) and display.strip() else model_id
    capabilities = raw.get("capabilities")
    model = CloudModel(
        id=model_id,
        label=label,
        description="",
        adaptive_thinking=_capability_supported(
            _branch(_branch(capabilities, "thinking"), "types"), "adaptive"
        ),
        effort_levels=_effort_levels(capabilities),
    )
    return (_created_sort_key(raw.get("created_at")), model)


def _effort_levels(capabilities) -> tuple[EffortLevel, ...]:
    """The supported effort ids, in the fixed order, labelled by their raw id. The
    default level is ``high`` when supported, else the middle supported level."""
    effort_caps = _branch(capabilities, "effort")
    supported = [level for level in _EFFORT_ORDER if _capability_supported(effort_caps, level)]
    if not supported:
        return ()
    default_id = "high" if "high" in supported else supported[len(supported) // 2]
    return tuple(
        EffortLevel(level, level, default=(level == default_id)) for level in supported
    )


def _branch(node, key: str) -> dict:
    """The child dict at ``node[key]`` when both are dicts, else ``{}`` — so a missing
    or oddly-shaped capabilities branch reads as 'unsupported' and never raises."""
    if isinstance(node, dict):
        child = node.get(key)
        if isinstance(child, dict):
            return child
    return {}


def _capability_supported(node, key: str) -> bool:
    """True only when ``node[key]`` is a dict whose ``supported`` leaf is exactly True."""
    if not isinstance(node, dict):
        return False
    leaf = node.get(key)
    return isinstance(leaf, dict) and leaf.get("supported") is True


def _created_sort_key(value) -> float:
    """A sortable timestamp from an ISO ``created_at`` string; 0 for anything
    unparseable (missing/odd entries sort oldest)."""
    if not isinstance(value, str) or not value.strip():
        return 0.0
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _apply_default_model(models: list[CloudModel]) -> list[CloudModel]:
    """Mark exactly one entry the default: the ADDISON_MODEL override if set, else
    ``claude-opus-4-8`` if present, else the newest (first) entry. An override naming
    a model NOT in the fetched list appends a bare default entry — dev/test-knob
    parity with ``load_cloud_catalog`` so a live sweep can point at any model."""
    override = (os.environ.get("ADDISON_MODEL") or "").strip()
    ids = {model.id for model in models}
    if override:
        if override in ids:
            return [replace(model, default=(model.id == override)) for model in models]
        cleared = [replace(model, default=False) for model in models]
        cleared.append(
            CloudModel(
                id=override,
                label=override,
                description="",
                adaptive_thinking=False,
                effort_levels=(),
                default=True,
            )
        )
        return cleared
    default_id = _DEFAULT_MODEL_ID if _DEFAULT_MODEL_ID in ids else models[0].id
    return [replace(model, default=(model.id == default_id)) for model in models]
