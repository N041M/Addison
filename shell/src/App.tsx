// Addison — top-level app shell (DARK direction; docs/design-brief-dark).
//
// One full-width header over three columns: the conversation Sidebar (212px),
// the chat column, and the hideable widget rail (232px). Both side columns
// collapse by animating width/opacity/margin/translateX, so hiding one widens
// the middle rather than leaving a hole.
//
// FIVE SURFACES — Settings, Tools, Snapshots, Build a widget, Code — replace the
// chat column (the rail hides entirely, the sidebar stays). `view` is the single
// state that says which one is showing; `changeView` owns the transition
// (children fadeDrop, commit at ~240ms) and the header's ← and Escape both
// route back to chat through it.
//
// "Code" is the odd one out and is the only view gated on the ACTIVE PROFILE:
// the Developer review surface (Phase 3) shows what Addison has changed on disk
// and lets one file be put back, so Simple gets no sidebar row for it and no
// render of it. The core refuses every one of its calls independently — this
// window just never offers the trip.
//
// This component owns the UI-chrome state and wires the Core → Frontend
// notifications (streamed text, permission prompts, tool activity, local-setup
// progress) into React state, and Frontend → Core actions back out through the
// typed `ipc`. The big state clusters live in dedicated hooks: useModelSelection,
// useWidgets, useTurn, useConversations, useSnapshots, useGuards, useRouting,
// useWorkspace, useMcpServers, useAutomations, useOffers.
//
// Theme is class-driven and persisted in localStorage ("addison.theme") as one of
// "light" | "dark" | "system"; the default is now "system" (Match this computer).
//
// FLOATING CHROME LIVES HERE, not inside a surface: the anchored model popup and
// the Restore points modal are both `position: fixed`, and a surface section
// carries a fadeRise transform (`both` fill), which would make a fixed child
// resolve against the section instead of the viewport. App owns them, so Escape
// can also be ordered honestly: drawer, then modal, then the surface itself.

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Method, type PermissionRequest, type ActivityUpdate } from "./types/protocol";
import type { DisplayMessage, LocalSetupState, ProfileState, View } from "./types/ui";
import {
  ipc,
  isEngineConnected,
  parseArming,
  subscribe,
  subscribeStatus,
  subscribeCoreState,
  subscribeDiagnostics,
  type StreamChunkParams,
  type LocalSetupProgressParams,
  type DiagnosticEntry,
} from "./ipc/client";
import { AddisonMark } from "./components/AddisonMark";
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
import {
  SettingsPage,
  API_KEYS_SECTION_ID,
  resolveCloudModel,
} from "./components/SettingsPage";
import { ModelPopup, type ModelPopupOption, type PopupAnchor } from "./components/ModelPopup";
import { RestorePointsModal } from "./components/RestorePointsModal";
import { ToolsSurface } from "./components/ToolsSurface";
import { SnapshotsSurface } from "./components/SnapshotsSurface";
import { CodeSurface } from "./components/CodeSurface";
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
import { useCodeReview } from "./hooks/useCodeReview";
import { useMcpServers } from "./hooks/useMcpServers";
import { useAutomations } from "./hooks/useAutomations";
import { useOffers } from "./hooks/useOffers";
import { useTurn } from "./hooks/useTurn";
import { useConversations } from "./hooks/useConversations";
import { asRecord, normalizeVariables, normalizeProfile } from "./lib/parse";
import {
  type ResolvedTheme,
  type ThemeChoice,
  parseThemeChoice,
  resolveTheme,
} from "./lib/theme";
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
const INLINE_RAIL_OPEN_KEY = "addison.inlineRailOpen";
const SIDE_OPEN_KEY = "addison.sideOpen";

/** How long the leaving surface's fadeDrop runs before the new view commits. */
const VIEW_COMMIT_MS = 240;

