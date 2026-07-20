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
  guard OFF and saving mints an **undeletable snapshot anchor** (which also
  captures the app binary), so weakening safety always leaves a guaranteed way back.

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
always allowed.

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
  (keys stay put — G1 holds). (New floor, 2026-07-20.)
- **G4 — Undeletable anchor on weakening.** Turning a guard OFF in Custom mode
  (and saving) mints a **permanent, undeletable** snapshot anchor that also
  captures the **app binary** — lowering your own protections always leaves a
  guaranteed, complete way back.

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

**Scope amendment (2026-07-20) — Phase-2 build order**, after this doc pass and in
dependency order (amendment §14): (1) the **snapshot/restore subsystem** (floor G3
— built and hardened first; "restore always works, even from a broken config" is
its single most important test), (2) the **Custom profile + guard model +
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

**Pulled forward by the amendment** (build per the Phase-2 order above, not
opportunistically): the four **named routing strategies** + custom, free/no-frontier
models + extensible endpoints, the **snapshot/rollback** subsystem, the **Custom**
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
