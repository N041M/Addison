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
import re
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
    # Which connected provider this model belongs to (``anthropic`` | ``openai`` |
    # ``google`` | ``custom``). The picker shows models from every connected provider
    # together (§4.1.1); this is the attribution the frontend renders, and it is what
    # the router keys a by-name pick to the right provider instance.
    provider: str = "anthropic"
    # --- routing metadata (step 3, contract D2) --------------------------------
    # HAND-ASSIGNED on the curated static catalogs below (this table is the
    # maintainer, in code, beside the entries — the capture-scope discipline). LOWER
    # rank == stronger; it orders quality_first's tail and breaks cross-provider ties.
    # ``None`` means "unknown rank" and sorts directly BEHIND the chain head, ahead of
    # every ranked model (D2 unknown-rank rule) — a just-released model must never be
    # demoted below known-weak ones. ADDISON_MODEL injections and custom-endpoint
    # models carry ``None`` ([N-c]); live-fetched entries used to as well, and that is
    # the flat-rank bug fixed on 2026-08-12 — they now go through
    # ``quality_rank_for_live_id`` (exact curated -> family inference -> unranked).
    quality_rank: int | None = None
    # True only for a genuinely free tier. Every curated cloud model here is PAID, so
    # this stays False for them; Ollama locals (built as RoutingCandidates directly,
    # not CloudModels) are the free candidates in step 3. Drives the "Answered with a
    # free model" disclaimer (D5) — custom-endpoint models are excluded there by the
    # candidate builder, never by this flag.
    free: bool = False
    # Set when this provider has actually REFUSED this model — a `model_gone`
    # row in `provider_attempts`, i.e. observed, not inferred. Holds the reason
    # slug; the picker dims the row and the sentence explaining the class lives
    # once under the list rather than thirty times inside it.
    unavailable: str | None = None

    @property
    def supported_effort(self) -> tuple[str, ...]:
        """The effort ids this model accepts — what ``AnthropicProvider`` checks an
        incoming ``effort`` against before it sends ``output_config`` (empty = the
        model has no effort control, so effort is never sent)."""
        return tuple(level.id for level in self.effort_levels)

    def to_wire(self) -> dict:
        """The ``cloudModels`` entry shape the frontend renders (§4.1.1 contract):
        ``{id, label, description, effortLevels: [{id, label, default}], default,
        provider, providerLabel}``. ``effortLevels`` is empty when the model has no
        answer-style control; ``providerLabel`` is the plain provider name for the
        picker's attribution."""
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "effortLevels": [
                {"id": level.id, "label": level.label, "default": level.default}
                for level in self.effort_levels
            ],
            "default": self.default,
            "provider": self.provider,
            "providerLabel": provider_label(self.provider),
            # Absent when the model is fine, so an older frontend sees exactly the
            # payload it always saw — the `unavailable` widget idiom.
            **({"unavailable": self.unavailable} if self.unavailable else {}),
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
        quality_rank=10,
    ),
    CloudModel(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        description="",
        adaptive_thinking=True,
        effort_levels=_STANDARD_EFFORT,
        quality_rank=30,
    ),
    CloudModel(
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        description="",
        adaptive_thinking=False,
        effort_levels=(),   # no effort control — sending output_config errors
        quality_rank=60,
    ),
)


# ---------------------------------------------------------------------------
# Provider registry (multi-provider, owner decision 2026-07-18)
# ---------------------------------------------------------------------------
# The four provider ids the keychain, Store, and picker share. ``custom`` is an
# OpenAI-compatible server the user points at (their own LAN model host).
PROVIDER_IDS: tuple[str, ...] = ("anthropic", "openai", "google", "custom")

_PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "custom": "Your own server",
}


def provider_label(provider_id: str) -> str:
    """Plain-language name for a provider id (the picker's attribution, the API-keys
    card row title). Unknown ids fall back to the id itself rather than raising."""
    return _PROVIDER_LABELS.get(provider_id, provider_id)


