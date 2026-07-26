// Consent card — the plain-language permission prompt (design-doc §7.4,
// engineering-spec §4.3; DARK direction card language — a flat `panel` block
// with a 1px `rail` border and a 7px radius).
//
// Rendered when the Core emits `permission.requestGrant`; blocks the tool call
// until the user answers. It sits in the widget rail (rail open) or inline in the
// thread (rail hidden). For the primary personas (54–68, non-technical) the
// answer must be ONE obvious choice: an accent Allow, a plain "Not now", no risk
// codes or jargon. The tool provides its own plain label (the question) +
// description (the consequence); we never show a stack trace or a raw tool id.
//
// EVERY SENTENCE HERE IS THE CORE'S, VERBATIM. The redesign changed the skin and
// nothing else: the question, the consequence and the exact command text are
// rendered as sent, and both answers stay one press away.

import type { PermissionRequest } from "../types/protocol";

interface Props {
  request: PermissionRequest;
  onRespond: (allow: boolean) => void;
}

// Per-invocation destructive cards (OPEN/Developer mode) describe the exact
// command each time, phrased by the core as "…run: <command>". When we see that
// shape we split the command off and set it as a machine fact (mono, inset chip)
// so it reads as data, not prose. SAFE-mode cards have no "run: " and render
// exactly as before.
const RUN_PREFIX = "run: ";

function splitCommand(description: string): { lead: string; command: string | null } {
  const at = description.indexOf(RUN_PREFIX);
  if (at === -1) return { lead: description, command: null };
  const command = description.slice(at + RUN_PREFIX.length).trim();
  if (!command) return { lead: description, command: null };
  return { lead: description.slice(0, at + RUN_PREFIX.length).trimEnd(), command };
}

export function PermissionCard({ request, onRespond }: Props) {
  const { lead, command } = splitCommand(request.description);
  return (
    <div className="animate-[fadeRise_.2s_ease_both] rounded-[7px] border border-rail bg-panel px-3.5 py-3">
      <p className="m-0 text-[12px] font-medium leading-[1.45] text-ink">{request.label}</p>
      <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-ink-soft">{lead}</p>
      {command && (
        <p
          title={command}
          className="m-0 mt-2 truncate rounded-[4px] bg-paper px-2 py-1 font-mono text-[10.5px] text-ink"
        >
          {command}
        </p>
      )}
      {/* Both answers are REAL targets, on every screen. These two buttons are
          what authorises a destructive command, and they were 14.5px tall on
          desktop — under WCAG 2.2 SC 2.5.8's 24×24 floor, and separated only by
          hue, for readers of 54 and 68. They are now 30px (the send button's
          desktop size) and 44px under `md`, with Allow carrying the accent FILL
          so the one obvious choice stays obvious without the copy changing.
          Every sentence on this card is still the core's, verbatim. */}
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={() => onRespond(true)}
          className="inline-flex h-[30px] shrink-0 items-center justify-center rounded-menu bg-accent px-3.5 text-[12px] font-medium text-on-accent transition-opacity hover:opacity-90 max-md:h-11"
        >
          Allow
        </button>
        <button
          type="button"
          onClick={() => onRespond(false)}
          className="inline-flex h-[30px] shrink-0 items-center justify-center rounded-menu px-2.5 text-[12px] text-muted transition-colors hover:bg-track hover:text-ink max-md:h-11"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
