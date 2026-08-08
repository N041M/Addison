"""The claims registry — load-bearing facts, and what a document contradicting one
looks like.

**Why this file exists.** This repository is written and read almost entirely by
agents, and an agent has none of a human's instincts about stale prose: no sense
that a tone is dated, no glance at the timestamp, no *"that can't still be right"*.
It reads a sentence as true and acts on it. Stale prose here is therefore not
untidy, it is a source of wrong behaviour — and the drift is invisible from inside
the file that carries it, because the change that falsified the sentence lives
somewhere else.

One mechanism has actually caught that automatically: `G3_RESOLVED_IN_OPEN` — an
owner constant plus a test that fails on any document asserting the other polarity.
Flipping it named eight offending lines across seven files in one run. This module
generalises it. **A rule here is a row, not a test.**

## Adding a rule

Append a `Claim` to `CLAIMS` below. `test_docs_drift.py`'s
`test_no_document_contradicts_a_registered_claim` iterates them; you do not write a
test, and you should not.

A row is worth adding when all three hold:

1. The fact is **load-bearing** — an agent that believes the stale version does the
   wrong thing (declines a capability, edits the wrong file, weakens a floor).
2. A contradiction has a **recognisable shape**. If you cannot write a pattern that
   is silent on today's tree, do not guess one: a gate that cries wolf gets deleted
   by the next agent and takes its real coverage with it.
3. The fact has **one owner**. The row names it, and the work order says *"link to
   the owner"* rather than *"copy the correction everywhere"*.

Then **prove the row**: break the thing it guards (edit a document to the wrong
polarity, or flip the claim's `holds`), watch
`test_no_document_contradicts_a_registered_claim` name the file and line, and put it
back. A pattern that has never been seen to fail is decoration.

## The failure output is the product

*"Docs are inconsistent"* costs an agent a whole turn of investigation. The message
these rows produce is a work order — file, line, the offending text, what is
actually true, and the specific edit to make. Write `fix` in the imperative, and
name the owner file in it.

## What is NOT a claim row

Presence requirements (*"docs/README.md must list every document"*), code-shaped
facts (*"the shipped prompt must describe every widget kind"*), and convention
well-formedness (*"a measurement carries its date and condition"*) are their own
named tests. This registry is for **a document asserting something the tree
contradicts**.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_PARTS = {"node_modules", ".git", ".venv", ".claude", ".pytest_cache", ".ruff_cache"}


def markdown_files() -> list[Path]:
    """Every markdown file in the repo, minus vendored and generated trees."""
    return [
        p
        for p in REPO.rglob("*.md")
        if not (SKIP_PARTS & set(p.relative_to(REPO).parts))
    ]


# ---------------------------------------------------------------------------
# Owner constants — the ONE place each polar fact is recorded.
#
# These are constants rather than probes of the tree because each is a human
# judgement ("is this floor enforced?", "which behaviour did the owner pick?"),
# not something a regex can measure. The value is a promise: the commit that
# changes the fact flips the constant AND amends the documents the run names.
# ---------------------------------------------------------------------------

# G3's OPEN-mode scope. From 2026-07-26 to 2026-07-31 the floor was genuinely
# overclaimed in OPEN — `run_command` could delete the recovery floor's own files.
# Step 5.5 closed it (seatbelt profile + pre-gate denylist;
# `an_approved_command_cannot_delete_the_recovery_floor` in
# `shell/src-tauri/src/exec.rs`). Five documents were updated to say so and at least
# eight passages were not, so the tree asserted both facts at once for a day. The
# gate that should have caught it stayed green through all eight, because it only
# checked that a caveat was PRESENT — never whether the caveat was still TRUE.
# A one-directional check is not a check.
G3_RESOLVED_IN_OPEN = True

# Artifact disabling (owner decision 2026-08-06). Routines and widgets created in
# OPEN are LISTED BUT DISABLED under SAFE, not hidden. They used to be hidden —
# filtered out of `routine.list` / `widget.list` entirely — and the correction was
# restated in six documents when the brief said two. `docs/SAFETY.md` owns why.
ARTIFACTS_LISTED_NOT_HIDDEN = True

# MCP transport (owner decision 2026-08-06, step 7 phase 1). HTTP only for v1: an
# `mcp_servers` row stores a URL and there is no column that could hold a launch
# command, because stdio would mean the Agent Core starting an arbitrary executable
# outside the seatbelt step 5.5 built. This is registered rather than merely written
# down because the docs already carried the pre-decision sketch — data-model.md
# described the row as holding "the launch command or base URL" — and a document
# that says a server row can carry a command is a document that invites someone to
# add the column. Flip this the day stdio genuinely ships.
MCP_TRANSPORT_HTTP_ONLY = True

# MCP admission (owner decision 2026-08-06, step 7). Dev-only for v1: an MCP tool
# never enters `visible_tools(SAFE)`, and what SAFE would ever admit is DEFERRED
# rather than answered. This is registered because the pre-decision sketch — "in SAFE
# only read-only or genuinely undo-able MCP tools are admitted" — had been copied into
# five documents, and it is the most dangerous shape of stale sentence in this repo:
# it reads as a specification for admitting a stranger's self-declared risk into SAFE,
# which is precisely the hole invariant 2 exists to keep shut and precisely the
# question the owner declined to answer. Deferring it is what unblocked step 7 at all
# (amendment §13 Q6 / design-doc question 14). Flip this the day SAFE admission is
# genuinely decided and built.
MCP_IS_DEV_ONLY_IN_V1 = True

#: Step 8 phase 3 (2026-08-07): Addison can AUTHOR an automation AND ARM one —
#: `arm_automation` installs a launchd job through the shell — and arming requires a
#: **per-automation code the person retypes** on top of the ordinary permission card
#: (`agent_core/automation_nonce.py`). G2 is unchanged and still holds: the OS runs
#: the job, Addison never fires it, and `RunAtLoad` is never set so arming itself
#: causes no run.
#:
#: THIS ROW REPLACED ITS OWN PREDECESSOR, WHICH IS THE POINT OF THE MECHANISM. It was
#: `AUTOMATION_AUTHORING_BUILT_ARMING_NOT` through phase 2 and earned its place then:
#: the phase-2 review found four documents still saying "nothing in the tree can
#: author automation", each correct when written and each falsified by a change that
#: never touched its file. Phase 3 falsified the row's own second half, so the FACT
#: changed and the row changed with it, in the same commit that changed the tree —
#: which is exactly what CLAUDE.md asks for and what nobody did in phase 2.
#:
#: Flip to False only if arming is REMOVED from the tree.
AUTOMATION_ARMING_BUILT = True

# Redaction's strength (step 5.5 item 4, and load-bearing since step 7). It is a
# pattern matcher over text: a secret in a format nobody has enumerated passes, and
# so does an enumerated one a stranger has split with a newline, a tab, a quote or a
# backslash, or written in fullwidth characters. `agent_core/redaction.py`'s own
# docstring says it in those words — "a backstop, not a boundary … no doc may
# describe it as elimination" — which is a rule, and a rule this registry can
# execute. There is no meaningful opposite to register: nothing would ever make a
# finite list of vendor prefixes complete, so this row is one-directional.
#
# It is here because the failure it guards is the shape a reviewer found twice in one
# day: a document promising a leak cannot happen is a document that stops the next
# person building the control that would actually stop one, AND the audit row is
# written from the redactor's own report, so an over-claim in prose is an over-claim
# a reader has no way to check.
REDACTION_IS_A_BACKSTOP = True

# Phase 3's scope (owner decision 2026-07-25). It is TWO tracks, not one: the
# packaging track it has always been — signing, notarisation, the auto-updater,
# previous-binary restore, Secure-Enclave identity — AND the Developer review surface
# (`docs/phase-3-review-surface-plan.md`: a file tree over the trusted roots, a
# read-only viewer, a diff of Addison's still-live edits, per-file revert).
#
# Registered because this drift is not hypothetical — the repo has already shipped it
# twice. The commit that added the plan said the redefinition "was written back into
# the four documents that had scoped Phase 3 the old way" and wrote it into two of
# them; a later pass added a third; `docs/architecture.md` never received it at all;
# and `ROADMAP.md` then acquired a FIFTH packaging-only definition afterwards. Every
# one of those sentences was correct when written and falsified by a change that never
# touched its file, which is the shape this registry exists for. The cost of believing
# the stale version is specific: an agent starting Phase 3 builds the updater and
# never learns there is an approved, unblocked plan sitting beside it.
#
# Flip this the day the review surface is genuinely dropped from the phase.
PHASE_3_INCLUDES_THE_REVIEW_SURFACE = True

# MCP dispatch (step 7). FALSE since phase 3 shipped on 2026-08-07: an MCP tool IS
# invoked, through the ordinary gate, with a card on every invocation in OPEN — and
# `mcp_catalog.MCP_TOOLS_ARE_CALLABLE` was flipped in the same commit, as this
# comment said it must be. That constant and this one are the same fact, one
# enforced in code and one in prose.
#
# It was registered because "nothing is callable" was the shape of stale sentence
# phase 1 had already produced once: every document had to say it, and phase 2
# changed HALF of the sentence (Addison could now see a server's tools) while
# leaving the other half exactly as true. Now the polarity has flipped, and the rule
# does the opposite job — it hunts the documents that still promise nothing can run,
# because a stale reassurance about a stranger's code is the more dangerous of the
# two directions. Both are worth catching, which is why the row is two-sided.
MCP_TOOLS_ARE_NOT_CALLABLE = False


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Wrong:
    """What a contradicting passage looks like, and the work order for fixing it.

    ``pattern``   a hit is a candidate offender. Prefer the phrasings actually in
                  the tree over an attempt at English: the guarantee on offer is
                  that flipping the fact fails documents, not that every possible
                  sentence is found. Add a phrasing when you write one.
    ``fix``       imperative, specific. This is the sentence an agent acts on.
    ``excused_by``a hit is forgiven when this also matches *within reach* — the
                  "defer to the owner" idiom. `None` means no hit is forgiven.
    ``window``    0 = the excuse must be on the same line; N = within N characters
                  either side of the match anywhere in the file.
    ``in_code_fence``  only search inside ``` fenced blocks. Use for rules about
                  commands, where prose mentioning the command is not the offence.
    ``quotation_is_citation``  a match inside a double-quoted span is being CITED,
                  not asserted, so it is forgiven. Off by default: for most facts a
                  quoted falsehood still reads as true to whoever greps for it.
    """

    pattern: str
    fix: str
    excused_by: str | None = None
    window: int = 0
    in_code_fence: bool = False
    quotation_is_citation: bool = False


@dataclass(frozen=True)
class Claim:
    """One load-bearing fact, and how a document contradicting it is recognised.

    ``holds`` is the owner constant. ``while_true`` is searched while it is True,
    ``while_false`` while it is False — that two-sidedness is the whole point:
    a check that only enforces the PRESENCE of a caveat stays green the day the
    caveat becomes a lie in the other direction.

    A one-directional rule (a fact with no meaningful opposite — a superseded
    script, an owner that must be deferred to) leaves ``while_false`` as None and
    ``holds`` at True.
    """

    id: str
    owner: str            # repo-relative path of the document that owns the topic
    true_state: str       # one line: what is true while `holds`
    while_true: Wrong
    holds: bool = True
    false_state: str = ""     # one line: what is true while NOT `holds`
    while_false: Wrong | None = None
    exempt: frozenset[str] = frozenset()   # repo-relative paths this rule skips
    globs: tuple[str, ...] = ("*.md",)

    def active(self) -> Wrong | None:
        return self.while_true if self.holds else self.while_false

    def state(self) -> str:
        return self.true_state if self.holds else self.false_state


# ---------------------------------------------------------------------------
# Exemption sets
# ---------------------------------------------------------------------------

# Frozen records. Both open by saying their present tense is the day they were
# written, and rewriting a record to keep it current is how a record stops being
# one. Exempt by FILE, never by phrasing.
FROZEN = frozenset(
    {
        "docs/addison-scope-amendment-2026-07.md",
        "docs/step-5.5-containment-plan.md",
    }
)

# `CLAUDE.md` is exempt from the restatement rules: it is the auto-loaded short
# form and is *supposed* to carry the enforceable version of the safety rules.
# It is NOT exempt from the polarity rules — a short form asserting the wrong
# polarity is the worst copy in the tree, because every session loads it.
_RESTATEMENT_EXEMPT = FROZEN | {"CLAUDE.md"}


# ---------------------------------------------------------------------------
# The claims
# ---------------------------------------------------------------------------

CLAIMS: tuple[Claim, ...] = (
    # -- G3's OPEN-mode scope: polarity ------------------------------------
    Claim(
        id="g3-open-mode-polarity",
        owner="docs/SAFETY.md",
        holds=G3_RESOLVED_IN_OPEN,
        true_state=(
            "G3's OPEN-mode overclaim was CLOSED by step 5.5 on 2026-07-31 "
            "(seatbelt profile + pre-gate denylist under run_command)."
        ),
        false_state=(
            "G3 is STILL overclaimed in OPEN mode — the restore path can be broken there."
        ),
        # Present-tense assertions that G3 IS limited in OPEN. Past tense is
        # deliberately NOT matched ("was overclaimed", "used to be overclaimed"):
        # recounting the five days it was true is what SAFETY.md and BUILD-LOG are for.
        while_true=Wrong(
            pattern=(
                r"(?:is|are|remains?|stays)\s+(?:currently\s+|presently\s+|still\s+)?overclaim"
                r"|(?:currently|presently|still)\s+overclaim"
                r"|overclaimed in OPEN until"
            ),
            fix=(
                "Amend the sentence to today's polarity, or delete the claim and link to "
                "docs/SAFETY.md, which owns this floor's scope. If the FACT changed, flip "
                "G3_RESOLVED_IN_OPEN in tests/doc_claims.py in the SAME commit."
            ),
        ),
        # The mirror image. A list of the phrasings actually in the tree.
        while_false=Wrong(
            pattern=(
                r"no longer overclaim"
                r"|overclaim(?:'s)? is closed"
                r"|(?:true|holds?)\s+(?:again\s+)?in OPEN"
                r"|guarantee holds again"
                r"|true in both modes"
                r"|qualification came off"
                r"|qualification is lifted"
                # Anchored to the correction it resolves: a bare "RESOLVED <date>"
                # also matches the keychain trace in BUILD-LOG, which has nothing
                # to do with this floor.
                r"|(?:scope correction|amended)[^|\n]{0,40}RESOLVED"
            ),
            fix=(
                "G3 is limited in OPEN again — this line says it is closed. Amend it, or "
                "link to docs/SAFETY.md, which owns the floor's scope."
            ),
        ),
        exempt=FROZEN,
    ),
    # -- G3's guarantee: never restated without deferring to its owner ------
    Claim(
        id="g3-guarantee-defers-to-owner",
        owner="docs/SAFETY.md",
        holds=G3_RESOLVED_IN_OPEN,
        # CORRECTED 2026-08-06, by reading the row against its owner. It used to say
        # "six copies of its normative sentence lived in three other authoritative
        # documents and every one went stale" — a count the tree has never had. On the
        # day the row was written the sentence appeared five times outside the owner and
        # the frozen record, across four documents (design-doc twice, the spec,
        # architecture.md, classes.md), and two of those quote it while explaining why
        # restore is not a registry tool, which is not a copy that went stale. A number
        # in a `true_state` is printed to the next agent as fact, so it has to be one:
        # this states the rule the row actually enforces instead.
        true_state=(
            "docs/SAFETY.md owns G3's scope and is the one place that states the "
            "guarantee; every other mention must have a pointer back to it within reach."
        ),
        false_state=(
            "docs/SAFETY.md owns G3's scope, and while the floor is genuinely limited "
            "the caveat words are load-bearing rather than residue."
        ),
        # The rule is deliberately the weaker, checkable one: a document may mention
        # the guarantee, but it must DEFER TO THE OWNER within reach. Distinguishing
        # "asserting" from "merely discussing" is a judgement a regex cannot make,
        # and an earlier draft grew three special cases trying.
        #
        # Tightened 2026-08-01, and this is the whole lesson of the drift it missed.
        # The excuse used to be "a link to SAFETY.md, OR the word 'overclaimed', or a
        # reference to step 5.5" — i.e. carrying a copy of the caveat was as good as
        # pointing at the owner. That is only ever true while the caveat is. When
        # step 5.5 closed the overclaim, eight passages went on asserting it and every
        # one still matched the excuse, because the excuse was the stale words
        # themselves. A copy of a caveat is not a scope; a pointer at the owner is.
        while_true=Wrong(
            pattern=r"restore path is itself unbreakable|drive Addison into an unrecoverable",
            fix=(
                "Replace the restated guarantee with a link to docs/SAFETY.md. A copy of "
                "a caveat outlives whatever is true today; a pointer at the owner does not."
            ),
            excused_by=r"SAFETY\.md",
            # A window, not a line: every real instance has its qualifier on the next
            # line or the one after, and a line-by-line rule flagged all three. Wide
            # enough to cover a long table row, whose qualifier sits in the same cell.
            window=500,
        ),
        while_false=Wrong(
            pattern=r"restore path is itself unbreakable|drive Addison into an unrecoverable",
            fix=(
                "Replace the restated guarantee with a link to docs/SAFETY.md, or keep the "
                "live caveat (the floor IS limited in OPEN right now) beside it."
            ),
            excused_by=r"SAFETY\.md|overclaim|step 5\.5|step-5\.5",
            window=500,
        ),
        # The OWNER states the guarantee; that is its job. Deference rules exempt the
        # owner, polarity rules never do — a short form asserting the wrong polarity
        # is the worst copy in the tree.
        exempt=_RESTATEMENT_EXEMPT | {"docs/SAFETY.md"},
    ),
    # -- Artifact disabling: polarity --------------------------------------
    Claim(
        id="artifact-disabling-polarity",
        owner="docs/SAFETY.md",
        holds=ARTIFACTS_LISTED_NOT_HIDDEN,
        true_state=(
            "Routines and widgets created in OPEN are LISTED BUT DISABLED under SAFE "
            "(display-only `unavailable` reason), never hidden — owner decision 2026-08-06."
        ),
        false_state=(
            "Routines and widgets created in OPEN are filtered out of routine.list / "
            "widget.list under SAFE."
        ),
        # Present tense only. The tree legitimately recounts the old behaviour
        # ("they were hidden outright until 2026-08-06", "as routines made in OPEN
        # were then hidden in SAFE"), and rewriting those would delete the reasoning.
        # Anchored to routines/widgets/artifacts so it never fires on a TOOL being
        # hidden from the SAFE view, which is true and unrelated (`open_only`).
        while_true=Wrong(
            pattern=(
                r"\b(?:routines?|widgets?|artifacts?)\b[^.\n]{0,110}?"
                r"\b(?:are|is|get|gets|stay|stays|remain|remains)\s+(?:\w+\s+){0,3}hidden\b"
                r"|\bhides?\s+(?:them|it|routines?|widgets?|artifacts?)\b"
                r"[^.\n]{0,40}\b(?:in|from|under)\s+(?:SAFE|Simple)\b"
                r"|\b(?:routines?|widgets?|artifacts?)\b[^.\n]{0,60}\b(?:are|is)\s+filtered out\b"
            ),
            fix=(
                "OPEN-built artifacts are listed-but-disabled under SAFE since 2026-08-06. "
                "Put the sentence in the past tense if it is history, or delete it and link "
                "to docs/SAFETY.md, which owns the rule and the reasoning."
            ),
        ),
        while_false=Wrong(
            pattern=r"\b(?:routines?|widgets?|artifacts?)\b[^.\n]{0,110}?listed but disabled",
            fix=(
                "Artifacts are hidden under SAFE again — this line says they are listed. "
                "Amend it, or link to docs/SAFETY.md."
            ),
        ),
        exempt=FROZEN,
    ),
    # -- MCP transport: HTTP only, so a server row is never a command ------
    Claim(
        id="mcp-transport-http-only",
        owner="docs/step-7-mcp-plan.md",
        holds=MCP_TRANSPORT_HTTP_ONLY,
        true_state=(
            "MCP transport is HTTP ONLY for v1 (owner decision 2026-08-06). An mcp_servers "
            "row holds a URL and never a command; nothing in step 7 launches a program. "
            "stdio is scheduled behind containment, not supported."
        ),
        false_state=(
            "stdio transport has shipped, so a configured MCP server can name a program "
            "to launch and the containment question is answered rather than deferred."
        ),
        # Two shapes, both taken from what the tree ACTUALLY said before the decision:
        # the data-model sketch ("`config_json` — the launch command or base URL") and
        # the plan's own "this is the one genuinely open question". A doc asserting
        # either again is a doc pointing the next agent at a column that must not exist.
        #
        # Both were TIGHTENED on 2026-08-06 when the precision half of this gate was
        # written (`test_the_claims_registry_is_silent_on_legitimate_prose`), because
        # both fired on sentences that state today's decision correctly:
        #   * "there is no launch command column, because stdio is not supported" — the
        #     denial of the field, flagged as if it were the field. Hence `(?<!no )`.
        #   * "stdio was a genuinely open question until 2026-08-06" — the history of
        #     the decision, flagged as if it were the decision. Present tense only now,
        #     the same rule the G3 polarity row above already follows; the pre-decision
        #     wording this was written for ("this IS the one genuinely open question")
        #     is still caught.
        while_true=Wrong(
            pattern=(
                r"(?<!no )launch command"
                r"|\bMCP\b[^.\n]{0,80}\bcommand to (?:run|launch)\b"
                r"|(?:transport|stdio)[^.\n]{0,60}\b(?:is|remains|stays)\s+"
                r"(?:still\s+)?(?:an?\s+)?open question"
                r"|\b(?:is|are|remains?|stays)\s+(?:still\s+)?(?:the\s+|an?\s+)?"
                r"(?:one\s+)?genuinely open question"
            ),
            fix=(
                "Transport was answered on 2026-08-06: HTTP only, so an mcp_servers row "
                "stores a URL and there is no launch-command field. Amend the sentence, or "
                "delete it and link to docs/step-7-mcp-plan.md §5, which owns the decision. "
                "If stdio has genuinely shipped, flip MCP_TRANSPORT_HTTP_ONLY in "
                "tests/doc_claims.py in the SAME commit."
            ),
        ),
        while_false=Wrong(
            pattern=r"HTTP only for v1|no stdio|url, never a command|URL and never a command",
            fix=(
                "stdio has shipped — this line still says the transport is HTTP only. Amend "
                "it, or link to docs/step-7-mcp-plan.md §5, which owns the transport decision."
            ),
        ),
        exempt=FROZEN,
    ),
    # -- MCP admission: dev-only, so SAFE admits nothing ------------------
    Claim(
        id="mcp-is-dev-only-in-v1",
        owner="docs/step-7-mcp-plan.md",
        holds=MCP_IS_DEV_ONLY_IN_V1,
        true_state=(
            "MCP is DEV-ONLY for v1 (owner decision 2026-08-06). No MCP tool enters "
            "visible_tools(SAFE); what SAFE would ever admit is deferred, not answered. "
            "Deferring it is what unblocked step 7."
        ),
        false_state=(
            "SAFE admission has been decided and built, so a document may state which "
            "MCP tools the companion admits."
        ),
        # The exact pre-decision phrasing, in the five shapes the tree carried it —
        # prefer these over an attempt at English. Every one pairs SAFE ADMISSION with
        # "undo-able", and that pairing is what makes the rule precise: a first draft
        # matched SAFE-admits-only on its own and immediately flagged two true WIDGET
        # sentences ("SAFE admits only the non-destructive set"), which is the
        # cries-wolf failure the module docstring warns about. Anchored on the
        # admission verb so it never fires on invariant 2's TRUE statement that a
        # no-undo tool is kept OUT of that view.
        while_true=Wrong(
            pattern=(
                r"SAFE[^.\n]{0,80}\badmits?\s+only[^.\n]{0,60}undo-able"
                r"|\bin SAFE only[^.\n]{0,80}undo-able[^.\n]{0,40}admitted"
                r"|SAFE\s+read-only\s*/\s*undo-able only"
                r"|constrained to read-only or genuinely\s+undo-able"
                r"|read-only or genuinely\s+undo-able only"
            ),
            fix=(
                "MCP is dev-only for v1, so SAFE admits NOTHING from a server and the "
                "constraint was deferred rather than chosen. Amend the sentence, or delete "
                "it and link to docs/step-7-mcp-plan.md §1, which owns the decision. If "
                "SAFE admission has genuinely shipped, flip MCP_IS_DEV_ONLY_IN_V1 in "
                "tests/doc_claims.py in the SAME commit."
            ),
            # Prose that states the deferral in the same breath is the correct shape.
            excused_by=r"dev-only|Developer-only|deferred|defers",
            window=400,
        ),
        while_false=Wrong(
            pattern=r"no MCP tool enters the SAFE view|MCP is (?:dev|Developer)-only for v1",
            fix=(
                "SAFE admission has shipped — this line still says MCP is dev-only. Amend "
                "it, or link to docs/step-7-mcp-plan.md, which owns the admission rule."
            ),
        ),
        exempt=FROZEN,
    ),
    # -- MCP dispatch: nothing a server offers is callable yet -------------
    Claim(
        id="mcp-tools-are-not-callable",
        owner="docs/step-7-mcp-plan.md",
        holds=MCP_TOOLS_ARE_NOT_CALLABLE,
        true_state=(
            "Step 7 phase 2 (2026-08-07) can SEE what a tool server offers and can run "
            "none of it. An mcp: id is absent from visible_tools(mode) in every mode, and "
            "both dispatch paths refuse one. Dispatch is phase 3."
        ),
        false_state=(
            "Phase 3 has shipped, so an MCP tool really is invoked through the gate and a "
            "document may describe Addison using one."
        ),
        # The offence is a document promoting "can see" to "can use" — asserted of
        # Addison, in the present tense. Anchored on the SUBJECT (Addison, not a
        # server and not a person) plus a use-verb, because "you can use a tool
        # server to..." and "a server's tools are used by its own author" are both
        # ordinary sentences that say nothing about this phase. The `run`/`call`
        # halves are the shapes phase 3's own prose will use, which is the point:
        # they must not appear until it ships.
        while_true=Wrong(
            pattern=(
                r"Addison\s+(?:can|does|will)\s+(?:now\s+)?(?:use|run|call|invoke)\s+"
                r"(?:a\s+|the\s+|its\s+|those\s+|these\s+|their\s+)?"
                r"(?:tool[- ]server|MCP)[^.\n]{0,20}\btools?\b"
                r"|MCP tools? (?:are|is) (?:now )?(?:callable|dispatched|runnable)"
            ),
            fix=(
                "Phase 2 discovers tools and runs none of them: an mcp: id is kept out of "
                "visible_tools in every mode and refused at both dispatch paths. Amend the "
                "sentence to say Addison can SEE what a server offers, or link to "
                "docs/step-7-mcp-plan.md §4.2, which owns the phase. If phase 3 has "
                "genuinely shipped, flip MCP_TOOLS_ARE_NOT_CALLABLE in tests/doc_claims.py "
                "AND mcp_catalog.MCP_TOOLS_ARE_CALLABLE in the SAME commit."
            ),
            # Prose that states the limit in the same breath is the correct shape —
            # "Addison can't use those tools yet", "it still can't run them".
            excused_by=r"can't|cannot|not yet|isn't callable|is not callable|phase 3",
            window=200,
        ),
        # The mirror image, and since 2026-08-07 the ACTIVE side. A document still
        # promising that nothing a tool server offers can run is the more dangerous
        # of the two directions now: it is a reassurance about a stranger's code,
        # and an agent that believes it will not think to check what a call does.
        #
        # RECOUNTING THE OLD STATE IS NOT THE OFFENCE — the plan and the build log
        # both have to say what phase 2 shipped, quoting the row copy it shipped
        # with. The excuse is therefore "a phase is named nearby": prose that says
        # which phase it is describing is a record, and prose that asserts the limit
        # with no phase in sight is a stale promise.
        while_false=Wrong(
            pattern=(
                r"nothing (?:a|the) (?:tool )?server offers is callable"
                r"|Addison can see this tool but can't use it yet"
                r"|no MCP tool is callable"
                # "it still can't use any of those tools yet" — the panel's and the
                # roadmap's old phrasing. ANCHORED ON "yet", which is the tell of a
                # not-yet-shipped promise: without it, "you can't run any of them
                # without approving the card" is an ordinary true sentence about the
                # gate, and a rule that flags it is a rule somebody switches off.
                r"|(?:can't|cannot) (?:use|run|call) any of (?:those|these|it|them)"
                r"[^.\n]{0,14}\byet\b"
            ),
            fix=(
                "Phase 3 shipped on 2026-08-07 — this line still says nothing an MCP "
                "server offers can run. Say that Addison asks before each use (which is "
                "what protects the person now), or name the phase you are recounting, or "
                "link to docs/step-7-mcp-plan.md §4.3, which owns dispatch. If dispatch "
                "has genuinely been withdrawn, flip MCP_TOOLS_ARE_NOT_CALLABLE in "
                "tests/doc_claims.py AND mcp_catalog.MCP_TOOLS_ARE_CALLABLE in the SAME "
                "commit."
            ),
            # NAMING A PHASE is the excuse, and nothing weaker. A bare "used to"
            # was tried first and it forgave a genuine offender out of a sentence
            # 44 characters away about an unrelated branch warning that "used to
            # live here" — an excuse loose enough to be satisfied by ordinary prose
            # is an excuse that switches the rule off wherever the tree is chatty.
            #
            # ``\s+`` rather than a literal space: a line wrap between the two words
            # is otherwise how "until phase 3" stops being an excuse, and a rule
            # that depends on where a paragraph happened to wrap is unpredictable.
            # ``phase\s+[12]`` does NOT match this repo's "Phase-2 step 7" (the
            # hyphenated project phase, a different thing entirely).
            excused_by=r"phase\s+[12]\b|until\s+phase\s+3|used\s+to\s+(?:say|read|print)",
            window=200,
        ),
        exempt=FROZEN,
    ),
    # -- Redaction: a backstop, never elimination --------------------------
    Claim(
        id="redaction-is-a-backstop",
        owner="agent_core/redaction.py",
        holds=REDACTION_IS_A_BACKSTOP,
        true_state=(
            "Redaction is a pattern matcher over text: a BACKSTOP, not a boundary. A "
            "secret in a format nobody enumerated passes, and so does an enumerated one "
            "split by a newline, tab, quote or backslash, or written in fullwidth "
            "characters (docs/KNOWN-GAPS.md owns those two). It reduces exposure and "
            "does not eliminate it."
        ),
        # Anchored on a UNIVERSAL plus a credential noun plus a removal verb, in the
        # four shapes an over-claim actually takes. Deliberately silent on the honest
        # forms this doc set already writes — "the credential shapes it knows", "the
        # passwords and keys Addison recognises" — because those carry no universal at
        # all, which is the whole difference. It is also anchored away from G1: "keys
        # never reach the webview" is a floor about Addison's OWN keys, enforced by
        # storage rather than by a regex, so every branch below requires a redaction
        # verb or the model/provider as the destination.
        while_true=Wrong(
            pattern=(
                r"redact\w*[^.\n]{0,60}\b(?:every|all|any)\b[^.\n]{0,40}"
                r"(?:credential|secret|key|password|token)"
                r"|\b(?:every|all)\s+(?:\w+\s+){0,2}"
                r"(?:credential|secret|key|password|token)s?\b"
                r"[^.\n]{0,50}\b(?:redact|strip|scrub|remov)"
                r"|\b(?:no|never any)\s+(?:credential|secret|key|password|token)s?\b"
                r"[^.\n]{0,60}\breach(?:es)?\b[^.\n]{0,20}(?:model|provider)"
                r"|redact\w*[^.\n]{0,50}\b(?:ensures?|guarantees?|prevents?)\b"
            ),
            fix=(
                "Redaction is a backstop, not a boundary — say what it removes (the "
                "shapes somebody enumerated) rather than what it prevents. "
                "agent_core/redaction.py's docstring owns the rule and states it in "
                "those words; docs/KNOWN-GAPS.md owns the two shapes a listed "
                "credential can still take. If a document needs the strong sentence, it "
                "needs a different control."
            ),
            # Prose that states the limit in the same breath is the correct shape, and
            # it is how every honest passage in the tree already reads.
            excused_by=(
                r"backstop|not a boundary|does not eliminate|passes untouched|"
                r"reduces exposure|enumerat|it knows|recognis|nobody has (?:listed|"
                r"enumerated)|KNOWN-GAPS"
            ),
            window=400,
        ),
    ),
    # -- The retired scope amendment ---------------------------------------
    Claim(
        id="retired-amendment-has-no-precedence",
        owner="docs/README.md",
        true_state=(
            "The 2026-07-20 scope amendment was folded into the specs and retired "
            "2026-07-27. It is minutes, not law, and settles nothing."
        ),
        # It kept a "where we differ, the amendment wins" rule long after its content
        # had been merged, and the engineering spec printed a second copy of the same
        # rule — so every reader replayed a merge that had already happened, and six
        # false statements in this doc set traced back to that chain. Prose DESCRIBING
        # the retired rule is fine and expected; prose ASSERTING it is the regression.
        while_true=Wrong(
            pattern=(
                r"(?:the )?amendment (?:wins|governs|overrides)"
                r"|where it and the two specs differ"
                r"|amendment (?:is )?authoritative"
            ),
            fix=(
                "Delete the precedence claim. The amendment was retired 2026-07-27 and its "
                "content already lives in the specs — cite those. If you are describing the "
                "retired rule rather than applying it, say so in the same sentence."
            ),
            # Prose that talks ABOUT the retired rule rather than applying it.
            excused_by=(
                r"used to|no longer|retired|reverse of|the rule that|kept a|which said|"
                r"it kept|kill the|kills the|kept its own|kept the"
            ),
            quotation_is_citation=True,
        ),
    ),
    # -- The dev-signing mechanism -----------------------------------------
    Claim(
        id="live-dev-signing-script",
        owner="docs/CONVENTIONS.md",
        true_state=(
            "`shell/src-tauri/sign-and-run.sh` is the live dev-signing mechanism — a cargo "
            "runner that signs each build with an EXPLICIT designated requirement. "
            "`scripts/sign-dev-binary.sh` is its superseded manual predecessor and does not "
            "set that requirement, so reaching for it does not fix keychain prompts."
        ),
        # This one drifted in the worst possible place: CONVENTIONS.md pointed a reader
        # at the superseded script AND asserted a property that script no longer
        # produced. Same "defer to the owner" idiom as G3's guarantee — a mention is
        # fine, an unqualified one is the bug.
        while_true=Wrong(
            pattern=r"sign-dev-binary(?:\.sh)?",
            fix=(
                "Name `sign-and-run.sh` beside it, or mark the mention as history. "
                "`scripts/sign-dev-binary.sh` is the superseded manual predecessor — it does "
                "NOT set the designated requirement, so it is not the fix for keychain "
                "prompts. docs/CONVENTIONS.md's environment section owns this."
            ),
            excused_by=r"sign-and-run|supersed|predecessor|manual|one-time|history",
            window=400,
        ),
        # The scripts too, and this is not belt-and-braces. The worst copy of this
        # drift was never in a .md file — it was in `scripts/sign-dev-binary.sh`'s own
        # header, which told a reader to re-run it after every rebuild and printed
        # "it will survive the next rebuild" on success. A header beside the code it
        # governs is the form this repo trusts MOST, so an unqualified mention there
        # outranks any document that disagrees with it.
        globs=("*.md", "scripts/*.sh", "shell/src-tauri/*.sh"),
    ),
    # -- The gate list -----------------------------------------------------
    Claim(
        id="gate-list-owner",
        owner="scripts/gates.sh",
        true_state=(
            "`scripts/gates.sh` IS the gate list — one executable definition, called by "
            "`.github/workflows/ci.yml` and by people. A list of gates in prose is a claim, "
            "and on 2026-08-06 three prose copies disagreed with each other and with CI."
        ),
        # Scoped to fenced code blocks on purpose: prose mentioning `npm run typecheck`
        # while describing a closed gap is not the offence. A COMMAND BLOCK that runs
        # gate tools is, unless the block points at the script.
        while_true=Wrong(
            pattern=(
                r"\b(?:pyright|eslint|ruff check|cargo clippy|cargo test|tsc --noEmit"
                r"|npm run (?:lint|typecheck)|pytest)\b"
            ),
            fix=(
                "Do not restate the gates. Replace the block with `./scripts/gates.sh` "
                "(or `... python|frontend|rust`), which is the one executable definition CI "
                "also runs — a prose copy is how CI and the docs came to disagree."
            ),
            excused_by=r"gates\.sh",
            window=600,
            in_code_fence=True,
        ),
    ),
    # -- step 8: arming exists, and it needs a typed code -------------------
    Claim(
        id="automation-arming-built",
        owner="docs/step-8-automation-plan.md",
        holds=AUTOMATION_ARMING_BUILT,
        true_state=(
            "Addison can arm an automation (arm_automation, phase 3, 2026-08-07), "
            "behind a per-automation code the person retypes."
        ),
        false_state=(
            "Nothing in the tree can arm automation — the keyword gate and the "
            "arming surface do not exist."
        ),
        # The phrasings that were true through phase 2 and are now false. Written
        # against the sentences ACTUALLY in the tree before this commit, which is
        # the lesson the previous row's first draft taught (it caught one of four).
        # Past tense is left alone: BUILD-LOG and the plan recount those phases.
        while_true=Wrong(
            pattern=(
                r"nothing (?:\w+ ){0,3}can (?:arm|run) (?:an? )?automation"
                r"|no arming surface"
                r"|arming (?:is|remains) (?:phase 3 and )?not built"
                # `(designed, **not built**: step 8 phase 3)` — the markdown bold
                # split the phrase, so the alternative above walked straight past
                # CLAUDE.md's G2 floor for a whole phase. Match the parenthetical
                # shape too, punctuation and emphasis included.
                r"|not built\*{0,2}:?\s*(?:it is )?(?:phase-?2 )?step 8"
                r"|(?:keyword gate|nonce)[^\n]{0,40}(?:does not exist|is not built)"
                r"|there is no keyword-gate code"
                r"|arming (?:does not exist|doesn't exist)"
            ),
            # A document RECOUNTING the phase where this was true is doing its job.
            excused_by=(
                r"used to|until phase 3|before phase 3|through phase [12]|no longer|"
                r"superseded|historical|standing claim|at the time|by absence|"
                # A RECOUNT quoting the retired sentence — `floor said`, `it said
                # "…"`, `it now says` — is a document recording that it changed.
                # Bare \bsaid\b was measured at 3.2% of the corpus and excused
                # unrelated prose ("what the SERVER said"); anchoring it to the
                # subject doing the quoting costs about a third of that. (`used to
                # say` needs no branch: `used to` above already matches it.)
                r"floor said|wording said|it said|it now says"
            ),
            window=120,
            fix=(
                "Arming shipped in step 8 phase 3 (arm_automation installs a launchd "
                "job through the shell, behind a typed per-automation code). Say that "
                "instead, and link to docs/step-8-automation-plan.md, which owns the "
                "phase order. If the FACT changed, flip AUTOMATION_ARMING_BUILT in "
                "tests/doc_claims.py in the SAME commit."
            ),
        ),
        while_false=Wrong(
            pattern=(
                r"arm_automation (?:installs|arms|exists|shipped)"
                r"|arming (?:shipped|exists|is built)"
            ),
            fix=(
                "Arming does not exist in this tree. Remove the claim, or flip "
                "AUTOMATION_ARMING_BUILT in tests/doc_claims.py if it shipped again."
            ),
        ),
        exempt=FROZEN,
    ),
    # -- Phase 3: packaging AND the Developer review surface ----------------
    Claim(
        id="phase-3-includes-the-review-surface",
        owner="docs/phase-3-review-surface-plan.md",
        holds=PHASE_3_INCLUDES_THE_REVIEW_SURFACE,
        true_state=(
            "Phase 3 has been TWO tracks since 2026-07-25: packaging / signing / "
            "notarisation / the updater / binary restore / Secure-Enclave identity, AND "
            "the Developer review surface. A definition that stops at packaging is the "
            "drift this repo has already shipped twice."
        ),
        false_state=(
            "The review surface is no longer part of Phase 3, so packaging is the whole "
            "phase again."
        ),
        # Anchored on a DEFINITION of the phase — `Phase 3`, a separator, then the
        # packaging list — and never on packaging being named as one item inside it.
        # "It is a Phase-3 packaging concern" and "Packaging/signing/updater = Phase 3"
        # are both true sentences about a single item, both already in the tree, and
        # both stay silent: that distinction is what decides whether this row survives
        # its first month. Single-line by construction (`[^.\n]`), because the shapes
        # that wrap a line — HANDOFF's "**Phase 3**\n(packaging, signing, …)" — always
        # name the second track in the same breath anyway.
        #
        # PRESENT TENSE ONLY, the same rule the G3 and artifact rows follow: the plan
        # and the build log both have to say what the phase MEANT before 2026-07-25,
        # and rewriting those recounts would delete the reasoning. So `means` is in the
        # verb list and `meant` is deliberately not — a tense is a cheaper and more
        # honest discriminator here than an excuse phrase, which is what the MCP rows
        # learned when a loose excuse switched a rule off wherever the tree got chatty.
        while_true=Wrong(
            pattern=(
                r"Phase[ -]3\b[^.\n]{0,40}?"
                r"(?:—|–|:|=|\bis\b|\bmeans?\b|\bas\b)"
                r"\s*[^.\n]{0,30}?packaging"
            ),
            fix=(
                "Phase 3 stopped being packaging-only on 2026-07-25 — it also carries the "
                "Developer review surface (file tree, read-only viewer, a diff of "
                "Addison's still-live edits, per-file revert). Name the second track, or "
                "link to docs/phase-3-review-surface-plan.md, which owns the "
                "redefinition. If the surface has genuinely been dropped from the phase, "
                "flip PHASE_3_INCLUDES_THE_REVIEW_SURFACE in tests/doc_claims.py in the "
                "SAME commit."
            ),
            # Prose that names the second track, or points at the plan, is the correct
            # shape — and it is how every honest passage in the tree already reads.
            excused_by=r"review surface|review-surface|phase-3-review-surface-plan",
            window=500,
        ),
        while_false=Wrong(
            pattern=(
                r"Phase[ -]3[^.\n]{0,60}review surface"
                r"|review surface[^.\n]{0,60}Phase[ -]3"
            ),
            fix=(
                "The review surface is no longer part of Phase 3 — this line still puts "
                "it there. Amend it, or link to docs/phase-3-review-surface-plan.md, "
                "which owns what the phase contains."
            ),
        ),
        exempt=FROZEN,
    ),
)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


