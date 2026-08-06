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

## Both halves

Each gate here also carries a precision test: it must be silent on a prompt a future
editor would legitimately write. That matters more for a prompt than for a document,
because the prompt is rewritten for *tone* — often, by someone who is not changing
what Addison can do — and a gate that reddens on an honest rewording is a gate that
gets deleted on the day the wording improves. [`gate_precision.py`](gate_precision.py)
owns the argument; one anchor here was tightened by writing its half (see
`_leaked_open_only_kinds`).
"""

from __future__ import annotations

import re

from agent_core.main import build_registry, load_primary_prompt, load_setup_prompt
from agent_core.policy import PolicyMode
from agent_core.widgets import STAT_SOURCES, WIDGET_KINDS, validate_widget_spec
from tests.gate_precision import assert_silent

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


# A denial is not a promise. `primary.txt`'s own house style is to state limits out
# loud — *"The app cannot schedule anything"*, *"Two honest limits to say out loud"* —
# so the sentence *"Addison can't run a shell command for you"* is exactly what an
# honest future edit produces, and the leak check flagged it as a promise. The clause
# BEFORE the mention is the discriminator: "the app cannot run a command" denies, "you
# can pin a command widget" promises. Scoped to that clause rather than the sentence so
# an unrelated later "not" cannot excuse a real leak — which does mean a denial that
# TRAILS the mention ("something that needs a command widget: there is no way to…")
# still flags. That is the safe direction to be wrong in, and the rewrite it asks for
# (put the denial first) is the clearer sentence anyway.
_DENIES = re.compile(
    r"\b(?:cannot|can't|can not|could not|couldn't|does not|doesn't|do not|don't"
    r"|will not|won't|is not|isn't|are not|aren't|never|no|refuses?|unable|without)\b",
    re.I,
)


def _clause_before(text: str, pos: int) -> str:
    """The text from the last sentence or clause boundary up to ``pos``."""
    start = max(
        text.rfind(".", 0, pos), text.rfind("\n", 0, pos),
        text.rfind(";", 0, pos), text.rfind(":", 0, pos),
    )
    return text[start + 1 : pos]


def _leaked_open_only_kinds(prompt: str, open_only: list[str]) -> list[str]:
    """OPEN-only kinds the Simple prompt PROMISES (as opposed to rules out)."""
    leaked: list[str] = []
    for kind in open_only:
        for match in re.finditer(_DESCRIBED_IN_PROMPT[kind], prompt, re.I):
            if _DENIES.search(_clause_before(prompt, match.start())):
                continue
            leaked.append(kind)
            break
    return leaked


def _kinds_not_described(enumeration: str, kinds: list[str]) -> list[str]:
    return [k for k in kinds if not re.search(_DESCRIBED_IN_PROMPT[k], enumeration, re.I)]


def _stat_sources_not_named(enumeration: str) -> list[str]:
    return [
        s for s in sorted(STAT_SOURCES)
        if not re.search(_STAT_SOURCE_IN_PROMPT[s], enumeration, re.I)
    ]


# A tool that MAKES a widget, not a tool that merely mentions them: `snapshot_now`
# says it captures "widgets and routines", which is true and not the claim. The id is a
# substring test on purpose — `create_widget` has no word boundary before "widget", and
# \b silently let exactly that name through.
_CREATES_A_WIDGET = re.compile(
    r"\b(?:creat|mak|add|build|pin|new)\w*\s+(?:a\s+|the\s+)?widget", re.I
)
_READS_AS_SCHEDULING = re.compile(r"\bschedule|\bcron\b|\brecurring\b|\bevery day\b", re.I)


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
    missing = _kinds_not_described(enumeration, sorted(_safe_kinds()))
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
    leaked = _leaked_open_only_kinds(prompt, open_only)
    assert not leaked, (
        f"primary.txt describes {leaked}, which validate_widget_spec REFUSES in SAFE "
        "mode. Simple would be offered a widget it cannot save. Remove the mention."
    )


def test_the_leak_check_reads_an_honest_limit_as_a_limit():
    """The precision half, and the reason `_leaked_open_only_kinds` exists at all.

    This gate scans the whole prompt, so it meets every sentence in it — including the
    ones whose job is to say what Addison *cannot* do. `primary.txt` already carries
    several ("The app cannot schedule anything…", "Two honest limits to say out loud"),
    so a rule that cannot tell a denial from an offer would redden the day someone adds
    the most useful sentence in the file. It still flags an actual offer, which is the
    half `test_the_simple_prompt_never_promises_a_developer_only_widget` proves.
    """
    open_only = sorted(set(WIDGET_KINDS) - _safe_kinds())
    assert open_only == ["command"], f"OPEN-only kinds changed to {open_only}"
    assert_silent(
        "test_the_simple_prompt_never_promises_a_developer_only_widget",
        lambda text: _leaked_open_only_kinds(text, open_only),
        {
            "an honest limit, in the voice the prompt already uses": (
                "The app cannot run a shell command, schedule anything, or send "
                "messages anywhere."
            ),
            "a refusal aimed at the person's request": (
                "If they ask for one, say Simple has no command widget, then offer "
                "what does work: a checklist, a note or a timer."
            ),
            "today's prompt, which mentions no developer widget at all": (
                load_primary_prompt()
            ),
        },
    )
    assert _leaked_open_only_kinds(
        "You can pin a command widget in the side panel and tap it to run a command.",
        open_only,
    ) == ["command"], (
        "the denial exemption now swallows a real offer — that is the leak this gate "
        "exists for, and it must still be caught"
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
    missing = _stat_sources_not_named(enumeration)
    assert not missing, (
        f"primary.txt's widget sentence lists the numbers Addison keeps track of and "
        f"omits {missing}. Add it there — that clause is the only place the person is "
        "told the readout exists."
    )


# A rewrite of the enumeration that changes the WORDS and not the capabilities: the
# clauses reordered, the kinds regrouped, the stat sources given as a list. This is the
# edit the prompt gets most often — it is prose written for Mira and Petr, and it is
# tuned for tone — so the two positive gates have to survive it or they are a tax on
# improving the sentence rather than a guard on what it promises.
_A_LEGITIMATE_REWORDING = (
    "A widget does one of four things: it is a timer they start and pause themselves; "
    "it holds a checklist they tick off, or a note they can edit; it runs a routine "
    "they saved, in one tap; or it shows a number Addison keeps track of — how fast "
    "the models reply, what's connected, or tokens used this month."
)


def test_the_positive_prompt_gates_survive_an_honest_rewording():
    """The gate that fires on a stale prompt must not fire on a *better* one.

    Both checks read the enumeration through a phrase table, and a phrase table is the
    kind of anchor that quietly demands one exact sentence forever. Reordering the
    clauses, splitting "a checklist or a note" differently and listing the three stat
    sources in another order are all edits that change nothing about what Addison can
    build — and all three are what a person improving this sentence would do first.
    """
    assert_silent(
        "test_the_prompt_describes_every_kind_a_simple_user_can_build",
        lambda text: _kinds_not_described(text, sorted(_safe_kinds())),
        {"the enumeration reworded, same five kinds": _A_LEGITIMATE_REWORDING},
    )
    assert_silent(
        "test_the_prompt_names_every_stat_source",
        _stat_sources_not_named,
        {"the three stat sources in another order": _A_LEGITIMATE_REWORDING},
    )


def test_the_enumeration_is_found_by_its_opening_and_stops_at_the_sentence_end():
    """`_what_a_widget_can_be` is scoped to one sentence because a wider search let a
    missing kind through — the word survives in the honest-limits sentence a few
    clauses later. The precision risk of scoping that tightly is the opposite: it must
    still FIND the sentence when the rest of the bullet is rewritten around it, and it
    must not swallow the limits that follow, or the scoping buys nothing.
    """
    prompt = (
        "- Widgets in the side panel: widgets are small launchers, not apps. "
        + _A_LEGITIMATE_REWORDING
        + " Two honest limits to say out loud: a checklist's lines are fixed when it "
        "is made; and a timer counts down while they are looking at it."
    )
    found = _what_a_widget_can_be(prompt)
    assert found.startswith("A widget does one of four things"), found
    assert "honest limits" not in found, (
        "the enumeration search now swallows the sentences after it, which is what let "
        "a missing kind through before — a kind mentioned in the LIMITS is not offered"
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
    for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
        offenders = [
            d.id
            for d in registry.visible_tools(mode)
            if "widget" in d.id.lower()
            or _CREATES_A_WIDGET.search(d.label)
            or _CREATES_A_WIDGET.search(d.description)
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
    for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
        offenders = [
            d.id
            for d in registry.visible_tools(mode)
            if _READS_AS_SCHEDULING.search(d.id) or _READS_AS_SCHEDULING.search(d.description)
        ]
        assert not offenders, (
            f"{mode.name} exposes {offenders}, which reads like scheduling, while "
            "primary.txt tells the person the app cannot schedule anything. One of "
            "the two is wrong — and G2 says it is not the prompt."
        )


def test_the_two_registry_reads_are_silent_on_the_descriptions_tools_actually_carry():
    """Both registry gates read every tool's plain-language `description`, and those are
    written for Mira and Petr — full of ordinary verbs about widgets, routines and time.
    A near-miss there fails the build on a tool that does nothing of the kind, and the
    fix a reader would reach for is to make the user-facing copy worse.

    `snapshot_now`'s real description is the standing example, and the comment on
    `_CREATES_A_WIDGET` has always claimed it is safe — it is asserted here instead.
    """
    snapshot_now = next(
        d for d in build_registry().visible_tools(PolicyMode.SAFE) if d.id == "snapshot_now"
    )
    assert "widgets" in snapshot_now.description, (
        "snapshot_now no longer mentions widgets — pick another tool whose honest "
        "description brushes against the claim, or drop this sample"
    )
    assert_silent(
        "test_the_prompt_is_right_that_no_tool_makes_a_widget",
        lambda text: [text] if _CREATES_A_WIDGET.search(text) else [],
        {
            "the real snapshot_now description, which captures widgets": (
                snapshot_now.description
            ),
            "a tool that mentions the side panel without making anything": (
                "Shows the widgets in the side panel and nothing else."
            ),
            "a tool that creates something that is not a widget": (
                "Creates a new routine from the steps you just approved."
            ),
        },
    )
    assert_silent(
        "test_the_prompt_is_right_that_nothing_runs_by_itself",
        lambda text: [text] if _READS_AS_SCHEDULING.search(text) else [],
        {
            "running saved steps on request, which is not scheduling": (
                "Runs the steps you saved, when you ask for them."
            ),
            "a timer the person watches, which is the widget kind that ships": (
                "Counts down while you are looking at it. Nothing rings at zero."
            ),
            "a description with a date in it": (
                "Looks things up online and tells you when the page was last updated."
            ),
        },
    )


def test_the_setup_prompt_still_describes_a_prompt_that_exists():
    """`setup_assistant.txt` is the other shipped prompt and makes one checkable
    claim — that it is running on a small free model with a key wizard to offer. Both
    prompts are in scope for this file; the cheap guard is that neither has been
    emptied or renamed out from under `main.py`, which reads them at turn time and
    would otherwise ship a blank system prompt.

    The one gate here with no precision test, deliberately: a length floor and a
    substring have no false-positive surface to test. They can only fail by the text
    genuinely being gone, so a "silent on legitimate content" sample would assert that
    a non-empty file is non-empty.
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


def test_the_kind_bookkeeping_accepts_a_kind_that_was_added_properly():
    """`test_every_widget_kind_is_accounted_for` is a forcing function, so its precision
    question is narrow but real: does it stay quiet for someone who did the work?

    Adding a kind means three edits — the code's `WIDGET_KINDS`, a sample here, and a
    phrase for the prompt gate to look for. If the check still complained after all
    three, the next person would delete it rather than find the fourth thing it wanted.
    """
    kinds = set(WIDGET_KINDS) | {"gauge"}
    samples = set(_SAMPLES) | {"gauge"}
    described = set(_DESCRIBED_IN_PROMPT) | {"gauge"}
    assert samples == kinds and described == kinds, (
        "the kind bookkeeping rejects a kind added to all three places at once — a "
        "forcing function that cannot be satisfied gets deleted instead of obeyed"
    )
