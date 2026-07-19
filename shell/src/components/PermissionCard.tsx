// Consent card — the plain-language permission prompt (design-doc §7.4,
// engineering-spec §4.3; Fern direction: design-brief-fern README §3).
//
// Rendered when the Core emits `permission.requestGrant`; blocks the tool call
// until the user answers. It sits in the widget rail (rail open) or inline in the
// thread (rail hidden). For the primary personas (54–68, non-technical) the
// answer must be ONE obvious tap: a big Allow, a plain "Not now", no risk codes
// or jargon. The tool provides its own plain label (the question) + description
// (the consequence); we never show a stack trace or a raw tool id.

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
    <div className="animate-[fade-rise_160ms_ease-out] rounded-card bg-fern-tint px-[15px] py-[13px]">
      <p className="text-meta font-semibold leading-snug text-ink">{request.label}</p>
      <p className="mt-1 text-fine leading-relaxed text-ink-soft">{lead}</p>
      {command && (
        <p
          title={command}
          className="mt-1.5 truncate rounded-sm bg-surface px-2 py-1 font-mono text-hint text-ink"
        >
          {command}
        </p>
      )}
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => onRespond(true)}
          className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep"
        >
          Allow
        </button>
        <button
          type="button"
          onClick={() => onRespond(false)}
          className="text-xs font-medium text-ink-soft hover:text-muted"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
