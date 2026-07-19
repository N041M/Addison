# Runtime flows

Sequence diagrams for the main flows across the three processes. Method and function
names match the code. Every Core-to-webview frame in these diagrams actually reaches
the webview as a `core-message` (or `core-status`) event relayed by the Rust shell;
the diagrams draw it as a direct arrow to keep the relay hop from repeating on every
line.

See also: [architecture.md](architecture.md), [data-model.md](data-model.md),
[classes.md](classes.md), and the [README](../README.md).

## 1. Send-message turn

A user message runs on the core's single turn worker: the read loop parses the frame
and queues it, the worker calls `_run_send_message`, and the orchestrator drives the
provider-and-tools loop until the model returns plain text.

```mermaid
sequenceDiagram
    participant WV as React webview
    participant SH as Rust shell
    participant SRV as Core server
    participant ORC as Orchestrator
    participant PG as PermissionGate
    participant TL as Tool
    participant UM as UndoManager
    participant PR as Provider

    WV->>SH: invoke send_to_core, conversation.sendMessage
    SH->>SRV: one JSON-RPC line to core stdin
    Note over SRV: _read_loop, _dispatch, queued to the turn worker
    SRV->>SRV: _run_send_message
    SRV->>ORC: run_turn(conversation, role, model, effort)
    ORC->>PR: provider.send(messages, tools, effort)
    PR-->>ORC: response with tool_calls
    ORC->>PG: check(tool_id)
    PG-->>ORC: NOT_YET_ASKED
    ORC->>PG: request(tool_id), blocks for the UI
    Note over PG,WV: permission.requestGrant then permission.respond, see flow 2
    PG-->>ORC: GRANTED
    ORC->>TL: execute(args, context)
    TL-->>ORC: ToolResult with snapshot
    ORC->>UM: record(snapshot)
    ORC->>PR: provider.send with the tool_result appended
    PR-->>ORC: final assistant text
    ORC->>SRV: stream_to_frontend(text)
    SRV-->>WV: conversation.streamChunk notification
    SRV-->>WV: response with userMessageId and assistantMessageId
```

## 2. Permission grant round-trip

The gate's consent prompt is an IPC round-trip. The worker thread parks an event
keyed by the tool id, emits the card, and blocks; the answering frame arrives on the
read loop and wakes the worker. A grant is remembered; a "Not now" only lasts the
rest of the current turn.

```mermaid
sequenceDiagram
    participant ORC as Orchestrator
    participant PG as PermissionGate
    participant SRV as Core server
    participant WV as React webview

    ORC->>PG: request(tool_id)
    PG->>SRV: _on_permission_request(tool_id)
    Note over SRV: park a threading.Event keyed by tool_id
    SRV-->>WV: permission.requestGrant, toolId label description riskTier
    Note over WV: user taps Allow or Not now
    WV->>SRV: permission.respond, toolId and allow
    SRV->>SRV: _handle_permission_respond sets the event
    PG-->>ORC: GRANTED or DENIED
    Note over PG: GRANTED is remembered, DENIED clears at the next user turn
```

## 3. Conversation history

History landed recently. Listing counts only user and assistant rows; loading rebuilds
the in-memory transcript from user and non-empty assistant rows and skips persisted
tool rows on purpose — the store never persists an assistant turn's `tool_calls`, so
replaying tool rows would send unpaired tool results and the provider would reject the
next turn. A new conversation gets a fresh uuid but no store row until its first real
turn, and the title is written first-write-wins from the first user message.

```mermaid
sequenceDiagram
    participant WV as React webview
    participant SRV as Core server
    participant ST as Store

    WV->>SRV: conversation.list
    SRV->>ST: list_conversations()
    ST-->>SRV: rows, tool rows excluded from the count
    SRV-->>WV: conversations, newest first

    WV->>SRV: conversation.load, conversationId
    SRV->>ST: messages_for_conversation(id)
    ST-->>SRV: full transcript
    Note over SRV: keep user and non-empty assistant rows, skip tool rows
    SRV-->>WV: conversationId, title, messages

    WV->>SRV: conversation.new
    Note over SRV: fresh uuid, no store row yet, created lazily on first turn
    SRV-->>WV: conversationId

    WV->>SRV: conversation.sendMessage, first message
    SRV->>ST: create_conversation lazily, then set_conversation_title
    Note over ST: title is first-write-wins from the first user message
```

## 4. Undo and conversational rewind

Two independent mechanisms. Action undo reverses the most recent mutating tool actions
through their snapshots; conversational rewind truncates the transcript. They never
touch each other's state.

```mermaid
sequenceDiagram
    participant WV as React webview
    participant SRV as Core server
    participant UM as UndoManager
    participant TL as Tool
    participant ST as Store

    WV->>SRV: undo.undoLastAction
    SRV->>UM: undo_last(1)
    UM->>ST: recent_unreverted_snapshots(1)
    ST-->>UM: latest snapshot
    UM->>TL: undo(snapshot)
    UM->>ST: mark_snapshot_reverted(id)
    UM-->>SRV: UndoResult
    SRV-->>WV: ok, detail, canRedo

    WV->>SRV: undo.rewindConversation, toMessageId
    SRV->>UM: rewind_conversation(id, toMessageId, keep_anchor false)
    UM->>ST: truncate_messages after the anchor
    Note over SRV: also truncates the in-memory transcript, does not touch snapshots
    SRV-->>WV: ok, detail
```

