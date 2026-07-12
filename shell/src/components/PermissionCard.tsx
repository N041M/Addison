// Consent card — the plain-language permission prompt (design-doc §7.4, engineering-spec §4.3).
// Rendered when the Core emits permission.requestGrant; blocks the tool call
// until the user answers. Low-risk = approve once/remembered; medium-risk =
// confirm each distinct action with a preview of exactly what will happen.

import type { PermissionRequest } from "../types/protocol";

interface Props {
  request: PermissionRequest;
  onRespond: (allow: boolean) => void;
}

export function PermissionCard({ request, onRespond }: Props) {
  return (
    <div className={`permission-card risk-${request.riskTier}`}>
      <h3>{request.label}</h3>
      <p>{request.description}</p>
      <div className="permission-actions">
        <button onClick={() => onRespond(true)}>Allow</button>
        <button onClick={() => onRespond(false)}>Not now</button>
      </div>
    </div>
  );
}
