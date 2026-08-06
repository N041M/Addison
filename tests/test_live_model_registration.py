"""A registered model id is one the provider actually serves (2026-08-06).

THE BUG THIS EXISTS FOR. `_connect_provider` used to call the provider's list
endpoint purely to validate the key, throw the reply away, and register the
hardcoded ids from the curated catalog in `models_catalog.py`:

    google_list_models(getter)              # validates the key
    models = static_catalog_for("google")   # ...and then ignores what it said

So `provider.connect` proved that LISTING worked. It never proved that the ids it
was about to register EXIST. The moment Google's real ids drifted from
``gemini-2.5-pro`` / ``gemini-2.5-flash``, the result was a provider that reported
itself connected, offered two models in the picker, and answered every single
message with ``404`` — with the correct list fetched and discarded one line above.

WHY IT SHIPPED. Nothing asserted the relationship between "what the provider
lists" and "what gets registered". Every test covered one side or the other: the
list call had tests, the catalog had tests, the registration in between had none.
The whole suite passed on the day Gemini was unusable, and passed again after the
fix. That gap is what the first test here closes — it is the only assertion in the
file that could have caught the original defect, and the others exist to keep its
supporting parts honest.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_core import main
from agent_core.models_catalog import (
    GOOGLE_CLOUD_MODELS,
    catalog_from_live_ids,
    is_chat_model_id,
    static_catalog_for,
)
from agent_core.providers import google_provider


# --- the regression: registration may only offer what the provider listed -----


def test_only_what_the_provider_listed_can_be_registered():
    """The assertion whose absence let the 404 ship.

    The live list here deliberately contains NEITHER curated id — exactly the
    drift that broke Gemini. Under the old code the picker got Pro and Flash and
    registered two models that answer 404; it must now hold what Google said and
    nothing else."""
    listed = ["gemini-3-pro-preview", "gemini-3-flash"]

    registered = [m.id for m in catalog_from_live_ids("google", listed)]

    assert registered == listed
    for curated in (m.id for m in GOOGLE_CLOUD_MODELS):
        assert curated not in registered, (
            f"{curated} was registered although Google never listed it — this is the "
            "404 bug: the curated table cannot outvote the provider about what exists"
        )


def test_registration_feeds_the_list_call_into_the_catalog_rather_than_discarding_it():
    """A SOURCE-level check, and it needs to be one.

    `_connect_provider` is nested inside the server's construction, so the wiring
    that actually broke — a list call whose result goes nowhere — cannot be
    reached from a unit test. The behavioural tests above prove
    `catalog_from_live_ids` is right; only this proves registration still ASKS it.
    Same instinct as `test_no_snapshot_query_filters_on_created_in_mode`: read the
    source, because the mistake is a line that can be quietly rewritten and the
    behaviour it breaks has no other witness.

    The original defect is literally `google_list_models(getter)` on a line of its
    own — a call whose value is thrown away — so a bare statement call to either
    lister is what this refuses."""
    source = Path(main.__file__).read_text(encoding="utf-8")

    for lister in ("google_list_models", "openai_list_models"):
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{lister}(") and "catalog_from_live_ids" not in stripped:
                raise AssertionError(
                    f"{lister} is called as a bare statement in main.py — its reply is "
                    "being discarded, which is the 404 bug. Pass it to "
                    f"catalog_from_live_ids: {stripped!r}"
                )

    assert "static_catalog_for" not in source, (
        "main.py registers from the curated catalog again — that table may supply "
        "labels and ranks, never the set of models a provider is claimed to serve"
    )


def test_a_curated_model_the_provider_still_serves_keeps_its_label_and_rank():
    """The other half: curation is DEMOTED, not deleted.

    A live id the table knows keeps its display name and its hand-assigned
    quality_rank, which routing reads. Registering the raw id for everything would
    have fixed the 404 and silently flattened quality-first routing."""
    curated = static_catalog_for("google")[0]
    models = catalog_from_live_ids("google", [curated.id, "gemini-9-experimental"])

    assert models[0].id == curated.id
    assert models[0].label == curated.label
    assert models[0].quality_rank == curated.quality_rank
    # ...and curated entries come FIRST, so a familiar picker stays familiar and a
    # newly-released model lands at the end rather than reshuffling the list.
    assert models[1].id == "gemini-9-experimental"


def test_an_unknown_live_model_is_offered_last_under_its_own_name():
    """A model nobody has assessed is still usable — by name, and by an explicit
    custom chain — but quality-first must not reach for it over a ranked one, so
    its rank sorts below every hand-assigned entry. Its label is the raw id: this
    catalog's rule is real names, and a prettifier would be inventing copy."""
    unknown = catalog_from_live_ids("google", ["gemini-9-experimental"])[0]

    assert unknown.label == "gemini-9-experimental"
    assert unknown.description == ""
    assert unknown.provider == "google"
    ranked = [m.quality_rank for m in GOOGLE_CLOUD_MODELS if m.quality_rank is not None]
    assert unknown.quality_rank is not None
    assert unknown.quality_rank > max(ranked)


