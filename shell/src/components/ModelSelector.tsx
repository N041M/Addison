// Model picker — sits at the bottom of the composer (design-doc §7.3.3, §7.3.4;
// Fern direction, docs/design-brief-fern README §2).
//
// One compact control: a muted IBM Plex Mono text pill reading
// "«model» · «effort» ▾" that opens a small popover *upward* (it lives at the
// bottom of the screen). The popover lists the cloud models by their names
// (e.g. "Claude Opus 4.8") — every model the configured key can access — and,
// when a model runs on this computer too, those under a plain "On this computer"
// group. Choosing an entry sets the role AND the model together, so there is no
// separate Cloud/On-this-computer toggle to reason about.
//
// When the chosen model offers levels of effort, a small segmented control
// appears below a hair divider, labelled by the API's own plain wording
// (e.g. "thorough"). It is hidden entirely for models with no effort control,
// and for models on this computer.
//
// This replaces a native <select> + a separate effort pill group, so it must not
// regress accessibility for readers who are 54 and 68: the pill is a real button
// (aria-haspopup="listbox"), the menu is a role="listbox" with role="option"
// rows and full keyboard support (Arrow/Home/End/Enter/Escape), outside-click and
// Tab close it, and focus moves into the list on open and back to the pill on
// close.
//
// Visual direction is binding (CLAUDE.md; Fern direction): the model name/tag in
// IBM Plex Mono (a "machine fact"), one fern-green accent for the selected row,
// plain language — never a generic AI-chat look.

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
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
    effortLevels: [],
    default: false,
  },
];

function defaultCloud(models: CloudModel[]): CloudModel | undefined {
  return models.find((m) => m.default) ?? models[0];
}

/** One selectable row in the popover, flattened across cloud + local. */
interface Option {
  role: ModelRole;
  id: string;
  /** Row label (may carry a provider suffix when several providers connected). */
  label: string;
  /** Compact label for the pill itself (never provider-suffixed). */
  pillLabel: string;
  current: boolean;
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
  // Two distinct notions: `blockOpen` (a turn is running — don't let the picker
  // open at all) vs `dimmed`/inert placeholder mode (disconnected design review —
  // the pill is dimmed but the popover still opens so the catalog is *browsable*;
  // picking is a no-op until a real engine is connected).
  const blockOpen = Boolean(disabled);
  const dimmed = Boolean(disabled) || usingPlaceholder;

  // The model that's currently in effect.
  const activeCloud = cloud.find((m) => m.id === selectedCloudModel) ?? defaultCloud(cloud);
  const activeLocalId = selectedLocalModel ?? locals[0]?.id;

  const onLocal = selectedRole === "local" && locals.length > 0;

  // Effort only applies to the chosen cloud model; local models never carry it.
  const effortLevels = onLocal ? [] : activeCloud?.effortLevels ?? [];

  // Which level reads as active. App keeps `selectedEffort` reconciled to the
  // model, but fall back to the middle/default level so a sensible one is always
  // lit — including inert placeholder mode, where App never reconciles it.
  const middleEffort =
    effortLevels.length > 0 ? effortLevels[Math.floor(effortLevels.length / 2)].id : undefined;
  const activeEffort = effortLevels.some((l) => l.id === selectedEffort)
    ? selectedEffort
    : middleEffort;

  // Attribute each model to its provider ("GPT-4.1 — OpenAI") only when more than
  // one provider is connected — with a single provider the suffix is just noise.
  const providerCount = new Set(
    cloud.map((m) => m.provider).filter((p): p is string => Boolean(p)),
  ).size;
  const cloudRowLabel = (m: CloudModel) =>
    providerCount > 1 && m.providerLabel ? `${m.label} — ${m.providerLabel}` : m.label;

  const localHeaderShown = locals.length > 0;
  const options: Option[] = [
    ...cloud.map((m) => ({
      role: "primary" as ModelRole,
      id: m.id,
      label: cloudRowLabel(m),
      pillLabel: m.label,
      current: !onLocal && m.id === activeCloud?.id,
    })),
    ...locals.map((m) => ({
      role: "local" as ModelRole,
      id: m.id,
      label: m.label,
      pillLabel: m.label,
      current: onLocal && m.id === activeLocalId,
    })),
  ];
  const firstLocalIndex = localHeaderShown ? cloud.length : -1;
  const currentIndex = Math.max(
    0,
    options.findIndex((o) => o.current),
  );

  // The pill text: the active model's compact label, plus its effort word when
  // the model has one. The screenshot reads "Claude Opus 4.8 · thorough ▾".
  const pillModelLabel = onLocal
    ? locals.find((m) => m.id === activeLocalId)?.label ?? activeLocalId ?? "Model"
    : activeCloud?.label ?? "Model";
  const activeEffortLabel = effortLevels.find((l) => l.id === activeEffort)?.label;

