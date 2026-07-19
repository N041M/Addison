# Architecture

Addison is one desktop application made of three processes held at three trust
levels, so the security model is enforced by the process boundary rather than by
convention. This document covers the trust boundaries between the processes and the
internal shape of the Agent Core. For the runtime flows across these boundaries, see
[flows.md](flows.md); for the persisted state, see [data-model.md](data-model.md).

Back to the [README](../README.md).

## Trust boundaries

```mermaid
flowchart TB
    subgraph webview["React webview — lowest trust"]
        direction TB
        UIrender["Renders chat, permission cards, settings"]
        UInote["No network. No core access. Never sees a key."]
    end

    subgraph shell["Tauri shell in Rust — highest trust"]
        direction TB
        SendCmd["send_to_core: validates and relays webview frames"]
        StoreKeyCmd["store_provider_key: write-only key path"]
        Supervisor["Agent Core supervisor and stdout pump"]
        Keychain["keychain.rs: provider keys, device keypair"]
        Filesystem["filesystem.rs: pickers, scoped handles, save and delete"]
    end

    subgraph core["Agent Core in Python — no OS permissions of its own"]
        direction TB
        Server["JsonRpcServer: read loop and turn worker"]
        Engine["Orchestrator, tools, permission gate, routines"]
        Store["SQLite store, on device"]
    end

    UIrender -->|"invoke send_to_core"| SendCmd
    UIrender -->|"invoke store_provider_key"| StoreKeyCmd
    SendCmd -->|"one JSON-RPC line to core stdin"| Server
    Server -.->|"core-message and core-status events"| UIrender
    Engine -.->|"shell.* and keychain.* requests over stdout"| Supervisor
    Supervisor --> Keychain
    Supervisor --> Filesystem
    StoreKeyCmd --> Keychain
```

What each process may and may not do:

- **React webview (lowest trust).** It renders state and turns clicks into typed IPC
  calls. It reaches the shell through exactly three Tauri commands — `send_to_core`
  for everything conversational, and the write/delete-only pair
  `store_provider_key` / `delete_provider_key` for saving or removing a key the
  user typed. It has no network access, cannot talk to the core directly, and
  can never read a key back. The shell rejects any relayed frame whose method is in
  the `shell.*` or `keychain.*` namespace, so the lowest-trust process can never
  drive the OS-level side.
- **Tauri shell (highest trust).** It is a relay and a supervisor, not a
  decision-maker. It spawns the Agent Core as a child process, pumps its stdout, and
  answers the core's `shell.*` and `keychain.*` requests in-process. `keychain.rs` is
  the only place a key value is handled in the shell, and it is strictly asymmetric:
  the webview may write a key, but only the core can read one back over stdio, and
  the device private key never leaves the module except as an in-memory signing key.
  `filesystem.rs` gives the core only opaque handles and paths the shell itself
  minted this session, so the core structurally cannot wander outside the user's live
  selection.
- **Agent Core (orchestration, no OS permissions).** It runs the conversation loop,
  the typed tools, the permission gate, the routine engine, and the SQLite store.
  Every filesystem, clipboard, external-app, or keychain effect leaves the core as a
  Core-to-Shell request; the core never makes a raw syscall.

## Agent Core components

Inside the core, `orchestrator.py` is the single fan-in. The three sibling packages —
`tools/`, `providers/`, and `routines/` — must not import from one another; only the
orchestrator (and the outer `JsonRpcServer` that wires everything) knows about all
three. That boundary is what lets the routine engine replay tool calls through the
exact same registry and gate as the live loop.

```mermaid
flowchart TB
    Server["JsonRpcServer (main.py): IPC, persistence, wiring"]
    Orch["Orchestrator: the single fan-in over the three packages"]

    subgraph tools["tools/"]
        TR["ToolRegistry: undo check at registration"]
        Tool["Typed tools: calculator, read_file, save_file, ..."]
    end
    subgraph providers["providers/"]
        MR["ModelRouter: resolve provider per turn"]
        Prov["AnthropicProvider, OpenAIProvider, GoogleProvider, OllamaProvider, SetupAssistantProvider"]
    end
    subgraph routines["routines/"]
        RE["RoutineEngine: replays a declarative plan"]
        RBL["RoutineBuilder and RoutineLibrary"]
    end

    PG["PermissionGate"]
    UM["UndoManager"]
    Store["Store: SQLite"]

    Server --> Orch
    Server --> RE
    Orch --> MR
    MR --> Prov
    Orch --> TR
    TR --> Tool
    Orch --> PG
    Orch --> UM
    RE --> TR
    RE --> PG
    RE --> UM
    UM --> Store
    RBL --> Store
    Server --> Store
```

The shared instances are the point of the diagram: the `Orchestrator` and the
`RoutineEngine` are handed the **same** `ToolRegistry`, `PermissionGate`, and
`UndoManager` objects, so a routine can never out-permission the live conversation.

