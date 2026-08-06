"""The shipped prompts tell the model what Addison can and cannot build. This is the
gate that keeps those sentences true.

**Why this one ranks above the other drift gates.** Every other stale document in
this repo costs an *agent* a wrong turn. A stale capability claim in
`agent_core/providers/prompts/primary.txt` costs the *user*: Addison declines a
feature it has, in its own voice, and the person believes it. That happened —
`primary.txt` told the model Addison could not build a to-do list months after it
could, and then again hours after checklists shipped. Nothing failed, because
nothing was watching the prompt.

The prompt is prose about code. So it is checked against the code that actually
decides the answer: `agent_core/widgets.py`'s closed kind list, its stat-source
whitelist, and the tool registry.

## Adding a widget kind

`test_every_widget_kind_is_accounted_for` fails first and tells you to add a row to
`_SAMPLES`. Then `test_the_prompt_describes_every_kind_a_simple_user_can_build`
tells you to describe it in `primary.txt` — or, for an OPEN-only kind, to keep it
out of the Simple prompt. That order is deliberate: the decision *"which profile
surfaces this"* gets forced before the wording is.
"""

from __future__ import annotations

import re

from agent_core.main import build_registry, load_primary_prompt, load_setup_prompt
from agent_core.policy import PolicyMode
from agent_core.widgets import STAT_SOURCES, WIDGET_KINDS, validate_widget_spec

# One minimal VALID spec per kind, so SAFE-legality is asked of `validate_widget_spec`
# rather than restated here. A second hard-coded list of "the SAFE kinds" would be a
# second risk model, which is the thing SAFE invariant 4 forbids.
_SAMPLES: dict[str, dict] = {
    "routine": {"kind": "routine", "routineId": "abc123", "title": "Morning"},
    "stat": {"kind": "stat", "source": "tokens_month", "title": "Tokens"},
    "checklist": {"kind": "checklist", "items": ["Buy milk"], "title": "Shopping"},
    "note": {"kind": "note", "text": "hello", "title": "Note"},
    "timer": {"kind": "timer", "seconds": 300, "title": "Tea"},
    "command": {"kind": "command", "command": "ls", "title": "List"},
}

# What `primary.txt` must SAY about each kind, as a pattern over the shipped text.
# Deliberately the words a person would recognise, not the internal kind name: the
# prompt is read by a model that speaks to Mira and Petr, and "a `checklist` widget"
# is not a sentence either of them would hear.
_DESCRIBED_IN_PROMPT: dict[str, str] = {
    "routine": r"runs a routine",
    "stat": r"shows a number",
    "checklist": r"\bchecklist\b",
    "note": r"\bnote\b",
    "timer": r"\btimer\b",
    # OPEN-only. The Simple prompt must NOT promise it — see the test below.
    "command": r"shell command|run a command|command widget",
}

# Each stat source, in the words the prompt uses for it.
_STAT_SOURCE_IN_PROMPT: dict[str, str] = {
    "tokens_month": r"tokens used this month",
    "provider_latency": r"how fast the models reply",
    "connections": r"what's connected",
}


def _safe_kinds() -> set[str]:
    return {
        k
        for k, spec in _SAMPLES.items()
        if validate_widget_spec(spec, PolicyMode.SAFE) is None
    }


def _what_a_widget_can_be(prompt: str) -> str:
    """The one sentence in `primary.txt` that ENUMERATES what a widget can be.

    Scoped this tightly on purpose, and the reason is a mutation that survived a
    looser version of this test. Searching the whole file — or even the whole widget
    bullet — passes while the enumeration has quietly lost a kind, because the word
    survives in the honest-limits sentence a few clauses later. The enumeration is
    what the model reads to decide whether to say *"the app can't make that"*, so it
    is the text that has to be complete.
    """
    match = re.search(r"A widget does one of[^.]*\.", prompt)
    assert match, (
        "primary.txt no longer contains the sentence enumerating what a widget can "
        "be ('A widget does one of ...'). That sentence is what makes Addison offer "
        "or decline a widget, so re-point this helper at whatever replaced it — do "
        "NOT widen the search to the whole file, which is what let a missing kind "
        "through before."
    )
    return match.group(0)


