// Addison — top-level app shell (DARK direction; docs/design-brief-dark).
//
// One full-width header over three columns: the conversation Sidebar (212px),
// the chat column, and the hideable widget rail (232px). Both side columns
// collapse by animating width/opacity/margin/translateX, so hiding one widens
// the middle rather than leaving a hole.
//
// FOUR SURFACES — Settings, Tools, Snapshots, Build a widget — replace the chat
// column (the rail hides entirely, the sidebar stays). `view` is the single
// state that says which one is showing; `changeView` owns the transition
// (children fadeDrop, commit at ~240ms) and the header's ← and Escape both
// route back to chat through it.
//
// This component owns the UI-chrome state and wires the Core → Frontend
// notifications (streamed text, permission prompts, tool activity, local-setup
// progress) into React state, and Frontend → Core actions back out through the
// typed `ipc`. The big state clusters live in dedicated hooks: useModelSelection,
// useWidgets, useTurn, useConversations, useSnapshots, useGuards, useRouting,
// useWorkspace, useOffers.
//
// Theme is class-driven and persisted in localStorage ("addison.theme") as one of
// "light" | "dark" | "system"; the default is now "system" (Match this computer).
//
// PHASE NOTE (redesign 1/4): the chat column's internals — ChatThread, Composer,
// the cards — and SettingsPage still carry their Fern-era styling. They are fully
// wired and rendering; phases 2–3 restyle them in place. Nothing here fakes a
// control or hides a real one to make the new chrome look finished.

import { useEffect, useMemo, useRef, useState } from "react";
import { Method, type PermissionRequest, type ActivityUpdate } from "./types/protocol";
import type { DisplayMessage, LocalSetupState, ProfileState, View } from "./types/ui";
import {
  ipc,
  isEngineConnected,
  subscribe,
  subscribeStatus,
  subscribeCoreState,
  subscribeDiagnostics,
  type StreamChunkParams,
  type LocalSetupProgressParams,
  type DiagnosticEntry,
} from "./ipc/client";
import { ChatThread } from "./components/ChatThread";
import { ActivityPanel } from "./components/ActivityPanel";
import { Sidebar } from "./components/Sidebar";
import { Surface, SurfaceSection, SurfaceRow, SURFACE_ID } from "./components/Surface";
import { WidgetRail } from "./components/WidgetRail";
import { WidgetProposalCard } from "./components/WidgetProposalCard";
import { EndpointProposalCard } from "./components/EndpointProposalCard";
import { CostPlanCard } from "./components/CostPlanCard";
import { Composer } from "./components/Composer";
import { PermissionCard } from "./components/PermissionCard";
import {
  RoutineProposalCard,
  type RoutineProposal,
} from "./components/RoutineProposalCard";
import { SettingsPage, API_KEYS_SECTION_ID } from "./components/SettingsPage";
import { FirstRunBanner } from "./components/FirstRunBanner";
import { Banner } from "./components/Banner";
import { MobileDrawer } from "./components/MobileDrawer";
import { useMediaQuery } from "./hooks/useMediaQuery";
import { useModelSelection } from "./hooks/useModelSelection";
import { useWidgets } from "./hooks/useWidgets";
import { useSkills } from "./hooks/useSkills";
import { useSnapshots } from "./hooks/useSnapshots";
import { useGuards } from "./hooks/useGuards";
import { useRouting } from "./hooks/useRouting";
import { useWorkspace } from "./hooks/useWorkspace";
import { useOffers } from "./hooks/useOffers";
import { useTurn } from "./hooks/useTurn";
import { useConversations } from "./hooks/useConversations";
import { asRecord, normalizeVariables, normalizeProfile } from "./lib/parse";
import { type ThemeChoice, parseThemeChoice, resolveTheme } from "./lib/theme";
import {
  INITIAL_SCRAMBLE_SELECTOR,
  installScrambleClickHandler,
  isMotionEnabled,
  scrambleAll,
  scrambleElement,
  useScrambleOnChange,
} from "./lib/scramble";

const THEME_KEY = "addison.theme";
const RAIL_OPEN_KEY = "addison.railOpen";
const SIDE_OPEN_KEY = "addison.sideOpen";

/** How long the leaving surface's fadeDrop runs before the new view commits. */
const VIEW_COMMIT_MS = 240;

// The header title for each surface. Chat shows the conversation's own title.
const SURFACE_TITLES: Record<Exclude<View, "chat">, string> = {
  settings: "Settings",
  tools: "Tools",
  snapshots: "Snapshots",
  widgets: "Build a widget",
};

// Seeds for the Build-a-widget surface. These are PROMPTS, not widgets: "use"
// writes the sentence into the composer and returns to chat, where the existing
// propose → card → confirm flow runs unchanged. Nothing is created by opening
// this page.
const WIDGET_IDEAS: { name: string; prompt: string }[] = [
  { name: "Bus departures from your stop", prompt: "bus departures from my stop" },
  { name: "Weather for the cottage weekend", prompt: "weather for the cottage this weekend" },
  { name: "Name-day calendar", prompt: "whose name day it is today" },
];

