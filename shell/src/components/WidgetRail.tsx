// Widget rail — the right column of the Fern app shell (design-brief-fern README
// §3, handoff §4). 270px, `paper` background, no left border (per the prototype),
// its own thin-scrollbar scroll. Hideable via the chat header's rail toggle.
//
// THIS PR ships the rail COLUMN and its non-widget furniture only: the "YOUR
// WIDGETS" header (with an inert "Edit" button), the "Addison's work" block and
// the consent card (passed in from App so they can also render inline when the
// rail is hidden), and the dashed "Ask Addison to build a widget" button (inert
// until the widgets PR). Real widget cards — routine rows, token meters,
// connection lists, the overflow tray — arrive in a later PR.

import type { ReactNode } from "react";

interface Props {
  /** The "Addison's work" annotation block (ActivityPanel), when there's work. */
  work?: ReactNode;
  /** The consent card (PermissionCard), when a permission is pending. */
  consent?: ReactNode;
}

export function WidgetRail({ work, consent }: Props) {
  return (
    <aside
      aria-label="Your widgets"
      className="thread-scroll flex w-[270px] shrink-0 flex-col gap-[22px] overflow-y-auto py-[30px]"
    >
      <div>
        <div className="mb-2.5 flex items-baseline justify-between">
          <p className="text-[10.5px] font-semibold uppercase tracking-[0.11em] text-faint">
            Your widgets
          </p>
          <button
            type="button"
            title="Coming soon"
            className="text-[11.5px] font-medium text-muted hover:text-ink-soft"
          >
            Edit
          </button>
        </div>

        <div className="flex flex-col gap-2">
          {/* Real widget cards arrive in a later PR; the rail ships empty but for
              the standing "ask Addison to build one" affordance. */}
          <button
            type="button"
            title="Coming soon"
            className="rounded-card border border-dashed border-dash bg-transparent px-2.5 py-2 text-center text-[11.5px] font-medium text-muted hover:opacity-80"
          >
            ＋ Ask Addison to build a widget
          </button>
        </div>
      </div>

      {(work || consent) && (
        <div>
          {work}
          {consent && <div className="mt-3.5">{consent}</div>}
        </div>
      )}
    </aside>
  );
}
