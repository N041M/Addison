// Surfaces — the full-page views that replace the chat column (Settings, Tools,
// Snapshots, Build a widget). docs/design-brief-dark, "Surfaces".
//
// One centred reading column, max 580px, scrolling with hidden scrollbars behind
// the vertical fade mask: 20px title, 13px `muted` description, then sections.
// A section is an 11px label on a 2px `rail` left rule; its rows are separated by
// 1px `line` top borders and read left to right as
//
//     name (12px ink-soft) — spacer — mono value (10.5px muted) — accent action
//
// Two conventions the App's view machine depends on, so keep them if this file
// is edited:
//
//   * the root carries `id={SURFACE_ID}`. Entering a surface staggers its DIRECT
//     children with fadeRise; leaving plays fadeDrop over them and commits the
//     new view at ~240ms. Both walk `surface.children`, so every section must be
//     a direct child of this container — wrapping sections in a <div> would
//     collapse the stagger to a single element.
//   * text that should resolve out of the scramble on a view change carries
//     `data-surf="1"` and holds a single text node.
//
// SAFETY NOTE: a surface renders whatever its caller passes. It never invents a
// row. Fabricated state on a page about what Addison can reach, or about the ways
// back it has saved, would be a lie in exactly the place a person is checking
// whether they can trust the thing (IMPLEMENTATION.md, standing rule 1).

import type { ReactNode } from "react";

/** The DOM id App's view transitions animate. Only one surface exists at a time. */
export const SURFACE_ID = "addison-surface";

interface SurfaceProps {
  title: string;
  description?: string;
  children?: ReactNode;
  /**
   * "column" (default) is the designed reading column: 580px, its own scroll,
   * fade-masked. "raw" drops the width, scroll and mask, and is a TEMPORARY
   * accommodation for a page that still brings its own scroller and layout —
   * today only SettingsPage, which phase 3 rebuilds into sections and rows.
   * Nesting that page inside a 580px masked scroller would give it two
   * scrollbars and clip its cards; the enter/leave transitions still apply.
   */
  variant?: "column" | "raw";
}

export function Surface({ title, description, children, variant = "column" }: SurfaceProps) {
  if (variant === "raw") {
    return (
      <div id={SURFACE_ID} className="flex min-h-0 w-full flex-1 flex-col">
        {children}
      </div>
    );
  }

  return (
    <div
      id={SURFACE_ID}
      className="no-scrollbar fade-mask-y flex w-full max-w-[580px] flex-col overflow-y-auto pb-6 pt-9"
    >
      <div
        data-surf="1"
        className="shrink-0 text-[20px] tracking-display text-ink"
      >
        {title}
      </div>
      {description && (
        <p
          data-surf="1"
          className="m-0 mb-[26px] mt-2.5 shrink-0 text-[13px] leading-[1.6] text-muted"
        >
          {description}
        </p>
      )}
      {children}
    </div>
  );
}

interface SurfaceSectionProps {
  label: string;
  children?: ReactNode;
}

/**
 * One labelled block of rows. Must be a DIRECT child of <Surface> (see above) —
 * the enter/leave stagger animates `surface.children`, and the inline animation
 * here is what plays on a plain re-render.
 */
export function SurfaceSection({ label, children }: SurfaceSectionProps) {
  return (
    <div className="mb-[26px] shrink-0 animate-[fadeRise_.35s_ease_both]">
      <div className="border-l-2 border-rail pl-3.5 text-[11px] font-medium tracking-[.04em] text-faint">
        {label}
      </div>
      <div className="mt-2.5 flex flex-col">{children}</div>
    </div>
  );
}

interface SurfaceRowProps {
  /** Left-hand name (12px ink-soft). */
  name: ReactNode;
  /** Machine fact — mono 10.5px muted. A count, a timestamp, a status. */
  value?: ReactNode;
  /** The action's label ("change", "restore", "open"). Rendered as accent text. */
  action?: ReactNode;
  /** Omit to render `action` as plain text — a state, not a control. */
  onAction?: () => void;
  /** Disables the action without removing it (a busy or unavailable step). */
  actionDisabled?: boolean;
  /** Optional block under the row (an inline confirm, a form) — full width. */
  children?: ReactNode;
}

export function SurfaceRow({
  name,
  value,
  action,
  onAction,
  actionDisabled = false,
  children,
}: SurfaceRowProps) {
  return (
    <div className="border-t border-line px-0.5 py-[13px] text-[12px]">
      <div className="flex items-baseline gap-3">
        <span data-surf="1" className="min-w-0 text-ink-soft">
          {name}
        </span>
        <span className="flex-1" />
        {value != null && value !== "" && (
          <span className="shrink-0 font-mono text-[10.5px] text-muted">{value}</span>
        )}
        {action != null &&
          action !== "" &&
          (onAction ? (
            <button
              type="button"
              onClick={onAction}
              disabled={actionDisabled}
              className="shrink-0 text-[12px] text-accent transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-disabled max-md:min-h-[44px]"
            >
              {action}
            </button>
          ) : (
            <span className="shrink-0 text-[12px] text-muted">{action}</span>
          ))}
      </div>
      {children}
    </div>
  );
}
