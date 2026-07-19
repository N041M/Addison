// Widget rail — the right column of the Fern app shell (design-brief-fern README
// §3, handoff §4). 270px, `paper` background, its own thin-scrollbar scroll.
// Hideable via the chat header's rail toggle.
//
// Order (handoff): YOUR WIDGETS header (+ Edit) → widget cards (pinned stored
// widgets, then the implicit token meter + connections cards) → overflow tray
// (unpinned stored widgets) → "＋ Ask Addison to build a widget" → Addison's
// work → consent card.
//
// SAFETY: widgets are DECLARATIVE specs only (agent_core/widgets.py). A routine
// widget runs a saved routine through the EXISTING run path (its own gates apply
// at run time); a stat widget DISPLAYS a core-computed value. There is no code,
// expression, or template anywhere here — the rail only renders specs and calls
// the typed ipc for actions.

import { useState, type ReactNode } from "react";
import type { WidgetRunResult } from "../ipc/client";
import type { Widget, Stats, WidgetStatSource } from "../types/ui";

const TRAY_OPEN_KEY = "addison.trayOpen";

/** Minimal routine info the rail needs to run a routine widget (variable prompts). */
export interface RailRoutine {
  id: string;
  name: string;
  variables: { name: string; prompt: string; default: string | null }[];
  /** The mode the routine was saved under ("safe" | "open"), when the core sends it. */
  createdInMode?: "safe" | "open";
}

interface Props {
  /** The "Addison's work" annotation block (ActivityPanel), when there's work. */
  work?: ReactNode;
  /** The consent card (PermissionCard), when a permission is pending. */
  consent?: ReactNode;
  /**
   * "rail" is the desktop right column (270px, own scroll). "sheet" is the
   * narrow-window bottom sheet (BottomSheet supplies the chrome + drag handle):
   * the same content flows to fill the sheet and scroll within it. On mobile
   * consent renders inline in the thread, so `consent` is not passed in sheet
   * mode — the sheet shows widgets + Addison's work only.
   */
  variant?: "rail" | "sheet" | "inline";
  /**
   * OPEN/Developer mode is active — surface the small blocky "DEV" annotation on
   * dev-created items (command widgets, and any widget/routine whose
   * createdInMode is "open" — the core forwards it on both list responses). In
   * Simple mode these items are already filtered out by the core, so this stays
   * false.
   */
  developer?: boolean;
  /** Stored widgets from `widget.list` (routine/stat/command specs). */
  widgets: Widget[];
  /** Core-computed stats for the token meter + connections cards. */
  stats: Stats | null;
  /** Saved routines (for a routine widget's variable prompts). */
  routines: RailRoutine[];
  onSetPinned: (id: string, pinned: boolean) => void;
  onDelete: (id: string) => void;
  onRunRoutine: (routineId: string, variables: Record<string, string>) => Promise<RunOutcome>;
  /** widget.run for a command widget (Developer profile) — the core re-checks
   * the mode and gates the command per invocation; never executed client-side. */
  onRunCommandWidget: (id: string) => Promise<WidgetRunResult>;
  onAskBuildWidget: () => void;
}

export interface RunOutcome {
  ok: boolean;
  detail: string;
}

