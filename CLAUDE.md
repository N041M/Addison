# CLAUDE.md

Guidance for working in this repository. This file is the short form; the
documents below are authoritative for their own topics, and **each topic has
exactly one owner** — a second mention anywhere is a link, never a copy.

| Topic | Owner |
|---|---|
| Floors, modes, guards, snapshots, SAFE invariants | [`docs/SAFETY.md`](docs/SAFETY.md) |
| Build brief — schema, IPC, subsystems | `docs/addison-engineering-spec.md` |
| Product and UX rationale (the *why*) | `docs/addison-design-doc.md` |
| What is built / next / not being built | [`ROADMAP.md`](ROADMAP.md) |
| Live issues and open design questions | [`docs/KNOWN-GAPS.md`](docs/KNOWN-GAPS.md) |
| The standard, conventions, environment | [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) |
| What each step shipped + its rigor findings | [`docs/BUILD-LOG.md`](docs/BUILD-LOG.md) |
| Gates and verification | `docs/VERIFICATION.md` |

**[`docs/README.md`](docs/README.md) is the full map** — every file, what it owns,
and the rule that keeps it that way.

There is **no precedence chain any more.** The 2026-07-20 scope amendment was
retired on 2026-07-27: its content had already been folded into the documents
above, but it kept a *"where we differ, the amendment wins"* rule that made every
reader replay a merge that had already happened. It is now a historical record —
minutes, not law. Do not cite it to settle a question.

## What this is

Addison is a local-first desktop **butler** — **approachable by default and
powerful on request**. Its default audience is
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

## Safety model — rules (full text: [docs/SAFETY.md](docs/SAFETY.md))

**`docs/SAFETY.md` owns this topic.** What follows is the enforceable short form.
Do not restate the reasoning here or anywhere else — link. Correcting one floor
used to mean editing thirteen files.

**Two policy modes, derived 1:1 from the active Profile** (`agent_core/policy.py`,
`mode_for_profile`) — there is no separately-persisted mode:

- **Simple → SAFE.** Today's behaviour, byte-for-byte. All four SAFE invariants hold.
- **Developer → OPEN.** Real command execution exists (`run_command`, dev-only); a
  `dev_only` tool may register at HIGH without `undo()`; routines and widgets may
  carry a `command` step/kind; the gate auto-allows non-destructive actions and
  prompts ONLY for destructive ones. Fewer prompts, **not** no gate.
- **Custom → OPEN + a `GuardConfig` overlay.** Two settings-backed *prompting*
  guards, never floors. Weakening one mints the G4 anchor first, and refuses the
  change if the anchor cannot mint.

**Four GLOBAL floors, never relaxed in any mode. Flag a conflict, never work around it:**

- **G1** — API keys never reach the frontend/webview or SQLite. Keychain only, read
  at the moment of use. Do not touch this machinery.
- **G2** — Addison never triggers itself. It may *author* automation the OS runs;
  arming a powerful action needs a user-typed keyword prefix (designed, **not built**).
- **G3** — Guaranteed rollback. Snapshots (auto before risky changes, plus
  on-command) always allow a one-action restore to the last verified-working config.
  **True in both modes since Phase-2 step 5.5** (2026-07-31) put a seatbelt profile
  and a pre-gate denylist under `run_command`; the two edges it does not reach are
  named in [docs/SAFETY.md](docs/SAFETY.md), which owns this floor's scope.
- **G4** — Turning a guard OFF in Custom mints a permanent, undeletable snapshot
  anchor recording the app build. A build *reference*, never bytes; restoring a
  previous binary is not implemented (Phase-3 updater).

**Four SAFE-mode invariants (Simple profile — hold byte-for-byte):**

1. **No arbitrary code/shell execution.** No `eval`, no Lua sandbox, no raw-code
   field. OPEN's `run_command` and the two `open_only` file tools are absent from
   `registry.visible_tools(SAFE)` and refused at dispatch outside OPEN.
2. **Every `risk_tier != LOW` tool has a real `undo()`**, enforced at registration
   in `tools/registry.py`, which raises otherwise. Never satisfy this with a no-op:
   a tool that genuinely cannot be undone stays LOW and read-only. **This is the
   single most important test in the codebase.** The only exception is a
   `allow_missing_undo` registration, never in the SAFE view.
3. **A Routine never gets permissions beyond what the user granted live.** Same
   registry and gate instances as the live orchestrator, in both modes —
   `visible_tools(mode)` is a filtered view, never a second registry.
4. **Widgets are capability-gated, not code.** Buildable in every mode; the mode
   gates the capability. SAFE widgets come from a safe, non-destructive vocabulary
   and are non-destructive by construction. That vocabulary is a **CLOSED SET OF
   KINDS**, hard-coded in `agent_core/widgets.py` — a spec never declares its own
   capabilities, and where a widget invokes a tool the tier check is
   `registry.visible_tools(mode)`, never a second risk model.

**Two rules that are easy to get wrong:**

