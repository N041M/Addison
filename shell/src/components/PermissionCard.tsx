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

export function PermissionCard({ request, onRespond }: Props) {
  return (
    <div className="rounded-card bg-fern-tint px-[15px] py-[13px]">
      <p className="text-meta font-semibold leading-snug text-ink">{request.label}</p>
      <p className="mt-1 text-fine leading-relaxed text-ink-soft">{request.description}</p>
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
