# Addison — Engineering Implementation Spec

**Audience: Claude Code (or any implementing engineer/agent). This is a build brief, not a menu of options — architecture decisions below are final for v1 unless flagged otherwise.**

Companion document: `addison-design-doc.md` (product/UX rationale). This document assumes that context and focuses on concrete architecture, data structures, and implementation order.

---

## Amendment — 2026-07-20 (owner-approved scope amendment)

**This spec has an authoritative amendment layered on top of it.** Read
`docs/addison-scope-amendment-2026-07.md` (owner-approved 2026-07-20) alongside
this document. Where the amendment and the unamended body below conflict, **the
amendment governs**; each affected section carries an inline `Amendment 2026-07-20`
note pointing back here. The amendment does **not** repeal any existing safety
invariant — it adds one global floor, adds a fourth, reinterprets one, and sharpens
the product identity around them.

**Identity.** Addison is a *butler*: acts when asked, never uninvited, and is
discreet and reversible — it never puts the house in a state you can't restore.
Developer/OPEN becomes a real coding-agent harness ("the harness you cannot
brick"), Simple/SAFE is an all-in-one companion on the same floor, and a new
**Custom** profile sits between them.

**What the amendment adds/changes (all Phase-2):**

- **G3 (new global floor) — guaranteed rollback.** Neither user nor model can
  drive Addison into an unrecoverable state; a one-action restore to the last
  **verified-working** config always exists and the restore path is itself
  unbreakable. Realised with **app-state snapshots** — taken automatically before
  any risky/sweeping change *and* on command. See the §1.4 / §3 / §4.9 notes.
- **Undeletable anchor (new fourth floor).** Turning a safety guard **off** in
  Custom mode mints a permanent, non-deletable snapshot of the last
  verified-working state, which also **records the app build it was minted on**.
  Neither user nor model can remove it. *(Owner decision 2026-07-20: a build
  **reference**, not the binary — see §4.9.)*
- **Simple / Developer / Custom mode model + capability tiers** (§4.10). Custom is
  reachable only deep in Settings behind extra confirmation and tunes *prompting*
  guards **only** — never the floors.
- **Workspace-trust** (§4.10): a user-granted, snapshotted project directory where
  the OPEN harness acts without a per-action card — the gate still *runs and logs*
  on every call; outside the workspace the per-invocation destructive card is
  unchanged.
- **Routing-strategy abstraction** (§4.11): quality-first / cost-first / local-only
  / balanced + a Developer-only Custom builder; strong-first degrade-down default,
  a visible "answered with a free model" disclaimer, graceful fallback + cooldown.
- **MCP client** (§4.12): Addison *consumes* external MCP tools through the
  **existing tool registry + permission gate** — never a side channel, never an
  MCP server/gateway.
- **Capability-tiered widget vocabulary** (§8 note, §3 note): widgets are
  buildable in **all** modes; the tier gates the *capability* a widget may use, not
  whether one can be built. SAFE widgets are non-destructive by construction.
- **Automation model** (§6 note): Addison authors OS-run automation, the OS runs
  it on its schedule, **Addison never self-triggers**; running/arming a powerful
  action requires a **user-typed keyword prefix** — which is also a
  prompt-injection defence (observed content cannot forge a keystroke).
- **Free / no-frontier-required models** (§4.11 note): Addison must be useful with
  no paid frontier key; only *legitimate* free/local sources appear in-app.

**G1 reaffirmed.** API keys stay keychain-only and are **excluded from every
snapshot**, including the Custom-mode undeletable anchor. After this amendment there are
**four global floors — G1, G2, G3, and the undeletable-anchor rule** — none of
which any mode or guard can switch off.

**Precedence note (mode-scoped model).** The unamended §4.7 / §8 body still frames
Profiles as "surface only, never a security boundary." That framing was already
superseded by the mode-scoped safety model (owner decision 2026-07-19:
Simple→SAFE, Developer→OPEN, derived 1:1 from the profile) and is extended again
here (adding Custom + tunable prompting guards). Read §4.7 and §8 through the
amendment notes below, not the original "not a security boundary" sentence.

**Where a detail is left open** it is flagged `(open — amendment §13)` below; do
**not** invent schema or syntax past those flags. The amendment's §14 gives the
Phase-2 build order (snapshot floor first) — mirrored in the §11 note.

---

## 0. What you're building

A desktop chat app that talks to an LLM, can use a small set of safe tools, remembers things locally, can undo anything it does, and — the focus of this document — lets the user turn a sequence of things Addison just did into a saved, reusable **Routine** they can re-run with one click. Routines are Addison's answer to OpenClaw's "skills," scoped down to fit a security model that never grants shell or arbitrary code execution. The app also ships two **Profiles** — Simple (default, for the non-technical personas) and Developer (opt-in) — that reshape the surface and default capabilities over this one engine without changing the security model (§4.7).

Build order matters. Section 12 gives the exact sequence. Do not build the Routine engine before the tool registry and permission gate exist and are tested — Routines are composed *from* registered tools, they don't introduce new capabilities of their own.

---

## 1. Chosen Architecture

### 1.1 Component diagram

```
TAURI SHELL (Rust) -- owns the process
  - window management, OS keychain access, updater, installer
  - owns the filesystem/OS-permission boundary
  - spawns and supervises the Agent Core as a child process

+----------------------------+     +------------------------------+
| Frontend (React + TS)      |     | Agent Core (Python, S1.2)    |
|                            |     |                              |
| - Chat thread              |     | - Orchestration              |
| - Activity panel           | <-> | - Tool Registry              |
| - Permission cards         |     | - Routine Engine             |
| - Routine library UI       |     | - ModelRouter (S4.1)         |
| - Model role selector      |     |                              |
+----------------------------+     +------------------------------+

                                    (JSON-RPC over stdio, S7)

                                    +----------------------+
                                    | SQLite (local, S3)   |
                                    +----------------------+

Agent Core reaches out to three concurrently reachable model sources:

+------------------------+  +------------------------+  +------------------------+
| Anthropic / OpenAI /   |  | Ollama (local,         |  | Setup Assistant        |
| Google APIs (BYOK)     |  | post-setup, S4.1.2)    |  | free relay             |
|                        |  | no network call        |  | (serverless, S5.6)     |
|                        |  | leaves the machine     |  |                        |
+------------------------+  +------------------------+  +------------------------+
```

Note the diagram now shows **three concurrently reachable model sources**, not one active connection swapped over time — this is the architectural change from the earlier draft. The Agent Core's `ModelRouter` (§4.1.1) decides, per request, which of these to call; more than one may be configured and in use within the same session.

### 1.2 Language/framework decisions (final for v1)

| Layer | Choice | Why |
|---|---|---|
| Desktop shell | **Tauri 2.x**, Rust | Small binary, no bundled Chromium runtime duplication, Rust owns the OS-permission-sensitive code (keychain, filesystem picker) |
| Frontend | **React + TypeScript + Tailwind** | Standard, fast to iterate |
| Agent Core (v1) | **Python 3.12** | Fastest iteration for orchestration logic and the Routine interpreter; ships as a bundled child process (PyInstaller or similar), not a system dependency the user installs |
| Agent Core ↔ Shell IPC | **JSON-RPC 2.0 over stdio** | Simple, debuggable, no network stack needed for local-only communication |
| Local storage | **SQLite**, accessed from the Agent Core via `sqlite3`/SQLAlchemy Core (no heavy ORM) | Matches local-first design, easy to inspect/back up |
| Cloud model calls | Native HTTPS from the Agent Core process, never from the frontend | Keeps all API keys out of the renderer/webview entirely |
| Local model runtime | **Ollama**, called via its local HTTP API (`http://127.0.0.1:11434` by default) | Handles model download/lifecycle/quantization; exposes an OpenAI-compatible endpoint, so `OllamaProvider` reuses most of `OpenAIProvider`'s request/response translation (§4.1.2) |

**Explicitly deferred, do not build in v1:** a Rust rewrite of the Agent Core. Python is the shipped v1 implementation, not just a prototype to throw away — revisit only if startup time or binary size becomes a real problem after v1 ships.

**Reversal from the earlier draft of this document:** local model support (via Ollama) is pulled into v1 scope, not deferred. See §4.1.2 and the removal from §10's deferred list.

### 1.3 Process & trust boundaries

Three processes, three trust levels:

1. **Tauri shell (Rust)** — highest trust, has real OS permissions (file picker, keychain, notifications). Never executes model-provided instructions directly; only relays IPC calls the Agent Core has already validated against the permission gate (§4.3).
2. **Agent Core (Python)** — orchestrates the model loop, owns the Tool Registry, Routine Engine, and SQLite. Has no direct OS permissions of its own — every filesystem/OS action goes back through the shell via IPC, which is what makes the filesystem-scope-by-picker security property (see design doc §9) actually enforceable at a process boundary, not just a convention.
3. **Frontend (webview)** — lowest trust, renders state, captures user input, never sees API keys, never talks to the network directly.

### 1.4 Amendment 2026-07-20: new architecture surfaces (Phase-2)

The amendment adds five architecture-level surfaces, all Phase-2, all living in
the **Agent Core** and driven through the *existing* orchestrator / registry /
gate rather than parallel paths. Concept detail is in §4.9–§4.12; this subsection
places them in the architecture.

- **Snapshot / restore subsystem (G3).** A new Agent-Core module (natural home:
  `agent_core/snapshots/`, beside the existing `undo_manager.py`) that captures
  point-in-time copies of Addison's **mutable state** (settings, provider/routing
  config, skills, widgets, routines) and restores them in one action. It is the
  load-bearing floor: automatic snapshots fire before any risky/sweeping change,
  restore always targets the last *verified-working* state, and **the restore
  path is itself unbreakable** ("restore always works, even from a broken
  config"). Snapshots **exclude the OS keychain** (G1) and the app binary; the
  **Custom-mode undeletable anchor** additionally records a build **reference**
  (`{"version", "identifier"}`), so a restore can say plainly that the app itself
  did not change. Restoring a binary is a Phase-3 updater item, not part of this
  floor. See §4.9.
- **Simple / Developer / Custom mode model + capability tiers.** The mode is still
  derived from the profile (2026-07-19 model); the amendment adds a third profile,
  **Custom**, reachable only deep in Settings behind extra confirmation, whose
  *prompting* guards are user-tunable — never the floors. Capabilities are
  **tiered**: SAFE admits only non-destructive capability; higher tiers admit
  code-backed / system-capable capability (tools, widgets, MCP tools). See §4.10.
- **Workspace-trust (OPEN harness).** A user-granted, snapshotted **project
  directory** inside which the OPEN harness acts freely — the gate still *runs and
  logs* every call, it just doesn't *prompt* within the trusted scope; outside it,
  destructive actions raise the per-invocation card exactly as today. This
  reconciles the agentic coding loop (dozens of edits/runs) with the per-call
  gate. See §4.10.
- **Routing-strategy abstraction.** The per-request `ModelRouter` (§4.1.1) gains a
  **strategy** layer — quality-first / cost-first / local-only / balanced, plus a
  Developer-only Custom builder — with strong-first degrade-down default, a
  free-model disclaimer, and graceful fallback + provider cooldown. See §4.11.
- **MCP client surface.** Addison consumes external MCP servers/tools as an MCP
  **client** (never a server or gateway), surfacing their tools through the
  **existing `ToolRegistry` + `PermissionGate`**. Connecting a server is
  reversible config, like adding a provider endpoint. See §4.12.

---

## 2. Repository Layout

```
addison/
├── shell/                      # Tauri + Rust
│   ├── src-tauri/
│   │   ├── src/
│   │   │   ├── main.rs
│   │   │   ├── agent_process.rs    # spawns/supervises the Python core
│   │   │   ├── ipc.rs              # JSON-RPC relay to/from frontend
│   │   │   ├── keychain.rs         # OS keychain read/write (BYOK keys, device key)
│   │   │   ├── filesystem.rs       # native file picker, scoped file handles
│   │   │   └── updater.rs
│   │   └── Cargo.toml
│   └── src/                        # React frontend
│       ├── components/
│       │   ├── ChatThread.tsx
│       │   ├── ActivityPanel.tsx
│       │   ├── PermissionCard.tsx
│       │   ├── RoutineLibrary.tsx
│       │   └── RewindControls.tsx
│       ├── ipc/
│       │   └── client.ts           # typed wrapper around Tauri IPC calls
│       └── types/
│           └── protocol.ts         # mirrors agent_core/protocol.py types
│
├── agent_core/                     # Python
│   ├── main.py                     # JSON-RPC server entrypoint
│   ├── orchestrator.py             # the agent loop (§4.4)
│   ├── providers/
│   │   ├── base.py                 # ModelProvider protocol (§4.1)
│   │   ├── router.py               # ModelRouter — resolves role → provider (§4.1.1)
│   │   ├── anthropic_provider.py
│   │   ├── openai_provider.py
│   │   ├── ollama_provider.py      # local models, post-setup (§4.1.2)
│   │   ├── setup_assistant_provider.py
│   │   └── direct_api_provider.py
│   ├── tools/
│   │   ├── registry.py             # ToolRegistry, risk tiers (§4.2)
│   │   ├── base.py                 # Tool protocol, undo contract
│   │   ├── web_search.py
│   │   ├── read_file.py
│   │   ├── save_file.py
│   │   ├── draft_message.py
│   │   ├── calculator.py
│   │   └── open_link.py
│   ├── permissions/
│   │   └── gate.py                 # Permission Gate (§4.3)
│   ├── memory/
│   │   ├── store.py                # SQLite access layer
│   │   └── schema.sql
│   ├── snapshots/
│   │   └── undo_manager.py         # Rewind & Self-Repair (§4.5)
│   ├── routines/
│   │   ├── model.py                # Routine data structure (§6.2)
│   │   ├── builder.py              # conversational Routine creation (§6.3)
│   │   ├── engine.py                # declarative plan interpreter (§6.4)
│   │   └── library.py              # CRUD for saved Routines
│   ├── profiles.py                 # Profile config + resolver (§4.7)
│   └── protocol.py                 # shared JSON-RPC message types
│
└── docs/
    ├── addison-design-doc.md
    └── addison-engineering-spec.md  # this file
```

Module boundary rule: **`tools/`, `providers/`, and `routines/` never import from each other directly** — they're all consumed by `orchestrator.py`, which is the only module allowed to know about all three. This is what keeps the system modular enough for Section 6's automation goal: a Routine is just a saved sequence of tool calls, and the engine that plays it back reuses the exact same `ToolRegistry` and `PermissionGate` the live orchestrator uses, rather than a parallel execution path.

`profiles.py` holds profile *config* only — it imports nothing from `tools/`, `providers/`, or `routines/`; `main.py`/`orchestrator.py` consume it when wiring the registry and onboarding, so a Profile parameterizes those choices without becoming a parallel path of its own (§4.7).

---

## 3. Core Data Structures

All persisted structures live in SQLite. Schema below; Python dataclasses mirror these 1:1 in `agent_core/*/model.py` files.

```sql
-- agent_core/memory/schema.sql

CREATE TABLE conversations (
    id              TEXT PRIMARY KEY,       -- uuid4
    title           TEXT,
    started_at      INTEGER NOT NULL,       -- unix epoch seconds
    provider_id     TEXT NOT NULL           -- which ModelProvider was active
);

CREATE TABLE messages (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id),
    role                TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
    content             TEXT NOT NULL,       -- text content, or JSON for tool calls/results
    tool_call_id        TEXT,                -- set if role='tool' or this message triggered a tool call
    created_at          INTEGER NOT NULL
);

CREATE TABLE memory_facts (
    id                          TEXT PRIMARY KEY,
    fact                        TEXT NOT NULL,
    source_conversation_id      TEXT REFERENCES conversations(id),
    confirmed_by_user           INTEGER NOT NULL DEFAULT 0,   -- boolean
    created_at                  INTEGER NOT NULL
);

CREATE TABLE tool_grants (
    tool_id         TEXT PRIMARY KEY,       -- e.g. 'web_search'
    granted_at      INTEGER NOT NULL,
    scope_details   TEXT                    -- JSON, tool-specific (e.g. remembered file handles)
);

CREATE TABLE action_snapshots (
    id                  TEXT PRIMARY KEY,
    tool_call_id        TEXT NOT NULL,
    tool_id             TEXT NOT NULL,
    undo_payload        TEXT NOT NULL,       -- JSON, tool-specific (e.g. {"deleted_file_backup": "..."})
    created_at          INTEGER NOT NULL,
    reverted            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE device_identity (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    device_id       TEXT NOT NULL,           -- uuid4, public identifier
    -- private key material lives ONLY in the OS keychain (via shell/keychain.rs), never here
    created_at      INTEGER NOT NULL
);

CREATE TABLE provider_config (
    role            TEXT PRIMARY KEY CHECK(role IN ('primary','local','setup_assistant')),
    provider_id     TEXT NOT NULL,        -- 'anthropic', 'openai', 'ollama', 'setup_assistant_relay'
    -- API keys are NEVER stored here. This table only holds non-secret config
    -- (selected model name, Ollama base URL, etc.). Keys live in OS keychain.
    config_json     TEXT,
    updated_at      INTEGER NOT NULL
);
-- Unlike a single "active provider" flag, multiple roles can be configured
-- and populated simultaneously — e.g. role='primary' -> Anthropic, AND
-- role='local' -> Ollama, both present at once. See §4.1.1 (ModelRouter).

CREATE TABLE app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);
-- Non-secret key/value app config. Notably 'active_profile'
-- ('simple' | 'developer'), default 'simple' — see §4.7. NEVER holds secrets;
-- API keys live in the OS keychain (§5), never here.

CREATE TABLE routines (
    id              TEXT PRIMARY KEY,        -- uuid4
    name            TEXT NOT NULL,           -- user-facing, e.g. "Weekly invoice summary"
    description     TEXT NOT NULL,           -- plain-language, shown in the library UI
    plan_json       TEXT NOT NULL,           -- the Routine plan, see §6.2
    created_from_conversation_id TEXT REFERENCES conversations(id),
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    run_count       INTEGER NOT NULL DEFAULT 0,
    last_run_at     INTEGER
);

CREATE TABLE routine_runs (
    id              TEXT PRIMARY KEY,
    routine_id      TEXT NOT NULL REFERENCES routines(id),
    started_at      INTEGER NOT NULL,
    completed_at    INTEGER,
    status          TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled')),
    step_log_json   TEXT                     -- array of {step_index, tool_id, result_summary}
);
```

### Python mirrors (representative, not exhaustive)

```python
# agent_core/tools/base.py

from typing import Protocol, Any
from dataclasses import dataclass
from enum import Enum

class RiskTier(str, Enum):
    LOW = "low"        # read-only, no undo needed
    MEDIUM = "medium"   # mutating, must have undo()
    HIGH = "high"       # not permitted in v1's default registry at all

@dataclass
class ToolDefinition:
    id: str
    label: str                  # plain-language, shown in permission cards
    description: str
    risk_tier: RiskTier
    parameters_schema: dict      # JSON Schema for the tool's arguments

class Tool(Protocol):
    definition: ToolDefinition

    def execute(self, args: dict, context: "ExecutionContext") -> "ToolResult": ...

    def undo(self, snapshot: "ActionSnapshot") -> None:
        """Required for any tool with risk_tier=MEDIUM or higher.
        A tool that cannot implement this MUST declare risk_tier=LOW
        and MUST NOT mutate state. Enforced at registration time —
        see ToolRegistry.register() in registry.py."""
        ...

@dataclass
class ToolResult:
    success: bool
    content: Any                 # returned to the model as the tool_result message
    snapshot: "ActionSnapshot | None" = None   # None for read-only tools
```

```python
# agent_core/providers/base.py

from typing import Protocol
from dataclasses import dataclass
from enum import Enum

class ModelRole(str, Enum):
    """Which job a configured provider is filling. Multiple roles may be
    configured and populated at once — this is not a single active-provider
    switch (see §4.1.1, ModelRouter)."""
    PRIMARY = "primary"                  # main conversation driver, typically a frontier cloud model
    LOCAL = "local"                      # self-hosted via Ollama, available once configured (§4.1.2)
    SETUP_ASSISTANT = "setup_assistant"  # onboarding-only free relay, unrelated to the above two

@dataclass
class ProviderCapabilities:
    native_tool_calling: bool
    max_context_tokens: int
    supports_streaming: bool
    runs_off_device: bool        # True only for local providers — informs privacy-sensitive routing
    vision: bool = False         # can analyze image input — gates the image path (§4.1.1, item A)
    audio: bool = False          # can analyze audio input

@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list["ToolCallRequest"]
    finish_reason: str

class ModelProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def send(self, messages: list["Message"], tools: list["ToolDefinition"]) -> ModelResponse: ...
```

```python
# agent_core/routines/model.py

from dataclasses import dataclass, field

@dataclass
class RoutineStep:
    step_id: str                       # local id within the routine, e.g. "step_1"
    tool_id: str                       # must reference a registered tool
    args_template: dict                # values may contain {{variable}} placeholders
    depends_on: list[str] = field(default_factory=list)   # step_ids that must complete first
    on_failure: str = "abort"          # "abort" | "skip" | "ask_user"
    model_role: str | None = None      # "primary" | "local" | None (None = use whatever the
                                        # current session/live toggle is set to). A privacy- or
                                        # cost-sensitive Routine can pin a step to "local" so it
                                        # always runs that way regardless of the live chat's
                                        # current selector state — see §4.1.1.
    model_id: str | None = None        # optional: pin this step to a SPECIFIC named model
                                        # (e.g. a cheap "haiku" draft vs a strong "opus" refine),
                                        # overriding role-based resolution. Passed as
                                        # ModelRouter.resolve(..., model_name=model_id) (§4.1.1).
                                        # This is the substrate the Model Cascade module (§6.8)
                                        # is built on — the module itself is v2.

@dataclass
class RoutineVariable:
    name: str
    prompt: str                        # what to ask the user for this value, if not supplied
    default: str | None = None

@dataclass
class Routine:
    id: str
    name: str
    description: str
    variables: list[RoutineVariable]
    steps: list[RoutineStep]
    # NOTE: no free-form code field exists on this structure, deliberately — see §6.1
```

### Amendment 2026-07-20: new persisted structures (Phase-2, provisional)

The shapes below are **representative, not final** — the amendment fixes the
*fields that must exist and the rules on them*, and defers exact column
types/retention to its open questions (§13). Treat the invariants (marked **MUST**)
as binding and the rest as a sketch to refine during the Phase-2 data-model pass.
Do not widen these past what the amendment states.

**Snapshots & the undeletable anchor (§4.9).** A snapshot is a point-in-time copy
of Addison's *mutable state* — settings, provider/routing config, skills, widgets,
routines, and the relevant `provider_config` / `app_settings` / skills / widgets /
routines rows.

> **SHIPPED 2026-07-20 (Phase-2 step 1) — this block is no longer provisional.**
> The sketch below was superseded by the implementation contract and by
> `agent_core/memory/schema.sql`, which is now authoritative. Four things changed
> and the old names are gone: the single three-value `reason` enum became **two
> columns**, `trigger` ∈ `auto` | `on_command` plus a `reason` slug from a closed
> vocabulary (`snapshot_manager.REASONS`) — the enum had nowhere to record *what
> change* prompted the capture; `is_anchor` became **`undeletable`** (it should name
> the guarantee the delete path enforces, not the provenance, and anchor-ness now
> has exactly one source of truth); `includes_binary` became **`captures_binary`**;
> and `payload_version` / `state_fingerprint` were added. See `docs/data-model.md`
> for the shipped column set and its reasoning.
>
> **The `created_in_mode` comment below — "mirrors existing artifact hiding" — was
> deliberately OVERRIDDEN, not implemented.** The column ships, but it is recorded
> **for display only** and **never filters a list, restore, prune, or delete query,
> in any mode.** Read literally, it would hide the way back from exactly the user
> who most needs it: someone who weakened a guard in Custom, broke something,
> switched to Simple, and now opens Restore points to an empty list. Snapshots are
> recovery machinery, not artifacts, and §8's artifact hiding is scoped to routines
> and widgets. Two tests hold the line — a behavioural one, and a source-level one
> that reads the SQL in `store.py` and `snapshot_manager.py` and fails if the column
> appears in a filter position.
>
> **Q8 (binary capture) is resolved, and narrower than this section implies** *(owner
> decision 2026-07-20)*: `binary_ref` holds a short build **reference** —
> `{"version", "identifier"}` from the new `shell.appBuildRef` — never bytes and
> never a path. Restore **reports** a build mismatch in plain language and changes
> settings only. **Restoring a previous binary is not implemented** and is a
> **Phase-3 updater** item; building a downgrade path here would put a second,
> uncoordinated binary-replacement mechanism on a collision course with the
> (unwired) `updater.rs`.

```sql
-- SUPERSEDED — kept for the record. The shipped DDL is in
-- agent_core/memory/schema.sql; see the note above for what changed and why.
CREATE TABLE config_snapshots (
    id                 TEXT PRIMARY KEY,       -- uuid4
    created_at         INTEGER NOT NULL,
    reason             TEXT NOT NULL,          -- 'auto_pre_change' | 'on_command' | 'weakening_anchor'
    verified_working   INTEGER NOT NULL DEFAULT 0,  -- 1 once a turn completes OK against this config (§4.9)
    created_in_mode    TEXT NOT NULL,          -- 'safe' | 'open' | 'custom' — mirrors existing artifact hiding
    is_anchor          INTEGER NOT NULL DEFAULT 0,  -- 1 = undeletable (minted on weakening a guard in Custom)
    includes_binary    INTEGER NOT NULL DEFAULT 0,  -- 1 ONLY for Custom anchors (§3.3 of the amendment)
    state_blob         TEXT NOT NULL           -- serialised mutable state; keys are NEVER included (G1)
);
-- Hard rules (MUST) — all of these survived into the shipped table:
--   * state_blob NEVER contains API keys or any keychain material (G1) — this holds
--     even for anchors. Restore leaves the keychain untouched; a restored provider
--     config re-binds to whatever keys are in the keychain by provider id.
--   * The conversation transcript is NOT part of a snapshot (history is append-only
--     and orthogonal; rollback restores config, never erases chats).
--   * Restore ALWAYS targets the most recent verified_working snapshot, not merely
--     "the state before the last edit."
--   * Permanent rows are undeletable by user AND model, and persist even if the
--     guard is later re-enabled. Ordinary snapshots are deletable. (Shipped
--     enforcement is in the DATABASE — two RAISE(ABORT) triggers — not in a WHERE
--     clause, and the genesis row carries the flag too, as the bottom of the walk.)
--   * The binary-capture mechanism was an OPEN question (amendment §13 Q8) and is
--     now resolved as a build REFERENCE — see the note above.
```

**Capability-tiered widget vocabulary (§8 note).** Widgets stay **declarative
specs, never code** (invariant 4 holds), but the vocabulary expands and each spec
now **declares the capability/tier it needs**, checked at save/render against the
active mode. SAFE admits only the non-destructive set; higher tiers admit
code-backed / system-capable kinds.

```python
# Phase-2, provisional — exact kind list & capability grammar are OPEN (amendment §13 Q7).
class WidgetCapability(str, Enum):
    DISPLAY_SAFE = "display_safe"   # SAFE: bounded vocab, trusted renderers, Addison's own safe storage
    SYSTEM       = "system"         # higher tiers only: code-backed / touches machine or network

# Existing SAFE launchers (unchanged): {kind:"routine"|"stat"|"command", ...}  (command is OPEN-only)
# NEW SAFE interactive display kinds — non-destructive by construction, no eval, CSP holds:
#   {kind:"todo",   title, items:[...]}        # to-do / checklist
#   {kind:"note",   title, body}               # free-text note
#   {kind:"timer",  title, ...}                # timer / counter
# Each spec carries the capability it needs; the tier check gates it:
#   SAFE  -> DISPLAY_SAFE only (buildable, non-destructive)
#   OPEN/Custom -> DISPLAY_SAFE + SYSTEM (code-backed monitors/scripts; run/arm via keyword gate §6)
# A widget MUST NOT exceed its mode's capability tier; a SAFE-tier widget MUST be
# non-destructive. created_in_mode hiding (SAFE hides OPEN-built artifacts) still applies.
```

**Routing config (§4.11).** The active routing strategy is non-secret app config,
so it lives alongside the existing provider/settings rows and is captured by
snapshots.

```python
# Phase-2, provisional.
class RoutingStrategy(str, Enum):
    QUALITY_FIRST = "quality_first"   # default — strongest capable, degrade down
    COST_FIRST    = "cost_first"
    LOCAL_ONLY    = "local_only"
    BALANCED      = "balanced"
    CUSTOM        = "custom"          # Developer-only builder
# Persisted as an app_settings/provider-config field (non-secret). The companion
# surface exposes only a "prefer quality / prefer free" toggle over this (§4.11).
```

**MCP-server config (§4.12).** An MCP server connection is **reversible config,
like a provider** — non-secret metadata persisted, secrets (if any) in the
keychain per G1, snapshotted, addable by prompting, revocable. It reuses the
add-an-endpoint plumbing; the concrete table shape lands with that work (open —
amendment §13 Q6, including how MCP tool metadata declares undo-ability).

---

## 4. Key Concepts

### 4.1 ModelProvider abstraction

Every model backend (Anthropic, OpenAI, Google, Ollama, the Setup Assistant relay, a user's BYOK connection) implements `ModelProvider` from §3. The orchestrator (§4.4) is written entirely against this interface and never branches on which concrete provider is active — capability differences are handled via `capabilities()`, not `isinstance` checks scattered through the codebase.

Concrete implementations, one file each under `agent_core/providers/`:

- `AnthropicProvider` — native tool-calling, primary v1 target for the `PRIMARY` role
- `OpenAIProvider` — native tool-calling, translates Addison's tool schema to OpenAI's function-calling format
- `OllamaProvider` — local models via Ollama, fills the `LOCAL` role once configured; see §4.1.2
- `SetupAssistantProvider` — calls the free relay (design doc §7.5.1), fills the `SETUP_ASSISTANT` role only, never holds a real key locally, degrades to a prompt-based tool-call parser if the underlying free model lacks native function-calling
- `DirectAPIProvider` — generic wrapper parameterized by provider name + key, used for BYOK; the key is fetched from the OS keychain at call time via the shell, never cached in Agent Core memory longer than a single request

**v1 supports multiple providers configured and reachable at once**, not a single active connection swapped over time. A user can have `PRIMARY` pointed at Anthropic via BYOK *and* `LOCAL` pointed at a downloaded Ollama model simultaneously — both usable within the same session. Resolving which one handles a given request is the `ModelRouter`'s job (§4.1.1), not a property the orchestrator tracks directly.

#### 4.1.1 ModelRouter — resolving which provider handles a given request

```python
# agent_core/providers/router.py

class ModelRouter:
    def __init__(self, configured: dict[ModelRole, ModelProvider]):
        self._configured = configured   # populated from provider_config at startup

    def resolve(self, requested_role: ModelRole | None = None) -> ModelProvider:
        """Returns the provider for a request. requested_role is an explicit
        override (from a UI selection or a Routine step's model_role field,
        §6.2); if None, defaults to PRIMARY. Falls back to whichever role IS
        configured if the requested one isn't (e.g. LOCAL requested but no
        Ollama model set up yet) — never a hard error mid-conversation,
        surface a plain-language notice in the Activity Panel instead."""

    def available_roles(self) -> list[ModelRole]:
        """Drives the frontend's model-role selector — only shows roles
        the user has actually configured. PRIMARY (via Setup Assistant or
        BYOK) is always available after onboarding; LOCAL only appears
        once a local model has been downloaded and verified (§4.1.2)."""
```

The orchestrator (§4.4) calls `model_router.resolve()` once per turn instead of holding a single `self.active_provider` reference. This is the core structural change enabling "multiple models for different things": PRIMARY and LOCAL can both be configured and reachable within the same running session, and which one handles a given message is a per-request decision, not a session-wide setting.

**How a role gets selected, in practice (v1 mechanisms, all explicit — no silent auto-routing):**

- **User toggle per message.** A small selector next to the input box (frontend, §7) lets the user pick "Cloud" or "Local" for the next message, defaulting to whatever they used last. **The `LOCAL` side is not a single model:** a user can configure several local models at once (e.g. a 14B vision model and an 8B text-only model) and the selector exposes a model dropdown within Local — item **B** below. This is the primary mechanism in v1 — simple, visible, no hidden decision-making about where a message goes.
- **Routine-level assignment.** A Routine (§6.2) can declare `model_role` on a step that involves model reasoning (e.g., a summarization step), so a privacy-sensitive Routine can be built once to always run locally, regardless of what the live chat's current toggle is set to. A step may also pin a specific local model by name (item B).
- **(A) Capability-gated input paths (v1).** Providers report `vision`/`audio` in `ProviderCapabilities` (§3). The orchestrator uses this to gate capability-specific input: if the user drops an image while the active model is text-only (`vision=False`), Addison says so plainly and offers to switch to a vision-capable model, instead of feeding an image to a model that can't see it and getting a hallucinated answer. In v1 this is a **warning + explicit switch**, never an automatic model change.
- **(B) Multiple local models with an explicit picker (v1).** The `LOCAL` role may hold more than one configured Ollama model at once; the user selects which per message from the Local dropdown, and a Routine step can pin one by name. Still fully explicit — the user picks; Addison never picks for them in v1.

**Planned for v2 — automatic task-based routing & auto-switching.** Deliberately deferred, not dropped. v2 will let Addison *choose* the model per turn from the task itself: a simple lookup to a small/cheap/local model, a hard multi-step or long-context task to a stronger one, an image or audio input to a `vision`/`audio`-capable model — automatically. The capability flags (item A) and the multi-model plumbing (item B) ship in v1 specifically to be the substrate this is built on. The v1→v2 line is intentional: v1 keeps every routing decision explicit and user-made (design doc §9, "no hidden decisions"); v2 adds an *optional* automatic layer on top that is **off by default, always overridable by the same manual picker, and always shows which model it chose and why in the Activity Panel**. Auto-routing must never be a silent or unaccountable decision — that constraint carries from v1 into v2 unchanged. Do not build the automatic layer before A and B are solid.

The Setup Assistant → BYOK handoff (design doc §7.5.1) still works exactly as before — it's just now described as the `SETUP_ASSISTANT` role being replaced by a newly-populated `PRIMARY` role in the router's `configured` dict, mid-conversation, with the message history untouched.

#### 4.1.2 OllamaProvider — local models, available post-setup

```python
# agent_core/providers/ollama_provider.py

class OllamaProvider:
    """Talks to a local Ollama instance over HTTP (default
    http://127.0.0.1:11434). Reuses OpenAIProvider's request/response
    translation where possible, since Ollama exposes an OpenAI-compatible
    endpoint — see design doc §7.3.2 for the product-level rationale."""

    def capabilities(self) -> ProviderCapabilities:
        # native_tool_calling depends on which model is loaded — queried from
        # Ollama's model metadata, not assumed; many small local models lack
        # reliable native function-calling, in which case Addison falls back
        # to the prompt-based tool-call parser also used by SetupAssistantProvider.
        # vision/audio likewise depend on the loaded model (a Mistral-based vision
        # model reports vision=True; a text-only model reports False) — queried per
        # model and used to gate the image path (§4.1.1, item A).
        ...
```

Setup for the LOCAL role is a distinct, explicit flow — not something enabled by default:

1. User opts in from Settings ("Run a model on this computer").
2. Hardware check (RAM/VRAM, disk space) before offering any model — see design doc §7.3.2 for the plain-language sizing UX.
3. Model download via Ollama, progress shown in-app. **More than one local model can be added** — each becomes a selectable entry under Local (item B, §4.1.1), e.g. a 14B vision model at ~12GB and an 8B text model at ~8GB.
4. Once at least one local model is verified working, `ModelRole.LOCAL` becomes available in `ModelRouter.available_roles()` and the per-message Local picker appears (a model dropdown when several are configured).

This is genuinely "post-setup" as the product framing requires: LOCAL is never available during the Setup Assistant conversation itself (design doc §7.5.1), since that flow already has its own free relay for onboarding — local model setup is a separate, later, user-initiated action once the user is already up and running.

### 4.2 Tool Registry & risk tiers

```python
# agent_core/tools/registry.py

class ToolRegistry:
    def register(self, tool: Tool) -> None:
        if tool.definition.risk_tier != RiskTier.LOW:
            if not hasattr(tool, "undo") or tool.undo is Tool.undo:
                raise ValueError(
                    f"Tool '{tool.definition.id}' has risk_tier={tool.definition.risk_tier} "
                    "but no undo() implementation. Either implement undo() or set risk_tier=LOW."
                )
        self._tools[tool.definition.id] = tool

    def get(self, tool_id: str) -> Tool: ...
    def list_for_model(self) -> list[ToolDefinition]: ...   # what gets sent to the LLM as available tools
```

This registration-time check is the literal enforcement of the design doc's constraint: **a tool without a real undo path is mechanically capped at read-only.** Do not bypass this with a default no-op `undo()` — that defeats the purpose. If a tool genuinely can't be undone, it stays LOW risk and read-only, full stop.

v1 tool set (register exactly these; see design doc §7.4.1 for rationale on each):

| `tool_id` | risk_tier | Module |
|---|---|---|
| `web_search` | low | `tools/web_search.py` |
| `read_file` | low | `tools/read_file.py` |
| `read_clipboard` | low | `tools/read_clipboard.py` |
| `calculator` | low | `tools/calculator.py` |
| `save_file` | medium | `tools/save_file.py` |
| `draft_message` | medium | `tools/draft_message.py` |
| `open_link` | low | `tools/open_link.py` |

**Profile note (§4.7):** *which* of these tools get registered — and whether any opt-in higher-risk tools are added on top — is chosen by the active Profile at startup. The Simple profile registers exactly the table above; the Developer profile may register additional opt-in tools. The registration-time undo check applies identically regardless of profile: a Profile decides *what* is registered, never *how* safety is enforced.

### 4.3 Permission Gate

```python
# agent_core/permissions/gate.py

class PermissionGate:
    def check(self, tool_id: str) -> PermissionStatus:
        """Returns GRANTED, DENIED, or NOT_YET_ASKED."""

    def request(self, tool_id: str) -> "PermissionRequest":
        """Emits an IPC event the frontend renders as a PermissionCard.
        Blocks the orchestrator's current step until the frontend calls
        `respond_to_permission_request()` — the model does not see a
        tool_result for this call until the user has answered."""

    def grant(self, tool_id: str, scope_details: dict | None = None) -> None: ...
    def revoke(self, tool_id: str) -> None: ...
```

The orchestrator calls `gate.check()` before every tool execution, not just the first time — a revoked permission (user changed their mind in Settings) must take effect immediately, not just block new grants.

### 4.4 Orchestration loop

```python
# agent_core/orchestrator.py (core loop, simplified)

def run_turn(self, conversation: Conversation, requested_role: ModelRole | None = None) -> None:
    provider = self.model_router.resolve(requested_role)   # per-turn resolution, §4.1.1 — not a stored attribute
    while True:
        response = provider.send(
            messages=conversation.messages,
            tools=self.tool_registry.list_for_model(),
        )
        if response.tool_calls:
            for call in response.tool_calls:
                status = self.permission_gate.check(call.tool_id)
                if status == PermissionStatus.NOT_YET_ASKED:
                    status = self.permission_gate.request(call.tool_id)  # blocks for UI response
                if status == PermissionStatus.DENIED:
                    result = ToolResult(success=False, content="User declined this permission.")
                else:
                    tool = self.tool_registry.get(call.tool_id)
                    result = tool.execute(call.args, self.execution_context)
                    if result.snapshot:
                        self.undo_manager.record(result.snapshot)
                conversation.append_tool_result(call.id, result)
            continue  # loop again with tool results appended
        else:
            conversation.append_assistant_message(response.text)
            self.stream_to_frontend(response.text)
            break  # turn complete
```

This same loop is reused, in a constrained form, by the Routine Engine (§6.4) — a Routine run is essentially this loop with tool calls coming from a saved plan instead of live model output, still passing through the same `PermissionGate`, `ToolRegistry`, and now `ModelRouter` (a step's `model_role`, if set, is passed as `requested_role`).

### 4.5 Rewind & Self-Repair

`agent_core/snapshots/undo_manager.py` implements the two mechanisms from the design doc (§7.9):

```python
class UndoManager:
    def record(self, snapshot: ActionSnapshot) -> None:
        """Called by the orchestrator after every tool execution that returns
        a non-None snapshot. Persists to action_snapshots table."""

    def undo_last(self, n: int = 1) -> list[UndoResult]:
        """Reverts the most recent n unreverted snapshots, most recent first,
        calling tool.undo(snapshot) for each. Marks them reverted=1."""

    def rewind_conversation(self, conversation_id: str, to_message_id: str) -> None:
        """Truncates message history back to (and including) to_message_id.
        Does NOT touch action_snapshots — conversational rewind and action
        rewind are independent, per design doc §7.9."""
```

Retention: a background job prunes `action_snapshots` older than the configured window (default 20 actions or 7 days) — implement as a simple SQL `DELETE ... WHERE created_at < ?` on startup, no need for anything more sophisticated in v1.

### 4.6 Setup Assistant mode

Not a separate code path in the orchestrator — it's `SetupAssistantProvider` plus a specialized system prompt (`agent_core/providers/prompts/setup_assistant.txt`, load and inject at conversation start if no `PRIMARY` role has been configured yet). The orchestrator loop itself is unchanged; only which role the `ModelRouter` resolves to, and the system prompt, differ. When the user completes BYOK setup, register a new `DirectAPIProvider` instance under `ModelRole.PRIMARY` in the router's `configured` dict and drop the setup-assistant system prompt from subsequent turns — `SETUP_ASSISTANT` and `PRIMARY` are independent roles, so this is additive, not a destructive swap.

### 4.7 Profiles

A **Profile** reshapes Addison's surface and default capabilities for a given audience (design doc §7.11, goal 9), layered over one shared engine. It is *configuration*, not a fork and not a security boundary — see the hard constraint in §8.7.

```python
# agent_core/profiles.py

from dataclasses import dataclass
from enum import Enum

class ProfileId(str, Enum):
    SIMPLE = "simple"        # default — the non-technical personas (design doc §5)
    DEVELOPER = "developer"  # opt-in — technical users

@dataclass
class Profile:
    id: ProfileId
    tool_ids: list[str]                 # which registered tools this profile exposes
    onboarding: str                     # "setup_assistant" | "byok_first"
    expose_routine_plan: bool = False   # Developer: view the declarative plan (read-only in v1, §6.5)
    headless_cli: bool = False          # Developer: expose the Agent Core JSON-RPC entry point for scripting
    raw_diagnostics: bool = False       # Developer: real errors/logs instead of translated messages
    allow_advanced_tools: bool = False  # Developer: permit opt-in higher-risk tools (still gated + undoable)
```

Resolution and effect:

- The active profile is read at startup from `app_settings` (`active_profile`, §3), default `SIMPLE`, and is switchable in Settings.
- It parameterizes exactly four things: (a) which tools `main.py` registers in the `ToolRegistry` (§4.2), (b) which onboarding path runs (Setup Assistant §4.6 vs. BYOK-first), (c) frontend feature flags (routine-plan view, raw-diagnostics panel, CLI hints), and (d) the default model-config path.
- It does **not** touch the `PermissionGate`, the undo-at-registration check, key handling, or the no-arbitrary-shell rule. Every safety invariant holds identically in both profiles (§8.7).

Profile-to-behaviour in v1:

| | Simple (default) | Developer (opt-in) |
|---|---|---|
| Onboarding | Setup Assistant (§4.6) | BYOK/model config up front |
| Tool set | exactly the §4.2 table | §4.2 table + opt-in higher-risk tools (still gated + undoable) |
| Routines | conversational authoring only (§6.3) | + read-only view of the declarative plan (structural editing stays v2, §6.5) |
| Errors | translated, plain-language | raw diagnostics available |
| Headless / CLI | not exposed | Agent Core JSON-RPC entry point exposed — essentially the step-4 CLI loop, productized |

The Developer profile deliberately reuses surfaces that already exist for other reasons: the "headless/CLI entry point" is the same JSON-RPC server the shell drives (§7) and the same loop built CLI-only in build step 4 — exposing it is a packaging decision, not new capability. Likewise "view the routine plan" is safe to expose precisely because the plan is declarative with no code field (§6.1).

### 4.8 Context budget & long-conversation continuation — planned for v2; v1 ships the substrate

**The problem.** A model's context window is a *per-request* token budget, not a per-session resource: every `provider.send()` replays the conversation, so long chats get linearly more expensive and slower each turn, degrade model attention, and eventually exceed the window outright. "Migrating to a new session" cannot escape this — a fresh conversation carrying the full transcript hits the same wall on its first request. The only real mechanisms are (a) summarize, (b) store externally and retrieve selectively, or (c) truncate. v2's continuation feature is a deliberate combination of all three; there is no fourth option, so nothing in v1 should pretend otherwise.

**What v2 builds — the Context Budget Manager.** An orchestrator-level mechanism (in `orchestrator.py`, alongside the loop in §4.4) that watches per-turn token usage against a threshold (e.g. ~70% of the resolved provider's `max_context_tokens` — per-provider via `capabilities()`, never hardcoded). When crossed, at the next turn boundary it:

1. produces a summary of the older portion of the conversation (a `model_router.resolve()` call — a Routine-style summarization step, so a privacy-sensitive user can have it run on `LOCAL`),
2. starts a continuation conversation seeded with: that summary, the user-confirmed `memory_facts`, and the most recent K turns verbatim,
3. records lineage (`conversations.continued_from_conversation_id`) and persists the summary (`conversations.summary`), leaving the full original transcript untouched in `messages`,
4. tells the user, in one plain sentence, that it condensed the older part of the chat and that nothing was deleted ("no hidden decisions", design doc §9). Not a modal, not a confirmation — a visible boundary marker in the thread.

**Hard rules (these carry into v2 unchanged):**

- It is **orchestrator machinery, not a registry tool.** It must never appear in `ToolRegistry`, never be model-invokable, and never surface a permission card — the model does not decide to rewrite its own memory. (Registry tools are user-consented *actions* with risk tiers; this is bookkeeping.)
- **Cut only at turn boundaries.** Never split an assistant `tool_use` from its `tool_result`s — a mid-turn cut produces an API-rejected history (the exact pairing bug fixed in build step 4).
- **`memory_facts` stays confirmation-only.** The continuation summary is conversation-scoped state, not long-term memory; it must not silently write facts (design doc §7.6).
- **Nothing is deleted.** The full transcript remains in `messages`; the summary is an *access path*, not a replacement. "What did we say earlier?" can always be answered from the stored transcript.
- **Same UX in both profiles.** Developer profile may additionally show token usage in raw diagnostics; the mechanism itself is identical (§4.7, §8.7).

**What v1 ships (the substrate, nothing more):** the two schema columns (`conversations.summary`, `conversations.continued_from_conversation_id` — landing with the step-6 store work), full-transcript persistence (step 6), and `ProviderCapabilities.max_context_tokens` (already present, §3). v1 does **not** measure tokens, does not summarize, and does not auto-continue — a v1 conversation that outgrows the window surfaces a plain-language error suggesting a new chat. Do not build the automatic layer before the store (step 6) and the shell thread UI (step 7) exist to host the boundary marker.

### 4.9 Amendment 2026-07-20: Snapshot / restore subsystem (G3) — the guaranteed-rollback floor

> **Built 2026-07-20 (Phase-2 step 1).** `agent_core/snapshots/snapshot_manager.py`,
> `agent_core/snapshots/scope.py`, the `config_snapshots` table, the `snapshot.*`
> RPC namespace, seven auto-capture hooks + one verified-working site, a sidecar
> cold-start recovery path, and the Settings **"Restore points"** card. The design
> below holds; the three places where the implementation is *narrower or more
> specific* than this prose are marked inline.

**Why this exists.** The motivating failure was a non-technical user who asked his
setup to "make the models run as cheaply as possible" and bricked it
*permanently* — the built-in rewind did not fire, and he had no way back. G3 is
the floor that makes that structurally impossible: **neither the user nor the
model can drive Addison into an unrecoverable configuration; at all times a
one-action restore to a last-known-working state exists, and the restore path is
itself unbreakable.**

Distinct from **§4.5 Rewind & Self-Repair**, which undoes *tool actions* and
truncates *conversation history*. The snapshot subsystem restores *Addison's own
configuration* (settings, provider/routing config, skills, widgets, routines).
The two are complementary and independent: a coding session that goes sideways has
fine-grained per-tool `undo()` (§4.5) *and* a whole-config restore (this section).

- **What a snapshot is / excludes.** A point-in-time copy of mutable state (§3
  amendment note). It **excludes API keys / the OS keychain** (G1 — a rollback can
  never move, expose, or clobber a key), **the app binary** (ordinary snapshots
  restore *state*, not *code*), and **the conversation transcript**.
- **When taken.** *Automatically* before any risky or sweeping change (a guard
  toggle, a provider/endpoint change, a bulk "make it cheaper" reconfiguration, a
  mode switch) — this is the guarantee and never depends on the user remembering.
  *On command* too, from a Settings control or by asking Addison ("snapshot now").
  *(Step 1 ships the Settings control and the RPC method. **Asking Addison** is
  step 2, and lands as a **LOW, capture-only** registry tool — it may only ever add
  a row, never restore and never delete.)*
- **Verified-working marking.** A config is marked *verified-working* after a turn
  completes successfully against it, and **restore targets the last
  verified-working state** — the difference between real recovery and the friend's
  dead end. *(Q4 resolved: "successful turn" = the turn's response was sent, i.e.
  `rpc/conversation.py` reached `_respond({"ok": True, …})`. A tool failure is
  deliberately not a turn failure. The mark does **not** flag the pre-change row —
  that config never ran; it captures the **current** config as a new verified row,
  deduped by fingerprint. And because restore never targets a config identical to
  the present one, **each click steps back one distinct proven configuration**.)*
- **Deletability & the undeletable anchor.** Ordinary snapshots are deletable
  housekeeping. The moment a safety guard is **turned off in Custom mode and
  saved**, Addison mints an **undeletable** anchor of the last verified-working
  state — removable by neither user nor model, surviving the guard being
  re-enabled; keys are **still** excluded. *(Owner decision 2026-07-20: the anchor
  **records the app build it was minted on** — a short `{"version", "identifier"}`
  reference, never bytes. The earlier "also captures the app binary / complete
  known-good build + config" wording promised more than the code does and was
  corrected. **Restoring a previous binary is a Phase-3 updater item**, not part of
  this floor. Q2 resolved: retention is 50 rows / 30 days, whichever keeps more,
  with permanent rows **and the newest verified row** exempt in the SQL — a rule
  that could prune the last verified row would switch G3 off silently. Anchors never
  prune and never count against the budget; evicting an anchor is deleting an
  anchor, whatever the code calls it.)*
- **Permanence is enforced in the database.** Two `RAISE(ABORT)` triggers on
  `config_snapshots` refuse to delete an `undeletable = 1` row and refuse to clear
  the flag — not a `WHERE` clause a future query can forget. The **genesis** row
  (written on first build, so G3 holds before the first turn) carries the flag too:
  it is the bottom of the restore walk.
- **`tool_grants` is excluded from capture.** Live consent state, not
  configuration — restoring it could reinstate a grant the user had revoked, via a
  deliberately ungated one-action button. A restore also clears the live in-session
  grants, so the session is never more permissive than the config it rolled back to.
- **The restore path is unbreakable.** The single most important Phase-2 test is
  "restore always works, **even from a broken config**." Build and harden this
  module *first* (amendment §14 Phase-2 step 1); everything else leans on it. *(As
  built, that means: the manager imports stdlib plus two schema-mirroring leaf
  modules and nothing else — no provider, router, profile, policy mode, registry or
  gate; retention and payload version are module constants, not settings, so nothing
  the model can write shrinks the rollback window; every payload is written **twice**,
  into the row and into a `0600` JSON sidecar at `<db_dir>/snapshots/<id>.json`; and
  `snapshot.list` + `snapshot.restoreLastWorking` are **exempt** from the
  build-failure short-circuit in `main.py`, so a database that will not open is
  answered from the sidecars — the restore renames the damaged file **aside, never
  deletes it**, and rebuilds in the same session with no restart. Restore is an RPC
  path, **never a registry tool and never gated**: a permission gate that could deny
  a restore would make this bullet false.)*

> **Reversible data vs. inviolable machinery.** Provider endpoints, model/routing
> choices, cost settings, which guards are on, skills, widgets, routines are all
> *reversible data* — user *and* model may change any of it, **because every such
> change is auto-snapshotted and one-action reversible.** Addison's own code, the
> orchestrator/gate/registry machinery, and the four floors (G1, G2, G3, anchor)
> are *inviolable machinery* — never alterable by user or model, in any mode. The
> "additional questioning" that fronts risky changes is **friction, not the safety
> net** (a determined user clicks through it and an injection could try to talk
> around it); **G3 is the actual guarantee.**

### 4.10 Amendment 2026-07-20: Simple / Developer / Custom + capability tiers + workspace-trust

**The third profile.** The mode stays derived from the profile (2026-07-19 model,
`policy.py` `mode_for_profile`: Simple→SAFE, Developer→OPEN). The amendment adds
**Custom**, reachable only deep in Settings behind additional questioning, whose
*prompting* guards are user-tunable — the per-invocation destructive card, the
auto-grant scope, the workspace-trust boundary, the keyword-gate strictness. The
user may **never** touch the floors: G1, G2, G3, and the undeletable-anchor rule
are **absent from the Custom panel entirely**. The Custom safety contract: it
lives deep behind extra confirmation; turning any guard *off* mints the
undeletable anchor (§4.9); the floors simply cannot be switched off. (Whether
Custom is reachable from Simple directly or only via Developer is open — §13 Q3;
current lean: reachable-but-deep regardless.)

**Capability tiers.** A capability tier gates *what a tool / widget / MCP tool may
do*, mapped from the mode: **SAFE → non-destructive capability only**; **OPEN /
Custom → non-destructive + code-backed / system capability.** This is the single
grammar behind the widget vocabulary (§3 note, §8 note), the MCP SAFE constraint
(§4.12), and the harness's code-backed tools.

**Workspace-trust — reconciling the agentic loop with the per-call gate.** Today
OPEN auto-grants non-destructive calls and raises a **per-invocation card for
every destructive one**, with no memory between them — correct for a chat butler,
*hostile* to a coding loop (dozens of edits/runs). Resolution:

- The user grants a **project directory** — an explicit, snapshotted act.
- **Inside** the trusted workspace, OPEN acts freely: edits/runs flow without a
  card per action. **The gate still runs and logs on every call** — it just
  doesn't *prompt* within the trusted scope.
- **Outside** the workspace (system paths, other directories, the keychain, the
  network beyond configured providers), destructive actions still raise the
  per-invocation card exactly as today.
- Trust is scoped, revocable, and snapshotted; revoking it or leaving the
  directory restores prompting. Every mutating tool keeps its `undo()`
  (invariant 2), and the workspace sits under the G3 snapshot floor.

The per-invocation card is **not weakened globally — it is *scoped*** to a
directory the user deliberately trusted.

### 4.11 Amendment 2026-07-20: Model routing strategies + free models

Brings the v1 substrate (capability flags + explicit picker, §4.1.1) toward
bounded auto-selection — a **strategy** layer over `ModelRouter.resolve()`, not a
rewrite of it. Four named strategies (curated from OmniRoute's 18) plus a Custom
builder:

- **Quality-first** *(default)* — strongest capable model, **degrade down** to
  cheaper/free on unavailability, rate-limit, or budget.
- **Cost-first** — cheapest capable, escalate only when needed.
- **Local-only** — never leaves the machine (privacy); local models only.
- **Balanced** — weighs capability, cost, and latency.
- **Custom** — a routing builder, **Developer only**.

**Exposure per surface.** Companion (Simple): a single **"prefer quality / prefer
free"** toggle over the strategy, default quality-first, no jargon. Developer: the
full picker + Custom builder.

**Quality floor + transparency.** Default is **strong-first, degrade-down** (the
inverse of OmniRoute's cheap-first) — the companion never silently gets a worse
answer. When a **free** model answers, a visible **"answered with a free model"**
disclaimer is shown. On unavailability/rate-limit, routing **falls forward** to a
stronger model rather than failing, with a plain-language note ("X was busy, so I
used your local model") and a light provider **cooldown** instead of hammering a
failing endpoint. (How much confidence-based escalation ships now vs. stays v2
substrate is open — §13 Q5.)

**Free / no-frontier-required models.** Addison must be genuinely useful **without
a paid frontier key** (central to the companion persona). Only **legitimate**
free/local sources are offered in-app; Ollama + the Setup Assistant relay already
cover the keyless path, and the OpenAI-compatible **custom-server** provider is
the extension hook for legit free cloud tiers. New endpoints are **addable by
prompting** ("add this endpoint") — registered as reversible provider-config data
(snapshotted; keys per G1). Gray-area aggregating routers are the **user's own
choice, documented on GitHub only**, and are **never surfaced, named, or endorsed
inside the app.** Explicitly *not* adopted from OmniRoute: mass provider/free-tier
farming, TLS/JA3-JA4 fingerprint spoofing (detection evasion — never built), team
quota-sharing, MCP/A2A-as-a-*gateway*, and 11-engine token compression.

### 4.12 Amendment 2026-07-20: MCP client (Addison consumes external tools)

Addison works with MCP as a **client** — it *consumes* external MCP servers/tools
— **not** as an MCP server or gateway (the OmniRoute-style thing still declined).

- **Through the existing registry + gate, never a side channel.** MCP tools are
  surfaced via the same `ToolRegistry` and `PermissionGate` as native tools —
  gated, logged, undo-aware. They inherit the risk-tier + undo rules unchanged.
- **Mode-scoped.** In OPEN/harness, MCP tools run under workspace-trust (§4.10) +
  the gate (and the keyword gate §6 to *run* powerful ones). In SAFE/companion
  they are constrained to **read-only or genuinely undo-able** tools — a mutating
  MCP tool with no `undo()` cannot be LOW-risk, so **invariant 2 keeps it out of
  the SAFE view automatically.** (The exact SAFE constraint — read-only only?
  curated allowlist? dev-only? — and how MCP tool metadata declares undo-ability
  are open, §13 Q6.)
- **Reversible config.** Connecting an MCP server is reversible provider-like
  config (§4.11) — snapshotted, addable by prompting, revocable; it shares the
  add-an-endpoint plumbing.

---

## 5. Credentials & Key Handling

- **BYOK keys**: entered in the frontend, sent via IPC to the Rust shell, written to the OS keychain (`shell/src-tauri/src/keychain.rs`). The Agent Core requests the key from the shell (via a dedicated IPC call, not a shared file) only at the moment it's needed for a `DirectAPIProvider` call, and does not persist it in its own process memory beyond that call.
- **Setup Assistant relay keys**: live only in the relay's own server-side secret store (external to this repo entirely) — the desktop app and Agent Core never possess them, by construction. There is no code path in this repository that could leak them, because they never enter it.
- **Device identity keypair**: generated in the Rust shell on first launch (`shell/src-tauri/src/keychain.rs`), private key stored in OS keychain, public key + `device_id` mirrored to the `device_identity` SQLite row for the Agent Core's use when signing relay requests.

**Amendment 2026-07-20 (G1 reaffirmed, reinforced).** Keys remain keychain-only
and, per the snapshot subsystem (§4.9), are **excluded from every snapshot** —
ordinary snapshots *and* the Custom-mode undeletable anchor, even though that
anchor uniquely records a build reference. A snapshot's `state_blob` **MUST NOT**
contain API keys or any keychain material; a rollback can never move, expose, or
clobber a key. After a restore, whatever keys are in the keychain remain, and a
restored provider config re-binds to them by provider id. Endpoints/MCP servers
added "by prompting" (§4.11–§4.12) follow this unchanged: their secrets (if any)
go to the keychain, their non-secret config is what gets snapshotted.

---

## 6. Automation / Routine Engine

This is the section implementing "small scripts that automate features the user wants," scoped to fit the security model in §9 of the design doc.

### 6.1 Concept & design rationale — why not literal scripts

OpenClaw-style harnesses let skills be arbitrary code or shell commands. That's wrong for this product for the same reason unrestricted shell access is wrong for the tool registry (design doc §9): a non-technical user cannot review, and Addison's own model cannot be fully trusted to always write, safe arbitrary code, and a bad automation is worse than a bad single tool call because it's designed to run again without a human in the loop each time.

**Resolution: a Routine is a declarative plan — an ordered (or DAG-shaped, via `depends_on`) sequence of calls into the exact same `ToolRegistry` used everywhere else, with templated arguments, not an interpreted scripting language.** This gets you the actual thing the user wants — "automate this sequence of things Addison did" — without introducing a second, less-audited execution surface alongside the tool system. Every step in a Routine is subject to the identical risk-tier and permission-gate rules as if the model had called that tool live in conversation (§6.4).

This is a deliberate, documented divergence from OpenClaw's model. Do not "upgrade" this to an embedded interpreter (Python `eval`, a Lua sandbox, etc.) without a full security review — that would reintroduce exactly the attack surface the rest of this document works to avoid.

### 6.2 Routine data structure

Already shown in §3 as Python dataclasses; here's the JSON form as saved in `routines.plan_json`:

```json
{
  "id": "a1b2c3d4",
  "name": "Weekly invoice summary",
  "description": "Reads the invoices I drop in, adds them up, and saves a summary to my Desktop.",
  "variables": [
    { "name": "output_filename", "prompt": "What should I name the summary file?", "default": "invoice_summary.docx" }
  ],
  "steps": [
    {
      "step_id": "step_1",
      "tool_id": "read_file",
      "args_template": { "path": "{{dropped_file_path}}" },
      "depends_on": [],
      "on_failure": "abort"
    },
    {
      "step_id": "step_2",
      "tool_id": "calculator",
      "args_template": { "expression": "sum of amounts found in {{step_1.result}}" },
      "depends_on": ["step_1"],
      "on_failure": "abort",
      "model_role": "local"
    },
    {
      "step_id": "step_3",
      "tool_id": "save_file",
      "args_template": { "filename": "{{output_filename}}", "content": "{{step_2.result}}" },
      "depends_on": ["step_2"],
      "on_failure": "ask_user"
    }
  ]
}
```

Notes on the schema:
- `args_template` values may reference `{{variable_name}}` (user-supplied at run time) or `{{step_id.result}}` (output of an earlier step in the same run) — resolved by the engine before each tool call, never interpreted as code.
- `on_failure` is one of `"abort"` (stop the whole run, surface the error), `"skip"` (continue to the next independent step), or `"ask_user"` (pause and ask, same UI pattern as a permission card).
- `model_role`, when a step involves model reasoning rather than a pure tool call, pins that step to `"primary"` or `"local"` regardless of the live session's current toggle (§4.1.1) — in the example above, `step_2`'s summarization/extraction work is pinned to run locally, e.g. because invoice contents are sensitive, while `step_3` is a plain tool call with no model involved and so has no `model_role` at all.
- `model_id`, when set, pins the step to a specific named model regardless of `model_role` — the substrate the Model Cascade module uses to send a "draft" step to a cheap model and a "refine" step to a strong one (§6.8).
- There is no field anywhere in this schema for raw code, shell commands, or arbitrary expressions — this is intentional and should stay that way (§6.1).

### 6.3 Creation flow — conversational, not hand-authored

Non-technical users don't write Routines directly; Addison writes them, from a conversation, on request.

```python
# agent_core/routines/builder.py

class RoutineBuilder:
    def propose_from_recent_actions(self, conversation: Conversation, n_messages: int = 10) -> Routine:
        """Triggered when the user says something like 'can you do this
        automatically next time' or 'save this as a routine'. Looks back
        over the last n_messages, extracts the tool calls that were made
        (NOT the model's prose), and generalizes literal values into
        {{variables}} where they look like per-run inputs (e.g. a specific
        filename becomes {{output_filename}}, a specific file path the
        user dropped in becomes {{dropped_file_path}}).
        Returns a draft Routine — NOT yet saved."""

    def present_for_confirmation(self, draft: Routine) -> None:
        """Emits an IPC event rendering a plain-language preview of the
        draft Routine in the chat: name, description, and a numbered list
        of what it will do each time it runs — not raw JSON. The user can
        edit the name/description inline, or approve/reject each step's
        inclusion. This reuses PermissionCard-style UI, not a new modal."""

    def save(self, draft: Routine, conversation_id: str) -> Routine:
        """Persists to the routines table only after explicit user
        confirmation — never silently."""
```

The generalization step (turning a literal value into a `{{variable}}`) is a heuristic, not guaranteed correct — the confirmation UI in `present_for_confirmation` must let the user see and correct exactly what got turned into a variable, since getting this wrong is the most likely source of a Routine that behaves unexpectedly on its second run.

### 6.4 Execution engine

```python
# agent_core/routines/engine.py

class RoutineEngine:
    def run(self, routine: Routine, variable_values: dict[str, str]) -> RoutineRunResult:
        run_id = new_uuid()
        step_results: dict[str, ToolResult] = {}
        for step in topologically_sorted(routine.steps):   # respects depends_on
            resolved_args = self._resolve_template(step.args_template, variable_values, step_results)
            status = self.permission_gate.check(step.tool_id)
            if status != PermissionStatus.GRANTED:
                # Routines NEVER auto-escalate permissions the user hasn't
                # already granted in live conversation. A step needing an
                # ungranted tool pauses and asks, exactly like §4.3.
                status = self.permission_gate.request(step.tool_id)
            if status == PermissionStatus.DENIED:
                return self._handle_failure(step, run_id, "permission denied")
            tool = self.tool_registry.get(step.tool_id)
            result = tool.execute(resolved_args, self.execution_context)
            if result.snapshot:
                self.undo_manager.record(result.snapshot)   # Routine runs are undoable too
            step_results[step.step_id] = result
            if not result.success:
                if step.on_failure == "abort":
                    return self._handle_failure(step, run_id, result.content)
                elif step.on_failure == "ask_user":
                    self._pause_for_user_decision(step, run_id)
                # "skip" falls through to the next step
        return RoutineRunResult(run_id=run_id, status="completed", step_results=step_results)
```

Critical invariant: **the Routine Engine calls the same `ToolRegistry` and `PermissionGate` instances as the live orchestrator — it does not have, and must never be given, elevated or pre-approved access beyond what the user has already granted in normal conversation.** A Routine is a shortcut for re-issuing a sequence of tool calls, not a way to bypass the permission system.

Each run writes a `routine_runs` row with a step-by-step log — this is what backs the "show what you just did" command (design doc §7.9.1) when the activity in question was a Routine rather than a live conversation turn.

### 6.5 Routine library UI (frontend contract)

`RoutineLibrary.tsx` lists saved Routines with: name, description, last-run time, a "Run now" button (prompts for any variables without defaults, then calls the engine), and an edit/delete action. Editing a Routine in v1 is limited to name, description, and variable defaults — editing the step sequence itself is a v2 feature; for v1, "delete and recreate via conversation" is the supported path for structural changes, which is consistent with keeping the authoring surface conversational rather than a form-based step editor.

### 6.6 Example Routines (for grounding, not literal fixtures)

- **"Summarize my weekly PDFs"** — `read_file` (repeated per dropped file) → model summarization (not a tool call, handled by the LLM turn itself) → `save_file`.
- **"Draft my Monday check-in email"** — `read_file` (a notes file) → `draft_message`. No `send` step exists because no send-capable tool exists in v1 (design doc §7.4.1) — the Routine opens a draft in the user's mail client, same as live conversation would.
- **"Look up and save today's exchange rate"** — `web_search` → `calculator` (unit conversion) → `save_file`.

### 6.7 Explicitly deferred: triggers and scheduling

v1 Routines are **manually triggered only** — a button press, or a chat command ("run my invoice summary"). There is no cron-like scheduler, no "run this every Monday," and no event-based trigger (email arrival, file-watch, etc.) in this version. This is intentional and matches the design doc's non-goal on always-on/scheduled agents (§4, §9) — an unattended Routine running on a timer is a materially different trust model (nobody's watching when it fires) than one a user explicitly starts. Do not add a scheduler to `RoutineEngine` without revisiting that decision explicitly; it is not an oversight.

**Amendment 2026-07-20: the automation model (G2 resolution, Phase-2).** The
friend's connection monitor needs background polling + autonomous notification —
which G2 forbids Addison from doing *itself*. The resolution keeps G2 as a floor
while enabling the use case:

> **Addison authors; the OS runs; Addison never triggers itself. Powerful/armed
> actions require a user-typed keyword prefix.**

- **Author, don't fire.** Addison may **write and set up** OS-level automation — a
  `launchd`/`cron` entry, a small watcher script — exactly as Claude Code can
  scaffold a cron job. **The OS** runs it on its schedule; **Addison itself never
  fires anything autonomously.** G2 ("no autonomous self-triggering by Addison")
  therefore **holds unchanged** — this is not a scheduler *inside* Addison, so the
  "do not add a scheduler to `RoutineEngine`" rule above still stands. Setting up
  OS automation is a snapshotted, reversible config act.
- **The keyword gate.** Running/arming a powerful or elevated action (including
  arming an OS-run automation, or running a code-backed / system-capable widget)
  requires the user to type a **specific keyword prefix** in front of the message.
  Ordinary chat is unaffected. Exact syntax is open — `!run …`, `arm:` … (§13 Q1).
- **Also a prompt-injection defence.** Because the prefix is **user-typed**,
  content Addison merely *observes* (a web page, a file, a tool result) **cannot
  supply it.** Observed content can instruct the model but cannot type a keystroke
  into the composer — so the prefix aligns the "elevated action" boundary with the
  one thing injected content can never forge.

**Code-backed routines/widgets are higher-tier only.** In line with the capability
tiers (§4.10): SAFE routines stay declarative plans (§6.1) and SAFE widgets stay
the non-destructive vocabulary (§3 note). Only in OPEN/Custom may a routine step
or widget be **code-backed / system-capable** (a monitor, a script) — governed by
workspace-trust (§4.10), per-tool `undo()`, the snapshot floor (§4.9), and the
keyword gate to *run or arm* one. The declarative-only constraint in §6.1 is
therefore a **SAFE** constraint, not a global one — but SAFE-1 (no arbitrary code
in SAFE) is untouched.

### 6.8 Model cascade module (draft → refine) — a module, not core

A common request is a "cheap model drafts, strong model verifies and polishes" pipeline (e.g. Haiku drafts code, Opus refines it). Addison supports this **as an optional module composed from Routines — not as logic in the orchestrator or `ModelRouter`.** A cascade is literally a two-step Routine:

1. a `draft` step — a model-reasoning step pinned via `model_id` (§6.2) to a cheap model;
2. a `refine` step — `depends_on` the draft, pinned to a strong model, taking `{{step_1.result}}` as input.

Because it's a Routine, it inherits everything: the same `ToolRegistry` and `PermissionGate` (§6.4), the declarative-only constraint (no code field, §6.1), undo, and the run log. **No new core code, no new tool, no new capability** — which is exactly why it's a module and not a built-in. The orchestrator's per-turn `model_router.resolve()` (§4.4) is unchanged and has no knowledge a cascade exists.

- **Substrate (v1):** `RoutineStep.model_id` (§3, §6.2) pins a step to a specific named model, resolved via `ModelRouter.resolve(role, model_name)` (§4.1.1). Item B already added multi-model plumbing for the LOCAL role; the cascade extends named-model selection to cloud models too.
- **The module (v2):** a shipped draft→refine Routine template, surfaced under the Developer profile (§4.7), sequenced with automatic routing (§4.1.1). Both are optional layers over the shared engine; neither is core.

**Economics — document wherever the module is offered; it is NOT a guaranteed saving.** A cascade saves money only when the strong refiner *edits* the draft (small output) rather than *rewriting* it: the draft cost plus the extra input tokens the refiner spends reading the draft must be outweighed by the reduction in the refiner's (expensive) output tokens. When drafts are poor and get rewritten, the cascade costs *more* than calling the strong model directly. LLM-verifying-LLM is not reliably cheaper than generating fresh — verification burns reasoning tokens. The cheap, reliable verifier for code is a compiler/linter/test-runner, which v1 deliberately lacks (no code execution, §8.1) — so v1's cascade is as much a quality pass as a cost lever. Present the tradeoff honestly.

---

## 7. Frontend ↔ Agent Core IPC Contract

JSON-RPC 2.0 methods, implemented in `agent_core/main.py` and called from `shell/src/ipc/client.ts`. Representative subset:

| Method | Direction | Purpose |
|---|---|---|
| `conversation.sendMessage` | Frontend → Core | User sends a chat message |
| `conversation.streamChunk` | Core → Frontend | Streamed assistant text |
| `permission.requestGrant` | Core → Frontend | Renders a PermissionCard |
| `permission.respond` | Frontend → Core | User's Allow/Deny answer |
| `tool.activityUpdate` | Core → Frontend | Drives the Activity Panel ("Searching the web…") |
| `undo.rewindConversation` | Frontend → Core | §4.5 |
| `undo.undoLastAction` | Frontend → Core | §4.5 |
| `routine.proposeFromConversation` | Frontend → Core | Triggers `RoutineBuilder.propose_from_recent_actions` |
| `routine.confirmSave` | Frontend → Core | §6.3 |
| `routine.list` / `routine.run` / `routine.delete` | Frontend → Core | §6.5 |
| `model.availableRoles` | Core → Frontend | Drives the model-role selector (§4.1.1) — which of PRIMARY/LOCAL are currently configured |
| `model.setRoleForNextMessage` | Frontend → Core | User's per-message Cloud/Local toggle selection |
| `model.startLocalSetup` / `model.localSetupProgress` | Frontend ↔ Core | Drives the Ollama hardware-check → download → verify flow (§4.1.2) |
| `keychain.getDeviceKey` / `keychain.getProviderKey` | Core → Shell (Rust-internal, not exposed to frontend) | §5 |

Keep `protocol.py` (Agent Core) and `types/protocol.ts` (frontend) hand-synced for v1 — a code-generation step (e.g., generating TS types from the Python dataclasses) is a reasonable Phase 3 improvement, not a v1 requirement.

---

## 8. Security Constraints (non-negotiable, restated for the implementer)

These are hard constraints, not preferences — flag to the user if any of these appear to conflict with a specific implementation request rather than silently working around them:

1. No tool may execute arbitrary shell commands or unrestricted code. The Routine Engine (§6) is declarative for the same reason.
2. Every `risk_tier != LOW` tool must have a real `undo()`, enforced at registration (§4.2) — not a convention, a runtime check that raises if violated.
3. API keys of any kind never reach the frontend/webview process. They live in the OS keychain, accessed only by the Rust shell and the Agent Core at the moment of use.
4. The Setup Assistant relay's keys never exist inside this repository's runtime at all — they're external, server-side, out of scope for the desktop app's trust boundary.
5. A Routine never has permissions beyond what the user has already granted in live conversation — no privilege escalation via automation.
6. No scheduling/autonomous triggering in v1 (§6.7).
7. Profiles (§4.7) are a surface / default-capability layer, **not** a security boundary. Switching to the Developer profile never bypasses the permission gate, the undo-at-registration check, key isolation, or the no-arbitrary-shell rule. A profile chooses what is registered and shown; every invariant above holds identically in all profiles. If a request implies a profile that relaxes any of these, flag it rather than implementing it.

### Amendment 2026-07-20: the four global floors (supersedes the flat list above where noted)

The 2026-07-19 mode-scoped model already replaced item 7's "profiles are never a
security boundary" framing: the profile *does* select a policy mode (Simple→SAFE,
Developer→OPEN), and OPEN relaxes SAFE's prompting — but never the global floors.
This amendment adds **G3** and the **anchor**, giving **four global floors that no
mode or guard (not even Custom) can switch off**. Flag any conflict rather than
working around it.

| Floor | Statement |
|---|---|
| **G1** | API keys never reach the frontend/webview or SQLite; keychain-only. **Reinforced:** excluded from every snapshot, including the Custom-mode undeletable anchor (§4.9, §5). |
| **G2** | No autonomous self-triggering / scheduling *by Addison*. **Reinterpreted (§6):** Addison may *author* OS-run automation; the OS runs it; Addison never fires it. |
| **G3** | Guaranteed one-action rollback to a last-verified-working state; the restore path is itself unbreakable (§4.9). **New.** |
| **Anchor** (**G4** in `CLAUDE.md` and in code — the two names are the same rule) | Turning a guard *off* in Custom mode mints an **undeletable** snapshot that **records the app build it was minted on** (a reference, not the binary; keys still excluded). Restoring a binary is a Phase-3 updater item — owner decision 2026-07-20, §4.9. **New.** |

**Reinterpreting invariant 4 (widgets).** SAFE invariant 4 said "widgets are
declarative specs, never code." The amendment's owner decision: **widgets are
buildable in *all* modes; the mode gates the *capability* a widget may use, not
whether one can be built.**

- **SAFE stays non-destructive by construction.** SAFE-tier widgets are drawn from
  a **safe, bounded, declarative vocabulary** — the existing launchers
  (routine / stat / command) **plus interactive display kinds** (to-do/checklist,
  note, timer) rendered by *trusted Addison components* backed by Addison's own
  safe storage. **No shell, no destructive filesystem/system reach, and no
  arbitrary code or eval** — so **SAFE-1 (no arbitrary code) and the webview CSP
  still hold.** "Build me a to-do widget" now works in SAFE and produces a real,
  usable checklist.
- **Higher tiers add code-backed / system-capable widgets** (monitors, scripts —
  the friend's connection monitor), governed by workspace-trust (§4.10), per-tool
  `undo()`, the snapshot floor (§4.9), and the keyword gate to *run or arm* one
  (§6).
- **Surviving guarantee:** a widget **never exceeds its mode's capability tier**,
  and a **SAFE-tier widget is non-destructive by construction.** Each spec declares
  the capability it needs; the tier check gates it at save and render (unknown
  kinds/capabilities rejected). (Exact kinds & capability grammar are open —
  §13 Q7.)

**MCP tools in SAFE (invariant 2 does the enforcing).** MCP tools flow through the
existing registry + gate (§4.12). In SAFE they are limited to **read-only or
genuinely undo-able** tools: a mutating MCP tool with no `undo()` cannot be
LOW-risk, so **invariant 2 mechanically keeps it out of the SAFE view** — no new
enforcement code required. OPEN runs them under workspace-trust. (Exact SAFE
constraint open — §13 Q6.)

---

## 9. Testing Strategy

- **Tool Registry**: unit test that registering a MEDIUM/HIGH-risk tool without `undo()` raises — this is the single most important test in the codebase, since it's the mechanical enforcement of the entire safety model.
- **Orchestration loop**: test with a mock `ModelProvider` that returns scripted tool-call sequences, asserting the permission gate is consulted before every execution.
- **Routine Engine**: test template resolution (`{{variable}}` and `{{step_id.result}}` substitution) in isolation from tool execution; test `on_failure` behavior for each of the three modes; test that a Routine step requiring an ungranted permission pauses rather than executes.
- **Undo Manager**: test that `undo_last(n)` reverts in reverse-chronological order and correctly marks snapshots as reverted so they aren't double-reverted.
- **IPC contract**: golden-file tests against `protocol.py`/`protocol.ts` to catch drift between the two schemas early, given they're hand-synced in v1.

---

## 10. What NOT to build yet

Explicitly out of scope for the initial implementation pass — do not add these without checking back against the design doc's roadmap:

- OpenAI/Google providers (Anthropic only for the first working build)
- Automatic task-based model routing / auto-switching — **planned for v2** (§4.1.1), deliberately deferred; v1 routing is explicit/user-selected only. (Multiple local models with an explicit picker — item B — *is* in v1; only the *automatic* choice among them is v2.)
- The Model Cascade module (draft → refine, §6.8) — **planned for v2**; v1 ships only its substrate (`RoutineStep.model_id`, per-step named-model pinning). It is a Routine-based *module*, never orchestrator/router core.
- The Context Budget Manager / automatic long-conversation continuation — **planned for v2** (§4.8); v1 ships only its substrate (the `conversations.summary` + `continued_from_conversation_id` columns and full-transcript persistence, step 6). Orchestrator machinery only — never a registry tool.
- Messaging channel integrations (Telegram/WhatsApp)
- Routine step-*editing* UI (delete-and-recreate is sufficient for v1). The Developer profile (§4.7) may expose a *read-only* view of the declarative plan, but structural step editing stays v2.
- Any form of Routine scheduling/triggers (§6.7)
- A Rust rewrite of the Agent Core

**Amendment 2026-07-20 — what moves in, what stays out.** These lines shift with
the amendment; the boundaries are deliberately narrow:

- **Model routing** — bounded **strategies now ship** (quality-first / cost-first /
  local-only / balanced + Custom, §4.11), superseding the old "explicit picker
  only" line as Phase-2. **Still deferred:** *fully-automatic task classification*
  (choosing the model from the task itself). Strategies are user-selected policies
  with a strong-first, transparent, degrade-down default — not a hidden per-task
  classifier. How much confidence-based escalation ships now is open (§13 Q5).
- **Scheduling** — **still no self-trigger by Addison** (G2 holds). What the
  amendment *adds* is "Addison **authors** OS-run automation, the **OS** runs it"
  (§6) — this is not a scheduler inside `RoutineEngine`, so the §6.7 deferral of an
  in-app scheduler stands.
- **MCP** — Addison as an MCP **client** is now in scope (§4.12). Addison as an MCP
  **server or gateway** remains **out** (as does A2A).
- **Widgets** — code-backed / system-capable widgets are now buildable **in higher
  tiers only** (§8 note); SAFE gains safe interactive kinds. Still out: any widget
  that exceeds its mode's capability tier.
- **Unchanged deferrals:** messaging-channel integrations, Routine step-editing UI,
  a Rust rewrite of the Agent Core, and the two v2 ecosystem-survey items (Routine
  export/import sharing, untrusted-content screening) all stay deferred.

---

## 11. Implementation Order

Build in this sequence — each step should be independently testable before moving to the next:

1. SQLite schema + Python dataclass mirrors (§3)
2. `ToolRegistry` + the registration-time undo check (§4.2), with 2-3 LOW-risk tools (`calculator`, `read_file`) to prove the pattern
3. `PermissionGate` (§4.3) with a minimal in-memory frontend stub (no Tauri yet) to validate the request/respond flow
4. `AnthropicProvider` + a minimal `ModelRouter` (single role — `PRIMARY` only at this point) + the orchestration loop (§4.4), CLI-only — get a working chat loop with tool use before touching the desktop shell at all
5. Add remaining v1 tools (`save_file`, `draft_message`, `web_search`, `read_clipboard`, `open_link`) with their `undo()` implementations
6. `UndoManager` (§4.5) — conversational rewind first (simpler), then action rewind. This step also lands the §4.8 substrate: full-transcript persistence in `messages` and the `conversations.summary` / `continued_from_conversation_id` columns (the Context Budget Manager itself is v2 — §10)
7. Tauri shell + JSON-RPC IPC (§7), wiring the CLI prototype from step 4 into a real desktop window
8. `RoutineBuilder` + `RoutineEngine` (§6) — only once steps 2-6 are solid, since Routines depend on all of them
9. `SetupAssistantProvider` + the free relay integration (design doc §7.5.1) — deliberately after the core loop works end-to-end on a known-good provider, since it's the most externally-dependent piece
10. `OllamaProvider` + full `ModelRouter` (adding the `LOCAL` role) + the frontend's model-role selector and local-setup flow (§4.1.2) — last, since it's explicitly a post-setup feature that only makes sense once a `PRIMARY` path (via step 9 or BYOK) already works; building it earlier would have nothing to route *against*. This step also lands item **B** (multiple local models + the Local model-picker) and item **A** (the `vision`/`audio` capability flags gating the image path); *automatic* routing across models is v2 (§4.1.1), not built here
11. **Profiles (§4.7)** — introduce `profiles.py` (the `Profile` config + resolver) and the `app_settings.active_profile` row, then parameterize `ToolRegistry` registration and the onboarding path by the active profile. The Simple profile *is* what steps 1–10 already build, so this largely formalizes existing defaults rather than adding behaviour. Then add the profile selector in Settings and the Developer-only surfaces incrementally: BYOK-first onboarding, the read-only routine-plan view, the raw-diagnostics panel, and the headless/CLI entry point (which is the step-4 CLI loop, productized). Land after the shell (step 7) so the selector has somewhere to live. This step must never add a code path around the permission gate — see §8.7

### Amendment 2026-07-20: Phase-2 order (docs first, then code)

The amendment's changes are **Phase-2**, sequenced after the numbered v1 order
above. Its §14 mandates **authoritative docs are updated first** (this note is part
of that pass); code then follows in **dependency order, safety floor first**:

1. **Snapshot / restore subsystem (G3, §4.9)** — the floor everything leans on;
   built and hardened *first*. The single most important Phase-2 test: **restore
   always works, even from a broken config.** Includes automatic + on-command
   snapshots and the app **build reference** recorded by Custom anchors.
   **Shipped 2026-07-20** — see the note at the head of §4.9.
2. **Custom profile + guard model** (`policy.py`) + the **undeletable-anchor rule**
   (§4.9, §4.10).
3. **Routing strategies** (4 + Custom) + the companion prefer-quality/prefer-free
   toggle + free-model disclaimer + graceful fallback/cooldown (§4.11).
4. **Free-model endpoints** — first-class legit free/local + add-by-prompt (shares
   plumbing with connecting an MCP server, step 7) (§4.11).
5. **Harness + workspace-trust** (OPEN) — the trust boundary the powerful
   capabilities below depend on (§4.10).
6. **Widget capability tiers + expanded vocabulary** — safe interactive kinds
   (to-do/checklist, note, timer) with trusted renderers + safe storage (buildable
   in all modes); capability-tier gating; make `primary.txt` capability-aware
   (§3 note, §8 note).
7. **MCP client integration** — external tools through the registry + gate,
   mode-scoped (§4.12).
8. **Automation keyword gate** + author-OS-run automation (§6).

Each Phase-2 step stays independently testable and ships behind the same gate as
today. Steps 3–4 are companion-facing and independent of the harness, so they can
proceed in parallel with 5–8 once 1–2 land. Open questions to resolve *during* the
doc/spec pass, not invent past, are the amendment's §13 (keyword syntax, snapshot
retention, Custom reachability, verified-working definition, auto-routing depth,
MCP-in-SAFE constraint, widget kinds/grammar, anchor binary capture).
