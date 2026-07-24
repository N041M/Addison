// The Custom-profile guard panel (Phase-2 step 2, contract D2/D8).
//
// It shows ONLY under the Custom profile, and it contains ONLY the two prompting
// guards — nothing that can touch a global floor (G1/G2/G3/G4). That is a frozen
// invariant (contract Scope): a toggle that controls nothing, sitting in a safety
// panel, is a lie in the worst possible place. So this file has exactly two
// controls, forever.
//
// What the guards change is how OFTEN Addison asks before acting — never whether
// it can go back to a working setup. The panel says so out loud (the intro), and
// when a change would make Addison ask LESS often (a "weakening"), it shows the
// permanent-restore-point confirm BEFORE saving. Tightening — asking more often —
// goes straight through: making yourself safer needs no ceremony.
//
// Fern shape rule (docs/design-brief-fern): every control here is rounded,
// because it is the person's to act on, and the fern accent marks the chosen
// option and the confirm's primary. There is NO danger token anywhere in this
// panel — turning a guard down is a choice, not a destructive act, and the way
// back (the anchor) is a recovery. The one blocky note is the permanent-anchor
// promise, which is Addison telling you something, not a control.

import { useState } from "react";
import type { GuardsCardState } from "../hooks/useGuards";
import type { DestructiveCardGuard, AutoGrantScopeGuard } from "../types/ui";

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
    return (
      <p className="text-meta text-muted">
        These settings appear here once Addison&rsquo;s engine is connected.
      </p>
    );
  }
  if (!guardsLoaded || !guards) {
    return <p className="text-meta text-muted">Loading your settings&hellip;</p>;
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
    <div>
      <p className="mb-3.5 text-fine leading-relaxed text-ink-soft">{PANEL_INTRO}</p>

      <GuardGroup
        label="Asking again for risky actions"
        selected={guards.destructiveCard}
        options={DESTRUCTIVE_CARD_OPTIONS}
        busy={busy}
        onPick={(v) => pick("destructiveCard", v)}
      />
      {pending?.guard === "destructiveCard" && (
        <WeakeningConfirm busy={busy} onConfirm={confirmWeakening} onCancel={() => setPending(null)} />
      )}

      <div className="mt-4">
        <GuardGroup
          label="Which actions to ask about"
          selected={guards.autoGrantScope}
          options={AUTO_GRANT_SCOPE_OPTIONS}
          busy={busy}
          onPick={(v) => pick("autoGrantScope", v)}
        />
      </div>
      {pending?.guard === "autoGrantScope" && (
        <WeakeningConfirm busy={busy} onConfirm={confirmWeakening} onCancel={() => setPending(null)} />
      )}

      {/* A refused save (a bad value, or the anchor couldn't be saved so nothing
          changed) in the core's own already-plain words — never a stack trace. */}
      {error && <p className="mt-3 text-fine leading-relaxed text-ink-soft">{error}</p>}
    </div>
  );
}

/** One guard's options, as a vertical list of selectable rows. Rounded (yours to
 * act on), the chosen one marked with the fern tint — the same selection cue as
 * the profile and appearance controls. */
function GuardGroup({
  label,
  selected,
  options,
  busy,
  onPick,
}: {
  label: string;
  selected: string;
  options: { value: string; copy: string }[];
  busy: boolean;
  onPick: (value: string) => void;
}) {
  return (
    <div role="group" aria-label={label}>
      <span className="text-control text-ink-soft">{label}</span>
      <div className="mt-2 flex flex-col gap-1.5">
        {options.map((o) => {
          const active = o.value === selected;
          return (
            <button
              key={o.value}
              type="button"
              aria-pressed={active}
              disabled={busy}
              onClick={() => onPick(o.value)}
              className={
                "rounded-md border px-3.5 py-2.5 text-left text-fine leading-relaxed transition-colors " +
                "disabled:opacity-50 max-md:min-h-[44px] " +
                (active
                  ? "border-fern bg-fern-tint text-ink"
                  : "border-line bg-paper text-ink-soft hover:border-muted")
              }
            >
              {o.copy}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The permanent-restore-point confirm shown before a weakening save. Fern-tinted
 * and rounded like the restore confirm in SnapshotsCard — a recovery promise, not
 * a warning, so no danger token. */
function WeakeningConfirm({
  busy,
  onConfirm,
  onCancel,
}: {
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="mt-3 rounded-card bg-fern-tint px-[15px] py-[13px]">
      <p className="text-fine leading-relaxed text-ink-soft">{WEAKENING_CONFIRM}</p>
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={onConfirm}
          className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50"
        >
          Save
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-xs font-medium text-ink-soft hover:text-muted"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
