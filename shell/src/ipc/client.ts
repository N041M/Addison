// Typed wrapper around Tauri IPC — engineering-spec §7.
//
// The webview NEVER talks to the Agent Core or the network directly (§1.3); it
// goes through the Rust shell's `send_to_core` command, which relays JSON-RPC
// frames to/from the Python core over stdio. ALL traffic back — both responses
// to our requests and Core→Frontend notifications — arrives as `core-message`
// events; plain-language shell notices arrive as `core-status` events.
//
// The frontend must NEVER construct `shell.*` / `keychain.*` frames: those are
// Rust-internal (see the comment in types/protocol.ts). This module only
// exposes the Frontend→Core method surface.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  Method,
  type ActivityUpdate,
  type Automation,
  type AutomationStatus,
  type ModelRole,
  type PermissionRequest,
  type WorkspaceEdit,
  type WorkspaceEditDiff,
  type WorkspaceEditList,
  type WorkspaceEntry,
  type WorkspaceFileView,
  type WorkspaceListing,
  type WorkspaceRevertResult,
} from "../types/protocol";
import { asRecord, normalizeUnavailable } from "../lib/parse";
import {
  parseConversationSummaries,
  type ConversationSummary,
  type Skill,
  type Snapshot,
  type SnapshotList,
  type Widget,
  type WidgetProposal,
  type WidgetSpec,
  type WidgetState,
  type WidgetStatSource,
  type Stats,
  type ConnectionStat,
  type ProviderLatencyStat,
  type GuardsState,
  type DestructiveCardGuard,
  type AutoGrantScopeGuard,
  type RoutingState,
  type RoutingStrategy,
  type RoutingSurface,
  type AnsweredWith,
  type EndpointProposal,
  type CostPlan,
  type WorkspaceRoot,
  type McpServer,
  type McpServerStatus,
  type McpDiscoveredTool,
  type Channel,
  type ChannelKind,
  type ChannelTokenPresence,
} from "../types/ui";

const DEFAULT_TIMEOUT_MS = 120_000;

// A turn's budget, not a request's. A turn legitimately outlives the default:
// model rounds plus tool calls, and — the hard floor — the core waits up to 600s
// (its _KEYCHAIN_TIMEOUT) for a person to answer an OS keychain dialog. If this
// were shorter, the reply the person waited for would arrive after the pending
// entry was deleted and be dropped on the floor, while the composer re-enabled
// and invited a duplicate turn. Must stay comfortably above the core's 600s.
const TURN_TIMEOUT_MS = 900_000;

// ---------------------------------------------------------------------------
// Tauri context detection — the app must degrade gracefully when opened in a
// plain browser (e.g. `npm run dev` for design review), where the Tauri APIs
// don't exist. In that case every call rejects with a plain message and the UI
// shows a quiet "engine isn't connected" banner instead of crashing.
// ---------------------------------------------------------------------------
export function isEngineConnected(): boolean {
  return (
    typeof window !== "undefined" &&
    ("__TAURI_INTERNALS__" in window || "__TAURI__" in window)
  );
}

const NOT_CONNECTED_MESSAGE =
  "Addison's engine isn't connected right now.";

// ---------------------------------------------------------------------------
// Notification param shapes (Core → Frontend). These aren't in protocol.ts
// (which pins method names + a few shared interfaces); the JSON-RPC params are
// free-form, so we parse them defensively.
// ---------------------------------------------------------------------------
export interface StreamChunkParams {
  text?: string;
  delta?: string;
  content?: string;
  messageId?: string;
  done?: boolean;
}

export interface LocalSetupProgressParams {
  stage?: string;
  label?: string;
  message?: string;
  percent?: number;
  done?: boolean;
  error?: string;
}

// A frame arriving on the `core-message` channel is either a response (has an
// `id`) or a notification (has a `method`, no `id`).
interface CoreFrame {
  jsonrpc?: string;
  id?: string | number | null;
  result?: unknown;
  // The plain `message` is identical in both profiles. Under the Developer
  // profile the core additionally attaches `data.raw` (the real exception text)
  // — never surfaced to Simple users, but carried through to callers here.
  error?: { code: number; message: string; data?: Record<string, unknown> };
  method?: string;
  params?: Record<string, unknown>;
}

// An Error surfaced from a Core response may carry the developer-only raw detail
// alongside its plain, always-shown `message`. Callers can read `err.raw`.
export interface RawError extends Error {
  raw?: string;
}

// One captured raw diagnostic — the developer-only raw text, the plain message
// that was (or would be) shown, and when it happened. The App keeps a small
// ring of the most recent ones for the Settings > Diagnostics panel.
export interface DiagnosticEntry {
  message: string;
  raw: string;
  at: number; // epoch ms
}

// One provider row from `provider.list` (multi-provider, owner decision
// 2026-07-18). NON-secret status/metadata ONLY — the key itself never crosses
// this boundary (it lives in the OS keychain). `addedAt` is epoch SECONDS;
// `baseUrl` is present for the custom "your own server" provider only.
export interface ProviderInfo {
  id: string;
  label: string;
  connected: boolean;
  addedAt?: number;
  baseUrl?: string;
  lastCheckOk?: boolean;
}

// ---------------------------------------------------------------------------
// Internal state: pending requests keyed by id, notification subscribers keyed
// by method, and status subscribers. Listeners are wired exactly once.
// ---------------------------------------------------------------------------
interface Pending {
  resolve: (result: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

const pending = new Map<string, Pending>();
const notificationHandlers = new Map<string, Set<(params: Record<string, unknown>) => void>>();
const statusHandlers = new Set<(text: string) => void>();
// Structured engine-lifecycle state ("ready" | "restarting" | "stopped" |
// "error") from the same core-status event — a "ready" after a restart means
// the NEW engine process needs its catalog/profile re-fetched.
const stateHandlers = new Set<(state: string) => void>();
const diagnosticsHandlers = new Set<(entry: DiagnosticEntry) => void>();

// --- diagnostics raised while nobody is listening --------------------------
//
// The CSP violation reporter is installed in `main.tsx` BEFORE the app renders,
// precisely so a violation raised while the first chunk loads is captured — a
// blocked worker or a blocked stylesheet is silent in the DOM and loud only in a
// devtools console nobody has open in a packaged build. Its only subscriber is an
// App effect gated on the engine being connected, which lands much later. Fanning
// out to live handlers alone
// therefore DISCARDED exactly the violations the early install exists to catch, and
// captured nothing at all on a machine where the engine never connects.
//
// So an entry raised with nobody listening waits here and is replayed to the next
// subscriber. The bound is 20: a page load raises one violation per blocked asset,
// twenty is far more than a working build produces and more than the Settings panel
// itself keeps (5), and a window that never connects cannot grow this without end.
// Full means the OLDEST goes, which is the panel's own preference — it shows the
// most recent — and a buffer that filled with the first twenty and ignored the rest
// would replay a story that stopped being current.
const EARLY_DIAGNOSTICS_LIMIT = 20;
const earlyDiagnostics: DiagnosticEntry[] = [];

let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `req-${Date.now()}-${idCounter}`;
}

let listenersReady: Promise<void> | null = null;

function ensureListeners(): Promise<void> {
  if (!isEngineConnected()) return Promise.resolve();
  if (listenersReady) return listenersReady;
  listenersReady = (async () => {
    await listen<CoreFrame>("core-message", (event) => handleCoreMessage(event.payload));
    await listen<unknown>("core-status", (event) => handleCoreStatus(event.payload));
  })();
  return listenersReady;
}

function handleCoreMessage(frame: CoreFrame): void {
  if (!frame || typeof frame !== "object") return;

  // Notification: has a method, no matching pending id.
  if (typeof frame.method === "string") {
    const handlers = notificationHandlers.get(frame.method);
    if (handlers) {
      const params = (frame.params ?? {}) as Record<string, unknown>;
      handlers.forEach((h) => h(params));
    }
    return;
  }

  // Response: resolve/reject the matching pending request.
  if (frame.id === undefined || frame.id === null) return;
  const key = String(frame.id);
  const entry = pending.get(key);
  if (!entry) return;
  pending.delete(key);
  clearTimeout(entry.timer);
  if (frame.error) {
    const message = frame.error.message || "Something went wrong.";
    const err: RawError = new Error(message);
    // Developer profile only: the core adds the real exception text under
    // `error.data.raw`. The plain message above is unchanged for both profiles.
    const rawValue = frame.error.data?.raw;
    if (typeof rawValue === "string" && rawValue) {
      err.raw = rawValue;
      // Named `diag`, not `entry`: the outer `entry` is the pending request we
      // reject just below — shadowing it here would be a footgun. Pushed through
      // the same function as the CSP reporter's, so both sources get the same
      // treatment when nothing is listening yet; a second fan-out site here is how
      // one of them would quietly stop getting it.
      const diag: DiagnosticEntry = { message, raw: rawValue, at: Date.now() };
      pushDiagnostic(diag);
    }
    entry.reject(err);
  } else {
    entry.resolve(frame.result);
  }
}

function handleCoreStatus(payload: unknown): void {
  const text = normalizeStatusText(payload);
  if (text) statusHandlers.forEach((h) => h(text));
  if (payload && typeof payload === "object") {
    const state = (payload as Record<string, unknown>).state;
    if (typeof state === "string" && state) {
      stateHandlers.forEach((h) => h(state));
    }
  }
}

function normalizeStatusText(payload: unknown): string {
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    const value = obj.message ?? obj.text ?? obj.status;
    if (typeof value === "string") return value;
  }
  return "";
}

// ---------------------------------------------------------------------------
// Core request/subscribe primitives.
// ---------------------------------------------------------------------------
async function call<T = unknown>(
  method: string,
  params: Record<string, unknown> = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  await ensureListeners();

  const id = nextId();
  const frame = { jsonrpc: "2.0", method, params, id };

  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("Addison took too long to answer. Please try again."));
    }, timeoutMs);

    pending.set(id, {
      resolve: (result) => resolve(result as T),
      reject,
      timer,
    });

    invoke("send_to_core", { frame }).catch((err: unknown) => {
      pending.delete(id);
      clearTimeout(timer);
      reject(new Error(toPlainMessage(err)));
    });
  });
}

/**
 * Route Core → Frontend notification frames (e.g. `conversation.streamChunk`,
 * `permission.requestGrant`, `tool.activityUpdate`, `model.localSetupProgress`)
 * to a handler. Returns an unsubscribe function.
 */
export function subscribe(
  method: string,
  handler: (params: Record<string, unknown>) => void,
): () => void {
  void ensureListeners();
  let set = notificationHandlers.get(method);
  if (!set) {
    set = new Set();
    notificationHandlers.set(method, set);
  }
  set.add(handler);
  return () => {
    set?.delete(handler);
  };
}

/** Subscribe to plain-language shell notices delivered on `core-status`. */
export function subscribeStatus(handler: (text: string) => void): () => void {
  void ensureListeners();
  statusHandlers.add(handler);
  return () => {
    statusHandlers.delete(handler);
  };
}

/**
 * Subscribe to the engine-lifecycle state carried on the same `core-status`
 * event ("ready" | "restarting" | "stopped" | "error"). Every "ready" is a
 * FRESH engine process — subscribers should re-fetch anything cached from the
 * previous one (model catalog, profile), or stale ids produce errors like
 * "That model option isn't available."
 */
export function subscribeCoreState(handler: (state: string) => void): () => void {
  void ensureListeners();
  stateHandlers.add(handler);
  return () => {
    stateHandlers.delete(handler);
  };
}

/**
 * Subscribe to developer-only raw diagnostics: each raw error the core attaches
 * to a failed response (`error.data.raw`) is reported here as it happens. Fires
 * only when the active profile actually surfaces raw text, so a Simple session
 * never sees an entry. Returns an unsubscribe function.
 */
