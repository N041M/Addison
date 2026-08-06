# Addison documentation — who owns what

**One rule: every topic has exactly one owner. A second mention is a link, never a
copy.**

That rule exists because this doc set broke it repeatedly and the cost was
measurable. Before 2026-07-27, G3 and G4 each appeared in thirteen of nineteen
markdown files; a three-document precedence chain (`design doc` → `spec` →
`amendment`, plus inline overrides) meant establishing one fact could take four
documents and three precedence rules applied in order; and a single week's passes
found ten statements that were false — every one of them correct when written and
falsified by a change that never touched its file.

`tests/test_docs_drift.py` now enforces the mechanical part.

---

## Authoritative — read these to find out what is true

| File | Owns |
|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | The rules for the code, in short form. Auto-loaded every session, so it stays small. |
| [`SAFETY.md`](SAFETY.md) | The four floors, the two policy modes, the Custom guards, the snapshot/restore subsystem, the SAFE invariants. |
| [`addison-engineering-spec.md`](addison-engineering-spec.md) | Build brief — schema, IPC, subsystem design. |
| [`addison-design-doc.md`](addison-design-doc.md) | Product and UX rationale: *why* it is shaped this way, and who for. |
| [`../ROADMAP.md`](../ROADMAP.md) | Status, and **only** status. What is built, next, and deliberately not being built. |
| [`KNOWN-GAPS.md`](KNOWN-GAPS.md) | Every live issue, deferral and open design question. Nothing else keeps a list. |
| [`CONVENTIONS.md`](CONVENTIONS.md) | How work is done here: the standard, the conventions, the environment. |

## Process

| File | Owns |
|---|---|
| [`VERIFICATION.md`](VERIFICATION.md) | Gates, live-driver end-to-end proofs, the safety-critical diff review. |
| [`TESTING-CHECKLIST.md`](TESTING-CHECKLIST.md) | The manual desktop pass — every "open the app and look" step. |
| [`test-hardening-plan.md`](test-hardening-plan.md) | The mutation-proof standard and the open test-hardening items. |

## Reference views of the code

These are hand-maintained and mirror real code, so they drift by construction. Treat
them as maps, and check the tree when a detail matters.

| File | Owns |
|---|---|
| [`architecture.md`](architecture.md) | The three processes and their trust boundaries. |
| [`classes.md`](classes.md) | Class diagrams for orchestration, providers, routines. |
| [`data-model.md`](data-model.md) | The SQLite schema as ER diagrams. Coverage is test-enforced against `schema.sql`. |
| [`flows.md`](flows.md) | Runtime sequence flows across the process boundaries. |

## Plans

`ROADMAP.md` owns whether a plan is scheduled; this table says only where each one
stands relative to the tree.

| File | Status |
|---|---|
| [`step-5.5-containment-plan.md`](step-5.5-containment-plan.md) | **COMPLETE.** Written 2026-07-26, all five items shipped 2026-07-31. Kept as the record of what was planned and why — its body is written in the present tense of 2026-07-26. |
| [`secrets-and-keychain-plan.md`](secrets-and-keychain-plan.md) | **PROPOSED, not scheduled** (2026-07-31). Repair-first plan for the keychain integration, with the encrypted vault kept as a destination behind named triggers. Owner decisions in its §14. |
| [`step-7-mcp-plan.md`](step-7-mcp-plan.md) | **PROPOSED, not started** (2026-08-06). Build order for the MCP *client*, unblocked by the dev-only-for-v1 decision. Two decisions still open in its §5 — transport first, everything else follows from it. |
| [`phase-3-review-surface-plan.md`](phase-3-review-surface-plan.md) | Approved 2026-07-25, not started. Blocked on steps 6, 7 and 8. |

## Design

| Path | What |
|---|---|
| [`design-brief-dark/`](design-brief-dark/) | **Authoritative.** The v4 dark direction — the designer's reference plus `IMPLEMENTATION.md`, which records the binding prototype→app mapping. |
| [`design-brief-fern/`](design-brief-fern/) | Superseded v3. Kept as history; do not build from it. |

## History — do not cite to settle a question

| File | What |
|---|---|
| [`addison-scope-amendment-2026-07.md`](addison-scope-amendment-2026-07.md) | The July 2026 scope change. Folded into the documents above and **retired 2026-07-27**. Kept for the motivating story and the dated owner decisions — minutes, not law. |
| [`HANDOFF.md`](HANDOFF.md) | Session handoff. Expected to go stale and be rewritten; holds nothing durable. |
| [`BUILD-LOG.md`](BUILD-LOG.md) | Per-step record. The "what shipped" halves are superseded by ROADMAP and git; **the rigor-pass findings are not** — each describes a way this code has actually been wrong. |

---

## If you are about to write documentation

1. **Find the owner in the tables above and write there.** If your sentence belongs
   in two files, it belongs in one and a link.
2. **A commit that changes a documented rule amends the doc in the same commit.**
   This project has shipped the opposite three times, once by re-adding the sentence
   its own changeset falsified.
3. **Do not write a number you are not prepared to maintain.** Test counts and line
   counts went stale twice in a day here. A stale number reads as a claim.
4. **State what is not true yet.** A floor the code does not enforce should say so,
   in the owner's file and nowhere else — `SAFETY.md` carried G3's OPEN-mode
   overclaim in plain words for the five days it was true, and states the two edges
   the floor still does not reach now that step 5.5 has closed it. The repo must not
   carry a guarantee its own tests do not cover.
5. **A caveat is as perishable as the claim it qualifies.** When the underlying fact
   flips, every copy of the caveat becomes a lie in the other direction, and a gate
   that only checks *"is the caveat present"* stays green through it. Give the fact
   one owner, point the copies at it, and let
   `test_docs_drift.py`'s `G3_RESOLVED_IN_OPEN` show what that looks like in a test.