def test_every_widget_kind_is_accounted_for():
    """The forcing function. A kind added to `WIDGET_KINDS` with no sample here means
    nobody decided whether the Simple prompt should describe it — and the prompt is
    where that decision becomes visible to the person.

    Fails on removal too: a stale sample would keep the prompt gate below asserting
    something about a kind that no longer exists.
    """
    assert set(_SAMPLES) == set(WIDGET_KINDS), (
        "agent_core/widgets.py's WIDGET_KINDS and this file's _SAMPLES disagree.\n"
        f"  only in WIDGET_KINDS: {sorted(set(WIDGET_KINDS) - set(_SAMPLES))}\n"
        f"  only in _SAMPLES:     {sorted(set(_SAMPLES) - set(WIDGET_KINDS))}\n"
        "Add a minimal valid spec for each new kind, then decide whether the Simple "
        "prompt (agent_core/providers/prompts/primary.txt) should describe it."
    )
    assert set(_DESCRIBED_IN_PROMPT) == set(WIDGET_KINDS), (
        "every kind needs a phrase this test can look for in primary.txt: "
        f"{sorted(set(WIDGET_KINDS) ^ set(_DESCRIBED_IN_PROMPT))}"
    )


def test_the_prompt_describes_every_kind_a_simple_user_can_build():
    """The drift with a user-visible cost. When `primary.txt` does not know about a
    kind, Addison says the app cannot make one — and the person, who has no way to
    check, believes it and goes without a feature that shipped.

    SAFE-legality is asked of `validate_widget_spec`, so this cannot pass by agreeing
    with a stale copy of the rules.
    """
    enumeration = _what_a_widget_can_be(load_primary_prompt())
    missing = [
        kind
        for kind in sorted(_safe_kinds())
        if not re.search(_DESCRIBED_IN_PROMPT[kind], enumeration, re.I)
    ]
    assert not missing, (
        f"agent_core/widgets.py accepts {missing} in SAFE mode and the sentence in "
        "agent_core/providers/prompts/primary.txt that enumerates what a widget can be "
        "does not offer them. Addison will tell the person it cannot build one. Add "
        f"each to that sentence — {enumeration[:120]}… — in plain words, and put its "
        "honest limits in the bullet beneath."
    )


def test_the_simple_prompt_never_promises_a_developer_only_widget():
    """The other direction, and the cheaper failure only because it is louder: the
    Simple prompt offering a `command` widget would have Addison propose something
    the save path refuses, in a profile whose whole promise is that nothing surprises
    you. `CLAUDE.md`: do not leak developer affordances into Simple.
    """
    prompt = load_primary_prompt()
    open_only = sorted(set(WIDGET_KINDS) - _safe_kinds())
    assert open_only, "no OPEN-only widget kind found — did the mode gate move?"
    # Whole file, not just the widget bullet: the positive claims are checked where
    # the person is TOLD what widgets are, but a leak anywhere is still a leak.
    leaked = [k for k in open_only if re.search(_DESCRIBED_IN_PROMPT[k], prompt, re.I)]
    assert not leaked, (
        f"primary.txt describes {leaked}, which validate_widget_spec REFUSES in SAFE "
        "mode. Simple would be offered a widget it cannot save. Remove the mention."
    )


def test_the_prompt_names_every_stat_source():
    """`STAT_SOURCES` is a whitelist, so a source added there and not here is a
    readout the person is never told exists — the same silent-omission shape as a
    missing kind, one layer down.
    """
    prompt = load_primary_prompt()
    assert set(_STAT_SOURCE_IN_PROMPT) == set(STAT_SOURCES), (
        "agent_core/widgets.py's STAT_SOURCES changed; add the phrase primary.txt "
        f"should use for it: {sorted(set(STAT_SOURCES) ^ set(_STAT_SOURCE_IN_PROMPT))}"
    )
    enumeration = _what_a_widget_can_be(prompt)
    missing = [
        source
        for source in sorted(STAT_SOURCES)
        if not re.search(_STAT_SOURCE_IN_PROMPT[source], enumeration, re.I)
    ]
    assert not missing, (
        f"primary.txt's widget sentence lists the numbers Addison keeps track of and "
        f"omits {missing}. Add it there — that clause is the only place the person is "
        "told the readout exists."
    )