# Static catalogs for the non-Anthropic cloud providers. Unlike Anthropic (whose
# list is fetched live, GET /v1/models), these are curated per-provider entries —
# real model ids, raw names, no editorial copy. None carry an effort control (the
# "answer style" is an Anthropic knob only; every other provider ignores effort).
# Each catalog's strongest entry is marked ``default``; the union-builder keeps at
# most one default across all connected providers.
#
# RANKS AGREE WITH THE FAMILY FORMULA below (``_infer_quality_rank``). That is a
# rule, not a coincidence: a curated rank that is BETTER than what the formula
# would give the same id sets a trap, because the *next* generation — which only
# the formula can rank — would then sort behind this hand-written entry forever.
# A curated rank may be WORSE than the formula's (gpt-4o and gpt-4o-mini are, on
# purpose: the formula cannot tell 4o from 4.1, and hand knowledge may).
OPENAI_CLOUD_MODELS: tuple[CloudModel, ...] = (
    CloudModel(
        id="gpt-5", label="GPT-5", description="", provider="openai",
        default=True, quality_rank=15,
    ),
    CloudModel(id="gpt-5-mini", label="GPT-5 mini", description="", provider="openai",
               quality_rank=45),
    CloudModel(id="o3", label="o3", description="", provider="openai", quality_rank=17),
    CloudModel(id="o4-mini", label="o4-mini", description="", provider="openai",
               quality_rank=46),
    CloudModel(id="gpt-4.1", label="GPT-4.1", description="", provider="openai",
               quality_rank=16),
    CloudModel(id="gpt-4.1-mini", label="GPT-4.1 mini", description="", provider="openai",
               quality_rank=46),
    CloudModel(id="gpt-4o", label="GPT-4o", description="", provider="openai", quality_rank=35),
    CloudModel(
        id="gpt-4o-mini", label="GPT-4o mini", description="", provider="openai", quality_rank=70,
    ),
)

GOOGLE_CLOUD_MODELS: tuple[CloudModel, ...] = (
    CloudModel(
        id="gemini-3-pro", label="Gemini 3 Pro", description="", provider="google",
        default=True, quality_rank=19,
    ),
    CloudModel(
        id="gemini-3-flash", label="Gemini 3 Flash", description="", provider="google",
        quality_rank=49,
    ),
    CloudModel(
        id="gemini-2.5-pro", label="Gemini 2.5 Pro", description="", provider="google",
        quality_rank=20,
    ),
    CloudModel(
        id="gemini-2.5-flash", label="Gemini 2.5 Flash", description="", provider="google",
        quality_rank=50,
    ),
    CloudModel(
        id="gemini-2.5-flash-lite", label="Gemini 2.5 Flash-Lite", description="",
        provider="google", quality_rank=64,
    ),
)


def static_catalog_for(provider_id: str) -> list[CloudModel]:
    """The curated static catalog for a non-Anthropic provider, or [] for one that
    has no static list (Anthropic fetches live; custom lists via GET /v1/models)."""
    if provider_id == "openai":
        return [replace(m) for m in OPENAI_CLOUD_MODELS]
    if provider_id == "google":
        return [replace(m) for m in GOOGLE_CLOUD_MODELS]
    return []


# Quality rank for a live model the curated table has never heard of. LOWER is
# better everywhere in this file, so this sits below every hand-ranked entry
# (Anthropic 10-60, OpenAI 15-70, Google 20-50): a model nobody has assessed is
# reachable by name and by an explicit custom chain, and is the last thing
# quality-first routing reaches for on its own.
_UNRANKED_QUALITY_RANK = 80
# Below even an unassessed model: a provider has refused this one, so automatic
# routing must exhaust everything else before trying it again.
_REFUSED_QUALITY_RANK = 99

# Model families a provider lists that cannot hold a conversation. Google's
# listing says which methods a model supports and is filtered on that; OpenAI's
# has no capability field at all, so without this a picker of chat models fills
# up with transcription, embedding and image endpoints.
#
# A DENYLIST, deliberately not an allowlist. An unrecognised id shows up and can
# be tried; an allowlist would hide the model that shipped this morning. Failing
# open costs one confusing row, failing closed costs the model somebody connected
# the key for — and being wrong in that direction is what this whole function
# exists to stop.
_NON_CHAT_PREFIXES: tuple[str, ...] = (
    "text-embedding", "text-moderation", "omni-moderation", "text-similarity",
    "whisper", "tts", "dall-e", "gpt-image", "sora", "babbage", "davinci", "ada",
    "curie", "embedding", "aqa", "imagen", "veo", "learnlm-embedding",
    # Google families that DECLARE generateContent and cannot hold a conversation.
    # Checked against a real key's listing on 2026-08-06, where 18 of 42 models
    # advertising the method were image, music, robotics or research endpoints:
    # ``supportedGenerationMethods`` says which METHOD a model answers, never what
    # the method is FOR, so it cannot be the only filter.
    "lyria", "nano-banana", "deep-research", "antigravity",
)
_NON_CHAT_SUBSTRINGS: tuple[str, ...] = (
    "-audio", "-realtime", "-transcribe", "-tts",
    # Same listing: `gemini-3-pro-image`, `gemini-2.5-computer-use-…`,
    # `gemini-robotics-er-…`. Substrings because the family sits mid-id.
    "-image", "computer-use", "robotics",
)


