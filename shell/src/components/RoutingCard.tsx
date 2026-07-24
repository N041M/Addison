// How Addison picks which model answers (Phase-2 step 3, contract D5/D7/D8).
//
// One card, two surfaces — the CORE decides which (routing.surface), never this
// component:
//   • "toggle" (Simple): ONE two-option control — Prefer quality / Prefer free,
//     mapping to quality_first / cost_first. No other strategy is visible and no
//     jargon appears; a companion never sees "local_only" or a chain builder.
//   • "full"  (Developer / Custom): the four-strategy picker (Quality first /
//     Cost first / Local only / Custom order) plus, when Custom order is chosen,
//     an ordered chain builder over the connected-models union — add / remove /
//     reorder, saved as one full list.
//
// Fern shape rule (docs/design-brief-fern): every control here is rounded (the
// person's to act on) and the fern accent marks the chosen option. A refused save
// renders as one plain sentence — it arrives already user-ready from the core
// (including the "couldn't save the restore point, so nothing changed" line that
// guards a custom-chain overwrite).

import { useEffect, useState } from "react";
import type { RoutingCardState } from "../hooks/useRouting";
import type { RoutingStrategy } from "../types/ui";

// --- Frozen copy (contract D8) — byte-for-byte. -----------------------------

/** The Simple two-option toggle. Order is fixed: quality first, then free. */
const TOGGLE_OPTIONS: { strategy: RoutingStrategy; copy: string }[] = [
  { strategy: "quality_first", copy: "Prefer quality — the strongest model answers." },
  { strategy: "cost_first", copy: "Prefer free — free models answer when they can." },
];

/** The full picker's strategy labels (contract D8). No "balanced" — cut from v1
 * (owner decision 2026-07-24). */
const STRATEGY_LABELS: Record<RoutingStrategy, string> = {
  quality_first: "Quality first",
  cost_first: "Cost first",
  local_only: "Local only",
  custom: "Custom order",
};

export interface RoutingCardModel {
  id: string;
  label: string;
}

export function RoutingCard({
  connected,
  routing: state,
  models,
}: {
  connected: boolean;
  routing: RoutingCardState;
  /** The connected-models union, for the custom chain builder. Same data the
   * model picker consumes; ignored entirely by the Simple toggle surface. */
  models: RoutingCardModel[];
}) {
  const { routing, routingLoaded, busy, error, handleSetStrategy, handleSaveChain } = state;

  if (!connected) {
    return (
      <p className="text-meta text-muted">
        This appears here once Addison&rsquo;s engine is connected.
      </p>
    );
  }
  if (!routingLoaded || !routing) {
    return <p className="text-meta text-muted">Loading your settings&hellip;</p>;
  }

  return (
    <div>
      {routing.surface === "toggle" ? (
        <ToggleSurface
          selected={routing.strategy}
          busy={busy}
          onPick={(s) => void handleSetStrategy(s)}
        />
      ) : (
        <FullSurface
          selected={routing.strategy}
          available={routing.availableStrategies}
          chain={routing.customChain}
          models={models}
          busy={busy}
          onPick={(s) => void handleSetStrategy(s)}
          onSaveChain={(c) => void handleSaveChain(c)}
        />
      )}

      {/* A refused save in the core's own already-plain words — never a stack trace. */}
      {error && <p className="mt-3 text-fine leading-relaxed text-ink-soft">{error}</p>}
    </div>
  );
}

