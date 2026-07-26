// The Addison mark — a dark tile, a lowercase "a" set baseline-left, and the
// lavender dot (docs/design-brief-dark/brand/, "Addison Logo Mark" — the final
// sheet; "Addison Logo.dc.html" is the exploration history).
//
// Construction (the sheet's CONSTRUCTION table): tile #16171B with a 1px inset
// ring #26272B and ~9% radius; letter Helvetica 500 at ~69% of the tile,
// letter-spacing -.04em, line-height .8; dot #B4A9F5 at ~23% of the tile, inset
// ~14% from the top-right. The sizes the app actually uses (22px header, 44px
// first-run splash) take their EXACT pixel geometry from the sheet's samples;
// anything else falls back to the construction ratios.
//
// The colors are fixed hex ON PURPOSE, never theme tokens: the sheet's ON LIGHT
// panel says "tile stays dark" — the mark is identical in both themes.

const SAMPLED: Record<
  number,
  { r: number; fs: number; pl: number; pb: number; dot: number; inset: number }
> = {
  16: { r: 2, fs: 12, pl: 3, pb: 1, dot: 4, inset: 2 },
  20: { r: 2, fs: 14, pl: 4, pb: 2, dot: 5, inset: 3 },
  22: { r: 3, fs: 16, pl: 4, pb: 2, dot: 5, inset: 3 },
  34: { r: 3, fs: 24, pl: 6, pb: 4, dot: 8, inset: 5 },
  44: { r: 5, fs: 30, pl: 8, pb: 6, dot: 10, inset: 6 },
  64: { r: 6, fs: 44, pl: 12, pb: 8, dot: 15, inset: 9 },
};

export function AddisonMark({ size = 22 }: { size?: number }) {
  const s = SAMPLED[size] ?? {
    r: Math.max(2, Math.round(size * 0.09)),
    fs: Math.round(size * 0.69),
    pl: Math.round(size * 0.18),
    pb: Math.round(size * 0.125),
    dot: Math.round(size * 0.23),
    inset: Math.round(size * 0.14),
  };
  return (
    <span
      aria-hidden="true"
      className="relative flex shrink-0 items-end"
      style={{
        width: size,
        height: size,
        borderRadius: s.r,
        background: "#16171B",
        boxShadow: "inset 0 0 0 1px #26272B",
        padding: `0 0 ${s.pb}px ${s.pl}px`,
        boxSizing: "border-box",
      }}
    >
      <span
        style={{
          fontSize: s.fs,
          fontWeight: 500,
          letterSpacing: "-.04em",
          lineHeight: 0.8,
          color: "#E9E9E7",
        }}
      >
        a
      </span>
      <span
        className="absolute rounded-full"
        style={{ top: s.inset, right: s.inset, width: s.dot, height: s.dot, background: "#B4A9F5" }}
      />
    </span>
  );
}