export function subscribeDiagnostics(handler: (entry: DiagnosticEntry) => void): () => void {
  diagnosticsHandlers.add(handler);
  // Anything raised while nobody was listening is delivered now, oldest first, and
  // then forgotten — see the buffer's own comment. Handed to THIS subscriber rather
  // than fanned out: a later one is not owed a replay of what happened before the
  // first reader took it.
  if (earlyDiagnostics.length > 0) {
    const waiting = earlyDiagnostics.splice(0, earlyDiagnostics.length);
    waiting.forEach((entry) => handler(entry));
  }
  return () => {
    diagnosticsHandlers.delete(handler);
  };
}

/**
 * Report a diagnostic that did NOT arrive on a JSON-RPC response.
 *
 * The ring's first source was `error.data.raw` off a failed core reply, which is
 * why it lives in this module at all. The second is the webview reporting its own
 * Content-Security-Policy violations (`lib/cspReport.ts`) — a fact about this
 * process, with no core round trip anywhere in it. Both belong in the same ring
 * because they answer the same question for the same reader: the Developer
 * profile's "what actually went wrong just now".
 *
 * Deliberately not a second channel with a second subscriber list: two rings would
 * mean two places to look, and the one that nobody wired up would be the one
 * holding the answer.
 *
 * WITH NOBODY LISTENING THE ENTRY IS KEPT, not dropped: the reporter is installed
 * before the app renders and its only subscriber arrives with the engine, so
 * discarding here threw away precisely the load-time violations that install exists
 * to catch. See `earlyDiagnostics` above for the bound and why it is that number.
 */
export function pushDiagnostic(entry: DiagnosticEntry): void {
  if (diagnosticsHandlers.size === 0) {
    earlyDiagnostics.push(entry);
    if (earlyDiagnostics.length > EARLY_DIAGNOSTICS_LIMIT) earlyDiagnostics.shift();
    return;
  }
  diagnosticsHandlers.forEach((h) => h(entry));
}

function toPlainMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return "Something went wrong talking to Addison.";
}

