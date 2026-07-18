# Class diagrams

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

## Providers and routing

The orchestrator is written against the `ModelProvider` protocol and never branches on
the concrete provider; capability differences are read from `ProviderCapabilities`.
The three concrete providers satisfy the protocol structurally (duck-typed, shown here
as realization). `ModelRouter` resolves a provider per turn from a role and an optional
model name, with several models reachable per role.

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
    class ModelRouter {
        +resolve(requested_role, model_name) ModelProvider
        +register(role, provider)
        +register_local_model(name, provider)
        +register_primary_model(name, provider)
        +available_roles()
        +available_local_models()
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
    ModelProvider <|.. OllamaProvider
    ModelProvider <|.. SetupAssistantProvider
    ModelProvider ..> ProviderCapabilities
    ModelProvider ..> ModelResponse
    ModelResponse --> ToolCallRequest
    ModelRouter o-- ModelProvider
    ModelRouter ..> ModelRole
```

## Routines

A routine is a declarative plan: an ordered, DAG-shaped list of tool calls with
templated arguments and no code field anywhere. The builder drafts one from a recent
conversation, the library stores and lists them, and the engine replays a plan through
the same permission gate, tool registry, and undo manager as the live loop.

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
