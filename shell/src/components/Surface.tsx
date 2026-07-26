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
//
// `pinned` is the one slot above the title, and it exists for a safety reason:
// a permission card that is waiting on an answer blocks the turn, so it must be
// visible on whatever page the person is standing on — never only on chat.

import type { MouseEvent, ReactNode } from "react";

/** The DOM id App's view transitions animate. Only one surface exists at a time. */
export const SURFACE_ID = "addison-surface";

interface SurfaceProps {
  title: string;
  description?: string;
  /**
   * Rendered ABOVE the title, inside the same reading column: status banners and
   * — the reason this slot exists — a pending permission card. A question that
   * is holding a turn open must never be invisible because the person walked to
   * Settings while it was on screen.
   */
  pinned?: ReactNode;
  children?: ReactNode;
}

export function Surface({ title, description, pinned, children }: SurfaceProps) {
  return (
    <div
      id={SURFACE_ID}
      className="no-scrollbar fade-mask-y flex w-full max-w-[580px] flex-col overflow-y-auto pb-6 pt-9"
    >
      {pinned && <div className="mb-5 flex shrink-0 flex-col gap-2">{pinned}</div>}
      <div data-surf="1" className="shrink-0 text-[20px] tracking-display text-ink">
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
  /** Optional DOM id — used by the one-shot scroll requests (first-run → API keys). */
  id?: string;
  label: string;
  children?: ReactNode;
}

/**
 * One labelled block of rows. Must be a DIRECT child of <Surface> (see above) —
 * the enter/leave stagger animates `surface.children`, and the inline animation
 * here is what plays on a plain re-render.
 */
export function SurfaceSection({ id, label, children }: SurfaceSectionProps) {
  return (
    <div
      id={id}
      // The fade mask eats the first 32px of the column, so a scrolled-to section
      // stops below it rather than under it.
      className="mb-[26px] shrink-0 scroll-mt-10 animate-[fadeRise_.35s_ease_both]"
    >
      <div
        data-surf="1"
        className="border-l-2 border-rail pl-3.5 text-[11px] font-medium tracking-[.04em] text-faint"
      >
        {label}
      </div>
      <div className="mt-2.5 flex flex-col">{children}</div>
    </div>
  );
}

/** How an action reads. `danger` is for real destructive controls only — a
 * restore is a recovery and is never styled with it (HANDOFF rule). */
export type RowActionTone = "accent" | "muted" | "danger";

interface RowActionProps {
  children: ReactNode;
  /** Receives the click event so an anchored popover can measure its trigger. */
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  /** Screen-reader name. A page of rows whose every control says "choose" is
   * unusable without one. */
  ariaLabel?: string;
  tone?: RowActionTone;
  /** Machine-fact actions (a skill's `on`/`off`) read in mono, like values. */
  mono?: boolean;
  title?: string;
}

/**
 * The action at the right of a row. Exported so a row that genuinely needs two
 * of them (replace + remove) can compose them itself through `actions`.
 */
export function RowAction({
  children,
  onClick,
  disabled = false,
  ariaLabel,
  tone = "accent",
  mono = false,
  title,
}: RowActionProps) {
  const toneClass =
    tone === "danger"
      ? "text-muted hover:text-danger"
      : tone === "muted"
        ? "text-muted hover:text-ink"
        : "text-accent hover:text-ink";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || !onClick}
      aria-label={ariaLabel}
      title={title}
      className={
        "shrink-0 transition-colors disabled:cursor-not-allowed disabled:text-disabled " +
        "disabled:hover:text-disabled max-md:min-h-[44px] " +
        (mono ? "font-mono text-[10.5px] " : "text-[12px] ") +
        toneClass
      }
    >
      {children}
    </button>
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
  onAction?: (event: MouseEvent<HTMLButtonElement>) => void;
  /** Screen-reader name for the action, when its label alone is ambiguous. */
  actionAriaLabel?: string;
  /** Disables the action without removing it (a busy or unavailable step). */
  actionDisabled?: boolean;
  /** How the action reads. `danger` only for real destructive controls. */
  actionTone?: RowActionTone;
  /** Two or more controls in the action slot — replaces `action`/`onAction`. */
  actions?: ReactNode;
  /** A blocky annotation above the name (the G4 "Permanent" tag). */
  tag?: ReactNode;
  /** Optional block under the row (an inline confirm, a form) — full width. */
  children?: ReactNode;
}

export function SurfaceRow({
  name,
  value,
  action,
  onAction,
  actionAriaLabel,
  actionDisabled = false,
  actionTone = "accent",
  actions,
  tag,
  children,
}: SurfaceRowProps) {
  return (
    <div className="border-t border-line px-0.5 py-[13px] text-[12px]">
      {tag}
      <div className="flex items-baseline gap-3">
        <span data-surf="1" className="min-w-0 text-ink-soft">
          {name}
        </span>
        <span className="flex-1" />
        {value != null && value !== "" && (
          <span className="shrink-0 font-mono text-[10.5px] text-muted">{value}</span>
        )}
        {actions ? (
          <span className="flex shrink-0 items-baseline gap-3">{actions}</span>
        ) : (
          action != null &&
          action !== "" &&
          (onAction ? (
            <RowAction
              onClick={onAction}
              disabled={actionDisabled}
              ariaLabel={actionAriaLabel}
              tone={actionTone}
            >
              {action}
            </RowAction>
          ) : (
            <span className="shrink-0 text-[12px] text-muted">{action}</span>
          ))
        )}
      </div>
      {children}
    </div>
  );
}

/**
 * The inline two-step confirm used across the surfaces (a restore, a profile
 * switch, a weakening save, a folder grant). Never a browser dialog: a native
 * confirm() cannot carry the consequence copy, and the copy is the point.
 */
export function RowConfirm({
  children,
  confirmLabel,
  onConfirm,
  onCancel,
  busy = false,
  cancelLabel = "Not now",
}: {
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  cancelLabel?: string;
}) {
  return (
    <div className="mt-2.5 border-l-2 border-rail pl-3.5">
      <div className="text-[12px] leading-[1.55] text-ink-soft">{children}</div>
      <div className="mt-2.5 flex flex-wrap items-baseline gap-5">
        <RowAction onClick={onConfirm} disabled={busy}>
          {confirmLabel}
        </RowAction>
        <RowAction onClick={onCancel} tone="muted">
          {cancelLabel}
        </RowAction>
      </div>
    </div>
  );
}

/** The blocky "Permanent" annotation on a G4 anchor row — square edges, a 2px
 * accent left rule, small caps. Addison telling you something about the record;
 * there is no control there to round off, and no Remove beside it because the
 * core refuses to delete the row. */
export function PermanentTag() {
  return (
    <span className="mb-1 inline-block border-l-2 border-accent pl-1.5 text-[9.5px] font-medium uppercase tracking-[.09em] text-accent">
      Permanent
    </span>
  );
}
