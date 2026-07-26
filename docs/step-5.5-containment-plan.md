# Phase-2 step 5.5 — containment for the OPEN harness

**Status: PROPOSED, not started. Written 2026-07-26.**

Step 5 shipped a shell. The compensating boundary that the design doc's security
model assumes did not ship with it. This plan pays that debt, and it belongs
*adjacent to step 5* rather than appended after step 8, because it is that step's
unfinished half — not a new feature.

Read [`addison-design-doc.md` §9](addison-design-doc.md) first, then this.

---

## Context — this is a reconciliation, not new scope

Design-doc §9 lists four mitigations. The first one is:

> **Capability allow-list, not a shell.** Tools are individual typed functions
> (`read_file(path)`, `web_search(query)`), not "run arbitrary command." This
> eliminates most of the attack surface OpenClaw explicitly warns about … "endless
> supply of security flaws" tracks directly back to broad shell/computer-control
> access.

The **second** bullet in that same list was amended when step 5 broke it, and the
amendment is the model to follow:

> **Amended 2026-07-24** … the two OPEN-only file tools scope by **trusted root**
> instead … The property the picker was protecting is **preserved rather than
> dropped, because the boundary is still enforced at the process edge**: the core
> hard-refuses an out-of-root path, **and the Rust shell independently refuses
> Addison's own data directory**.

That is the correct discipline: name the property the removed control protected,
and re-establish it somewhere else. Bullet 2 did it. **Bullet 1 did not.**
`run_command` shipped, the "not a shell" rationale was left standing, and the
property it protected — *the model cannot issue an unbounded OS effect* — is today
protected by nothing but a permission card.

### What the card does and does not do

The card is real and it is per-invocation: no prior grant is consulted, none is
recorded, and the exact command text is shown every time (owner decision
2026-07-24). That is stronger than most harnesses' approval model.

It is also the **only** layer. There is no second one, and the project's own
standard is that a single layer guarded by human attention is not a floor.

### The specific consequence for G3

`run_command` runs `subprocess.run(command, shell=True, cwd=os.path.expanduser("~"))`
([`agent_core/tools/run_command.py`](../agent_core/tools/run_command.py)).
`affected_path` returns `None` by design, so confinement never governs it.
`workspace_trust_allows` keeps the data dir un-trustable, but that predicate only
gates path-bounded tools.

So **one approved command deletes the entire recovery floor** — database, sidecars,
genesis row, and every `undeletable` G4 anchor. The `RAISE(ABORT)` triggers protect
rows *inside* a live database; nothing protects the file the database lives in.

CLAUDE.md currently states G3 as *"Neither the user nor the model can drive Addison
into an unrecoverable state"* and *"the restore path is itself unbreakable."* In
OPEN mode that is false. The code comment in `policy.py` is honest — it claims only
that the floor cannot be erased *"with no card."* The doc is not.

This has a precedent to follow: G4's promise was narrowed from "captures the app
binary" to "records a build reference" because **the repo must not carry a floor its
own tests do not cover.** Either the floor gets a real boundary, or the sentence
gets narrowed. This plan does the former.

---

## Bright line

**A sandbox is not a guard.** It never appears in the Custom guard panel, has no
toggle, and is not user-tunable. The panel holds *prompting* guards; a
user-disableable containment boundary would be a floor with an off switch. It
behaves like `refuse_addison_data_dir` — invisible, and not negotiable.

**The card stays.** This plan changes *blast radius*, not *prompting*. It does not
touch the owner decision that `run_command` always cards. Addison's own step-5
vocabulary already draws this distinction: **confinement is permission-to-touch and
is a predicate separate from prompting**; the gate's `trusted` bool is only
permission-to-skip-the-card. Containment is a confinement-class control.

---

## Prerequisites

None. Every item is independent of steps 6, 7 and 8, and items 1–3 are independent
of each other.

**Step 7 (MCP) is downstream of item 4.** Amendment §8.5 promises MCP tools are
*"gated, logged, undo-aware"* — and there is no log. §8.5 also leaves *"the exact
SAFE constraint (read-only only? curated allowlist? dev-only?)"* as an open question
in §13. That question must be closed before step 7, not during it: **an MCP server
self-declares whether its tool is read-only, and admitting a tool to SAFE on that
say-so breaks SAFE invariant 2 through a path the registry check cannot see.**

---

## Build

### 1. Move `run_command` behind the ShellBridge

