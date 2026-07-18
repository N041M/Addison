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
      const entry: DiagnosticEntry = { message, raw: rawValue, at: Date.now() };
      diagnosticsHandlers.forEach((h) => h(entry));
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
  // Kicks off the one-time local-model download/verify for `modelName` (the
  // curated Ollama tag). Resolves when the model is set up and has appeared in
  // `availableRoles`; rejects with a plain-language error (e.g. Ollama not
  // running, machine too small). Live progress arrives on
  // `model.localSetupProgress` in between.
  startLocalSetup: (modelName?: string) =>
    call(Method.ModelStartLocalSetup, { modelName }),
};

// ---------------------------------------------------------------------------
// Keychain write (Frontend → Rust shell, NOT via the core). BYOK keys are
// handed straight to the highest-trust Rust process to store in the OS
// keychain; they are write-only from here and never read back, never persisted
// in the webview, never sent to the Agent Core memory (invariant §8.3).
// ---------------------------------------------------------------------------
export async function storeProviderKey(
  role: string,
  provider: string,
  key: string,
): Promise<void> {
  if (!isEngineConnected()) {
    throw new Error(NOT_CONNECTED_MESSAGE);
  }
  await invoke("store_provider_key", { role, provider, key });
}