def test_the_prompt_is_right_that_no_tool_makes_a_widget():
    """`primary.txt` states flatly: *"There is no tool that makes a widget"*, and the
    model is meant to act on it — it is the sentence that stops Addison saving an
    HTML file as a stand-in.

    A future `create_widget` tool would falsify it silently, because a prompt cannot
    notice the registry changing under it.
    """
    prompt = load_primary_prompt()
    assert "no tool that makes a widget" in prompt, (
        "the sentence this test guards has been reworded in primary.txt — re-point "
        "the test at the new wording, or drop the claim if it is no longer true."
    )
    registry = build_registry()
    # A tool that MAKES one, not a tool that merely mentions them: `snapshot_now`
    # says it captures "widgets and routines", which is true and not the claim.
    # The id is a substring test on purpose — `create_widget` has no word boundary
    # before "widget", and \b silently let exactly that name through.
    creates = re.compile(r"\b(?:creat|mak|add|build|pin|new)\w*\s+(?:a\s+|the\s+)?widget", re.I)
    for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
        offenders = [
            d.id
            for d in registry.visible_tools(mode)
            if "widget" in d.id.lower()
            or creates.search(d.label)
            or creates.search(d.description)
        ]
        assert not offenders, (
            f"primary.txt promises no tool makes a widget, but {mode.name} exposes "
            f"{offenders}. Amend the prompt in the SAME commit that adds the tool — "
            "a prompt that under-claims makes Addison decline what it can do."
        )


def test_the_prompt_is_right_that_nothing_runs_by_itself():
    """*"The app cannot schedule anything, run routines by itself, or send emails or
    messages anywhere."* That is G2 (`CLAUDE.md`, floors) restated to the model, and
    it is the claim step 8 will make false — the keyword gate lands with
    author-OS-run automation, and this prompt sentence has to change in that commit.

    `tests/test_g2_no_self_trigger.py` owns the floor itself; this owns only the
    sentence, so the two cannot disagree without one of them going red.
    """
    prompt = load_primary_prompt()
    assert "cannot schedule anything, run routines by itself" in prompt, (
        "primary.txt's no-self-trigger sentence was reworded. G2 is a global floor "
        "(docs/SAFETY.md owns it) — if the floor still holds, restore a sentence "
        "saying so and re-point this test; if step 8 has landed, this test and that "
        "sentence change together."
    )
    registry = build_registry()
    scheduling = re.compile(r"\bschedule|\bcron\b|\brecurring\b|\bevery day\b", re.I)
    for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
        offenders = [
            d.id
            for d in registry.visible_tools(mode)
            if scheduling.search(d.id) or scheduling.search(d.description)
        ]
        assert not offenders, (
            f"{mode.name} exposes {offenders}, which reads like scheduling, while "
            "primary.txt tells the person the app cannot schedule anything. One of "
            "the two is wrong — and G2 says it is not the prompt."
        )


def test_the_setup_prompt_still_describes_a_prompt_that_exists():
    """`setup_assistant.txt` is the other shipped prompt and makes one checkable
    claim — that it is running on a small free model with a key wizard to offer. Both
    prompts are in scope for this file; the cheap guard is that neither has been
    emptied or renamed out from under `main.py`, which reads them at turn time and
    would otherwise ship a blank system prompt.
    """
    shipped = (
        ("primary.txt", load_primary_prompt()),
        ("setup_assistant.txt", load_setup_prompt()),
    )
    for name, text in shipped:
        assert len(text.strip()) > 200, f"{name} is empty or truncated"
    assert "free model" in load_setup_prompt(), (
        "setup_assistant.txt no longer says it is on a small free model — that is the "
        "claim its 'name your own limits' principle rests on."
    )
