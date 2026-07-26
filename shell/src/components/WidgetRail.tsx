// Widget rail — the right column (DARK direction; docs/design-brief-dark,
// "Right rail"). 232px, no background of its own, its own hidden-scrollbar
// scroll. Top to bottom: the "Addison's work" block during and after a task,
// then hairline-separated rows — the token meter, the stored widgets, whatever
// connections the core reports — and a footer row that opens the Build-a-widget
// surface.
//
// The Fern cards are gone: a widget is a ROW now (name — spacer — mono value, or
// name — accent action), separated by 1px `line` top borders, which is the same
// row anatomy the sidebar and the surfaces use.
//
// SAFETY: widgets are DECLARATIVE specs only (agent_core/widgets.py). A routine
// widget runs a saved routine through the EXISTING run path (its own gates apply
// at run time); a stat widget DISPLAYS a core-computed value; a command widget's
// Run goes to the core's widget.run, which re-checks the mode and raises the
// per-invocation card before anything executes. There is no code, expression, or
// template anywhere here — the rail only renders specs and calls the typed ipc.

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
   * "rail" is the desktop right column (232px, own scroll). "inline" flows in
   * the chat thread's own scroll on the narrow-window layout, at the foot of
   * the messages — there is no side column there to hold it.
   */
  variant?: "rail" | "inline";
  /**
   * OPEN/Developer mode is active — surface the small "dev" annotation on
   * dev-created items (command widgets, and any widget/routine whose
   * createdInMode is "open"). In Simple mode these items are already filtered
   * out by the core, so this stays false.
   */
  developer?: boolean;
  /** Stored widgets from `widget.list` (routine/stat/command specs). */
  widgets: Widget[];
  /** Core-computed stats for the token meter + connection rows. */
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

