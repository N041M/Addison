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

## Non-negotiable safety invariants (spec §8)

These are hard constraints. If a request appears to conflict with one, **flag it
rather than working around it silently.**

1. **No arbitrary code/shell execution — ever.** Tools are individual typed
   functions, not "run command". Routines are *declarative plans* (§6.1), not
   scripts. Do not add `eval`, a Lua sandbox, or a raw-code field to a Routine.
2. **Every `risk_tier != LOW` tool must have a real `undo()`**, enforced at
   registration in `tools/registry.py` (it raises otherwise). Do NOT satisfy this
   with a no-op `undo()` — a tool that genuinely can't be undone stays LOW and
   read-only. This registration check is the single most important test in the
   codebase (spec §9).
3. **API keys never reach the frontend/webview.** They live in the OS keychain,
   read by the Rust shell / Agent Core only at the moment of use, never persisted
   in Agent Core memory beyond one request, never in SQLite.
4. The Setup Assistant relay's keys never exist in this repo's runtime — they're
   external and server-side.
5. **A Routine never gets permissions beyond what the user granted live** — no
   privilege escalation via automation. It uses the *same* `ToolRegistry` and
   `PermissionGate` instances as the live orchestrator.
6. **No scheduling / autonomous triggering in v1** (§6.7).

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
- **UI (step 7+): distinct, non-AI look** (design-doc §7.1). Never the generic
  AI-chat aesthetic (purple gradients, glassmorphism, sparkle/bot icons,
  shimmer effects) and never a model vendor's branding (no warm
  cream/terracotta). The direction is a **dark, terminal-adjacent everyday-utility
  look** — minimal chrome, system-monospace accents (wordmark, labels,
  timestamps, code; body stays sans), one restrained steel-blue accent for
  primary actions, sharp corners (no rounded cards), no decorative taglines,
  compact-but-legible type (16px root) for older readers (personas 54 and 68) —
  dark but calm and legible, never hacker-neon. No external fonts (strict CSP —
  system stacks only). The tokens in **shell/tailwind.config.js are
  authoritative**. This **supersedes design-doc §7.1's light cool-slate palette
  (amended 2026-07)**; §7.1's layout/IA and accessibility rules are unchanged.
- **IPC types are hand-synced**: keep `agent_core/protocol.py` and
  `shell/src/types/protocol.ts` in lockstep (codegen is Phase 3, not v1).

## Build order (spec §11 — build in sequence, each independently testable)

Done: (1) schema + dataclasses, (2) `ToolRegistry` + undo check + calculator,
(3) `PermissionGate`.

Next: (4) `AnthropicProvider` + minimal `ModelRouter` + orchestration loop,
**CLI-only** — get a working chat-with-tools loop before touching the shell.
Then (5) remaining tools + their `undo()`, (6) `UndoManager`, (7) Tauri shell +
IPC, (8) Routines, (9) Setup Assistant relay, (10) Ollama + full router, (11)
Profiles — formalize the Simple/Developer split (`profiles.py` exists as scaffold;
it parameterizes registration/onboarding, never the permission gate — spec §4.7,
§8.7).

Most files past step 3 are stubs marked `TODO(step N)` pointing at the spec
section — implement them in order, not opportunistically.

## Do NOT build yet (spec §10)

OpenAI/Google providers (Anthropic only for the first build), automatic
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