// ---------------------------------------------------------------------------
// Typed Frontend → Core method surface. Kept in lockstep with protocol.ts's
// Method names; params are the free-form JSON-RPC payloads each method expects.
// ---------------------------------------------------------------------------
export const ipc = {
  sendMessage: (text: string, role?: ModelRole, modelId?: string, effort?: string) =>
    // TURN_TIMEOUT_MS, not the default: a turn may sit behind an OS keychain
    // dialog for up to the core's 600s before it even starts.
    call(Method.ConversationSendMessage, { text, role, modelId, effort }, TURN_TIMEOUT_MS),

  // `typed` is the ARMING card's code box (step 8 phase 3) and rides only that
  // card's answer — every other card sends the exact payload it always did. It goes
  // out VERBATIM: the core normalises (case, separators) and compares with
  // `hmac.compare_digest`, and a second normaliser on this side would be a place
  // where the two could one day disagree about a security decision. Nothing here
  // ever compares it, and nothing here ever stores it.
  // What is the engine waiting for RIGHT NOW? Asked by App's re-sync watchdog when
  // a turn has been working with no card on screen (see PENDING_RESYNC_MS there).
  // A read: it answers no card and costs an arming card no attempt.
  pendingPermission: () => call(Method.PermissionPending),

  respondToPermission: (toolId: string, allow: boolean, typed?: string) =>
    call(
      Method.PermissionRespond,
      typed === undefined ? { toolId, allow } : { toolId, allow, typed },
    ),

  // Stop, sent as the person presses it. Fire-and-forget by design: the webview
  // has already let go of the turn, and a failed frame must not leave a card
  // looking answerable — the caller greys the card out either way and the CORE is
  // what refuses a late answer. Nothing here waits for a turn to end, because the
  // core does not end one: it ends the turn's consent.
  stopTurn: () => call(Method.ConversationStop),

  undoLastAction: () => call(Method.UndoUndoLastAction),
  redoLastAction: () => call(Method.UndoRedoLastAction),
  rewindConversation: (toMessageId: string) =>
    call(Method.UndoRewindConversation, { toMessageId }),

  listRoutines: () => call(Method.RoutineList),
  runRoutine: (routineId: string, variables: Record<string, string>) =>
    call(Method.RoutineRun, { routineId, variables }),
  proposeRoutine: () => call(Method.RoutineProposeFromConversation),
  confirmSaveRoutine: (name?: string, description?: string) =>
    call(Method.RoutineConfirmSave, { name, description }),
  deleteRoutine: (routineId: string) => call(Method.RoutineDelete, { routineId }),

  // Sharing a routine out and in. All three answer {ok:false, error} rather than
  // rejecting, because every refusal here is a plain sentence the person is meant
  // to read (a step that runs a command, a default pointing at a folder on this
  // machine, a file that is not a routine) and a thrown Error would lose it.
  //
  // Both import calls put up an OS dialog inside the core's own turn (a picker,
  // then nothing), so they take the long timeout for the same reason `sendMessage`
  // does: a person deciding in a file dialog is not a slow engine.
  exportRoutine: (routineId: string) =>
    call(Method.RoutineExport, { routineId }, TURN_TIMEOUT_MS),
  // Reads the file the person picks and DESCRIBES it. Saves nothing.
  previewRoutineImport: () => call(Method.RoutineImportPreview, {}, TURN_TIMEOUT_MS),
  // Adds what the preview described. Deliberately parameterless: the core holds
  // the file's own bytes between the two calls, so nothing this process could
  // edit reaches the database.
  confirmRoutineImport: () => call(Method.RoutineImportConfirm),

  // Profiles (§4.7). `getProfile` returns the active profile, the pickable
  // profiles (label/description authored by the core), and the frontend feature
  // flags. `setProfile` switches immediately (no restart); callers re-fetch
  // `getProfile` afterwards to pick up the new flags.
  getProfile: () => call(Method.ProfileGet),
  setProfile: (profileId: string) => call(Method.ProfileSet, { profileId }),

  availableRoles: () => call(Method.ModelAvailableRoles),
  setRoleForNextMessage: (role: ModelRole, modelId?: string, effort?: string) =>
    call(Method.ModelSetRoleForNextMessage, { role, modelId, effort }),

  // Multi-provider API keys (owner decision 2026-07-18). These carry only
  // non-secret status/metadata; the key itself was already stored in the OS
  // keychain by `storeProviderKey` before `connectProvider` is called.
  listProviders: (): Promise<ProviderInfo[]> =>
    call(Method.ProviderList).then(parseProviderList),
  // Validates the just-stored key with one tiny request through the core, then
  // records the connection. Resolves to {ok, error?} — a failed connect is a
  // resolved {ok:false}, not a reject, so the card can show the plain error line.
  connectProvider: (provider: string, baseUrl?: string): Promise<ProviderConnectResult> =>
    call(Method.ProviderConnect, { provider, baseUrl }).then(parseConnectResult),
  disconnectProvider: (provider: string) =>
    call(Method.ProviderDisconnect, { provider }),
  // Kicks off the one-time local-model download/verify for `modelName` (the
  // curated Ollama tag). Resolves when the model is set up and has appeared in
  // `availableRoles`; rejects with a plain-language error (e.g. Ollama not
  // running, machine too small). Live progress arrives on
  // `model.localSetupProgress` in between.
  startLocalSetup: (modelName?: string) =>
    call(Method.ModelStartLocalSetup, { modelName }),

  // Conversation history (backend already merged on the parent branch).
  // `list` returns summaries newest-first; `new` mints a fresh conversation and
  // returns its id; `load` returns the stored rows (user + non-empty assistant,
  // in order) for one conversation, or a plain-language error for a bad id.
  listConversations: (): Promise<ConversationSummary[]> =>
    call(Method.ConversationList).then(parseConversationSummaries),

  newConversation: (): Promise<string> =>
    call(Method.ConversationNew).then(parseConversationId),

  loadConversation: (conversationId: string): Promise<LoadedConversation> =>
    call(Method.ConversationLoad, { conversationId }).then(parseLoadedConversation),

  // Rename a chat (double-click its title in the sidebar). Returns the canonical
  // stored title (trimmed/capped by the core) so the frontend adopts exactly it.
  renameConversation: (conversationId: string, title: string): Promise<ConversationRenameResult> =>
    call(Method.ConversationRename, { conversationId, title }).then(parseConversationRename),

  // Widgets — DECLARATIVE specs only (see agent_core/widgets.py). `list` returns
  // stored widgets (invalid specs already hidden by the core); `setPinned`/`delete`
  // persist edit-mode changes. Proposing mirrors routines: a draft is held in the
  // core and only saved on `confirmWidget({accept:true})`. Saving is display-only
  // (LOW-risk) — the routine a routine-widget runs keeps its own gates at run time.
  listWidgets: (): Promise<Widget[]> => call(Method.WidgetList).then(parseWidgetList),
  setWidgetPinned: (id: string, pinned: boolean): Promise<WidgetMutationResult> =>
    call(Method.WidgetSetPinned, { id, pinned }).then(parseWidgetMutation),
  deleteWidget: (id: string): Promise<WidgetMutationResult> =>
    call(Method.WidgetDelete, { id }).then(parseWidgetMutation),
  // A tick, an edited note, a paused timer (the three interactive kinds). The
  // core validates the state against the widget's own spec before storing it and
  // answers with what it stored, so an optimistic update has something real to
  // reconcile against. No permission card: these kinds run nothing.
  setWidgetState: (id: string, state: WidgetState): Promise<WidgetStateResult> =>
    call(Method.WidgetSetState, { id, state }).then(parseWidgetStateResult),
  proposeWidget: (): Promise<WidgetProposal> =>
    call(Method.WidgetProposeFromConversation).then(parseWidgetProposal),
  confirmWidget: (accept: boolean): Promise<WidgetMutationResult> =>
    call(Method.WidgetConfirmSave, { accept }).then(parseWidgetMutation),
  // Command widgets only (Developer profile). The core re-checks the mode and
  // routes through the same gate as a routine command step — a destructive
  // command raises its per-invocation card before anything runs.
  runWidget: (id: string): Promise<WidgetRunResult> =>
    call(Method.WidgetRun, { id }).then(parseWidgetRun),

  // Core-computed, read-only stats for the token meter + connections cards. No
  // key material is ever in this payload (§8.3).
  getStats: (): Promise<Stats> => call(Method.StatsGet).then(parseStats),

  // Skills — user-authored, plain-text guidance notes (pure text, no execution).
  // `list` returns every saved skill with its on/off state; the mutators persist
  // a create/edit/toggle/remove and resolve to {ok, id?, error?} so a create can
  // surface the new id and any failure shows a plain line instead of throwing.
  listSkills: (): Promise<Skill[]> => call(Method.SkillList).then(parseSkillList),
  createSkill: (name: string, instructions: string): Promise<SkillMutationResult> =>
    call(Method.SkillCreate, { name, instructions }).then(parseSkillMutation),
  updateSkill: (id: string, name: string, instructions: string): Promise<SkillMutationResult> =>
    call(Method.SkillUpdate, { id, name, instructions }).then(parseSkillMutation),
  setSkillEnabled: (id: string, enabled: boolean): Promise<SkillMutationResult> =>
    call(Method.SkillSetEnabled, { id, enabled }).then(parseSkillMutation),
  deleteSkill: (id: string): Promise<SkillMutationResult> =>
    call(Method.SkillDelete, { id }).then(parseSkillMutation),

  // Restore points — the G3 guaranteed-rollback floor. These are plain RPC
  // methods, never registry tools: a floor the permission gate could deny is not
  // a floor. `restoreLastWorking` takes no argument on purpose — the one-action
  // way back must not require the user to know which point to pick.
  listSnapshots: (): Promise<SnapshotList> =>
    call(Method.SnapshotList).then(parseSnapshotList),
  createSnapshot: (): Promise<SnapshotMutationResult> =>
    call(Method.SnapshotCreate).then(parseSnapshotMutation),
  // NO CALLER IN STEP 1, on purpose — the same staging the core gives
  // `mint_anchor()` (contract §1.1 item 4, §1.2). Step 1's Settings card ships a
  // list, "Save a snapshot now", the one-action "Restore to the last working
  // state" and a per-row Remove; it deliberately ships NO per-row Restore
  // (contract §1.1 item 11), so nothing here calls this yet. It stays because
  // step 2's Custom-profile anchor path restores one specific point by id, and
  // because `snapshot.restore` is a frozen method string (§11.3 item 7) that
  // step 2 must not have to re-derive. snapshots.test.ts covers it so it cannot
  // rot in the meantime. If you add a per-row Restore, it follows §11.2: the
  // accent two-step INLINE confirm, never window.confirm(), never the danger
  // token — going back to a setup that worked is a recovery, not a destructive
  // act.
  restoreSnapshot: (id: string): Promise<SnapshotRestoreResult> =>
    call(Method.SnapshotRestore, { id }).then(parseSnapshotRestore),
  restoreLastWorking: (): Promise<SnapshotRestoreResult> =>
    call(Method.SnapshotRestoreLastWorking).then(parseSnapshotRestore),
  deleteSnapshot: (id: string): Promise<SnapshotMutationResult> =>
    call(Method.SnapshotDelete, { id }).then(parseSnapshotMutation),

  // Guards — the two tunable prompting guards of the Custom profile (Phase-2
  // step 2). `getGuards` returns the current values + fixed defaults + whether
  // they're effective right now; `setGuards` sends only the guard(s) that
  // changed. A weakening save mints the G4 undeletable anchor CORE-side before
  // anything persists; a refusal (bad value, or the anchor couldn't be saved)
  // is a resolved {ok:false} carrying a plain, already-user-ready sentence.
  getGuards: (): Promise<GuardsState> => call(Method.GuardsGet).then(parseGuards),
  setGuards: (patch: {
    destructiveCard?: DestructiveCardGuard;
    autoGrantScope?: AutoGrantScopeGuard;
  }): Promise<GuardsSetResult> => call(Method.GuardsSet, patch).then(parseGuardsSet),

  // Routing — how Addison picks which model answers (Phase-2 step 3). `getRouting`
  // returns the current strategy + the surface (Simple toggle vs. full picker) +
  // the Developer custom order. `setRouting` sends only what changed — a strategy,
  // a custom chain, or both. A refusal (a bad value, or the custom-chain overwrite
  // whose snapshot couldn't be saved) is a resolved {ok:false} carrying a plain,
  // already-user-ready sentence, never a reject.
  getRouting: (): Promise<RoutingState> => call(Method.RoutingGet).then(parseRouting),
  setRouting: (patch: {
    strategy?: RoutingStrategy;
    customChain?: string[];
  }): Promise<RoutingSetResult> => call(Method.RoutingSet, patch).then(parseRoutingSet),

  // Add-a-model-server by prompt (Phase-2 step 4). `proposeEndpoint` asks the core
  // whether the CURRENT turn's user text named an add-endpoint address; it resolves
  // to a drafted proposal or `null` (nothing to add — Addison answers in prose).
  // `confirmAddEndpoint` applies (or declines) the held draft by running the
  // existing `provider.connect {provider:"custom", baseUrl}` path CORE-side. The
  // key is NEVER a parameter here — it went to the keychain via `storeProviderKey`
  // before this call (G1); this frame carries only the base URL + the decision.
  proposeEndpoint: (): Promise<EndpointProposal | null> =>
    call(Method.EndpointProposeFromConversation).then(parseEndpointProposal),
  confirmAddEndpoint: (baseUrl: string, accept: boolean): Promise<EndpointConfirmResult> =>
    call(Method.EndpointConfirmAdd, { baseUrl, accept }).then(parseEndpointConfirm),

  // "Make it cheaper" (Phase-2 step 4). `proposeCostPlan` asks the core to draft the
  // canned prefer-cheaper plan (a fixed guidance note + the cost_first strategy);
  // it resolves to the plan or `null`. `applyCostPlan` applies (or declines) it —
  // the core validates, snapshots FIRST (refusing the whole change if the restore
  // point can't be saved), then persists the note + strategy atomically.
  proposeCostPlan: (): Promise<CostPlan | null> =>
    call(Method.CostPlanPropose).then(parseCostPlan),
  applyCostPlan: (accept: boolean): Promise<CostPlanApplyResult> =>
    call(Method.CostPlanApply, { accept }).then(parseCostPlanApply),
  // Workspace trust — the coding-harness trust boundary (Phase-2 step 5). These
  // carry only the folder path + when it was trusted; no key material, no file
  // contents ever cross this boundary. `grantTrust` resolves to {ok, error?} — a
  // refusal (the folder is Addison's own data dir, or doesn't exist) is a resolved
  // {ok:false} carrying the core's plain, already-user-ready sentence, never a
  // reject, so the card can show one calm line. `revokeTrust` likewise resolves to
  // {ok}. `listWorkspaceRoots` returns the currently-trusted roots. `pickDirectory`
  // opens the OS folder picker through the Rust shell and resolves to
  // `{directory, error}`: the chosen absolute path, or `null` when the person
  // cancelled (or the picker is unavailable) — the caller simply does nothing then
  // — with `error` set only when the core stopped waiting on a picker still open.
  grantWorkspaceTrust: (directory: string): Promise<WorkspaceMutationResult> =>
    call(Method.WorkspaceGrantTrust, { directory }).then(parseWorkspaceMutation),
  revokeWorkspaceTrust: (directory: string): Promise<WorkspaceMutationResult> =>
    call(Method.WorkspaceRevokeTrust, { directory }).then(parseWorkspaceMutation),
  listWorkspaceRoots: (): Promise<WorkspaceRoot[]> =>
    call(Method.WorkspaceList).then(parseWorkspaceRoots),
  pickWorkspaceDirectory: (): Promise<WorkspaceDirectoryPick> =>
    call(Method.WorkspacePickDirectory).then(parseWorkspaceDirectory),

  // The review surface (Phase 3). Browsing is a USER-DRIVEN read, which is why it
  // is RPC and never a registry tool: routing it through the registry would hand
  // the model a `list_directory` capability as a side effect AND put a permission
  // card in front of a click the person just made. Precedent: snapshot restore.
  //
  // Every one of these is refused core-side outside the Developer/Custom profile,
  // and the read paths are confined to a currently-trusted folder; nothing here is
  // the boundary, it is the way to ASK across it.
  listWorkspaceDirectory: (directory: string): Promise<BrowseResult<WorkspaceListing>> =>
    call(Method.WorkspaceListDirectory, { directory }).then(parseWorkspaceListing),
  readWorkspaceFile: (path: string): Promise<BrowseResult<WorkspaceFileView>> =>
    call(Method.WorkspaceReadFile, { path }).then(parseWorkspaceFileView),
  listWorkspaceEdits: (): Promise<BrowseResult<WorkspaceEditList>> =>
    call(Method.WorkspaceListEdits).then(parseWorkspaceEditList),
  readWorkspaceEditDiff: (path: string): Promise<BrowseResult<WorkspaceEditDiff>> =>
    call(Method.WorkspaceReadEditDiff, { path }).then(parseWorkspaceEditDiff),
  revertWorkspaceFile: (path: string): Promise<WorkspaceRevertResult> =>
    call(Method.WorkspaceRevertFile, { path }).then(parseWorkspaceRevert),

  // MCP servers — external tool servers Addison consumes as a client (Phase-2
  // step 7, phases 1–2). `addMcpServer` saves a name and an address; nothing
  // connects until somebody calls `refreshMcpServer`, and even then Addison can
  // only SEE what the server offers — nothing it finds is callable. A refusal (a
  // bad address, a name already used, the Developer-only sentence) is a resolved
  // {ok:false} carrying the core's own plain words, never a reject. No key or
  // token ever rides these payloads.
  listMcpServers: (): Promise<McpServer[]> => call(Method.McpList).then(parseMcpServers),
  addMcpServer: (name: string, url: string): Promise<McpMutationResult> =>
    call(Method.McpAdd, { name, url }).then(parseMcpMutation),
  removeMcpServer: (id: string): Promise<McpMutationResult> =>
    call(Method.McpRemove, { id }).then(parseMcpMutation),
  refreshMcpServer: (id: string): Promise<McpRefreshResult> =>
    call(Method.McpRefresh, { id }).then(parseMcpRefresh),

  // Messaging channels — the phone connections a person can save (phase 1 of
  // three). NOT ONE OF THESE REACHES A NETWORK: `addChannel` writes a row that is
  // switched off and connected to nothing, `listChannels` reads those rows, and
  // `removeChannel` takes one away. There is no adapter, no poll loop and no
  // pairing in this build. NO TOKEN RIDES THESE PAYLOADS in either direction — the
  // token goes to the OS keychain through `storeChannelKey` below, exactly as an
  // API key does. A refusal (a name already used, the Developer-only sentence) is a
  // resolved {ok:false} carrying the core's own plain words, never a reject.
  listChannels: (): Promise<Channel[]> => call(Method.ChannelList).then(parseChannels),
  addChannel: (kind: ChannelKind, name: string): Promise<ChannelMutationResult> =>
    call(Method.ChannelAdd, { kind, name }).then(parseChannelMutation),
  removeChannel: (id: string): Promise<ChannelMutationResult> =>
    call(Method.ChannelRemove, { id }).then(parseChannelMutation),

  // Automations — what Addison has written down for the OS to run (Phase-2 step 8).
  // NOT ONE OF THESE CAN START ANYTHING. `listAutomations` reads saved rows,
  // `removeAutomation` takes one away, `getAutomationStatus` ASKS what is installed
  // and changes nothing, and `disarmOrphanAutomation` only ever STOPS a job. Arming
  // itself is a TOOL (`arm_automation`), gated by the typed code and performed by
  // the Rust shell — there is no arm method on this surface and no plist on any of
  // these payloads. All four answer in every profile: a saved automation is
  // configuration, not an ability, so a profile switch never hides one, never traps
  // a removal, and never leaves somebody unable to switch a job off.
  listAutomations: (): Promise<Automation[]> =>
    call(Method.AutomationList).then(parseAutomations),
  removeAutomation: (id: string): Promise<AutomationMutationResult> =>
    call(Method.AutomationRemove, { id }).then(parseAutomationMutation),
  // Asked when the Automations section LOADS and at no other time: never stored,
  // never polled, never checked at startup (plan §5.6). A G3 restore can put a row
  // back and can never put a running job back, so the row is the record and the
  // operating system is the truth.
  getAutomationStatus: (): Promise<AutomationStatus> =>
    call(Method.AutomationStatus).then(parseAutomationStatus),
  // The ORPHAN path: a job the operating system is holding under one of Addison's own
  // labels with no saved row behind it, because a restore took the row away. It takes
  // the LABEL, since a label is all that is left of such a job — there is no id, no
  // name and no command to send. The core validates the label against the set Addison
  // mints before it asks the shell anything, so this cannot reach somebody else's
  // launchd job even if this side sent one.
  disarmOrphanAutomation: (label: string): Promise<AutomationMutationResult> =>
    call(Method.AutomationDisarmOrphan, { label }).then(parseAutomationMutation),
};

// ---------------------------------------------------------------------------
// Conversation-history result shapes + defensive parsers. Like the rest of the
// core payloads these aren't pinned in protocol.ts, so we coerce carefully.
// ---------------------------------------------------------------------------
export interface LoadedConversationRow {
  id: string;
  role: string;
  content: string;
}

