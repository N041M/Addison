// The Addison mark — concept B, the service bell (design brief: "Ring, and
// Addison comes; it acts when asked, never uninvited"). Three primitive shapes:
// the knob, the dome, and the base. Monochrome only, drawn in `currentColor`, so
// a parent `text-fern` (or cream on pine) tints it and it flips with the theme
// automatically. Never given effects or gradients.
//
// From docs/design-brief-fern/Addison Logo.dc.html (chosen concept B).

interface Props {
  /** Rendered size in px (17 next to the wordmark; 16 for favicon/tray). */
  size?: number;
  className?: string;
}

export function BellLogo({ size = 17, className }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="currentColor"
      aria-hidden="true"
      className={className}
    >
      <circle cx="32" cy="15" r="4" />
      <path d="M13 42 A19 19 0 0 1 51 42 Z" />
      <rect x="9" y="45" width="46" height="7" rx="3.5" />
    </svg>
  );
}
