// Model role picker — sits beside the message input (design-doc §7.3.3, §7.3.4).
//
// Rules (from the step-7 brief):
//   - Hidden entirely unless more than one role is actually configured.
//   - When both Cloud and On-this-computer are available: a plain two-way
//     toggle (no jargon, no model names for the primary personas).
//   - A model dropdown appears ONLY within "On this computer" and ONLY when
//     several local models exist (§7.3.4). Automatic choice among them is v2.

import type { ModelRole } from "../types/protocol";
import type { RoleOption } from "../types/ui";

interface Props {
  roles: RoleOption[];
  selectedRole: ModelRole;
  selectedLocalModel?: string;
  onSelectRole: (role: ModelRole) => void;
  onSelectLocalModel: (modelId: string) => void;
  disabled?: boolean;
}

export function ModelSelector({
  roles,
  selectedRole,
  selectedLocalModel,
  onSelectRole,
  onSelectLocalModel,
  disabled,
}: Props) {
  const configured = roles.filter((r) => r.configured);

  // Hide the whole selector when there's nothing to choose between.
  if (configured.length <= 1) return null;

  const localRole = configured.find((r) => r.role === "local");
  const showModelDropdown =
    selectedRole === "local" && (localRole?.models?.length ?? 0) > 1;

  return (
    <div className="flex items-center gap-2">
      <div
        role="group"
        aria-label="Where Addison thinks"
        className="inline-flex rounded-lg border border-line bg-surface p-0.5"
      >
        {configured.map((r) => {
          const active = r.role === selectedRole;
          return (
            <button
              key={r.role}
              type="button"
              disabled={disabled}
              aria-pressed={active}
              onClick={() => onSelectRole(r.role)}
              className={[
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                active
                  ? "bg-accent-tint text-accent-dark"
                  : "text-muted hover:text-ink",
                disabled ? "cursor-not-allowed opacity-60" : "",
              ].join(" ")}
            >
              {r.label}
            </button>
          );
        })}
      </div>

      {showModelDropdown && localRole?.models && (
        <label className="flex items-center gap-1.5 text-sm text-muted">
          <span className="sr-only">Which local model</span>
          <select
            disabled={disabled}
            value={selectedLocalModel ?? localRole.models[0]?.id}
            onChange={(e) => onSelectLocalModel(e.target.value)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink"
          >
            {localRole.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}