export interface LoadedConversation {
  conversationId: string;
  title: string | null;
  messages: LoadedConversationRow[];
  /**
   * The steps of the reopened chat's LAST turn — the same {toolId, label, detail?}
   * shape a live `tool.activityUpdate` carries, so "Addison's work" is one panel
   * fed from two places rather than two panels (KNOWN-BUGS #5: it used to vanish on
   * reload, taking "Save as routine" with it). Empty when that turn did no work.
   */
  work: ActivityUpdate[];
  /**
   * The chat this one carried on from (§4.8), or null for an ordinary chat. Read
   * off the stored `conversations` row, which is what makes the thread's boundary
   * marker durable: the live note goes out on the per-turn Activity channel and is
   * gone by the next send, while this is still true a week later.
   */
  continuedFrom: string | null;
  /**
   * The summary the continuation was seeded with, or null. Shown behind a
   * disclosure in the marker — the older messages are still on disk in the chat
   * `continuedFrom` names, and this is the access path to them, never a
   * replacement for them.
   */
  summary: string | null;
}

export interface ConversationRenameResult {
  ok: boolean;
  /** The canonical stored title (trimmed/capped by the core), when ok. */
  title?: string;
  error?: string;
}

// Fails closed, like the other mutation parsers: a missing/garbled result is
// simply `{ ok: false }`, so the caller reverts the optimistic rename.
export function parseConversationRename(result: unknown): ConversationRenameResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    title: typeof obj?.title === "string" ? obj.title : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

// --- provider.list / provider.connect parsers ------------------------------
export interface ProviderConnectResult {
  ok: boolean;
  error?: string;
}

function parseProviderList(result: unknown): ProviderInfo[] {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.providers) ? (obj.providers as unknown[]) : [];
  const out: ProviderInfo[] = [];
  for (const item of list) {
    const row = asRecord(item);
    if (!row || typeof row.id !== "string") continue;
    const info: ProviderInfo = {
      id: row.id,
      label: typeof row.label === "string" ? row.label : row.id,
      connected: row.connected === true,
    };
    if (typeof row.addedAt === "number") info.addedAt = row.addedAt;
    if (typeof row.baseUrl === "string") info.baseUrl = row.baseUrl;
    if (typeof row.lastCheckOk === "boolean") info.lastCheckOk = row.lastCheckOk;
    out.push(info);
  }
  return out;
}

function parseConnectResult(result: unknown): ProviderConnectResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

function parseConversationId(result: unknown): string {
  const obj = asRecord(result);
  const id = obj?.conversationId ?? obj?.id;
  if (typeof id !== "string" || !id) {
    throw new Error("Couldn't start a new conversation.");
  }
  return id;
}

export function parseLoadedConversation(result: unknown): LoadedConversation {
  const obj = asRecord(result);
  if (!obj) throw new Error("Couldn't open that conversation.");
  const conversationId =
    typeof obj.conversationId === "string"
      ? obj.conversationId
      : typeof obj.id === "string"
        ? obj.id
        : "";
  const rawMessages = Array.isArray(obj.messages) ? obj.messages : [];
  const messages: LoadedConversationRow[] = [];
  for (const item of rawMessages) {
    const row = asRecord(item);
    if (!row || typeof row.role !== "string") continue;
    messages.push({
      id: typeof row.id === "string" ? row.id : "",
      role: row.role,
      content: typeof row.content === "string" ? row.content : "",
    });
  }
  // Fails closed, like every parser here: a garbled step is dropped rather than
  // rendered as "undefined", and a missing `work` key (an older core, or a last
  // turn that used no tools) is simply no steps.
  const rawWork = Array.isArray(obj.work) ? obj.work : [];
  const work: ActivityUpdate[] = [];
  for (const item of rawWork) {
    const step = asRecord(item);
    if (!step || typeof step.label !== "string" || !step.label) continue;
    const detail = typeof step.detail === "string" ? step.detail.trim() : "";
    work.push({
      label: step.label,
      toolId: typeof step.toolId === "string" ? step.toolId : "",
      ...(detail ? { detail } : {}),
    });
  }
  // The boundary, when there is one. Both keys are absent on an ordinary chat and
  // the marker is drawn from `continuedFrom` alone — a continuation whose summary
  // somehow did not come back still says a boundary is here, with nothing behind
  // the disclosure, rather than saying nothing at all.
  const continuedFrom =
    typeof obj.continuedFrom === "string" && obj.continuedFrom ? obj.continuedFrom : null;
  const summary = typeof obj.summary === "string" && obj.summary.trim() ? obj.summary : null;
  return {
    conversationId,
    title: typeof obj.title === "string" ? obj.title : null,
    messages,
    work,
    continuedFrom,
    summary,
  };
}

// ---------------------------------------------------------------------------
// Widget / stats result shapes + defensive parsers. Like the rest of the core
// payloads these aren't pinned in protocol.ts, so we coerce carefully — and a
// spec that doesn't match one of the two allowed shapes is DROPPED, never
// rendered (the frontend mirror of the core's render-time validation).
// ---------------------------------------------------------------------------
export interface WidgetMutationResult {
  ok: boolean;
  error?: string;
}

/** widget.run — command widgets only (OPEN mode). `output` is the command's
 * transcript-capped output on success; `error` a plain sentence otherwise. */
export interface WidgetRunResult {
  ok: boolean;
  output?: string;
  error?: string;
}

const STAT_SOURCES: WidgetStatSource[] = ["tokens_month", "provider_latency", "connections"];

function parseWidgetSpec(value: unknown): WidgetSpec | null {
  const obj = asRecord(value);
  if (!obj || typeof obj.title !== "string" || !obj.title) return null;
  if (obj.kind === "routine") {
    if (typeof obj.routineId !== "string" || !obj.routineId) return null;
    return { kind: "routine", routineId: obj.routineId, title: obj.title };
  }
  if (obj.kind === "stat") {
    const source = obj.source;
    if (typeof source !== "string" || !STAT_SOURCES.includes(source as WidgetStatSource)) {
      return null;
    }
    return { kind: "stat", source: source as WidgetStatSource, title: obj.title };
  }
  // The three interactive SAFE kinds. Each is DISPLAY + LOCAL EDIT only: ticking
  // a box or typing in a note calls widget.setState, which the core validates
  // per kind before storing. Nothing here runs anything.
  if (obj.kind === "checklist") {
    const items = Array.isArray(obj.items) ? obj.items : null;
    // Fail CLOSED, like every other parser here: a checklist we can't render
    // fully is dropped rather than rendered short, because `checked` maps to
    // these items by POSITION and a silently shortened list would tick wrongly.
    if (!items || items.length === 0 || items.some((i) => typeof i !== "string")) return null;
    return { kind: "checklist", items: items as string[], title: obj.title };
  }
  if (obj.kind === "note") {
    if (typeof obj.text !== "string") return null;
    return { kind: "note", text: obj.text, title: obj.title };
  }
  if (obj.kind === "timer") {
    if (typeof obj.seconds !== "number" || !Number.isFinite(obj.seconds) || obj.seconds <= 0) {
      return null;
    }
    return { kind: "timer", seconds: obj.seconds, title: obj.title };
  }
  // A command widget (OPEN/Developer mode) is DISPLAY DATA ONLY — never executed
  // client-side. We keep the command text so the rail can show it; running it is
  // the core's job (run_command tool + gate), and this build exposes no such path.
  if (obj.kind === "command") {
    if (typeof obj.command !== "string" || !obj.command) return null;
    return { kind: "command", command: obj.command, title: obj.title };
  }
  return null;
}

/**
 * One widget's stored state, judged AGAINST ITS SPEC. Fails closed to
 * `undefined` — a state that doesn't fit is not half-applied, the widget simply
 * draws its declaration (the same call the core makes on the way out).
 */
export function parseWidgetState(spec: WidgetSpec, value: unknown): WidgetState | undefined {
  const obj = asRecord(value);
  if (!obj) return undefined;
  if (spec.kind === "checklist") {
    const checked = Array.isArray(obj.checked) ? obj.checked : null;
    // Length is the whole safety property: `checked[i]` belongs to `items[i]`.
    if (!checked || checked.length !== spec.items.length) return undefined;
    if (checked.some((c) => typeof c !== "boolean")) return undefined;
    return { checked: checked as boolean[] };
  }
  if (spec.kind === "note") {
    return typeof obj.text === "string" ? { text: obj.text } : undefined;
  }
  if (spec.kind === "timer") {
    const { running, remaining, startedAt } = obj;
    if (typeof running !== "boolean") return undefined;
    if (typeof remaining !== "number" || !Number.isFinite(remaining) || remaining < 0) {
      return undefined;
    }
    if (running) {
      if (typeof startedAt !== "number" || !Number.isFinite(startedAt)) return undefined;
      return { running, remaining, startedAt };
    }
    return { running, remaining, startedAt: null };
  }
  return undefined;
}

export function parseWidgetList(result: unknown): Widget[] {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.widgets) ? (obj.widgets as unknown[]) : [];
  const out: Widget[] = [];
  for (const item of list) {
    const row = asRecord(item);
    if (!row || typeof row.id !== "string") continue;
    const spec = parseWidgetSpec(row.spec);
    if (!spec) continue; // drop anything not one of the allowed shapes
    // created_in_mode ("safe" | "open") when the core forwards it — drives the
    // Developer "DEV" annotation tag. Accept either camel/snake spelling.
    const rawMode = row.createdInMode ?? row.created_in_mode;
    // Why this widget can't be used under the active profile, when the core says
    // so — the rail renders those rows disabled instead of dropping them.
    const unavailable = normalizeUnavailable(row.unavailable);
    // Interactive kinds only, and only when it fits the spec — see parseWidgetState.
    const state = parseWidgetState(spec, row.state);
    out.push({
      id: row.id,
      spec,
      pinned: row.pinned !== false,
      createdInMode: rawMode === "open" || rawMode === "safe" ? rawMode : undefined,
      ...(state ? { state } : {}),
      ...(unavailable ? { unavailable } : {}),
    });
  }
  return out;
}

/** widget.setState — `state` is what the CORE stored, echoed back. */
export interface WidgetStateResult {
  ok: boolean;
  state?: unknown;
  error?: string;
}

function parseWidgetStateResult(result: unknown): WidgetStateResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    state: obj?.state,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

function parseWidgetMutation(result: unknown): WidgetMutationResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

function parseWidgetRun(result: unknown): WidgetRunResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    output: typeof obj?.output === "string" ? obj.output : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

function parseWidgetProposal(result: unknown): WidgetProposal {
  const obj = asRecord(result);
  const spec = parseWidgetSpec(obj?.spec);
  if (!obj || !spec) {
    throw new Error("Addison couldn't draft a widget from this yet.");
  }
  return {
    title: typeof obj.title === "string" ? obj.title : spec.title,
    kind: typeof obj.kind === "string" ? obj.kind : spec.kind,
    summary: typeof obj.summary === "string" ? obj.summary : "",
    spec,
  };
}

export function parseStats(result: unknown): Stats {
  const obj = asRecord(result);
  const tokens = asRecord(obj?.tokensMonth);
  const total = typeof tokens?.total === "number" ? tokens.total : 0;
  const limit = typeof tokens?.limit === "number" ? tokens.limit : null;

  const latencyRaw = obj && Array.isArray(obj.providerLatency) ? obj.providerLatency : [];
  const providerLatency: ProviderLatencyStat[] = [];
  for (const item of latencyRaw) {
    const row = asRecord(item);
    if (!row || typeof row.provider !== "string" || typeof row.ms !== "number") continue;
    providerLatency.push({
      provider: row.provider,
      ms: row.ms,
    });
  }

  const connRaw = obj && Array.isArray(obj.connections) ? obj.connections : [];
  const connections: ConnectionStat[] = [];
  for (const item of connRaw) {
    const row = asRecord(item);
    if (!row || typeof row.id !== "string") continue;
    const status = row.status;
    connections.push({
      id: row.id,
      label: typeof row.label === "string" ? row.label : row.id,
      status:
        status === "running" || status === "reachable" || status === "idle" || status === "unreachable"
          ? status
          : "idle",
      detail: typeof row.detail === "string" ? row.detail : "",
    });
  }

  return { tokensMonth: { total, limit }, providerLatency, connections };
}

