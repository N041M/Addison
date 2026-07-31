"""Documentation drift — the failures that prose alone cannot catch.

This repo's recorded failure mode is not bad code, it is **prose that stopped being
true**: `CLAUDE.md` has twice asserted the opposite of what shipped, and a
branch-state paragraph was falsified by a merge that never touched the file
containing it. Every check here exists because that class of drift has actually
happened, and none of it is caught by the other gates.

Same idiom as `test_capture_scope_covers_every_schema_table`: the build fails when a
document and the tree disagree, so the decision is forced at the moment of the
change rather than discovered a fortnight later.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_PARTS = {"node_modules", ".git", ".venv", ".claude", ".pytest_cache", ".ruff_cache"}

# ---------------------------------------------------------------------------
# The one place in this repo that records the POLARITY of G3's OPEN-mode scope.
#
# `docs/SAFETY.md` owns the floor's prose; this constant owns the yes/no the tests
# reason from, so that flipping the fact fails every document that disagrees —
# in EITHER direction.
#
# It is a constant rather than a probe of the tree because the fact is a human
# judgement about whether a floor is enforced, not something a regex can measure.
# Its value is a promise to update the docs in the same commit that changes it
# (docs/README.md, rule 2).
#
# History, and why this exists at all: from 2026-07-26 to 2026-07-31 G3 was
# genuinely overclaimed in OPEN mode — `run_command` could delete the recovery
# floor's own files. Step 5.5 closed that (seatbelt profile + pre-gate denylist;
# `an_approved_command_cannot_delete_the_recovery_floor` in
# `shell/src-tauri/src/exec.rs`). Five documents were updated to say so and at
# least eight passages were not, so the tree asserted both facts at once for a
# day. The gate that was supposed to catch exactly this stayed green through all
# eight, because it only checked that a caveat was PRESENT — never whether the
# caveat was still TRUE. A one-directional check is not a check.
G3_RESOLVED_IN_OPEN = True


def markdown_files() -> list[Path]:
    return [
        p
        for p in REPO.rglob("*.md")
        if not (SKIP_PARTS & set(p.relative_to(REPO).parts))
    ]


def test_every_markdown_link_resolves():
    """A relative link — or an embedded image — that points at nothing.

    Covers `![...](...)` as well as `[...](...)`, because the README now embeds
    generated screenshots and a deleted or renamed PNG would otherwise break the
    front page silently.

    Cheap, mechanical, and it has already caught a real break: the safety model was
    split into its own file and one link kept a path relative to the repo root
    instead of to `docs/`.
    """
    broken: list[str] = []
    for md in markdown_files():
        for match in re.finditer(r"\]\(([^)#][^)]*?)\)", md.read_text()):
            target = match.group(1).split("#")[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).exists():
                broken.append(f"{md.relative_to(REPO)} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


def test_data_model_er_diagrams_cover_every_schema_table():
    """`docs/data-model.md` is a hand-maintained mirror of `schema.sql`, so it drifts
    by construction — a new table simply does not appear, and nothing complains.

    The diagrams are deliberately NOT generated: they are split into three blocks
    grouped by concern, and a machine dump of fifteen tables would be less use than
    the curated grouping. So the *grouping* stays human and the *coverage* is
    enforced here. A new table means adding it to whichever block it belongs in.
    """
    schema = (REPO / "agent_core/memory/schema.sql").read_text()
    tables = set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? ([a-z_]+)", schema))
    assert tables, "no CREATE TABLE found — did schema.sql move?"

    doc = (REPO / "docs/data-model.md").read_text()
    blocks = re.findall(r"```mermaid\s*\nerDiagram(.*?)```", doc, re.S)
    assert blocks, "no erDiagram blocks found in data-model.md"

    named: set[str] = set()
    for block in blocks:
        named |= set(re.findall(r"^\s{4}([a-z_]+)\s*\{", block, re.M))
        named |= set(re.findall(r"\b([a-z_]+)\s+\|\|", block))

    missing = sorted(tables - named)
    assert not missing, (
        "these tables exist in schema.sql but appear in no ER diagram: "
        f"{missing}. Add them to docs/data-model.md."
    )
    stale = sorted(named - tables)
    assert not stale, (
        f"these entities are drawn in docs/data-model.md but no longer exist: {stale}"
    )


# `CLAUDE.md` is exempt from the restatement rule: it is the auto-loaded short form
# and is *supposed* to carry the rule. `SAFETY.md` is the owner. The retired scope
# amendment and the step-5.5 plan are frozen records whose present tense describes
# the day they were written — both say so in their own opening lines.
_FROZEN = {
    "docs/addison-scope-amendment-2026-07.md",
    "docs/step-5.5-containment-plan.md",
}
_GUARANTEE_ALLOWED = {"docs/SAFETY.md", "CLAUDE.md"} | _FROZEN


def test_g3s_guarantee_never_appears_without_its_scope():
    """`docs/SAFETY.md` owns the floors. Six copies of G3's normative sentence lived
    in three other authoritative documents, and every one asserted *"the restore path
    is itself unbreakable"* **without** the qualification that it then held only in
    SAFE — so correcting the floor took four commits and still left three files
    disagreeing.

    The rule enforced here is deliberately the weaker, checkable one: a document may
    mention the guarantee, but **it must defer to the owner within reach** — a link
    to `SAFETY.md`. Distinguishing "asserting" from "merely discussing" is a
    judgement a regex cannot make, and an earlier draft grew three special cases
    trying.

    **Tightened 2026-08-01, and this is the whole lesson of the drift it missed.**
    The excuse used to be *"a link to SAFETY.md, **or** the word 'overclaimed', or a
    reference to step 5.5"* — i.e. carrying a copy of the caveat was as good as
    pointing at the owner. That is only ever true while the caveat is. When step 5.5
    closed the overclaim, eight passages went on asserting it and every one of them
    still matched the excuse, because the excuse was the stale words themselves.
    A copy of a caveat is not a scope; a pointer at the owner is. While the floor is
    genuinely limited (`G3_RESOLVED_IN_OPEN = False`) the caveat words are admitted
    again, because then they are load-bearing rather than residue.
    """
    guarantee = re.compile(
        r"restore path is itself unbreakable|drive Addison into an unrecoverable",
        re.I,
    )
    # Deference to the owner always excuses. The caveat words excuse ONLY while the
    # caveat is true — see the docstring; this is the direction the old gate lacked.
    patterns = [r"SAFETY\.md"]
    if not G3_RESOLVED_IN_OPEN:
        patterns.append(r"overclaim|step 5\.5|step-5\.5")
    excused = re.compile("|".join(patterns), re.I)
    # Checked over a WINDOW, not a line: every real instance in the tree has its
    # qualifier on the next line or the one after, and a line-by-line rule flagged
    # all three of them. Wide enough to cover a long table row, whose qualifier sits
    # in the same cell.
    WINDOW = 500

    offenders: list[str] = []
    for md in markdown_files():
        rel = md.relative_to(REPO).as_posix()
        if rel in _GUARANTEE_ALLOWED:
            continue
        text = md.read_text()
        for match in guarantee.finditer(text):
            lo = max(0, match.start() - WINDOW)
            if excused.search(text[lo : match.end() + WINDOW]):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            line = text.split("\n")[line_no - 1].strip()
            offenders.append(f"{rel}:{line_no}: {line[:90]}")
    assert not offenders, (
        "G3's guarantee is stated outside docs/SAFETY.md without deferring to it — "
        "link to the owner instead, or the copy outlives whatever is true today:\n  "
        + "\n  ".join(offenders)
    )


# Present-tense assertions that G3 IS limited in OPEN. Past tense is deliberately
# NOT matched ("was overclaimed", "used to be overclaimed"): recounting the five
# days it was true is exactly what SAFETY.md and BUILD-LOG are for.
_SAYS_STILL_OVERCLAIMED = re.compile(
    r"(?:is|are|remains?|stays)\s+(?:currently\s+|presently\s+|still\s+)?overclaim"
    r"|(?:currently|presently|still)\s+overclaim"
    r"|overclaimed in OPEN until",
    re.I,
)

# Present-tense assertions that it is CLOSED. Deliberately a list of the phrasings
# actually in the tree rather than an attempt at English: the guarantee this test
# offers is that flipping the constant fails documents, not that it finds every
# possible sentence. Add a phrasing here when you write one.
_SAYS_RESOLVED = re.compile(
    r"no longer overclaim"
    r"|overclaim(?:'s)? is closed"
    r"|(?:true|holds?)\s+(?:again\s+)?in OPEN"
    r"|guarantee holds again"
    r"|true in both modes"
    r"|qualification came off"
    r"|qualification is lifted"
    # Anchored to the correction it resolves: a bare "RESOLVED <date>" also matches
    # the keychain trace in BUILD-LOG, which has nothing to do with this floor.
    r"|(?:scope correction|amended)[^|\n]{0,40}RESOLVED",
    re.I,
)


def test_no_document_contradicts_the_recorded_state_of_g3_in_open():
    """The check the old gate could not make: is the caveat still TRUE?

    `test_g3s_guarantee_never_appears_without_its_scope` asks whether a scope is
    within reach of a restatement. It cannot ask which scope, so on 2026-07-31 it
    passed a tree that simultaneously said G3 was overclaimed in OPEN (eight
    passages) and that step 5.5 had closed it (five). Both cannot be true, and the
    only automated defence against that exact class of drift was blind to it.

    Polarity here comes from ONE place — `G3_RESOLVED_IN_OPEN` — so the day the
    fact changes, every document still asserting the old one fails. That is the
    property the previous design lacked: it enforced the PRESENCE of a caveat,
    which is symmetric under the caveat becoming false.

    Frozen records are exempt by file, not by phrasing: the retired amendment and
    the step-5.5 plan both open by saying their present tense is the day they were
    written, and rewriting a record to keep it current is how a record stops being
    one.
    """
    wrong_way = _SAYS_STILL_OVERCLAIMED if G3_RESOLVED_IN_OPEN else _SAYS_RESOLVED
    truth = "closed (step 5.5)" if G3_RESOLVED_IN_OPEN else "still open"

    offenders: list[str] = []
    for md in markdown_files():
        rel = md.relative_to(REPO).as_posix()
        if rel in _FROZEN:
            continue
        for n, line in enumerate(md.read_text().split("\n"), 1):
            if wrong_way.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:110]}")
    assert not offenders, (
        f"G3_RESOLVED_IN_OPEN says the OPEN-mode overclaim is {truth}, and these "
        "lines say the opposite. Fix them in the same commit that flipped the "
        "constant — docs/SAFETY.md owns the wording:\n  " + "\n  ".join(offenders)
    )


def test_the_retired_amendment_is_never_granted_precedence():
    """The 2026-07-20 scope amendment was folded into the specs and retired
    2026-07-27. It kept a *"where we differ, the amendment wins"* rule long after its
    content had been merged, and the engineering spec printed a second copy of the
    same rule — so every reader replayed a merge that had already happened, and six
    false statements in this doc set traced back to that chain.

    Prose describing the retired rule is fine and expected. Prose *asserting* it is
    the regression.
    """
    pattern = re.compile(
        r"(?:the )?amendment (?:wins|governs|overrides)"
        r"|where it and the two specs differ"
        r"|amendment (?:is )?authoritative",
        re.I,
    )
    # Prose that talks ABOUT the retired rule rather than applying it.
    historical = re.compile(
        r"used to|no longer|retired|reverse of|the rule that|kept a|which said|"
        r"it kept|kill the|kills the|kept its own|kept the",
        re.I,
    )

    def is_quotation(line: str, span: tuple[int, int]) -> bool:
        """True when the match sits inside double quotes — the phrase is being
        CITED, not asserted. Cheap parity check: an odd number of quote marks
        before the match means it opened a quoted span that has not closed."""
        return line.count('"', 0, span[0]) % 2 == 1

    offenders: list[str] = []
    for md in markdown_files():
        for n, line in enumerate(md.read_text().split("\n"), 1):
            match = pattern.search(line)
            if not match or historical.search(line):
                continue
            if is_quotation(line, match.span()):
                continue
            offenders.append(f"{md.relative_to(REPO)}:{n}: {line.strip()[:90]}")
    assert not offenders, (
        "a document grants the retired scope amendment precedence again:\n  "
        + "\n  ".join(offenders)
    )