def is_chat_model_id(model_id: str) -> bool:
    """Could this listed model id hold a conversation? See ``_NON_CHAT_PREFIXES``
    for why this is a denylist and why that direction is the safe one."""
    lowered = model_id.lower()
    if lowered.startswith(_NON_CHAT_PREFIXES):
        return False
    return not any(part in lowered for part in _NON_CHAT_SUBSTRINGS)


# A pinned snapshot (`-001`) or a preview, when the thing it is a snapshot OR
# preview OF is also on the list. Both suffixes mean "another name for a model you
# can already see", and a picker that shows a model twice is asking somebody to
# choose between two identical things.
_PINNED_SNAPSHOT = re.compile(r"^(?P<base>.+)-\d{3}$")
_PREVIEW_OF = re.compile(r"^(?P<base>.+)-preview$")


def _is_redundant_alias(model_id: str, available: set[str]) -> bool:
    """Is this id just another name for one already in ``available``?

    STRUCTURAL, not curated — the rule reads the list against itself, so it needs
    no table anybody has to maintain and it keeps working the day Google renames
    everything. That distinction is why this exists at all: the non-chat filter
    beside it IS a maintained denylist, and it is one only because no field in the
    API says what a model is for.

    A suffix ALONE is never enough. `gemini-3-pro-preview` is the only Gemini 3
    Pro there is, so dropping it for looking provisional would remove the model
    rather than a duplicate of it — which is why the base has to be present before
    anything is dropped."""
    for pattern in (_PINNED_SNAPSHOT, _PREVIEW_OF):
        match = pattern.match(model_id)
        if match and match.group("base") in available:
            return True
    return False


# ---------------------------------------------------------------------------
# Rank resolution for a live id (the flat-rank fix, 2026-08-12)
# ---------------------------------------------------------------------------
# THE BUG THIS EXISTS FOR. Matching a live id against the curated tables was an
# EXACT string match, so `claude-sonnet-5-20260203`, `gemini-3-pro-preview` and
# every id newer than the tables all landed on ``_UNRANKED_QUALITY_RANK``. In
# practice that was nearly the whole picker: one rank, one tier, and quality-first
# / cost-first degraded to insertion order.
#
# Three steps, in this order, and each is strictly more speculative than the last:
#   1. EXACT curated id  -> the hand-assigned label AND rank.
#   2. NORMALIZED curated id (a dated/pinned/preview variant of a curated entry)
#      -> that entry's RANK ONLY. The label stays the raw id, because a provider
#      can list the base and the dated variant side by side and two rows reading
#      "Claude Sonnet 5" would be a picker asking somebody to choose between two
#      identical-looking things. Raw names is this file's rule anyway.
#   3. FAMILY inference -> a rank derived from a recognised family + size word.
#   ...and otherwise ``_UNRANKED_QUALITY_RANK``, exactly as before.
#
# CONSERVATIVE ON PURPOSE. A wrong confident rank is worse than no rank: the
# router's D2 rule already sorts unknown-rank models AHEAD of every ranked one, so
# an unrecognised model is *favoured*, never buried. Nothing below invents a family
# it does not recognise, and none of it can resurrect a curated id the provider did
# not list — every one of these functions is asked ABOUT AN ID THE PROVIDER LISTED
# and answers with a number, never with a model.

