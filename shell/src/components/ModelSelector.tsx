// The composer's model label + its menu (DARK direction; docs/design-brief-dark,
// "Screens → Composer model menu"). Replaces the Fern popover.
//
// The label is a mono 10.5px machine fact at the right of the composer row;
// clicking it opens a small panel ABOVE it, anchored bottom-right: an "Answer
// with" header over rows of `name … note`, where the note reads `local` for a
// model on this computer and `quality` for a cloud one, and the selected row
// carries a ✓ in accent. When the chosen cloud model offers levels of effort, an
// "Effort" section follows in the same row idiom. A footer hint says where the
// permanent choice lives: this menu is per message.
//
// ROWS ARE THE REAL CATALOG. Every entry comes from `model.availableRoles` —
// cloud models from the connected providers, plus whatever is set up under the
// local role. `free` is never assumed: the note says so only if the core's own
// flag says so, and no cloud model carries it today (agent_core's CloudModel
// keeps `free` off the wire, and the free chip stays Ollama-only — CLAUDE.md,
// Phase-2 step 4). Inventing it here would be the app claiming a model costs
// nothing on its own authority.
//
// ACCESSIBILITY IS NOT RESTYLED AWAY. The readers are 54 and 68: the label stays
// a real button (aria-haspopup="listbox"), the model list stays a role="listbox"
// with role="option" rows and Arrow/Home/End/Enter, focus moves into the list on
// open and back to the label on close, and outside-click dismisses.
//
// TAB WALKS THE PANEL, it does not leave it. Tab used to close the menu
// outright, which meant the three `aria-pressed` Effort buttons could never take
// focus: a keyboard user could choose a model but never the effort the composer
// label is advertising back at them ("Claude Opus 4.8 · high"). Tab now cycles
// list → effort → list; Escape is the way out and returns focus to the label.
// When there is no Effort section there is nothing to cycle to, so Tab keeps its
// old behaviour and leaves.

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { isMotionEnabled } from "../lib/scramble";
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

/** One selectable row in the menu, flattened across cloud + local. */
interface Option {
  role: ModelRole;
  id: string;
  /** Row label (may carry a provider suffix when several providers connected). */
  label: string;
  /** Compact label for the composer's own label (never provider-suffixed). */
  pillLabel: string;
  /** The mono note at the right of the row: "local" | "free" | "quality". */
  note: string;
  current: boolean;
}

/** How long the menu takes to fade back down. Shorter than the .2s it rises on:
 * an exit that matches its entrance reads as sluggish, because nobody is waiting
 * to read anything on the way out. */
