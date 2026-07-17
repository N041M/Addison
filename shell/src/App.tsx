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

import { useEffect, useMemo, useState } from "react";
import { Method, type ModelRole, type PermissionRequest, type ActivityUpdate } from "./types/protocol";
import type { DisplayMessage, LocalSetupState, RoleOption } from "./types/ui";
import {
  ipc,
  isEngineConnected,
  storeProviderKey,
  subscribe,
  subscribeStatus,
  type StreamChunkParams,
  type LocalSetupProgressParams,
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

  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [selectedRole, setSelectedRole] = useState<ModelRole>(loadDefaultRole());
  const [selectedLocalModel, setSelectedLocalModel] = useState<string | undefined>(undefined);
  const [localSetup, setLocalSetup] = useState<LocalSetupState | null>(null);

  const [statusBanner, setStatusBanner] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [lastUserText, setLastUserText] = useState<string | null>(null);
  const [routineProposal, setRoutineProposal] = useState<RoutineProposal | null>(null);

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
        // plainly if there's actually nothing to put back.
        setHasUndoableActions(true);
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

    refreshRoles();

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

  function refreshRoles() {
    if (!isEngineConnected()) return;
    ipc
      .availableRoles()
      .then((res) => setRoles(normalizeRoles(res)))
      .catch(() => {
        /* leave the selector hidden if we can't read roles */
      });
  }

  // --- Turn lifecycle -------------------------------------------------------
  async function runTurn(text: string, opts: { isRetry?: boolean } = {}) {
    const assistantId = uid();
    setMessages((prev) => {
      const base = opts.isRetry
        ? dropTrailingAssistant(prev)
        : [...prev, { id: uid(), role: "user", content: text } as DisplayMessage];
      return [...base, { id: assistantId, role: "assistant", content: "", pending: true }];
    });

    setLastUserText(text);
    setActivities([]);
    setCurrentActivity(null);
    setPermission(null);
    setIsWorking(true);

    try {
      // Deliver the *effective* local model: if the user picked "On this
      // computer" but never touched the model dropdown, the picker shows the
      // first model as selected, so that's the one we must send (§4.1.1 B).
      const modelId = effectiveLocalModel(selectedRole, selectedLocalModel);
      const res = await ipc.sendMessage(text, selectedRole, modelId);
      const finalText = extractFinalText(res);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, pending: false, content: finalText ?? m.content }
            : m,
        ),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong.";
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                pending: false,
                failed: true,
                content:
                  m.content ||
                  `I couldn't finish that. ${message} You can try again in a moment.`,
              }
            : m,
        ),
      );
    } finally {
      setIsWorking(false);
      setCurrentActivity(null);
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

  function handleRewindTo(messageId: string) {
    // Reset the conversation to that point right away, then let the core
    // confirm / return the authoritative history.
    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === messageId);
      return idx === -1 ? prev : prev.slice(0, idx + 1);
    });
    setPermission(null);
    ipc
      .rewindConversation(messageId)
      .then((res) => {
        const msgs = extractMessages(res);
        if (msgs) setMessages(msgs);
      })
      .catch(() => {
        /* keep the local reset if the core doesn't return history */
      });
  }

  function handleUndoLastAction() {
    ipc
      .undoLastAction()
      .then((res) => {
        setLastUndoDetail(extractDetail(res) ?? "Put things back the way they were.");
      })
      .catch((err) => {
        setLastUndoDetail(err instanceof Error ? err.message : "Couldn't undo that.");
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

  function handleSelectRole(role: ModelRole) {
    setSelectedRole(role);
    if (role !== "local") {
      setSelectedLocalModel(undefined);
      ipc.setRoleForNextMessage(role, undefined).catch(() => {});
      return;
    }
    // Pin the effective model on the state and the core hint so the per-message
    // picker path is complete even before the dropdown is touched.
    const modelId = effectiveLocalModel("local", selectedLocalModel);
    setSelectedLocalModel(modelId);
    ipc.setRoleForNextMessage("local", modelId).catch(() => {});
  }

  function handleSelectLocalModel(modelId: string) {
    setSelectedLocalModel(modelId);
    ipc.setRoleForNextMessage("local", modelId).catch(() => {});
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
          selectedRole={selectedRole}
          selectedLocalModel={selectedLocalModel}
          onSelectRole={handleSelectRole}
          onSelectLocalModel={handleSelectLocalModel}
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
        defaultRole={selectedRole}
        onChangeDefaultRole={handleChangeDefaultRole}
        onSaveKey={handleSaveKey}
        localSetup={localSetup}
        onStartLocalSetup={handleStartLocalSetup}
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

function extractMessages(result: unknown): DisplayMessage[] | null {
  const obj = asRecord(result);
  const raw = Array.isArray(result) ? result : obj && Array.isArray(obj.messages) ? obj.messages : null;
  if (!raw) return null;
  const out: DisplayMessage[] = [];
  for (const item of raw) {
    const m = asRecord(item);
    if (!m) continue;
    const role = m.role;
    if (role !== "user" && role !== "assistant" && role !== "tool") continue;
    out.push({
      id: typeof m.id === "string" ? m.id : uid(),
      role,
      content: typeof m.content === "string" ? m.content : "",
    });
  }
  return out;
}
