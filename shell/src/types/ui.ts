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

/**
 * Live state of the "Run a model on this computer" flow (spec §4.1.2), held in
 * App and rendered inside the Settings section. Only one setup runs at a time;
 * `modelId` is the curated model the user chose. Progress lines arrive on
 * `model.localSetupProgress`; the terminal state comes from the
 * `startLocalSetup` promise (done) or a plain-language error (error).
 */
export interface LocalSetupState {
  modelId: string;
  status: "running" | "done" | "error";
  /** Plain-language stage label, e.g. "Checking your computer", "Downloading". */
  stage?: string;
  /** 0–100 when the core reports it; omitted for stages with no measurable progress. */
  percent?: number;
  /** A plain-language line from the core to show under the stage. */
  message?: string;
  /** Plain-language failure, shown inline. */
  error?: string;
}
