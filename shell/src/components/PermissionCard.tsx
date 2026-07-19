// Consent card — the plain-language permission prompt (design-doc §7.4, engineering-spec §4.3).
//
// Rendered inline in the thread when the Core emits `permission.requestGrant`;
// blocks the tool call until the user answers. For the primary personas (54–68,
// non-technical) the answer must be ONE obvious tap: a big Allow, a plain "Not
// now", no risk codes or jargon. The tool provides its own plain label +
// description; we never show a stack trace or a raw tool id.

import type { PermissionRequest } from "../types/protocol";

interface Props {
  request: PermissionRequest;
  onRespond: (allow: boolean) => void;
}

export function PermissionCard({ request, onRespond }: Props) {
  return (
    <div className="rounded-card bg-fern-tint p-4">
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-fern-deep">
        Addison is asking
      </p>
      <h3 className="mt-1.5 text-[12.5px] font-semibold leading-snug text-ink">
        {request.label}
      </h3>
      <p className="mt-1 text-[11.5px] leading-relaxed text-ink-soft">
        {request.description}
      </p>
      <div className="mt-3.5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => onRespond(true)}
          className="rounded-pill bg-fern px-5 py-1.5 text-[13px] font-semibold text-on-accent hover:bg-fern-deep"
        >
          Allow
        </button>
        <button
          type="button"
          onClick={() => onRespond(false)}
          className="text-[13px] font-medium text-muted hover:text-ink-soft"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
