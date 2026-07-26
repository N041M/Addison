// First-run setup block — the nudge shown in the chat column on a fresh,
// unconfigured launch (DARK direction; docs/design-brief-dark, "Screens → Chat
// empty state": *the pine banner is REPLACED by this empty state plus a
// first-run block restyled into the row idiom*).
//
// The Fern pine card is gone — no filled block, no cream, no serif. What is left
// is the same two steps as hairline rows under a section label, an accent "Start
// setup" action, and the launch-only "Skip for now" beside it. It rides inside
// the empty state's centred stack (ChatThread), under the greeting.
//
// The behaviour is untouched: step 1 is current on a fresh launch; once a
// provider connects during this launch the block advances to step 2 and the copy
// flips to a "say hello" nudge (App focuses the composer). "Start setup" opens
// Settings on the API-keys card; "Skip for now" dismisses for this launch only
// (plain state, deliberately not persisted — it returns next launch while
// nothing is configured).

interface Props {
  /** 1 = connect a model (fresh), 2 = say hello (a provider connected this launch). */
  step: 1 | 2;
  /** Open Settings focused on the API-keys card. */
  onStartSetup: () => void;
  /** Dismiss for this launch only (plain state, no persistence). */
  onSkip: () => void;
}

export function FirstRunBanner({ step, onStartSetup, onSkip }: Props) {
  return (
    <section aria-label="First-time setup" className="w-full text-left">
      <div className="border-l-2 border-rail pl-3.5">
        <p className="m-0 text-[11px] font-medium tracking-[.04em] text-faint">
          First-time setup · step {step} of 2
        </p>
        <p className="m-0 mt-2 text-[12px] text-ink-soft">
          {step === 1 ? "Let's get Addison ready." : "You're set up. Say hello to Addison."}
        </p>
      </div>

      <div className="mt-2.5 flex flex-col">
        <StepRow
          state={step === 1 ? "current" : "done"}
          text="Choose where Addison thinks — connect a cloud account, or download a model that stays on this computer."
        />
        <StepRow
          state={step === 2 ? "current" : "later"}
          text="Say hello — Addison introduces itself and asks what you need."
        />
      </div>

      <div className="mt-3.5 flex items-baseline gap-5 pl-0.5">
        {step === 1 && (
          <button
            type="button"
            onClick={onStartSetup}
            className="text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
          >
            Start setup
          </button>
        )}
        <button
          type="button"
          onClick={onSkip}
          className="text-[12px] text-muted transition-colors hover:text-ink max-md:min-h-[44px]"
        >
          Skip for now
        </button>
      </div>
    </section>
  );
}

// One step, as a hairline row: what it is, then a mono word for where it stands.
// "done ✓" only ever appears for a step that really is done.
function StepRow({ state, text }: { state: "current" | "done" | "later"; text: string }) {
  const tone =
    state === "current" ? "text-ink-soft" : state === "done" ? "text-muted" : "text-disabled";
  const note = state === "current" ? "now" : state === "done" ? "done ✓" : "next";
  return (
    <div className="flex items-baseline gap-3 border-t border-line px-0.5 py-[13px] text-[12px]">
      <span className={"min-w-0 " + tone}>{text}</span>
      <span className="flex-1" />
      <span
        className={
          "shrink-0 font-mono text-[10px] " +
          (state === "current" ? "text-accent" : "text-disabled")
        }
      >
        {note}
      </span>
    </div>
  );
}