- **Artifact disabling** — routines/widgets created in OPEN are **listed but
  disabled** in SAFE (`created_in_mode`), carrying a display-only
  `unavailable: {reason, message}`, and return untouched when Developer is active
  again. They used to be hidden; owner decision 2026-08-06 changed that, and
  [docs/SAFETY.md](docs/SAFETY.md) owns why. The marker is never the enforcement:
  dispatch (`routine.run` / `widget.run` / the engine's per-step check) refuses,
  and **wins** if the two ever disagree.
- **Snapshots are NEVER hidden by mode (C6).** `created_in_mode` ships on
  `config_snapshots` but is **display only**. No list, restore, prune or delete
  query may filter on it, in any mode. A source-level test enforces this.

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
- **UI (step 7+): distinct, non-AI look — the dark "correspondence-instrument"
  direction (v4, adopted 2026-07-26 from the owner's design handoff)**.
  **`docs/design-brief-dark/` is authoritative** — `README.md` + `prototype.html`
  are the designer's pixel-perfect reference, `IMPLEMENTATION.md` records the
  binding prototype→app mapping (demo content is never shipped; real features are
  restyled, never de-wired); the tokens in **shell/tailwind.config.js** implement
  it. The direction: a **calm, text-first dark UI** — near-black paper
  (`#0C0C0D`), hairline separators, one **violet accent** (`#B4A9F5`) reserved
  for actions, selection rails, and live state (never decoration), **system type
  only** ('Helvetica Neue' UI beside `ui-monospace` machine facts — the earlier
  bundled-font exception is retired, no @font-face). Shape rule: selection/active
  is a **2px accent left rail**; sections sit on 2px rules; hairline-row surfaces,
  with floating chrome (popovers/menus/modal, 5–8px radii) the only
  bordered-panel elements. Signature motion: the **character-scramble**
  (`shell/src/lib/scramble.ts`) + fadeRise/fadeDrop transitions, all no-ops under
  `prefers-reduced-motion`. **Dark is the designed reference; light is a derived
  translation** — the theme stays class-driven three-way (light/dark/system,
  default **system**, persisted as `addison.theme`). Still never generic-AI
  styling beyond the sanctioned flat accent (no gradients, glassmorphism,
  sparkle/bot icons, shimmer) and never a model vendor's branding. This
  **supersedes the Fern "warm correspondence" direction (v3,
  `docs/design-brief-fern`, kept as history)**, which superseded the dark
  terminal-adjacent look, which superseded design-doc §7.1's cool-slate palette;
  §7.1's layout/IA and accessibility rules are unchanged (personas 54 and 68
  still govern legibility).
- **IPC types are hand-synced**: keep `agent_core/protocol.py` and
  `shell/src/types/protocol.ts` in lockstep (codegen is Phase 3, not v1).

## Build order

**[ROADMAP.md](ROADMAP.md) owns status** — what is built, what is next, what is
deliberately not being built. This section held a second copy and the two drifted;
it now holds only what a *builder* needs that status does not convey.

The v1 sequence (spec §11, steps 1–11) is complete and merged, as are Phase-2
steps 1–6 (snapshots/G3, the Custom profile + guards + the G4 anchor, routing
strategies, free-model endpoints, the coding harness + workspace-trust,
containment for that harness, and the widget vocabulary + tiers). **No file is marked `TODO(step N)` any more** — that
sequence records the order the system was built in, not work outstanding.

Two steps remain — 7 and 8. Step 6 landed on 2026-08-06 (both halves; the
capability-declaration lattice was cut in favour of the closed kind list — see
invariant 4 above and [ROADMAP.md](ROADMAP.md) for status). The one dependency that
is not obvious from the list is inside 7:

- **7 — MCP client.** 5.5 shipped the `tool_audit` log, so the spec's promise that
  MCP tools are "gated, logged, undo-aware" is now satisfiable and that half of the
  dependency is discharged. It is **still blocked on the SAFE-constraint question**
  in "Known gaps", because a server declares its own risk and admitting a tool to
  SAFE on that say-so breaks SAFE invariant 2 through a path the registration check
  cannot see.
- **8 — the automation keyword gate** + author-OS-run automation. Until it exists,
  nothing in the tree can author or arm automation, so G2 holds trivially.

When adding a capability, ask which profile and tier surfaces it — do not leak
developer affordances into Simple.

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

**Routing & free models (scope amendment 2026-07-20).** Routing gains **three**
named strategies — quality-first (default; strong→weak degrade), cost-first and
local-only — plus a Developer custom chain. (The amendment drafted a fourth,
**balanced**; it was **CUT from v1** by owner decision, §10.1, because it was
provably identical to cost-first at two-model pools. `ROUTING_STRATEGIES` in
`providers/router.py` is the authority.) The companion sees a single
prefer-quality/prefer-free toggle. Strong-first with graceful fallback + provider
cooldown; a visible "answered with a free model" disclaimer when a free model
answers. Addison must be useful **without a paid frontier key** (local Ollama +
legitimate free cloud tiers); new endpoints are extensible and addable by prompting
Addison (reversible config, keys per G1). Gray-area aggregating routers
(OmniRoute/LiteLLM) are the user's own choice — documented on GitHub only, never
surfaced or endorsed in-app. **MCP is a *client* capability** (consume external
tools through the existing registry + gate; SAFE admits only read-only/undo-able
ones), never a server/gateway.

## Do NOT build yet

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
the app. Of the step-1 deferrals, only **`tool_grants` capture** is still open (and
would then need to be an INTERSECT). The rest have LANDED and are no longer
deferrals: the `snapshot_now` tool and the anchor-minting caller
(`rpc/guards.py`), `_valid_http_url` credential hardening (`rpc/providers.py`),
and the permanent distrust of Addison's own data directory
(`policy.py::_protected_dirs`). The ledger with each one's reasoning is in
`docs/HANDOFF.md`.

**Pulled forward by the 2026-07-20 scope change** (build per the order above, not
opportunistically): the **named routing strategies** + custom, free/no-frontier
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

# Shell (from shell/)
npm install
npm run tauri dev
```