// The header title for each surface. Chat shows the conversation's own title.
const SURFACE_TITLES: Record<Exclude<View, "chat">, string> = {
  settings: "Settings",
  tools: "Tools",
  snapshots: "Restore points",
  widgets: "Build a widget",
  code: "Code",
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
  // The narrow layout gets its OWN remembered state, defaulting to CLOSED.
  //
  // Not duplication of `railOpen`: beside-the-thread and inside-the-thread are
  // different affordances, and wanting a 232px ambient column at 1600px says
  // nothing about wanting six rows of it inside a 500px reading column. Below
  // 1024 it opens only when asked (reported 2026-07-27: it covered the chat and
  // there was no way to close it).
  const [inlineRailOpen, setInlineRailOpen] = useState<boolean>(() =>
    loadBool(INLINE_RAIL_OPEN_KEY, false),
  );

  // Narrow-window (mobile) layout. Below the md breakpoint (768px) the sidebar
  // becomes a slide-over drawer and the widget rail moves inline to the foot of
  // the chat thread (no side column fits). The drawer is ephemeral — deliberately
  // NOT persisted.
  const isMobile = useMediaQuery("(max-width: 767.98px)");
  // The step BETWEEN the two: 768–1024px has room for one side column, not two.
  // With both up, an 820px window left the reading column 208px wide (sidebar
  // 212 + rail 232 + the 44px gutters), which wrapped the greeting subline to
  // three lines and the suggestion chips to two rows — measured 2026-07-26. The
  // rail sheds first because it is ambient; the sidebar is navigation, and the
  // drawer doesn't take over until 768. Widgets are not lost — below this width
  // they render inline at the foot of the thread, exactly as they do on mobile.
  const railBeside = !useMediaQuery("(max-width: 1023.98px)");
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Appearance — "light" | "dark" | "system" ("Match this computer", the
  // default). The class on <html> drives the whole palette. The inline script in
  // index.html sets it before first paint to avoid a flash; the effect below
  // keeps it in sync, persists the CHOICE, and (only while "system") follows the
  // OS live.
  const [themeChoice, setThemeChoiceState] = useState<ThemeChoice>(loadThemeChoice);
  // The CONCRETE palette in effect, which `apply()` below has always computed and
  // thrown away. It is lifted into state for one consumer that cannot ask the DOM
  // for it: Monaco bakes its hex values at the moment a theme is DEFINED, so a
  // persistent editor somebody is reading when they flip Appearance has to be told
  // to redefine, not merely re-select. Prop-drilled to the code screen — NOT a
  // MutationObserver on the `dark` class, which would be a second source of truth
  // for a fact this component already knows and computes.
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() =>
    resolveTheme(loadThemeChoice(), prefersDark()),
  );
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
  // The clear callback is memoised on purpose: SettingsPage schedules the scroll
  // on a short timer and cancels it in the effect's cleanup, so a NEW function
  // identity on every App render would cancel and reschedule the timer forever
  // and the scroll would never actually happen.
  const [settingsScrollTarget, setSettingsScrollTarget] = useState<string | null>(null);
  const clearSettingsScrollTarget = useCallback(() => setSettingsScrollTarget(null), []);
  // The two pieces of floating chrome (see the file header for why they live
  // here): the anchored model popup's click point, and the Restore points modal.
  const [modelAnchor, setModelAnchor] = useState<PopupAnchor | null>(null);
  // The control that opened the popup, so focus can go back to it. A ref rather
  // than state: it is read at close time and nothing renders differently for it.
  const modelTrigger = useRef<HTMLElement | null>(null);
  const openModelPopup = useCallback((at: PopupAnchor, trigger: HTMLElement) => {
    modelTrigger.current = trigger;
    setModelAnchor(at);
  }, []);
  // Both memoised: the popup binds document listeners keyed on `onClose`, and a
  // fresh identity every render re-bound them behind every streamed chunk.
  const closeModelPopup = useCallback(() => setModelAnchor(null), []);
  const returnFocusToModelTrigger = useCallback(() => modelTrigger.current?.focus(), []);
  // The last place the model popup opened, kept after `modelAnchor` clears so it
  // can fade out where it was instead of disappearing on a frame.
  const [lastModelAnchor, setLastModelAnchor] = useState<PopupAnchor | null>(null);
  useEffect(() => {
    if (modelAnchor) setLastModelAnchor(modelAnchor);
  }, [modelAnchor]);
  const [restorePointsOpen, setRestorePointsOpen] = useState(false);
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

  // The Developer/Custom gate, keyed off the ACTIVE PROFILE and never the policy
  // mode. It answers two questions that must never diverge: whether the sidebar
  // offers a way to the workspace surfaces at all, and whether the code screen
  // renders. Trust rows outlive a profile switch core-side, so a Simple-profile
  // person must not see a folder they trusted under Developer — the core refuses
  // every call independently, and this keeps the window honest about it.
  // Declared HERE, above the hooks and effects that read it, rather than beside
  // the other render-time derivations further down.
  const developerSurfaces =
    profile?.activeProfile === "developer" || profile?.activeProfile === "custom";

  // --- The extracted state clusters ----------------------------------------
  const models = useModelSelection();
  const widgetsState = useWidgets({ connected, railOpen, setStatusBanner });
  const skillsState = useSkills({ connected, setStatusBanner });
  // Restore points (G3). The hook re-reads itself on every engine "ready", so it
  // isn't in the refresh list below; what it needs from here is the other way
  // round — a restore replaces the profile, the services and the saved items
  // wholesale, so everything this file cached from before it is now describing a
  // configuration that no longer exists.
  // EVERY snapshot-captured table is re-read here. A row missed from this list
  // does not go stale quietly: the surface goes on offering controls for a thing
  // the core has already put back, and "Addison has forgotten X" comes back for
  // a server it no longer had.
  // HAZARD: `refreshProfile`, `guardsState`, `routingState`, `mcpState` and
  // `automationsState` are forward references; this closure only ever runs at
  // event time, after a restore has landed.
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
      mcpState.refreshServers();
      // The saved automations. The ROWS only: a restore can put a row back and
      // can never put a JOB back — there is no armed column to restore and a
      // one-action restore cannot perform the keyword ceremony (plan §5.6) — so
      // what the OS is running is unchanged, and re-asking it here would be a
      // check nobody caused. Armed-ness is matched to a row by its label on
      // every render, so the answer already in hand stays true.
      automationsState.refreshAutomations();
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
  // The review surface (Phase 3): what Addison changed, what is on disk, and
  // putting one file back. Nothing is fetched until the screen is actually open —
  // this is a read of the person's own disk, and doing it because an app happened
  // to start is a check nobody asked for.
  const codeReview = useCodeReview({
    connected,
    active: view === "code",
    roots: workspaceState.roots,
  });
  // The MCP tool-server configuration (Phase-2 step 7, phase 1 of five). Same
  // Developer/Custom gate as workspace trust, applied in SettingsPage. Adding one
  // saves an address and nothing else — there is no MCP client yet.
  const mcpState = useMcpServers({ connected });
  // The saved automations — what Addison has written down for the OS to run
  // (Phase-2 step 8, phase 4 of four). Owned here rather than by the
  // Settings section because `automations` is a SNAPSHOT-CAPTURED table: a G3
  // restore can add or remove rows underneath an open Settings page, and every
  // other captured table is re-read above. Reading the list on mount is a local
  // read; what the OPERATING SYSTEM is running is asked by the section when it
  // loads, never here — nothing checks at startup (plan §5.6).
  const automationsState = useAutomations({ connected });
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

  // Leaving Settings takes its floating chrome with it — an anchored popup or a
  // modal left over a page that is no longer there points at nothing.
  useEffect(() => {
    if (view !== "settings") {
      setModelAnchor(null);
      setRestorePointsOpen(false);
    }
  }, [view]);

  // A profile can change UNDER an open screen — from Settings, or from a G3
  // restore putting a whole configuration back. If the code screen loses its
  // profile while somebody is standing on it, leave it. The render below refuses
  // to draw it anyway (that is the protection); this is what stops the header
  // saying "Code" over an empty middle column afterwards. `profile` must have
  // loaded first: `null` means "not answered yet", not "Simple".
  useEffect(() => {
    if (view === "code" && profile && !developerSurfaces) {
      changeView("chat");
    }
    // `changeView` is re-created every render (it closes over `view`), so it is
    // deliberately not a dependency — this runs on the transition, not on identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, profile, developerSurfaces]);

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
        // The delta lands on the pending message as the TRUE text; useTurn also
        // feeds the streaming scramble, which decorates the DISPLAY only.
        // Today the core emits this ONCE per turn with the whole answer — the
        // RPC result carries only message ids, so this is the sole delivery path
        // for the text. `appendStreamedText` already targets the message by turn
        // id rather than the `pending` flag, so per-token deltas need no change
        // here; what real streaming still needs is on the core side.
        turn.appendStreamedText(text);
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
      // The one new line: the answer this function has always computed is now
      // also readable by React. Setting the same value twice is a no-op, so the
      // OS-preference listener below can call `apply` freely.
      setResolvedTheme(resolved);
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
    saveBool(INLINE_RAIL_OPEN_KEY, inlineRailOpen);
  }, [inlineRailOpen]);
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

  // `typed` arrives only from the ARMING card's code box (step 8 phase 3) and is
  // relayed exactly as typed. Nothing here compares it to anything — the core mints
  // the code, the core compares it, and this side never sees the verdict except as
  // the next thing the core does.
  function handleRespondPermission(allow: boolean, typed?: string) {
    const p = turn.permission;
    turn.setPermission(null);
    if (!p) return;
    ipc.respondToPermission(p.toolId, allow, typed).catch(() => {
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

  // A surface asking Addison for something: write the sentence into the composer
  // and go back to chat. THE PERSON STILL PRESSES SEND — a Settings button never
  // starts a turn behind their back, and the sentence it seeded is on screen to be
  // read or edited first. Used by "use this idea" on the widgets surface and by the
  // Automations section's Arm / Disarm actions, which is the only route from a
  // surface to the arming ceremony: arming is a TOOL the model calls and the gate
  // cards, not an RPC this webview can invoke (there is no `automation.arm` on the
  // Frontend→Core surface, deliberately).
  function seedAsk(text: string) {
    setComposerSeed(text);
    changeView("chat");
  }

  // "use" on an idea: write the sentence into the composer and go back to chat.
  // The person still presses Send, and the propose → card → confirm flow is
  // unchanged.
  function seedWidgetIdea(prompt: string) {
    seedAsk(`Build me a widget: ${prompt}`);
  }

  // Window-level shortcuts: Escape returns from any surface to chat; Cmd/Ctrl+N
  // starts a new chat (unless a turn or permission prompt is in flight).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        // Innermost thing first, always: the drawer, then the modal over the
        // surface, and only then the surface itself. (The model popup handles
        // Escape in the capture phase, so it never reaches this listener.)
        if (drawerOpen) {
          setDrawerOpen(false);
          return;
        }
        if (restorePointsOpen) {
          setRestorePointsOpen(false);
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
  }, [view, connected, controlsBusy, drawerOpen, restorePointsOpen]);

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

  // Workspace trust is a Developer/Custom surface, keyed off the ACTIVE PROFILE
  // and never the policy mode (Phase-2 step 5). Trust rows outlive a profile
  // switch core-side, so a Simple-profile person must not see them here either —
  // the Tools page is not a back door to a surface Settings hides. The same
  // predicate gates the code screen; it is computed once, above the hooks that
  // read it, so the two can never answer differently.
  const showTrustedFolders = developerSurfaces;
  const trustedRoots =
    showTrustedFolders && workspaceState.rootsLoaded ? workspaceState.roots : [];
  const connectedProviders = models.providers.filter((p) => p.connected);
  const readyLocalModels =
    models.roles.find((r) => r.role === "local" && r.configured)?.models ?? [];

  // The sidebar's two machine facts, both REAL: how many things Addison can
  // actually reach right now (exactly what the Tools surface lists), and how many
  // restore points exist. With nothing reachable there is no honest count to
  // show, so the policy mode takes the slot instead.
  const reachableCount =
    connectedProviders.length + readyLocalModels.length + trustedRoots.length;
  const toolsHint = reachableCount > 0 ? String(reachableCount) : profile?.mode;
  const snapshotsHint = snapshotsState.snapshotsLoaded
    ? String(snapshotsState.snapshots.length)
    : undefined;

  // The engine/status banners, shown in whichever column the person is looking
  // at. On a surface they ride in its `pinned` slot — the widget rail, which
  // normally carries them on chat, is collapsed to zero width there.
  // While the first-run block is up it is the one thing to read, so the
  // not-connected banner stands down and lets it speak. The two say the same
  // thing to a person on a first launch ("nothing can happen yet, here is what
  // to do"), and stacking them turned a fresh window into two warnings before a
  // greeting. Note they are NOT the same condition underneath: `connected` is
  // the Agent Core being reachable, and first-run is a reachable core with
  // nothing configured. Anything that actually goes wrong still gets a banner,
  // because `statusBanner` is untouched by this.
  const quietBanner = !connected && firstRunActive;
  const banners =
    (!connected && !quietBanner) || statusBanner ? (
      <>
        {!connected && !quietBanner && (
          <Banner message="Addison's engine isn't connected. You can look around, but I can't chat just yet." />
        )}
        {statusBanner && (
          <Banner message={statusBanner} onDismiss={() => setStatusBanner(null)} />
        )}
      </>
    ) : null;
  // Only the banners ride in the surface's own `pinned` slot now. The consent
  // card is hoisted onto a fixed layer of its own (see the end of the render) —
  // inside <main> it sat under the restore modal's and the drawer's scrim.
  const surfacePinned = banners ?? undefined;

  // The anchored model popup's rows: the SAME real catalog the composer's menu
  // reads (cloud models from the connected providers + whatever is set up
  // locally), and picking one writes the SAME default state. `free` is never
  // inferred — only the core may call a model free.
  const defaultCloudId = resolveCloudModel(models.cloudModels, models.selectedCloudModel)?.id;
  const activeLocalId = models.selectedLocalModel ?? readyLocalModels[0]?.id;
  // Grouped BY COMPANY, which the picker draws as folders — so the old
  // "Model — Provider" suffix is gone: the folder says it once instead of once
  // per row. Order WITHIN a company is still the core's, and that is
  // load-bearing: a model the core has watched a provider refuse comes back sunk
  // to the end of its company, and the folders narrow that to the end of its own
  // family — last thing met either way, and struck through when it is met
  // (types/ui.ts owns why a refused model is marked rather than dropped).
  const modelPopupOptions: ModelPopupOption[] = [
    ...models.cloudModels.map((m) => ({
      key: `primary:${m.id}`,
      id: m.id,
      label: m.label,
      group: m.providerLabel ?? "Cloud",
      note: m.unavailable ? "unavailable" : m.free ? "free" : "quality",
      // The core's own sentence, forwarded whole: the row prints it under the
      // model's name, because "unavailable" alone is a dead end.
      unavailable: m.unavailable,
      selected: models.selectedRole !== "local" && m.id === defaultCloudId,
      onPick: () => {
        models.handleChangeDefaultCloudModel(m.id);
        setModelAnchor(null);
      },
    })),
    ...readyLocalModels.map((m) => ({
      key: `local:${m.id}`,
      id: m.id,
      label: m.label,
      group: "On this computer",
      note: "local",
      selected: models.selectedRole === "local" && m.id === activeLocalId,
      onPick: () => {
        models.handleChangeDefaultRole("local");
        models.handleSelectModel("local", m.id);
        setModelAnchor(null);
      },
    })),
  ];

  // The thread is handed straight to ChatThread, which decides for itself
  // whether it is empty and shows the greeting stack instead. The seeded
  // "welcome" line is RETIRED by the redesign — an invitation is not something
  // Addison already said, and the stack says the same thing in the surface's
  // own voice — so there is no longer a seed to filter out on the way in.
  //
  // The first-run block rides inside that stack while first-run is active (step
  // 1 = nothing configured; step 2 = a provider connected during this launch,
  // which also focuses the composer for the "say hello" nudge).
  const firstRunHeader = firstRunActive ? (
    <FirstRunBanner
      step={anyConfigured ? 2 : 1}
      onStartSetup={handleStartSetup}
      onSkip={() => setFirstRunDismissed(true)}
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
  // The nav entry for the code screen EXISTS only under Developer/Custom: passing
  // no handler is what removes the row (Sidebar renders it only when it has one),
  // so Simple has no way to reach the screen rather than a disabled row inviting
  // the question.
  const openCodeFromNav = developerSurfaces ? () => toggleSurface("code") : undefined;

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

  const railVisible = railOpen && view === "chat" && railBeside;
  // Whether widgets are on screen at all, in whichever layout applies. The header
  // button and the inline block both read this so they cannot disagree.
  const widgetsShowing = railBeside ? railOpen : inlineRailOpen;

  return (
    <div className="relative flex h-full flex-col overflow-hidden bg-paper text-[13px] text-ink">
      {/* One header across the whole window: the way out on the left (← from a
          surface, the sidebar chevron on chat), the view's name beside it, and
          the two live controls on the right. */}
      <header className="relative z-10 flex shrink-0 items-center justify-between gap-4 border-b border-line px-6 py-4">
        <span className="flex min-w-0 items-center gap-4">
          {/* The mark alone, far left. The brandbook's APP HEADER application
              pairs it with an "Addison" wordmark; that pairing is redundant in
              the app's own chrome — the tile already says it, and the word was
              spending the view title's width budget (the title truncated at
              320px). Owner decision 2026-07-26.
              It is also the way home: clicking it starts a new chat and returns
              to the thread, so the brand doubles as the one control that always
              gets you back to a blank page. That makes it a real button — the
              tile inside stays `aria-hidden`, and the NAME lives here, because a
              control whose only label is a logo is unusable by screen reader.
              Held while a turn or a permission prompt is in flight, exactly like
              the sidebar's "＋ New chat": abandoning a running turn by
              mis-clicking the logo is not a thing this should make easy. */}
          <button
            type="button"
            onClick={newChatFromNav}
            disabled={!connected || controlsBusy}
            title="New chat"
            aria-label="New chat"
            className="shrink-0 rounded-[3px] transition-opacity hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <AddisonMark size={22} />
          </button>
          {isSurface && (
            <button
              type="button"
              onClick={() => changeView("chat")}
              title="Back to chat"
              aria-label="Back to chat"
              className="shrink-0 text-[13px] text-muted transition-colors hover:text-ink max-md:min-h-[44px] max-md:min-w-[44px]"
            >
              ←
            </button>
          )}
          {/* On a narrow window the sidebar is a drawer, and the ☰ is the ONLY
              way to it — including from a surface, where the ← alone would
              strand a phone user with no way to reach Tools, Snapshots or their
              chats without going back to the thread first. */}
          {isMobile ? (
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="Menu"
              className="flex h-11 w-11 shrink-0 items-center justify-center text-[15px] text-disabled transition-colors hover:text-ink"
            >
              ☰
            </button>
          ) : (
            !isSurface && (
              <button
                type="button"
                onClick={() => setSideOpen((v) => !v)}
                title={sideOpen ? "Hide chats" : "Show chats"}
                aria-label={sideOpen ? "Hide chats" : "Show chats"}
                className="shrink-0 text-[12px] text-disabled transition-colors hover:text-ink"
              >
                {sideOpen ? "«" : "»"}
              </button>
            )
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
          {/* Offered at EVERY width on the chat view. It used to be gated on
              `railBeside`, on the reasoning that "below 1024px it would toggle
              something that is never on screen" — but below 1024 the rail is very
              much on screen, just inline at the foot of the thread, and it was
              rendered unconditionally. So the one control that could put it away
              was hidden exactly where it was needed (reported 2026-07-27). The
              button drives whichever layout is showing. */}
          {!isSurface && (
            <button
              type="button"
              onClick={() =>
                railBeside ? setRailOpen((v) => !v) : setInlineRailOpen((v) => !v)
              }
              title={widgetsShowing ? "Hide widgets" : "Show widgets"}
              aria-label={widgetsShowing ? "Hide widgets" : "Show widgets"}
              className="text-[12px] text-disabled transition-colors hover:text-ink"
            >
              {widgetsShowing ? "»" : "«"}
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
            // A collapsed column is 0px wide and fully transparent, but its
            // buttons stayed in the tab order: Tab landed on Tools / Snapshots /
            // Settings with the focus ring rendering nowhere, and Enter
            // navigated. `inert` is what actually removes them — and it is also
            // what makes the aria-hidden above legal, since focusable
            // descendants inside aria-hidden are an ARIA violation.
            {...inertWhen(!sideOpen)}
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
              onOpenCode={openCodeFromNav}
            />
          </div>
        )}

        {/* Centre column: the thread + composer, or a surface in their place. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 justify-center">
            {view === "chat" ? (
              <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center">
                {banners && (
                  <div className="flex w-full max-w-[580px] flex-col gap-2 pt-3">{banners}</div>
                )}
                <ChatThread
                  messages={turn.messages}
                  onRetry={turn.handleRetry}
                  retryAvailable={!turn.isWorking && Boolean(turn.lastUserText)}
                  onRewindTo={handleRewindTo}
                  showTechnicalDetails={Boolean(profile?.flags.rawDiagnostics)}
                  streamDisplay={turn.streamDisplay}
                  streamMessageId={turn.streamMessageId}
                  conversationKey={conversationsState.currentConversationId}
                  onSuggestion={(text) => setComposerSeed(text)}
                  header={firstRunHeader}
                  footer={
                    <>
                      {proposalBlock}
                      {widgetProposalBlock}
                      {offerBlock}
                      {/* Below 1024px there's no side rail (mobile drawer or the
                          768–1024 band), so the widgets live inline at the foot
                          of the thread — visible on the chat view, carrying the
                          work + consent blocks with them. Wide: work/consent go
                          inline only when the rail is hidden (otherwise they're
                          in the rail). */}
                      {!railBeside && inlineRailOpen ? (
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
                          onSetWidgetState={widgetsState.handleSetWidgetState}
                          onAskBuildWidget={handleAskBuildWidget}
                        />
                      ) : (
                        // `!widgetsShowing`, NOT `!railOpen`: with widgets closed on
                        // the narrow layout, `railOpen` is still true (it belongs to
                        // the wide layout), so keying on it left the work annotation
                        // and — the part that matters — a PENDING CONSENT CARD with
                        // nowhere to render. A consent surface that cannot be seen is
                        // a safety failure, not a cosmetic one; the same rule put the
                        // card above the modal scrim in `1241026`.
                        !widgetsShowing && (
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
              <SettingsPage
                connected={connected}
                pinned={surfacePinned}
                models={models}
                skills={skillsState}
                snapshots={snapshotsState}
                guards={guardsState}
                routing={routingState}
                workspace={workspaceState}
                mcp={mcpState}
                automations={automationsState}
                profile={profile}
                onSetProfile={handleSetProfile}
                diagnostics={diagnostics}
                onClearDiagnostics={clearDiagnostics}
                theme={themeChoice}
                onSetTheme={setThemeChoice}
                onOpenModelPopup={openModelPopup}
                onAskAddison={seedAsk}
                onOpenRestorePoints={() => setRestorePointsOpen(true)}
                scrollTarget={settingsScrollTarget}
                onScrolled={clearSettingsScrollTarget}
              />
            ) : view === "tools" ? (
              <ToolsSurface
                connected={connected}
                pinned={surfacePinned}
                providers={models.providers}
                roles={models.roles}
                trustedRoots={trustedRoots}
                showTrustedFolders={showTrustedFolders}
                mcpServers={mcpState.servers}
                workspaceBusy={workspaceState.busy}
                onAddKey={handleStartSetup}
                onStopTrusting={(dir) => void workspaceState.handleRevoke(dir)}
              />
            ) : view === "snapshots" ? (
              <SnapshotsSurface
                connected={connected}
                pinned={surfacePinned}
                snapshots={snapshotsState}
              />
            ) : view === "code" ? (
              // Gated on the ACTIVE PROFILE, exactly like the workspace-trust card
              // and the nav row above. Rendering nothing rather than falling through
              // to the next branch: a profile that changes underneath an open screen
              // must not leave a DIFFERENT surface drawn under the "Code" title. The
              // effect above returns to chat on the next tick.
              developerSurfaces ? (
                <CodeSurface
                  connected={connected}
                  pinned={surfacePinned}
                  roots={workspaceState.roots}
                  rootsLoaded={workspaceState.rootsLoaded}
                  review={codeReview}
                  theme={resolvedTheme}
                  turnWorking={turn.isWorking}
                  // The Activity Panel FOLLOWS the person here (see the rail's
                  // `work` prop below, which stands down on a surface). The consent
                  // card follows too, on App's own fixed layer at the end of this
                  // render — neither is ever in two places at once.
                  work={workBlock}
                />
              ) : null
            ) : (
              <Surface
                title="Build a widget"
                description="Describe what you want to keep an eye on, and Addison turns it into a small card in the right rail."
                pinned={surfacePinned}
              >
                <SurfaceSection label="Ideas to start from">
                  {WIDGET_IDEAS.map((idea) => (
                    <SurfaceRow
                      key={idea.prompt}
                      name={idea.name}
                      action="use"
                      actionAriaLabel={`Use the idea: ${idea.name}`}
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
            middle, and the rail's contents are about the conversation. Below
            1024px it isn't rendered at all: there is no room for two side
            columns, and the same widgets ride inline in the thread instead. */}
        {railBeside && (
          <div
            className="shrink-0 overflow-hidden"
            aria-hidden={!railVisible}
            // Same reason as the sidebar above: a 0px-wide, transparent rail
            // whose buttons still take Tab stops puts the focus ring nowhere and
            // lets Enter run a widget nobody can see.
            {...inertWhen(!railVisible)}
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
              // Same rule as the consent card below, and now it matters: the rail
              // is collapsed to zero width and `inert` on a surface, so "Addison's
              // work" was in the DOM and visible to nobody. The code screen renders
              // it itself, so standing down here is what keeps it from being in two
              // places at once.
              work={isSurface ? null : workBlock}
              // On a surface the consent card is on its own fixed layer instead
              // (see the end of the render) — the rail is collapsed to zero
              // width there, so a card left in it would be a blocking question
              // nobody can see or answer.
              consent={isSurface ? null : consentBlock}
              developer={profileModeNote === "open"}
              widgets={widgetsState.widgets}
              stats={widgetsState.stats}
              routines={widgetsState.railRoutines}
              onSetPinned={widgetsState.handleSetWidgetPinned}
              onDelete={widgetsState.handleDeleteWidget}
              onRunRoutine={widgetsState.handleRunWidgetRoutine}
              onRunCommandWidget={(id) => ipc.runWidget(id)}
              onSetWidgetState={widgetsState.handleSetWidgetState}
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
            onOpenCode={
              openCodeFromNav &&
              (() => {
                closeDrawer();
                toggleSurface("code");
              })
            }
            onCloseDrawer={closeDrawer}
          />
        </MobileDrawer>
      )}

      {/* Floating chrome, outside every animated container (see the file
          header). Both are reached from Settings and both close on Escape —
          the modal through the handler above, the popup in the capture phase. */}
      {/* `lastModelAnchor`, not `modelAnchor`: the popup has to stay on screen
          at the place it opened while it fades out, and `modelAnchor` is already
          null by then. Several paths close it (Escape, an outside click, picking
          a model, a profile change), and driving the exit off `open` means every
          one of them animates without having to remember to. */}
      {lastModelAnchor && modelPopupOptions.length > 0 && (
        <ModelPopup
          anchor={lastModelAnchor}
          open={Boolean(modelAnchor)}
          options={modelPopupOptions}
          onClose={closeModelPopup}
          returnFocus={returnFocusToModelTrigger}
        />
      )}
      {restorePointsOpen && (
        <RestorePointsModal
          connected={connected}
          snapshots={snapshotsState}
          // The footer's undo promise is mode-scoped: under OPEN, `run_command`
          // is invariant 2's explicit exemption from undo(), so "everything can
          // be undone" would be false there.
          mode={profile?.mode}
          onClose={() => setRestorePointsOpen(false)}
        />
      )}

      {isSurface && consentBlock && (
        <SurfaceConsentLayer>{consentBlock}</SurfaceConsentLayer>
      )}
    </div>
  );
}

/**
 * The pending consent card while a surface is showing, on a layer of its own.
 *
 * It used to ride in the surface's `pinned` slot inside <main>, which has no
 * stacking context — so the Restore points modal and the mobile drawer (both
 * `fixed inset-0 z-40`) drew their 55% scrim over it and ATE THE CLICK:
 * elementFromPoint at "Allow" returned the scrim, and pressing it closed the
 * modal instead of answering. A permission card holds the turn open, so that
 * was a dead end.
 *
 * Hoisted ABOVE the floating chrome rather than suppressing it, so the modal
 * underneath stays usable — hence z-50, which must stay strictly above every
 * `fixed z-40` overlay in the app. The layer itself is click-through
 * (`pointer-events-none`) so it never steals presses meant for the page below;
 * only the card takes them. Chat is untouched: there the card lives in the
 * widget rail, or inline in the thread when the rail is hidden.
 *
 * Exported for the regression test — the z-order relationship is the fix.
 */
export function SurfaceConsentLayer({ children }: { children: ReactNode }) {
  return (
    <div
      data-consent-layer=""
      className="pointer-events-none fixed inset-x-0 top-[72px] z-50 flex justify-center px-4"
    >
      <div className="pointer-events-auto w-full max-w-[580px] rounded-[7px] shadow-modal">
        {children}
      </div>
    </div>
  );
}

// React 18 has no typed `inert` prop (React 19 adds one), but the ATTRIBUTE is
// what browsers act on — it takes the subtree out of the tab order, out of the
// a11y tree, and out of hit-testing. Spread it as an attribute so a hidden
// column stops collecting focus stops nothing can render.
function inertWhen(hidden: boolean): Record<string, string> {
  return hidden ? { inert: "" } : {};
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

// The OS preference at the moment of the first render. Only used to seed
// `resolvedTheme`; the effect that applies the theme immediately re-computes it
// and keeps it in sync from then on. Guarded because jsdom and a design-review
// browser can both be missing matchMedia.
function prefersDark(): boolean {
  try {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  } catch {
    return false;
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
  // The arming half (step 8 phase 3) when the core sent one — the code to retype
  // and the preview it exists to make somebody read. Absent on every other card,
  // and the property is omitted rather than set to undefined so `request.arming`
  // is the whole of the question the card asks itself.
  const arming = parseArming(req.arming);
  return {
    toolId: typeof req.toolId === "string" ? req.toolId : "",
    label: typeof req.label === "string" ? req.label : "Addison would like to do something",
    description:
      typeof req.description === "string"
        ? req.description
        : "Addison is asking for your permission to continue.",
    riskTier: riskTier === "medium" || riskTier === "high" ? riskTier : "low",
    ...(arming ? { arming } : {}),
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
