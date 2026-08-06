"""Documentation drift — the failures that prose alone cannot catch.

This repo's recorded failure mode is not bad code, it is **prose that stopped being
true**: `CLAUDE.md` has twice asserted the opposite of what shipped, and a
branch-state paragraph was falsified by a merge that never touched the file
containing it. Every check here exists because that class of drift has actually
happened, and none of it is caught by the other gates.

It matters more here than in a human-run repo. A person senses a stale doc — the
tone is off, the date is old. An agent reads it as true and acts on it.

**The load-bearing facts and their contradiction patterns live in
[`doc_claims.py`](doc_claims.py), one row each.** Adding a rule is adding a row;
`test_no_document_contradicts_a_registered_claim` below is the only test that
iterates them, and it is the only one you should need to touch.

Same idiom as `test_capture_scope_covers_every_schema_table`: the build fails when a
document and the tree disagree, so the decision is forced at the moment of the
change rather than discovered a fortnight later.
"""

from __future__ import annotations

import re
from datetime import date

from tests.doc_claims import CLAIMS, REPO, markdown_files, offenders_for


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


# ---------------------------------------------------------------------------
# The claims registry — one test, many rows
# ---------------------------------------------------------------------------


def test_no_document_contradicts_a_registered_claim():
    """The generalisation of `G3_RESOLVED_IN_OPEN`, which is the only mechanism that
    has ever caught this repo's drift automatically — flipping it named eight
    offending lines across seven files in one run.

    Each row in `doc_claims.CLAIMS` carries a load-bearing fact, its owning
    document, and the shape a contradiction takes. This iterates them and prints a
    **work order** per offender: file, line, the offending text, what is actually
    true, and the edit to make. *"Docs are inconsistent"* costs a turn of
    investigation; a file and a line gets fixed on the spot.

    Two properties the rows are designed around, both learned the hard way:

    * **Polarity is two-sided.** A check that enforces the PRESENCE of a caveat is
      symmetric under the caveat becoming false, and stays green through exactly the
      drift it was written for. Rows carry `while_true` AND `while_false`, so the
      day an owner constant flips, every document still asserting the old fact fails.
    * **Deference beats copying.** Where a rule cannot tell "asserting" from
      "discussing", the checkable rule is that a restatement must have its owner
      within reach. A copy of a caveat outlives whatever is true today; a pointer at
      the owner does not.

    To add a rule, add a row — see `doc_claims.py`'s module docstring. Do not add a
    test here.
    """
    reports: list[str] = []
    for claim in CLAIMS:
        reports += [offender.work_order() for offender in offenders_for(claim)]
    assert not reports, (
        f"{len(reports)} passage(s) contradict a claim registered in "
        "tests/doc_claims.py. Fix the document, or — if the FACT changed — flip the "
        "claim's owner constant and amend the named lines in the SAME commit:\n\n"
        + "\n\n".join(reports)
    )


def test_every_claim_row_is_well_formed():
    """Structural sanity for the registry itself, so a malformed row fails loudly
    rather than silently guarding nothing.

    The failure mode this exists for: a row whose `holds` is False and whose
    `while_false` is None is *inert* — it looks like coverage in a diff and enforces
    nothing. That is worse than no row, because the next agent trusts it.
    """
    problems: list[str] = []
    ids: set[str] = set()
    for claim in CLAIMS:
        if claim.id in ids:
            problems.append(f"{claim.id}: duplicate id")
        ids.add(claim.id)
        if not (REPO / claim.owner).exists():
            problems.append(f"{claim.id}: owner {claim.owner} does not exist")
        if claim.while_false is not None and not claim.false_state:
            problems.append(
                f"{claim.id}: has a while_false pattern but no false_state sentence, "
                "so flipping `holds` would print a blank work order"
            )
        if not claim.holds and claim.while_false is None:
            problems.append(
                f"{claim.id}: holds=False with no while_false pattern — this row is "
                "INERT and guards nothing. Write the other polarity or delete the row."
            )
        for wrong in (claim.while_true, claim.while_false):
            if wrong is None:
                continue
            re.compile(wrong.pattern)          # raises on a bad pattern
            if wrong.excused_by:
                re.compile(wrong.excused_by)
            if len(wrong.fix) < 40:
                problems.append(
                    f"{claim.id}: `fix` is too short to be a work order — say which "
                    "file to edit and what to write."
                )
    assert not problems, "malformed claim rows:\n  " + "\n  ".join(problems)