export function WidgetRail({
  work,
  consent,
  variant = "rail",
  developer = false,
  widgets,
  stats,
  routines,
  onSetPinned,
  onDelete,
  onRunRoutine,
  onRunCommandWidget,
  onAskBuildWidget,
}: Props) {
  const [editMode, setEditMode] = useState(false);
  const [trayOpen, setTrayOpen] = useState<boolean>(loadTrayOpen);

  const pinned = widgets.filter((w) => w.pinned);
  const unpinned = widgets.filter((w) => !w.pinned);

  const hasUsage = (stats?.tokensMonth.total ?? 0) > 0;
  const isSheet = variant === "sheet";
  // Inline: flows in the chat thread's own scroll on mobile (no fixed width, no
  // nested scroll container) so widgets are simply visible on the chat screen.
  const isInline = variant === "inline";

  function toggleTray() {
    setTrayOpen((v) => {
      const next = !v;
      saveTrayOpen(next);
      return next;
    });
  }

  return (
    <aside
      aria-label="Your widgets"
      className={
        "flex flex-col gap-[22px] " +
        (isSheet
          ? "thread-scroll min-h-0 flex-1 overflow-y-auto pb-4"
          : isInline
            ? "w-full pt-1"
            : "thread-scroll w-[270px] shrink-0 overflow-y-auto py-[30px]")
      }
    >
      <div>
        <div className="mb-2.5 flex items-baseline justify-between">
          <p className="text-label font-semibold uppercase tracking-caps-wider text-faint">
            Your widgets
          </p>
          <button
            type="button"
            onClick={() => setEditMode((v) => !v)}
            className="text-fine font-medium text-muted hover:text-ink-soft"
          >
            {editMode ? "Done" : "Edit"}
          </button>
        </div>

        <div className="flex flex-col gap-2">
          {/* Pinned stored widgets. */}
          {pinned.map((w) => (
            <WidgetCard
              key={w.id}
              widget={w}
              stats={stats}
              routines={routines}
              editMode={editMode}
              developer={developer}
              onSetPinned={onSetPinned}
              onDelete={onDelete}
              onRunRoutine={onRunRoutine}
              onRunCommandWidget={onRunCommandWidget}
            />
          ))}

          {/* Implicit, core-provided cards: the token meter (once any usage exists)
              and the connections card (always). These are NOT stored widgets. */}
          {hasUsage && <TokenMeter stats={stats} />}
          <ConnectionsCard stats={stats} />

          {/* Overflow tray: unpinned stored widgets behind a stacked-edge tab. */}
          {unpinned.length > 0 && (
            <>
              {trayOpen &&
                unpinned.map((w) => (
                  <WidgetCard
                    key={w.id}
                    widget={w}
                    stats={stats}
                    routines={routines}
                    editMode={editMode}
                    developer={developer}
                    inTray
                    onSetPinned={onSetPinned}
                    onDelete={onDelete}
                    onRunRoutine={onRunRoutine}
                    onRunCommandWidget={onRunCommandWidget}
                  />
                ))}
              <div className="relative mt-0.5">
                {!trayOpen && (
                  <>
                    {/* The two offset strips peeking above the tab (prototype §9a). */}
                    <div className="absolute left-2.5 right-2.5 -top-1 h-2 rounded-t-[8px] border border-line bg-surface" />
                    <div className="absolute left-1.5 right-1.5 -top-2 h-2 rounded-t-[8px] border border-line bg-hair" />
                  </>
                )}
                <button
                  type="button"
                  onClick={toggleTray}
                  aria-expanded={trayOpen}
                  className="relative flex w-full items-center justify-between rounded-card border border-line bg-surface px-[13px] py-[9px] hover:opacity-85 max-md:min-h-[44px]"
                >
                  <span className="text-meta font-semibold text-fern-deep">
                    {trayOpen ? "Show fewer" : `${unpinned.length} more widget${unpinned.length === 1 ? "" : "s"}`}
                  </span>
                  <span className="text-tick text-faint">{trayOpen ? "▴" : "▾"}</span>
                </button>
              </div>
            </>
          )}

          <button
            type="button"
            onClick={onAskBuildWidget}
            className="rounded-card border border-dashed border-dash bg-transparent px-2.5 py-2 text-center text-fine font-medium text-muted hover:opacity-80 max-md:min-h-[44px] max-md:text-meta"
          >
            ＋ Ask Addison to build a widget
          </button>
        </div>
      </div>

      {(work || consent) && (
        <div>
          {work}
          {consent && <div className="mt-3.5">{consent}</div>}
        </div>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// One stored widget card — a routine Run pill or a stat display.
// ---------------------------------------------------------------------------
interface CardProps {
  widget: Widget;
  stats: Stats | null;
  routines: RailRoutine[];
  editMode: boolean;
  developer?: boolean;
  inTray?: boolean;
  onSetPinned: (id: string, pinned: boolean) => void;
  onDelete: (id: string) => void;
  onRunRoutine: (routineId: string, variables: Record<string, string>) => Promise<RunOutcome>;
  onRunCommandWidget: (id: string) => Promise<WidgetRunResult>;
}

function WidgetCard({
  widget,
  stats,
  routines,
  editMode,
  developer = false,
  inTray = false,
  onSetPinned,
  onDelete,
  onRunRoutine,
  onRunCommandWidget,
}: CardProps) {
  const spec = widget.spec;
  const routine = spec.kind === "routine" ? routines.find((r) => r.id === spec.routineId) : undefined;
  // A dev-created item: a command widget (inherently OPEN), or anything the core
  // marked created_in_mode="open" (the widget itself, or the routine it runs).
  const isDev =
    spec.kind === "command" ||
    widget.createdInMode === "open" ||
    routine?.createdInMode === "open";
  return (
    <div className="rounded-card border border-line bg-surface px-[13px] py-[11px]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {developer && isDev && <DevTag />}
          {spec.kind === "routine" ? (
            <RoutineWidgetBody
              title={spec.title}
              routine={routine}
              routineId={spec.routineId}
              onRunRoutine={onRunRoutine}
            />
          ) : spec.kind === "command" ? (
            <CommandWidgetBody
              title={spec.title}
              command={spec.command}
              onRun={() => onRunCommandWidget(widget.id)}
            />
          ) : (
            <StatWidgetBody title={spec.title} source={spec.source} stats={stats} />
          )}
        </div>
        {/* Edit affordances: pin toggle (⬤/◯) + remove ✕. In the tray, the pin
            toggle is always shown so an unpinned widget can be re-pinned. */}
        {(editMode || inTray) && (
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              title={widget.pinned ? "Unpin" : "Pin"}
              onClick={() => onSetPinned(widget.id, !widget.pinned)}
              className="text-control leading-none text-muted hover:text-fern-deep"
            >
              {widget.pinned ? "⬤" : "◯"}
            </button>
            {editMode && (
              <button
                type="button"
                title="Remove widget"
                onClick={() => onDelete(widget.id)}
                className="text-control leading-none text-faint hover:text-danger"
              >
                ✕
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// A routine widget: name + fern-tint Run pill. When the routine has variables
// without defaults, a compact inline prompt collects them first (§6.5), exactly
// like the Routines library.
function RoutineWidgetBody({
  title,
  routine,
  routineId,
  onRunRoutine,
}: {
  title: string;
  routine?: RailRoutine;
  routineId: string;
  onRunRoutine: (routineId: string, variables: Record<string, string>) => Promise<RunOutcome>;
}) {
  const [filling, setFilling] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);

  const needsInput = (routine?.variables ?? []).filter((v) => !v.default);

  async function run() {
    if (needsInput.length > 0 && !filling) {
      const prefill: Record<string, string> = {};
      for (const v of routine?.variables ?? []) if (v.default) prefill[v.name] = v.default;
      setValues(prefill);
      setFilling(true);
      return;
    }
    setFilling(false);
    setRunning(true);
    setOutcome(null);
    try {
      const res = await onRunRoutine(routineId, values);
      setOutcome(res);
    } finally {
      setRunning(false);
      setValues({});
    }
  }

  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-meta font-semibold text-ink">{title}</span>
        <button
          type="button"
          disabled={running}
          onClick={() => void run()}
          className="shrink-0 rounded-pill bg-fern-tint px-3 py-1 text-fact font-semibold text-fern-deep hover:opacity-85 disabled:opacity-60 max-md:min-h-[44px] max-md:px-5 max-md:text-hint"
        >
          {running ? "Running…" : "Run"}
        </button>
      </div>

      {filling && (
        <div className="mt-2.5 rounded border border-line bg-paper p-2.5">
          {needsInput.map((v) => (
            <label key={v.name} className="mb-2 block text-hint font-medium text-ink-soft">
              {v.prompt}
              <input
                type="text"
                value={values[v.name] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [v.name]: e.target.value }))}
                className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 text-control text-ink"
              />
            </label>
          ))}
          <div className="mt-1 flex gap-2">
            <button
              type="button"
              onClick={() => void run()}
              className="rounded-sm bg-fern px-3 py-1 text-hint font-semibold text-on-accent hover:bg-fern-deep"
            >
              Start
            </button>
            <button
              type="button"
              onClick={() => setFilling(false)}
              className="rounded-sm border border-line bg-surface px-3 py-1 text-hint font-medium text-ink-soft"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {outcome && (
        <p className={"mt-2 text-fine " + (outcome.ok ? "text-fern-deep" : "text-ink-soft")}>
          {outcome.detail}
        </p>
      )}
    </>
  );
}

// A command widget (OPEN/Developer mode): title + the command shown as a machine
// fact (mono) under it. Run goes through the core's widget.run — the SAME
// registry + gate path as a routine command step, so a destructive command
// raises its per-invocation card before anything executes. The command is never
// executed client-side; this component only displays it and shows the outcome.
function CommandWidgetBody({
  title,
  command,
  onRun,
}: {
  title: string;
  command: string;
  onRun: () => Promise<WidgetRunResult>;
}) {
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; detail: string } | null>(null);

  function handleRun() {
    setRunning(true);
    setOutcome(null);
    onRun()
      .then((res) => {
        // First output line only — the rail is a glance surface, not a terminal.
        const firstLine = (res.output ?? "").split("\n", 1)[0].trim();
        setOutcome(
          res.ok
            ? { ok: true, detail: firstLine || "Done." }
            : { ok: false, detail: res.error || "That didn't work." },
        );
      })
      .catch(() => setOutcome({ ok: false, detail: "That didn't work." }))
      .finally(() => setRunning(false));
  }

  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-meta font-semibold text-ink">{title}</span>
        <button
          type="button"
          disabled={running}
          onClick={handleRun}
          className="shrink-0 rounded-pill bg-fern-tint px-3 py-1 text-fact font-semibold text-fern-deep hover:bg-rule disabled:opacity-45"
        >
          {running ? "Running…" : "Run"}
        </button>
      </div>
      <p
        title={command}
        className="mt-1.5 truncate rounded-sm bg-paper px-2 py-1 font-mono text-fine text-ink-soft"
      >
        {command}
      </p>
      {outcome && (
        <p
          title={outcome.detail}
          className={
            "mt-1.5 truncate font-mono text-fact " +
            (outcome.ok ? "text-fern-deep" : "text-danger")
          }
        >
          {outcome.detail}
        </p>
      )}
    </>
  );
}

// The blocky "DEV" annotation (design-brief-fern shape rule: blocky = a live
// annotation Addison is showing you). Square edges, 2px left fern rule, small-
// caps — marks an item created with developer abilities. Shown in Developer
// profile only; Simple never sees these items at all (core-filtered).
function DevTag() {
  return (
    <span className="mb-1 inline-block border-l-2 border-fern pl-1.5 text-tag font-semibold uppercase tracking-caps-wide text-fern-deep">
      Dev
    </span>
  );
}

// A stat widget renders one of the three core-computed sources with the widget's
// own title. The bodies are shared with the implicit token-meter / connections
// cards below.
function StatWidgetBody({
  title,
  source,
  stats,
}: {
  title: string;
  source: WidgetStatSource;
  stats: Stats | null;
}) {
  if (source === "tokens_month") return <TokenMeterBody title={title} stats={stats} />;
  if (source === "connections") return <ConnectionsBody title={title} stats={stats} />;
  return <LatencyBody title={title} stats={stats} />;
}

// ---------------------------------------------------------------------------
// Stat bodies (shared by stored stat widgets + the implicit cards).
// ---------------------------------------------------------------------------
function TokenMeter({ stats }: { stats: Stats | null }) {
  return (
    <div className="rounded-card border border-line bg-surface px-[13px] py-[11px]">
      <TokenMeterBody title={`Tokens · ${currentMonthLabel()}`} stats={stats} />
    </div>
  );
}

function TokenMeterBody({ title, stats }: { title: string; stats: Stats | null }) {
  const total = stats?.tokensMonth.total ?? 0;
  const limit = stats?.tokensMonth.limit ?? null;
  const value = limit != null ? `${formatTokens(total)} / ${formatTokens(limit)}` : formatTokens(total);
  const pct = limit ? Math.max(0, Math.min(100, Math.round((total / limit) * 100))) : null;
  return (
    <>
      <div className="flex items-baseline justify-between">
        <SmallCaps>{title}</SmallCaps>
        <span className="font-mono text-fine text-ink-soft">{value}</span>
      </div>
      {/* The 5px fern-on-hair progress bar renders ONLY when a limit exists. */}
      {pct != null && (
        <div className="mt-2 h-[5px] overflow-hidden rounded-pill bg-hair">
          <div className="h-full rounded-pill bg-fern" style={{ width: `${pct}%` }} />
        </div>
      )}
    </>
  );
}

// The implicit connections card carries no title of its own (final design
// screenshots 2026-07): the dot rows are self-explanatory. A user-saved stat
// widget still shows its own title via ConnectionsBody's `title`.
function ConnectionsCard({ stats }: { stats: Stats | null }) {
  return (
    <div className="rounded-card border border-line bg-surface px-[13px] py-[11px]">
      <ConnectionsBody stats={stats} />
    </div>
  );
}

function ConnectionsBody({ title, stats }: { title?: string; stats: Stats | null }) {
  const rows = stats?.connections ?? [];
  return (
    <div className="flex flex-col gap-[5px]">
      {title && <SmallCaps className="mb-0.5">{title}</SmallCaps>}
      {rows.length === 0 && <p className="text-fine text-faint">Nothing connected yet.</p>}
      {rows.map((c) => (
        <div key={c.id} className="flex items-center gap-[7px]">
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-pill"
            style={{ backgroundColor: dotColor(c.status) }}
          />
          <span className="min-w-0 truncate text-hint text-ink">{c.label}</span>
          <span className="ml-auto shrink-0 font-mono text-tick text-faint">{c.detail}</span>
        </div>
      ))}
    </div>
  );
}

function LatencyBody({ title, stats }: { title: string; stats: Stats | null }) {
  const rows = stats?.providerLatency ?? [];
  return (
    <div className="flex flex-col gap-[5px]">
      <SmallCaps className="mb-0.5">{title}</SmallCaps>
      {rows.length === 0 && <p className="text-fine text-faint">No calls yet.</p>}
      {rows.map((r) => (
        <div key={r.provider} className="flex items-center gap-[7px]">
          <span className="min-w-0 truncate text-hint text-ink">{r.provider}</span>
          <span className="ml-auto shrink-0 font-mono text-tick text-faint">{r.ms} ms</span>
        </div>
      ))}
    </div>
  );
}

function SmallCaps({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={
        "text-fact font-semibold uppercase tracking-caps text-faint " + className
      }
    >
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------
// fern (running/reachable) · dash-gray (idle) · danger (unreachable). Uses the
// CSS custom-property channels so it flips with the theme like every token.
function dotColor(status: string): string {
  if (status === "running" || status === "reachable") return "rgb(var(--c-fern))";
  if (status === "unreachable") return "rgb(var(--c-danger))";
  return "rgb(var(--c-dash))"; // idle
}

// 412000 → "412k", 1_500_000 → "1.5M". Compact machine-fact formatting.
function formatTokens(n: number): string {
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return (m >= 10 || Number.isInteger(m) ? Math.round(m) : Number(m.toFixed(1))) + "M";
  }
  if (n >= 1_000) return Math.round(n / 1_000) + "k";
  return String(n);
}

function currentMonthLabel(): string {
  return new Date().toLocaleDateString(undefined, { month: "long" });
}

function loadTrayOpen(): boolean {
  try {
    return localStorage.getItem(TRAY_OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function saveTrayOpen(open: boolean): void {
  try {
    localStorage.setItem(TRAY_OPEN_KEY, open ? "1" : "0");
  } catch {
    /* non-fatal */
  }
}
