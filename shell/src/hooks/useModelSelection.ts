// Model selection — the role/cloud-model/effort picker state, the provider
// connections (multi-provider API keys), and the local-model setup flow.
// Extracted from App.tsx as a mechanical move: the state, its reconciliation
// effects, and its handlers are unchanged.

import { useEffect, useState } from "react";
import type { ModelRole } from "../types/protocol";
import type { CloudModel, LocalSetupState, RoleOption } from "../types/ui";
import {
  ipc,
  isEngineConnected,
  storeProviderKey,
  deleteProviderKey,
  type ProviderInfo,
} from "../ipc/client";
import { asRecord } from "../lib/parse";

const DEFAULT_ROLE_KEY = "addison.defaultRole";
const CLOUD_MODEL_KEY = "addison.cloudModel";
const EFFORT_KEY = "addison.effort";

export function useModelSelection() {
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

  return {
    roles,
    rolesLoaded,
    cloudModels,
    providers,
    selectedRole,
    selectedCloudModel,
    selectedLocalModel,
    selectedEffort,
    localSetup,
    setLocalSetup,
    refreshRoles,
    refreshProviders,
    effectiveLocalModel,
    effectiveCloudModel,
    handleSelectModel,
    handleSelectEffort,
    handleChangeDefaultCloudModel,
    handleChangeDefaultRole,
    handleStartLocalSetup,
    handleConnectProvider,
    handleRemoveProvider,
  };
}

export type ModelSelection = ReturnType<typeof useModelSelection>;

// ---------------------------------------------------------------------------
// Small pure helpers (moved with the selection state from App.tsx).
// ---------------------------------------------------------------------------
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
      effortLevels,
      default: obj.default === true,
      provider: typeof obj.provider === "string" ? obj.provider : undefined,
      providerLabel: typeof obj.providerLabel === "string" ? obj.providerLabel : undefined,
    });
  }
  return out;
}
