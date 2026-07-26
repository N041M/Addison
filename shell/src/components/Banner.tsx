// Quiet plain-language banners (design-doc §9's no-jargon rule; DARK direction
// card language — a flat `panel` block with a 1px `rail` border, 7px radius,
// 12px text and a muted text action).
//
// One low-key tone: transient shell notices delivered on `core-status` (e.g. the
// engine restarting), and the "engine isn't connected" degraded state. No
// alarming reds, no icons that shout — just a calm strip a reader can take or
// leave.

interface Props {
  message: string;
  tone?: "notice";
  onDismiss?: () => void;
}

export function Banner({ message, tone = "notice", onDismiss }: Props) {
  void tone; // single tone today; kept for future quiet variants.
  // Width-agnostic on purpose: the banner fills its container, and each
  // placement (chat column, settings column) owns the width and stacking gap —
  // that's what keeps every banner flush with the content beneath it.
  return (
    <div className="flex w-full animate-[fadeRise_.2s_ease_both] items-baseline gap-3 rounded-[7px] border border-rail bg-panel px-3.5 py-2.5">
      <p className="m-0 flex-1 text-[12px] leading-[1.55] text-ink-soft">{message}</p>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss this notice"
          className="shrink-0 text-[12px] text-muted transition-colors hover:text-ink max-md:min-h-[44px]"
        >
          Dismiss
        </button>
      )}
    </div>
  );
}
