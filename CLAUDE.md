# CLAUDE.md

Guidance for working in this repository. Read the two specs before non-trivial
work — this file is the short version, they are authoritative:

- `docs/addison-design-doc.md` — product/UX rationale (the *why*)
- `docs/addison-engineering-spec.md` — build brief; **architecture is final for v1**

## What this is

Addison is a local-first desktop chat agent that is **approachable by default and
powerful on request**. Its default audience is non-technical users (personas
"Mira", 54, and "Petr", 68 — design-doc §5); technical users/developers are served
by an opt-in **Developer profile** (design-doc §7.11), not by complicating the
default. A profile reshapes the *surface and default capabilities* only — it
**never** changes the security model (see invariants below). Simple is the default;
Developer is opt-in. When adding a capability, ask which profile surfaces it — do
not leak developer affordances into Simple.

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

**Two GLOBAL invariants never relax, in EITHER mode** (flag any conflict rather
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
- **G2 — No scheduling / autonomous triggering in v1** (§6.7). This is a v1 scope
  line, not a mode question — it holds in SAFE *and* OPEN.

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
4. **Widgets are declarative specs (routine-run or whitelisted stat display) —
   never code; enforced at save and render.** In SAFE mode a widget is one of
   exactly two fixed shapes (`agent_core/widgets.py`): `{kind: "routine",
   routineId, title}` runs a saved routine through the *existing* routine.run path
   (same registry + gate, zero new execution surface), or `{kind: "stat", source,
   title}` displays a value from a fixed whitelist (`tokens_month`,
   `provider_latency`, `connections`). No eval, expression, or template field
   exists; unknown kinds/sources are rejected at save and hidden at render. (OPEN
   mode adds a third `{kind: "command", command, title}` shape, valid only in OPEN
   and hidden in SAFE.)

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
prompting, but NEVER the two GLOBAL invariants (keys isolation, no scheduling).
The permission gate is mode-aware (`authorize`), not profile-blind — the earlier
"never the permission gate" framing is superseded by the mode-scoped model above.

Most files past step 3 are stubs marked `TODO(step N)` pointing at the spec
section — implement them in order, not opportunistically.

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

## Do NOT build yet (spec §10)

Automatic
task-based model routing/auto-switching (**planned for v2** — v1 ships the
substrate: `vision`/`audio` capability flags and multiple local models with an
*explicit* picker, but the automatic choice among them is v2), the Context
Budget Manager / automatic long-conversation continuation (**planned for v2**
— spec §4.8; v1 ships only the schema substrate at step 6, and it is
orchestrator machinery, never a registry tool), messaging channels, Routine
step-editing UI, any Routine scheduling/triggers, a Rust rewrite of the Agent
Core, and the two v2 items adopted from the 2026-07 ecosystem survey —
Routine export/import sharing and untrusted-content screening (design-doc
§11 "Adopted from the 2026-07 ecosystem survey") — do not pull them forward.

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