  // ---- Popover open/close + keyboard state --------------------------------
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(currentIndex);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const pillRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const listboxId = useId();
  const optionId = (i: number) => `${listboxId}-opt-${i}`;

  // On open, aim the highlight at the current selection and move focus into the
  // list (listbox pattern with aria-activedescendant).
  useLayoutEffect(() => {
    if (open) {
      setActiveIndex(currentIndex);
      listRef.current?.focus();
    }
    // Only re-run when the popover toggles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Keep the highlighted row scrolled into view as the user arrows through.
  useEffect(() => {
    if (!open) return;
    document.getElementById(optionId(activeIndex))?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex, open]);

  // Outside-click closes.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function close(returnFocus = true) {
    setOpen(false);
    if (returnFocus) pillRef.current?.focus();
  }

  function pickModel(o: Option) {
    // Inert while showing placeholders (disconnected): browse, but don't commit.
    if (!usingPlaceholder) onSelectModel(o.role, o.id);
    close();
  }

  function onListKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % options.length);
        break;
      case "ArrowUp":
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + options.length) % options.length);
        break;
      case "Home":
        e.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        e.preventDefault();
        setActiveIndex(options.length - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (options[activeIndex]) pickModel(options[activeIndex]);
        break;
      case "Escape":
        e.preventDefault();
        close();
        break;
      case "Tab":
        // Let focus leave naturally, but dismiss the popover.
        close(false);
        break;
      default:
        break;
    }
  }

  return (
    <div ref={rootRef} className="relative min-w-0">
      <button
        ref={pillRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={blockOpen}
        aria-label={
          activeEffortLabel
            ? `Model: ${pillModelLabel}, effort ${activeEffortLabel}. Choose model and effort.`
            : `Model: ${pillModelLabel}. Choose model.`
        }
        onClick={() => !blockOpen && setOpen((v) => !v)}
        className={[
          "flex max-w-full items-center gap-1 rounded-sm px-1 py-0.5 font-mono text-hint transition-colors",
          "text-muted hover:text-ink-soft focus:outline-none focus-visible:ring-2 focus-visible:ring-fern/40",
          "max-md:min-h-[44px] max-md:px-1.5",
          dimmed ? "opacity-60" : "",
          blockOpen ? "cursor-not-allowed" : "",
        ].join(" ")}
      >
        <span className="truncate">
          {pillModelLabel}
          {activeEffortLabel ? ` · ${activeEffortLabel.toLowerCase()}` : ""}
        </span>
        <span aria-hidden="true" className="shrink-0">
          ▾
        </span>
      </button>

      {open && (
        <div
          className="absolute bottom-full left-0 z-20 mb-1.5 min-w-[240px] max-w-[320px] animate-[fade-rise_140ms_ease-out] overflow-hidden rounded-card border border-line bg-surface shadow-soft"
          role="presentation"
        >
          <div
            ref={listRef}
            role="listbox"
            tabIndex={-1}
            aria-label="Which model Addison uses"
            aria-activedescendant={optionId(activeIndex)}
            onKeyDown={onListKeyDown}
            className="max-h-[40vh] overflow-y-auto py-1 focus:outline-none thread-scroll"
          >
            {options.map((o, i) => (
              <div key={`${o.role}:${o.id}`}>
                {i === firstLocalIndex && (
                  <div className="px-3 pb-1 pt-2 text-label font-semibold uppercase tracking-caps-wide text-faint">
                    On this computer
                  </div>
                )}
                <div
                  id={optionId(i)}
                  role="option"
                  aria-selected={o.current}
                  onClick={() => pickModel(o)}
                  onMouseEnter={() => setActiveIndex(i)}
                  className={[
                    "mx-1 flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 font-mono text-hint",
                    o.current ? "bg-fern-tint text-fern-deep" : "text-ink-soft",
                    i === activeIndex && !o.current ? "bg-hair" : "",
                  ].join(" ")}
                >
                  <span className="w-3 shrink-0 text-fern-deep" aria-hidden="true">
                    {o.current ? "✓" : ""}
                  </span>
                  <span className="truncate">{o.label}</span>
                </div>
              </div>
            ))}
          </div>

          {effortLevels.length > 0 && (
            <div className="border-t border-line px-2.5 py-2">
              <div className="px-0.5 pb-1.5 text-label font-semibold uppercase tracking-caps-wide text-faint">
                Effort
              </div>
              <div role="group" aria-label="How thorough Addison should be" className="flex gap-1">
                {effortLevels.map((level) => {
                  const active = activeEffort === level.id;
                  return (
                    <button
                      key={level.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => {
                        if (!usingPlaceholder) onSelectEffort(level.id);
                      }}
                      className={[
                        "rounded-sm px-2.5 py-1 text-hint font-medium transition-colors",
                        active
                          ? "bg-fern-tint text-fern-deep"
                          : "text-muted hover:bg-hair hover:text-ink",
                      ].join(" ")}
                    >
                      {level.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
