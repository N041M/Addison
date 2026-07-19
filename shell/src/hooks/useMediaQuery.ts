// useMediaQuery — a tiny dependency-free media-query hook (Fern mobile layout).
//
// Addison is a desktop Tauri app; "mobile" here means the NARROW-WINDOW layout
// (which also future-proofs a phone shell). The one breakpoint is 768px — the
// same as Tailwind's `md:` — so structural changes that can't be expressed in
// CSS alone (the slide-over drawer, the widget bottom sheet, where consent cards
// render) key off `useMediaQuery("(max-width: 767.98px)")`, while purely visual
// changes (hit-target sizes, paddings) stay in Tailwind `max-md:`/`md:` variants.
//
// 767.98px is the exact complement of Tailwind's `min-width: 768px`, so the JS
// boolean and the CSS breakpoint always agree at the edge.

import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false,
  );

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange(); // sync in case the query changed since the initial render
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