export function App() {
  const connected = useMemo(() => isEngineConnected(), []);

  const [hasUndoableActions, setHasUndoableActions] = useState(false);
  const [lastUndoDetail, setLastUndoDetail] = useState<string | null>(null);
  // Mirrors the core's session redo stack: set from undo/redo responses,
  // cleared whenever a new tool action lands (the core clears its stack too).
  const [canRedo, setCanRedo] = useState(false);
  // One-shot composer prefill for rewind's edit-and-resend.
  const [composerSeed, setComposerSeed] = useState<string | null>(null);

  const [statusBanner, setStatusBanner] = useState<string | null>(null);
  // Which in-window view is showing: the live chat, or one of the four surfaces.
  const [view, setView] = useState<View>("chat");
  const viewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seenView = useRef<View>("chat");
  // App-shell chrome, both persisted. The rail hosts the widget column + the
  // "Addison's work"/consent blocks; hiding it moves those inline. The sidebar
  // now collapses too (the header's «/» on the chat view).
  const [railOpen, setRailOpen] = useState<boolean>(() => loadBool(RAIL_OPEN_KEY, true));
  const [sideOpen, setSideOpen] = useState<boolean>(() => loadBool(SIDE_OPEN_KEY, true));

  // Narrow-window (mobile) layout. Below the md breakpoint (768px) the sidebar
  // becomes a slide-over drawer and the widget rail moves inline to the foot of
  // the chat thread (no side column fits). The drawer is ephemeral — deliberately
  // NOT persisted.
  const isMobile = useMediaQuery("(max-width: 767.98px)");
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Appearance — "light" | "dark" | "system" ("Match this computer", the
  // default). The class on <html> drives the whole palette. The inline script in
  // index.html sets it before first paint to avoid a flash; the effect below
  // keeps it in sync, persists the CHOICE, and (only while "system") follows the
  // OS live.
  const [themeChoice, setThemeChoiceState] = useState<ThemeChoice>(loadThemeChoice);
  const [routineProposal, setRoutineProposal] = useState<RoutineProposal | null>(null);

  // First-run experience. `startedUnconfigured` latches once — true iff this
  // launch began with nothing configured (or we're in a disconnected
  // design-review browser, where roles never load) — so connecting a provider
  // mid-launch advances the banner to step 2 rather than hiding it, while a
  // launch that began configured never shows it at all. `firstRunDismissed` is
  // "Skip for now": this launch only, deliberately not persisted.
  const [startedUnconfigured, setStartedUnconfigured] = useState<boolean | null>(null);
  const [firstRunDismissed, setFirstRunDismissed] = useState(false);
  // One-shot Settings scroll request (first-run "Start setup" → API-keys card).
  const [settingsScrollTarget, setSettingsScrollTarget] = useState<string | null>(null);
  // Bumped to focus the composer for the "say hello" nudge when first-run reaches
  // step 2 (a provider connected during this launch).
  const [composerFocusSignal, setComposerFocusSignal] = useState(0);

  // Profiles (§4.7). Simple by default; null until the core answers (and while
  // disconnected — the Settings section then shows a quiet placeholder).
  const [profile, setProfile] = useState<ProfileState | null>(null);
  // A small ring of the most recent raw diagnostics (Developer only). Captured
  // globally from client.ts regardless of profile; only rendered when the
  // raw-diagnostics flag is on, so Simple never sees it.
  const [diagnostics, setDiagnostics] = useState<DiagnosticEntry[]>([]);

  // --- The extracted state clusters ----------------------------------------
  const models = useModelSelection();
  const widgetsState = useWidgets({ connected, railOpen, setStatusBanner });
  const skillsState = useSkills({ connected, setStatusBanner });
  // Restore points (G3). The hook re-reads itself on every engine "ready", so it
  // isn't in the refresh list below; what it needs from here is the other way
  // round — a restore replaces the profile, the services and the saved items
  // wholesale, so everything this file cached from before it is now describing a
  // configuration that no longer exists.
  // HAZARD: `refreshProfile` is a forward reference; this closure only ever runs
  // at event time, after a restore has landed.
  const snapshotsState = useSnapshots({
    connected,
    onRestored: () => {
      models.refreshRoles();
      models.refreshProviders();
      refreshProfile();
      widgetsState.refreshWidgets();
      widgetsState.refreshStats();
      skillsState.refreshSkills();
      guardsState.refreshGuards();
      routingState.refreshRouting();
    },
  });
  // The Custom-profile guards (Phase-2 step 2). A weakening save mints a permanent
  // restore point core-side, so a successful save re-reads the snapshots list — the
  // way back the confirm promised should appear at once.
  const guardsState = useGuards({
    connected,
    onSaved: () => snapshotsState.refreshSnapshots(),
  });
  // Routing strategies + the companion prefer-quality/prefer-free toggle (Phase-2
  // step 3). A strategy change and a custom-chain save both snapshot core-side, so
  // a successful save re-reads the restore-points list.
  const routingState = useRouting({
    connected,
    onSaved: () => snapshotsState.refreshSnapshots(),
  });
  // The coding-harness workspace-trust boundary (Phase-2 step 5). Its card shows
  // only on the Developer/Custom surfaces (SettingsPage keys that off the active
  // profile); the hook itself just owns the trusted roots + grant/revoke.
  const workspaceState = useWorkspace({ connected });
  // The two step-4 conversational offers — "add my own model server" and "make it
  // cheaper" (useOffers). Same propose -> card -> explicit confirm mechanism as the
  // widget flow above; nothing is applied until the person presses the card's
  // button, and only the person's OWN words can arm a card. Applying either changes
  // config the core snapshots first, so both refresh the restore-points list.
  const offers = useOffers(
    () => connected,
    setStatusBanner,
    () => {
      snapshotsState.refreshSnapshots();
      routingState.refreshRouting();
      skillsState.refreshSkills();
      models.refreshProviders();
    },
  );
  const turn = useTurn({
    connected,
    setStatusBanner,
    selectedRole: models.selectedRole,
    selectedLocalModel: models.selectedLocalModel,
    selectedEffort: models.selectedEffort,
    effectiveLocalModel: models.effectiveLocalModel,
    effectiveCloudModel: models.effectiveCloudModel,
    maybeProposeWidget: widgetsState.maybeProposeWidget,
    maybeProposeOffers: offers.maybeProposeOffers,
    // Invoked from runTurn's `finally` — at event time, well after render, when
    // the `conversationsState` const below is initialized. The lazy wrapper is
    // what keeps the hook call order acyclic.
    // HAZARD: never invoke this lazy wrapper synchronously during render —
    // `conversationsState` is a forward reference, uninitialized until the hooks below run.
    refreshConversations: (adopt?: boolean) => conversationsState.refreshConversations(adopt),
    refreshStats: widgetsState.refreshStats,
  });
  // Sidebar controls are held while a turn is running or a permission prompt is
  // open — switching conversations mid-turn would strand in-flight work.
  const controlsBusy = turn.isWorking || turn.permission != null;
  const conversationsState = useConversations({
    connected,
    controlsBusy,
    resetTransientState,
    setMessages: turn.setMessages,
    // Loading or starting a conversation always lands on the chat view, through
    // the same transition every other route uses.
    setScreen: (screen) => changeView(screen),
    setStatusBanner,
  });

  // Clear the per-turn/per-conversation transient state. Deliberately leaves the
  // global action undo/redo state (hasUndoableActions / canRedo) alone — that's
  // core session state, not tied to which conversation is on screen.
  function resetTransientState() {
    turn.resetTurn();
    setRoutineProposal(null);
    setComposerSeed(null);
  }

  // --- The view machine -----------------------------------------------------
  // Leaving a surface plays fadeDrop over its children and commits at 240ms, so
  // the page leaves before it is replaced. Entering is handled after the commit
  // (the effect below) because the incoming children don't exist until then.
  // With motion off, or when no surface is on screen, the change is immediate —
  // the fast path, not a degraded one.
  function changeView(next: View) {
    if (next === view) return;
    if (viewTimer.current) clearTimeout(viewTimer.current);
    const commit = () => setView(next);
    const surface = document.getElementById(SURFACE_ID);
    if (!isMotionEnabled() || !surface) return commit();
    Array.from(surface.children).forEach((child) => {
      replayAnimation(child as HTMLElement, "fadeDrop .25s ease both");
    });
    viewTimer.current = setTimeout(commit, VIEW_COMMIT_MS);
  }

  useEffect(() => {
    return () => {
      if (viewTimer.current) clearTimeout(viewTimer.current);
    };
  }, []);

  const isSurface = view !== "chat";
  const viewTitle =
    view === "chat"
      ? conversationsState.conversationTitle || "New conversation"
      : SURFACE_TITLES[view];

  // The header title resolves out of the scramble whenever it changes — a view
  // change or a chat that just got its name.
  const titleRef = useRef<HTMLSpanElement>(null);
  useScrambleOnChange(titleRef, viewTitle);

  // Entering a view: stagger the surface's children in, and let its labels and
  // row names resolve out of the scramble behind them.
  useEffect(() => {
    if (seenView.current === view) return; // first paint — the load pass owns it
    seenView.current = view;
    const surface = document.getElementById(SURFACE_ID);
    if (surface && isMotionEnabled()) {
      Array.from(surface.children).forEach((child, i) => {
        replayAnimation(child as HTMLElement, `fadeRise .35s ease both ${i * 40}ms`);
      });
    }
    document.querySelectorAll("[data-surf]").forEach((el, i) => {
      if (el.children.length === 0) scrambleElement(el, i * 45);
    });
  }, [view]);

  // Initial load: the staggered scramble pass, and the global click handler that
  // scrambles any leaf element carrying a data-scramble attribute.
  useEffect(() => {
    scrambleAll(INITIAL_SCRAMBLE_SELECTOR);
    return installScrambleClickHandler();
  }, []);

  // --- Wire up notifications + initial data on mount ------------------------
  useEffect(() => {
    if (!connected) return;
    const unsubs: Array<() => void> = [];

    unsubs.push(
      subscribe(Method.ConversationStreamChunk, (p) => {
        const params = p as StreamChunkParams;
        const text = params.text ?? params.delta ?? params.content ?? "";
        if (!text) return;
        // TODO(streaming): real streaming needs two edits — the final result's
        // `finalText` must append to (not overwrite) streamed content in useTurn's
        // runTurn, and this handler must target the message by id, not the `pending` flag.
        turn.setMessages((prev) =>
          prev.map((m) => (m.pending ? { ...m, content: m.content + text } : m)),
        );
      }),
    );

    unsubs.push(
      subscribe(Method.PermissionRequestGrant, (p) => {
        turn.setPermission(normalizePermission(p));
      }),
    );

    unsubs.push(
      subscribe(Method.ToolActivityUpdate, (p) => {
        const update = normalizeActivity(p);
        turn.setCurrentActivity(update);
        turn.setActivities((prev) => [...prev, update]);
        // Any tool step means something may be undoable; the core reports back
        // plainly if there's actually nothing to put back. A new action also
        // discards the undone future — the core just cleared its redo stack.
        setHasUndoableActions(true);
        setCanRedo(false);
      }),
    );

    unsubs.push(
      subscribe(Method.ModelLocalSetupProgress, (p) => {
        const params = p as LocalSetupProgressParams;
        // Progress belongs INSIDE the Settings section, not in a fleeting
        // banner. Only one setup runs at a time, so we fold each update onto the
        // in-progress entry (App set its modelId when it kicked things off).
        models.setLocalSetup((prev) => {
          if (!prev) return prev; // no setup running — ignore stray progress
          const status: LocalSetupState["status"] = params.error
            ? "error"
            : params.done
              ? "done"
              : "running";
          return {
            ...prev,
            status,
            stage: params.stage ?? params.label ?? prev.stage,
            percent: typeof params.percent === "number" ? params.percent : prev.percent,
            message: params.message ?? params.label ?? prev.message,
            error: params.error ?? prev.error,
          };
        });
      }),
    );

    unsubs.push(subscribeStatus((text) => setStatusBanner(text)));

    // Every "ready" is a fresh engine process (first launch OR the shell's
    // one-time respawn after a crash). Re-fetch what we cached from the old
    // one — offering a dead engine's model catalog produces "That model
    // option isn't available." (2026-07 manual pass finding).
    unsubs.push(
      subscribeCoreState((state) => {
        if (state === "ready") {
          models.refreshRoles();
          models.refreshProviders();
          refreshProfile();
          conversationsState.refreshConversations();
          widgetsState.refreshWidgets();
          widgetsState.refreshStats();
          skillsState.refreshSkills();
        }
      }),
    );

    // Keep the last ~5 raw diagnostics for the Developer-only panel. The ring is
    // maintained even in Simple (it simply never fills, since the core only ever
    // emits raw text under the Developer profile) and never rendered there.
    unsubs.push(
      subscribeDiagnostics((entry) =>
        setDiagnostics((prev) => [entry, ...prev].slice(0, 5)),
      ),
    );

    models.refreshRoles();
    models.refreshProviders();
    refreshProfile();
    conversationsState.refreshConversations();
    widgetsState.refreshWidgets();
    widgetsState.refreshStats();
    skillsState.refreshSkills();

    return () => unsubs.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Transient shell notices fade out on their own so they don't linger.
  useEffect(() => {
    if (!statusBanner) return;
    const t = setTimeout(() => setStatusBanner(null), 8000);
    return () => clearTimeout(t);
  }, [statusBanner]);

  // Reflect the chosen theme onto <html> (the Tailwind `dark:` selector keys off
  // this class) and persist the choice. The inline bg matches so a reload paints
  // the right color before the stylesheet is parsed. When the choice is "system"
  // we resolve against the OS preference AND subscribe to its changes, so the app
  // flips live when the user switches their computer between light and dark; the
  // listener is torn down (and never attached for explicit light/dark).
  useEffect(() => {
    const root = document.documentElement;
    const mql =
      typeof window.matchMedia === "function"
        ? window.matchMedia("(prefers-color-scheme: dark)")
        : null;
    const apply = () => {
      const resolved = resolveTheme(themeChoice, mql?.matches ?? false);
      root.classList.toggle("dark", resolved === "dark");
      root.style.backgroundColor = resolved === "dark" ? "#0C0C0D" : "#F7F7F5";
    };
    apply();
    try {
      localStorage.setItem(THEME_KEY, themeChoice);
    } catch {
      /* non-fatal */
    }
    if (themeChoice !== "system" || !mql) return;
    mql.addEventListener("change", apply);
    return () => mql.removeEventListener("change", apply);
  }, [themeChoice]);

  function setThemeChoice(next: ThemeChoice) {
    setThemeChoiceState(next);
  }

  // Persist the app-shell chrome toggles alongside the other prefs.
  useEffect(() => {
    saveBool(RAIL_OPEN_KEY, railOpen);
  }, [railOpen]);
  useEffect(() => {
    saveBool(SIDE_OPEN_KEY, sideOpen);
  }, [sideOpen]);

  // Growing the window past the breakpoint reveals the static sidebar + rail, so
  // the mobile drawer must not linger (and mustn't pop back if the window shrinks
  // again).
  useEffect(() => {
    if (!isMobile) {
      setDrawerOpen(false);
    }
  }, [isMobile]);

  // Whether any model role is set up right now.
  const anyConfigured = models.roles.some((r) => r.configured);

  // Latch the first-run signal exactly once. If we're disconnected (a plain
  // design-review browser, where roles never load) treat it as a fresh launch so
  // the setup guidance is visible; otherwise wait for the first real roles fetch
  // and latch on whether anything was configured at startup.
  useEffect(() => {
    if (startedUnconfigured !== null) return;
    if (!connected) {
      setStartedUnconfigured(true);
      return;
    }
    if (models.rolesLoaded) setStartedUnconfigured(!anyConfigured);
  }, [connected, models.rolesLoaded, anyConfigured, startedUnconfigured]);

  // First-run is "active" until the user configures something OR skips. Once a
  // provider connects mid-launch (anyConfigured flips true) the banner advances
  // to step 2 and nudges the user to say hello — focus the composer for them.
  const firstRunActive = startedUnconfigured === true && !firstRunDismissed;
  useEffect(() => {
    if (firstRunActive && anyConfigured) {
      setComposerFocusSignal((n) => n + 1);
    }
  }, [firstRunActive, anyConfigured]);

  function refreshProfile() {
    if (!isEngineConnected()) return;
    ipc
      .getProfile()
      .then((res) => {
        const parsed = normalizeProfile(res);
        if (parsed) setProfile(parsed);
      })
      .catch(() => {
        /* leave the Profile section on its quiet placeholder if we can't read it */
      });
  }

  // Switching a profile takes effect immediately (no restart). Re-fetch so the
  // new flags reshape the surface right away; quietly no-op if the switch fails.
  function handleSetProfile(profileId: string) {
    if (!isEngineConnected()) return;
    ipc
      .setProfile(profileId)
      .then(() => {
        refreshProfile();
        // A mode switch changes which routines/widgets are visible (dev-created
        // ones hide in Simple, return in Developer). Re-fetch both so the rail
        // and library reflect the new mode immediately — and so their empty
        // states settle cleanly when the lists shrink.
        widgetsState.refreshWidgets();
        // Switching to (or from) Custom changes whether the guards are effective;
        // re-read so the guard panel reflects the new profile at once.
        guardsState.refreshGuards();
      })
      .catch((err) => {
        setStatusBanner(
          err instanceof Error ? err.message : "I couldn't switch the profile.",
        );
      });
  }

  function clearDiagnostics() {
    setDiagnostics([]);
  }

  function handleRespondPermission(allow: boolean) {
    const p = turn.permission;
    turn.setPermission(null);
    if (!p) return;
    ipc.respondToPermission(p.toolId, allow).catch(() => {
      setStatusBanner("I couldn't send that answer. Please try again.");
    });
  }

  function handleRewindTo(storeId: string) {
    // Edit-and-resend: the anchored message leaves the thread too, and its text
    // goes back into the composer — nothing re-runs until the user presses Send.
    // Optimistic, but reversible: if the core can't rewind, the view snaps back
    // (a thread that looks rewound while the core remembers is the worst outcome).
    let before: DisplayMessage[] = [];
    let anchorText = "";
    turn.setMessages((prev) => {
      before = prev;
      const idx = prev.findIndex((m) => m.storeId === storeId);
      if (idx === -1) return prev;
      anchorText = prev[idx].content;
      return prev.slice(0, idx);
    });
    turn.setPermission(null);
    ipc
      .rewindConversation(storeId)
      .then(() => {
        if (anchorText) setComposerSeed(anchorText);
      })
      .catch((err) => {
        turn.setMessages(before);
        setStatusBanner(
          err instanceof Error ? err.message : "Couldn't rewind the conversation.",
        );
      });
  }

  function handleUndoLastAction() {
    ipc
      .undoLastAction()
      .then((res) => {
        setLastUndoDetail(extractDetail(res) ?? "Put things back the way they were.");
        setCanRedo(asRecord(res)?.canRedo === true);
      })
      .catch((err) => {
        setLastUndoDetail(err instanceof Error ? err.message : "Couldn't undo that.");
      });
  }

  function handleRedoLastAction() {
    ipc
      .redoLastAction()
      .then((res) => {
        setLastUndoDetail(extractDetail(res) ?? "Did that again.");
        setCanRedo(asRecord(res)?.canRedo === true);
        // A successful redo means the action is live again — undoable again.
        setHasUndoableActions(true);
      })
      .catch((err) => {
        setLastUndoDetail(err instanceof Error ? err.message : "Couldn't do that again.");
      });
  }

  // --- Routines (§6.3): propose -> confirmation card -> explicit save --------
  function handleProposeRoutine() {
    ipc
      .proposeRoutine()
      .then((res) => {
        const proposal = normalizeProposal(res);
        if (proposal) setRoutineProposal(proposal);
        else setStatusBanner("I couldn't turn that into a routine.");
      })
      .catch((err) => {
        setStatusBanner(
          err instanceof Error ? err.message : "I couldn't turn that into a routine.",
        );
      });
  }

  function handleConfirmRoutine(name: string) {
    setRoutineProposal(null);
    ipc
      .confirmSaveRoutine(name)
      .then(() => setStatusBanner(`Saved "${name}" — it's in Settings under Routines.`))
      .catch((err) => {
        setStatusBanner(
          err instanceof Error ? err.message : "I couldn't save that routine.",
        );
      });
  }

  // First-run "Start setup": open Settings scrolled to the API-keys card. The
  // scroll request is one-shot (SettingsPage clears it via onScrolled).
  function handleStartSetup() {
    changeView("settings");
    setSettingsScrollTarget(API_KEYS_SECTION_ID);
  }

  // The rail's "＋ Ask Addison to build a widget" now opens the Build-a-widget
  // surface (it used to seed the composer directly). Nothing is created there
  // either — the surface's rows seed the composer, below.
  function handleAskBuildWidget() {
    changeView("widgets");
  }

  // "use" on an idea: write the sentence into the composer and go back to chat.
  // The person still presses Send, and the propose → card → confirm flow is
  // unchanged.
  function seedWidgetIdea(prompt: string) {
    setComposerSeed(`Build me a widget: ${prompt}`);
    changeView("chat");
  }

  // Window-level shortcuts: Escape returns from any surface to chat; Cmd/Ctrl+N
  // starts a new chat (unless a turn or permission prompt is in flight).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        // The mobile drawer takes Escape first, before it would fall through to
        // leaving a surface.
        if (drawerOpen) {
          setDrawerOpen(false);
          return;
        }
        if (view !== "chat") {
          changeView("chat");
          return;
        }
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === "n" || e.key === "N")) {
        if (connected && !controlsBusy) {
          e.preventDefault();
          conversationsState.handleNewChat();
        }
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, connected, controlsBusy, drawerOpen]);

  // --- Render ---------------------------------------------------------------
  // The two movable blocks: the "Addison's work" annotation and the consent card
  // live in the widget rail when it's open, and fall back inline in the thread
  // when it's hidden. Assemble each once so it can render in either slot.
  const hasWork =
    turn.isWorking || turn.activities.length > 0 || Boolean(lastUndoDetail) || canRedo;
  const workBlock = hasWork ? (
    <ActivityPanel
      isWorking={turn.isWorking}
      current={turn.currentActivity}
      activities={turn.activities}
      canRedo={canRedo}
      onRedoLastAction={handleRedoLastAction}
      lastUndoDetail={lastUndoDetail}
      onProposeRoutine={connected ? handleProposeRoutine : undefined}
    />
  ) : null;
  const consentBlock = turn.permission ? (
    <PermissionCard request={turn.permission} onRespond={handleRespondPermission} />
  ) : null;
  const proposalBlock = routineProposal ? (
    <RoutineProposalCard
      proposal={routineProposal}
      onSave={handleConfirmRoutine}
      onCancel={() => setRoutineProposal(null)}
    />
  ) : null;
  const widgetProposalBlock = widgetsState.widgetProposal ? (
    <WidgetProposalCard
      proposal={widgetsState.widgetProposal}
      onAdd={widgetsState.handleAddWidget}
      onCancel={widgetsState.handleDismissWidgetProposal}
    />
  ) : null;
  // The step-4 offers, in the same footer slot as the widget/routine cards. One at
  // a time (useOffers enforces it) — two consent cards under the composer is how a
  // person confirms the one they did not read.
  const offerBlock = offers.endpointProposal ? (
    <EndpointProposalCard
      proposal={offers.endpointProposal}
      onDismiss={offers.dismissEndpointProposal}
      onAdded={offers.handleEndpointAdded}
    />
  ) : offers.costPlan ? (
    <CostPlanCard
      plan={offers.costPlan}
      onDismiss={offers.dismissCostPlan}
      onApplied={offers.handleCostPlanApplied}
    />
  ) : null;

  const profileLabel =
    profile?.activeProfile === "developer"
      ? "Developer profile"
      : profile?.activeProfile === "custom"
        ? "Custom profile"
        : "Simple profile";
  // In OPEN mode the sidebar appends a dim, mono " · open" — the one quiet
  // acknowledgement that the safety posture is different. Nothing louder.
  const profileModeNote = profile?.mode === "open" ? "open" : undefined;

  // The sidebar's two machine facts. Both are REAL: the count of folders Addison
  // may work in (Developer/Custom only — Simple has none by construction, and
  // then the policy mode is the honest thing to show), and how many restore
  // points exist right now.
  const trustedRoots = workspaceState.rootsLoaded ? workspaceState.roots.length : 0;
  const toolsHint =
    trustedRoots > 0
      ? `${trustedRoots} folder${trustedRoots === 1 ? "" : "s"}`
      : profile?.mode;
  const snapshotsHint = snapshotsState.snapshotsLoaded
    ? String(snapshotsState.snapshots.length)
    : undefined;

  // First-run render pieces. The banner rides in the chat column above the
  // thread while first-run is active; the greeting replaces the welcome message
  // only at step 1 (nothing configured yet) with an otherwise-empty thread. Once
  // a provider connects (step 2), the normal welcome returns so Addison
  // "introduces itself" per the step-2 copy.
  const threadEmpty = turn.messages.length === 1 && turn.messages[0]?.id === "welcome";
  const showGreeting = firstRunActive && !anyConfigured && threadEmpty;
  const threadMessages = showGreeting
    ? turn.messages.filter((m) => m.id !== "welcome")
    : turn.messages;
  const firstRunHeader = firstRunActive ? (
    <FirstRunBanner
      step={anyConfigured ? 2 : 1}
      onStartSetup={handleStartSetup}
      onSkip={() => setFirstRunDismissed(true)}
      showGreeting={showGreeting}
    />
  ) : undefined;

  // Wrap the sidebar's pick handlers so, in the mobile drawer, choosing a
  // conversation / Settings / New chat also closes the drawer.
  const closeDrawer = () => setDrawerOpen(false);
  // Opening a conversation or starting a new chat always returns to the chat
  // view — picked from a surface, either would otherwise load invisibly behind it.
  const openConversationFromNav = (id: string) => {
    changeView("chat");
    conversationsState.handleOpenConversation(id);
  };
  const newChatFromNav = () => {
    changeView("chat");
    conversationsState.handleNewChat();
  };
  // Clicking the workspace item that is already open goes back to chat — the
  // sidebar rows toggle rather than dead-end.
  const toggleSurface = (target: View) => changeView(view === target ? "chat" : target);

  const sidebarProps = {
    conversations: conversationsState.conversations,
    currentConversationId: conversationsState.currentConversationId,
    onRenameConversation: conversationsState.handleRenameConversation,
    newChatDisabled: !connected || controlsBusy,
    view,
    toolsHint,
    snapshotsHint,
    profileLabel,
    modeNote: profileModeNote,
  };

  const railVisible = railOpen && view === "chat";

  return (
    <div className="relative flex h-full flex-col overflow-hidden bg-paper text-[13px] text-ink">
      {/* One header across the whole window: the way out on the left (← from a
          surface, the sidebar chevron on chat), the view's name beside it, and
          the two live controls on the right. */}
      <header className="relative z-10 flex shrink-0 items-center justify-between gap-4 border-b border-line px-6 py-4">
        <span className="flex min-w-0 items-center gap-4">
          {isSurface ? (
            <button
              type="button"
              onClick={() => changeView("chat")}
              title="Back to chat"
              aria-label="Back to chat"
              className="shrink-0 text-[13px] text-muted transition-colors hover:text-ink max-md:min-h-[44px] max-md:min-w-[44px]"
            >
              ←
            </button>
          ) : isMobile ? (
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="Menu"
              className="flex h-11 w-11 shrink-0 items-center justify-center text-[15px] text-disabled transition-colors hover:text-ink"
            >
              ☰
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setSideOpen((v) => !v)}
              title={sideOpen ? "Hide chats" : "Show chats"}
              aria-label={sideOpen ? "Hide chats" : "Show chats"}
              className="shrink-0 text-[12px] text-disabled transition-colors hover:text-ink"
            >
              {sideOpen ? "«" : "»"}
            </button>
          )}
          <span
            ref={titleRef}
            data-scramble-live="0"
            className="min-w-0 truncate text-[13px] font-medium text-ink-soft"
          >
            {viewTitle}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-[22px] text-[12px]">
          {hasUndoableActions && (
            <button
              type="button"
              data-scramble="320"
              onClick={handleUndoLastAction}
              className="text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
            >
              Undo last action
            </button>
          )}
          {!isSurface && !isMobile && (
            <button
              type="button"
              onClick={() => setRailOpen((v) => !v)}
              title={railOpen ? "Hide widgets" : "Show widgets"}
              aria-label={railOpen ? "Hide widgets" : "Show widgets"}
              className="text-[12px] text-disabled transition-colors hover:text-ink"
            >
              {railOpen ? "»" : "«"}
            </button>
          )}
        </span>
      </header>

      <main className="relative flex min-h-0 min-w-0 flex-1 px-4 md:gap-[44px] md:px-10">
        {/* Left column. Collapsing animates width, opacity, margin and offset
            together so the chat column glides wider instead of jumping. */}
        {!isMobile && (
          <div
            className="shrink-0 overflow-hidden"
            aria-hidden={!sideOpen}
            style={{
              transition:
                "width .35s ease, opacity .25s ease, margin-right .35s ease, transform .35s ease",
              width: sideOpen ? "212px" : "0px",
              opacity: sideOpen ? 1 : 0,
              marginRight: sideOpen ? "0px" : "-44px",
              transform: sideOpen ? "translateX(0)" : "translateX(-16px)",
              pointerEvents: sideOpen ? undefined : "none",
            }}
          >
            <Sidebar
              {...sidebarProps}
              onOpenConversation={openConversationFromNav}
              onNewChat={newChatFromNav}
              onOpenSettings={() => toggleSurface("settings")}
              onOpenTools={() => toggleSurface("tools")}
              onOpenSnapshots={() => toggleSurface("snapshots")}
            />
          </div>
        )}

        {/* Centre column: the thread + composer, or a surface in their place. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 justify-center">
            {view === "chat" ? (
              <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center">
                {(!connected || statusBanner) && (
                  <div className="flex w-full max-w-[580px] flex-col gap-2 pt-3">
                    {!connected && (
                      <Banner message="Addison's engine isn't connected. You can look around, but I can't chat just yet." />
                    )}
                    {statusBanner && (
                      <Banner message={statusBanner} onDismiss={() => setStatusBanner(null)} />
                    )}
                  </div>
                )}
                <ChatThread
                  messages={threadMessages}
                  onRetry={turn.handleRetry}
                  retryAvailable={!turn.isWorking && Boolean(turn.lastUserText)}
                  onRewindTo={handleRewindTo}
                  showTechnicalDetails={Boolean(profile?.flags.rawDiagnostics)}
                  header={firstRunHeader}
                  footer={
                    <>
                      {proposalBlock}
                      {widgetProposalBlock}
                      {offerBlock}
                      {/* Mobile: there's no side rail, so the widgets live inline
                          at the foot of the thread — visible on the chat view,
                          carrying the work + consent blocks with them. Desktop:
                          work/consent go inline only when the rail is hidden
                          (otherwise they're in the rail). */}
                      {isMobile ? (
                        <WidgetRail
                          variant="inline"
                          work={workBlock}
                          consent={consentBlock}
                          developer={profileModeNote === "open"}
                          widgets={widgetsState.widgets}
                          stats={widgetsState.stats}
                          routines={widgetsState.railRoutines}
                          onSetPinned={widgetsState.handleSetWidgetPinned}
                          onDelete={widgetsState.handleDeleteWidget}
                          onRunRoutine={widgetsState.handleRunWidgetRoutine}
                          onRunCommandWidget={(id) => ipc.runWidget(id)}
                          onAskBuildWidget={handleAskBuildWidget}
                        />
                      ) : (
                        !railOpen && (
                          <>
                            {workBlock}
                            {consentBlock}
                          </>
                        )
                      )}
                    </>
                  }
                />
              </div>
            ) : view === "settings" ? (
              // PHASE NOTE (redesign 3/4): SettingsPage still brings its own
              // header, scroller and card layout, so it renders in the "raw"
              // surface — the transitions apply, the 580px reading column does
              // not. Phase 3 rebuilds it as sections and rows and drops this
              // variant.
              <Surface variant="raw" title="Settings">
                <SettingsPage
                  connected={connected}
                  notice={
                    (!connected || statusBanner) && (
                      <>
                        {!connected && (
                          <Banner message="Addison's engine isn't connected. You can look around, but I can't chat just yet." />
                        )}
                        {statusBanner && (
                          <Banner
                            message={statusBanner}
                            onDismiss={() => setStatusBanner(null)}
                          />
                        )}
                      </>
                    )
                  }
                  models={models}
                  skills={skillsState}
                  snapshots={snapshotsState}
                  guards={guardsState}
                  routing={routingState}
                  workspace={workspaceState}
                  profile={profile}
                  onSetProfile={handleSetProfile}
                  diagnostics={diagnostics}
                  onClearDiagnostics={clearDiagnostics}
                  theme={themeChoice}
                  onSetTheme={setThemeChoice}
                  onOpenMenu={() => setDrawerOpen(true)}
                  scrollTarget={settingsScrollTarget}
                  onScrolled={() => setSettingsScrollTarget(null)}
                />
              </Surface>
            ) : view === "tools" ? (
              <Surface
                title="Tools"
                description="What Addison can reach on this computer. Connect only what you're comfortable with."
              >
                <SurfaceSection label="Still being built">
                  <SurfaceRow
                    name="This page doesn't list anything yet."
                    value="coming in this build"
                  />
                  <SurfaceRow
                    name="What Addison can reach today is in Settings."
                    action="open"
                    onAction={() => changeView("settings")}
                  />
                </SurfaceSection>
              </Surface>
            ) : view === "snapshots" ? (
              <Surface
                title="Snapshots"
                description="Addison saves a restore point before anything risky, so you can always go back to a setup that worked."
              >
                <SurfaceSection label="Still being built">
                  <SurfaceRow
                    name="The full list of restore points isn't here yet."
                    value="coming in this build"
                  />
                  <SurfaceRow
                    name="Your restore points"
                    value={snapshotsHint ? `${snapshotsHint} saved` : undefined}
                    action="open in Settings"
                    onAction={() => changeView("settings")}
                  />
                </SurfaceSection>
              </Surface>
            ) : (
              <Surface
                title="Build a widget"
                description="Describe what you want to keep an eye on, and Addison turns it into a small card in the right rail."
              >
                <SurfaceSection label="Ideas to start from">
                  {WIDGET_IDEAS.map((idea) => (
                    <SurfaceRow
                      key={idea.prompt}
                      name={idea.name}
                      action="use"
                      onAction={() => seedWidgetIdea(idea.prompt)}
                    />
                  ))}
                </SurfaceSection>
              </Surface>
            )}
          </div>

          {view === "chat" && (
            <Composer
              connected={connected}
              turn={turn}
              models={models}
              draftSeed={composerSeed}
              onDraftSeedUsed={() => setComposerSeed(null)}
              focusSignal={composerFocusSignal}
            />
          )}
        </div>

        {/* Right column: widgets. Chat view only — a surface takes the whole
            middle, and the rail's contents are about the conversation. */}
        {!isMobile && (
          <div
            className="shrink-0 overflow-hidden"
            aria-hidden={!railVisible}
            style={{
              transition:
                "width .35s ease, opacity .25s ease, margin-left .35s ease, transform .35s ease",
              width: railVisible ? "232px" : "0px",
              opacity: railVisible ? 1 : 0,
              marginLeft: railVisible ? "0px" : "-44px",
              transform: railVisible ? "translateX(0)" : "translateX(16px)",
              pointerEvents: railVisible ? undefined : "none",
            }}
          >
            <WidgetRail
              work={workBlock}
              consent={consentBlock}
              developer={profileModeNote === "open"}
              widgets={widgetsState.widgets}
              stats={widgetsState.stats}
              routines={widgetsState.railRoutines}
              onSetPinned={widgetsState.handleSetWidgetPinned}
              onDelete={widgetsState.handleDeleteWidget}
              onRunRoutine={widgetsState.handleRunWidgetRoutine}
              onRunCommandWidget={(id) => ipc.runWidget(id)}
              onAskBuildWidget={handleAskBuildWidget}
            />
          </div>
        )}
      </main>

      {/* Mobile slide-over drawer: the same Sidebar, in drawer mode. Picking a
          conversation / a surface / New chat / the close arrow closes it; so does
          the scrim (in MobileDrawer) and Escape (handled above). Every path just
          flips `drawerOpen` false — MobileDrawer plays the slide-out either way,
          which is why it stays mounted here (open-prop, not a conditional). */}
      {isMobile && (
        <MobileDrawer open={drawerOpen} onClose={closeDrawer}>
          <Sidebar
            {...sidebarProps}
            variant="drawer"
            onOpenConversation={(id) => {
              closeDrawer();
              openConversationFromNav(id);
            }}
            onNewChat={() => {
              closeDrawer();
              newChatFromNav();
            }}
            onOpenSettings={() => {
              closeDrawer();
              toggleSurface("settings");
            }}
            onOpenTools={() => {
              closeDrawer();
              toggleSurface("tools");
            }}
            onOpenSnapshots={() => {
              closeDrawer();
              toggleSurface("snapshots");
            }}
            onCloseDrawer={closeDrawer}
          />
        </MobileDrawer>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small pure helpers — defensive parsing of free-form JSON-RPC payloads, since
// the Python side's result/notification shapes aren't pinned in protocol.ts.
// ---------------------------------------------------------------------------
// Restart a CSS animation on an element that may already be carrying one (the
// standard reflow trick — assigning the same animation name would otherwise be
// ignored).
function replayAnimation(el: HTMLElement, animation: string): void {
  el.style.animation = "none";
  el.getBoundingClientRect(); // force reflow so the restart is seen
  el.style.animation = animation;
}

// Appearance persists like the default role. Absent/legacy values fall back to
// "system" — Match this computer (see lib/theme).
function loadThemeChoice(): ThemeChoice {
  try {
    return parseThemeChoice(localStorage.getItem(THEME_KEY));
  } catch {
    /* localStorage may be unavailable; fall through to the default */
    return "system";
  }
}

// Boolean prefs (rail/sidebar open) persist as "1"/"0".
function loadBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    if (v === "1") return true;
    if (v === "0") return false;
  } catch {
    /* localStorage may be unavailable; fall through to the default */
  }
  return fallback;
}

