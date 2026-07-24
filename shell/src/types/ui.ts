// UI-only view types (NOT part of the hand-synced IPC contract in protocol.ts).
// These describe how the frontend holds and renders state; they never cross the
// process boundary.

import type { ChatMessage, ModelRole } from "./protocol";
import { asRecord } from "../lib/parse";

/** A message as rendered in the thread, with transient display flags. */
export interface DisplayMessage extends ChatMessage {
  /** Assistant message still receiving streamed chunks. */
  pending?: boolean;
  /** The turn ended in a plain-language error rather than a normal answer. */
  failed?: boolean;
  /**
   * Which model actually answered this turn (Phase-2 step 3). Rides on the
   * sendMessage reply. The free-model disclaimer chip renders ONLY when
   * `answeredWith.free && answeredWith.routed` — both booleans are computed by
   * the core (routed = the answering model wasn't the user's explicit pick); the
   * frontend just reads them, never re-derives (contract D5 [S-b]).
   */
  answeredWith?: AnsweredWith;
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
}

/**
 * Defensive parser for a `conversation.list` result. Accepts either a bare
 * array or `{ conversations: [...] }`, drops any entry without a string id, and
 * fills sensible fallbacks so a partial payload never crashes the list.
 */
export function parseConversationSummaries(result: unknown): ConversationSummary[] {
  const record = asRecord(result);
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
 * see. When `effortLevels` is empty the effort control is hidden for that
 * model. Exactly one entry in the catalog has `default: true`.
 */
export interface CloudModel {
  id: string;
  label: string;
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
 * DATA ONLY here: the frontend never runs the command itself — the Run pill
 * calls the core's widget.run, which routes through the run_command tool + gate
 * (per-invocation destructive prompt), exactly like a routine command step.
 * A command kind in a spec means the widget was created in OPEN mode.
 */
export interface CommandWidgetSpec {
  kind: "command";
  command: string;
  title: string;
}

export type WidgetSpec = RoutineWidgetSpec | StatWidgetSpec | CommandWidgetSpec;

/**
 * One stored widget from `widget.list`: id + declarative spec + pin state. The
 * core returns the list already in user-visible order (ORDER BY position in
 * MemoryStore.list_widgets), so the frontend renders it as received.
 */
export interface Widget {
  id: string;
  spec: WidgetSpec;
  pinned: boolean;
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
}

/** The full `stats.get` picture backing the token meter + connections cards. */
export interface Stats {
  tokensMonth: { total: number; limit: number | null };
  providerLatency: ProviderLatencyStat[];
  connections: ConnectionStat[];
}

// ---------------------------------------------------------------------------
// Skills — user-authored, plain-text guidance notes (skill.list). A skill is
// PURE TEXT: a name + free-form instructions the person writes, toggled on/off.
// When enabled, Addison follows it. There is no code, tool step, or execution
// surface — so, unlike routines/widgets, a skill carries no mode/command field.
// ---------------------------------------------------------------------------
export interface Skill {
  id: string;
  name: string;
  instructions: string;
  enabled: boolean;
}

// ---------------------------------------------------------------------------
// Restore points (snapshot.list) — the G3 guaranteed-rollback floor. One row is
// a point-in-time copy of Addison's settings, services, notes, widgets and
// routines. It never holds a saved key (those stay in the system keychain) and
// never holds a chat, so nothing here is sensitive and nothing here is large:
// `capturesBinary` is a boolean, and the copy itself never crosses the wire.
// ---------------------------------------------------------------------------
export interface Snapshot {
  id: string;
  /** Unix seconds, as the core stores it. */
  createdAt: number;
  /** "auto" (Addison saved it before a risky change) or "on_command" (you did). */
  trigger: string;
  /** The closed reason slug; `reasonLabel` is the sentence to actually show. */
  reason: string;
  reasonLabel: string;
  /** True once a message was answered against this configuration. */
  verifiedWorking: boolean;
  /**
   * G4: a permanent row. Minted when a safety guard is turned off, plus the
   * very first snapshot — the bottom of the restore walk. It has no Remove
   * control anywhere in the UI, because the core refuses to delete it.
   */
  undeletable: boolean;
  /** Whether the row records which build of Addison it was saved on. */
  capturesBinary: boolean;
  /** Recorded for display only — a restore point is NEVER hidden by mode. */
  createdInMode?: "safe" | "open" | "custom";
}

/** The whole `snapshot.list` picture: the rows, what "Restore to the last
 * working state" would actually do right now, and a sticky warning when an
 * automatic restore point couldn't be saved. */
export interface SnapshotList {
  snapshots: Snapshot[];
  lastWorkingId?: string;
  lastWorkingLabel?: string;
  /** Present only when the restore would also change profile — and therefore
   * how freely Addison may act. Named before the click, never after. */
  lastWorkingProfileChange?: string;
  warning?: string;
}

/** One selectable profile, with label + description authored by the core. */
export interface ProfileOption {
  id: string;
  label: string;
  description: string;
  /**
   * True for a profile kept behind an "Advanced…" disclosure (Phase-2 step 2 —
   * only the Custom profile sets it). An advanced profile is never rendered as an
   * ordinary segmented option; it appears only once the disclosure is opened, and
   * selecting it runs a deeper two-step confirm. Absent/false on Simple and
   * Developer, whose serialized shape is unchanged.
   */
  advanced?: boolean;
}

// ---------------------------------------------------------------------------
// Guards — the two tunable prompting guards of the Custom profile (guards.get /
// guards.set; Phase-2 step 2). Each is a CLOSED vocabulary with a total
// strictness order. They change ONLY how often Addison asks before acting — they
// can never touch a global floor, so nothing here is a security boundary, only a
// prompting one. Guards are EFFECTIVE only while the active profile is Custom;
// Simple/Developer use the fixed defaults, byte-for-byte unchanged.
// ---------------------------------------------------------------------------

/** How the per-invocation destructive card behaves. `per_invocation` (the
 * default, today's OPEN behaviour) is stricter than `session`. */
export type DestructiveCardGuard = "per_invocation" | "session";

/** Which actions auto-grant without a prompt. `none` (strictest) > `non_destructive`
 * (default, today's OPEN) > `everything` (weakest — destructive auto-grants too). */
export type AutoGrantScopeGuard = "none" | "non_destructive" | "everything";

/** The `guards.get` picture: the current values, the fixed defaults, and whether
 * the guards are effective right now (`active` = the profile is Custom). */
export interface GuardsState {
  destructiveCard: DestructiveCardGuard;
  autoGrantScope: AutoGrantScopeGuard;
  defaults: {
    destructiveCard: DestructiveCardGuard;
    autoGrantScope: AutoGrantScopeGuard;
  };
  active: boolean;
}

// ---------------------------------------------------------------------------
// Routing — how Addison picks which model answers (routing.get / routing.set;
// Phase-2 step 3). The strategy is a CLOSED vocabulary; anything off-vocabulary
// is coerced to `quality_first` (the safe default — the strongest model answers)
// rather than trusted, so a garbled wire value never becomes a live strategy the
// picker then misreads. NO "balanced" (owner decision 2026-07-24 — it was
// provably identical to cost_first at small pools).
// ---------------------------------------------------------------------------

/** The four named routing strategies. `quality_first` is the default. */
export type RoutingStrategy = "quality_first" | "cost_first" | "local_only" | "custom";

/**
 * Which routing surface the person sees. `toggle` is the Simple two-option
 * control (Prefer quality / Prefer free → quality_first / cost_first); `full`
 * is the Developer/Custom picker (all four strategies) + the custom-chain
 * builder. The core keys this off the profile — the frontend just renders it.
 */
export type RoutingSurface = "toggle" | "full";

/** The `routing.get` picture: the current strategy, the strategies this surface
 * may offer, the Developer custom order (model ids, best-first), and the
 * surface. `availableStrategies` is advisory for the full picker; the toggle
 * always shows exactly its two mapped options. */
export interface RoutingState {
  strategy: RoutingStrategy;
  availableStrategies: RoutingStrategy[];
  customChain: string[];
  surface: RoutingSurface;
}

/**
 * Which model answered a turn (rides on the sendMessage reply; contract D5).
 * `free` is whether that model is a free one; `routed` is whether it differs
 * from the user's explicit pick for this message — BOTH computed by the core.
 * The free-model chip renders only when both are true.
 */
export interface AnsweredWith {
  modelId: string;
  label: string;
  free: boolean;
  routed: boolean;
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
