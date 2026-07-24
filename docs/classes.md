# Class diagrams

> **Amended 2026-07-20** — see [Scope Amendment](addison-scope-amendment-2026-07.md).
> Adds the `SnapshotManager` (global floor **G3**, guaranteed rollback), the
> Simple/Developer/**Custom** mode-and-guard model with capability tiers, a
> `RoutingStrategy` abstraction (four named strategies + custom, with graceful
> fallback), and an `McpClient` external-tool surface over the existing registry +
> gate. Members marked *(Phase-2)* describe shape the amendment implies but that is
> not yet in code.

The core in three views: orchestration, providers, and routines. Attributes and
methods are the real ones from the code, trimmed to the load-bearing members. The
`tools/`, `providers/`, and `routines/` packages do not import one another; the
orchestrator is the only module that knows all three.

Back to the [README](../README.md); see also [architecture.md](architecture.md),
[flows.md](flows.md), and [data-model.md](data-model.md).

## Core orchestration

The turn loop and the safety machinery. `Tool` is a structural protocol; a tool whose
`risk_tier` is not LOW must implement a real `undo()`, and `ToolRegistry.register`
raises otherwise.

```mermaid
classDiagram
    class Orchestrator {
        +run_turn(conversation, requested_role, model_name, effort)
    }
    class Conversation {
        +id
        +messages
        +append_tool_result(tool_call_id, result)
        +append_assistant_message(text)
        +append_assistant_tool_calls(text, tool_calls)
    }
    class Message {
        +role
        +content
        +tool_call_id
        +tool_calls
    }
    class Tool {
        <<interface>>
        +definition
        +execute(args, context) ToolResult
        +undo(snapshot)
    }
    class ToolDefinition {
        +id
        +label
        +description
        +risk_tier
        +parameters_schema
    }
    class RiskTier {
        <<enumeration>>
        LOW
        MEDIUM
        HIGH
    }
    class ToolRegistry {
        +register(tool)
        +get(tool_id) Tool
        +list_for_model()
    }
    class PermissionGate {
        +check(tool_id) PermissionStatus
        +request(tool_id) PermissionStatus
        +grant(tool_id)
        +revoke(tool_id)
        +clear_denials()
    }
    class UndoManager {
        +record(snapshot)
        +undo_last(n) UndoResult
        +redo_last(n) UndoResult
        +rewind_conversation(id, to_message_id)
        +prune()
        +can_redo()
    }
    class ActionSnapshot {
        +id
        +tool_call_id
        +tool_id
        +undo_payload
        +created_at
        +reverted
    }
    class ToolResult {
        +success
        +content
        +snapshot
    }
    class Store {
        +insert_message()
        +messages_for_conversation()
        +truncate_messages()
        +insert_action_snapshot()
        +recent_unreverted_snapshots()
    }

    Orchestrator --> ToolRegistry
    Orchestrator --> PermissionGate
    Orchestrator --> UndoManager
    Orchestrator ..> Conversation
    Conversation "1" *-- "many" Message
    ToolRegistry o-- Tool
    Tool --> ToolDefinition
    ToolDefinition --> RiskTier
    Tool ..> ToolResult
    ToolResult --> ActionSnapshot
    UndoManager --> Store
    UndoManager ..> ActionSnapshot
```

## Modes, guards, and snapshots

The scope amendment layers three things onto the safety machinery above: a third
**Custom** profile whose *prompting* guards are user-tunable, **capability tiers** that
gate what a tool or widget may do per mode, and the **SnapshotManager** that makes
global floor **G3** (guaranteed rollback) real. The mode is still derived from the
active profile; Custom is a tuned overlay whose *floors* are fixed. The
`SnapshotManager` captures app-state snapshots (config/DB rows — never keys, so G1
holds), marks a configuration verified-working after a turn completes, and restores to
the last verified-working state. Turning a guard off in Custom mode mints an
**undeletable anchor** that records the app build it was minted on (a reference, not
the binary — owner decision 2026-07-20; see `data-model.md`). `WorkspaceTrust` scopes
the gate's OPEN-mode auto-grant to a user-granted project directory.

**`SnapshotManager` shipped in Phase-2 step 1**, so its members below are real and the
signatures are the ones in `agent_core/snapshots/snapshot_manager.py`. Three names in
the earlier sketch were wrong and are corrected here: `snapshot(reason)` is
**`capture(...)`** (the verb set is capture / restore / mint_anchor / prune, never
record / undo_last, so it can never be confused with `UndoManager`);
`mark_verified_working(config_id)` takes **no argument** (there is no config-identity
concept in the data model — it captures the *current* config as a new verified row,
deduped by fingerprint); and `Snapshot.payload` is **`ConfigSnapshot.state_blob`**,
because dataclasses mirror their table 1:1 and the column is `state_blob`.
`restore(snapshot_id)` and `restore_last_working()` **both** exist: the second is the
G3 floor — the one-action button, which cannot take an argument — and is implemented
as the first, so there is one code path. `mint_anchor()` got its caller in Phase-2
step 2: `guards.set` mints the anchor before persisting any weakening, deduped by
fingerprint so repeated toggling cannot grow an unbounded permanent list.

```mermaid
classDiagram
    class Profile {
        <<enumeration>>
        SIMPLE
        DEVELOPER
        CUSTOM
    }
    class PolicyMode {
        <<enumeration>>
        SAFE
        OPEN
    }
    class CapabilityTier {
        <<enumeration>>
        NON_DESTRUCTIVE
        CODE_BACKED
        SYSTEM_CAPABLE
    }
    class GuardConfig {
        +destructive_card
        +auto_grant_scope
        +workspace_trust : Phase-2 step 5
        +keyword_gate_strictness : Phase-2 step 8
    }
    class WorkspaceTrust {
        +root_dir
        +granted_at
        +contains(path) bool
        +revoke()
    }
    class SnapshotManager {
        +capture(trigger, reason, verified_working, prune) ConfigSnapshot
        +mark_verified_working() ConfigSnapshot
        +restore(snapshot_id) RestoreResult
        +restore_last_working() RestoreResult
        +last_working_target() dict
        +mint_anchor(reason) ConfigSnapshot
        +list()
        +delete(snapshot_id)
        +prune()
    }
    class ConfigSnapshot {
        +id
        +created_at
        +trigger
        +reason
        +payload_version
        +state_blob
        +state_fingerprint
        +verified_working
        +undeletable
        +captures_binary
        +binary_ref
        +created_in_mode
    }

    Profile --> PolicyMode
    GuardConfig ..> Profile
    GuardConfig ..> PolicyMode
    GuardConfig --> WorkspaceTrust
    PermissionGate ..> GuardConfig
    PermissionGate ..> CapabilityTier
    SnapshotManager ..> ConfigSnapshot
    SnapshotManager --> Store
```

`mode_for_profile` is a module function in `policy.py`, not a `GuardConfig` member:
Simple→SAFE, Developer and Custom→OPEN, with `GuardConfig` as the Custom profile's
overlay on the OPEN gate. `CapabilityTier` is what the
gate and the widget validator consult to decide whether a tool/widget's requested
capability is admissible in the active mode — SAFE admits only `NON_DESTRUCTIVE`.
Neither `ConfigSnapshot.undeletable` anchors nor the four floors (G1, G2, G3, the
anchor rule — **G4** in code and in `CLAUDE.md`; the two names are the same rule) are
reachable from `GuardConfig`. `SnapshotManager`, `ConfigSnapshot`, the `CUSTOM`
profile and `GuardConfig` (its two shipped fields, in `policy.py`) are **shipped**
and their names are fixed — Phase-2 step 2 built the Custom profile and the guard
overlay, and lowering a guard mints the G4 anchor through `guards.set`.
`WorkspaceTrust` (step 5), `CapabilityTier` (step 6) and `GuardConfig`'s two
remaining fields are still *(Phase-2)* sketches whose names are not.

`SnapshotManager` depends on `Store` and nothing else in this diagram — deliberately.
It reaches no provider, router, profile, policy mode, registry, or gate, because the
restore path has to work when any of those is broken. For the same reason **restore is
never a registry tool and never passes the `PermissionGate`**: a gate that could deny a
restore would make "the restore path is itself unbreakable" false. The only
model-facing snapshot surface is a **LOW, capture-only** `snapshot_now` tool
(`agent_core/tools/snapshot_now.py`, in both v1 profiles) that may add a row and
nothing else — it reaches the `SnapshotManager` through a **late-bound** ref (the
registry is built before the manager exists, so it answers "can't save yet" until the
store is up) and calls only `capture(...)`, never restore/delete/prune.

## External tools via MCP

Addison is an MCP **client** — it consumes external MCP servers — never a server or
gateway. `McpClient` adapts each remote tool into the *existing* `ToolRegistry`, so an
MCP tool is registered, gated, logged, and undo-checked exactly like a native tool
(§ Core orchestration). Because a mutating tool with no `undo()` cannot be LOW-risk,
invariant 2 automatically keeps such an MCP tool out of the SAFE view. Connecting a
server is reversible, snapshotted config, sharing the add-an-endpoint plumbing.

```mermaid
classDiagram
    class McpClient {
        +connect(server_config) McpConnection
        +disconnect(server_id)
        +list_connections()
        +adapt_tools(registry)
    }
    class McpConnection {
        +server_id
        +transport
        +connected
        +tools
    }
    class McpToolAdapter {
        +definition
        +execute(args, context) ToolResult
        +undo(snapshot)
        +declares_undo
    }

    McpClient ..> McpConnection
    McpClient ..> McpToolAdapter
    McpToolAdapter ..|> Tool
    McpClient ..> ToolRegistry
```

`McpToolAdapter` satisfies the same `Tool` protocol as native tools, which is what lets
it flow through the one shared registry + gate. All members here are *(Phase-2)*.

## Providers and routing

The orchestrator is written against the `ModelProvider` protocol and never branches on
the concrete provider; capability differences are read from `ProviderCapabilities`.
The concrete providers satisfy the protocol structurally (duck-typed, shown here as
realization). `ModelRouter` resolves a provider per turn from a role and an optional
model name, with several models reachable per role.

Phase-2 step 3 **shipped** the bounded routing layer. A routing strategy orders the
fallback chain behind the user's standing default model, which always heads the chain:
**quality-first** (default), **cost-first**, **local-only** (no model call leaves the
machine — the Setup Assistant relay included), plus a Developer-only **custom** ordered
list. **Balanced was cut from v1 by owner decision** (amendment §10.1): the drafted
version was indistinguishable from cost-first at two-model pools. The companion surface
is a single "prefer quality / prefer free" toggle. On failure the turn falls forward
gracefully: only on a provider-unavailable failure (a rejected request or bad key ends
the turn instead), with a plain note ("[X] was busy, so Addison used [Y]."), an
in-memory per-provider cooldown, a per-turn deadline, and an "Answered with a free
model." chip whenever routing — not an explicit pick — chose a free model.

```mermaid
classDiagram
    class ModelProvider {
        <<interface>>
        +capabilities() ProviderCapabilities
        +send(messages, tools, effort) ModelResponse
    }
    class ProviderCapabilities {
        +native_tool_calling
        +max_context_tokens
        +supports_streaming
        +runs_off_device
        +vision
        +audio
    }
    class AnthropicProvider {
        +send(messages, tools, effort) ModelResponse
    }
    class OpenAIProvider {
        +send(messages, tools, effort) ModelResponse
    }
    class GoogleProvider {
        +send(messages, tools, effort) ModelResponse
    }
    class OllamaProvider {
        +send(messages, tools, effort) ModelResponse
    }
    class SetupAssistantProvider {
        +send(messages, tools, effort) ModelResponse
    }
    class ModelRole {
        <<enumeration>>
        PRIMARY
        LOCAL
        SETUP_ASSISTANT
    }
    class RoutingStrategy {
        <<enumeration>>
        QUALITY_FIRST
        COST_FIRST
        LOCAL_ONLY
        CUSTOM
    }
    class RoutingCandidate {
        +model_id
        +role
        +provider_id
        +quality_rank
        +free
        +local
    }
    class ModelRouter {
        +resolve(requested_role, model_name) ModelProvider
        +register(role, provider)
        +register_local_model(name, provider)
        +register_primary_model(name, provider)
        +available_roles()
        +available_local_models()
        +selected_primary_model()
        +selected_local_model()
    }
    class ModelResponse {
        +text
        +tool_calls
        +finish_reason
    }
    class ToolCallRequest {
        +id
        +tool_id
        +args
    }

    ModelProvider <|.. AnthropicProvider
    ModelProvider <|.. OpenAIProvider
    ModelProvider <|.. GoogleProvider
    ModelProvider <|.. OllamaProvider
    ModelProvider <|.. SetupAssistantProvider
    ModelProvider ..> ProviderCapabilities
    ModelProvider ..> ModelResponse
    ModelResponse --> ToolCallRequest
    ModelRouter o-- ModelProvider
    ModelRouter ..> ModelRole
    RoutingCandidate ..> RoutingStrategy
```

All members are shipped code. The strategy layer lives beside the router, not on it:
`resolve_chain(strategy, candidates, head, custom_order)` is a pure module function in
`providers/router.py` that orders `RoutingCandidate`s, and the attempt loop — per-send
continuation, cooldown, the per-turn deadline — is orchestrator machinery. The router
itself still answers one question: which provider instance serves this role and model
name.

## Routines

A routine is a declarative plan: an ordered, DAG-shaped list of tool calls with
templated arguments and no code field anywhere. The builder drafts one from a recent
conversation, the library stores and lists them, and the engine replays a plan through
the same permission gate, tool registry, and undo manager as the live loop. Saved
routines are declarative artifacts, so they are part of the app state the
`SnapshotManager` captures (§ Modes, guards, and snapshots) and are restored with a
rollback. Under the amendment, an OPEN-mode `command` step still raises the gate's
per-invocation destructive card unless it runs inside a trusted workspace, and any
routine that arms OS-run automation is subject to the keyword gate.

```mermaid
classDiagram
    class Routine {
        +id
        +name
        +description
        +variables
        +steps
    }
    class RoutineStep {
        +step_id
        +tool_id
        +args_template
        +depends_on
        +on_failure
        +model_role
        +model_id
    }
    class RoutineVariable {
        +name
        +prompt
        +default
    }
    class RoutineRunResult {
        +run_id
        +status
        +step_results
        +detail
    }
    class RoutineBuilder {
        +propose_from_recent_actions(conversation, n) Routine
        +preview(draft, tool_registry)
        +save(draft, conversation_id) Routine
    }
    class RoutineLibrary {
        +list()
        +get(routine_id) Routine
        +update_metadata()
        +record_run(routine_id)
        +delete(routine_id)
    }
    class RoutineEngine {
        +run(routine, variable_values) RoutineRunResult
    }

    Routine "1" *-- "many" RoutineStep
    Routine "1" *-- "many" RoutineVariable
    RoutineBuilder ..> Routine
    RoutineLibrary ..> Routine
    RoutineEngine ..> Routine
    RoutineEngine ..> RoutineRunResult
```