# Suffixes that mean "this is a particular build of a model named earlier in the
# id". The first two mirror ``_PINNED_SNAPSHOT`` / ``_PREVIEW_OF`` above (same
# reasoning, reused rather than re-invented); the date forms are what Anthropic and
# Google actually append (`-20260203`, `-10-2025`, `-2024-08-06`).
_VERSION_SUFFIXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"-\d{3}$"),              # pinned snapshot, cf. _PINNED_SNAPSHOT
    re.compile(r"-preview$"),            # cf. _PREVIEW_OF
    re.compile(r"-latest$"),
    re.compile(r"-\d{8}$"),              # claude-sonnet-5-20260203
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),  # gpt-4o-2024-08-06
    re.compile(r"-\d{2}-\d{4}$"),        # gemini-…-10-2025
)


def _normalized_id(model_id: str) -> str:
    """A live id with its build/date/preview suffixes stripped, so a dated variant
    can be recognised as the model it is a build of. Purely textual; it never
    consults any table, and it is only ever used to LOOK UP a rank."""
    out = model_id.lower()
    # Bounded loop: ids stack at most a couple of these (`…-preview-10-2025`), and
    # an unbounded while over regexes on provider-supplied text is not worth it.
    for _ in range(3):
        for pattern in _VERSION_SUFFIXES:
            stripped = pattern.sub("", out)
            if stripped != out and stripped:
                out = stripped
                break
        else:
            break
    return out


def curated_entries(provider_id: str) -> tuple[CloudModel, ...]:
    """The curated table for a provider — labels and ranks ONLY.

    Separate from ``static_catalog_for`` on purpose: that one is the *registerable*
    catalog and returns nothing for Anthropic, because registering ids Anthropic
    did not list is the 404 bug (`tests/test_live_model_registration.py`). This one
    is the metadata table, so it includes the Anthropic entries — a live Anthropic
    id gets its hand-assigned rank, and an id Anthropic never listed still cannot
    reach the picker through it."""
    if provider_id == "anthropic":
        return FALLBACK_CLOUD_MODELS
    if provider_id == "openai":
        return OPENAI_CLOUD_MODELS
    if provider_id == "google":
        return GOOGLE_CLOUD_MODELS
    return ()


# --- family inference (step 3) ---------------------------------------------
# Each entry is (size words, base rank). LOWER is stronger, as everywhere here.
# The final rank is ``base - min(major_version, 9)``, so a later generation of the
# same size sorts ahead of an earlier one (gemini-3-pro 19 < gemini-2.5-pro 20)
# while a size boundary is never crossed by version alone (any pro beats any
# flash). The bases are chosen so a family-inferred rank lands beside the curated
# entry for the same class rather than leapfrogging a whole tier.
#
# Size words are checked IN ORDER, most specific first: "flash-lite" contains
# "flash", and reading it as a flash would rank a Lite model a whole tier too high.
_ANTHROPIC_TIERS: tuple[tuple[str, int], ...] = (
    ("opus", 14), ("sonnet", 34), ("haiku", 64),
)
_GOOGLE_SIZES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("flash-lite", "-lite", "nano"), 66),
    (("flash",), 52),
    (("ultra", "pro"), 22),
)
_OPENAI_SIZES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("nano",), 62),
    (("mini", "small"), 50),
)
_OPENAI_BASE = 20          # a plain gpt-N / o-N with no size word
_MAX_VERSION_BONUS = 9     # bounded, so a "gpt-77" can never rank below zero

_ANTHROPIC_MAJOR = re.compile(r"^claude-(?:[a-z]+-)*?(\d+)")
_GEMINI_MAJOR = re.compile(r"^gemini-(\d+)")
_GPT_MAJOR = re.compile(r"^gpt-(\d+)")
_O_SERIES_MAJOR = re.compile(r"^o(\d+)(?:-|$)")


def _version_bonus(match: re.Match[str] | None) -> int:
    """How much a generation number improves a family rank — capped, and 0 when the
    id carries no number at all (`gemini-flash-latest`)."""
    if match is None:
        return 0
    try:
        return min(int(match.group(1)), _MAX_VERSION_BONUS)
    except ValueError:   # pragma: no cover - the group is \d+
        return 0


def _size_base(model_id: str, sizes: tuple[tuple[tuple[str, ...], int], ...]) -> int | None:
    """The base rank for the first size word this id carries, or None when it
    carries none of them."""
    for words, base in sizes:
        if any(word in model_id for word in words):
            return base
    return None


