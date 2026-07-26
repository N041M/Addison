// The Custom-profile guard panel (Phase-2 step 2, contract D2/D8), in the dark
// direction's row idiom.
//
// It shows ONLY under the Custom profile, and it contains ONLY the two prompting
// guards — nothing that can touch a global floor (G1/G2/G3/G4). That is a frozen
// invariant (contract Scope): a toggle that controls nothing, sitting in a safety
// panel, is a lie in the worst possible place. So this file has exactly two
// controls, forever, and a test counts the `role="group"`s to keep it that way.
//
// What the guards change is how OFTEN Addison asks before acting — never whether
// it can go back to a working setup. The panel says so out loud (the intro), and
// when a change would make Addison ask LESS often (a "weakening"), it shows the
// permanent-restore-point confirm BEFORE saving. Tightening — asking more often —
// goes straight through: making yourself safer needs no ceremony.
//
// There is NO danger token anywhere in this panel: turning a guard down is a
// choice, not a destructive act, and the way back (the anchor) is a recovery. The
// one thing that is not a control is the permanent-anchor promise, which is
// Addison telling you something.

import { useState } from "react";
import type { GuardsCardState } from "../hooks/useGuards";
import type { DestructiveCardGuard, AutoGrantScopeGuard } from "../types/ui";
import { RowConfirm, SurfaceRow } from "./Surface";

// --- Frozen copy (contract D8) — byte-for-byte. -----------------------------

/** The panel's opening line: says plainly what these settings do and, more
 * importantly, what they can never do. */
const PANEL_INTRO =
  "These settings change how often Addison asks you before acting. " +
  "They never change Addison's ability to go back to a working setup.";

/** Shown before a weakening save actually happens. Names what is saved (a
 * permanent restore point of the last working setup) and its permanence. */
const WEAKENING_CONFIRM =
  "Addison will ask you less often before acting. Before this changes, Addison " +
  "saves a permanent restore point of the last setup it saw working — it can't " +
  "be deleted, and you can always go back to it.";

/** The two guard vocabularies, each listed STRICT → WEAK so the array index is
 * the strictness rank: a later index is a weaker (asks-less) choice, and moving
 * to a later index is the "weakening" that mints the anchor. */
const DESTRUCTIVE_CARD_OPTIONS: { value: DestructiveCardGuard; copy: string }[] = [
  {
    value: "per_invocation",
    copy: "Always ask — Addison asks every time before anything that can't be undone.",
  },
  {
    value: "session",
    // The breadth is the point of this sentence: an approval covers EVERYTHING the
    // tool does afterwards — for the command tool that means every later command,
    // not a repeat of the one whose text was on the card. Softening this line hides
    // exactly the cost the choice carries (coordinator pass, 2026-07-24).
    copy:
      "Ask once — approve a risky tool once and anything else it does goes ahead " +
      "without asking, until you close Addison.",
  },
];

const AUTO_GRANT_SCOPE_OPTIONS: { value: AutoGrantScopeGuard; copy: string }[] = [
  {
    value: "none",
    copy: "Ask about everything — Addison asks before every kind of action.",
  },
  {
    value: "non_destructive",
    copy:
      "Ask only for risky actions — everyday actions go ahead; anything that can't be " +
      "undone asks first.",
  },
  {
    value: "everything",
    copy:
      "Never ask — Addison acts without asking, including things that can't be undone, " +
      "like deleting files.",
  },
];

/** Which guard a pending weakening confirm belongs to, plus the value it would
 * move to. Held until the person confirms or backs out — inline, never a browser
 * dialog, which couldn't carry the anchor promise or be styled as a recovery. */
type Pending =
  | { guard: "destructiveCard"; value: DestructiveCardGuard }
  | { guard: "autoGrantScope"; value: AutoGrantScopeGuard }
  | null;

