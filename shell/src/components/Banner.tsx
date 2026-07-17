// Quiet plain-language banners (design-doc §9's no-jargon rule).
//
// Two low-key tones:
//   - "notice": transient shell notices delivered on `core-status` (e.g. the
//     engine restarting), and the "engine isn't connected" degraded state.
// No alarming reds, no icons that shout — just a calm warm strip that a reader
// can take or leave.

interface Props {
  message: string;
  tone?: "notice";
  onDismiss?: () => void;
}

export function Banner({ message, tone = "notice", onDismiss }: Props) {
  void tone; // single tone today; kept for future quiet variants.
  return (
    <div className="flex items-center gap-3 border-b border-line bg-notice-tint px-6 py-2.5">
      <p className="flex-1 text-sm text-notice">{message}</p>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss this notice"
          className="text-sm font-medium text-notice hover:underline"
        >
          Dismiss
        </button>
      )}
    </div>
  );
}