def _infer_quality_rank(model_id: str) -> int | None:
    """A rank inferred from a RECOGNISED family + size, or None.

    ID-BASED, not provider-based: the family lives in the name, and the same names
    turn up behind an OpenAI-compatible proxy. Returning None is always allowed and
    is what an unrecognised name gets — see the conservatism note above."""
    mid = model_id.lower()

    if mid.startswith("claude"):
        # Anthropic names the tier outright, which is the whole reason this family
        # is worth inferring at all. Both orderings occur: `claude-sonnet-5` and
        # the older `claude-3-5-sonnet-…`, so the tier word is searched anywhere
        # and the first number after "claude-" is the generation.
        for tier, base in _ANTHROPIC_TIERS:
            if tier in mid:
                return base - _version_bonus(_ANTHROPIC_MAJOR.search(mid))
        return None

    if mid.startswith("gemini"):
        base = _size_base(mid, _GOOGLE_SIZES)
        if base is None:
            return None
        return base - _version_bonus(_GEMINI_MAJOR.search(mid))

    if mid.startswith("gpt-") or _O_SERIES_MAJOR.match(mid):
        # OpenAI's names say size ("mini"/"nano") far more reliably than they say
        # strength — gpt-4.1 and gpt-4o are the same generation and not the same
        # model — so a plain id gets ONE base and the tie is broken by insertion
        # order, which is the honest answer.
        base = _size_base(mid, _OPENAI_SIZES)
        if base is None:
            base = _OPENAI_BASE
        major = _GPT_MAJOR.search(mid) or _O_SERIES_MAJOR.match(mid)
        return base - _version_bonus(major)

    return None


def quality_rank_for_live_id(provider_id: str, model_id: str) -> int:
    """The rank for an id THE PROVIDER LISTED: exact curated, else the curated entry
    this is a dated/pinned/preview build of, else family inference, else unranked.

    Never returns None: an id that reaches here exists, and ``_UNRANKED_QUALITY_RANK``
    is the "nobody has assessed this" answer the picker has always used for it."""
    curated = curated_entries(provider_id)
    for entry in curated:
        if entry.id == model_id:
            return entry.quality_rank if entry.quality_rank is not None else _UNRANKED_QUALITY_RANK
    normalized = _normalized_id(model_id)
    for entry in curated:
        if _normalized_id(entry.id) == normalized and entry.quality_rank is not None:
            return entry.quality_rank
    inferred = _infer_quality_rank(model_id)
    return inferred if inferred is not None else _UNRANKED_QUALITY_RANK


def catalog_from_live_ids(provider_id: str, ids: list[str]) -> list[CloudModel]:
    """The picker's entries for a provider, built from THE IDS IT ACTUALLY SERVES.

    This is the fix for a bug that made a whole provider unusable while reporting
    itself connected (2026-08-06). Registration used to call the provider's list
    endpoint purely to validate the key, DISCARD the reply, and register the
    hardcoded ids in the curated table above. `provider.connect` therefore proved
    only that listing works — never that the ids it was about to register exist —
    so when Google's real ids drifted from ``gemini-2.5-pro``/``gemini-2.5-flash``,
    the picker offered two models, the Connections panel said "connected", and
    every single message came back ``404``. The authoritative list was fetched and
    thrown away one line earlier.

    So the live list decides WHICH models exist, and the curated table is demoted
    to what it is genuinely good for: a display name and a hand-assigned
    ``quality_rank`` for the ids it recognises. Curation can no longer outvote the
    provider about what is real.

    Order is curated-first (hand-ranked, best first) and then everything else the
    provider serves, so a familiar list stays familiar and new models land at the
    end rather than shuffling it. An id the provider does not list is simply
    absent — including a curated one, which is exactly the case that used to 404.
    """
    chat = [i for i in ids if isinstance(i, str) and i and is_chat_model_id(i)]
    # Deduped against the CHAT set, not the raw one: a base that was itself
    # filtered out (an image model, say) must not silently take its alias with it.
    chat_ids = set(chat)
    live = [i for i in chat if not _is_redundant_alias(i, chat_ids)]
    live_ids = set(live)
    out: list[CloudModel] = []
    seen: set[str] = set()
    for entry in static_catalog_for(provider_id):
        if entry.id in live_ids:
            out.append(entry)
            seen.add(entry.id)
    for model_id in live:
        if model_id in seen:
            continue
        seen.add(model_id)
        # Raw id as the label: this file's rule is real names, never invented
        # copy, and a prettifier would be inventing one. The RANK, unlike the
        # label, can be improved for an id the curated table half-recognises —
        # see quality_rank_for_live_id (2026-08-12: without it every id newer
        # than the table shared one rank and routing had one tier).
        out.append(
            CloudModel(
                id=model_id,
                label=model_id,
                description="",
                provider=provider_id,
                quality_rank=quality_rank_for_live_id(provider_id, model_id),
            )
        )
    return out