/** One hairline-separated rail row. Everything below is built out of this. */
function Row({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={"shrink-0 border-t border-line px-0.5 py-[13px] text-[12px] " + className}>
      {children}
    </div>
  );
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
  const connections = stats?.connections ?? [];
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
        "flex flex-col " +
        (isInline
          ? "w-full pt-2"
          : "no-scrollbar h-full w-[232px] shrink-0 overflow-y-auto pb-5 pt-9")
      }
    >
      {/* The live annotation and the consent card ride at the top of the rail,
          above the ambient rows — a pending question is never below the fold of
          a widget list. */}
      {work}
      {consent && <div className="shrink-0 pb-5">{consent}</div>}

      {/* Pinned stored widgets. */}
      {pinned.map((w) => (
        <WidgetRow
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

      {/* Core-provided, not stored widgets: the token meter (once any usage
          exists) and the connection rows (when the core reports any). Neither
          is ever shown with an invented number. */}
      {hasUsage && <TokenMeterRow stats={stats} />}
      {connections.map((c) => (
        <Row key={c.id}>
          <div className="flex items-baseline gap-2">
            <span
              aria-hidden="true"
              className={
                "h-[5px] w-[5px] shrink-0 translate-y-[-2px] rounded-full " + dotClass(c.status)
              }
            />
            <span className="min-w-0 truncate text-ink-soft">{c.label}</span>
            <span className="flex-1" />
            <span className="shrink-0 font-mono text-[10px] text-muted">{c.detail}</span>
          </div>
        </Row>
      ))}

      {/* Overflow: unpinned stored widgets behind one row. */}
      {unpinned.length > 0 && (
        <>
          {trayOpen &&
            unpinned.map((w) => (
              <WidgetRow
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
          <Row>
            <button
              type="button"
              onClick={toggleTray}
              aria-expanded={trayOpen}
              className="flex w-full items-baseline gap-2 text-left text-[12px] text-muted transition-colors hover:text-ink max-md:min-h-[44px]"
            >
              <span className="min-w-0 truncate">
                {trayOpen
                  ? "Show fewer"
                  : `${unpinned.length} more widget${unpinned.length === 1 ? "" : "s"}`}
              </span>
              <span className="flex-1" />
              <span aria-hidden="true" className="font-mono text-[10px] text-disabled">
                {trayOpen ? "▴" : "▾"}
              </span>
            </button>
          </Row>
        </>
      )}

      {/* Footer: the way to ask for a new widget, and — only when there is
          something to edit — the way to unpin or remove one. */}
      <Row>
        <div className="flex items-baseline gap-2">
          <button
            type="button"
            data-scramble="900"
            onClick={onAskBuildWidget}
            className="text-left text-[12px] text-disabled transition-colors hover:text-muted max-md:min-h-[44px]"
          >
            ＋ Ask Addison to build a widget
          </button>
          <span className="flex-1" />
          {widgets.length > 0 && (
            <button
              type="button"
              onClick={() => setEditMode((v) => !v)}
              className="shrink-0 font-mono text-[10px] text-disabled transition-colors hover:text-muted max-md:min-h-[44px]"
            >
              {editMode ? "done" : "edit"}
            </button>
          )}
        </div>
      </Row>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// One stored widget, as a row.
// ---------------------------------------------------------------------------
interface WidgetRowProps {
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

function WidgetRow({
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
}: WidgetRowProps) {
  const spec = widget.spec;
  const routine =
    spec.kind === "routine" ? routines.find((r) => r.id === spec.routineId) : undefined;
  // A dev-created item: a command widget (inherently OPEN), or anything the core
  // marked created_in_mode="open" (the widget itself, or the routine it runs).
  const isDev =
    spec.kind === "command" || widget.createdInMode === "open" || routine?.createdInMode === "open";

  return (
    <Row>
      {developer && isDev && <DevTag />}
      <div className="flex items-baseline gap-2">
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
        {/* Edit affordances: pin toggle (⬤/◯) + remove ✕. In the tray, the pin
            toggle is always shown so an unpinned widget can be re-pinned. */}
        {(editMode || inTray) && (
          <span className="flex shrink-0 items-center gap-2 pl-1">
            <button
              type="button"
              title={widget.pinned ? "Unpin" : "Pin"}
              onClick={() => onSetPinned(widget.id, !widget.pinned)}
              className="text-[10px] leading-none text-disabled transition-colors hover:text-ink"
            >
              {widget.pinned ? "⬤" : "◯"}
            </button>
            {editMode && (
              <button
                type="button"
                title="Remove widget"
                onClick={() => onDelete(widget.id)}
                className="text-[11px] leading-none text-disabled transition-colors hover:text-danger"
              >
                ✕
              </button>
            )}
          </span>
        )}
      </div>
    </Row>
  );
}

// A routine widget: name — accent Run. When the routine has variables without
// defaults, a compact inline prompt collects them first (§6.5), exactly like the
// Routines library.
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
    <span className="min-w-0 flex-1">
      <span className="flex items-baseline gap-2">
        <span className="min-w-0 truncate text-ink-soft">{title}</span>
        <span className="flex-1" />
        <button
          type="button"
          disabled={running}
          onClick={() => void run()}
          className="shrink-0 text-[12px] text-accent transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-disabled max-md:min-h-[44px]"
        >
          {running ? "Running…" : "Run"}
        </button>
      </span>

      {filling && (
        <span className="mt-2.5 block rounded-[6px] border border-line bg-panel p-2.5">
          {needsInput.map((v) => (
            <label key={v.name} className="mb-2 block text-[11px] text-ink-soft">
              {v.prompt}
              <input
                type="text"
                value={values[v.name] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [v.name]: e.target.value }))}
                className="mt-1 w-full rounded-[5px] border border-line bg-paper px-2 py-1.5 text-[12px] text-ink outline-none focus:border-track-hi"
              />
            </label>
          ))}
          <span className="mt-1 flex gap-4">
            <button
              type="button"
              onClick={() => void run()}
              className="text-[12px] text-accent transition-colors hover:text-ink"
            >
              Start
            </button>
            <button
              type="button"
              onClick={() => setFilling(false)}
              className="text-[12px] text-muted transition-colors hover:text-ink"
            >
              Cancel
            </button>
          </span>
        </span>
      )}

      {outcome && (
        <span className={"mt-1.5 block text-[11px] " + (outcome.ok ? "text-muted" : "text-ink-soft")}>
          {outcome.detail}
        </span>
      )}
    </span>
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
    <span className="min-w-0 flex-1">
      <span className="flex items-baseline gap-2">
        <span className="min-w-0 truncate text-ink-soft">{title}</span>
        <span className="flex-1" />
        <button
          type="button"
          disabled={running}
          onClick={handleRun}
          className="shrink-0 text-[12px] text-accent transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-disabled max-md:min-h-[44px]"
        >
          {running ? "Running…" : "Run"}
        </button>
      </span>
      <span title={command} className="mt-1.5 block truncate font-mono text-[10px] text-muted">
        {command}
      </span>
      {outcome && (
        <span
          title={outcome.detail}
          className={
            "mt-1 block truncate font-mono text-[10px] " +
            (outcome.ok ? "text-muted" : "text-danger")
          }
        >
          {outcome.detail}
        </span>
      )}
    </span>
  );
}

// The "dev" annotation — marks an item created with developer abilities. Shown
// in the Developer/Custom profile only; Simple never sees these items at all
// (core-filtered). A machine fact about the item, so mono.
function DevTag() {
  return (
    <span className="mb-1 block font-mono text-[10px] tracking-[.04em] text-disabled">dev</span>
  );
}

// A stat widget renders one of the three core-computed sources with the widget's
// own title.
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
// Stat bodies (shared by stored stat widgets + the implicit token meter).
// ---------------------------------------------------------------------------
function TokenMeterRow({ stats }: { stats: Stats | null }) {
  return (
    <Row>
      <TokenMeterBody title="Tokens this month" stats={stats} scramble="780" />
    </Row>
  );
}

function TokenMeterBody({
  title,
  stats,
  scramble,
}: {
  title: string;
  stats: Stats | null;
  scramble?: string;
}) {
  const total = stats?.tokensMonth.total ?? 0;
  const limit = stats?.tokensMonth.limit ?? null;
  const value =
    limit != null ? `${formatTokens(total)} / ${formatTokens(limit)}` : formatTokens(total);
  const pct = limit ? Math.max(0, Math.min(100, Math.round((total / limit) * 100))) : null;
  return (
    <span className="flex min-w-0 flex-1 flex-col gap-[9px]">
      <span className="flex items-baseline justify-between gap-2 text-muted">
        <span data-scramble={scramble} className="min-w-0 truncate">
          {title}
        </span>
        <span className="shrink-0 font-mono text-[11px]">{value}</span>
      </span>
      {/* The bar renders ONLY when the core reports a limit: a fill needs a real
          denominator, and drawing one against a guessed ceiling would be a
          fabricated number in a place people read as a budget. */}
      {pct != null && (
        <span className="block h-[2px] w-full bg-track">
          <span
            className="block h-[2px] bg-ink transition-[width] duration-[400ms]"
            style={{ width: `${pct}%` }}
          />
        </span>
      )}
    </span>
  );
}

function ConnectionsBody({ title, stats }: { title?: string; stats: Stats | null }) {
  const rows = stats?.connections ?? [];
  return (
    <span className="flex min-w-0 flex-1 flex-col gap-[7px]">
      {title && <span className="text-muted">{title}</span>}
      {rows.length === 0 && <span className="text-[11px] text-disabled">Nothing connected yet.</span>}
      {rows.map((c) => (
        <span key={c.id} className="flex items-baseline gap-2">
          <span
            aria-hidden="true"
            className={
              "h-[5px] w-[5px] shrink-0 translate-y-[-2px] rounded-full " + dotClass(c.status)
            }
          />
          <span className="min-w-0 truncate text-ink-soft">{c.label}</span>
          <span className="flex-1" />
          <span className="shrink-0 font-mono text-[10px] text-muted">{c.detail}</span>
        </span>
      ))}
    </span>
  );
}

function LatencyBody({ title, stats }: { title: string; stats: Stats | null }) {
  const rows = stats?.providerLatency ?? [];
  return (
    <span className="flex min-w-0 flex-1 flex-col gap-[7px]">
      <span className="text-muted">{title}</span>
      {rows.length === 0 && <span className="text-[11px] text-disabled">No calls yet.</span>}
      {rows.map((r) => (
        <span key={r.provider} className="flex items-baseline gap-2">
          <span className="min-w-0 truncate text-ink-soft">{r.provider}</span>
          <span className="flex-1" />
          <span className="shrink-0 font-mono text-[10px] text-muted">{r.ms} ms</span>
        </span>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------
// Live = `ink` (the same weight the current work step carries), idle = dimmed,
// unreachable = the rose danger token. Nothing here is a filled pill.
function dotClass(status: string): string {
  if (status === "running" || status === "reachable") return "bg-ink";
  if (status === "unreachable") return "bg-danger";
  return "bg-disabled"; // idle
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