// ---------------------------------------------------------------------------
// Skill result shapes + defensive parsers. Like the other core payloads these
// aren't pinned in protocol.ts, so we coerce carefully — and fail CLOSED: a row
// without a usable string id or name is DROPPED, never rendered.
// ---------------------------------------------------------------------------

/** skill.create/update/setEnabled/delete → {ok, id?, error?}. `id` rides only
 * on a successful create; a failed mutation is a resolved {ok:false} carrying a
 * plain-language `error`, never a reject. */
export interface SkillMutationResult {
  ok: boolean;
  id?: string;
  error?: string;
}

export function parseSkillList(result: unknown): Skill[] {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.skills) ? (obj.skills as unknown[]) : [];
  const out: Skill[] = [];
  for (const item of list) {
    const row = asRecord(item);
    // Fail closed: a skill with no usable id or name can't be listed or acted on.
    if (!row || typeof row.id !== "string" || !row.id) continue;
    if (typeof row.name !== "string" || !row.name) continue;
    out.push({
      id: row.id,
      name: row.name,
      instructions: typeof row.instructions === "string" ? row.instructions : "",
      // Default ON when absent (the core defaults enabled=1); only an explicit
      // `false` turns it off. Mirrors parseWidgetList's `pinned !== false`.
      enabled: row.enabled !== false,
    });
  }
  return out;
}

function parseSkillMutation(result: unknown): SkillMutationResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    id: typeof obj?.id === "string" ? obj.id : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

// ---------------------------------------------------------------------------
// Restore-point shapes + defensive parsers (G3). Fail CLOSED, and here that word
// carries more weight than usual: a row we can't identify or date is a row the
// card could offer as a way back and then fail to restore. Better to not offer
// it. Nothing in these payloads is secret — no copy of the config, no key, no
// chat — so the parsers only have to worry about shape.
// ---------------------------------------------------------------------------

/** snapshot.create/delete → {ok, snapshotId?, error?}. An expected refusal (a
 * permanent row, a failed save) is a resolved {ok:false} carrying a plain
 * sentence, never a reject. */
export interface SnapshotMutationResult {
  ok: boolean;
  snapshotId?: string;
  error?: string;
}

/** snapshot.restore/restoreLastWorking → {ok, snapshotId?, detail?, error?,
 * binaryMismatch?}. `detail` is the plain "here's what just happened" sentence;
 * `binaryMismatch` says the point was saved on a different version of Addison. */
export interface SnapshotRestoreResult {
  ok: boolean;
  snapshotId?: string;
  detail?: string;
  error?: string;
  binaryMismatch?: string;
}

/** The label the core gives the very first snapshot. Restoring to it throws away
 * everything the person has set up since install, so the card says so out loud
 * (§11.2). Kept in step with REASONS["genesis"] in snapshot_manager.py. */
export const GENESIS_LABEL = "Addison as first installed";

export function parseSnapshotList(result: unknown): SnapshotList {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.snapshots) ? (obj.snapshots as unknown[]) : [];
  const out: Snapshot[] = [];
  for (const item of list) {
    const row = asRecord(item);
    if (!row || typeof row.id !== "string" || !row.id) continue;
    // No usable timestamp means the row can't be named to the person before they
    // click it, and naming the target is the whole point of the confirm step.
    if (typeof row.createdAt !== "number" || !Number.isFinite(row.createdAt)) continue;
    // Accept either camel/snake spelling, like parseWidgetList.
    const rawMode = row.createdInMode ?? row.created_in_mode;
    out.push({
      id: row.id,
      createdAt: row.createdAt,
      trigger: row.trigger === "on_command" ? "on_command" : "auto",
      reason: typeof row.reason === "string" ? row.reason : "other",
      // Never fall back to the raw slug — a slug is a machine fact and this line
      // is read by the person deciding whether to go back to it.
      reasonLabel:
        typeof row.reasonLabel === "string" && row.reasonLabel ? row.reasonLabel : "Before a change",
      // Default OFF for all three: claiming a point is verified-working, or
      // permanent, or version-stamped when the core didn't say so would each be
      // a promise the floor can't keep.
      verifiedWorking: row.verifiedWorking === true,
      undeletable: row.undeletable === true,
      capturesBinary: row.capturesBinary === true,
      createdInMode:
        rawMode === "open" || rawMode === "safe" || rawMode === "custom" ? rawMode : undefined,
    });
  }
  const target = typeof obj?.lastWorkingId === "string" ? obj.lastWorkingId : undefined;
  return {
    snapshots: out,
    lastWorkingId: target,
    lastWorkingLabel: typeof obj?.lastWorkingLabel === "string" ? obj.lastWorkingLabel : undefined,
    lastWorkingProfileChange:
      typeof obj?.lastWorkingProfileChange === "string" ? obj.lastWorkingProfileChange : undefined,
    warning: typeof obj?.warning === "string" ? obj.warning : undefined,
  };
}

function parseSnapshotMutation(result: unknown): SnapshotMutationResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    snapshotId: typeof obj?.snapshotId === "string" ? obj.snapshotId : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

function parseSnapshotRestore(result: unknown): SnapshotRestoreResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    snapshotId: typeof obj?.snapshotId === "string" ? obj.snapshotId : undefined,
    detail: typeof obj?.detail === "string" ? obj.detail : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
    binaryMismatch: typeof obj?.binaryMismatch === "string" ? obj.binaryMismatch : undefined,
  };
}

// ---------------------------------------------------------------------------
// Guard shapes + defensive parsers (Custom profile, Phase-2 step 2). Both guards
// are CLOSED vocabularies, so anything off-vocabulary is coerced to a known-safe
// value rather than trusted: an unrecognized guard value on the wire must never
// become a live setting the strictness comparison then misreads.
// ---------------------------------------------------------------------------

/** guards.set → {ok, destructiveCard?, autoGrantScope?, error?}. A refusal (a
 * bad value, or the anchor that goes with a weakening couldn't be saved) is a
 * resolved {ok:false} carrying a plain, already-user-ready sentence. */
export interface GuardsSetResult {
  ok: boolean;
  destructiveCard?: DestructiveCardGuard;
  autoGrantScope?: AutoGrantScopeGuard;
  error?: string;
}

const DESTRUCTIVE_CARD_VALUES: DestructiveCardGuard[] = ["per_invocation", "session"];
const AUTO_GRANT_SCOPE_VALUES: AutoGrantScopeGuard[] = ["none", "non_destructive", "everything"];

function asDestructiveCard(value: unknown, fallback: DestructiveCardGuard): DestructiveCardGuard {
  return DESTRUCTIVE_CARD_VALUES.includes(value as DestructiveCardGuard)
    ? (value as DestructiveCardGuard)
    : fallback;
}

function asAutoGrantScope(value: unknown, fallback: AutoGrantScopeGuard): AutoGrantScopeGuard {
  return AUTO_GRANT_SCOPE_VALUES.includes(value as AutoGrantScopeGuard)
    ? (value as AutoGrantScopeGuard)
    : fallback;
}

export function parseGuards(result: unknown): GuardsState {
  const obj = asRecord(result);
  const defaultsObj = asRecord(obj?.defaults);
  // The wire carries the fixed defaults, but fall back to the known constants so
  // a partial payload still yields a usable panel rather than a broken one.
  const defaults = {
    destructiveCard: asDestructiveCard(defaultsObj?.destructiveCard, "per_invocation"),
    autoGrantScope: asAutoGrantScope(defaultsObj?.autoGrantScope, "non_destructive"),
  };
  return {
    destructiveCard: asDestructiveCard(obj?.destructiveCard, defaults.destructiveCard),
    autoGrantScope: asAutoGrantScope(obj?.autoGrantScope, defaults.autoGrantScope),
    defaults,
    active: obj?.active === true,
  };
}

function parseGuardsSet(result: unknown): GuardsSetResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    destructiveCard: DESTRUCTIVE_CARD_VALUES.includes(obj?.destructiveCard as DestructiveCardGuard)
      ? (obj?.destructiveCard as DestructiveCardGuard)
      : undefined,
    autoGrantScope: AUTO_GRANT_SCOPE_VALUES.includes(obj?.autoGrantScope as AutoGrantScopeGuard)
      ? (obj?.autoGrantScope as AutoGrantScopeGuard)
      : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

// ---------------------------------------------------------------------------
// Routing shapes + defensive parsers (Phase-2 step 3). The strategy is a CLOSED
// vocabulary, so anything off-vocabulary is coerced to `quality_first` (the safe
// default — the strongest model answers) rather than trusted: a garbled wire
// value must never become a live strategy the picker then misreads. The chain is
// a list of model-id strings; non-string entries are dropped. `answeredWith`
// fails closed too — a malformed shape yields `undefined`, so no chip renders.
// ---------------------------------------------------------------------------

/** routing.set → {ok, strategy?, customChain?, error?}. A refusal (a bad value,
 * or the custom-chain overwrite whose snapshot couldn't be saved) is a resolved
 * {ok:false} carrying a plain, already-user-ready sentence. */
export interface RoutingSetResult {
  ok: boolean;
  strategy?: RoutingStrategy;
  customChain?: string[];
  error?: string;
}

const ROUTING_STRATEGIES: RoutingStrategy[] = [
  "quality_first",
  "cost_first",
  "local_only",
  "custom",
];

function asStrategy(value: unknown, fallback: RoutingStrategy): RoutingStrategy {
  return ROUTING_STRATEGIES.includes(value as RoutingStrategy)
    ? (value as RoutingStrategy)
    : fallback;
}

/** Coerce a raw chain to a list of non-empty model-id strings. Anything else is
 * dropped — a chain the picker can't act on is worse than a shorter one. */
function asChain(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item) out.push(item);
  }
  return out;
}

export function parseRouting(result: unknown): RoutingState {
  const obj = asRecord(result);
  // Off-vocabulary or missing → quality_first, the strongest-model default. Never
  // fall through to a strategy the picker can't render.
  const strategy = asStrategy(obj?.strategy, "quality_first");
  // Keep only known strategies, in the order the core listed them; if none
  // survive, offer at least the current strategy so the picker isn't empty.
  const rawAvailable = Array.isArray(obj?.availableStrategies) ? obj.availableStrategies : [];
  const available = rawAvailable.filter((s): s is RoutingStrategy =>
    ROUTING_STRATEGIES.includes(s as RoutingStrategy),
  );
  const availableStrategies = available.length > 0 ? available : [strategy];
  // The Simple two-option toggle is the safe default surface — an unknown value
  // never reveals the full picker + chain builder to a Simple user.
  const surface: RoutingSurface = obj?.surface === "full" ? "full" : "toggle";
  return {
    strategy,
    availableStrategies,
    customChain: asChain(obj?.customChain),
    surface,
  };
}

function parseRoutingSet(result: unknown): RoutingSetResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    strategy: ROUTING_STRATEGIES.includes(obj?.strategy as RoutingStrategy)
      ? (obj?.strategy as RoutingStrategy)
      : undefined,
    customChain: obj?.customChain !== undefined ? asChain(obj.customChain) : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

/**
 * Parse the optional `answeredWith` block on a sendMessage reply (contract D5).
 * Fails CLOSED: a missing block, or one without a usable `modelId`, yields
 * `undefined` so the transcript shows no chip. `free`/`routed`/`truncated` are
 * trusted only on a strict boolean `true` — the chip must never fire on a
 * truthy-ish value, and neither must the offer to carry an answer on.
 *
 * `truncated` is the newest of the three and is read exactly like the others,
 * which is what keeps this parser BACKWARD-COMPATIBLE: a reply from a core that
 * does not send the field leaves it `false`, so nothing new appears. Absent is
 * "no claim", never "cut off".
 */
