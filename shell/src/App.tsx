// Addison — top-level app shell (Fern direction; design-brief-fern README §1).
//
// Three columns: the conversation Sidebar, the chat column (header + ChatThread +
// Composer), and the hideable WidgetRail. Settings is an in-window screen
// (SettingsPage) that replaces the chat column, not a drawer. This component owns
// the UI-chrome state and wires the Core → Frontend notifications (streamed
// text, permission prompts, tool activity, local-setup progress) into React
// state, and Frontend → Core actions back out through the typed `ipc`. The four
// big state clusters live in dedicated hooks (mechanical extractions from this
// file): useModelSelection, useWidgets, useTurn, useConversations.
//
// Visual direction is binding (CLAUDE.md; Fern direction, docs/design-brief-fern,
// amended 2026-07 v3): warm paper neutrals + one fern-green accent, a serif
// "correspondence" voice (Source Serif 4) beside a plain Public Sans UI, blocky
// live annotations vs. rounded ownable/actionable things, real typographic
// hierarchy for readers who are 54 and 68 — never a generic AI-chat template,
// never a model vendor's branding. Theme is class-driven (light default) and
// persisted in localStorage ("addison.theme").

import { useEffect, useMemo, useState } from "react";
import { Method, type PermissionRequest, type ActivityUpdate } from "./types/protocol";
import type { DisplayMessage, LocalSetupState, ProfileState } from "./types/ui";
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
import { WidgetRail } from "./components/WidgetRail";
import { WidgetProposalCard } from "./components/WidgetProposalCard";
import { Composer } from "./components/Composer";
import { PermissionCard } from "./components/PermissionCard";
import {
  RoutineProposalCard,
  type RoutineProposal,
} from "./components/RoutineProposalCard";
import { SettingsPage, API_KEYS_SECTION_ID } from "./components/SettingsPage";
import { FirstRunBanner } from "./components/FirstRunBanner";
import { Banner } from "./components/Banner";
import { BellLogo } from "./components/BellLogo";
import { MobileDrawer } from "./components/MobileDrawer";
import { BottomSheet } from "./components/BottomSheet";
import { useMediaQuery } from "./hooks/useMediaQuery";
import { useModelSelection } from "./hooks/useModelSelection";
import { useWidgets } from "./hooks/useWidgets";
import { useTurn } from "./hooks/useTurn";
import { useConversations } from "./hooks/useConversations";
import { asRecord, normalizeVariables } from "./lib/parse";

const THEME_KEY = "addison.theme";
const RAIL_OPEN_KEY = "addison.railOpen";
const SIDEBAR_COLLAPSED_KEY = "addison.sidebarCollapsed";

