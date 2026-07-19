// Addison — top-level app shell (Fern direction; design-brief-fern README §1).
//
// Three columns: the conversation Sidebar, the chat column (header + ChatThread +
// Composer), and the hideable WidgetRail. Settings is an in-window screen
// (SettingsPage) that replaces the chat column, not a drawer. This component owns
// the conversation/turn/UI-chrome state and wires the Core → Frontend notifications
// (streamed text, permission prompts, tool activity, local-setup progress) into
// React state, and Frontend → Core actions back out through the typed `ipc`.
//
// Visual direction is binding (CLAUDE.md; Fern direction, docs/design-brief-fern,
// amended 2026-07 v3): warm paper neutrals + one fern-green accent, a serif
// "correspondence" voice (Source Serif 4) beside a plain Public Sans UI, blocky
// live annotations vs. rounded ownable/actionable things, real typographic
// hierarchy for readers who are 54 and 68 — never a generic AI-chat template,
// never a model vendor's branding. Theme is class-driven (light default) and
// persisted in localStorage ("addison.theme").

import { useEffect, useMemo, useRef, useState } from "react";
import { Method, type ModelRole, type PermissionRequest, type ActivityUpdate } from "./types/protocol";
import type {
  CloudModel,
  ConversationSummary,
  DisplayMessage,
  LocalSetupState,
  ProfileState,
  RoleOption,
  Widget,
  WidgetProposal,
  Stats,
} from "./types/ui";
import {
  ipc,
  isEngineConnected,
  storeProviderKey,
  deleteProviderKey,
  subscribe,
  subscribeStatus,
  subscribeCoreState,
  subscribeDiagnostics,
  type StreamChunkParams,
  type LocalSetupProgressParams,
  type DiagnosticEntry,
  type ProviderInfo,
  type RawError,
} from "./ipc/client";
import { ChatThread } from "./components/ChatThread";
import { ActivityPanel } from "./components/ActivityPanel";
import { Sidebar } from "./components/Sidebar";
import { WidgetRail, type RailRoutine, type RunOutcome } from "./components/WidgetRail";
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

const DEFAULT_ROLE_KEY = "addison.defaultRole";
const CLOUD_MODEL_KEY = "addison.cloudModel";
const EFFORT_KEY = "addison.effort";
const THEME_KEY = "addison.theme";
const RAIL_OPEN_KEY = "addison.railOpen";
const SIDEBAR_COLLAPSED_KEY = "addison.sidebarCollapsed";

type Theme = "light" | "dark";

const WELCOME: DisplayMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "Hello — I'm Addison. Tell me what you'd like help with, and I'll ask first " +
    "before doing anything on your computer. You can always undo.",
};

