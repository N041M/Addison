// UI-only view types (NOT part of the hand-synced IPC contract in protocol.ts).
// These describe how the frontend holds and renders state; they never cross the
// process boundary.

import type { ChatMessage, ModelRole } from "./protocol";
import { asRecord } from "../lib/parse";

/**
 * Which in-window view is showing (docs/design-brief-dark, "Screens"). "chat" is
 * the live conversation; the other four are SURFACES — they replace the chat
 * column (the right rail hides entirely, the sidebar stays) and are left with
 * the header's ← or Escape.
 */
export type View = "chat" | "settings" | "tools" | "snapshots" | "widgets";

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
  /**
   * User + assistant turns, tool rows excluded (`store.py`'s
   * `SUM(m.role != 'tool')`). Nothing renders it yet — it is here because the
   * core SENDS it on every row and this interface said otherwise for months.
   *
   * That gap was not harmless: when `tsc` first covered the test files it
   * flagged a fixture carrying this field, and the fix was to delete it from the
   * FIXTURE, which made the type and the tests agree with each other and both
   * disagree with the wire. Optional because the type is describing a payload it
   * does not control — not because the core sometimes omits it.
   */
  messageCount?: number;
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
      ...(typeof obj.messageCount === "number" ? { messageCount: obj.messageCount } : {}),
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
  /**
   * Set when the CORE has watched this provider refuse this model — a
   * `model_gone` row in `provider_attempts`, i.e. observed rather than guessed.
   * The picker dims the row and sinks it to the end of its company; it stays
   * PICKABLE, because a refusal could have been a bad afternoon and a model that
   * quietly vanished would be a worse mystery than one visibly out of order.
   */
  unavailable?: string;
  /**
   * Whether this is a genuinely free model, as the CORE reports it. The composer
   * menu's note reads "free" only when this is true — the frontend never infers
   * it from a price, a provider name, or a "free tier" claim (CLAUDE.md, Phase-2
   * step 4: no cloud model ever claims free; the free chip stays Ollama-only).
   * `agent_core`'s CloudModel.to_wire currently omits the field, so today every
   * cloud row honestly reads "quality"; parsing it defensively means the day the
   * core does send it, the note is the core's fact rather than our guess.
   */
  free?: boolean;
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

/**
 * Why a saved routine or widget can't be used under the profile that's active
 * right now (`routine.list` / `widget.list`, key `unavailable`). Absent on
 * anything usable.
 *
 * Today there is one cause: it was made with developer abilities and the Simple
 * profile is on. Those artifacts used to vanish from the lists, which read as
 * "my work is gone"; they are listed and visibly disabled instead (owner
 * decision 2026-08-06).
 *
 * DISPLAY ONLY. Hiding a Run control is a courtesy, never the enforcement — the
 * core refuses the run itself, with this exact `message`, whatever this frontend
 * decides to render. Never treat the ABSENCE of this field as permission.
 *
 * `reason` is a machine-readable slug from an OPEN vocabulary ("developer_abilities"
 * is the only one the core sends today) — deliberately not a boolean, so a later
 * cause needs no new field. Unknown slugs are still rendered: `message` is always
 * the sentence to show.
 */