Component by component:

- **Orchestrator** — the turn loop. It resolves a provider per turn through the
  `ModelRouter` (there is no single active provider), sends the conversation, and for
  each requested tool call consults the permission gate, executes the tool through
  the registry, records an undo snapshot, and feeds the result back to the model
  until the model returns plain text. The same loop is reused, constrained, by the
  routine engine, which is why the gate and registry live here and not inside any
  provider.
- **ToolRegistry** — holds the typed tools and enforces the central invariant at
  registration: a tool whose risk tier is not LOW must implement a real `undo()`, or
  registration raises. This single check is the mechanical backbone of the safety
  model. Mode-scoped safety (owner decision 2026-07-19, `policy.py`): a `dev_only`
  tool (only `run_command` today) is exempt from that check and lives in the ONE
  shared registry, but is filtered out of the SAFE view — `visible_tools(mode)`
  returns it only in OPEN mode, so the Simple profile can neither see nor run it,
  while routines still share the same registry instance (no second registry).
- **PermissionGate** — consulted before every tool execution, not just the first, so
  a revoked grant takes effect immediately. It is mode-aware (`authorize`): in SAFE
  mode it prompts for every not-yet-granted tool exactly as before; in OPEN mode it
  auto-allows non-destructive calls (recording them in the activity log) and prompts
  **per invocation** for destructive ones — no prior grant is consulted and none is
  recorded, so approving one destructive command never authorizes a later one, and
  the card names the exact command text each time (`detail`, truncated ~120 chars).
  The gate still runs on every call in both modes. Destructiveness is per-call
  (`run_command` classifies its own command via a read-only allowlist; any other
  tool is destructive iff its tier is HIGH). Non-dev tools keep the coarse
  session-grant model it tracks; the consent prompt itself is an IPC round-trip to
  the webview.
- **UndoManager** — records an action snapshot per mutating tool call and reverses
  the most recent ones on request, and separately truncates message history for a
  conversational rewind. The two mechanisms are independent.
- **ModelRouter** — resolves which provider handles a request from an explicit role
  (PRIMARY, LOCAL, SETUP_ASSISTANT) and an optional model name. Multiple roles and
  several models per role can be configured and reachable at once; the choice is
  always explicit in v1.
- **Providers** — one adapter per backend. `AnthropicProvider`, `OpenAIProvider`, and
  `GoogleProvider` are cloud providers (multi-provider, owner decision 2026-07-18);
  `OpenAIProvider` also backs an OpenAI-compatible **custom server** via a `base_url`
  override and an optional key. `OllamaProvider` runs local models, and
  `SetupAssistantProvider` fills the onboarding relay role. Each connected cloud
  provider contributes models to one picker union; a by-name pick resolves to that
  provider's instance in the router. The orchestrator never branches on the concrete
  provider; it reads capabilities instead.
- **Provider connections** — keys are stored per provider id (`anthropic | openai |
  google | custom`) in the OS keychain; `provider.connect` validates a saved key with
  one tiny request, then registers the provider's models. Non-secret connection
  metadata (connected, added date, custom base URL) lives in `provider_config`;
  `provider.list`/`connect`/`disconnect` responses never carry key material.
- **RoutineBuilder / RoutineLibrary / RoutineEngine** — build a declarative plan from
  a recent conversation, store and list saved routines, and replay a plan's steps
  through the shared gate and registry. Mode-scoped safety (`policy.py`): a plan step
  may carry an OPEN-mode-only `command` (run through the `run_command` dev-only tool,
  same gate + registry, so a destructive command still prompts). A routine's
  `created_in_mode` column records the mode it was saved under; routines created in
  OPEN mode are hidden from `routine.list` and refused by `routine.run` in SAFE mode,
  and return untouched in OPEN. Command routines can only be saved in OPEN mode.
- **Widgets and usage** — server/orchestrator machinery, not registry tools. After
  each provider call the orchestrator's `on_usage` hook records a `usage_log` row
  (tokens + latency) at that single choke point; `stats.get` derives the token meter
  and per-provider latency from it. Widgets themselves are **declarative specs**
  (`agent_core/widgets.py`) — a saved-routine Run pill (which runs through the existing
  `routine.run` path, adding no execution surface) or a whitelisted stat display —
  validated at save *and* at render, never code. In OPEN mode a third `command` kind
  is valid (runs `run_command` on click, same gate); it is rejected in SAFE mode, and
  like routines a widget's `created_in_mode` hides OPEN-created widgets while the
  Simple profile is active. They are proposed like routines (draft held in the core,
  saved only on an explicit confirm) and stored in the `widgets` table.
- **Store** — the SQLite access layer. It reads and writes the transcript, action
  snapshots, routines, usage, widgets, and settings; it holds no secrets, since keys
  live only in the keychain.
