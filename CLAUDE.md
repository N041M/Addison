# CLAUDE.md

Guidance for working in this repository. Read the specs before non-trivial
work — this file is the short version, they are authoritative:

- `docs/addison-design-doc.md` — product/UX rationale (the *why*)
- `docs/addison-engineering-spec.md` — build brief
- `docs/addison-scope-amendment-2026-07.md` — **the 2026-07-20 scope amendment:
  butler identity; Developer = coding harness / Simple = companion / new Custom
  profile; the guaranteed-rollback floor (G3); widgets buildable in all modes,
  capability-gated; MCP client; routing strategies; free / no-frontier models.
  Where it and the two specs differ, the amendment wins.**

## What this is

Addison is a local-first desktop **butler** — **approachable by default and
powerful on request** (scope amendment 2026-07-20). Its default audience is
non-technical users (personas "Mira", 54, and "Petr", 68 — design-doc §5), served
by the **Simple** profile as an all-in-one **companion**. Technical users get the
opt-in **Developer** profile: a **Claude-Code-class coding-agent harness** (real
project work — read/edit files, run builds/tests, iterate) with Addison's safety +
QoL layered on. A third **Custom** profile (deep in Settings, behind extra
confirmation) lets advanced users tune the *prompting* guards — never the floors.
A profile reshapes the *surface, capability tier, and prompting* — it **never**
removes a global floor (see invariants). Simple is the default; Developer/Custom
are opt-in. When adding a capability, ask which profile/tier surfaces it — do not
leak developer affordances into Simple.

Three processes, three trust levels (spec §1.3):

- **`shell/`** — Tauri 2.x (Rust). Highest trust: OS keychain, file picker,
  updater. Supervises the Agent Core; relays IPC. Never runs model instructions.
- **`agent_core/`** — Python 3.12. Orchestration loop, tool registry, permission
  gate, routines, SQLite. No OS permissions of its own — every filesystem/OS
  effect goes back through the shell via IPC.
- **`shell/src/`** — React + TS frontend. Lowest trust: renders state, never sees
  API keys, never touches the network directly.

Shell ↔ Core talk over **JSON-RPC 2.0 over stdio**.

## Mode-scoped safety model (owner decision 2026-07-19, spec §8)

The safety model is **mode-scoped**. There are two policy modes, and the mode is
**derived 1:1 from the active Profile** — the profile is the single source of
truth, there is no separately-persisted mode (`agent_core/policy.py`,
`mode_for_profile`):

- **Simple profile → SAFE mode** — today's behaviour, **byte-for-byte**. Every
  SAFE-MODE invariant below holds.
- **Developer profile → OPEN mode** — "nearly completely open." OPEN mode
  **relaxes** the SAFE-mode invariants as follows: real command execution exists
  (the `run_command` **dev-only** tool, `tools/run_command.py`); a `dev_only` tool
  may register at HIGH **without** an `undo()`; routines and widgets may carry a
  `command` step/kind; and the permission gate **auto-allows non-destructive
  actions, prompting ONLY for destructive ones**. "Open" means *fewer prompts, not
  no gate* — the gate still runs (and logs) on every call.
- **Custom profile → a user-tuned surface** (scope amendment 2026-07-20; deep in
  Settings, behind extra confirmation). The user may loosen/tighten the *prompting*
  guards (per-invocation destructive card, auto-grant scope, the workspace-trust
  boundary, keyword-gate strictness) — **never** the global floors. Turning any
  guard OFF and saving mints an **undeletable snapshot anchor** (which records the
  app build it was minted on — see G4), so weakening safety always leaves a
  guaranteed way back.

Organizing principle (amendment): **reversible data/config** (endpoints, models,
guards, skills, widgets, routines — all snapshotted and one-action reversible) vs.
**inviolable machinery** (Addison's code and the global floors, never alterable by
user or model). The apparent "users can reconfigure Addison" / "users can't break
Addison" tension resolves here: everything a user or the model can change is
reversible config sitting on the rollback floor (G3).