export function parseAnsweredWith(result: unknown): AnsweredWith | undefined {
  const obj = asRecord(result);
  const raw = asRecord(obj?.answeredWith);
  if (!raw || typeof raw.modelId !== "string" || !raw.modelId) return undefined;
  return {
    modelId: raw.modelId,
    label: typeof raw.label === "string" && raw.label ? raw.label : raw.modelId,
    free: raw.free === true,
    routed: raw.routed === true,
    truncated: raw.truncated === true,
  };
}

// ---------------------------------------------------------------------------
// Add-a-model-server + "make it cheaper" shapes + defensive parsers (Phase-2
// step 4). Both propose parsers fail CLOSED: a shape the card can't act on is
// `null`, so no card renders and Addison falls back to prose. Nothing here is
// secret — the endpoint key NEVER rides in these payloads (G1); it goes straight
// to the keychain via `storeProviderKey`.
// ---------------------------------------------------------------------------

/** endpoint.confirmAdd → {ok, error?}. A failed connect is a resolved
 * {ok:false} carrying a plain, already-user-ready sentence, never a reject. */
export interface EndpointConfirmResult {
  ok: boolean;
  error?: string;
}

// Workspace-trust shapes + defensive parsers (coding harness, Phase-2 step 5).
// Fail CLOSED throughout: a mutation whose shape we can't read is {ok:false}
// (so a grant/revoke never reports a success the core didn't confirm), a roots
// list drops any row without a usable directory string (so the card never offers
// a "Stop trusting" button it can't act on), and the picker yields `null` on
// anything that isn't a non-empty string path (a cancelled or unavailable picker
// must not look like a chosen folder).
// ---------------------------------------------------------------------------

/** workspace.grantTrust/revokeTrust → {ok, error?}. A refusal (the folder is
 * Addison's own data dir, or doesn't exist) is a resolved {ok:false} carrying a
 * plain, already-user-ready sentence, never a reject. */
export interface WorkspaceMutationResult {
  ok: boolean;
  error?: string;
}

/** costPlan.apply → {ok, snapshotId?, error?}. `snapshotId` rides on success (the
 * restore point saved before the change). An expected refusal — most importantly
 * "the restore point couldn't be saved, so nothing changed" — is a resolved
 * {ok:false} carrying a plain sentence, never a reject. */
export interface CostPlanApplyResult {
  ok: boolean;
  snapshotId?: string;
  error?: string;
}

/**
 * Parse `endpoint.proposeFromConversation`. Fails CLOSED: `{none}`, a missing
 * payload, or anything without a usable http(s) base URL yields `null` — no card.
 * The http(s) scheme check is a belt-and-braces guard on top of the core's own
 * `_base_url_problem` validation: the card renders the URL as the address the user
 * is about to trust, so a non-web scheme must never reach it. `isLocalOrLan` is
 * trusted only on a strict boolean `true`.
 */