// --- Simple: the two-option toggle -----------------------------------------
function ToggleSurface({
  selected,
  busy,
  onPick,
}: {
  selected: RoutingStrategy;
  busy: boolean;
  onPick: (strategy: RoutingStrategy) => void;
}) {
  return (
    <div role="group" aria-label="How Addison picks a model">
      <div className="flex flex-col gap-1.5">
        {TOGGLE_OPTIONS.map((o) => {
          const active = o.strategy === selected;
          return (
            <button
              key={o.strategy}
              type="button"
              aria-pressed={active}
              disabled={busy || active}
              onClick={() => onPick(o.strategy)}
              className={optionClass(active)}
            >
              {o.copy}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// --- Developer / Custom: the full picker + chain builder --------------------
function FullSurface({
  selected,
  available,
  chain,
  models,
  busy,
  onPick,
  onSaveChain,
}: {
  selected: RoutingStrategy;
  available: RoutingStrategy[];
  chain: string[];
  models: RoutingCardModel[];
  busy: boolean;
  onPick: (strategy: RoutingStrategy) => void;
  onSaveChain: (chain: string[]) => void;
}) {
  return (
    <div>
      <div role="group" aria-label="How Addison picks a model" className="flex flex-col gap-1.5">
        {available.map((s) => {
          const active = s === selected;
          return (
            <button
              key={s}
              type="button"
              aria-pressed={active}
              disabled={busy || active}
              onClick={() => onPick(s)}
              className={optionClass(active)}
            >
              {STRATEGY_LABELS[s]}
            </button>
          );
        })}
      </div>

      {/* The chain builder appears only for the Custom-order strategy. */}
      {selected === "custom" && (
        <ChainBuilder chain={chain} models={models} busy={busy} onSave={onSaveChain} />
      )}
    </div>
  );
}

function ChainBuilder({
  chain,
  models,
  busy,
  onSave,
}: {
  chain: string[];
  models: RoutingCardModel[];
  busy: boolean;
  onSave: (chain: string[]) => void;
}) {
  // The edited order, local until "Save order". Re-seeded whenever the saved
  // chain changes underneath us (a fresh routing.get after a successful save, or
  // an engine restart), so the draft never drifts from what the core holds.
  const [draft, setDraft] = useState<string[]>(chain);
  useEffect(() => {
    setDraft(chain);
  }, [chain]);

  const labelFor = (id: string) => models.find((m) => m.id === id)?.label ?? id;
  const notInChain = models.filter((m) => !draft.includes(m.id));
  const dirty = draft.length !== chain.length || draft.some((id, i) => id !== chain[i]);

  function move(index: number, delta: number) {
    const next = [...draft];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setDraft(next);
  }
  function remove(index: number) {
    setDraft(draft.filter((_, i) => i !== index));
  }
  function add(id: string) {
    if (!id || draft.includes(id)) return;
    setDraft([...draft, id]);
  }

  return (
    <div className="mt-3 rounded-card border border-line bg-paper px-[15px] py-[13px]">
      <p className="text-fine leading-relaxed text-ink-soft">
        Addison tries these in order, top first, and moves down when one can&rsquo;t answer.
      </p>

      {draft.length === 0 ? (
        <p className="mt-2.5 text-fine text-faint">
          No models yet. Add one below to build the order.
        </p>
      ) : (
        <ol className="mt-2.5 flex flex-col gap-1.5">
          {draft.map((id, i) => (
            <li
              key={id}
              className="flex items-center justify-between gap-2 rounded-md border border-line bg-surface px-3 py-2"
            >
              <span className="min-w-0 truncate text-fine text-ink">
                <span className="mr-1.5 font-mono text-label text-faint">{i + 1}</span>
                {labelFor(id)}
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                <IconButton label={`Move ${labelFor(id)} up`} disabled={busy || i === 0} onClick={() => move(i, -1)}>
                  ↑
                </IconButton>
                <IconButton
                  label={`Move ${labelFor(id)} down`}
                  disabled={busy || i === draft.length - 1}
                  onClick={() => move(i, 1)}
                >
                  ↓
                </IconButton>
                <IconButton label={`Remove ${labelFor(id)}`} disabled={busy} onClick={() => remove(i)}>
                  ✕
                </IconButton>
              </span>
            </li>
          ))}
        </ol>
      )}

      {notInChain.length > 0 && (
        <div className="mt-2.5">
          <label htmlFor="routing-add-model" className="block text-fine font-medium text-muted">
            Add a model
          </label>
          <select
            id="routing-add-model"
            value=""
            disabled={busy}
            onChange={(e) => {
              add(e.target.value);
              e.target.value = "";
            }}
            className="mt-1 block w-full rounded-sm border border-line bg-surface px-3 py-2 text-control text-ink disabled:opacity-50"
          >
            <option value="" disabled>
              Choose a model to add&hellip;
            </option>
            {notInChain.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          disabled={busy || !dirty}
          onClick={() => onSave(draft)}
          className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50"
        >
          Save order
        </button>
        {dirty && (
          <button
            type="button"
            disabled={busy}
            onClick={() => setDraft(chain)}
            className="text-xs font-medium text-ink-soft hover:text-muted disabled:opacity-50"
          >
            Undo changes
          </button>
        )}
      </div>
    </div>
  );
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded-md border border-line bg-paper text-glyph text-ink-soft hover:border-muted disabled:opacity-40 max-md:h-11 max-md:w-11"
    >
      <span aria-hidden="true">{children}</span>
    </button>
  );
}

/** One selectable option row — rounded (yours to act on), the chosen one marked
 * with the fern tint. Shared by both surfaces and the strategy picker. */
function optionClass(active: boolean): string {
  return (
    "rounded-md border px-3.5 py-2.5 text-left text-fine leading-relaxed transition-colors " +
    "disabled:cursor-default max-md:min-h-[44px] " +
    (active
      ? "border-fern bg-fern-tint text-ink"
      : "border-line bg-paper text-ink-soft hover:border-muted disabled:opacity-50")
  );
}