export function App() {
  const connected = useMemo(() => isEngineConnected(), []);

  const [messages, setMessages] = useState<DisplayMessage[]>([WELCOME]);
  const [isWorking, setIsWorking] = useState(false);
  const [permission, setPermission] = useState<PermissionRequest | null>(null);

  const [currentActivity, setCurrentActivity] = useState<ActivityUpdate | null>(null);
  const [activities, setActivities] = useState<ActivityUpdate[]>([]);
  const [hasUndoableActions, setHasUndoableActions] = useState(false);
  const [lastUndoDetail, setLastUndoDetail] = useState<string | null>(null);
  // Mirrors the core's session redo stack: set from undo/redo responses,
  // cleared whenever a new tool action lands (the core clears its stack too).
  const [canRedo, setCanRedo] = useState(false);
  // One-shot composer prefill for rewind's edit-and-resend.
  const [composerSeed, setComposerSeed] = useState<string | null>(null);
  // Identifies the turn whose IPC result may still touch shared turn state (the
  // assistant message, isWorking, the activity line). Stop and every new turn
  // reassign it, so a result arriving late from an abandoned turn — the core has
  // no cancel, so its work keeps landing after Stop (see handleStop) — is dropped
  // instead of resurrecting stopped text or re-enabling the composer mid-turn.
  const currentTurnRef = useRef<string | null>(null);

  const [roles, setRoles] = useState<RoleOption[]>([]);
  // Whether we've actually fetched roles at least once this launch. Distinguishes
  // "not loaded yet" from "loaded, genuinely nothing configured" so the first-run
  // banner doesn't flash for a configured user during the mount fetch.
  const [rolesLoaded, setRolesLoaded] = useState(false);
  const [cloudModels, setCloudModels] = useState<CloudModel[]>([]);
  // Multi-provider API keys (owner decision 2026-07-18). Non-secret status only —
  // the keys themselves live in the OS keychain and never reach the webview.
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selectedRole, setSelectedRole] = useState<ModelRole>(loadDefaultRole());
  const [selectedCloudModel, setSelectedCloudModel] = useState<string | undefined>(
    loadStored(CLOUD_MODEL_KEY),
  );
  const [selectedLocalModel, setSelectedLocalModel] = useState<string | undefined>(undefined);
  const [selectedEffort, setSelectedEffort] = useState<string | undefined>(
    loadStored(EFFORT_KEY),
  );
  const [localSetup, setLocalSetup] = useState<LocalSetupState | null>(null);

  const [statusBanner, setStatusBanner] = useState<string | null>(null);
  // In-window screen: the live chat, or the Settings page (replaces the drawer).
  const [screen, setScreen] = useState<"chat" | "settings">("chat");
  // Fern app-shell chrome, both persisted. Rail hosts the widget column + the
  // "Addison's work"/consent blocks; hiding it moves those inline (§3–§4).
  const [railOpen, setRailOpen] = useState<boolean>(() => loadBool(RAIL_OPEN_KEY, true));
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() =>
    loadBool(SIDEBAR_COLLAPSED_KEY, false),
  );
  // Appearance (Fern direction). Light by default; the class on <html> drives the
  // whole palette. The inline script in index.html sets it before first paint to
  // avoid a flash; this keeps it in sync and persisted when the user toggles.
  const [theme, setThemeState] = useState<Theme>(loadTheme);
  const [lastUserText, setLastUserText] = useState<string | null>(null);
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

  // Widgets (declarative specs) + core-computed stats for the rail. Widgets are
  // proposed like routines (draft-in-core, saved only on confirm); the token
  // meter + connections cards read `stats`, refreshed on a light schedule.
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [railRoutines, setRailRoutines] = useState<RailRoutine[]>([]);
  const [widgetProposal, setWidgetProposal] = useState<WidgetProposal | null>(null);

  // Conversations. The core mints a conversation per launch, but the frontend
  // doesn't learn its id until it starts or loads one — `null` means "the launch
  // conversation", and the sidebar marks no row current until an id is known. The
  // list lives permanently in the sidebar (it replaced the old HistoryView): it's
  // loaded on mount and refreshed after each completed turn + after new/load, so a
  // new chat's auto-title appears without a reload.
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  // The active conversation's title, shown in the chat header. Null → the
  // "New conversation" fallback (an untitled or not-yet-titled chat).
  const [conversationTitle, setConversationTitle] = useState<string | null>(null);
  // A stable mirror of the current id so the post-turn list refresh (which runs
  // in an async `finally`) reads the up-to-date value, not a stale closure.
  const currentConversationIdRef = useRef<string | null>(null);
  useEffect(() => {
    currentConversationIdRef.current = currentConversationId;
  }, [currentConversationId]);

  // Profiles (§4.7). Simple by default; null until the core answers (and while
  // disconnected — the Settings section then shows a quiet placeholder).
  const [profile, setProfile] = useState<ProfileState | null>(null);
  // A small ring of the most recent raw diagnostics (Developer only). Captured
  // globally from client.ts regardless of profile; only rendered when the
  // raw-diagnostics flag is on, so Simple never sees it.
  const [diagnostics, setDiagnostics] = useState<DiagnosticEntry[]>([]);

  // --- Wire up notifications + initial data on mount ------------------------
  useEffect(() => {
    if (!connected) return;
    const unsubs: Array<() => void> = [];

    unsubs.push(
      subscribe(Method.ConversationStreamChunk, (p) => {
        const params = p as StreamChunkParams;
        const text = params.text ?? params.delta ?? params.content ?? "";
        if (!text) return;
        setMessages((prev) =>
          prev.map((m) => (m.pending ? { ...m, content: m.content + text } : m)),
        );
      }),
    );

    unsubs.push(
      subscribe(Method.PermissionRequestGrant, (p) => {
        setPermission(normalizePermission(p));
      }),
    );

    unsubs.push(
      subscribe(Method.ToolActivityUpdate, (p) => {
        const update: ActivityUpdate = {
          label: typeof p.label === "string" ? p.label : "Working…",
          toolId: typeof p.toolId === "string" ? p.toolId : "",
        };
        setCurrentActivity(update);
        setActivities((prev) => [...prev, update]);
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
        setLocalSetup((prev) => {
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
          refreshRoles();
          refreshProviders();
          refreshProfile();
          refreshConversations();
          refreshWidgets();
          refreshStats();
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

    refreshRoles();
    refreshProviders();
    refreshProfile();
    refreshConversations();
    refreshWidgets();
    refreshStats();

    return () => unsubs.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Refresh stats on a 60s interval WHILE the rail is open (cleared on hide /
  // unmount). No websockets, no busywork — the rail also refreshes on mount and
  // after each completed turn.
  useEffect(() => {
    if (!connected || !railOpen) return;
    const t = setInterval(() => refreshStats(), 60_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, railOpen]);

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

  // Whether any model role is set up right now.
  const anyConfigured = roles.some((r) => r.configured);

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
    if (rolesLoaded) setStartedUnconfigured(!anyConfigured);
  }, [connected, rolesLoaded, anyConfigured, startedUnconfigured]);

  // First-run is "active" until the user configures something OR skips. Once a
  // provider connects mid-launch (anyConfigured flips true) the banner advances
  // to step 2 and nudges the user to say hello — focus the composer for them.
  const firstRunActive = startedUnconfigured === true && !firstRunDismissed;
  useEffect(() => {
    if (firstRunActive && anyConfigured) {
      setComposerFocusSignal((n) => n + 1);
    }
  }, [firstRunActive, anyConfigured]);

  // Once roles load, make sure the selected role is one that's actually set up.
  useEffect(() => {
    const configured = roles.filter((r) => r.configured);
    if (configured.length === 0) return;
    if (!configured.some((r) => r.role === selectedRole)) {
      setSelectedRole(configured[0].role);
    }
  }, [roles, selectedRole]);

  // Once the cloud catalog loads, make sure the selected cloud model is really
  // in it — otherwise fall back to the catalog's default.
  useEffect(() => {
    if (cloudModels.length === 0) return;
    setSelectedCloudModel((prev) =>
      prev && cloudModels.some((m) => m.id === prev) ? prev : defaultCloudModel(cloudModels)?.id,
    );
  }, [cloudModels]);

  // Keep the effort level valid for the active cloud model: clear it for models
  // that offer no levels, and reset to the model's default when the current one
  // isn't among that model's levels.
  useEffect(() => {
    const model = cloudModels.find((m) => m.id === selectedCloudModel);
    if (!model) return;
    setSelectedEffort((prev) => pickEffort(model, prev));
  }, [cloudModels, selectedCloudModel]);

  // Persist the picks alongside the default role so they survive a restart.
  useEffect(() => {
    saveStored(CLOUD_MODEL_KEY, selectedCloudModel);
  }, [selectedCloudModel]);
  useEffect(() => {
    saveStored(EFFORT_KEY, selectedEffort);
  }, [selectedEffort]);

  function refreshRoles() {
    if (!isEngineConnected()) return;
    ipc
      .availableRoles()
      .then((res) => {
        setRoles(normalizeRoles(res));
        setCloudModels(normalizeCloudModels(res));
        setRolesLoaded(true);
      })
      .catch(() => {
        /* leave the selector on placeholders if we can't read roles */
      });
  }

  function refreshProviders() {
    if (!isEngineConnected()) return;
    ipc
      .listProviders()
      .then(setProviders)
      .catch(() => {
        /* leave the API-keys card on its last-known rows if we can't read them */
      });
  }

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

  // Refresh the sidebar's conversation list. When `adopt` is set and we don't yet
  // know the current conversation's id (the launch conversation, whose id the
  // frontend never learns until a turn lands), take the newest row as current —
  // that's the chat a just-finished turn belongs to — so the sidebar highlights
  // it and the header shows its freshly minted auto-title. Otherwise just refresh
  // the current row's title in place.
  function refreshConversations(adopt = false) {
    if (!isEngineConnected()) return;
    ipc
      .listConversations()
      .then((list) => {
        setConversations(list);
        const currentId = currentConversationIdRef.current;
        if (currentId != null) {
          const match = list.find((c) => c.id === currentId);
          if (match) setConversationTitle(match.title);
        } else if (adopt && list.length > 0) {
          setCurrentConversationId(list[0].id);
          setConversationTitle(list[0].title);
        }
      })
      .catch(() => {
        /* leave the sidebar list as-is if we can't read it */
      });
  }

  function refreshWidgets() {
    if (!isEngineConnected()) return;
    ipc
      .listWidgets()
      .then(setWidgets)
      .catch(() => {
        /* leave the rail on its last-known widgets if we can't read them */
      });
    // A routine widget needs its routine's variables to prompt on Run — keep a
    // light copy of the library alongside the widgets.
    ipc
      .listRoutines()
      .then((res) => setRailRoutines(normalizeRailRoutines(res)))
      .catch(() => {
        /* leave the routine metadata as-is */
      });
  }

  function refreshStats() {
    if (!isEngineConnected()) return;
    ipc
      .getStats()
      .then(setStats)
      .catch(() => {
        /* leave the token meter / connections on their last-known values */
      });
  }

  // Switching a profile takes effect immediately (no restart). Re-fetch so the
  // new flags reshape the surface right away; quietly no-op if the switch fails.
  function handleSetProfile(profileId: string) {
    if (!isEngineConnected()) return;
    ipc
      .setProfile(profileId)
      .then(() => refreshProfile())
      .catch((err) => {
        setStatusBanner(
          err instanceof Error ? err.message : "I couldn't switch the profile.",
        );
      });
  }

  function clearDiagnostics() {
    setDiagnostics([]);
  }

  // --- Turn lifecycle -------------------------------------------------------
  async function runTurn(text: string, opts: { isRetry?: boolean } = {}) {
    const assistantId = uid();
    const userId = uid();
    currentTurnRef.current = assistantId;
    setMessages((prev) => {
      const base = opts.isRetry
        ? dropTrailingAssistant(prev)
        : [...prev, { id: userId, role: "user", content: text } as DisplayMessage];
      return [...base, { id: assistantId, role: "assistant", content: "", pending: true }];
    });

    setLastUserText(text);
    setActivities([]);
    setCurrentActivity(null);
    setPermission(null);
    setIsWorking(true);

    try {
      // Deliver the *effective* model for the active role. For "local", fall
      // back to the first model when the dropdown was never touched (the picker
      // shows it as selected). For cloud, send the picked model + its effort
      // level; effort never applies to local models (§4.1.1 B).
      const isLocal = selectedRole === "local";
      const modelId = isLocal
        ? effectiveLocalModel("local", selectedLocalModel)
        : effectiveCloudModel();
      const effort = isLocal ? undefined : selectedEffort;
      const res = await ipc.sendMessage(text, selectedRole, modelId, effort);
      // Stopped or superseded by a newer turn while we were waiting — drop this
      // result so it can't overwrite "(Stopped.)" or a later turn's answer.
      if (currentTurnRef.current !== assistantId) return;
      const finalText = extractFinalText(res);
      // The core's persisted ids: what "Rewind to here" must anchor on.
      const ids = asRecord(res);
      const userStoreId = typeof ids?.userMessageId === "string" ? ids.userMessageId : undefined;
      const assistantStoreId =
        typeof ids?.assistantMessageId === "string" ? ids.assistantMessageId : undefined;
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id === assistantId) {
            return { ...m, pending: false, content: finalText ?? m.content, storeId: assistantStoreId };
          }
          if (m.id === userId) {
            return { ...m, storeId: userStoreId };
          }
          return m;
        }),
      );
      // Composer path: if the user asked Addison to build a widget, draft one from
      // the just-finished conversation (nothing is saved until they confirm).
      maybeProposeWidget(text);
    } catch (err) {
      // Same guard on the failure path: an abandoned turn's error must not
      // replace the stopped message or a newer turn's content.
      if (currentTurnRef.current !== assistantId) return;
      const message = err instanceof Error ? err.message : "Something went wrong.";
      // Developer-only: the client attaches the real exception text as `.raw`.
      // We keep it on the message; ChatThread renders it only when the
      // raw-diagnostics flag is on, so the plain message is all Simple ever sees.
      const raw = (err as RawError | undefined)?.raw;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                pending: false,
                failed: true,
                // The core and the IPC client both send complete plain-language
                // sentences with a next step — render them as-is, no re-wrapping.
                content: m.content || message,
                raw: typeof raw === "string" ? raw : undefined,
              }
            : m,
        ),
      );
    } finally {
      // Only the still-current turn clears the working/activity state; an
      // abandoned turn's cleanup would otherwise re-enable the composer and hide
      // the activity line while a newer turn is still running.
      if (currentTurnRef.current === assistantId) {
        currentTurnRef.current = null;
        setIsWorking(false);
        setCurrentActivity(null);
        // A turn just landed: refresh the sidebar so a new chat's auto-title
        // appears, and adopt the launch conversation as current if we didn't
        // know its id yet. Usage changed too, so refresh the token meter.
        refreshConversations(true);
        refreshStats();
      }
    }
  }

  function handleSend(text: string) {
    if (!connected) {
      setStatusBanner("Addison's engine isn't connected yet, so I can't reply.");
      return;
    }
    void runTurn(text);
  }

  function handleRetry() {
    if (!connected || isWorking || !lastUserText) return;
    void runTurn(lastUserText, { isRetry: true });
  }

  function handleStop() {
    // The v1 IPC contract has no core-side cancel method, so Stop halts the
    // webview turn: it stops accepting streamed text and re-enables the input.
    // Abandon the turn so its still-in-flight result can't land later and
    // overwrite the "(Stopped.)" message (the core keeps working regardless).
    currentTurnRef.current = null;
    setIsWorking(false);
    setCurrentActivity(null);
    setMessages((prev) =>
      prev.map((m) =>
        m.pending
          ? { ...m, pending: false, content: m.content || "(Stopped.)" }
          : m,
      ),
    );
  }

  function handleRespondPermission(allow: boolean) {
    const p = permission;
    setPermission(null);
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
    setMessages((prev) => {
      before = prev;
      const idx = prev.findIndex((m) => m.storeId === storeId);
      if (idx === -1) return prev;
      anchorText = prev[idx].content;
      return prev.slice(0, idx);
    });
    setPermission(null);
    ipc
      .rewindConversation(storeId)
      .then(() => {
        if (anchorText) setComposerSeed(anchorText);
      })
      .catch((err) => {
        setMessages(before);
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

  // The models configured under the "local" role, or [] when none is set up.
  function localModelOptions(): { id: string; label: string }[] {
    return roles.find((r) => r.role === "local" && r.configured)?.models ?? [];
  }

  // The model id we should actually deliver for a role. For "local", fall back
  // to the first configured model when the user hasn't picked one — the picker
  // already displays that first model as selected, so state and delivery agree.
  function effectiveLocalModel(role: ModelRole, picked?: string): string | undefined {
    if (role !== "local") return undefined;
    const models = localModelOptions();
    if (picked && models.some((m) => m.id === picked)) return picked;
    return models[0]?.id;
  }

  // The cloud model id to deliver: the pick if it's still in the catalog, else
  // the catalog's default. Mirrors how the picker resolves the shown selection.
  function effectiveCloudModel(): string | undefined {
    if (selectedCloudModel && cloudModels.some((m) => m.id === selectedCloudModel)) {
      return selectedCloudModel;
    }
    return defaultCloudModel(cloudModels)?.id;
  }

  // The picker hands back a role + model id together. Cloud picks also carry an
  // effort level (reset to the model's default when the old one doesn't fit);
  // local picks never do.
  function handleSelectModel(role: ModelRole, modelId: string) {
    setSelectedRole(role);
    if (role === "local") {
      setSelectedLocalModel(modelId);
      ipc.setRoleForNextMessage("local", modelId).catch(() => {});
      return;
    }
    setSelectedCloudModel(modelId);
    const model = cloudModels.find((m) => m.id === modelId);
    const effort = pickEffort(model, selectedEffort);
    setSelectedEffort(effort);
    ipc.setRoleForNextMessage("primary", modelId, effort).catch(() => {});
  }

  function handleSelectEffort(effort: string) {
    setSelectedEffort(effort);
    // Effort is a cloud-model notion; only hint the core when cloud is active.
    if (selectedRole === "primary") {
      ipc.setRoleForNextMessage("primary", effectiveCloudModel(), effort).catch(() => {});
    }
  }

  // Settings' persistent "default model" control changes the same cloud pick.
  function handleChangeDefaultCloudModel(modelId: string) {
    setSelectedCloudModel(modelId);
    const model = cloudModels.find((m) => m.id === modelId);
    setSelectedEffort((prev) => pickEffort(model, prev));
  }

  // --- Local model setup (§4.1.2): explicit, opt-in, one at a time -----------
  function handleStartLocalSetup(modelId: string) {
    if (!isEngineConnected()) return;
    setLocalSetup({ modelId, status: "running", stage: "Getting ready", message: "Getting ready…" });
    ipc
      .startLocalSetup(modelId)
      .then(() => {
        setLocalSetup((prev) =>
          prev && prev.modelId === modelId
            ? { ...prev, status: "done", percent: 100, message: undefined, error: undefined }
            : prev,
        );
        // The new model now exists under the local role — refresh so it appears
        // in the chat's model selector.
        refreshRoles();
      })
      .catch((err) => {
        const message =
          err instanceof Error ? err.message : "Setting up the local model didn't work.";
        setLocalSetup((prev) =>
          prev && prev.modelId === modelId
            ? { ...prev, status: "error", error: message }
            : { modelId, status: "error", error: message },
        );
      });
  }

  function handleChangeDefaultRole(role: ModelRole) {
    setSelectedRole(role);
    saveDefaultRole(role);
    ipc.setRoleForNextMessage(role).catch(() => {});
  }

  // Connect a provider (multi-provider, owner decision 2026-07-18). The key (if any)
  // goes straight to the OS keychain via the Rust command; then the core validates it
  // with one tiny request and records the connection. On failure we throw the plain
  // error so the row can show it (and offer Remove to clear the stored key); the
  // picker's model union is refreshed either way.
  async function handleConnectProvider(provider: string, key: string, baseUrl?: string) {
    if (key) await storeProviderKey(provider, key);
    let result;
    try {
      result = await ipc.connectProvider(provider, baseUrl);
    } finally {
      refreshProviders();
      refreshRoles();
    }
    if (!result.ok) {
      throw new Error(result.error || "Couldn't connect. Check the key and try again.");
    }
  }

  // Remove a provider's key (the "Remove" action): delete it from the keychain and
  // clear the core's connection metadata, then refresh the card + the model union.
  async function handleRemoveProvider(provider: string) {
    try {
      await deleteProviderKey(provider);
      await ipc.disconnectProvider(provider);
    } finally {
      refreshProviders();
      refreshRoles();
    }
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

  // --- Widgets (declarative specs): propose -> card -> explicit save ---------
  // Called after a turn whose user message mentioned a widget: draft one from the
  // conversation. A refusal (the core can't make one yet) is silent — no card.
  function maybeProposeWidget(userText: string) {
    if (!isEngineConnected() || !/widget/i.test(userText)) return;
    ipc
      .proposeWidget()
      .then((proposal) => setWidgetProposal(proposal))
      .catch(() => {
        /* refusal / nothing to propose — stay quiet */
      });
  }

  function handleAddWidget() {
    setWidgetProposal(null);
    ipc
      .confirmWidget(true)
      .then((res) => {
        if (res.ok) {
          setStatusBanner("Added the widget — it's in your rail.");
          refreshWidgets();
        } else if (res.error) {
          setStatusBanner(res.error);
        }
      })
      .catch((err) => {
        setStatusBanner(err instanceof Error ? err.message : "I couldn't add that widget.");
      });
  }

  function handleDismissWidgetProposal() {
    setWidgetProposal(null);
    // Let the core drop its held draft too (accept:false).
    ipc.confirmWidget(false).catch(() => {});
  }

  function handleSetWidgetPinned(id: string, pinned: boolean) {
    ipc
      .setWidgetPinned(id, pinned)
      .then((res) => {
        if (!res.ok && res.error) setStatusBanner(res.error);
        refreshWidgets();
      })
      .catch(() => setStatusBanner("Couldn't change that widget just now."));
  }

  function handleDeleteWidget(id: string) {
    ipc
      .deleteWidget(id)
      .then(() => refreshWidgets())
      .catch(() => setStatusBanner("Couldn't remove that widget just now."));
  }

  // Run a routine straight from its widget (§6.5 variable prompts happen in the
  // card). Returns the plain outcome for the card to show; refreshes stats since
  // a run may have used the model.
  async function handleRunWidgetRoutine(
    routineId: string,
    variables: Record<string, string>,
  ): Promise<RunOutcome> {
    try {
      const res = (await ipc.runRoutine(routineId, variables)) as Record<string, unknown>;
      const ok = res?.ok === true;
      const detail =
        typeof res?.detail === "string" && res.detail
          ? res.detail
          : ok
            ? "Done — every step finished."
            : "It didn't finish. Nothing else was changed.";
      refreshStats();
      return { ok, detail };
    } catch (err) {
      return { ok: false, detail: err instanceof Error ? err.message : "That routine couldn't run." };
    }
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

  // --- Conversations --------------------------------------------------------
  // Sidebar controls are held while a turn is running or a permission prompt is
  // open — switching conversations mid-turn would strand in-flight work.
  const controlsBusy = isWorking || permission != null;

  // Clear the per-turn/per-conversation transient state. Deliberately leaves the
  // global action undo/redo state (hasUndoableActions / canRedo) alone — that's
  // core session state, not tied to which conversation is on screen.
  function resetTransientState() {
    currentTurnRef.current = null;
    setIsWorking(false);
    setActivities([]);
    setCurrentActivity(null);
    setPermission(null);
    setLastUserText(null);
    setRoutineProposal(null);
    setComposerSeed(null);
  }

  function handleNewChat() {
    if (!connected || controlsBusy) return;
    ipc
      .newConversation()
      .then((id) => {
        resetTransientState();
        setMessages([WELCOME]);
        setCurrentConversationId(id);
        setConversationTitle(null);
        setScreen("chat");
        // The new (still empty) conversation may not be in the list until its
        // first turn; refresh anyway so an existing row is reconciled.
        refreshConversations();
      })
      .catch(() => setStatusBanner("Couldn't start a new conversation."));
  }

  function handleOpenConversation(id: string) {
    ipc
      .loadConversation(id)
      .then((loaded) => {
        const rows: DisplayMessage[] = loaded.messages.map((row) => ({
          id: row.id,
          storeId: row.id,
          role: normalizeRole(row.role),
          content: row.content,
        }));
        resetTransientState();
        setMessages(rows);
        setCurrentConversationId(loaded.conversationId || id);
        setConversationTitle(
          loaded.title ?? conversations.find((c) => c.id === (loaded.conversationId || id))?.title ?? null,
        );
        setScreen("chat");
      })
      .catch((err) => {
        // Surface the plain-language reason (e.g. the core's "Couldn't find that
        // conversation.").
        setStatusBanner(
          err instanceof Error ? err.message : "Couldn't open that conversation.",
        );
      });
  }

  // Window-level shortcuts: Escape returns from Settings to chat; Cmd/Ctrl+N
  // starts a new chat (unless a turn or permission prompt is in flight).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && screen === "settings") {
        setScreen("chat");
        return;
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === "n" || e.key === "N")) {
        if (connected && !controlsBusy) {
          e.preventDefault();
          handleNewChat();
        }
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, connected, controlsBusy]);

  // --- Render ---------------------------------------------------------------
  // The two movable blocks (design-brief-fern §3–§4): the "Addison's work"
  // annotation and the consent card live in the widget rail when it's open, and
  // fall back inline in the thread when it's hidden. Assemble each once so it can
  // render in either slot without duplication.
  const hasWork =
    isWorking || activities.length > 0 || Boolean(lastUndoDetail) || canRedo;
  const workBlock = hasWork ? (
    <ActivityPanel
      isWorking={isWorking}
      current={currentActivity}
      activities={activities}
      canRedo={canRedo}
      onRedoLastAction={handleRedoLastAction}
      lastUndoDetail={lastUndoDetail}
      onProposeRoutine={connected ? handleProposeRoutine : undefined}
    />
  ) : null;
  const consentBlock = permission ? (
    <PermissionCard request={permission} onRespond={handleRespondPermission} />
  ) : null;
  const proposalBlock = routineProposal ? (
    <RoutineProposalCard
      proposal={routineProposal}
      onSave={handleConfirmRoutine}
      onCancel={() => setRoutineProposal(null)}
    />
  ) : null;
  const widgetProposalBlock = widgetProposal ? (
    <WidgetProposalCard
      proposal={widgetProposal}
      onAdd={handleAddWidget}
      onCancel={handleDismissWidgetProposal}
    />
  ) : null;

  const profileLabel =
    profile?.activeProfile === "developer" ? "Developer profile" : "Simple profile";

  // First-run render pieces. The pine banner rides in the chat column above the
  // thread while first-run is active; the serif greeting replaces the welcome
  // message only at step 1 (nothing configured yet) with an otherwise-empty
  // thread. Once a provider connects (step 2), the normal welcome returns so
  // Addison "introduces itself" per the step-2 copy.
  const threadEmpty = messages.length === 1 && messages[0]?.id === "welcome";
  const showGreeting = firstRunActive && !anyConfigured && threadEmpty;
  const threadMessages = showGreeting
    ? messages.filter((m) => m.id !== "welcome")
    : messages;
  const firstRunHeader = firstRunActive ? (
    <FirstRunBanner
      step={anyConfigured ? 2 : 1}
      onStartSetup={handleStartSetup}
      onSkip={() => setFirstRunDismissed(true)}
      showGreeting={showGreeting}
    />
  ) : undefined;

  return (
    <div className="flex h-full bg-paper text-ink">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
        conversations={conversations}
        currentConversationId={currentConversationId}
        onOpenConversation={handleOpenConversation}
        onNewChat={handleNewChat}
        newChatDisabled={!connected || controlsBusy}
        screen={screen}
        onOpenSettings={() => setScreen("settings")}
        profileLabel={profileLabel}
      />

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {!connected && (
          <Banner message="Addison's engine isn't connected. You can look around, but I can't chat just yet." />
        )}
        {statusBanner && (
          <Banner message={statusBanner} onDismiss={() => setStatusBanner(null)} />
        )}

        {screen === "settings" ? (
          <SettingsPage
            connected={connected}
            roles={roles}
            cloudModels={cloudModels}
            defaultRole={selectedRole}
            defaultCloudModel={selectedCloudModel}
            onChangeDefaultRole={handleChangeDefaultRole}
            onChangeDefaultCloudModel={handleChangeDefaultCloudModel}
            providers={providers}
            onConnectProvider={handleConnectProvider}
            onRemoveProvider={handleRemoveProvider}
            localSetup={localSetup}
            onStartLocalSetup={handleStartLocalSetup}
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
            {/* Chat header — active title left; undo (when undoable) + rail toggle
                right (design-brief-fern §2). */}
            <header className="flex items-baseline justify-between gap-4 border-b border-line px-[44px] py-3.5">
              <span className="min-w-0 truncate text-[13px] font-semibold tracking-[0.02em] text-ink-soft">
                {conversationTitle || "New conversation"}
              </span>
              <div className="flex shrink-0 items-baseline gap-[18px]">
                {hasUndoableActions && (
                  <button
                    type="button"
                    onClick={handleUndoLastAction}
                    className="text-[12.5px] font-medium text-muted hover:text-ink-soft"
                  >
                    <span aria-hidden="true">↺</span> Undo last action
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setRailOpen((v) => !v)}
                  className="text-[12.5px] font-medium text-fern-deep hover:text-fern"
                >
                  {railOpen ? "Hide widgets »" : "« Show widgets"}
                </button>
              </div>
            </header>

            {/* Body: centered chat column + (optional) widget rail, each with its
                own scroll. */}
            <div className="flex min-h-0 flex-1 justify-center gap-[38px] px-[44px]">
              <ChatThread
                messages={threadMessages}
                onRetry={handleRetry}
                retryAvailable={!isWorking && Boolean(lastUserText)}
                onRewindTo={handleRewindTo}
                showTechnicalDetails={Boolean(profile?.flags.rawDiagnostics)}
                header={firstRunHeader}
                footer={
                  <>
                    {proposalBlock}
                    {widgetProposalBlock}
                    {!railOpen && workBlock}
                    {!railOpen && consentBlock}
                  </>
                }
              />
              {railOpen && (
                <WidgetRail
                  work={workBlock}
                  consent={consentBlock}
                  widgets={widgets}
                  stats={stats}
                  routines={railRoutines}
                  onSetPinned={handleSetWidgetPinned}
                  onDelete={handleDeleteWidget}
                  onRunRoutine={handleRunWidgetRoutine}
                  onAskBuildWidget={handleAskBuildWidget}
                />
              )}
            </div>

            <Composer
              connected={connected}
              isWorking={isWorking}
              onSend={handleSend}
              onStop={handleStop}
              roles={roles}
              cloudModels={cloudModels}
              selectedRole={selectedRole}
              selectedCloudModel={selectedCloudModel}
              selectedLocalModel={selectedLocalModel}
              selectedEffort={selectedEffort}
              onSelectModel={handleSelectModel}
              onSelectEffort={handleSelectEffort}
              draftSeed={composerSeed}
              onDraftSeedUsed={() => setComposerSeed(null)}
              focusSignal={composerFocusSignal}
            />
          </>
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small pure helpers — defensive parsing of free-form JSON-RPC payloads, since
// the Python side's result/notification shapes aren't pinned in protocol.ts.
// ---------------------------------------------------------------------------
function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `m-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// Coerce a stored row's role string to the display union. Loaded history holds
// only user + assistant rows; anything unexpected is shown as an assistant line
// rather than dropped.
function normalizeRole(role: string): DisplayMessage["role"] {
  return role === "user" || role === "assistant" || role === "tool" ? role : "assistant";
}

function dropTrailingAssistant(list: DisplayMessage[]): DisplayMessage[] {
  const copy = [...list];
  while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
  return copy;
}

function loadDefaultRole(): ModelRole {
  try {
    const stored = localStorage.getItem(DEFAULT_ROLE_KEY);
    if (stored === "primary" || stored === "local") return stored;
  } catch {
    /* localStorage may be unavailable; fall through to the default */
  }
  return "primary";
}

function saveDefaultRole(role: ModelRole): void {
  try {
    localStorage.setItem(DEFAULT_ROLE_KEY, role);
  } catch {
    /* non-fatal */
  }
}

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

function loadStored(key: string): string | undefined {
  try {
    return localStorage.getItem(key) ?? undefined;
  } catch {
    return undefined;
  }
}

function saveStored(key: string, value: string | undefined): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* non-fatal */
  }
}

// The catalog's default model (exactly one has default: true), or the first as
// a defensive fallback if the core ever omits the flag.
function defaultCloudModel(models: CloudModel[]): CloudModel | undefined {
  return models.find((m) => m.default) ?? models[0];
}

// The effort level to use for a model: keep the current one if the model still
// offers it, otherwise the model's middle/default level. `undefined` for models
// with no levels (the effort control is hidden for them).
function pickEffort(model: CloudModel | undefined, current: string | undefined): string | undefined {
  const levels = model?.effortLevels ?? [];
  if (levels.length === 0) return undefined;
  if (current && levels.some((l) => l.id === current)) return current;
  return levels[Math.floor(levels.length / 2)].id;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
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

function roleLabel(role: string): string {
  if (role === "local") return "On this computer";
  if (role === "primary") return "Cloud";
  return role;
}

function normalizeModel(m: unknown): { id: string; label: string } | null {
  if (typeof m === "string") return { id: m, label: m };
  const obj = asRecord(m);
  if (!obj) return null;
  const id = obj.id ?? obj.name;
  if (typeof id !== "string") return null;
  return { id, label: typeof obj.label === "string" ? obj.label : id };
}

function normalizeRoles(result: unknown): RoleOption[] {
  const record = asRecord(result);
  const list = Array.isArray(result)
    ? result
    : record && Array.isArray(record.roles)
      ? (record.roles as unknown[])
      : [];

  const out: RoleOption[] = [];
  for (const item of list) {
    if (typeof item === "string") {
      if (item !== "primary" && item !== "local") continue;
      out.push({ role: item, label: roleLabel(item), configured: true });
      continue;
    }
    const obj = asRecord(item);
    if (!obj) continue;
    const role = (obj.role ?? obj.id) as unknown;
    if (role !== "primary" && role !== "local") continue; // setup_assistant isn't user-pickable
    // The core may carry local models under `models` or `localModels` — accept
    // either (the field name isn't pinned in protocol.ts).
    const rawModels = Array.isArray(obj.models)
      ? obj.models
      : Array.isArray(obj.localModels)
        ? (obj.localModels as unknown[])
        : undefined;
    const models = rawModels
      ? (rawModels.map(normalizeModel).filter(Boolean) as { id: string; label: string }[])
      : undefined;
    out.push({
      role,
      label: typeof obj.label === "string" ? obj.label : roleLabel(role),
      configured: obj.configured !== false,
      models,
    });
  }
  return out;
}

// The cloud catalog rides alongside `roles` on the `model.availableRoles`
// result. Parse it defensively — like the rest of the core payloads, its exact
// shape isn't pinned in protocol.ts. An entry with no `effortLevels` simply has
// none (the picker hides the effort control for it).
function normalizeCloudModels(result: unknown): CloudModel[] {
  const record = asRecord(result);
  const list =
    record && Array.isArray(record.cloudModels) ? (record.cloudModels as unknown[]) : [];

  const out: CloudModel[] = [];
  for (const item of list) {
    const obj = asRecord(item);
    if (!obj) continue;
    const id = obj.id ?? obj.name;
    if (typeof id !== "string") continue;
    const rawLevels = Array.isArray(obj.effortLevels) ? obj.effortLevels : [];
    const effortLevels = rawLevels.flatMap((l) => {
      const lo = asRecord(l);
      if (!lo || typeof lo.id !== "string") return [];
      return [{ id: lo.id, label: typeof lo.label === "string" ? lo.label : lo.id }];
    });
    out.push({
      id,
      label: typeof obj.label === "string" ? obj.label : id,
      description: typeof obj.description === "string" ? obj.description : "",
      effortLevels,
      default: obj.default === true,
      provider: typeof obj.provider === "string" ? obj.provider : undefined,
      providerLabel: typeof obj.providerLabel === "string" ? obj.providerLabel : undefined,
    });
  }
  return out;
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
  return {
    activeProfile: typeof obj.activeProfile === "string" ? obj.activeProfile : "simple",
    profiles,
    flags: {
      exposeRoutinePlan: flags.exposeRoutinePlan === true,
      rawDiagnostics: flags.rawDiagnostics === true,
      headlessCli: flags.headlessCli === true,
      byokFirstOnboarding: flags.byokFirstOnboarding === true,
    },
  };
}

function extractFinalText(result: unknown): string | null {
  const obj = asRecord(result);
  if (!obj) return typeof result === "string" ? result : null;
  if (typeof obj.text === "string") return obj.text;
  if (typeof obj.content === "string") return obj.content;
  const msg = asRecord(obj.message);
  if (msg && typeof msg.content === "string") return msg.content;
  return null;
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
    variables: Array.isArray(obj.variables)
      ? obj.variables.flatMap((v) => {
          const rv = asRecord(v);
          if (!rv || typeof rv.name !== "string") return [];
          return [
            {
              name: rv.name,
              prompt: typeof rv.prompt === "string" ? rv.prompt : `Value for ${rv.name}?`,
              default: typeof rv.default === "string" ? rv.default : null,
            },
          ];
        })
      : [],
  };
}

// Light copy of the routine library for the rail: just enough to prompt for a
// routine widget's variables on Run. Mirrors RoutineLibrary's normalizer.
function normalizeRailRoutines(result: unknown): RailRoutine[] {
  const record = asRecord(result);
  const list = record && Array.isArray(record.routines) ? record.routines : [];
  const out: RailRoutine[] = [];
  for (const item of list) {
    const r = asRecord(item);
    if (!r || typeof r.id !== "string" || typeof r.name !== "string") continue;
    out.push({
      id: r.id,
      name: r.name,
      variables: Array.isArray(r.variables)
        ? r.variables.flatMap((v) => {
            const rv = asRecord(v);
            if (!rv || typeof rv.name !== "string") return [];
            return [
              {
                name: rv.name,
                prompt: typeof rv.prompt === "string" ? rv.prompt : `Value for ${rv.name}?`,
                default: typeof rv.default === "string" ? rv.default : null,
              },
            ];
          })
        : [],
    });
  }
  return out;
}

function extractDetail(result: unknown): string | null {
  const obj = asRecord(result);
  if (!obj) return typeof result === "string" ? result : null;
  const detail = obj.detail ?? obj.message ?? obj.text;
  return typeof detail === "string" ? detail : null;
}