def mark_refused(models: list[CloudModel], refused: set[str]) -> list[CloudModel]:
    """Mark the models this provider has actually refused, and sink them.

    EVIDENCE, NOT INFERENCE — `refused` comes from `provider_attempts`, where a
    `model_gone` row means the provider answered 404 for that exact id. Every other
    filter in this file reasons about what a model probably is; this one reports
    what happened. Google listed `gemini-2.5-flash` advertising the right method
    and then refused it as "no longer available to new users", and nothing on the
    row could have shown that until the log existed.

    MARKED, NEVER REMOVED. A refusal could have been a bad afternoon, and a model
    that quietly vanished from the picker would be a worse mystery than one that
    is visibly out of order. It stays pickable — the person may know something the
    log does not.

    Sunk to the END OF ITS OWN PROVIDER RUN rather than the end of the list, so the
    picker's grouping survives; the fold then hides it by default, which is the
    whole reason marking and sinking are one change and not two. Its quality rank
    goes to last-resort in the same move: leaving a known-refused model where
    automatic routing can reach for it is how an evening gets spent."""
    if not refused:
        return models
    out: list[CloudModel] = []
    i = 0
    while i < len(models):
        provider = models[i].provider
        end = i
        while end < len(models) and models[end].provider == provider:
            end += 1
        run = models[i:end]
        ok = [m for m in run if m.id not in refused]
        bad = [
            replace(m, unavailable="model_gone", quality_rank=_REFUSED_QUALITY_RANK)
            for m in run
            if m.id in refused
        ]
        out.extend(ok + bad)
        i = end
    return out


def merge_catalogs(catalogs: list[list[CloudModel]]) -> list[CloudModel]:
    """Union several providers' catalogs into the single picker menu, keeping at
    most ONE default across the whole list (the first default seen wins; later
    providers' defaults are cleared). De-dupes by model id (first occurrence wins)
    so the same id can't appear twice if two providers ever share one."""
    merged: list[CloudModel] = []
    seen_ids: set[str] = set()
    have_default = False
    for catalog in catalogs:
        for model in catalog:
            if model.id in seen_ids:
                continue
            seen_ids.add(model.id)
            if model.default and have_default:
                model = replace(model, default=False)
            if model.default:
                have_default = True
            merged.append(model)
    return merged


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
    """The default entry (exactly one is marked ``default``); the first entry is the
    fallback when a caller hands over a catalog with none marked.

    An EMPTY catalog raises ``ValueError``, not ``IndexError``. The old docstring
    called the fallback "safe" while ``catalog[0]`` raised on empty — safe for the
    none-marked case it was describing, and not for the one it wasn't. No caller
    can reach it today (``rpc/models`` returns None on an empty catalog first,
    ``main`` passes the built-in fallback, ``_maybe_load_live_catalog`` drops an
    empty fetch), so this is about the NEXT caller: an empty live catalog should
    say what went wrong, not surface as a list-index error three frames away."""
    for model in catalog:
        if model.default:
            return model
    if not catalog:
        raise ValueError("The model catalog is empty, so there is no default model.")
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
    key = key.strip() if key else ""
    if not key:
        raise CatalogFetchError("No API key available for the model list.")
    if not key.isascii() or not key.isprintable():
        # A key with header-illegal characters must degrade to the built-in
        # list like every other fetch failure, not crash header encoding.
        raise CatalogFetchError("The API key has a stray character in it.")
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
        # The live list still decides WHICH Anthropic models exist — this only
        # ranks one it just listed. Before 2026-08-12 every live Anthropic entry
        # carried rank None, so Opus, Sonnet and Haiku were one undifferentiated
        # bucket to quality-first routing.
        quality_rank=quality_rank_for_live_id("anthropic", model_id),
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