# The line every work order ends on, held once so no row can forget it.
#
# WHY. A row's `true_state` is a hand-written constant, not a reading of the tree —
# nothing in this file verifies that what a row calls true IS what its owner
# document says. A subtly wrong row is therefore worse than no row at all: it fails
# the build on correct prose and hands the next agent a file:line telling them to
# write the wrong sentence, with all the authority of a green-except-this gate. The
# eight rows here were read against their owners on 2026-08-06 and one was corrected
# (`g3-guarantee-defers-to-owner` asserted a copy count the tree never had), which is
# exactly the rate that makes this sentence worth printing every time.
THIS_ROW_MAY_BE_WRONG = (
    "or   : this row is wrong. `true` above is a constant in tests/doc_claims.py, "
    "not a reading of the tree — check {owner} before you rewrite anything, and "
    "correct the row if the owner disagrees with it."
)



@dataclass(frozen=True)
class Offender:
    claim: Claim
    path: str
    line_no: int
    line: str

    def work_order(self) -> str:
        wrong = self.claim.active()
        assert wrong is not None
        return (
            f"{self.path}:{self.line_no}  — claim `{self.claim.id}` "
            f"(owner: {self.claim.owner})\n"
            f"    says : {self.line[:150]}\n"
            f"    true : {self.claim.state()}\n"
            f"    do   : {wrong.fix}\n"
            f"    {THIS_ROW_MAY_BE_WRONG.format(owner=self.claim.owner)}"
        )