export interface ArtifactUnavailable {
  reason: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Widgets — DECLARATIVE specs mirrored from the core (agent_core/widgets.py).
// A CLOSED set of kinds: two launchers (a saved-routine Run pill, a whitelisted
// stat display), three interactive SAFE kinds (checklist, note, timer), and the
// Developer-only command widget. NEVER code. The frontend renders these; it
// never constructs or evaluates one, and a kind it does not know is DROPPED.
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

/**
 * The three INTERACTIVE SAFE kinds (Phase-2 step 6, half A —
 * agent_core/widgets.py). They invoke no tool and reach nothing outside
 * Addison, which is why they are available in the Simple profile.
 *
 * A spec is what was DECLARED and never changes: a checklist's items are FIXED
 * at creation (v1 has no item editing, exactly as it has no routine step
 * editing — you delete and recreate by asking), and a note's `text` is only its
 * INITIAL text. What the person has since done lives in `WidgetState` below.
 */
export interface ChecklistWidgetSpec {
  kind: "checklist";
  items: string[];
  title: string;
}

export interface NoteWidgetSpec {
  kind: "note";
  /** The note's initial text. The CURRENT text is in the widget's state. */
  text: string;
  title: string;
}

export interface TimerWidgetSpec {
  kind: "timer";
  /** How long the timer runs for, in seconds. */
  seconds: number;
  title: string;
}

export type WidgetSpec =
  | RoutineWidgetSpec
  | StatWidgetSpec
  | ChecklistWidgetSpec
  | NoteWidgetSpec
  | TimerWidgetSpec
  | CommandWidgetSpec;

/**
 * What the person has done with an interactive widget, from `widget.list` and
 * written back with `widget.setState`. Kept apart from the spec on both sides of
 * the wire (the core stores it in its own table): the spec is the declaration,
 * this is the doing.
 *
 * `checked` maps to the spec's items BY POSITION and is exactly as long — the
 * core drops a state whose length disagrees rather than guess which row moved,
 * so a mismatch renders as an untouched list, never as the wrong box ticked.
 *
 * The timer holds start/duration/paused only. NOTHING counts it down but this
 * frontend, and nothing anywhere fires at zero — Addison never triggers itself.
 * `remaining` is the seconds left AT `startedAt` when running, or simply the
 * seconds left when paused.
 */
export type WidgetState =
  | { checked: boolean[] }
  | { text: string }
  | { running: boolean; remaining: number; startedAt: number | null };

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
   * Present only on an interactive kind that has stored state the core still
   * considers valid for its spec. Absent means "draw the declaration" — an
   * un-ticked list, the note's initial text, a full paused timer.
   */
  state?: WidgetState;
  /**
   * The policy mode the widget was saved under ("safe" | "open"), when the core
   * forwards it. Drives the Developer-profile "DEV" annotation tag. A command
   * widget is inherently OPEN-created even when this is absent.
   */
  createdInMode?: "safe" | "open";
  /**
   * Present when the active profile can't use this widget — the rail renders it
   * as a disabled row carrying `message`, with no Run control. Display only (see
   * ArtifactUnavailable): the core refuses `widget.run` on its own terms.
   */
  unavailable?: ArtifactUnavailable;
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

// ---------------------------------------------------------------------------
// Add-a-model-server by prompt + "make it cheaper" (Phase-2 step 4). Both are
// CORE-derived (the model authors no actionable field on the turn reply): the
// core drafts a proposal from the conversation and holds it; a separate confirm
// applies it, mirroring the widget precedent. Nothing here is secret — the
// endpoint API key NEVER rides in one of these shapes; it goes straight to the
// OS keychain via `storeProviderKey` (G1). Both parsers fail CLOSED: a shape the
// card can't act on becomes `null`, so no card is shown.
// ---------------------------------------------------------------------------

/**
 * A drafted "add this model server" proposal from `endpoint.proposeFromConversation`.
 * `baseUrl` is the full address the core extracted from a short, add-endpoint-shaped
 * user utterance (never assistant content, never a URL buried in pasted page text).
 * `isLocalOrLan` is true when the address resolves to the user's own computer or a
 * device on their network — the card discloses that in plain language.
 */
export interface EndpointProposal {
  baseUrl: string;
  isLocalOrLan: boolean;
}

/**
 * A drafted "switch to cheaper models" plan from `costPlan.propose`. ALL fields are
 * CANNED in the core (the model authors none): `skillName` + `skillInstructions`
 * are fixed constants (a brevity + prefer-cheaper guidance note), and `strategy`
 * is always `cost_first`. The card renders the name AND the full instructions text
 * so the person sees exactly what note is being added before confirming.
 */
export interface CostPlan {
  skillName: string;
  skillInstructions: string;
  strategy: "cost_first";
}

// ---------------------------------------------------------------------------
// Workspace trust — the coding-harness trust boundary (workspace.list; Phase-2
// step 5). A trusted ROOT is a folder Addison may read and edit inside without a
// per-change card; the typed file tools stay undoable and every change is logged,
// and commands Addison runs still ask every time. Nothing here is secret — no key,
// no file contents — only the folder path and when it was trusted, so the parser
// only has to worry about shape. It fails CLOSED: a row without a usable directory
// string is DROPPED, never rendered, because a row the card can't name is a row it
// could offer a "Stop trusting" button for and then fail to act on.
// ---------------------------------------------------------------------------
export interface WorkspaceRoot {
  /** The absolute, canonicalized folder path the core is currently trusting. */
  directory: string;
  /** Unix seconds when trust was granted, when the core reports it. */
  grantedAt?: number;
}

// ---------------------------------------------------------------------------
// MCP servers — the external tool servers Addison consumes as a CLIENT
// (mcp.list / mcp.refresh; Phase-2 step 7, phases 1–2 of five). A row is a saved
// address PLUS whatever the last check found. What it is NOT is a capability:
// Addison can list a server's tools and cannot run one, so every surface that
// renders this says so in its own words rather than letting a tool count imply
// otherwise. Nothing secret rides the payload — the address is refused core-side
// if it carries a sign-in name, password or key, and no token is stored anywhere
// (a server that wants one gets a plain sentence). The parser fails CLOSED: a row
// without a usable id and name is DROPPED rather than rendered with a Remove
// button it can't act on.
// ---------------------------------------------------------------------------

/**
 * Where a server stands.
 *
 * - `never` — nobody has checked it. The honest state for a new row AND after a
 *   restart: the discovered catalog lives in the core's memory and is never
 *   persisted, because a catalog is the server's truth, not Addison's config.
 * - `checking` — a refresh is in flight. **Set by the frontend, never by the
 *   core**: `mcp.list` and `mcp.refresh` answer on the same worker thread, so a
 *   list request queues behind the refresh and could not observe it.
 * - `ok` / `failed` — the last check landed, or didn't (`error` says why).
 */
export type McpServerStatus = "never" | "checking" | "ok" | "failed";

/** One tool a server offered. Name and description only, both already cleaned
 * and length-capped in the core — and both are still a STRANGER'S TEXT, so they
 * are rendered as plain text through React's own escaping and never as markdown. */
export interface McpDiscoveredTool {
  name: string;
  description: string;
}

export interface McpServer {
  /** The core's row id — what `mcp.remove` and `mcp.refresh` take. */
  id: string;
  /** The plain name the person gave this server. */
  name: string;
  /** Its http(s) address. Never a command; nothing here starts a program. */
  url: string;
  /** Whether Addison would consume it once there is something to consume. */
  enabled: boolean;
  /** Unix seconds when it was added, when the core reports it. */
  addedAt?: number;
  /** Where the last check got to. Defaults to `never` on anything unrecognised. */
  status: McpServerStatus;
  /** Unix seconds of the last check. Absent until there has been one. */
  checkedAt?: number;
  /** How many tools that check found. Absent unless the check succeeded. */
  toolCount?: number;
  /** What it found. Empty for every state but `ok`. */
  tools: McpDiscoveredTool[];
  /** How many the server offered that Addison would not take — an odd name, a
   * name already in use, or past its cap. A count, never the names. */
  skipped?: number;
  /** Why the last check failed, as one plain sentence from the core. Never the
   * server's own words. */
  error?: string;
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
