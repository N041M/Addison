// Model picker — sits beside the message input (design-doc §7.3.3, §7.3.4).
//
// One compact, obvious control. A single dropdown lists the cloud models by
// their names (e.g. "Claude Opus 4.8") — every model the configured key can
// access — and, when a model runs on this computer too, those under a plain "On
// this computer" divider. Choosing an entry sets the role AND the model together,
// so there is no separate Cloud/On-this-computer toggle to reason about.
//
// When the chosen model offers levels of effort, a small control appears next to
// it, labelled by the API's own effort ids (low / high / xhigh, …). It is hidden
// entirely for models with no effort control, and for models on this computer.
//
// A one-line plain description of the chosen model rides along in the dropdown's
// title, so it's there on hover/read-out without cluttering the row.
//
// Visual direction is binding (CLAUDE.md): dark terminal-adjacent surfaces, sharp
// corners, one restrained steel-blue accent, system-monospace for the model name,
// plain language for readers who are 54 and 68 — never a generic AI-chat look.

import type { ModelRole } from "../types/protocol";
import type { CloudModel, RoleOption } from "../types/ui";

interface Props {
  roles: RoleOption[];
  cloudModels: CloudModel[];
  selectedRole: ModelRole;
  selectedCloudModel?: string;
  selectedLocalModel?: string;
  selectedEffort?: string;
  /** Choose a model — carries its role (cloud = "primary", local) with it. */
  onSelectModel: (role: ModelRole, modelId: string) => void;
  onSelectEffort: (effort: string) => void;
  disabled?: boolean;
}

// Shown only when there's no real catalog yet — e.g. opened in a plain browser
// for design review, before the engine is connected. It keeps the control
// visible and laid out (and inert), rather than vanishing. The engine always
// replaces this with the core's real catalog once connected.
const PLACEHOLDER_CLOUD: CloudModel[] = [
  {
    id: "claude-opus-4-8",
    label: "Claude Opus 4.8",
    description: "",
    effortLevels: [
      { id: "low", label: "low" },
      { id: "high", label: "high" },
      { id: "xhigh", label: "xhigh" },
    ],
    default: true,
  },
  {
    id: "claude-sonnet-5",
    label: "Claude Sonnet 5",
    description: "",
    effortLevels: [
      { id: "low", label: "low" },
      { id: "high", label: "high" },
      { id: "xhigh", label: "xhigh" },
    ],
    default: false,
  },
  {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    description: "",
    effortLevels: [],
    default: false,
  },
];

// A dropdown value packs role + model id. Role has no colon and comes first, so
// we split on the FIRST colon only — local (Ollama) ids like "llama3.1:8b"
// keep their own colons intact.
function encode(role: ModelRole, id: string): string {
  return `${role}:${id}`;
}
function decode(value: string): { role: ModelRole; id: string } {
  const i = value.indexOf(":");
  return { role: value.slice(0, i) as ModelRole, id: value.slice(i + 1) };
}

function defaultCloud(models: CloudModel[]): CloudModel | undefined {
  return models.find((m) => m.default) ?? models[0];
}

export function ModelSelector({
  roles,
  cloudModels,
  selectedRole,
  selectedCloudModel,
  selectedLocalModel,
  selectedEffort,
  onSelectModel,
  onSelectEffort,
  disabled,
}: Props) {
  const localModels = roles.find((r) => r.role === "local" && r.configured)?.models ?? [];

  // Nothing real to offer yet (disconnected design review): fall back to inert
  // placeholders so the row still reads as part of the composer.
  const usingPlaceholder = cloudModels.length === 0 && localModels.length === 0;
  const cloud = usingPlaceholder ? PLACEHOLDER_CLOUD : cloudModels;
  const locals = usingPlaceholder ? [] : localModels;
  const isDisabled = Boolean(disabled) || usingPlaceholder;

  // The model that's currently in effect, and its plain description.
  const activeCloud =
    cloud.find((m) => m.id === selectedCloudModel) ?? defaultCloud(cloud);
  const activeLocalId = selectedLocalModel ?? locals[0]?.id;

  const onLocal = selectedRole === "local" && locals.length > 0;
  const currentValue = onLocal
    ? encode("local", activeLocalId ?? "")
    : encode("primary", activeCloud?.id ?? "");

  // Effort only applies to the chosen cloud model; local models never carry it.
  const effortLevels = onLocal ? [] : activeCloud?.effortLevels ?? [];
  const description = onLocal ? undefined : activeCloud?.description;

  // Which level reads as active. App keeps `selectedEffort` reconciled to the
  // model, but fall back to the middle/default level so a sensible one is always
  // lit — including inert placeholder mode, where App never reconciles it.
  const middleEffort =
    effortLevels.length > 0
      ? effortLevels[Math.floor(effortLevels.length / 2)].id
      : undefined;
  const activeEffort = effortLevels.some((l) => l.id === selectedEffort)
    ? selectedEffort
    : middleEffort;

  function handlePick(value: string) {
    const { role, id } = decode(value);
    if (!id) return;
    onSelectModel(role, id);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5">
        <span className="sr-only">Which model Addison uses</span>
        <select
          disabled={isDisabled}
          value={currentValue}
          title={description || undefined}
          onChange={(e) => handlePick(e.target.value)}
          className="border border-line bg-surface px-2.5 py-1.5 font-mono text-sm text-ink disabled:opacity-60"
        >
          {cloud.map((m) => (
            <option key={m.id} value={encode("primary", m.id)}>
              {m.label}
            </option>
          ))}
          {locals.length > 0 && (
            <optgroup label="On this computer">
              {locals.map((m) => (
                <option key={m.id} value={encode("local", m.id)}>
                  {m.label}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </label>

      {effortLevels.length > 0 && (
        <div
          role="group"
          aria-label="How thorough Addison should be"
          className="inline-flex border border-line bg-surface p-0.5"
        >
          {effortLevels.map((level) => {
            const active = activeEffort === level.id;
            return (
              <button
                key={level.id}
                type="button"
                disabled={isDisabled}
                aria-pressed={active}
                onClick={() => onSelectEffort(level.id)}
                className={[
                  "px-2.5 py-1 text-sm font-medium transition-colors",
                  active
                    ? "bg-accent-tint text-accent-dark"
                    : "text-muted hover:text-ink",
                  isDisabled ? "cursor-not-allowed opacity-60" : "",
                ].join(" ")}
              >
                {level.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