export function parseEndpointProposal(result: unknown): EndpointProposal | null {
  const obj = asRecord(result);
  if (!obj) return null;
  if (typeof obj.baseUrl !== "string" || !obj.baseUrl) return null;
  if (!/^https?:\/\//i.test(obj.baseUrl)) return null;
  return {
    baseUrl: obj.baseUrl,
    isLocalOrLan: obj.isLocalOrLan === true,
  };
}

function parseEndpointConfirm(result: unknown): EndpointConfirmResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

function parseWorkspaceMutation(result: unknown): WorkspaceMutationResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

/**
 * Parse `costPlan.propose`. Fails CLOSED: `{none}`, a missing payload, or a plan
 * without BOTH a usable skill name and non-empty instructions yields `null` — no
 * card. `strategy` is hard-set to `cost_first` (the only value this flow ever
 * uses); we never trust a different value off the wire onto the card.
 */
export function parseCostPlan(result: unknown): CostPlan | null {
  const obj = asRecord(result);
  if (!obj) return null;
  if (typeof obj.skillName !== "string" || !obj.skillName) return null;
  if (typeof obj.skillInstructions !== "string" || !obj.skillInstructions) return null;
  return {
    skillName: obj.skillName,
    skillInstructions: obj.skillInstructions,
    strategy: "cost_first",
  };
}

function parseCostPlanApply(result: unknown): CostPlanApplyResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    snapshotId: typeof obj?.snapshotId === "string" ? obj.snapshotId : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

export function parseWorkspaceRoots(result: unknown): WorkspaceRoot[] {
  const obj = asRecord(result);
  // `folders` — the key the core actually sends (rpc/workspace._workspace_list,
  // documented on Method.WORKSPACE_LIST). This read said `roots` until an
  // adversarial pass caught it: the list rendered permanently empty, so the
  // "Stop trusting" button never appeared and standing consent that suppresses
  // permission cards could not be revoked from the UI. Both sides' tests were
  // green — the Python one asserted `folders`, the vitest one parsed a hand-built
  // `{roots: […]}` literal, and neither could see the other. The fixture below
  // (shell/src/__tests__/fixtures/workspace.list.json, generated from this very
  // handler) is what makes that class of mismatch impossible now.
  const list = obj && Array.isArray(obj.folders) ? (obj.folders as unknown[]) : [];
  const out: WorkspaceRoot[] = [];
  for (const item of list) {
    const row = asRecord(item);
    // A row we can't name is a row the card can't offer a "Stop trusting" button
    // for — drop it rather than render a control that could only fail.
    if (!row || typeof row.directory !== "string" || !row.directory) continue;
    out.push({
      directory: row.directory,
      grantedAt:
        typeof row.grantedAt === "number" && Number.isFinite(row.grantedAt)
          ? row.grantedAt
          : undefined,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// The review surface's read paths, diff and revert (Phase-3 plan Build §1–§4).
//
// EVERY ONE of these handlers answers one of two shapes: the payload itself, with
// no `ok` key at all, or `{ok: false, error}` carrying a plain sentence the core
// already wrote for a person. So the parsers below return a two-armed result and
// the screen renders one or the other — it never has to decide what an empty
// object means.
//
// Fail CLOSED, and each closure is a specific harm rather than a habit:
//   * an entry whose `escapes` we cannot read is treated as ESCAPING, so the row
//     is dimmed and carries its warning. The opposite would invite a click.
//   * an edit whose `revertable` is not literally `true` renders read-only. The
//     opposite is a button that can only fail.
//   * an edit whose `onDiskChanged` is not literally `true` or `false` is `null` —
//     "Addison can't tell" — because collapsing an unknown into `false` is the one
//     wrong reading that lets a revert discard somebody's own work with no warning.
//   * ...and `replacedBy` fails the OTHER way, which is not an inconsistency: an
//     unreadable value there means "nothing was swapped", because the core refuses a
//     swapped file regardless, and guessing a swap would hide a Revert that works.
//   * a shape we cannot read at all becomes an error sentence, never an empty
//     listing: a file tree that renders "nothing here" for a folder full of files
//     is a lie in exactly the place this surface exists to tell the truth.
// ---------------------------------------------------------------------------

/** Either the payload, or one plain sentence to show instead of it. */
export type BrowseResult<T> = { value: T; error?: undefined } | { value?: undefined; error: string };

/** The one sentence this side authors. Every other refusal is the core's own
 * words, forwarded whole. */
const UNREADABLE_ANSWER = "Addison couldn't read that just now.";

/** `{ok:false, error}` → the sentence; anything else → null (it is a payload). */
function refusal(result: unknown): string | null {
  const obj = asRecord(result);
  if (!obj) return UNREADABLE_ANSWER;
  if (obj.ok === false) {
    return typeof obj.error === "string" && obj.error ? obj.error : UNREADABLE_ANSWER;
  }
  return null;
}

/** The closed set of kinds. Anything else is "other" — NOT "directory", so an
 * unrecognised row can never render as a folder somebody expands. */
function parseEntryKind(value: unknown): WorkspaceEntry["kind"] {
  return value === "file" || value === "directory" || value === "symlink" ? value : "other";
}

export function parseWorkspaceListing(result: unknown): BrowseResult<WorkspaceListing> {
  const refused = refusal(result);
  if (refused) return { error: refused };
  const obj = asRecord(result)!;
  if (typeof obj.directory !== "string" || !obj.directory) return { error: UNREADABLE_ANSWER };
  const rows = Array.isArray(obj.entries) ? (obj.entries as unknown[]) : [];
  const entries: WorkspaceEntry[] = [];
  for (const item of rows) {
    const row = asRecord(item);
    // A row with no name is a row nothing can be said about, and every later call
    // is keyed by it. Dropping beats rendering a nameless line.
    if (!row || typeof row.name !== "string" || !row.name) continue;
    entries.push({
      name: row.name,
      kind: parseEntryKind(row.kind),
      size: typeof row.size === "number" && Number.isFinite(row.size) ? row.size : 0,
      // Unreadable → escaping. See the fail-closed note above.
      escapes: row.escapes !== false,
    });
  }
  return {
    value: {
      directory: obj.directory,
      root: typeof obj.root === "string" && obj.root ? obj.root : null,
      entries,
      truncated: obj.truncated === true,
    },
  };
}

export function parseWorkspaceFileView(result: unknown): BrowseResult<WorkspaceFileView> {
  const refused = refusal(result);
  if (refused) return { error: refused };
  const obj = asRecord(result)!;
  if (typeof obj.path !== "string" || !obj.path) return { error: UNREADABLE_ANSWER };
  // An empty file is a real answer (`""`), so this checks the TYPE and not the
  // truthiness — the difference between "nothing in it" and "we got nothing".
  if (typeof obj.content !== "string") return { error: UNREADABLE_ANSWER };
  return {
    value: {
      path: obj.path,
      root: typeof obj.root === "string" && obj.root ? obj.root : null,
      content: obj.content,
      bytes:
        typeof obj.bytes === "number" && Number.isFinite(obj.bytes)
          ? obj.bytes
          : obj.content.length,
      truncated: obj.truncated === true,
    },
  };
}

function parseEdit(item: unknown): WorkspaceEdit | null {
  const row = asRecord(item);
  if (!row || typeof row.path !== "string" || !row.path) return null;
  const path = row.path;
  return {
    path,
    root: typeof row.root === "string" && row.root ? row.root : null,
    relativePath:
      typeof row.relativePath === "string" && row.relativePath ? row.relativePath : path,
    snapshotIds: Array.isArray(row.snapshotIds)
      ? (row.snapshotIds as unknown[]).filter((id): id is string => typeof id === "string")
      : [],
    writes: typeof row.writes === "number" && Number.isFinite(row.writes) ? row.writes : 1,
    created: row.created === true,
    firstWrittenAt: typeof row.firstWrittenAt === "number" ? row.firstWrittenAt : 0,
    lastWrittenAt: typeof row.lastWrittenAt === "number" ? row.lastWrittenAt : 0,
    // Only a literal `true` earns a Revert control.
    revertable: row.revertable === true,
    // TRI-STATE, and `null` is the answer for everything that is not one of the
    // two booleans.
    onDiskChanged:
      row.onDiskChanged === true ? true : row.onDiskChanged === false ? false : null,
    missing: row.missing === true,
    // Only the two values this side has a sentence for. Anything else — including a
    // kind a later build invents — reads as `null`, which is the OPEN direction here
    // and deliberately so: the core refuses a swapped file whatever this says, so an
    // unreadable value costs a person one refused press, while inventing a swap for
    // an ordinary edit would remove a Revert that works.
    replacedBy:
      row.replacedBy === "shortcut" || row.replacedBy === "other-file"
        ? row.replacedBy
        : null,
  };
}

export function parseWorkspaceEditList(result: unknown): BrowseResult<WorkspaceEditList> {
  const refused = refusal(result);
  if (refused) return { error: refused };
  const obj = asRecord(result)!;
  const rows = Array.isArray(obj.edits) ? (obj.edits as unknown[]) : [];
  const edits: WorkspaceEdit[] = [];
  for (const item of rows) {
    const edit = parseEdit(item);
    if (edit) edits.push(edit);
  }
  return { value: { edits, truncated: obj.truncated === true } };
}

export function parseWorkspaceEditDiff(result: unknown): BrowseResult<WorkspaceEditDiff> {
  const refused = refusal(result);
  if (refused) return { error: refused };
  const obj = asRecord(result)!;
  if (typeof obj.path !== "string" || !obj.path) return { error: UNREADABLE_ANSWER };
  // Both panes must be strings for the same reason as `content` above: a created
  // file's BEFORE is legitimately "".
  if (typeof obj.before !== "string" || typeof obj.after !== "string") {
    return { error: UNREADABLE_ANSWER };
  }
  return {
    value: {
      path: obj.path,
      before: obj.before,
      after: obj.after,
      beforeTruncated: obj.beforeTruncated === true,
      afterTruncated: obj.afterTruncated === true,
    },
  };
}

/** workspace.revertFile → {ok, path?, detail?} | {ok:false, error}. A refusal is a
 * resolved answer carrying the core's plain sentence, never a reject — the same
 * shape `grantTrust` uses. */
export function parseWorkspaceRevert(result: unknown): WorkspaceRevertResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    path: typeof obj?.path === "string" ? obj.path : undefined,
    detail: typeof obj?.detail === "string" ? obj.detail : undefined,
    error:
      obj?.ok === true
        ? undefined
        : typeof obj?.error === "string" && obj.error
          ? obj.error
          : UNREADABLE_ANSWER,
  };
}

/** mcp.add/mcp.remove → {ok, error?}. A refusal — a bad address, a name already
 * in use, or "tool servers are part of the Developer profile" — is a resolved
 * {ok:false} carrying the core's plain sentence, never a reject. */
export interface McpMutationResult {
  ok: boolean;
  error?: string;
}

function parseMcpMutation(result: unknown): McpMutationResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

/** The three states a row may arrive in — the whole of `McpServerStatus`.
 * Anything else is not a state this side knows how to draw, so it becomes
 * "never": on a page about what Addison can reach, the safe way to be wrong is
 * to understate. */
const MCP_CORE_STATUSES = new Set(["never", "ok", "failed"]);

/** How many tools one row may carry into the UI. The core caps discovery well
 * below this; the belt is here because these rows are a STRANGER'S text and a
 * surface that renders ten thousand of them is unusable whatever the reason. */
const MAX_MCP_TOOLS_RENDERED = 200;

function parseMcpTools(value: unknown): McpDiscoveredTool[] {
  const list = Array.isArray(value) ? (value as unknown[]) : [];
  const out: McpDiscoveredTool[] = [];
  for (const item of list) {
    if (out.length >= MAX_MCP_TOOLS_RENDERED) break;
    const row = asRecord(item);
    // A nameless tool is a row nothing can be said about, so it is dropped
    // rather than rendered as a blank line the person has to interpret. A
    // missing description is fine — the section says so in its own words.
    if (!row || typeof row.name !== "string" || !row.name) continue;
    out.push({
      name: row.name,
      description: typeof row.description === "string" ? row.description : "",
    });
  }
  return out;
}

/** One `mcp.list` / `mcp.refresh` row, or `null` when it isn't usable.
 *
 * Fails CLOSED on the `parseWorkspaceRoots` reasoning: a row without a usable id
 * AND name is dropped, because a row the panel can't name is one it would render
 * a "Remove" button for and then fail to act on. `url` is shown to the person as
 * the address Addison reaches, so a non-http(s) string is dropped too — the core
 * already refuses one at the store boundary, and this is the belt on those braces.
 *
 * The discovery fields fail closed in the direction that UNDERSTATES: an
 * unrecognised `status` becomes `never`, a `toolCount` that isn't a number is
 * dropped rather than guessed from the array, and `tools` outside an `ok` row is
 * ignored. Every one of those defaults lands on "Addison doesn't know", which is
 * the only safe way for a page about what Addison can reach to be wrong.
 */
function parseMcpServerRow(value: unknown): McpServer | null {
  const row = asRecord(value);
  if (!row || typeof row.id !== "string" || !row.id) return null;
  if (typeof row.name !== "string" || !row.name) return null;
  if (typeof row.url !== "string" || !/^https?:\/\//i.test(row.url)) return null;
  const status =
    typeof row.status === "string" && MCP_CORE_STATUSES.has(row.status)
      ? (row.status as McpServerStatus)
      : "never";
  const tools = status === "ok" ? parseMcpTools(row.tools) : [];
  return {
    id: row.id,
    name: row.name,
    url: row.url,
    enabled: row.enabled !== false,
    addedAt:
      typeof row.addedAt === "number" && Number.isFinite(row.addedAt) ? row.addedAt : undefined,
    status,
    checkedAt:
      typeof row.checkedAt === "number" && Number.isFinite(row.checkedAt)
        ? row.checkedAt
        : undefined,
    toolCount:
      status === "ok" && typeof row.toolCount === "number" && Number.isFinite(row.toolCount)
        ? row.toolCount
        : undefined,
    tools,
    skipped:
      typeof row.skipped === "number" && Number.isFinite(row.skipped) && row.skipped > 0
        ? row.skipped
        : undefined,
    error: status === "failed" && typeof row.error === "string" ? row.error : undefined,
  };
}

/** Parse `mcp.list` → the configured tool servers, each with what the last check
 * found. Unusable rows are dropped; junk never throws. */
export function parseMcpServers(result: unknown): McpServer[] {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.servers) ? (obj.servers as unknown[]) : [];
  const out: McpServer[] = [];
  for (const item of list) {
    const row = parseMcpServerRow(item);
    if (row) out.push(row);
  }
  return out;
}

/** `mcp.refresh` → the refreshed row, or the core's refusal.
 *
 * TWO FAILURE CHANNELS, mirroring the handler. `ok:false` means the check did not
 * RUN (wrong profile, or a server no longer saved) and there is no row. A check
 * that ran and FAILED comes back `ok:true` with `server.status === "failed"` and
 * the reason on the row — a person who pressed "Check now" on a server that is
 * switched off has not made a mistake, and flattening the two would tell them
 * they had. A missing/unusable row on an `ok:true` answer degrades to `ok:false`,
 * so the panel never has to render a success with nothing in it. */
export interface McpRefreshResult {
  ok: boolean;
  server?: McpServer;
  error?: string;
}

export function parseMcpRefresh(result: unknown): McpRefreshResult {
  const obj = asRecord(result);
  const error = typeof obj?.error === "string" ? obj.error : undefined;
  if (obj?.ok !== true) return { ok: false, error };
  const server = parseMcpServerRow(obj.server);
  if (!server) return { ok: false, error };
  return { ok: true, server };
}

// ---------------------------------------------------------------------------
// Messaging channels (phase 1) — the phone connections a person has saved.
// Configuration only: nothing in this build connects, polls or pairs, and no
// payload here has ever carried a token.
// ---------------------------------------------------------------------------

/** `channel.add` / `channel.remove` → {ok, error?}. A refusal — the wrong profile,
 * a name already in use, a restore point that could not be saved — is a resolved
 * {ok:false} carrying the core's plain sentence, which the panel prints verbatim. */
export interface ChannelMutationResult {
  ok: boolean;
  error?: string;
}

function parseChannelMutation(result: unknown): ChannelMutationResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

/** The transports this side knows how to describe — the core's closed set. */
const CHANNEL_KINDS = new Set<string>(["telegram"]);

/** The three presence answers, whole. Anything else becomes "unknown", which is
 * the only safe direction: "unknown" reads as "Addison doesn't know", while
 * "absent" would read as "no token saved" about a token that may well be there. */
const CHANNEL_PRESENCES = new Set<string>(["present", "absent", "unknown"]);

/** One `channel.list` row, or `null` when it isn't usable.
 *
 * Fails CLOSED on `parseMcpServerRow`'s reasoning: a row without a usable id and
 * name is dropped, because a row the panel cannot name is one it would render a
 * "Remove" button for and then fail to act on. A `kind` outside the closed set is
 * dropped too — the panel's copy names the transport, and a connection to a
 * transport with no adapter behind it is a claim the app would be making up.
 *
 * `enabled` defaults to FALSE on anything unrecognised (the opposite default to
 * `McpServer.enabled`, deliberately: an MCP row arrives enabled and a channel row
 * arrives off, and each side's default is the state its core actually writes).
 * `tokenPresent` and `pairedDevices` both fail towards "Addison doesn't know" and
 * zero. */
function parseChannelRow(value: unknown): Channel | null {
  const row = asRecord(value);
  if (!row || typeof row.id !== "string" || !row.id) return null;
  if (typeof row.name !== "string" || !row.name) return null;
  if (typeof row.kind !== "string" || !CHANNEL_KINDS.has(row.kind)) return null;
  const presence =
    typeof row.tokenPresent === "string" && CHANNEL_PRESENCES.has(row.tokenPresent)
      ? (row.tokenPresent as ChannelTokenPresence)
      : "unknown";
  return {
    id: row.id,
    kind: row.kind as ChannelKind,
    name: row.name,
    enabled: row.enabled === true,
    tokenPresent: presence,
    pairedDevices:
      typeof row.pairedDevices === "number" && Number.isFinite(row.pairedDevices)
        ? row.pairedDevices
        : 0,
    addedAt:
      typeof row.addedAt === "number" && Number.isFinite(row.addedAt) ? row.addedAt : undefined,
  };
}

/** Parse `channel.list` → the saved connections. Unusable rows are dropped; junk
 * never throws. */
export function parseChannels(result: unknown): Channel[] {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.channels) ? (obj.channels as unknown[]) : [];
  const out: Channel[] = [];
  for (const item of list) {
    const row = parseChannelRow(item);
    if (row) out.push(row);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Automations (Phase-2 step 8) — the work Addison has written down for THIS
// COMPUTER to run on a schedule. A row is a draft: nothing in the app arms one
// yet, and when arming lands it is the Rust shell that writes the job file.
// ---------------------------------------------------------------------------

/** The core's own words for a row whose schedule says nothing this vocabulary
 * recognises (agent_core/automations.py `schedule_sentence`). Repeated here as the
 * FALLBACK and nothing else: when the core's sentence is missing or unusable, the
 * honest answer is that no schedule is saved — never a guess assembled from the
 * numbers, which is how a surface ends up telling somebody a job runs hourly
 * because a field parsed as a 1. Kept byte-for-byte in step with the core; the
 * generated fixture is where the two are held together. */
const NO_SCHEDULE_SENTENCE = "No schedule saved yet.";

/** `automation.remove` → {ok, error?}. A refusal — the row is already gone, or a
 * restore point could not be saved first — is a resolved {ok:false} carrying the
 * core's plain sentence, never a reject. */
export interface AutomationMutationResult {
  ok: boolean;
  error?: string;
}

/** Exported for the generated-fixture suite, like `parseAutomations` beside it: the
 * refusal sentence this pulls out is the whole of what a person sees when switching
 * an orphaned job off does not land, and the only artifact both sides share is the
 * payload the real handler produces. */
export function parseAutomationMutation(result: unknown): AutomationMutationResult {
  const obj = asRecord(result);
  return {
    ok: obj?.ok === true,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}

/** The closed schedule vocabulary, exactly as the core declares it
 * (`automations.SCHEDULE_KINDS`). Anything else is a kind this build has never
 * heard of, and the row simply does not say which — the sentence still speaks. */
const AUTOMATION_SCHEDULE_KINDS = new Set(["interval", "calendar"]);

/** The schedule's numbers, and only numbers. The core already projects the stored
 * JSON against the closed field set; this is the belt on those braces, so a string
 * that arrives where a number belongs can never be rendered as one. */
function parseAutomationSchedule(value: unknown): Record<string, number> {
  const record = asRecord(value);
  if (!record) return {};
  const out: Record<string, number> = {};
  for (const [key, entry] of Object.entries(record)) {
    if (typeof entry === "number" && Number.isFinite(entry)) out[key] = entry;
  }
  return out;
}

/** One `automation.list` row, or `null` when it isn't usable.
 *
 * Fails CLOSED on the `parseWorkspaceRoots` / `parseMcpServerRow` reasoning, with
 * one addition of its own. A row is dropped without a usable `id` and `name` — the
 * Remove control is named after the automation, so a row that cannot be named is
 * one the section would offer a button for and then fail to act on — AND without a
 * usable `command`, because the command IS the automation. A row that cannot say
 * what would run would render as a schedule with no consequence attached, which is
 * the one thing this surface must never show.
 */
function parseAutomationRow(value: unknown): Automation | null {
  const row = asRecord(value);
  if (!row || typeof row.id !== "string" || !row.id) return null;
  if (typeof row.name !== "string" || !row.name) return null;
  if (typeof row.command !== "string" || !row.command) return null;
  // Why the active profile can't use this row, when the core says so — in Simple,
  // every row, because an automation runs a command. The SAME parser the routine
  // and widget rows use (lib/parse), because it is the same marker said in each
  // layer's own vocabulary, and one shape must not grow two readings of what
  // counts as one.
  //
  // Fail-closed here means ABSENT, not disabled: a marker that cannot say WHY in a
  // sentence a person can read is dropped, so a malformed payload can never leave
  // somebody looking at their own saved work sitting inert with no explanation.
  // The other direction is never invented — this side does not decide that a row
  // is unavailable, it only renders that the core said so, and the absence of a
  // marker is not a permission either (dispatch is what refuses).
  const unavailable = normalizeUnavailable(row.unavailable);
  return {
    id: row.id,
    name: row.name,
    label: typeof row.label === "string" ? row.label : "",
    command: row.command,
    scheduleKind:
      typeof row.scheduleKind === "string" && AUTOMATION_SCHEDULE_KINDS.has(row.scheduleKind)
        ? (row.scheduleKind as Automation["scheduleKind"])
        : undefined,
    schedule: parseAutomationSchedule(row.schedule),
    // The core's sentence, or the core's own "nothing saved" line. Never one built
    // here out of `schedule`: two renderers of one fact is how the second one ends
    // up saying something the first never would.
    scheduleSentence:
      typeof row.scheduleSentence === "string" && row.scheduleSentence
        ? row.scheduleSentence
        : NO_SCHEDULE_SENTENCE,
    createdInMode:
      row.createdInMode === "safe" || row.createdInMode === "open"
        ? row.createdInMode
        : undefined,
    createdAt:
      typeof row.createdAt === "number" && Number.isFinite(row.createdAt)
        ? row.createdAt
        : undefined,
    ...(unavailable ? { unavailable } : {}),
  };
}

/** Parse `automation.list` → the saved automations, oldest first. Unusable rows are
 * dropped; junk never throws. */
export function parseAutomations(result: unknown): Automation[] {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.automations) ? (obj.automations as unknown[]) : [];
  const out: Automation[] = [];
  for (const item of list) {
    const row = parseAutomationRow(item);
    if (row) out.push(row);
  }
  return out;
}

/** `automation.status` → what the OPERATING SYSTEM says is installed right now.
 *
 * Fails closed on both fields, in the direction that cannot invent capability:
 * `supported` is true only when the core said exactly `true`, so junk, an absent
 * field or a string "true" all read as "arming isn't available here" and the
 * surface offers no Arm. `armed` keeps only non-empty strings — a label is matched
 * against a row's own label, so anything else could only ever match nothing.
 *
 * `error` is the core's own plain sentence when it has one; this side prefers it to
 * anything it would say itself, exactly as the removal path does.
 */
export function parseAutomationStatus(result: unknown): AutomationStatus {
  const obj = asRecord(result);
  const armed = Array.isArray(obj?.armed) ? (obj.armed as unknown[]) : [];
  return {
    armed: armed.filter((label): label is string => typeof label === "string" && label !== ""),
    supported: obj?.supported === true,
    ...(typeof obj?.error === "string" && obj.error ? { error: obj.error } : {}),
  };
}

/** The core's attempt budget (`agent_core/automation_nonce.MAX_ATTEMPTS`) — the
 * value a first ask carries, and what an unusable `attemptsLeft` falls back to so a
 * card that cannot count says nothing about counting. Mirrored in PermissionCard,
 * which is where the decision to SHOW the line lives. */
const ARMING_FIRST_ASK_ATTEMPTS = 3;

/** The arming half of a `permission.requestGrant` frame (step 8 phase 3), or
 * `undefined` when this is an ordinary card.
 *
 * Fail-closed here means keeping the CEREMONY, not dropping it: an arming payload
 * missing a fact is still an arming request, and rendering it as a plain Allow card
 * would show a one-press approval for the one action in the app that must never
 * have one. So the ceremony survives whenever there is a `nonce` to type — and
 * without a nonce there is nothing to type, so the field goes away and the core
 * (which is the only thing that decides) refuses the answer for want of a match.
 *
 * The nonce is never stored, never logged and never put anywhere a model can read
 * it: it lives in this object for as long as the card is on screen, and goes back
 * to the core as the typed answer.
 */
export function parseArming(value: unknown): PermissionRequest["arming"] {
  const obj = asRecord(value);
  if (!obj) return undefined;
  const nonce = typeof obj.nonce === "string" ? obj.nonce.trim() : "";
  if (!nonce) return undefined;
  const text = (field: unknown): string => (typeof field === "string" ? field : "");
  const warnings = Array.isArray(obj.warnings) ? (obj.warnings as unknown[]) : [];
  const attemptsLeft = obj.attemptsLeft;
  return {
    nonce,
    automationName: text(obj.automationName),
    // The core's sentence or the core's own "nothing saved" line — never one built
    // here out of numbers this side does not even receive.
    scheduleSentence: text(obj.scheduleSentence) || NO_SCHEDULE_SENTENCE,
    command: text(obj.command),
    installPath: text(obj.installPath),
    // Verbatim, and only the strings: this side renders the core's warning copy and
    // has none of its own to substitute.
    warnings: warnings.filter((w): w is string => typeof w === "string" && w !== ""),
    attemptsLeft:
      typeof attemptsLeft === "number" && Number.isInteger(attemptsLeft) && attemptsLeft >= 0
        ? attemptsLeft
        : ARMING_FIRST_ASK_ATTEMPTS,
  };
}

/** workspace.pickDirectory → the chosen absolute path, or `null` when the person
 * cancelled or no picker is available. Anything that isn't a non-empty string is
 * `null` — a cancelled picker must never look like a chosen folder.
 *
 * `error` is the core's own plain sentence for the ONE case it can name: the
 * picker was still open when the core stopped waiting for it. It used to arrive
 * as a bare `{directory: null}`, i.e. indistinguishable from Cancel, so browsing
 * too long silently did nothing. Verbatim, like every other core sentence this
 * file renders — and `null` for a cancel, which needs no explaining. */
export interface WorkspaceDirectoryPick {
  directory: string | null;
  error: string | null;
}

export function parseWorkspaceDirectory(result: unknown): WorkspaceDirectoryPick {
  const obj = asRecord(result);
  const dir = obj?.directory;
  const error = obj?.error;
  return {
    directory: typeof dir === "string" && dir ? dir : null,
    error: typeof error === "string" && error ? error : null,
  };
}

// ---------------------------------------------------------------------------
// Keychain write (Frontend → Rust shell, NOT via the core). BYOK keys are
// handed straight to the highest-trust Rust process to store in the OS
// keychain; they are write-only from here and never read back, never persisted
// in the webview, never sent to the Agent Core memory (invariant §8.3).
// ---------------------------------------------------------------------------
export async function storeProviderKey(provider: string, key: string): Promise<void> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  try {
    await invoke("store_provider_key", { provider, key });
  } catch (err) {
    // §5.3. The Rust store boundary refuses a key whose SHAPE is wrong with a
    // plain, fixable sentence ("That key has a line break in it — paste it again
    // as one line."). A Tauri command returning `Err(String)` rejects with the
    // BARE STRING, and the Settings row only re-shows `err.message` — so without
    // this the one sentence that says how to fix the paste is thrown away and
    // replaced by the generic "check the key and try again", which is the
    // mystifying failure §5.3 exists to remove.
    throw new Error(toPlainMessage(err));
  }
}

// The messaging-channel bot token (phase 1). The SAME path as a provider key and a
// PARALLEL command, never the provider one: the account namespace is
// `channel-key:<kind>` and the shell's own comment gives the three reasons the
// provider path is not reused. Write-only from here — the engine reads a token at
// the moment of use and this window can never read one back (G1).
export async function storeChannelKey(kind: string, key: string): Promise<void> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  try {
    await invoke("store_channel_key", { kind, key });
  } catch (err) {
    // The Rust store boundary refuses a token whose SHAPE is wrong with a plain,
    // fixable sentence ("That key has a line break in it — paste it again as one
    // line."). A Tauri command returning `Err(String)` rejects with the BARE
    // STRING, so without this the one sentence that says how to fix the paste is
    // thrown away — the same repair `storeProviderKey` above needed.
    throw new Error(toPlainMessage(err));
  }
}

// Removing a channel deletes its token first, from HERE. The core's side of the
// keychain is a read and nothing more, so the window that wrote the token is what
// removes it — the same shape as the provider "Remove" action, and the reason the
// core was never handed a delete-anything verb.
export async function deleteChannelKey(kind: string): Promise<void> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  try {
    await invoke("delete_channel_key", { kind });
  } catch (err) {
    throw new Error(toPlainMessage(err));
  }
}

// The "Remove" action: delete a provider's stored key from the OS keychain. Like
// the write, this goes straight to the highest-trust Rust process, never the core.
export async function deleteProviderKey(provider: string): Promise<void> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  await invoke("delete_provider_key", { provider });
}

