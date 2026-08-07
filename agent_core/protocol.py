"""Shared JSON-RPC message types — engineering-spec §7.

Kept hand-synced with the frontend's ``shell/src/types/protocol.ts`` for v1
(codegen is a Phase 3 improvement, not a v1 requirement). The golden-file drift
test (§9) compares the two.

METHODS (representative subset, §7):
  Frontend -> Core:
    conversation.sendMessage
    permission.respond
    undo.rewindConversation, undo.undoLastAction
    routine.proposeFromConversation, routine.confirmSave
    routine.list, routine.run, routine.delete
    model.setRoleForNextMessage
    model.startLocalSetup
  Core -> Frontend:
    conversation.streamChunk
    permission.requestGrant
    tool.activityUpdate
    model.availableRoles
    model.localSetupProgress
  Core -> Shell (Rust-internal, not exposed to the frontend):
    keychain.getDeviceKey, keychain.getProviderKey
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JsonRpcRequest:
    method: str
    params: dict = field(default_factory=dict)
    id: str | int | None = None
    jsonrpc: str = "2.0"


@dataclass
class JsonRpcResponse:
    id: str | int | None
    result: Any = None
    error: dict | None = None
    jsonrpc: str = "2.0"


# Method name constants — keep in lockstep with protocol.ts.
class Method:
    CONVERSATION_SEND_MESSAGE = "conversation.sendMessage"
    CONVERSATION_NEW = "conversation.new"    # {} -> {conversationId}
    CONVERSATION_LOAD = "conversation.load"  # {conversationId} -> {conversationId, title, messages}
    CONVERSATION_LIST = "conversation.list"  # {} -> {conversations}
    CONVERSATION_RENAME = "conversation.rename"  # {conversationId, title} -> {ok, title?, error?}
    CONVERSATION_STREAM_CHUNK = "conversation.streamChunk"
    PERMISSION_REQUEST_GRANT = "permission.requestGrant"
    PERMISSION_RESPOND = "permission.respond"
    # {toolId, label, detail?} -> notification only. `detail` is the tool's own
    # permission_detail for that call (read_web_page: the site it is about to reach)
    # and is OMITTED for the tools that have none. It is what tells the person WHERE
    # a call is going after the first grant makes later calls of that tool ungated —
    # visibility instead of per-site grant scoping (owner decision 2026-07-20).
    TOOL_ACTIVITY_UPDATE = "tool.activityUpdate"
    UNDO_REWIND_CONVERSATION = "undo.rewindConversation"
    UNDO_UNDO_LAST_ACTION = "undo.undoLastAction"
    UNDO_REDO_LAST_ACTION = "undo.redoLastAction"
    ROUTINE_PROPOSE_FROM_CONVERSATION = "routine.proposeFromConversation"
    ROUTINE_CONFIRM_SAVE = "routine.confirmSave"
    # routine.list rows: {id, name, description, runCount, lastRunAt, createdInMode,
    # variables, planSteps?} — plus `unavailable` {reason, message} on a row the
    # ACTIVE profile can't use (owner decision 2026-08-06: a dev-created routine is
    # listed in Simple, visibly disabled, instead of vanishing). The key is ABSENT
    # on a usable row. `reason` is an open slug vocabulary ("developer_abilities"
    # today), never a boolean, so a later cause needs no shape change; `message` is
    # the plain sentence to show, byte-identical to what routine.run refuses with.
    # DISPLAY ONLY — dispatch, not this field, is what refuses the run.
    ROUTINE_LIST = "routine.list"
    ROUTINE_RUN = "routine.run"
    ROUTINE_DELETE = "routine.delete"
    # profile.get profiles entries are {id,label,description}; the Custom entry ALSO
    # carries "advanced": true (D4) — Simple/Developer entries never grow the key.
    PROFILE_GET = "profile.get"      # {} -> {activeProfile, mode, profiles: [...], flags}
    PROFILE_SET = "profile.set"      # {profileId} -> {ok, mode}; persisted in app_settings (§4.7)

    # Custom-profile prompting guards (scope amendment 2026-07-20, §7; D2/D5). Two
    # settings-backed guards that change ONLY how often Addison asks before acting;
    # they never touch a GLOBAL floor (G1–G4). Lowering one ("weakening") mints the
    # G4 undeletable anchor FIRST, so a working setup always stays reachable. Values
    # are the closed snake-case slugs (policy.py); keys are camelCase (house style).
    GUARDS_GET = "guards.get"        # {} -> {destructiveCard, autoGrantScope, defaults, active}
    GUARDS_SET = "guards.set"        # {destructiveCard?, autoGrantScope?} -> {ok, destructiveCard?, autoGrantScope?, error?}

    # Routing strategies (step 3, scope amendment 2026-07-20; D7). How Addison picks
    # among configured models per turn — a closed vocabulary (quality_first |
    # cost_first | local_only | custom; NO balanced, owner decision 2026-07-24). The
    # Simple profile sees a two-way toggle (surface "toggle"); Developer/Custom see the
    # full picker + reorderable custom chain (surface "full"). Reversible config,
    # snapshot-captured, never a floor.
    ROUTING_GET = "routing.get"      # {} -> {strategy, availableStrategies, customChain, surface}
    ROUTING_SET = "routing.set"      # {strategy?, customChain?} -> {ok, strategy, customChain} | {ok:false, error}
    # Workspace trust (step 5) — the OPEN-mode coding harness's trust boundary.
    # Developer/Custom surfaces only (the frontend hides the card in Simple).
    # grantTrust floor-refuses Addison's own data dir. Trust rows are EXCLUDED from
    # snapshots (standing consent, like tool_grants).
    WORKSPACE_GRANT_TRUST = "workspace.grantTrust"   # {directory} -> {ok, directory} | {ok:false, error}
    WORKSPACE_REVOKE_TRUST = "workspace.revokeTrust" # {directory} -> {ok}
    WORKSPACE_LIST = "workspace.list"                # {} -> {folders: [{directory, grantedAt}]}
    WORKSPACE_PICK_DIRECTORY = "workspace.pickDirectory"  # {} -> {directory: str | null} (relays the shell folder picker)
    # External MCP servers Addison consumes as a CLIENT (step 7 phases 1–4; spec
    # §4.12). A discovered tool is registered namespaced (`mcp:<server>:<tool>`) and
    # dev-only, so it is absent from the SAFE view and refused outside OPEN at both
    # dispatch sites; in OPEN it is offered to the model and invoked through the
    # ordinary gate, reaching it HIGH and destructive — the strongest thing a tool
    # can arrive as, because a server's own claim about its risk is exactly what
    # v1 refuses to trust. How often that produces a card is the gate's answer and
    # the Custom profile can tune it (docs/SAFETY.md owns the guards), so nothing
    # here promises a frequency (phase 3, 2026-08-07).
    # Transport is HTTP ONLY for v1 (owner decision 2026-08-06), so `url` is
    # the whole of a server's address and there is NEVER a command: stdio would mean
    # the Agent Core launching an executable outside the seatbelt. No credential
    # rides these payloads and none is stored — a server that wants a sign-in gets
    # one plain sentence back (2026-08-07). `add` and `refresh` are Developer-only
    # (refused in SAFE); `list` and `remove` answer in every mode, because saved
    # configuration is not a capability and a tightening must not be trapped by a
    # profile switch. See docs/step-7-mcp-plan.md.
    #
    # A row's `status` is one of "never" | "ok" | "failed", and the frontend's own
    # type is that vocabulary exactly. A check IN FLIGHT is deliberately not one of
    # them, on either side: `mcp.list` and `mcp.refresh` run on the same worker
    # thread, so a list request queues behind the refresh and could not observe it.
    # The frontend tracks the row it is waiting on itself, which is a fact about its
    # own request rather than a state in a payload the core owns.
    MCP_LIST = "mcp.list"        # {} -> {servers: [<row>]}, oldest first
    MCP_ADD = "mcp.add"          # {name, url} -> {ok, server} | {ok:false, error}
    MCP_REMOVE = "mcp.remove"    # {id} -> {ok} | {ok:false, error}
    # {id} -> {ok, server: <row>} | {ok:false, error}. `ok:false` means the check did
    # not RUN (wrong profile, a server that is no longer saved, or one whose row is
    # switched off); a check that ran and failed is `ok:true` with status "failed"
    # and one plain sentence in `error`.
    MCP_REFRESH = "mcp.refresh"
    # <row> = {id, name, url, enabled, addedAt, status,
    #          checkedAt?, toolCount?, tools?: [{name, description}], skipped?, error?}
    # Optional fields are OMITTED, never null: a checkedAt on an unchecked row is a
    # number the app made up. `tools` carries names and descriptions only — cleaned
    # and capped at the mcp_client boundary — and never a server's input schema.
    # `tools` and `toolCount` describe what is REGISTERED and therefore callable,
    # not what the server offered: a tool whose namespaced id was already taken is
    # refused at admission, so it is absent from both and counted in `skipped` with
    # everything else Addison would not take. A reader can trust that every name in
    # `tools` is a name dispatch would find.
    # Automation Addison AUTHORS for the OS to run (step 8 phase 1; amendment §9).
    # Addison never triggers itself — G2 — so nothing on this surface starts, arms or
    # schedules anything, and no phase ever will: the OS runs the job, Addison writes
    # the file it runs from (phase 3, through a typed shell surface).
    #
    # PHASE 1 HAS NO ADD, AND THAT IS THE POINT. The table exists, these two methods
    # answer over it, and nothing in the tree can write a row — authoring is phase 2
    # (a `dev_only` registered tool, gated and audited like every other), arming is
    # phase 3 (behind a per-automation typed keyword). So `automation.list` answers
    # `{automations: []}` on every install until then.
    #
    # BOTH answer in EVERY profile, deliberately. A saved row is configuration, not a
    # capability — what an automation's shell command needs is Developer, and that is
    # enforced where the capability is (the authoring/arming tools' `dev_only`
    # registration and their dispatch), never by hiding rows. Hiding somebody's saved
    # configuration on a profile switch is the failure the 2026-08-06 artifact
    # decision reversed, and a REMOVAL must never be the thing a switch traps
    # (docs/SAFETY.md owns the rule; docs/step-8-automation-plan.md §4.1 the choice).
    #
    # Nothing here carries whether an automation is ARMED, in either direction. That
    # truth lives in the OS and is asked for when the surface loads (plan §5.6): a
    # stored flag is what a one-action G3 restore would put back, and a restore can
    # never perform the keyword ceremony arming requires.
    AUTOMATION_LIST = "automation.list"      # {} -> {automations: [<row>]}, oldest first
    AUTOMATION_REMOVE = "automation.remove"  # {id} -> {ok} | {ok:false, error}
    # <row> = {id, name, label, command, scheduleKind, schedule, scheduleSentence,
    #          createdInMode, createdAt}
    # `schedule` is the parsed CLOSED-FIELD object for this row's kind — interval:
    # {minutes}; calendar: {hour, minute, weekday?} — with camelCase keys that are the
    # stored names exactly, because every one of them is a single word. It is a
    # PROJECTION (agent_core/automations.py): only that kind's fields survive, only as
    # numbers, so nothing a hand-edited row put in the column can ride out to a
    # surface. `scheduleSentence` is that same projection in ONE plain sentence
    # ("Every 30 minutes", "Every Monday at 7:30", "No schedule saved yet." for a row
    # the vocabulary does not recognise), rendered CORE-side from the very object the
    # row carries: the words and the numbers beside them are made from one value, and
    # no surface assembles English out of a schedule for itself. `command` is the exact
    # text the OS would run, sent whole because the preview a person reads before
    # arming is the defence the keyword ceremony exists to make them read.
    # `createdInMode` is DISPLAY-ONLY provenance (as on routines and widgets) and
    # decides nothing.
    #
    # NO PAYLOAD HERE EVER CARRIES A BUILT PLIST (plan §5.8). The shell assembles the
    # XML itself from typed fields, so a document crossing this boundary would be
    # `run_command` with extra steps; a sentence about a schedule is a fact, a plist is
    # an instrument. tests/test_automations.py pins that rpc/automations.py cannot even
    # reach the preview builder.
    MODEL_AVAILABLE_ROLES = "model.availableRoles"
    MODEL_SET_ROLE_FOR_NEXT_MESSAGE = "model.setRoleForNextMessage"
    MODEL_START_LOCAL_SETUP = "model.startLocalSetup"
    MODEL_LOCAL_SETUP_PROGRESS = "model.localSetupProgress"
    # Multi-provider API keys (owner decision 2026-07-18). Keys themselves NEVER
    # cross this boundary — the webview stores them straight into the OS keychain via
    # the Rust command; these methods carry only non-secret status/metadata.
    PROVIDER_LIST = "provider.list"            # {} -> {providers: [{id,label,connected,addedAt?,baseUrl?,lastCheckOk?}]}
    PROVIDER_CONNECT = "provider.connect"      # {provider, baseUrl?} -> {ok, error?}
    PROVIDER_DISCONNECT = "provider.disconnect"  # {provider} -> {ok}

    # Add-a-server-by-prompt (step 4, free-model endpoints; contract F2/R2/R6).
    # The turn reply NEVER carries a model-authored actionable payload; instead the
    # CORE inspects the CURRENT turn's user messages, extracts a base URL from a
    # short add-endpoint utterance (never assistant content, never a pasted wall of
    # text), validates it, and HOLDS it for a confirm — the widget/routine
    # precedent. The key is pasted into the card and stored straight to the OS
    # keychain by the shell (G1); it never crosses this boundary.
    ENDPOINT_PROPOSE_FROM_CONVERSATION = "endpoint.proposeFromConversation"  # {} -> {baseUrl, isLocalOrLan, error?} | {none:true}
    ENDPOINT_CONFIRM_ADD = "endpoint.confirmAdd"  # {baseUrl, accept} -> {ok, error?} (runs provider.connect custom)

    # "Make it cheaper" (step 4; contract F3/D4). A canned, core-authored plan —
    # the model authors NONE of its fields — that adds a fixed brevity/prefer-cheaper
    # guidance note and switches routing to cost_first, behind an explicit confirm.
    # apply is idempotent, snapshots FIRST (refuse-on-failure), and persists the
    # skill + setting in ONE atomic Store commit.
    COSTPLAN_PROPOSE = "costPlan.propose"      # {} -> {skillName, skillInstructions, strategy:"cost_first"}
    COSTPLAN_APPLY = "costPlan.apply"          # {accept} -> {ok, snapshotId?, error?}

    # Widgets — DECLARATIVE specs only (agent_core/widgets.py): a saved-routine Run
    # pill, a whitelisted stat display, or one of the three interactive SAFE kinds
    # (checklist / note / timer). NEVER code. Widgets are proposed like routines
    # (draft-held-in-memory + explicit confirm) and saved LOW-risk.
    # {} -> {widgets: [{id, spec, pinned, position, createdInMode, state?,
    # unavailable?}]}; `state` rides only on a stateful kind that has one stored
    # AND still valid for its spec (see WIDGET_SET_STATE below);
    # `unavailable` is the same {reason, message} marker routine.list carries, on the
    # same terms (absent when usable, display only). See ROUTINE_LIST above.
    WIDGET_LIST = "widget.list"
    WIDGET_SET_PINNED = "widget.setPinned"     # {id, pinned} -> {ok, error?}
    WIDGET_DELETE = "widget.delete"            # {id} -> {ok}
    # {id, state} -> {ok, state?} | {ok:false, error}. The mutable half of the
    # three interactive SAFE kinds (checklist / note / timer — step 6, half A):
    # a tick, an edited note, a paused timer. The SPEC never changes; this writes
    # the separate widget_state row. The state is validated PER KIND server-side
    # against the same closed vocabulary — the frontend's shape is never trusted —
    # and a valid write echoes the stored state back so an optimistic UI can
    # reconcile. NO permission card: these kinds invoke no tool and have no
    # execution surface at all, which is precisely SAFE invariant 4.
    WIDGET_SET_STATE = "widget.setState"
    WIDGET_PROPOSE_FROM_CONVERSATION = "widget.proposeFromConversation"  # {} -> {title, kind, summary, spec}
    WIDGET_CONFIRM_SAVE = "widget.confirmSave"  # {accept} -> {ok, widgetId?}
    WIDGET_RUN = "widget.run"                   # {id} -> {ok, output?, error?}
    # Core-computed, read-only stat sources for the token meter / connections cards.
    STATS_GET = "stats.get"                    # {} -> {tokensMonth, providerLatency, connections}

    # Guidance skills — DECLARATIVE plain-text notes (agent_core/skills.py) that steer
    # HOW Addison approaches tasks; enabled skills append to the transient per-turn
    # system prompt. NEVER executable and NEVER widen permissions (the gate stays sole
    # authority). Available in both SAFE and OPEN modes. Local content only (no sharing).
    SKILL_LIST = "skill.list"                  # {} -> {skills: [{id, name, instructions, enabled}]}
    SKILL_CREATE = "skill.create"              # {name, instructions} -> {ok, id} | {ok:false, error}
    SKILL_UPDATE = "skill.update"              # {id, name, instructions} -> {ok, error?}
    SKILL_SET_ENABLED = "skill.setEnabled"     # {id, enabled} -> {ok}
    SKILL_DELETE = "skill.delete"              # {id} -> {ok}

    # Snapshots — GLOBAL FLOOR G3 (guaranteed rollback; amendment §3, spec §4.9).
    # An app-state snapshot is a point-in-time copy of Addison's mutable CONFIG
    # (settings, providers, skills, widgets, routines) — NEVER keys (they
    # stay in the OS keychain, G1) and NEVER the transcript. Taken automatically
    # before any risky change and on command. Restore always targets the last
    # VERIFIED-WORKING config. These are RPC methods, never registry tools: the
    # permission gate must never be able to deny a restore. Snapshots are visible
    # and restorable in EVERY mode — artifact hiding does not apply to them.
    SNAPSHOT_LIST = "snapshot.list"                    # {} -> {snapshots: [...], warning?}
    SNAPSHOT_CREATE = "snapshot.create"                # {} -> {ok, snapshotId} | {ok:false, error}
    # {id} -> {ok, detail?, error?, binaryMismatch?}
    SNAPSHOT_RESTORE = "snapshot.restore"
    # {} -> {ok, snapshotId?, detail?, error?}
    SNAPSHOT_RESTORE_LAST_WORKING = "snapshot.restoreLastWorking"
    SNAPSHOT_DELETE = "snapshot.delete"                # {id} -> {ok, error?}

    # Core -> Shell (handled in Rust, NEVER exposed to or callable from the
    # webview — §1.3, §5). Listed here and mirrored in protocol.ts only so the
    # golden-file drift test (§9) covers the full method surface. These carry
    # the ShellBridge contract (tools/base.py) across the process boundary.
    SHELL_SAVE_NEW_FILE = "shell.saveNewFile"          # {filename, content} -> {path}
    SHELL_DELETE_FILE = "shell.deleteFile"             # {path} -> {}
    SHELL_RESTORE_FILE = "shell.restoreFile"           # {path, content} -> {} (redo of delete)
    SHELL_OPEN_DRAFT = "shell.openDraft"               # {to, subject, body} -> {draftRef}
    SHELL_DISCARD_DRAFT = "shell.discardDraft"         # {draftRef} -> {}
    SHELL_READ_CLIPBOARD = "shell.readClipboard"       # {} -> {text}
    SHELL_OPEN_EXTERNAL = "shell.openExternal"         # {url} -> {}
    SHELL_PICK_FILE = "shell.pickFile"                 # {} -> {fileHandle} (opaque, not a path)
    SHELL_READ_SCOPED_FILE = "shell.readScopedFile"    # {fileHandle} -> {content, kind}
    # Workspace-trust file surface (step 5, OPEN harness). Path-based (NOT picker-
    # scoped like the four above) — the core confines which paths reach here (D3),
    # and the shell independently refuses Addison's own data dir + ledgers what it
    # wrote so restore can only touch a path this session created/overwrote.
    SHELL_WRITE_WORKSPACE_FILE = "shell.writeWorkspaceFile"     # {path, content} -> {existed, prior}
    SHELL_READ_WORKSPACE_FILE = "shell.readWorkspaceFile"       # {path} -> {content}
    SHELL_RESTORE_WORKSPACE_FILE = "shell.restoreWorkspaceFile" # {path, content?|delete} -> {}
    SHELL_PICK_DIRECTORY = "shell.pickDirectory"                # {} -> {path} (native folder picker)
    # OPEN-mode command execution (step 5.5, item 1). The core does NOT run this
    # itself: run_command crosses the bridge like every other OS effect (§1.3), so
    # execution happens in the process that can apply a sandbox. `writeRoots` is the
    # live workspace-trust list, which the shell turns into the seatbelt profile's
    # write allowlist; the shell independently re-denies Addison's own data dir on
    # top of whatever roots it is sent. `sandboxed` reports whether a profile was
    # actually applied — NEVER silently false (design-doc §9).
    # {command, timeoutMs, writeRoots} -> {stdout, stderr, exitCode, sandboxed}
    SHELL_RUN_COMMAND = "shell.runCommand"
    # {} -> {deviceId, publicKey}; the public half ONLY
    KEYCHAIN_GET_DEVICE_KEY = "keychain.getDeviceKey"
    # {provider} -> {key}; read per-call at the moment of use, never cached (G1)
    KEYCHAIN_GET_PROVIDER_KEY = "keychain.getProviderKey"
    # {payload} -> {signature, deviceId}. The shell signs relay requests with the
    # device private key, which never leaves the OS keychain (§5) — the core sends
    # bytes to sign, never sees key material.
    KEYCHAIN_SIGN_RELAY_REQUEST = "keychain.signRelayRequest"
    # {} -> {version, identifier}. The build the app is running, recorded on a G4
    # anchor so a restore can SAY when the build has moved on. A reference string,
    # never bytes and never a path — nothing in the codebase replaces a binary.
    SHELL_APP_BUILD_REF = "shell.appBuildRef"