**Destructive-prompt rule (OPEN mode).** The gate auto-grants a call iff it is
non-destructive; destructive calls raise a permission card **per invocation** —
no prior grant is consulted and none is recorded, so approving one destructive
command never silently authorizes a later one (same or different), whether it
arrives directly, via a routine command step, or via a command widget. The card
carries the exact command text (truncated ~120 chars) so the user knows precisely
what they are approving each time; a "Not now" is honoured for the rest of the
turn (don't-nag), then cleared. Destructiveness is per-call
(`tools/base.call_is_destructive`): `run_command` classifies its own command via a
conservative read-only allowlist (see its docstring); any other tool is
destructive iff its tier is HIGH. Normal (non-dev) tools keep the coarse
session-grant model in both modes — per-invocation is specific to destructive dev
actions.

**Artifact hiding.** Routines/widgets created in OPEN mode (`created_in_mode`
column) are **hidden and disabled in SAFE mode** — never listed, never runnable —
and return **untouched** when Developer mode is active again. Switching modes is
always allowed. **Snapshots are the one exception and it is not negotiable — see
"Snapshots are never hidden by mode" below.**

**Four GLOBAL floors never relax, in ANY mode** (flag any conflict rather
than working around it silently):

- **G1 — API keys never reach the frontend/webview or SQLite.** They live in the
  OS keychain, read by the Rust shell / Agent Core only at the moment of use,
  never persisted in Agent Core memory beyond one request, never in SQLite. The
  Rust shell may hold a session-lifetime in-memory cache of provider keys (owner
  decision 2026-07-19 — one keychain read/prompt per provider per launch; evicted
  on Remove, gone at exit); the cache never widens where keys can GO (shell
  process memory only). The Setup Assistant relay's keys never exist in this
  repo's runtime — they're external and server-side. **Do not touch this
  machinery.**
- **G2 — Addison never triggers itself.** No autonomous self-triggering or
  self-scheduling, in any mode. Addison *may author* automation the OS runs (a
  launchd/cron entry, a watcher script) — like Claude Code scaffolding a cron job;
  the OS runs it, Addison never fires itself. Running/arming a powerful action
  requires a **user-typed keyword prefix** (e.g. `!run …`); because it is
  user-typed, observed/injected content can never supply it, so the prefix is also
  a prompt-injection defense. (Scope amendment 2026-07-20; supersedes the earlier
  "no scheduling in v1" wording.)
- **G3 — Guaranteed rollback (the operative meaning of "safety").** Neither the
  user nor the model can drive Addison into an unrecoverable state. App-state
  **snapshots** — automatic before any risky change, plus **on-command** — always
  allow a one-action **Restore to the last verified-working state**, and the
  restore path is itself unbreakable. Snapshots cover config/DB (settings,
  providers, models, skills, widgets, routines) and **exclude the OS keychain**
  (keys stay put — G1 holds). (New floor, 2026-07-20. **Built** in Phase-2 step 1
  — see "The snapshot / restore subsystem" below.)
- **G4 — Undeletable anchor on weakening** (≡ what the other docs call *the
  undeletable-anchor rule*; use **G4** in code, comments, and test names).
  Turning a guard OFF in Custom mode (and saving) mints a **permanent,
  undeletable** snapshot anchor that **records the app build it was minted on** —
  lowering your own protections always leaves a guaranteed way back to a working
  **configuration**. (Owner decision 2026-07-20 — this corrects the earlier
  wording, which promised the anchor "captures the app binary". What ships is a
  short build **reference** string in `binary_ref` (`{"version", "identifier"}`,
  never bytes, never a path); a restore whose build differs says so in plain
  language and changes settings only. **Restoring a previous app *binary* is not
  implemented** and is tracked as a **Phase-3 updater** item — `updater.rs` is an
  unwired stub, and a second binary-replacement mechanism inside the recovery
  floor would collide with it. The repo must not carry a floor its own tests do
  not cover; that is the anti-pattern the amendment was written against, so the
  promise was narrowed to what the code does.)

### The snapshot / restore subsystem (G3 — shipped, Phase-2 step 1)

`agent_core/snapshots/snapshot_manager.py` + the `config_snapshots` table. **Not**
the `UndoManager` beside it: `UndoManager` reverses ONE tool call
(`action_snapshots`, §4.5); this restores Addison's whole mutable **configuration**.
Complementary, independent, and they never call each other. Verbs are
**capture / restore / mint_anchor / prune** — never `record` / `undo_last`.

The single most important property, and the one every change to this code is
judged against: **restore still works when everything else is broken.** That is
why the manager imports stdlib plus two schema-mirroring leaf modules and nothing
else — no provider, no router, no profile, no policy mode, no registry, no gate;
why retention and payload version are module constants rather than settings (so
the model cannot shrink the rollback window); and why every payload is written
**twice** — into the row and into a plain JSON sidecar at
`<db_dir>/snapshots/<id>.json` (dir `0700`, files `0600`), so a damaged database
is recoverable with no SQLite at all. `snapshot.list` and
`snapshot.restoreLastWorking` are the only two RPC methods **exempt** from the
build-failure short-circuit in `main.py`: with a broken store they are answered
from the sidecars, and a restore renames the damaged file aside (never deletes it)
and rebuilds in the same session.

- **Restore is an RPC path, never a registry tool, and never passes the
  `PermissionGate`** — a gate that could deny a restore would make "the restore
  path is itself unbreakable" false. The only model-facing snapshot surface that
  will ever exist is a **LOW, capture-only** `snapshot_now` tool (step 2): it may
  only ever ADD a row, never restore and never delete.
- **What is captured** is a declared table set *and* a declared column set
  (`agent_core/snapshots/scope.py`). Tests fail the build if any schema table, or
  any column of a captured table, is neither captured nor explicitly excluded —
  because restore is replace-all, an uncaptured new column would be silently reset
  to its default **by the recovery path**. Add a Phase-2 table or column, and you
  decide there, in code.
- **Never captured:** the keychain (G1), the transcript, `usage_log`,
  `action_snapshots`, `routine_runs`, `device_identity`, `config_snapshots`
  itself, and **`tool_grants`** — live consent state, not config: restoring it
  could reinstate a grant the user had revoked, i.e. a privilege grant delivered
  by a deliberately ungated one-action button. A restore additionally clears the
  live in-session grants.
- **Permanence lives in the DATABASE.** Two `RAISE(ABORT)` triggers refuse to
  delete an `undeletable = 1` row and refuse to clear the flag — not a `WHERE`
  clause someone can forget. Three kinds of row carry it: the G4 anchor
  (`reason='guard_weakened'`, step 2) and the two possible **bottom rows**, which
  differ by how Addison arrived at this database.
- **The bottom of the restore walk is not the same row on every install.** On a
  **fresh install** it is **genesis** (`reason='genesis'`), written
  `verified_working = 1` — a brand-new install is a configuration that works — so
  the walk has a guaranteed floor from before the first turn ever runs. On an
  **upgraded install** (any database predating this subsystem: `config_snapshots`
  is empty, but the config is not) the bottom row is **`pre_upgrade`** instead,
  and it is **captured unverified**. Nothing has run against it under this
  subsystem's own eyes, and it is a copy of whatever the user has *right now* —
  up to and including the broken setup they may be about to need rescuing from.
  So it starts out unreachable by the one-action button, and there is exactly one
  way for that to change:

  **The rule (amended 2026-07-20 by `4c7ae78`, and this paragraph is the
  authority — earlier wording said the opposite).** `verified_working` means *a
  turn demonstrably answered against these exact bytes*, and nothing else.
  `mark_verified_working()` ordinarily writes a **new** `turn_verified` row. It
  flips the flag on an existing row in **one** narrowed case: a **permanent**
  (`undeletable`) row whose payload fingerprint matches the current config **byte
  for byte** (`_permanent_row_matching`). That match is evidence, not a guess —
  the turn ran against precisely that content — so a fingerprint-proven
  `pre_upgrade` **does** become a one-action target. Ordinary pre-change rows are
  never flagged after the fact, in any circumstance; widening past `undeletable`
  would make "restore lands somewhere that actually ran" false, which is the
  failure G3 exists to prevent.

  **Why this is honest rather than a weakening.** The old rule denied the flag to
  the one row retention can never prune and the triggers refuse to delete — so the
  row most worth returning to was the only row that could never be proven, however
  many turns ran against its exact contents. It did not protect the user, because
  the very next line wrote a `turn_verified` **clone holding identical bytes**,
  and the button restored that instead: the user got the same configuration either
  way, and the only difference was which row was named. Meanwhile the refusal copy
  — *"Addison never saw that one working"* — had become false in the production
  case. The two protections that actually carry the weight are untouched: (1) the
  flag still requires a **completed turn** against those bytes, and (2)
  `restore_last_working()` **skips any row whose fingerprint matches the current
  config**, so this row can never hand back the setup the user is sitting on. The
  restore copy also stays `pre_upgrade`-specific (`_RESTORED_DETAIL`), never the
  generic "last working setup" sentence, so the honesty concern above is answered
  by the copy rather than by keeping the row unprovable.

  Two consequences follow:
  - **On an upgraded install the walk still has no target until the first turn
    completes** — and after that first turn the target may be the `pre_upgrade`
    row itself. Once verified rows exist and are exhausted, the walk stops *above*
    any remaining unverified row and **names** it rather than restoring it
    (`_OLDER_IN_THE_LIST`) — the row is on the user's screen, so claiming there is
    nothing further back would be false. Note that `_OLDER_IN_THE_LIST` is now
    **rarely reached on an upgraded install**: once the permanent bottom row is
    verified, nothing sits below it and the walk ends on the honest
    `_AT_THE_BOTTOM` instead. Both branches are still correct; only the traffic
    moved.
  - **The disk arm will still apply it, as an explicitly-labelled last resort.**
    Before any verified row exists (walk outcome `'none'` — the state an upgraded
    install is in until a turn completes) `restore_last_working()` restores
    `pre_upgrade` and says exactly that: *"Addison couldn't find a setup it had
    seen working, so it went back to the most recent settings it had saved
    instead. Have a look and check things are how you want them."*
    (`_RESTORED_UNVERIFIED`). This is deliberate — see the rationale on
    `select_payload_to_restore`: *"nothing at all" is a worse answer than "the most
    recent settings I had, and I said so."* An unverified restore is never
    presented as a verified one; that dishonesty is the failure the floor was
    written against, not the restore itself.

  Which install this is is **measured, not inferred**. `main.py` checks whether
  the database file existed immediately before opening it and passes the answer
  to `SnapshotManager(created_the_database=...)`. Three outcomes, not two: `True`,
  `False`, and `None` for "couldn't find out" — and only `True` mints a verified
  `genesis`, so an unknown can never produce a permanent, undeletable restore
  point that claims to be a fresh install. An earlier heuristic inferred this
  from the config row-image and was **deleted**: it read only providers, skills,
  routines and a non-default profile, so a companion with tuned settings, widgets
  and months of chats — the ordinary state of a user who never opens Settings —
  was classified fresh, and the floor handed their broken config back under copy
  promising it had been cleared.
- **`reason` is a closed slug vocabulary** (`REASONS`), never free text — it is
  written by auto-hooks and, later, by model-orchestrated flows, and free text
  would let model-authored prose into the config store. Unknown slugs collapse to
  `other`.
- **Restore targets the last *verified-working* config, not "before the last
  edit"** — so it always lands somewhere that actually ran. A row is verified
  once a turn completed against it. `restore_last_working()` never targets a
  config identical to the present one, so **each click steps back one distinct
  proven configuration**; two bad changes deep, the user clicks twice. Retention
  is 50 snapshots / 30 days (whichever keeps more), with anchors and the newest
  **two** verified rows exempt **in the SQL** — a rule that could prune the last
  verified rows would switch G3 off with no error anywhere. Two, not one, and the
  second is not slack: the restore walk skips any verified row whose fingerprint
  matches the *current* config (restoring it would change zero bytes), so if only
  the newest verified row were exempt, the one surviving row could be exactly the
  row the walk skips — leaving the floor with no target at all.

**Snapshots are never hidden by mode (C6 — a deliberate override).**
`created_in_mode` ships on `config_snapshots`, but it is **recorded for display
only**. No list, restore, prune, or delete query may filter on it, in any mode.
The engineering spec's DDL comment said this column "mirrors existing artifact
hiding"; that phrasing was **overridden, not followed**. Taken literally it hides
the way back from exactly the user who most needs it — weakened a guard in Custom,
broke things, switched to Simple, opens Restore points and sees an empty list.
That is a larger threat to G3 than any question in the amendment's §13. Two tests
hold the line: a behavioural one (rows made in every mode restore under SAFE) and
a **source-level** one, `test_no_snapshot_query_filters_on_created_in_mode`, which
reads the SQL in `store.py` and `snapshot_manager.py` and fails if the column ever
appears in a filter position. The behavioural test alone would only prove today's
behaviour; it would not stop someone adding `AND created_in_mode = ?` next quarter.

### SAFE-MODE invariants (Simple profile — hold byte-for-byte)

These are hard constraints in SAFE mode. If a SAFE-mode request appears to
conflict with one, **flag it rather than working around it silently.** OPEN mode
relaxes exactly these four, and only as spelled out above.

1. **No arbitrary code/shell execution.** SAFE-view tools are individual typed
   functions, not "run command"; SAFE routines are *declarative plans* (§6.1),
   not scripts. Do not add `eval`, a Lua sandbox, or a raw-code field. (OPEN mode's
   `run_command` is a single **dev-only** tool, absent from the SAFE registry view
   — `registry.visible_tools(SAFE)` — and it refuses to run under SAFE as a belt.)
2. **Every `risk_tier != LOW` tool must have a real `undo()`**, enforced at
   registration in `tools/registry.py` (it raises otherwise). Do NOT satisfy this
   with a no-op `undo()` — a tool that genuinely can't be undone stays LOW and
   read-only. This registration check is the single most important test in the
   codebase (spec §9). (The ONLY exception is a `dev_only` registration, which is
   never in the SAFE view; it exists solely for OPEN mode.)
3. **A Routine never gets permissions beyond what the user granted live** — no
   privilege escalation via automation. It uses the *same* `ToolRegistry` and
   `PermissionGate` instances as the live orchestrator, in **both** modes: the
   SAFE/OPEN distinction is a *filtered view* over the one shared registry
   (`visible_tools(mode)`), never a second registry, so this no-escalation
   property survives OPEN mode intact.
4. **Widgets are capability-gated, not code — buildable in every mode (scope
   amendment 2026-07-20).** Widgets can be *built* in all modes; the mode gates
   the *capability*, not the ability to build. SAFE-tier widgets come from a
   **safe, non-destructive vocabulary** (`agent_core/widgets.py`): the launchers
   (`{kind:"routine",routineId,title}` runs a saved routine through the *existing*
   routine.run path — same registry + gate, zero new execution surface;
   `{kind:"stat",source,title}` from the fixed whitelist `tokens_month` /
   `provider_latency` / `connections`) **plus new interactive display kinds**
   (to-do/checklist, note, timer, …) rendered by *trusted Addison components* and
   backed by safe storage. Still **no eval, no arbitrary code, no raw-code/template
   field** — SAFE-1 and the webview CSP hold; a SAFE widget can never reach
   anything that harms the machine or Addison. Unknown kinds/sources are rejected
   at save and hidden at render. Higher tiers (Developer/Custom) add **code-backed
   / system-capable** widgets (today's OPEN `{kind:"command",…}`; monitors/scripts
   under workspace-trust + undo + snapshot + keyword gate). Surviving guarantee: a
   widget never exceeds its mode's tier, and SAFE widgets are non-destructive by
   construction.

## Module boundary rule (spec §2)

`agent_core/tools/`, `agent_core/providers/`, and `agent_core/routines/` **must
not import from each other**. They are all consumed by `orchestrator.py`, the
only module allowed to know about all three. This is what lets the Routine engine
replay tool calls through the exact same registry + gate as the live loop.

## Conventions

- **Python**: 3.12, stdlib-first. SQLite via `sqlite3`/SQLAlchemy Core, no heavy
  ORM. `httpx` for HTTPS. Ruff, line length 100. Dataclasses mirror the SQL
  schema 1:1.
- **Provider-agnostic orchestrator**: never branch on the concrete provider with
  `isinstance`. Handle capability differences via `ProviderCapabilities`.
- **Per-request model routing**: the orchestrator calls `model_router.resolve()`
  each turn — there is no single `self.active_provider`. Multiple roles
  (PRIMARY, LOCAL) can be configured and reachable at once (spec §4.1.1).
- **Plain language, no jargon** in any user-facing string (tool labels,
  permission cards, errors). No stack traces reach the user — errors become a
  plain message + one suggested next step.
- **UI (step 7+): distinct, non-AI look — "Fern" direction (v3, amended
  2026-07)** (design-doc §7.1). Never the generic AI-chat aesthetic (purple
  gradients, glassmorphism, sparkle/bot icons, shimmer) and never a model
  vendor's branding (no cream/terracotta, no steel blue). The direction is a
  **warm, calm "correspondence" look**: warm paper neutrals + one **fern-green**
  accent (Addison's voice, primary actions, live state — never decoration), the
  message body in a **serif** (Source Serif 4) beside a plain **Public Sans** UI
  and **IBM Plex Mono** for machine facts only. One honest **shape rule** —
  **blocky things are live annotations** (square edges, 2px left rules, small-caps
  labels: "Addison's work", section labels, active sidebar item) and **rounded
  things are yours to own/act on** (6px small buttons · 8px inputs/rows · 10px
  cards/composer · 12px banners · 999px pills). **Light by default with a
  class-driven dark mode** (`darkMode:"class"`, persisted as `addison.theme`);
  type stays compact-but-legible for older readers (personas 54 and 68). Fonts
  are the **one exception to "system stacks only"**: the three families are
  **bundled as OFL woff2** in `shell/src/assets/fonts/` with `@font-face` —
  CSP-safe because bundled, never hotlinked (strict `default-src 'self'`), always
  with system fallbacks. **`docs/design-brief-fern` is authoritative** for tokens,
  type, shape, and copy; the tokens in **shell/tailwind.config.js** implement it.
  This **supersedes the earlier dark terminal-adjacent look** (which superseded
  design-doc §7.1's cool-slate palette); §7.1's layout/IA and accessibility rules
  are unchanged.
- **IPC types are hand-synced**: keep `agent_core/protocol.py` and
  `shell/src/types/protocol.ts` in lockstep (codegen is Phase 3, not v1).

## Build order (spec §11 — build in sequence, each independently testable)

Done: (1) schema + dataclasses, (2) `ToolRegistry` + undo check + calculator,
(3) `PermissionGate`. Also shipped past the numbered sequence: the Fern UI
redesign, and the **widget rail** — declarative routine/stat widgets
(`agent_core/widgets.py`, invariant 7) plus the `usage_log` token/latency
substrate (§4.8) that feeds the token meter + connections cards. The Fern wave
is complete through its final PR: multi-provider API keys, the three-column
app shell + in-window Settings, widgets/tray, class-driven dark mode, the
**first-run pine banner** (`FirstRunBanner.tsx` — setup steps, launch-only
skip, serif time-of-day greeting) with the bell favicon bundled from
`shell/public/`, and a both-themes QA pass (TESTING-CHECKLIST §13). Also shipped:
the **mode-scoped safety backend** (owner decision 2026-07-19, `agent_core/policy.py`)
— the SAFE/OPEN split derived 1:1 from the profile, `run_command` (dev-only),
mode-aware `ToolRegistry.visible_tools` + `PermissionGate.authorize`, routine/widget
`command` kinds + `created_in_mode` hiding. The **frontend PR is next**: Settings
copy for the two profiles/modes (honest about what OPEN relaxes and the two GLOBAL
invariants that never do), the auto-grant/destructive-prompt UI, and rendering the
`mode` field now carried on `profile.get`/`profile.set`.

Next: (4) `AnthropicProvider` + minimal `ModelRouter` + orchestration loop,
**CLI-only** — get a working chat-with-tools loop before touching the shell.
Then (5) remaining tools + their `undo()`, (6) `UndoManager`, (7) Tauri shell +
IPC, (8) Routines, (9) Setup Assistant relay, (10) Ollama + full router, (11)
Profiles — the Simple/Developer split, which now ALSO derives the policy mode
(policy.py): Developer = OPEN mode reshapes the visible tool set and the gate's
prompting, but NEVER the global floors. The permission gate is mode-aware
(`authorize`), not profile-blind — the earlier "never the permission gate"
framing is superseded by the mode-scoped model above.

Most files past step 3 are stubs marked `TODO(step N)` pointing at the spec
section — implement them in order, not opportunistically.

Also shipped alongside step 1: **`read_web_page`** (`agent_core/tools/read_web_page.py`)
— LOW, read-only, in the **Simple** tool set, because answering *from* a page rather
than handing over a link is the companion's core job. It is the first SAFE tool that
sends a request to an address the **model** picks, so every URL and every redirect hop
is vetted by **resolved IP** and the connection is **pinned** to the address that was
vetted (SSRF + DNS-rebinding closed). Outward reach is bounded by **visibility, not
per-site grants** (owner decision 2026-07-20): `permission_detail` names the site and
the Activity Panel shows it on every granted call, in both modes and on the routine
path as well. The grant is still per tool id, and the panel names the *requested*
host — both are tracked in `docs/HANDOFF.md`, not silently accepted.

**Scope amendment (2026-07-20) — Phase-2 build order**, after this doc pass and in
dependency order (amendment §14): (1) **DONE — the snapshot/restore subsystem**
(floor G3; `agent_core/snapshots/`, the `config_snapshots` table, the `snapshot.*`
RPC namespace, seven auto-capture hooks + the verified-working site, the sidecar
cold-start recovery path, and the Settings "Restore points" card. Its single most
important test, `test_restore_always_works_from_a_broken_config`, passes; the
subsystem is described above under the floors. **`mint_anchor()` ships fully
implemented with no caller** — step 2 supplies it, because the Custom-profile guard
toggle that mints an anchor does not exist yet), (2) the **Custom profile + guard model +
undeletable anchor** (policy.py), (3) **routing strategies** (4 + custom) +
companion prefer-quality/prefer-free toggle + free-model disclaimer + graceful
fallback/cooldown, (4) **free-model endpoints** (legit free/local + add-by-prompt),
(5) **harness + workspace-trust** (OPEN), (6) **widget capability tiers + expanded
safe vocabulary** (to-do/checklist, note, timer), (7) **MCP client** tools via the
registry + gate, (8) the **automation keyword gate** + author-OS-run automation.
Steps 3–4 (companion) can run in parallel with 5–8 once 1–2 land.

## Multi-provider (owner decision 2026-07-18 — overrides spec §10 "Anthropic only")

OpenAI, Google (Gemini), and an OpenAI-compatible **custom server** are now v1,
alongside Anthropic. Keys are stored per **provider id** (`anthropic | openai |
google | custom`) in the OS keychain (Rust `store_provider_key`/`delete_provider_key`,
account = `provider-key:{provider}`; the legacy `provider-key:primary` Anthropic
entry auto-migrates on first read). The core reads a key via
`keychain.getProviderKey {provider}` at the moment of use only — keys never reach
the webview or SQLite (`provider.list`/`connect` responses carry status/metadata
ONLY). `provider.connect` validates with one tiny request (Anthropic: `GET /v1/models`;
OpenAI/custom: `GET {base}/v1/models`; Google: `GET /v1beta/models`), then folds the
provider's models into the single picker union. Non-secret connection metadata lives
in the `provider_config` table; the custom base URL is the ONE permitted `http://`
case (validated http(s)://). The orchestrator stays provider-agnostic — capability
differences via `ProviderCapabilities`, never `isinstance`.

**Routing & free models (scope amendment 2026-07-20).** Routing gains four named
strategies — quality-first (default; strong→weak degrade), cost-first, local-only,
balanced — plus a Developer custom builder; the companion sees a single
prefer-quality/prefer-free toggle. Strong-first with graceful fallback + provider
cooldown; a visible "answered with a free model" disclaimer when a free model
answers. Addison must be useful **without a paid frontier key** (local Ollama +
legitimate free cloud tiers); new endpoints are extensible and addable by prompting
Addison (reversible config, keys per G1). Gray-area aggregating routers
(OmniRoute/LiteLLM) are the user's own choice — documented on GitHub only, never
surfaced or endorsed in-app. **MCP is a *client* capability** (consume external
tools through the existing registry + gate; SAFE admits only read-only/undo-able
ones), never a server/gateway.

## Do NOT build yet (spec §10; reconciled with the 2026-07-20 amendment)

Still deferred: **fully-automatic task classification** for routing (the *choice
logic* that picks a strategy per task — v2; the four *named* strategies below ship
now), the Context Budget Manager / automatic long-conversation continuation (**v2**
— spec §4.8; v1 ships only the schema substrate, orchestrator machinery, never a
registry tool), messaging channels, Routine step-editing UI, a Rust rewrite of the
Agent Core, and the two v2 items from the 2026-07 ecosystem survey — Routine
export/import **sharing** and untrusted-content screening (design-doc §11) — do not
pull them forward. (Untrusted-content screening becomes load-bearing once
free/gray-area endpoints and MCP tools are in play — still v2.)

Also deferred, and specifically **not** to be solved inside the snapshot
subsystem: **restoring a previous app binary** (owner decision 2026-07-20 — a
**Phase-3 updater** item; G4 promises a config anchor that records its build, see
above). Building a downgrade path into the recovery floor would put a second,
uncoordinated binary-replacement mechanism on a collision course with
`updater.rs`, and it would be the one piece of the floor that could itself brick
the app. The rest of the step-1 deferrals — the `snapshot_now` tool and the anchor
minting caller (step 2), `_valid_http_url` credential hardening (step 4), the
permanent distrust of Addison's own data directory (step 5), `tool_grants` capture
(step 2, and then as an INTERSECT) — are itemised with their reasons in
`docs/HANDOFF.md`.

**Pulled forward by the amendment** (build per the Phase-2 order above, not
opportunistically): the four **named routing strategies** + custom, free/no-frontier
models + extensible endpoints, the **snapshot/rollback** subsystem (now built), the **Custom**
profile, the **coding harness + workspace-trust**, **capability-tiered widgets**,
the **MCP client**, and OS-authored automation behind the **keyword gate**.
Scheduling is still **not** Addison triggering itself (G2) — Addison authors, the
OS runs.

## Commands

```bash
# Agent Core (from agent_core/)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest ../tests/ -q          # safety-invariant tests must pass

# Shell (from shell/) — once step 7 lands
npm install
npm run tauri dev
```