# ---------------------------------------------------------------------------
# Documents whose self-description is checkable
# ---------------------------------------------------------------------------


def test_docs_readme_maps_every_document():
    """`docs/README.md` calls itself *"the full map — every file, what it owns"*, and
    `CLAUDE.md` repeats that promise. A claim that specific is checkable, so it is
    checked: a new document that nobody linked is invisible to an agent that reads
    the map instead of listing the directory.

    Scope is deliberately the depth-1 document set plus the immediate
    subdirectories — the map lists design briefs as directories on purpose, and
    enumerating every file inside them would be the kind of noise that gets a gate
    deleted.
    """
    readme = REPO / "docs/README.md"
    linked = {m.group(1).split("#")[0] for m in re.finditer(r"\]\(([^)]+)\)", readme.read_text())}
    linked = {t.rstrip("/") for t in linked}

    missing: list[str] = []
    for path in sorted((REPO / "docs").iterdir()):
        if path.name.startswith("."):
            continue
        rel = path.name.rstrip("/")
        if path.is_dir():
            if rel not in linked and not any(t.startswith(rel + "/") for t in linked):
                missing.append(f"docs/{rel}/")
        elif path.suffix == ".md" and path.name != "README.md" and rel not in linked:
            missing.append(f"docs/{rel}")

    assert not missing, (
        "docs/README.md claims to map every document and does not list these. Add a "
        "row saying what each one OWNS (or, for history, that it settles nothing) — "
        "an unlisted document is invisible to anyone who reads the map:\n  "
        + "\n  ".join(missing)
    )


def test_ci_runs_the_gate_script_for_every_job_it_defines():
    """`scripts/gates.sh` exists because the gate list lived in three places and all
    three disagreed on 2026-08-06 — CI ran `npx tsc --noEmit` (src only), so the
    `tsconfig.test.json` gate that `KNOWN-GAPS.md` recorded as CLOSED had never
    actually run there.

    The script's header states that `ci.yml` calls it. That is the claim, so this is
    the check: every job the workflow defines runs `scripts/gates.sh <job>`, and
    every job the script knows about has a workflow job. A second copy of the list
    cannot reappear without failing here.
    """
    script = (REPO / "scripts/gates.sh").read_text()
    workflow = (REPO / ".github/workflows/ci.yml").read_text()

    case_body = re.search(r"case \"\$JOB\" in(.*?)esac", script, re.S)
    assert case_body, "no `case \"$JOB\"` found in scripts/gates.sh — did it move?"
    known = set(re.findall(r"^\s*(\w+)\)", case_body.group(1), re.M)) - {"all"}
    assert known, "no jobs found in scripts/gates.sh — did the case statement move?"

    called = set(re.findall(r"\./scripts/gates\.sh\s+(\w+)", workflow))
    jobs_block = re.search(r"^jobs:\n(.*)\Z", workflow, re.S | re.M)
    assert jobs_block, "no `jobs:` block in .github/workflows/ci.yml"
    jobs = set(re.findall(r"^  (\w+):$", jobs_block.group(1), re.M))

    assert known == called, (
        f"scripts/gates.sh defines jobs {sorted(known)} but .github/workflows/ci.yml "
        f"runs {sorted(called)}. CI must run the script, never its own copy of the "
        "commands — that divergence is why the script exists."
    )
    assert jobs == called, (
        f"ci.yml defines jobs {sorted(jobs)} but only {sorted(called)} call "
        "scripts/gates.sh. Every job runs the script; a job with inline commands is a "
        "second gate list."
    )


# ---------------------------------------------------------------------------
# Perishable claims
# ---------------------------------------------------------------------------