`run_command` is the **only** tool in Addison that performs an OS effect without
crossing the ShellBridge. That contradicts engineering-spec §1.3 — *"The Agent Core
has no OS permissions of its own"* — and it is why the tool has no second
enforcement layer: the typed file tools get `refuse_addison_data_dir` in Rust for
free precisely because they cross the boundary.

Moving it is therefore an architecture correction that *also* puts execution in the
process where a sandbox can be applied. One change, two problems.

- **Protocol:** `SHELL_RUN_COMMAND = "shell.runCommand"` —
  `{command, timeoutMs} -> {stdout, stderr, exitCode, sandboxed}`. Hand-sync
  `protocol.py` / `protocol.ts` (the drift test covers it).
- **Core:** `run_command.py` drops `subprocess` entirely and calls
  `context.shell_bridge.run_command(...)`. The SAFE-mode refusal belt stays.
- **Rust:** new `exec.rs`, shaped like `filesystem.rs`. It **independently** refuses
  the data dir, exactly as `refuse_addison_data_dir` already does — defence in depth
  is the point, not redundancy.

`sandboxed` is in the response so the UI can say *"ran without a sandbox"* honestly
on a platform where no profile could be applied. **A silent unsandboxed fallback is
the failure mode to design against** — that is this project's own anti-pattern
(a guard that reports success while doing nothing).

### 2. Seatbelt profile, generated from `workspace_trust`

The profile is derived from the live trusted roots, not hardcoded:

```scheme
(version 1)
(deny default)
(allow process-exec process-fork signal)
(allow file-read*)                                   ; reads stay broad — item 3 covers exfil
(deny file-write*)
(allow file-write* (subpath "<each trusted root>"))
(deny file-write* (subpath "<data dir>"))            ; after the allows; the floor always wins
(allow file-write* (subpath "/private/tmp"))
```

Invoked as `sandbox-exec -p <profile> /bin/sh -c <command>`.

This finally makes workspace trust govern the shell. Today trust bounds the typed
file tools while `run_command` roams the whole home directory — the boundary applies
to the careful tools and not to the dangerous one.

**Two honesty notes, both of which belong in the threat model (item 5):**

- `sandbox-exec` is formally deprecated by Apple. It still works and is what Claude
  Code and Codex CLI both rely on. Acceptable; not permanent. Say so.
- Linux needs a separate Landlock/bubblewrap path. Until it exists, Linux reports
  `sandboxed: false` and the UI says so.

### 3. A hardline denylist, checked before the gate

Ship this **first** — it is hours of work and it makes G3 true against the obvious
case immediately, without waiting for the Rust work.

Addison already has the pattern: confinement hard-refuses *before the gate and
before execute* ([`orchestrator.py`](../agent_core/orchestrator.py), the CONFINEMENT
block). The denylist is a second predicate at that same site.

In `tools/base.py` (no `providers/` or `routines/` import — the module-boundary rule
holds):

```python
def call_is_forbidden(tool, args) -> str | None:
    """A refusal sentence, or None. Checked BEFORE the gate: this is not a card the
    person can approve, it is a call that does not happen."""
```

Minimum contents: any path resolving inside `_protected_dirs()`, plus `~/.ssh`,
`~/.aws`, `~/.gnupg`, `.env`.

**Scope the guarantee honestly in the docstring.** Pattern-matching a `shell=True`
string is the game #48 lost three times — `ls\nrm -rf` defeated `shlex`, and it will
be defeated again. This is a **backstop against the obvious**, not a parser. The
real boundary is item 2's `deny file-write*`, which no amount of quoting evades. If
this ships without that sentence it becomes the next thing that reads stronger than
it is.

### 4. Output redaction, and a tool-call audit log

**Redaction.** `run_command` output goes into `ToolResult` → the conversation → the
cloud provider. There is no redaction anywhere in `agent_core/` today. G1 guards
*Addison's* keys with four layers while the shell will `cat ~/.ssh/id_rsa` into an
Anthropic request on request. New `agent_core/redaction.py`, stdlib `re` only:
`sk-…`, `ghp_…`/`gho_…`, `AKIA…`, `Bearer …`, PEM private-key headers, `xox[baprs]-…`.

Two decisions to make deliberately rather than by accident:

- **Redact toward the model, not into the store.** The transcript is the user's own
  record; scrubbing it destroys evidence. That argues for the provider translators
  as the seam rather than `Conversation.append_tool_result`. Decide explicitly — it
  changes whether the user can see what leaked.
- **Never redact silently.** Substitute `[redacted: AWS access key]` so the model
  knows something was there and the user can see it happened.