function saveBool(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* non-fatal */
  }
}

function normalizePermission(p: Record<string, unknown>): PermissionRequest {
  const req = asRecord(p.request) ?? p;
  const riskTier = req.riskTier;
  return {
    toolId: typeof req.toolId === "string" ? req.toolId : "",
    label: typeof req.label === "string" ? req.label : "Addison would like to do something",
    description:
      typeof req.description === "string"
        ? req.description
        : "Addison is asking for your permission to continue.",
    riskTier: riskTier === "medium" || riskTier === "high" ? riskTier : "low",
  };
}

// Exported only so it can be tested directly. This is the single point where a
// tool.activityUpdate frame becomes an ActivityUpdate, and `detail` — the site a
// page read is reaching — is the one security-visible field crossing here: after
// the first grant, later calls of an allowed tool are ungated, so this line is
// where the person finds out where one went (protocol.ts, owner decision
// 2026-07-20). Silently dropping it here would leave every other piece of the
// pipeline correct and the person still blind, which is why it is worth a test.
export function normalizeActivity(p: Record<string, unknown>): ActivityUpdate {
  // Kept only when it is a non-empty string, so an absent or null field becomes an
  // absent property rather than the word "undefined" under a step.
  const detail = typeof p.detail === "string" ? p.detail.trim() : "";
  return {
    label: typeof p.label === "string" ? p.label : "Working…",
    toolId: typeof p.toolId === "string" ? p.toolId : "",
    ...(detail ? { detail } : {}),
  };
}

function normalizeProposal(result: unknown): RoutineProposal | null {
  const obj = asRecord(result);
  if (!obj || typeof obj.routineId !== "string") return null;
  return {
    routineId: obj.routineId,
    name: typeof obj.name === "string" ? obj.name : "My new routine",
    description: typeof obj.description === "string" ? obj.description : "",
    steps: Array.isArray(obj.steps)
      ? obj.steps.filter((s): s is string => typeof s === "string")
      : [],
    variables: normalizeVariables(obj.variables),
  };
}

function extractDetail(result: unknown): string | null {
  const obj = asRecord(result);
  if (!obj) return typeof result === "string" ? result : null;
  const detail = obj.detail ?? obj.message ?? obj.text;
  return typeof detail === "string" ? detail : null;
}