# The convention (docs/CONVENTIONS.md owns it): an empirical number is written with
# the date it was taken AND the conditions it was taken under, like
#
#     29 ms *(measured 2026-07-31 · app-owned keychain item, self-signed dev build)*
#
# A measurement written as a permanent property is a trap for an agent, which has no
# instinct that a spike result might have been voided by a change elsewhere. This
# repo has already quoted one as a permanent property months after the condition it
# was measured under had changed.
MEASURED_MARKER = re.compile(r"\*\(measured (\d{4}-\d{2}-\d{2})\s*·\s*([^)]*)\)\*")
# Just the opening. Used where a window is too small to contain a long condition —
# well-formedness is the other test's job, not every caller's.
MEASURED_OPENING = re.compile(r"\*\(measured \d{4}-\d{2}-\d{2}\b")


def _without_code(text: str) -> str:
    """Blank out fenced blocks and inline code spans, preserving offsets.

    A marker being *shown* — the template in `CONVENTIONS.md`, an example in a
    fence — is not a marker being *used*, and a gate that cannot tell the two apart
    makes it impossible to document its own convention.
    """
    out = list(text)
    for match in re.finditer(r"```.*?```|`[^`\n]*`", text, re.S):
        for i in range(match.start(), match.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def test_every_measurement_marker_is_well_formed():
    """A marker that does not parse is a marker nobody can trust, and a convention
    nobody can grep for is not a convention.

    Silent by construction on prose that carries no marker — this checks the shape of
    the ones that do, so the cost of the convention is zero until someone uses it
    badly.
    """
    problems: list[str] = []
    for md in markdown_files():
        text = _without_code(md.read_text())
        rel = md.relative_to(REPO).as_posix()
        for match in re.finditer(r"\*\(measured\b[^)]*\)\*", text):
            line_no = text.count("\n", 0, match.start()) + 1
            full = MEASURED_MARKER.match(match.group(0))
            if full is None:
                problems.append(
                    f"{rel}:{line_no}: {match.group(0)[:80]} — expected "
                    "`*(measured YYYY-MM-DD · what it was measured under)*`"
                )
                continue
            try:
                date.fromisoformat(full.group(1))
            except ValueError:
                problems.append(f"{rel}:{line_no}: {full.group(1)} is not a real date")
            if len(full.group(2).strip()) < 8:
                problems.append(
                    f"{rel}:{line_no}: the marker names a date but not the CONDITIONS. "
                    "The condition is the perishable half — a number is only void "
                    "because the thing it was measured under changed."
                )
    assert not problems, "malformed measurement markers:\n  " + "\n  ".join(problems)


def test_a_spike_result_is_marked_perishable():
    """A spike is an experiment, and its result expires when the thing it was run
    against changes. `docs/secrets-and-keychain-plan.md` quoted spike 1's conclusion
    as a permanent property for six days after `sign-and-run.sh` had voided it.

    Narrow on purpose: only a spike reference whose neighbourhood carries a NUMBER
    WITH A UNIT — the perishable part. A spike discussed without a figure is
    narrative and needs no marker, and the design briefs' `ms` durations are design
    specifications rather than measurements, so they never come near this.
    """
    spike = re.compile(r"\bspikes?\s*\d", re.I)
    measurement = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:ms|s|%|MB|KB|GB)\b")
    # A result explicitly retired is already marked perishable, in the strongest way.
    retired = re.compile(r"SUPERSEDED|VOID|no longer true|is (?:now )?history", re.I)
    WINDOW = 260

    problems: list[str] = []
    for md in markdown_files():
        text = md.read_text()
        rel = md.relative_to(REPO).as_posix()
        for match in spike.finditer(text):
            scope = text[max(0, match.start() - WINDOW) : match.end() + WINDOW]
            if not measurement.search(scope):
                continue
            if MEASURED_OPENING.search(scope) or retired.search(scope):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            problems.append(
                f"{rel}:{line_no}: {text.split(chr(10))[line_no - 1].strip()[:110]}"
            )
    assert not problems, (
        "these spike figures read as permanent properties. Mark each perishable with "
        "`*(measured YYYY-MM-DD · the conditions it was measured under)*` — the "
        "condition is the half that goes void — or say plainly that the result has "
        "been superseded. docs/CONVENTIONS.md owns the convention:\n  "
        + "\n  ".join(problems)
    )
