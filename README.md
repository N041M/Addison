# Addison

A local-first, zero-config AI agent harness for non-technical users.

Addison is a desktop chat app that talks to an LLM, uses a small set of *safe*
tools, remembers things locally, can undo anything it does, and lets the user
turn a sequence of actions into a reusable **Routine**. The hard problem here is
not agent orchestration — it's product packaging and trust.

See the specs this scaffold implements:

- [`docs/addison-design-doc.md`](docs/addison-design-doc.md) — product / UX rationale
- [`docs/addison-engineering-spec.md`](docs/addison-engineering-spec.md) — the build brief (architecture is final for v1)

## Architecture (three processes, three trust levels — spec §1.3)

| Process | Language | Trust | Responsibility |
|---|---|---|---|
| Desktop shell | Rust (Tauri 2.x) | highest | OS permissions: keychain, file picker, updater; supervises the core |
| Agent Core | Python 3.12 | middle | orchestration loop, tool registry, permission gate, routines, SQLite |
| Frontend | React + TS | lowest | renders state, captures input; never sees keys or the network |

The shell and core talk over **JSON-RPC 2.0 over stdio**. The core reaches out
to model sources (Anthropic/OpenAI/Google BYOK, local Ollama, the Setup
Assistant relay) — the `ModelRouter` picks which one per request (spec §4.1.1).

## Layout

```
agent_core/      Python — orchestrator, providers, tools, permissions, memory, snapshots, routines
shell/           Tauri (Rust) + React frontend
docs/            design doc + engineering spec
tests/           starter tests for the core safety invariants
```

## Safety invariants (non-negotiable — spec §8)

1. No tool executes arbitrary shell/code. Routines are **declarative plans**, not scripts.
2. Every `risk_tier != LOW` tool must have a real `undo()` — **enforced at registration** (raises otherwise).
3. API keys never reach the frontend; they live in the OS keychain, read only at moment of use.
4. Setup Assistant relay keys never exist in this repo's runtime.
5. A Routine never has permissions beyond what the user already granted live.
6. No scheduling / autonomous triggering in v1.

## Status

Scaffold following the spec's build order (§11). Implemented as working code:

- SQLite schema + Python dataclass mirrors (step 1)
- `ToolRegistry` with the registration-time undo check + `calculator` (step 2)
- `PermissionGate` (step 3)
- Orchestration loop, `ModelRouter`, `UndoManager`, `RoutineEngine` ordering — structured, with typed stubs where they call not-yet-built pieces

Everything else (provider HTTP calls, Tauri wiring, web/file tool bodies, Setup
Assistant relay, Ollama) is stubbed with a `TODO(step N)` pointing at the exact
spec section. See **Build order** below.

## Getting started

### Agent Core (Python)

```bash
cd agent_core
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd .. && pytest tests/ -q          # safety-invariant tests should pass
```

### Shell (Tauri + React)

```bash
cd shell
npm install
npm run tauri dev                  # once step 7 (shell wiring) is implemented
```

## Build order (spec §11)

1. ✅ SQLite schema + dataclass mirrors
2. ✅ `ToolRegistry` + undo check, with 2–3 LOW-risk tools
3. ✅ `PermissionGate` (in-memory stub handler)
4. ⬜ `AnthropicProvider` + minimal `ModelRouter` + orchestration loop, CLI-only
5. ⬜ Remaining v1 tools with their `undo()` bodies
6. ⬜ `UndoManager` (conversational rewind, then action rewind)
7. ⬜ Tauri shell + JSON-RPC IPC
8. ⬜ `RoutineBuilder` + `RoutineEngine`
9. ⬜ `SetupAssistantProvider` + free relay
10. ⬜ `OllamaProvider` + full `ModelRouter` (LOCAL role) + model-role selector

## What NOT to build yet (spec §10)

OpenAI/Google providers, automatic model routing, messaging channels, Routine
step-editing UI, any Routine scheduling/triggers, a Rust rewrite of the core.