export function CustomGuardPanel({
  connected,
  guards: state,
}: {
  connected: boolean;
  guards: GuardsCardState;
}) {
  const [pending, setPending] = useState<Pending>(null);
  const { guards, guardsLoaded, busy, error, handleSave } = state;

  if (!connected) {
    return <SurfaceRow name="These settings appear here once Addison's engine is connected." />;
  }
  if (!guardsLoaded || !guards) {
    return <SurfaceRow name="Loading your settings…" />;
  }

  // A pick is a "weakening" when it moves to a later (weaker) index in that
  // guard's strict→weak list. Weakening needs the anchor confirm first;
  // tightening (an earlier index) saves straight away.
  function pick(guard: "destructiveCard" | "autoGrantScope", value: string) {
    if (!guards) return;
    if (guard === "destructiveCard") {
      const current = guards.destructiveCard;
      const next = value as DestructiveCardGuard;
      if (next === current) return;
      const values = DESTRUCTIVE_CARD_OPTIONS.map((o) => o.value);
      if (values.indexOf(next) > values.indexOf(current)) {
        setPending({ guard, value: next });
      } else {
        void handleSave({ destructiveCard: next });
      }
    } else {
      const current = guards.autoGrantScope;
      const next = value as AutoGrantScopeGuard;
      if (next === current) return;
      const values = AUTO_GRANT_SCOPE_OPTIONS.map((o) => o.value);
      if (values.indexOf(next) > values.indexOf(current)) {
        setPending({ guard, value: next });
      } else {
        void handleSave({ autoGrantScope: next });
      }
    }
  }

  function confirmWeakening() {
    if (!pending) return;
    if (pending.guard === "destructiveCard") {
      void handleSave({ destructiveCard: pending.value });
    } else {
      void handleSave({ autoGrantScope: pending.value });
    }
    setPending(null);
  }

  return (
    <>
      <SurfaceRow name={PANEL_INTRO} />

      <GuardGroup
        label="Asking again for risky actions"
        selected={guards.destructiveCard}
        options={DESTRUCTIVE_CARD_OPTIONS}
        busy={busy}
        onPick={(v) => pick("destructiveCard", v)}
        confirm={
          pending?.guard === "destructiveCard" ? (
            <RowConfirm
              busy={busy}
              confirmLabel="Save"
              onConfirm={confirmWeakening}
              onCancel={() => setPending(null)}
            >
              {WEAKENING_CONFIRM}
            </RowConfirm>
          ) : null
        }
      />

      <GuardGroup
        label="Which actions to ask about"
        selected={guards.autoGrantScope}
        options={AUTO_GRANT_SCOPE_OPTIONS}
        busy={busy}
        onPick={(v) => pick("autoGrantScope", v)}
        confirm={
          pending?.guard === "autoGrantScope" ? (
            <RowConfirm
              busy={busy}
              confirmLabel="Save"
              onConfirm={confirmWeakening}
              onCancel={() => setPending(null)}
            >
              {WEAKENING_CONFIRM}
            </RowConfirm>
          ) : null
        }
      />

      {/* A refused save (a bad value, or the anchor couldn't be saved so nothing
          changed) in the core's own already-plain words — never a stack trace. */}
      {error && <SurfaceRow name={error} />}
    </>
  );
}

/** One guard's options, as rows. The chosen one carries a mono `chosen ✓`; every
 * other offers an accent "choose". Each control's accessible name repeats the
 * option, because a surface whose every button says "choose" is unusable to
 * anyone not looking at the screen. */
function GuardGroup({
  label,
  selected,
  options,
  busy,
  onPick,
  confirm,
}: {
  label: string;
  selected: string;
  options: { value: string; copy: string }[];
  busy: boolean;
  onPick: (value: string) => void;
  confirm: React.ReactNode;
}) {
  return (
    <div role="group" aria-label={label}>
      <div className="px-0.5 pb-1 pt-[18px] text-[11px] font-medium tracking-[.04em] text-faint">
        {label}
      </div>
      {options.map((o) => {
        const active = o.value === selected;
        return (
          <SurfaceRow
            key={o.value}
            name={o.copy}
            value={active ? "selected ✓" : undefined}
            action={active ? undefined : "choose"}
            actionAriaLabel={`Choose: ${o.copy}`}
            actionDisabled={busy}
            onAction={active ? undefined : () => onPick(o.value)}
          />
        );
      })}
      {confirm}
    </div>
  );
}
