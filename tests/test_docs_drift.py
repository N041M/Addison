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


def test_g3s_guarantee_never_appears_without_its_scope():
    """`docs/SAFETY.md` owns the floors. Six copies of G3's normative sentence lived
    in three other authoritative documents, and every one asserted *"the restore path
    is itself unbreakable"* **without** the qualification that it holds in SAFE and
    is overclaimed in OPEN — so correcting the floor took four commits and still left
    three files disagreeing.

    The rule enforced here is deliberately the weaker, checkable one: a document may
    mention the guarantee, but **its scope must be within reach** — a link to
    `SAFETY.md`, the word "overclaimed", or a reference to step 5.5. Distinguishing
    "asserting" from "merely discussing" is a judgement a regex cannot make, and an
    earlier draft of this test grew three special cases trying. Requiring the caveat
    nearby needs no judgement and catches the defect that actually happened, which
    was dropping the caveat in the copy.

    `CLAUDE.md` is exempt: it is the auto-loaded short form and carries the rule with
    its qualification. The retired amendment is exempt as a record of the original
    wording.
    """
    guarantee = re.compile(
        r"restore path is itself unbreakable|drive Addison into an unrecoverable",
        re.I,
    )
    allowed = {"docs/SAFETY.md", "CLAUDE.md", "docs/addison-scope-amendment-2026-07.md"}
    # A restatement is acceptable when its own neighbourhood either defers to the
    # owner or carries the caveat. Checked over a WINDOW, not a line: every real
    # instance in the tree has its qualifier on the next line or the one after, and a
    # line-by-line rule flagged all three of them.
    excused = re.compile(r"SAFETY\.md|overclaim|step 5\.5|step-5\.5", re.I)
    # Wide enough to cover a long table row, whose qualifier sits in the same cell.
    WINDOW = 500

    offenders: list[str] = []
    for md in markdown_files():
        rel = md.relative_to(REPO).as_posix()
        if rel in allowed:
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
        "G3's guarantee is stated outside docs/SAFETY.md — link to it instead, or the "
        "OPEN-mode caveat gets dropped in the copy:\n  " + "\n  ".join(offenders)
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
