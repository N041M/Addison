"""Curated cloud-model catalog — the explicit picker's menu (§4.1.1, §6.8).

This is the *cloud* half of the same "named model selection" substrate the LOCAL
role already has (router.py, item B). It ships as ONE constant — a short, curated
list — because the picker is for non-technical users (personas Mira 54, Petr 68):
every label and description is plain language, no jargon, no parameter counts, no
model-family branding leaking through (CLAUDE.md). The user picks a model and an
"answer style" (effort); Addison never picks for them (v1 is explicit-only — the
automatic task-based choice is v2, §4.1.1).

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


@dataclass(frozen=True)
class EffortLevel:
    """One "answer style" choice for a model. ``id`` is the value sent back as the
    ``effort`` param; ``label`` is what the user reads. Exactly one level per model
    (with any levels at all) is marked ``default`` so the picker can pre-select it."""

    id: str
    label: str
    default: bool = False


@dataclass(frozen=True)
class CloudModel:
    id: str                       # the API model id, e.g. "claude-opus-4-8"
    label: str                    # plain-language name shown in the picker
    description: str              # one plain sentence, no jargon
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


# Opus and Sonnet share the same three answer styles. "Balanced" is the default —
# a sensible middle for everyday use (design-doc §5: don't make the user think).
_STANDARD_EFFORT: tuple[EffortLevel, ...] = (
    EffortLevel("low", "Quick"),
    EffortLevel("high", "Balanced", default=True),
    EffortLevel("xhigh", "Thorough"),
)


# The single curated catalog. Order is the display order; exactly one entry is the
# default (opus, the shipped default model). ADDISON_MODEL can move the default at
# runtime via ``load_cloud_catalog`` — the same dev/test knob main.py already reads.
CLOUD_MODELS: tuple[CloudModel, ...] = (
    CloudModel(
        id="claude-opus-4-8",
        label="Most capable",
        description="The strongest model — best for hard or important questions.",
        adaptive_thinking=True,
        effort_levels=_STANDARD_EFFORT,
        default=True,
    ),
    CloudModel(
        id="claude-sonnet-5",
        label="Balanced",
        description="Fast and smart — good for everyday questions.",
        adaptive_thinking=True,
        effort_levels=_STANDARD_EFFORT,
    ),
    CloudModel(
        id="claude-haiku-4-5",
        label="Fast",
        description="Quickest and cheapest — good for simple things.",
        adaptive_thinking=False,
        effort_levels=(),   # no effort control — sending output_config errors
    ),
)


def load_cloud_catalog(model_override: str | None = None) -> list[CloudModel]:
    """The curated catalog, with ``ADDISON_MODEL`` (if set) forced to be the default.

    ``ADDISON_MODEL`` is the same dev/test knob main.py reads for the shipped default
    model. When it names a catalog entry, that entry becomes the default (its effort
    levels and thinking flag preserved). When it names a model NOT in the catalog, a
    bare entry with no effort control is appended and made default — so a live test
    sweep can point at any model without editing this file. Otherwise the catalog's
    own default (opus) stands. Exactly one returned entry is always the default.

    ``model_override`` lets tests pass the value explicitly instead of via the env.
    """
    override = model_override if model_override is not None else os.environ.get("ADDISON_MODEL")
    override = (override or "").strip()

    if not override:
        return [replace(model) for model in CLOUD_MODELS]

    if any(model.id == override for model in CLOUD_MODELS):
        # A curated model: move the default flag onto it, leave everything else intact.
        return [replace(model, default=(model.id == override)) for model in CLOUD_MODELS]

    # Not curated: keep the catalog (defaults cleared) and append a bare default entry.
    catalog = [replace(model, default=False) for model in CLOUD_MODELS]
    catalog.append(
        CloudModel(
            id=override,
            label=override,
            description="A model set with ADDISON_MODEL.",
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
