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
  /**
   * Developer-only raw error text for a failed turn (the core's `error.data.raw`).
   * Held regardless of profile; only ever rendered when the raw-diagnostics flag
   * is on (Simple never shows it — the plain `content` is unchanged for both).
   */
  raw?: string;
  /**
   * The core's persisted id for this message (from the sendMessage result).
   * "Rewind to here" needs THIS id — the local `id` is display-only and means
   * nothing to the core. Messages without one (welcome, errors) can't anchor
   * a rewind.
   */
  storeId?: string;
}

/**
 * One row in the conversation history list, from `conversation.list`. Parsed
 * defensively like the other free-form core payloads (the shape isn't pinned in
 * protocol.ts). `title` is never null on the wire, but we coerce to a plain
 * fallback just in case; `startedAt` is epoch SECONDS.
 */
export interface ConversationSummary {
  id: string;
  title: string;
  startedAt: number;
  messageCount: number;
}

/**
 * Defensive parser for a `conversation.list` result. Accepts either a bare
 * array or `{ conversations: [...] }`, drops any entry without a string id, and
 * fills sensible fallbacks so a partial payload never crashes the list.
 */
export function parseConversationSummaries(result: unknown): ConversationSummary[] {
  const record = result && typeof result === "object" ? (result as Record<string, unknown>) : null;
  const list = Array.isArray(result)
    ? result
    : record && Array.isArray(record.conversations)
      ? (record.conversations as unknown[])
      : [];

  const out: ConversationSummary[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const obj = item as Record<string, unknown>;
    if (typeof obj.id !== "string") continue;
    out.push({
      id: obj.id,
      title: typeof obj.title === "string" && obj.title ? obj.title : "Untitled conversation",
      startedAt: typeof obj.startedAt === "number" ? obj.startedAt : 0,
      messageCount: typeof obj.messageCount === "number" ? obj.messageCount : 0,
    });
  }
  return out;
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
 * One "how hard to work" level for a cloud model, as surfaced inside a
 * `cloudModels[].effortLevels` entry from `model.availableRoles`. Both fields
 * come from the core — the id crosses back to the core on send; the label is the
 * plain-language wording shown to the user (e.g. "Quick" / "Balanced" /
 * "Thorough"). We never invent or translate these.
 */
export interface EffortLevel {
  id: string;
  label: string;
}

/**
 * One cloud model choice from `model.availableRoles`' `cloudModels` list. The
 * plain `label` (e.g. "Most capable", "Balanced", "Fast") is what the personas
 * see; `description` is a one-line plain explainer shown unobtrusively. When
 * `effortLevels` is empty the effort control is hidden for that model. Exactly
 * one entry in the catalog has `default: true`.
 */
export interface CloudModel {
  id: string;
  label: string;
  description: string;
  effortLevels: EffortLevel[];
  default: boolean;
  /**
   * Which connected provider this model belongs to, and its plain-language name
   * (multi-provider, owner decision 2026-07-18). The picker shows models from
   * every connected provider together and attributes each to its provider when
   * more than one is connected. Optional so an older/partial payload still parses.
   */
  provider?: string;
  providerLabel?: string;
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

/**
 * Frontend feature flags carried on `profile.get` (spec §4.7). These reshape
 * only what is *shown* — never how the permission gate, undo, or key handling
 * work (§8.7). A profile is presentation + defaults, not a security boundary.
 */
export interface ProfileFlags {
  /** Developer: reveal a routine's declarative plan (READ-ONLY in v1, §6.5). */
  exposeRoutinePlan: boolean;
  /** Developer: show real error text / a diagnostics panel instead of only plain messages. */
  rawDiagnostics: boolean;
  /** Developer: surface the headless JSON-RPC entry-point hint for scripting. */
  headlessCli: boolean;
  /** Developer: BYOK/model config up front instead of the Setup Assistant. */
  byokFirstOnboarding: boolean;
}

// ---------------------------------------------------------------------------
// Widgets — DECLARATIVE specs mirrored from the core (agent_core/widgets.py).
// Exactly two shapes: a saved-routine Run pill, or a whitelisted stat display.
// NEVER code. The frontend renders these; it never constructs or evaluates one.
// ---------------------------------------------------------------------------
export type WidgetStatSource = "tokens_month" | "provider_latency" | "connections";

export interface RoutineWidgetSpec {
  kind: "routine";
  routineId: string;
  title: string;
}

export interface StatWidgetSpec {
  kind: "stat";
  source: WidgetStatSource;
  title: string;
}

/**
 * A command widget (OPEN/Developer mode only — agent_core/widgets.py). DISPLAY
 * DATA ONLY here: the frontend never runs the command itself — a command widget
 * would run through the core's run_command tool + gate, exactly like a live
 * command. In this build the core exposes no widget-run path, so the rail shows
 * the command but its Run pill is inert (see WidgetRail). Present in a widget
 * spec means the widget was created in OPEN mode.
 */
export interface CommandWidgetSpec {
  kind: "command";
  command: string;
  title: string;
}

export type WidgetSpec = RoutineWidgetSpec | StatWidgetSpec | CommandWidgetSpec;

/** One stored widget from `widget.list`: id + declarative spec + pin/order state. */
export interface Widget {
  id: string;
  spec: WidgetSpec;
  pinned: boolean;
  position: number;
  /**
   * The policy mode the widget was saved under ("safe" | "open"), when the core
   * forwards it. Drives the Developer-profile "DEV" annotation tag. A command
   * widget is inherently OPEN-created even when this is absent.
   */
  createdInMode?: "safe" | "open";
}

/** A drafted widget from `widget.proposeFromConversation`, awaiting confirm. */
export interface WidgetProposal {
  title: string;
  kind: string;
  summary: string;
  spec: WidgetSpec;
}

/** One connection row from `stats.get`. Status drives the dot color. */
export interface ConnectionStat {
  id: string;
  label: string;
  status: "running" | "reachable" | "idle" | "unreachable";
  detail: string;
}

/** One provider's most-recent latency from `stats.get`. */
export interface ProviderLatencyStat {
  provider: string;
  ms: number;
  checkedAt: number;
}

/** The full `stats.get` picture backing the token meter + connections cards. */
export interface Stats {
  tokensMonth: { total: number; limit: number | null };
  providerLatency: ProviderLatencyStat[];
  connections: ConnectionStat[];
}

/** One selectable profile, with label + description authored by the core. */
export interface ProfileOption {
  id: string;
  label: string;
  description: string;
}

/** The full profile picture from `profile.get`. */
export interface ProfileState {
  activeProfile: string;
  profiles: ProfileOption[];
  flags: ProfileFlags;
  /**
   * The policy mode this profile runs under (agent_core/policy.py): "safe" for
   * Simple, "open" for Developer. Unlike a ProfileFlag, the mode reshapes what
   * Addison is ALLOWED to do (OPEN prompts only for destructive actions and can
   * run commands), so the surface must speak honestly about it. Absent on an old
   * core → treated as "safe" (never over-permissive).
   */
  mode?: "safe" | "open";
}