## 5. Routine run

A routine is a shortcut for re-issuing a sequence of tool calls. The engine runs on
the same `ToolRegistry`, `PermissionGate`, and `UndoManager` instances as the live
loop, so it can never gain permissions the user has not already granted live.

```mermaid
sequenceDiagram
    participant WV as React webview
    participant SRV as Core server
    participant RL as RoutineLibrary
    participant RE as RoutineEngine
    participant PG as PermissionGate
    participant TL as Tool
    participant UM as UndoManager

    WV->>SRV: routine.run, routineId and variables
    SRV->>RL: get(routineId)
    RL-->>SRV: Routine, a declarative plan
    SRV->>RE: run(routine, variables)
    Note over RE: topologically_sorted, then resolve_template per step
    loop each step
        RE->>PG: check(tool_id), then request if not granted
        PG-->>RE: GRANTED or DENIED
        RE->>TL: execute(resolved_args, context)
        TL-->>RE: ToolResult
        RE->>UM: record(snapshot) when the step mutated state
    end
    RE-->>SRV: RoutineRunResult
    SRV->>RL: record_run(routineId)
    SRV-->>WV: ok, status, per-step summaries
```

## 6. Setup Assistant relay signing

When no primary key is configured, a turn runs on the onboarding relay. The relay's
own keys live server-side, outside this repository. The device only signs each request
with an ed25519 keypair whose private half never leaves the OS keychain; the core hands
bytes to sign and gets back a signature.

```mermaid
sequenceDiagram
    participant ORC as Orchestrator
    participant SAP as SetupAssistantProvider
    participant BR as IpcShellBridge
    participant SH as Rust shell keychain
    participant RLY as External relay

    ORC->>SAP: send(messages, tools)
    SAP->>BR: get_device_key()
    BR->>SH: keychain.getDeviceKey
    SH-->>BR: deviceId and publicKey, public half only
    SAP->>BR: sign_relay_request(body)
    BR->>SH: keychain.signRelayRequest, payload
    Note over SH: signs canonical JSON with the device private key, which stays in the keychain
    SH-->>BR: signature and deviceId
    SAP->>RLY: POST body with x-addison-device and x-addison-signature
    RLY-->>SAP: text, or an at_cap wrap-up
    SAP-->>ORC: ModelResponse
```

## 7. Connecting a provider key (multi-provider)

Adding a provider key (owner decision 2026-07-18) is a three-hop dance: the webview
hands the key straight to the highest-trust Rust process (never the core), then asks
the core to validate and record the connection. The core pulls the just-stored key
from the keychain, makes ONE tiny request to prove it works, and folds that provider's
models into the picker union. On failure the provider is left disconnected and the
card offers Remove to clear the stored key. Keys never cross to the core in a frame —
only the provider id does, and the core reads the value from the keychain at the moment
of use.

```mermaid
sequenceDiagram
    participant UI as Webview (API keys card)
    participant SH as Rust shell keychain
    participant SRV as JsonRpcServer
    participant BR as IpcShellBridge
    participant P as Provider API

    UI->>SH: invoke store_provider_key(provider, key)
    Note over SH: key written to provider-key:{provider}, never echoed back
    UI->>SRV: provider.connect(provider, baseUrl?)
    SRV->>BR: get_provider_key(provider)
    BR->>SH: keychain.getProviderKey {provider}
    SH-->>BR: key (core-ward only, one request)
    SRV->>P: one tiny request (GET models / generateContent probe)
    P-->>SRV: 200 ok, or 401/timeout
    Note over SRV: on ok — record connected + added_at in provider_config,<br/>register the provider's models in the union
    SRV-->>UI: {ok: true} or {ok: false, error}
    UI->>SRV: provider.list + model.availableRoles (refresh)
```

## 8. Widget propose and confirm

Addison proposes widgets the same way it proposes routines: a draft is held in the
core and nothing is saved until an explicit confirm. A widget is a **declarative**
spec (`agent_core/widgets.py`) — a saved-routine Run pill or a whitelisted stat
display — never code, validated at save and at render. Saving is display-only
(LOW-risk), so there is no permission card; a routine widget's routine keeps its own
gates when it is actually run.

```mermaid
sequenceDiagram
    participant WV as React webview
    participant SRV as Core server
    participant W as widgets.validate_widget_spec
    participant DB as Store (widgets)

    Note over WV: user sends "Build me a widget that …" (composer seed)
    WV->>SRV: widget.proposeFromConversation
    Note over SRV: draft from recent chat — a routine just run/named,<br/>or a token/latency/connections stat; else a plain refusal
    SRV-->>WV: {title, kind, summary, spec}  (held in memory, nothing saved)
    Note over WV: WidgetProposalCard — "Add widget" / "Not now"
    WV->>SRV: widget.confirmSave {accept: true}
    SRV->>W: validate_widget_spec(draft)
    W-->>SRV: None (valid) — reject otherwise
    SRV->>DB: insert_widget (pinned if under the 6-pin cap)
    SRV-->>WV: {ok: true, widgetId}
    WV->>SRV: widget.list (refresh the rail)
```
