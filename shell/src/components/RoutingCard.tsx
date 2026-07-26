// How Addison picks which model answers (Phase-2 step 3, contract D5/D7/D8), in
// the dark direction's row idiom.
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
// Every row reads the same way as the rest of the surface: the chosen option
// carries a mono `selected ✓` and no control (there is nothing to do to it), and
// each other offers an accent "choose" whose accessible name repeats the option,
// because a page whose every button says "choose" is unusable to anyone not
// looking at the screen. A refused save renders as one plain sentence — it
// arrives already user-ready from the core (including the "couldn't save the
// restore point, so nothing changed" line that guards a custom-chain overwrite).

import { useEffect, useState } from "react";
import type { RoutingCardState } from "../hooks/useRouting";
import type { RoutingStrategy } from "../types/ui";
import { RowAction, SurfaceRow } from "./Surface";

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
    return <SurfaceRow name="This appears here once Addison’s engine is connected." />;
  }
  if (!routingLoaded || !routing) {
    return <SurfaceRow name="Loading your settings…" />;
  }

  return (
    <>
      {routing.surface === "toggle" ? (
        <div role="group" aria-label="How Addison picks a model">
          {TOGGLE_OPTIONS.map((o) => (
            <StrategyRow
              key={o.strategy}
              label={o.copy}
              active={o.strategy === routing.strategy}
              busy={busy}
              onPick={() => void handleSetStrategy(o.strategy)}
            />
          ))}
        </div>
      ) : (
        <>
          <div role="group" aria-label="How Addison picks a model">
            {routing.availableStrategies.map((s) => (
              <StrategyRow
                key={s}
                label={STRATEGY_LABELS[s]}
                active={s === routing.strategy}
                busy={busy}
                onPick={() => void handleSetStrategy(s)}
              />
            ))}
          </div>
          {/* The chain builder appears only for the Custom-order strategy. */}
          {routing.strategy === "custom" && (
            <ChainBuilder
              chain={routing.customChain}
              models={models}
              busy={busy}
              onSave={(c) => void handleSaveChain(c)}
            />
          )}
        </>
      )}

      {/* A refused save in the core's own already-plain words — never a stack trace. */}
      {error && <SurfaceRow name={error} />}
    </>
  );
}

/** One selectable strategy. The chosen one is a statement, not a control. */
function StrategyRow({
  label,
  active,
  busy,
  onPick,
}: {
  label: string;
  active: boolean;
  busy: boolean;
  onPick: () => void;
}) {
  return (
    <SurfaceRow
      name={label}
      value={active ? "selected ✓" : undefined}
      action={active ? undefined : "choose"}
      actionAriaLabel={`Choose: ${label}`}
      actionDisabled={busy}
      onAction={active ? undefined : onPick}
    />
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
    <>
      <SurfaceRow name="Addison tries these in order, top first, and moves down when one can’t answer." />

      {draft.length === 0 ? (
        <SurfaceRow name="No models yet" value="add one below to build the order" />
      ) : (
        draft.map((id, i) => (
          <SurfaceRow
            key={id}
            name={
              <>
                <span className="mr-1.5 font-mono text-[10.5px] text-faint">{i + 1}</span>
                {labelFor(id)}
              </>
            }
            actions={
              <>
                <RowAction
                  mono
                  ariaLabel={`Move ${labelFor(id)} up`}
                  disabled={busy || i === 0}
                  onClick={() => move(i, -1)}
                >
                  ↑
                </RowAction>
                <RowAction
                  mono
                  ariaLabel={`Move ${labelFor(id)} down`}
                  disabled={busy || i === draft.length - 1}
                  onClick={() => move(i, 1)}
                >
                  ↓
                </RowAction>
                <RowAction
                  mono
                  tone="danger"
                  ariaLabel={`Remove ${labelFor(id)}`}
                  disabled={busy}
                  onClick={() => remove(i)}
                >
                  ✕
                </RowAction>
              </>
            }
          />
        ))
      )}

      {notInChain.length > 0 && (
        <div>
          <div className="px-0.5 pb-1 pt-[18px] text-[11px] font-medium tracking-[.04em] text-faint">
            Add a model
          </div>
          {notInChain.map((m) => (
            <SurfaceRow
              key={m.id}
              name={m.label}
              action="add"
              actionAriaLabel={`Add ${m.label}`}
              actionDisabled={busy}
              onAction={() => add(m.id)}
            />
          ))}
        </div>
      )}

      <SurfaceRow
        name="Model order"
        value={dirty ? "not saved yet" : "saved"}
        actions={
          <>
            <RowAction disabled={busy || !dirty} onClick={() => onSave(draft)}>
              Save order
            </RowAction>
            {dirty && (
              <RowAction tone="muted" disabled={busy} onClick={() => setDraft(chain)}>
                Undo changes
              </RowAction>
            )}
          </>
        }
      />
    </>
  );
}
