// UI-only view types (NOT part of the hand-synced IPC contract in protocol.ts).
// These describe how the frontend holds and renders state; they never cross the
// process boundary.

import type { ChatMessage, ModelRole } from "./protocol";

/** A message as rendered in the thread, with transient display flags. */
export interface DisplayMessage extends ChatMessage {
  /** Assistant message still receiving streamed chunks. */
  pending?: boolean;
  /** The turn ended in a plain-language error rather than a normal answer. */
  failed?: boolean;
}

/** One configurable model role, as surfaced by `model.availableRoles`. */
export interface RoleOption {
  role: ModelRole; // "primary" (Cloud) | "local" (On this computer)
  label: string; // plain-language label, e.g. "Cloud"
  configured: boolean; // whether a way to use this role is set up
  /** Only meaningful for the "local" role: several local models to choose from. */
  models?: { id: string; label: string }[];
}
