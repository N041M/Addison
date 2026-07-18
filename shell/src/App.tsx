// Addison — top-level app shell (design-doc §7.1).
//
// Single window, three regions: the message thread, the collapsible activity
// strip, and the settings drawer (never required on first run). This component
// owns the conversation/turn state and wires the Core → Frontend notifications
// (streamed text, permission prompts, tool activity, local-setup progress) into
// React state, and Frontend → Core actions back out through the typed `ipc`.
//
// Visual direction is binding (CLAUDE.md, design-doc §7.1): a calm cool-slate
// everyday-utility look with sharp corners, one deep steel-blue accent for
// primary actions only, no decorative taglines, real typographic hierarchy for
// readers who are 54 and 68 — never a generic AI-chat template, never a model
// vendor's branding.

import { useEffect, useMemo, useRef, useState } from "react";
import { Method, type ModelRole, type PermissionRequest, type ActivityUpdate } from "./types/protocol";
import type {
  CloudModel,
  DisplayMessage,
  LocalSetupState,
  ProfileState,
  RoleOption,
} from "./types/ui";
import {
  ipc,
  isEngineConnected,
  storeProviderKey,
  subscribe,
  subscribeStatus,
  subscribeCoreState,
  subscribeDiagnostics,
  type StreamChunkParams,
  type LocalSetupProgressParams,
  type DiagnosticEntry,
  type RawError,
} from "./ipc/client";
import { ChatThread } from "./components/ChatThread";
import { ActivityPanel } from "./components/ActivityPanel";
import {
  RoutineProposalCard,
  type RoutineProposal,
} from "./components/RoutineProposalCard";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { Banner } from "./components/Banner";

const DEFAULT_ROLE_KEY = "addison.defaultRole";
const CLOUD_MODEL_KEY = "addison.cloudModel";
const EFFORT_KEY = "addison.effort";

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
  const [cloudModels, setCloudModels] = useState<CloudModel[]>([]);
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [lastUserText, setLastUserText] = useState<string | null>(null);
  const [routineProposal, setRoutineProposal] = useState<RoutineProposal | null>(null);

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
          refreshProfile();
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
    refreshProfile();

    return () => unsubs.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Transient shell notices fade out on their own so they don't linger.
  useEffect(() => {
    if (!statusBanner) return;
    const t = setTimeout(() => setStatusBanner(null), 8000);
    return () => clearTimeout(t);
  }, [statusBanner]);

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
      })
      .catch(() => {
        /* leave the selector on placeholders if we can't read roles */
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

  async function handleSaveKey(role: string, provider: string, key: string) {
    await storeProviderKey(role, provider, key);
    refreshRoles();
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

  // --- Render ---------------------------------------------------------------
  return (
    <div className="flex h-full flex-col bg-paper text-ink">
      <header className="flex items-center justify-between border-b border-line bg-surface px-6 py-3">
        <span className="text-xl font-semibold tracking-tight text-ink">Addison</span>
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="border border-line bg-paper px-3.5 py-1.5 text-sm font-medium text-ink-soft hover:border-muted"
        >
          Settings
        </button>
      </header>

      {!connected && (
        <Banner message="Addison's engine isn't connected. You can look around, but I can't chat just yet." />
      )}
      {statusBanner && (
        <Banner message={statusBanner} onDismiss={() => setStatusBanner(null)} />
      )}

      <main className="flex min-h-0 flex-1 flex-col">
        <ChatThread
          messages={messages}
          isWorking={isWorking}
          connected={connected}
          permission={permission}
          onRespondPermission={handleRespondPermission}
          onSend={handleSend}
          onStop={handleStop}
          onRetry={handleRetry}
          retryAvailable={!isWorking && Boolean(lastUserText)}
          onRewindTo={handleRewindTo}
          roles={roles}
          cloudModels={cloudModels}
          selectedRole={selectedRole}
          selectedCloudModel={selectedCloudModel}
          selectedLocalModel={selectedLocalModel}
          selectedEffort={selectedEffort}
          onSelectModel={handleSelectModel}
          onSelectEffort={handleSelectEffort}
          showTechnicalDetails={Boolean(profile?.flags.rawDiagnostics)}
          draftSeed={composerSeed}
          onDraftSeedUsed={() => setComposerSeed(null)}
          activityStrip={
            <>
              {routineProposal && (
                <RoutineProposalCard
                  proposal={routineProposal}
                  onSave={handleConfirmRoutine}
                  onCancel={() => setRoutineProposal(null)}
                />
              )}
              <ActivityPanel
                isWorking={isWorking}
                current={currentActivity}
                activities={activities}
                hasUndoableActions={hasUndoableActions}
                onUndoLastAction={handleUndoLastAction}
                canRedo={canRedo}
                onRedoLastAction={handleRedoLastAction}
                lastUndoDetail={lastUndoDetail}
                onProposeRoutine={connected ? handleProposeRoutine : undefined}
              />
            </>
          }
        />
      </main>

      <SettingsDrawer
        open={settingsOpen}
        connected={connected}
        roles={roles}
        cloudModels={cloudModels}
        defaultRole={selectedRole}
        defaultCloudModel={selectedCloudModel}
        onChangeDefaultRole={handleChangeDefaultRole}
        onChangeDefaultCloudModel={handleChangeDefaultCloudModel}
        onSaveKey={handleSaveKey}
        localSetup={localSetup}
        onStartLocalSetup={handleStartLocalSetup}
        profile={profile}
        onSetProfile={handleSetProfile}
        diagnostics={diagnostics}
        onClearDiagnostics={clearDiagnostics}
        onClose={() => setSettingsOpen(false)}
      />
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

function extractDetail(result: unknown): string | null {
  const obj = asRecord(result);
  if (!obj) return typeof result === "string" ? result : null;
  const detail = obj.detail ?? obj.message ?? obj.text;
  return typeof detail === "string" ? detail : null;
}