type Theme = "light" | "dark";

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
  // In-window screen: the live chat, or the Settings page (replaces the drawer).
  const [screen, setScreen] = useState<"chat" | "settings">("chat");
  // Fern app-shell chrome, both persisted. Rail hosts the widget column + the
  // "Addison's work"/consent blocks; hiding it moves those inline (§3–§4).
  const [railOpen, setRailOpen] = useState<boolean>(() => loadBool(RAIL_OPEN_KEY, true));
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() =>
    loadBool(SIDEBAR_COLLAPSED_KEY, false),
  );

  // Narrow-window (mobile) layout. Below the md breakpoint (768px — the same one
  // Tailwind's `md:` uses) the sidebar becomes a slide-over drawer and the widget
  // rail becomes a bottom sheet behind the top bar's bell. Both overlays are
  // ephemeral — deliberately NOT persisted (the drawer never is; the sheet's open
  // state is per-session). `isMobile` drives the structural swaps that CSS alone
  // can't express (which overlay exists, where consent cards render); purely
  // visual mobile tweaks stay in Tailwind `max-md:` variants.
  const isMobile = useMediaQuery("(max-width: 767.98px)");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  // Appearance (Fern direction). Light by default; the class on <html> drives the
  // whole palette. The inline script in index.html sets it before first paint to
  // avoid a flash; this keeps it in sync and persisted when the user toggles.
  const [theme, setThemeState] = useState<Theme>(loadTheme);
  const [routineProposal, setRoutineProposal] = useState<RoutineProposal | null>(null);

  // First-run experience (design-brief-fern §5). `startedUnconfigured` latches
  // once — true iff this launch began with nothing configured (or we're in a
  // disconnected design-review browser, where roles never load) — so connecting
  // a provider mid-launch advances the banner to step 2 rather than hiding it,
  // while a launch that began configured never shows it at all. `firstRunDismissed`
  // is "Skip for now": this launch only, deliberately not persisted.
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

  // --- The four extracted state clusters (mechanical moves from this file) ---
  const models = useModelSelection();
  const widgetsState = useWidgets({ connected, railOpen, setStatusBanner });
  const turn = useTurn({
    connected,
    setStatusBanner,
    selectedRole: models.selectedRole,
    selectedLocalModel: models.selectedLocalModel,
    selectedEffort: models.selectedEffort,
    effectiveLocalModel: models.effectiveLocalModel,
    effectiveCloudModel: models.effectiveCloudModel,
    maybeProposeWidget: widgetsState.maybeProposeWidget,
    // Invoked from runTurn's `finally` — at event time, well after render, when
    // the `conversationsState` const below is initialized. The lazy wrapper is
    // what keeps the hook call order acyclic.
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
    setScreen,
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

  // --- Wire up notifications + initial data on mount ------------------------
  useEffect(() => {
    if (!connected) return;
    const unsubs: Array<() => void> = [];

    unsubs.push(
      subscribe(Method.ConversationStreamChunk, (p) => {
        const params = p as StreamChunkParams;
        const text = params.text ?? params.delta ?? params.content ?? "";
        if (!text) return;
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
        const update: ActivityUpdate = {
          label: typeof p.label === "string" ? p.label : "Working…",
          toolId: typeof p.toolId === "string" ? p.toolId : "",
        };
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
  // this class) and persist it. The inline bg matches so a reload paints the
  // right color before the stylesheet is parsed.
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.style.backgroundColor = theme === "dark" ? "#171D1A" : "#F6F5F1";
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* non-fatal */
    }
  }, [theme]);

  function setTheme(next: Theme) {
    setThemeState(next);
  }

  // Persist the app-shell chrome toggles alongside the other prefs.
  useEffect(() => {
    saveBool(RAIL_OPEN_KEY, railOpen);
  }, [railOpen]);
  useEffect(() => {
    saveBool(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed);
  }, [sidebarCollapsed]);

  // Growing the window past the breakpoint reveals the static sidebar + rail, so
  // any open mobile overlay must not linger (and mustn't pop back if the window
  // shrinks again).
  useEffect(() => {
    if (!isMobile) {
      setDrawerOpen(false);
      setSheetOpen(false);
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
    setScreen("settings");
    setSettingsScrollTarget(API_KEYS_SECTION_ID);
  }

  // The dashed "＋ Ask Addison to build a widget" seeds the composer (does NOT
  // create anything) and switches to chat if we're on Settings.
  function handleAskBuildWidget() {
    setScreen("chat");
    setComposerSeed("Build me a widget that ");
  }

  // Window-level shortcuts: Escape returns from Settings to chat; Cmd/Ctrl+N
  // starts a new chat (unless a turn or permission prompt is in flight).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        // A mobile overlay takes Escape first (drawer, then sheet), before it
        // would fall through to leaving Settings.
        if (drawerOpen) {
          setDrawerOpen(false);
          return;
        }
        if (sheetOpen) {
          setSheetOpen(false);
          return;
        }
        if (screen === "settings") {
          setScreen("chat");
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
  }, [screen, connected, controlsBusy, drawerOpen, sheetOpen]);

  // --- Render ---------------------------------------------------------------
  // The two movable blocks (design-brief-fern §3–§4): the "Addison's work"
  // annotation and the consent card live in the widget rail when it's open, and
  // fall back inline in the thread when it's hidden. Assemble each once so it can
  // render in either slot without duplication.
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

  const profileLabel =
    profile?.activeProfile === "developer" ? "Developer profile" : "Simple profile";
  // In OPEN (Developer) mode the sidebar appends a dim, mono " · open" — the one
  // quiet acknowledgement that the safety posture is different. Nothing louder.
  const profileModeNote = profile?.mode === "open" ? "open" : undefined;

  // First-run render pieces. The pine banner rides in the chat column above the
  // thread while first-run is active; the serif greeting replaces the welcome
  // message only at step 1 (nothing configured yet) with an otherwise-empty
  // thread. Once a provider connects (step 2), the normal welcome returns so
  // Addison "introduces itself" per the step-2 copy.
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
  // conversation / Settings / New chat also closes the drawer (handoff §1).
  const closeDrawer = () => setDrawerOpen(false);
  const closeSheet = () => setSheetOpen(false);

  return (
    <div className="flex h-full bg-paper text-ink">
      {/* Desktop: the static left column. Below md it's replaced by the slide-over
          drawer (rendered at the end of this tree). */}
      {!isMobile && (
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
          conversations={conversationsState.conversations}
          currentConversationId={conversationsState.currentConversationId}
          onOpenConversation={conversationsState.handleOpenConversation}
          onNewChat={conversationsState.handleNewChat}
          newChatDisabled={!connected || controlsBusy}
          screen={screen}
          onOpenSettings={() => setScreen("settings")}
          profileLabel={profileLabel}
          modeNote={profileModeNote}
        />
      )}

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {screen === "settings" && (!connected || statusBanner) && (
          /* Same alignment rule as the chat screen: banners share the settings
             content's gutters and centered max-width, stacked with one gap. */
          <div className="px-4 pt-3 md:px-[44px]">
            <div className="mx-auto flex w-full max-w-[880px] flex-col gap-2">
              {!connected && (
                <Banner message="Addison's engine isn't connected. You can look around, but I can't chat just yet." />
              )}
              {statusBanner && (
                <Banner message={statusBanner} onDismiss={() => setStatusBanner(null)} />
              )}
            </div>
          </div>
        )}

        {screen === "settings" ? (
          <SettingsPage
            connected={connected}
            models={models}
            profile={profile}
            onSetProfile={handleSetProfile}
            diagnostics={diagnostics}
            onClearDiagnostics={clearDiagnostics}
            theme={theme}
            onSetTheme={setTheme}
            onBack={() => setScreen("chat")}
            scrollTarget={settingsScrollTarget}
            onScrolled={() => setSettingsScrollTarget(null)}
          />
        ) : (
          <>
            {/* Desktop chat header — active title left; undo (when undoable) +
                rail toggle right (design-brief-fern §2). Hidden below md. */}
            <header className="hidden items-baseline justify-between gap-4 border-b border-line px-[44px] py-3.5 md:flex">
              <span className="min-w-0 truncate text-control font-semibold tracking-emphasis text-ink-soft">
                {conversationsState.conversationTitle || "New conversation"}
              </span>
              <div className="flex shrink-0 items-baseline gap-[18px]">
                {hasUndoableActions && (
                  <button
                    type="button"
                    onClick={handleUndoLastAction}
                    className="text-meta font-medium text-muted hover:text-ink-soft"
                  >
                    <span aria-hidden="true">↺</span> Undo last action
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setRailOpen((v) => !v)}
                  className="text-meta font-medium text-fern-deep hover:text-fern"
                >
                  {railOpen ? "Hide widgets »" : "« Show widgets"}
                </button>
              </div>
            </header>

            {/* Mobile top bar (below md): ☰ opens the drawer · centered title ·
                bell opens the widget sheet. "Undo last action" moves into the
                sheet header. Safe-area top inset for a phone status bar. */}
            <header className="flex items-center gap-2 border-b border-line px-4 pt-[env(safe-area-inset-top)] md:hidden">
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                aria-label="Chats"
                className="flex h-11 w-11 shrink-0 items-center justify-center text-glyph text-ink-soft"
              >
                ☰
              </button>
              <span className="min-w-0 flex-1 truncate text-center text-control font-semibold text-ink-soft">
                {conversationsState.conversationTitle || "New conversation"}
              </span>
              <button
                type="button"
                onClick={() => setSheetOpen(true)}
                aria-label="Widgets"
                className="flex h-11 w-11 shrink-0 items-center justify-center"
              >
                <BellLogo size={19} className="text-fern" />
              </button>
            </header>

            {/* Body: centered chat column + (optional) widget rail, each with its
                own scroll. Full-bleed side padding below md; 44px gutters at md. */}
            <div className="flex min-h-0 flex-1 justify-center gap-[38px] px-4 md:px-[44px]">
              {/* The banner wrapper shares the chat column's exact width and
                  center (owner: banners were centered across column + rail and
                  read as off-balance). Same geometry as before for the thread:
                  the column centers in the space beside the rail. */}
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
                      {/* Consent always renders inline on mobile (the sheet may
                          be closed); on desktop it goes inline only when the
                          rail is hidden. The work block stays out of the thread
                          on mobile — it lives in the sheet. */}
                      {!isMobile && !railOpen && workBlock}
                      {(isMobile || !railOpen) && consentBlock}
                    </>
                  }
                />
              </div>
              {!isMobile && railOpen && (
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
              )}
            </div>

            <Composer
              connected={connected}
              turn={turn}
              models={models}
              draftSeed={composerSeed}
              onDraftSeedUsed={() => setComposerSeed(null)}
              focusSignal={composerFocusSignal}
            />
          </>
        )}
      </main>

      {/* Mobile slide-over drawer: the same Sidebar, in drawer mode. Picking a
          conversation / Settings / New chat closes it; so does the scrim (in
          MobileDrawer) and Escape (handled above). */}
      {isMobile && drawerOpen && (
        <MobileDrawer onClose={closeDrawer}>
          <Sidebar
            variant="drawer"
            collapsed={false}
            onToggleCollapsed={() => {}}
            conversations={conversationsState.conversations}
            currentConversationId={conversationsState.currentConversationId}
            onOpenConversation={(id) => {
              closeDrawer();
              conversationsState.handleOpenConversation(id);
            }}
            onNewChat={() => {
              closeDrawer();
              conversationsState.handleNewChat();
            }}
            newChatDisabled={!connected || controlsBusy}
            screen={screen}
            onOpenSettings={() => {
              closeDrawer();
              setScreen("settings");
            }}
            profileLabel={profileLabel}
            modeNote={profileModeNote}
          />
        </MobileDrawer>
      )}

      {/* Mobile widget bottom sheet (chat screen only): the same WidgetRail
          content, in sheet mode, with "Undo last action" moved into its header.
          Consent cards never appear here — they render inline in the thread. */}
      {isMobile && sheetOpen && screen === "chat" && (
        <BottomSheet onClose={closeSheet}>
          {hasUndoableActions && (
            <div className="flex shrink-0 justify-end pb-2 pt-1">
              <button
                type="button"
                onClick={handleUndoLastAction}
                className="min-h-[44px] text-meta font-medium text-muted hover:text-ink-soft"
              >
                <span aria-hidden="true">↺</span> Undo last action
              </button>
            </div>
          )}
          <WidgetRail
            variant="sheet"
            work={workBlock}
            developer={profileModeNote === "open"}
            widgets={widgetsState.widgets}
            stats={widgetsState.stats}
            routines={widgetsState.railRoutines}
            onSetPinned={widgetsState.handleSetWidgetPinned}
            onDelete={widgetsState.handleDeleteWidget}
            onRunRoutine={widgetsState.handleRunWidgetRoutine}
            onRunCommandWidget={(id) => ipc.runWidget(id)}
            onAskBuildWidget={() => {
              closeSheet();
              handleAskBuildWidget();
            }}
          />
        </BottomSheet>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small pure helpers — defensive parsing of free-form JSON-RPC payloads, since
// the Python side's result/notification shapes aren't pinned in protocol.ts.
// ---------------------------------------------------------------------------
// Appearance persists like the default role. Light unless the user chose dark.
function loadTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t === "light" || t === "dark") return t;
  } catch {
    /* localStorage may be unavailable; fall through to the default */
  }
  return "light";
}

// Boolean prefs (rail open / sidebar collapsed) persist as "1"/"0".
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

// Parse `profile.get` defensively, like the other core payloads. `activeProfile`
// defaults to "simple" and every flag defaults to false, so a partial or missing
// payload degrades to the protected Simple surface rather than exposing anything.
function normalizeProfile(result: unknown): ProfileState | null {
  const obj = asRecord(result);
  if (!obj) return null;
  const profiles = Array.isArray(obj.profiles)
    ? obj.profiles.flatMap((p) => {
        const rp = asRecord(p);
        if (!rp || typeof rp.id !== "string") return [];
        return [
          {
            id: rp.id,
            label: typeof rp.label === "string" ? rp.label : rp.id,
            description: typeof rp.description === "string" ? rp.description : "",
          },
        ];
      })
    : [];
  const flags = asRecord(obj.flags) ?? {};
  // The policy mode ("safe" | "open") the active profile runs under (policy.py).
  // Anything unrecognized falls back to "safe" — an unknown surface never
  // escalates the safety model.
  const mode = obj.mode === "open" ? "open" : "safe";
  return {
    activeProfile: typeof obj.activeProfile === "string" ? obj.activeProfile : "simple",
    profiles,
    mode,
    flags: {
      exposeRoutinePlan: flags.exposeRoutinePlan === true,
      rawDiagnostics: flags.rawDiagnostics === true,
      headlessCli: flags.headlessCli === true,
      byokFirstOnboarding: flags.byokFirstOnboarding === true,
    },
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
