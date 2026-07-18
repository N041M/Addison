// First-run pine banner — the setup nudge shown in the chat column on a fresh,
// unconfigured launch (design-brief-fern README §5, directions option 5a).
//
// The pine banner is the ONE high-contrast block in the Fern palette: pine bg,
// cream text, 12px radius, the `pine` lift shadow. It never inverts between
// themes, so its text colors are fixed tokens (pine-soft / pine-ink / cream /
// pine-body / pine-muted / pine-line — see styles.css) rather than the usual
// light↔dark pairs.
//
// Two steps: (1) connect a model, (2) say hello. Step 1 is current on a fresh
// launch; once a provider connects during this launch the banner advances to
// step 2 and the copy flips to a "say hello" nudge (App focuses the composer).
// "Start setup" opens Settings on the API-keys card; "Skip for now" dismisses
// the banner for this launch only (no persistence — it returns next launch while
// nothing is configured). Below the pine card, on a fresh launch with an empty
// thread, a serif time-of-day greeting replaces the normal welcome message.

interface Props {
  /** 1 = connect a model (fresh), 2 = say hello (a provider connected this launch). */
  step: 1 | 2;
  /** Open Settings focused on the API-keys card. */
  onStartSetup: () => void;
  /** Dismiss for this launch only (plain state, no persistence). */
  onSkip: () => void;
  /** Show the serif greeting below the card (first-run + empty thread only). */
  showGreeting: boolean;
}

export function FirstRunBanner({ step, onStartSetup, onSkip, showGreeting }: Props) {
  return (
    <div className="flex flex-col gap-9">
      <section
        aria-label="First-time setup"
        className="rounded-banner bg-pine px-7 py-6 text-cream shadow-banner"
      >
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-[10.5px] font-semibold uppercase tracking-[0.11em] text-pine-soft">
            First-time setup · step {step} of 2
          </p>
          <button
            type="button"
            onClick={onSkip}
            className="text-[12.5px] font-medium text-pine-muted hover:text-cream"
          >
            Skip for now
          </button>
        </div>

        <h2 className="mt-2.5 font-serif text-[24px] font-medium text-pine-ink">
          {step === 1 ? "Let's get Addison ready." : "You're set up. Say hello to Addison."}
        </h2>

        <div className="mt-4 flex flex-col gap-2.5">
          <StepRow
            state={step === 1 ? "current" : "done"}
            n={1}
            text="Choose where Addison thinks — connect a cloud account, or download a model that stays on this computer."
          />
          <StepRow
            state={step === 2 ? "current" : "later"}
            n={2}
            text="Say hello — Addison introduces itself and asks what you need."
          />
        </div>

        {step === 1 && (
          <button
            type="button"
            onClick={onStartSetup}
            className="mt-[18px] rounded-[7px] bg-cream px-6 py-2.5 text-[14px] font-semibold text-pine hover:bg-pine-ink"
          >
            Start setup
          </button>
        )}
      </section>

      {showGreeting && (
        <div>
          <h1 className="font-serif text-[32px] font-medium tracking-[-0.01em] text-ink">
            {greeting()}
          </h1>
          <p className="mt-2 text-[15px] text-muted">
            Tell me what you'd like help with — I'll ask before doing anything, and
            you can always undo.
          </p>
        </div>
      )}
    </div>
  );
}

// One numbered step. current = cream-filled circle with pine number; done =
// cream-filled circle with a pine check; later = outlined circle, muted number.
function StepRow({
  state,
  n,
  text,
}: {
  state: "current" | "done" | "later";
  n: number;
  text: string;
}) {
  const circle =
    state === "later"
      ? "border border-pine-line text-pine-muted"
      : "bg-cream text-pine";
  const bodyColor = state === "later" ? "text-pine-muted" : "text-pine-body";
  return (
    <div className="flex items-start gap-3">
      <span
        className={
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-pill text-[12.5px] font-semibold " +
          circle
        }
      >
        {state === "done" ? "✓" : n}
      </span>
      <p className={"mt-px text-[14px] " + bodyColor}>{text}</p>
    </div>
  );
}

// Time-of-day greeting: morning 5–12, afternoon 12–18, evening otherwise.
function greeting(): string {
  const h = new Date().getHours();
  if (h >= 5 && h < 12) return "Good morning.";
  if (h >= 12 && h < 18) return "Good afternoon.";
  return "Good evening.";
}