/**
 * Appended to a failed-connect message when — and only when — the rollback below
 * could NOT put things back. The clobber itself is contract-mandated (the key is
 * saved before the connect, because the core reads it from the OS at connect time
 * and it may never be a parameter of a core frame, G1). An UNDISCLOSED clobber is
 * not, and this is the floor for the case Addison genuinely cannot undo.
 */
export const KEY_REPLACED_NOTICE =
  "The key you entered replaced the one you had saved before, and Addison couldn't " +
  "put the old one back. Add it again in Settings if you still need it.";

/**
 * Undo the last `storeProviderKey` for this provider by putting back whatever it
 * replaced — for a connect that then failed and made the save pointless.
 *
 * Answers whether the keychain is back as it was. `false` means it is NOT, and the
 * caller must show {@link KEY_REPLACED_NOTICE}: the shell records what a save
 * replaced only when it positively READ it, so a dismissed password dialog leaves
 * nothing to put back and it will not guess by deleting an item it never saw
 * (`keychain.rs`, "PUTTING BACK WHAT A SAVE REPLACED").
 *
 * Never throws, and never rejects: every caller is already on a path that is
 * reporting a failure, and a rollback that threw would replace that failure's own
 * sentence with its own. It answers `false` instead, which discloses.
 *
 * G1: a provider id goes out and a boolean comes back. No key value crosses here in
 * either direction — the previous key never leaves the Rust process.
 */
export async function restoreReplacedProviderKey(provider: string): Promise<boolean> {
  if (!isEngineConnected()) {
    return false;
  }
  try {
    return (await invoke("restore_replaced_provider_key", { provider })) === true;
  } catch {
    return false;
  }
}
