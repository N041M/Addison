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
    <div className="my-2 rounded-card border border-accent/40 bg-accent-tint/60 p-4 shadow-card">
      <p className="text-xs font-semibold uppercase tracking-wide text-accent-dark">
        Addison is asking
      </p>
      <h3 className="mt-1 text-lg font-semibold text-ink">{request.label}</h3>
      <p className="mt-1 text-base leading-relaxed text-ink-soft">
        {request.description}
      </p>
      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={() => onRespond(true)}
          className="rounded-lg bg-accent px-5 py-2.5 text-base font-semibold text-white hover:bg-accent-dark"
        >
          Allow
        </button>
        <button
          type="button"
          onClick={() => onRespond(false)}
          className="rounded-lg border border-line bg-surface px-5 py-2.5 text-base font-medium text-ink-soft hover:border-muted"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