**Audit log.** New `tool_audit` table: `(id, conversation_id, tool_id, detail, mode,
destructive, outcome, created_at)` where `outcome ∈ granted | denied | forbidden |
confined_out`. Written at the same orchestrator site as everything else, on every
branch including refusals; `detail` reuses the already-computed
`call_permission_detail`, so the log, the card and the Activity Panel cannot
disagree.

**It must be added to `snapshots/scope.py` as EXCLUDED, or the build fails** —
`test_capture_scope_covers_every_schema_table` forces the decision. Excluded on the
`tool_grants` precedent: an audit log is history, and a restore that rewrote the
record of what happened would be worse than no log.

This closes a real hole: `read_web_page` is LOW, so it produces no `action_snapshots`
row. **The tool most exposed to prompt injection currently leaves no persistent
record of which URLs it fetched.**

### 5. Bring design-doc §9 current

Not a new file — §9 *is* the threat model, and it is out of date.

- **Amend bullet 1** in bullet 2's own idiom: name the property, say where it is now
  enforced.
- **Add a "what this does not defend against" section.** OpenClaw documents its
  boundaries explicitly (*"one operator per gateway"*; *"if someone can modify
  Gateway host state/config, treat them as a trusted operator"*), and Claude Code
  states plainly that no system is immune. Addison's docs currently read as though
  the floors are absolute. Minimum contents: an attacker with write access to
  `~/.addison`; a user who approves a malicious command; prompt injection in OPEN
  mode; `sandbox-exec`'s deprecation; multi-user machines; and the already-tracked
  gap that **the floor protects Addison's data, not Addison's code** (a packaged
  install's `/Applications/Addison.app` is not in `_protected_dirs`).

---

## Deliberately NOT in this plan

- **Relaxing the destructive prompt.** Auto-granting non-destructive commands the
  way Hermes does inside its container is the natural payoff — Anthropic reports
  sandboxing cutting prompts by up to 84%, and `run_command` currently cards for
  `ls`. But relaxing prompts *before* the sandbox exists trades away the only
  barrier there is. Revisit after item 2 lands; it is an owner decision, not a
  consequence of this plan.
- **Untrusted-content screening** (an isolated extraction call for fetched pages).
  This is the v2 item on CLAUDE.md's do-not-pull-forward list. Noted here only
  because the deferral was written with a trigger — *"becomes load-bearing once
  free/gray-area endpoints and MCP tools are in play"* — step 4 is done and step 7
  is next, so **the trigger is arriving and the deferral needs an explicit owner
  decision rather than silent expiry.**
- **Docker-per-agent**, the OpenClaw model. Addison is a desktop app whose default
  persona is 68 years old; requiring Docker breaks the product. Seatbelt is
  invisible to that user, which is exactly why it is the right borrow.

---

## Verification

The repo standard applies: every fix carries a regression test **proven to fail when
its own line is reverted**, mutated in a scratch copy outside the repo.

The headline test, and the one this plan is judged against:

```
test_an_approved_command_cannot_delete_the_recovery_floor
```

Approve a `run_command` that targets the data dir; assert the sidecars and the
`undeletable` rows are still there afterwards. **Write it as an `xfail` now**, before
any of the build items — the same discipline used for
`test_the_addison_data_dir_can_never_be_workspace_trusted`, which existed as an
`xfail` from step 1 and was flipped live at step 5. The rule should exist before the
capability does.

Then, per item:

1. A command writing outside every trusted root fails, and the file is absent from
   disk. Mutate by deleting one `deny file-write*` line.
2. `sandboxed: false` surfaces in the UI when no profile could be applied — assert
   the honest-degradation path, not just the happy one.
3. A forbidden call never reaches the gate: assert `PermissionGate.authorize` was not
   called, not merely that the result failed.
4. A secret in tool output does not appear in the provider request body. Assert on
   the wire, not on the `ToolResult`.

---

## Owner decisions this plan surfaces

1. **G3's wording.** Land items 2–3 and keep the sentence, or narrow it now and land
   the code after? The floor is currently overclaimed either way until one happens.
2. **Redaction seam** — provider translators (preserves the transcript) vs.
   `append_tool_result` (simpler, scrubs the user's own record).
3. **Untrusted-content screening** — the v2 deferral's trigger is arriving. Pull
   forward, or restate the deferral with a new condition?
4. **`/Applications/Addison.app` in `_protected_dirs`** — currently a tracked gap and
   an explicit owner call. This plan is the natural place to close it.
5. **Sequencing against step 6.** Step 6 is companion-facing and independent; this is
   Developer-facing. They can run in parallel, or 5.5 can go first on the grounds
   that it finishes already-merged work.