const MENU_EXIT_MS = 140;

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
  // the label is dimmed but the menu still opens so the catalog is *browsable*;
  // picking is a no-op until a real engine is connected).
  const blockOpen = Boolean(disabled);
  const dimmed = Boolean(disabled) || usingPlaceholder;

  // The model that's currently in effect.
  const activeCloud = cloud.find((m) => m.id === selectedCloudModel) ?? defaultCloud(cloud);
  const activeLocalId = selectedLocalModel ?? locals[0]?.id;

  const onLocal = selectedRole === "local" && locals.length > 0;

  // Effort only applies to the chosen cloud model; local models never carry it.
  const effortLevels = onLocal ? [] : (activeCloud?.effortLevels ?? []);

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
  const providerCount = new Set(cloud.map((m) => m.provider).filter((p): p is string => Boolean(p)))
    .size;
  const cloudRowLabel = (m: CloudModel) =>
    providerCount > 1 && m.providerLabel ? `${m.label} — ${m.providerLabel}` : m.label;

  const options: Option[] = [
    ...cloud.map((m) => ({
      role: "primary" as ModelRole,
      id: m.id,
      label: cloudRowLabel(m),
      pillLabel: m.label,
      // Only the core may call a model free (see the file header).
      note: m.free ? "free" : "quality",
      current: !onLocal && m.id === activeCloud?.id,
    })),
    ...locals.map((m) => ({
      role: "local" as ModelRole,
      id: m.id,
      label: m.label,
      pillLabel: m.label,
      note: "local",
      current: onLocal && m.id === activeLocalId,
    })),
  ];
  const currentIndex = Math.max(
    0,
    options.findIndex((o) => o.current),
  );

  // The composer's label: the active model, plus its effort word when it has one.
  const activeLabel = onLocal
    ? (locals.find((m) => m.id === activeLocalId)?.label ?? activeLocalId ?? "Model")
    : (activeCloud?.label ?? "Model");
  const activeEffortLabel = effortLevels.find((l) => l.id === activeEffort)?.label;

  // ---- Menu open/close + keyboard state -----------------------------------
  const [open, setOpen] = useState(false);
  // The menu stays mounted for the length of its exit. Without this it faded and
  // rose IN and then vanished on a single frame when it closed — "it just plops
  // out" (reported 2026-07-26). Every close path goes through `setOpen(false)`
  // (Escape, an outside click, picking a model or an effort, the composer being
  // blocked mid-turn), so driving the exit off `open` covers all of them.
  const [exiting, setExiting] = useState(false);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      setExiting(false);
      return;
    }
    if (!wasOpen.current) return; // never opened, nothing to animate away
    wasOpen.current = false;
    if (!isMotionEnabled()) return;
    setExiting(true);
    const timer = setTimeout(() => setExiting(false), MENU_EXIT_MS);
    return () => clearTimeout(timer);
  }, [open]);
  // DERIVED, not state: the panel has to exist on the very render that opens it,
  // or the effect below has nothing to move focus to and the menu opens with
  // focus stranded on <body>. Asking for it a render later cost exactly that.
  const menuPresent = open || exiting;
  const [activeIndex, setActiveIndex] = useState(currentIndex);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const labelRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const effortRef = useRef<HTMLDivElement | null>(null);
  const listboxId = useId();
  const optionId = (i: number) => `${listboxId}-opt-${i}`;

  // On open, aim the highlight at the current selection and move focus into the
  // list (listbox pattern with aria-activedescendant).
  useLayoutEffect(() => {
    if (open) {
      setActiveIndex(currentIndex);
      listRef.current?.focus();
    }
    // Only re-run when the menu toggles.
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

  // A turn starting while the menu is open would leave a menu over a composer
  // that can no longer act on it.
  useEffect(() => {
    if (blockOpen) setOpen(false);
  }, [blockOpen]);

  function close(returnFocus = true) {
    setOpen(false);
    if (returnFocus) labelRef.current?.focus();
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
      default:
        break;
    }
    // Escape and Tab are handled once, on the panel, so they behave the same
    // whether focus is on the list or on an Effort button.
  }

  /** The panel's tab ring, in DOM order: the model listbox, then each Effort
   * button. */
  function panelStops(): HTMLElement[] {
    const stops: HTMLElement[] = [];
    if (listRef.current) stops.push(listRef.current);
    if (effortRef.current) {
      stops.push(...Array.from(effortRef.current.querySelectorAll<HTMLElement>("button")));
    }
    return stops;
  }

  function onPanelKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const stops = panelStops();
    if (stops.length < 2) {
      // Nothing else in here to reach (a model with no effort levels, or a local
      // one): let focus leave naturally and take the menu with it, as before.
      close(false);
      return;
    }
    e.preventDefault();
    const i = stops.indexOf(document.activeElement as HTMLElement);
    const next = e.shiftKey
      ? stops[(i <= 0 ? stops.length : i) - 1]
      : stops[(i + 1) % stops.length];
    next.focus();
  }

  return (
    <span ref={rootRef} className="relative inline-flex min-w-0">
      <button
        ref={labelRef}
        type="button"
        data-scramble="440"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={blockOpen}
        aria-label={
          activeEffortLabel
            ? `Model: ${activeLabel}, effort ${activeEffortLabel}. Choose model and effort.`
            : `Model: ${activeLabel}. Choose model.`
        }
        onClick={() => !blockOpen && setOpen((v) => !v)}
        className={
          // Narrower on a phone: the label shares one row with the textarea, and
          // a full model name there squeezes the message down to a keyhole.
          "max-w-[110px] truncate font-mono text-[10.5px] transition-colors max-md:min-h-[44px] md:max-w-[180px] " +
          (open ? "text-muted" : "text-disabled hover:text-muted ") +
          // Dark only. On light, `disabled` at 60% measured 1.55:1 against the
          // paper — the label that names the model in effect was the least
          // readable text in the app. The light ramp carries the dimming now.
          (dimmed ? " dark:opacity-60" : "") +
          (blockOpen ? " cursor-not-allowed" : "")
        }
      >
        {activeLabel}
        {activeEffortLabel ? ` · ${activeEffortLabel.toLowerCase()}` : ""}
      </button>

      {menuPresent && (
        <div
          onKeyDown={onPanelKeyDown}
          // `w-max` over the 196px floor: a model picker that abbreviates the
          // model name ("Claude Opus 4…") is asking people to choose between
          // things it won't show them. The panel grows to its content and only
          // truncates past 320px, where a provider-suffixed name would otherwise
          // push the menu off the column.
          //
          // The height clamp is the viewport one: the menu opens upward from the
          // composer, so in a short window it ran past the top of the app and
          // over the header (measured at 1280×420: menu top 76, header bottom
          // 77). It scrolls instead of climbing.
          className={
            "no-scrollbar absolute bottom-[26px] right-0 z-30 flex max-h-[calc(100vh-160px)] w-max min-w-[196px] max-w-[320px] flex-col overflow-y-auto rounded-[6px] border border-rail bg-panel p-1.5 shadow-menu " +
            (open
              ? "animate-[fadeRise_.2s_ease_both]"
              : "pointer-events-none animate-[fadeDrop_.14s_ease_both]")
          }
          role="presentation"
          // While it fades out the menu is a picture, not a control: out of the
          // a11y tree, and not clickable. Screen readers and tests both see it
          // gone the moment it closes, which is what "closed" should mean.
          aria-hidden={!open}
        >
          <div className="px-2.5 pb-2 pt-1.5 text-[10px] font-medium tracking-[.04em] text-faint">
            Answer with
          </div>

          <div
            ref={listRef}
            role="listbox"
            tabIndex={-1}
            aria-label="Which model Addison uses"
            aria-activedescendant={optionId(activeIndex)}
            onKeyDown={onListKeyDown}
            className="no-scrollbar max-h-[40vh] overflow-y-auto outline-none"
          >
            {options.map((o, i) => (
              <div
                key={`${o.role}:${o.id}`}
                id={optionId(i)}
                role="option"
                aria-selected={o.current}
                onClick={() => pickModel(o)}
                onMouseEnter={() => setActiveIndex(i)}
                className={
                  "flex cursor-pointer items-baseline gap-2.5 rounded-[4px] px-2.5 py-[7px] " +
                  (i === activeIndex ? "bg-line" : "")
                }
              >
                <span
                  className={
                    "min-w-0 truncate font-mono text-[10.5px] " +
                    (o.current ? "text-ink" : "text-muted")
                  }
                >
                  {o.label}
                </span>
                <span className="flex-1" />
                <span
                  className={
                    "shrink-0 font-mono text-[10px] " +
                    (o.current ? "text-accent" : "text-disabled")
                  }
                >
                  {o.current ? `${o.note} ✓` : o.note}
                </span>
              </div>
            ))}
          </div>

          {effortLevels.length > 0 && (
            <>
              <div className="px-2.5 pb-2 pt-3 text-[10px] font-medium tracking-[.04em] text-faint">
                Effort
              </div>
              <div
                ref={effortRef}
                role="group"
                aria-label="How thorough Addison should be"
              >
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
                      className="flex w-full items-baseline gap-2.5 rounded-[4px] px-2.5 py-[7px] text-left transition-colors hover:bg-line"
                    >
                      <span
                        className={
                          "min-w-0 truncate font-mono text-[10.5px] " +
                          (active ? "text-ink" : "text-muted")
                        }
                      >
                        {level.label}
                      </span>
                      <span className="flex-1" />
                      {active && (
                        <span className="shrink-0 font-mono text-[10px] text-accent">✓</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </>
          )}

          <div className="mt-1.5 border-t border-line px-2.5 pb-1 pt-2 font-mono text-[10px] text-disabled">
            picked per message · default in Settings
          </div>
        </div>
      )}
    </span>
  );
}