@dataclass
class _Fences:
    """Character spans covered by ``` fenced code blocks, per file."""

    spans: list[tuple[int, int]] = field(default_factory=list)

    def contains(self, pos: int) -> bool:
        return any(lo <= pos < hi for lo, hi in self.spans)


def _fences(text: str) -> _Fences:
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for match in re.finditer(r"^\s*```", text, re.M):
        if open_at is None:
            open_at = match.end()
        else:
            spans.append((open_at, match.start()))
            open_at = None
    return _Fences(spans)


def _is_quoted(line: str, start: int) -> bool:
    """True when the match sits inside double quotes — the phrase is being CITED.

    Cheap parity check: an odd number of quote marks before the match means it
    opened a quoted span that has not closed.
    """
    return line.count('"', 0, start) % 2 == 1


def findings_in_text(claim: Claim, text: str, rel: str = "<sample>") -> list[Offender]:
    """Every passage in ``text`` that contradicts ``claim``.

    Split out from `offenders_for` so the rule can be run over a SAMPLE and not
    only over the tree — a gate needs both halves, and the precision half is only
    honest if it exercises this function rather than a copy of it
    (`tests/gate_precision.py` owns that argument).
    """
    wrong = claim.active()
    if wrong is None:
        return []
    pattern = re.compile(wrong.pattern, re.I)
    excused = re.compile(wrong.excused_by, re.I) if wrong.excused_by else None

    lines = text.split("\n")
    fences = _fences(text) if wrong.in_code_fence else None
    found: list[Offender] = []
    for match in pattern.finditer(text):
        if fences is not None and not fences.contains(match.start()):
            continue
        line_no = text.count("\n", 0, match.start()) + 1
        line = lines[line_no - 1]
        line_start = match.start() - (text.rfind("\n", 0, match.start()) + 1)
        if wrong.quotation_is_citation and _is_quoted(line, line_start):
            continue
        if excused is not None:
            scope = (
                line
                if wrong.window == 0
                else text[max(0, match.start() - wrong.window) : match.end() + wrong.window]
            )
            if excused.search(scope):
                continue
        found.append(Offender(claim, rel, line_no, line.strip()))
    return found


def offenders_for(claim: Claim) -> list[Offender]:
    """Every passage in the tree that contradicts ``claim``."""
    found: list[Offender] = []
    seen: set[tuple[str, int]] = set()
    for glob in claim.globs:
        paths = markdown_files() if glob == "*.md" else sorted(REPO.glob(glob))
        for path in paths:
            rel = path.relative_to(REPO).as_posix()
            if rel in claim.exempt:
                continue
            for offender in findings_in_text(claim, path.read_text(), rel):
                if (rel, offender.line_no) in seen:
                    continue
                seen.add((rel, offender.line_no))
                found.append(offender)
    return found
