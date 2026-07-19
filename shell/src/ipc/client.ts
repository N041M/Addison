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
import { Method, type ModelRole } from "../types/protocol";
import { asRecord } from "../lib/parse";
import {
  parseConversationSummaries,
  type ConversationSummary,
  type Skill,
  type Widget,
  type WidgetProposal,
  type WidgetSpec,
  type WidgetStatSource,
  type Stats,
  type ConnectionStat,
  type ProviderLatencyStat,
} from "../types/ui";

const DEFAULT_TIMEOUT_MS = 120_000;

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
      // reject just below — shadowing it here would be a footgun.
      const diag: DiagnosticEntry = { message, raw: rawValue, at: Date.now() };
      diagnosticsHandlers.forEach((h) => h(diag));
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
    }, DEFAULT_TIMEOUT_MS);

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
  return () => {
    diagnosticsHandlers.delete(handler);
  };
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
    call(Method.ConversationSendMessage, { text, role, modelId, effort }),

  respondToPermission: (toolId: string, allow: boolean) =>
    call(Method.PermissionRespond, { toolId, allow }),

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

function parseLoadedConversation(result: unknown): LoadedConversation {
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
  return {
    conversationId,
    title: typeof obj.title === "string" ? obj.title : null,
    messages,
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
  // A command widget (OPEN/Developer mode) is DISPLAY DATA ONLY — never executed
  // client-side. We keep the command text so the rail can show it; running it is
  // the core's job (run_command tool + gate), and this build exposes no such path.
  if (obj.kind === "command") {
    if (typeof obj.command !== "string" || !obj.command) return null;
    return { kind: "command", command: obj.command, title: obj.title };
  }
  return null;
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
    out.push({
      id: row.id,
      spec,
      pinned: row.pinned !== false,
      createdInMode: rawMode === "open" || rawMode === "safe" ? rawMode : undefined,
    });
  }
  return out;
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
// Keychain write (Frontend → Rust shell, NOT via the core). BYOK keys are
// handed straight to the highest-trust Rust process to store in the OS
// keychain; they are write-only from here and never read back, never persisted
// in the webview, never sent to the Agent Core memory (invariant §8.3).
// ---------------------------------------------------------------------------
export async function storeProviderKey(provider: string, key: string): Promise<void> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  await invoke("store_provider_key", { provider, key });
}

// The "Remove" action: delete a provider's stored key from the OS keychain. Like
// the write, this goes straight to the highest-trust Rust process, never the core.
export async function deleteProviderKey(provider: string): Promise<void> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  await invoke("delete_provider_key", { provider });
}