def test_an_empty_live_list_registers_nothing_rather_than_the_old_hardcoded_pair():
    """The tempting "fallback" is the bug wearing a hat. A provider that lists no
    usable model contributes none; falling back to ids it did not list is how the
    404 reached a person in the first place."""
    assert catalog_from_live_ids("google", []) == []
    assert catalog_from_live_ids("openai", []) == []


# --- what counts as a model you can talk to ----------------------------------


def test_google_listing_keeps_only_models_that_support_generateContent():
    """Google SAYS what each model does, so the filter asks instead of guessing —
    its listing carries embedding and image models that error at
    ``:generateContent``."""
    body = {
        "models": [
            {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/aqa", "supportedGenerationMethods": ["generateAnswer"]},
            # No capability field at all: KEPT. The field is the filter, and its
            # ABSENCE must never quietly empty somebody's picker.
            {"name": "models/gemini-3-flash"},
        ]
    }
    ids = google_provider._ids_from_models_payload(body)
    assert ids == ["gemini-3-pro", "gemini-3-flash"]


def test_non_chat_families_are_denied_by_name_where_the_api_will_not_say():
    """OpenAI's listing has no capability field, so this is a denylist — and it
    fails OPEN on purpose: an unrecognised id shows up and can be tried, where an
    allowlist would hide the model released this morning."""
    for chat in ("gpt-4.1", "o4-mini", "gemini-3-pro", "some-model-nobody-knows"):
        assert is_chat_model_id(chat) is True, chat
    for other in (
        "text-embedding-3-large", "whisper-1", "tts-1", "dall-e-3",
        "omni-moderation-latest", "gpt-4o-realtime-preview", "gpt-4o-audio-preview",
        "babbage-002", "imagen-3.0-generate-001",
    ):
        assert is_chat_model_id(other) is False, other


def test_googles_own_method_list_is_not_enough_on_its_own():
    """Every id here was returned by a REAL key on 2026-08-06 declaring
    ``generateContent``, and not one of them can hold a conversation.

    That is the whole argument for keeping a name denylist alongside the
    capability field: ``supportedGenerationMethods`` says which METHOD a model
    answers, never what the method is FOR. Trusting it alone put eighteen image,
    music, robotics and research endpoints into the picker — models a person can
    select and an automatic routing strategy can reach for."""
    for declared_but_not_chat in (
        "gemini-2.5-flash-image", "gemini-3-pro-image-preview", "nano-banana-pro-preview",
        "gemini-3.1-flash-lite-image", "lyria-3-clip-preview", "lyria-3-pro-preview",
        "gemini-robotics-er-2-preview", "gemini-2.5-computer-use-preview-10-2025",
        "antigravity-preview-05-2026", "deep-research-pro-preview-12-2025",
        "gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview",
    ):
        assert is_chat_model_id(declared_but_not_chat) is False, declared_but_not_chat

    # ...and the real chat models from that same listing survive, including the
    # ones whose names sit close to a denied family.
    for chat in (
        "gemini-2.5-pro", "gemini-2.0-flash", "gemini-flash-latest",
        "gemini-3.1-pro-preview-customtools", "gemma-4-31b-it", "gemini-3.6-flash",
    ):
        assert is_chat_model_id(chat) is True, chat


# --- the migration that a "log" needs in order to keep logging ---------------


def test_a_database_that_predates_server_detail_gains_it_and_keeps_recording(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` does NOTHING to a table that already exists.

    `provider_attempts` shipped, recorded one real 404, and gained `server_detail`
    hours later. On every database that saw the first version the column never
    appeared — and `insert_provider_attempt` then raised "no such column" straight
    into the orchestrator's best-effort `except`, which drops the row without a
    word. The table stayed frozen at one row while failures kept happening, and an
    empty log reads as "nothing went wrong".

    So this asserts the INSERT, not the column: `PRAGMA table_info` passing proves
    the migration ran, and only a successful write proves it ran far enough."""
    from agent_core.memory.store import Store

    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE provider_attempts ("
        " id TEXT PRIMARY KEY, conversation_id TEXT, provider TEXT NOT NULL,"
        " model TEXT NOT NULL, outcome TEXT NOT NULL, status_code INTEGER,"
        " detail TEXT, created_at INTEGER NOT NULL)"
    )
    conn.commit()
    conn.close()

    store = Store(db)
    try:
        store.insert_provider_attempt(
            id="a1", conversation_id=None, provider="google", model="gemini-2.5-flash",
            outcome="rejected", status_code=404, detail="plain sentence",
            server_detail="models/... is not found for API version v1beta",
            created_at=1,
        )
        rows = store.list_provider_attempts()
        assert rows[0]["server_detail"].startswith("models/")
    finally:
        store.close()


# --- one model, two names ----------------------------------------------------


def test_a_pinned_snapshot_and_a_preview_are_dropped_when_their_base_is_listed():
    """Google lists `gemini-2.0-flash` AND `gemini-2.0-flash-001`, and
    `gemini-3.1-flash-lite` AND `-lite-preview`. Those are one model under two
    names, and a picker that offers both asks somebody to choose between identical
    things.

    STRUCTURAL, not curated: the rule reads the list against itself, so it needs no
    table anyone maintains and survives a wholesale renaming. That is the
    difference between this and the non-chat denylist beside it, which is a
    maintained list only because no field in the API says what a model is for."""
    ids = [
        "gemini-2.0-flash", "gemini-2.0-flash-001",
        "gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview",
    ]
    assert [m.id for m in catalog_from_live_ids("google", ids)] == [
        "gemini-2.0-flash", "gemini-3.1-flash-lite",
    ]


def test_a_suffix_alone_never_drops_a_model():
    """`gemini-3-pro-preview` is the only Gemini 3 Pro there is. Dropping it for
    looking provisional would remove the model rather than a duplicate of it, so
    the base has to be PRESENT before anything goes."""
    ids = ["gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.0-flash-001"]
    assert [m.id for m in catalog_from_live_ids("google", ids)] == ids


def test_an_alias_survives_a_base_that_was_filtered_out_for_another_reason():
    """Dedup runs against the CHAT set, not the raw listing. `gemini-3-pro-image`
    is denied as a non-chat model; its `-preview` sibling is denied by the same
    rule — but if a base is ever removed for a reason of its own, it must not take
    an otherwise-usable alias with it."""
    kept = [m.id for m in catalog_from_live_ids("google", ["gemini-9-flash-001"])]
    assert kept == ["gemini-9-flash-001"], "no base present, so nothing to be a duplicate of"


# --- the only filter built on what HAPPENED ----------------------------------


def test_a_refused_model_is_marked_and_sunk_but_never_removed():
    """Evidence, not inference. `refused` comes from `provider_attempts`, where a
    `model_gone` row means the provider answered 404 for that exact id — Google
    listing `gemini-2.5-flash` and then calling it "no longer available to new
    users". Every other filter reasons about what a model probably is; this one
    reports what happened.

    MARKED, NEVER REMOVED: a refusal could have been a bad afternoon, and a model
    that quietly vanished would be a worse mystery than one visibly out of order.

    Sunk to the end of ITS OWN provider run, not the end of the list — the picker
    groups by company, and a model exiled to the bottom of everything would leave
    its heading behind."""
    from agent_core.models_catalog import CloudModel, mark_refused

    models = [
        CloudModel(id="a1", label="A1", description="", provider="anthropic"),
        CloudModel(id="g1", label="G1", description="", provider="google"),
        CloudModel(id="dead", label="Dead", description="", provider="google", quality_rank=20),
        CloudModel(id="g2", label="G2", description="", provider="google"),
    ]
    out = mark_refused(models, {"dead"})

    assert [m.id for m in out] == ["a1", "g1", "g2", "dead"]
    assert out[-1].unavailable == "model_gone"
    # ...and automatic routing must exhaust everything else first. Leaving a
    # known-refused model where cost-first can reach for it is how the evening of
    # 2026-08-06 was spent.
    sunk_rank = out[-1].quality_rank
    assert sunk_rank is not None
    assert sunk_rank > max(m.quality_rank or 0 for m in out[:-1])
    # The wire carries it only when it is true, so an older frontend sees the
    # payload it always saw.
    assert "unavailable" not in out[0].to_wire()
    assert out[-1].to_wire()["unavailable"] == "model_gone"


def test_nothing_refused_means_nothing_changes():
    """A missing or empty log is "nothing is KNOWN to be refused" — never a claim
    that everything works, and never a reordering."""
    from agent_core.models_catalog import CloudModel, mark_refused

    models = [CloudModel(id="a", label="A", description="", provider="anthropic")]
    assert mark_refused(models, set()) == models
