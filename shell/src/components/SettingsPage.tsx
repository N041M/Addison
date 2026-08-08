// Settings — the in-window settings SURFACE (dark direction; docs/design-brief-dark,
// "Settings sections, in order"). It replaces the chat column; the global 56px
// header (← back, and ☰ on a narrow window) is the only chrome around it, so this
// file brings no header, no scroller and no columns of its own — just the
// <Surface> reading column and its sections.
//
// This is a RESKIN, not a rewrite: every existing IPC wiring (key save,
// role/default-model change, routing, routines, skills, local-model setup + its
// progress subscription, guards, workspace trust, restore points, profile switch,
// theme) is preserved exactly. What changed is that a card became a labelled
// section and a control became a row:
//
//     name (12px ink-soft) — spacer — mono value (10.5px muted) — accent action
//
// Sections, in the brief's order: Where Addison thinks · Which model answers ·
// API keys · Run a model on this computer · Routines · Skills · Profile · How
// careful Addison is (Custom only) · Folders Addison may work in
// (Developer/Custom only) · Tool servers (Developer/Custom only) · Automations
// (EVERY profile — Simple lists them disabled, saying why) · Restore points ·
// Diagnostics.
//
// TWO THINGS THAT ARE NOT STYLING, and must survive any future edit here:
//   * G1 — a key typed into a row goes to the OS keychain through the Rust
//     command and nowhere else. `provider.list` carries status only; nothing on
//     this page ever holds or displays a key.
//   * The Custom profile is never one click away. It lives behind the
//     "Advanced…" disclosure and a two-step confirm, with the core's own honest
//     description in between (Phase-2 step 2).

import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import type { Automation, AutomationStatus, ModelRole } from "../types/protocol";
import type { CloudModel, ProfileState, RoleOption } from "../types/ui";
import type { DiagnosticEntry, ProviderInfo } from "../ipc/client";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { RoutingCardState } from "../hooks/useRouting";
import type { WorkspaceCardState } from "../hooks/useWorkspace";
import type { McpServersCardState } from "../hooks/useMcpServers";
import type { AutomationsCardState } from "../hooks/useAutomations";
import type { ThemeChoice } from "../lib/theme";
import type { PopupAnchor } from "./ModelPopup";
import {
  RowAction,
  RowConfirm,
  Surface,
  SurfaceRow,
  SurfaceSection,
  SURFACE_ID,
  WaitingTag,
} from "./Surface";
import { RoutineLibrary } from "./RoutineLibrary";
import { SkillsSection } from "./SkillsSection";
import { RestorePointsSection } from "./SnapshotsCard";
import { CustomGuardPanel } from "./CustomGuardPanel";
import { WorkspaceTrustPanel } from "./WorkspaceTrustPanel";
import { McpServersPanel } from "./McpServersPanel";
import { RoutingCard, type RoutingCardModel } from "./RoutingCard";
import { LocalModelSetup } from "./LocalModelSetup";

interface Props {
  connected: boolean;
  /**
   * Pinned above the title: the engine/status banners and — the reason the slot
   * exists — a pending permission card. A question holding a turn open must be
   * visible wherever the person is standing.
   */
  pinned?: ReactNode;
  /**
   * The model-selection bundle (useModelSelection): roles + cloud catalog, the
   * default role/model picks, provider connections, and the local-setup flow.
   */
  models: ModelSelection;
  /** The skills bundle (useSkills): the list + create/edit/toggle/remove handlers. */
  skills: SkillsState;
  /** The restore-points bundle (useSnapshots) — the G3 floor's Settings face. */
  snapshots: SnapshotsState;
  /** The Custom-profile guard bundle (useGuards). Its section renders only while
   * the active profile is Custom (Phase-2 step 2). */
  guards: GuardsCardState;
  /** The routing bundle (useRouting; Phase-2 step 3). Optional so a partial
   * caller (older tests) still renders — the routing section is simply omitted
   * then. The card itself decides toggle vs. full from the core's `surface`. */
  routing?: RoutingCardState;
  /** The workspace-trust bundle (useWorkspace; Phase-2 step 5). Optional so a
   * partial caller (older tests) still renders — the section is simply omitted
   * then. It shows ONLY on the Developer/Custom surfaces (keyed off the active
   * profile, never the mode); Simple never sees it. */
  workspace?: WorkspaceCardState;
  /** The MCP tool-server bundle (useMcpServers; Phase-2 step 7 phase 1). Optional
   * so a partial caller (older tests) still renders — the section is simply
   * omitted then. It shows ONLY on the Developer/Custom surfaces (keyed off the
   * active profile, never the mode); Simple never sees it, and the core refuses
   * `mcp.add` outside Developer independently of this gate. */
  mcp?: McpServersCardState;
  /**
   * The automations bundle (useAutomations; Phase-2 step 8 phase 4). Optional so a
   * partial caller (older tests) still renders — the section is simply omitted
   * then. UNLIKE the two above it has NO profile gate: a saved automation is
   * configuration, so Simple lists it, disabled and saying why (the 2026-08-06
   * artifact decision). App owns the state because `automations` is a
   * snapshot-captured table and a restore must re-read it.
   */
  automations?: AutomationsCardState;
  profile: ProfileState | null;
  onSetProfile: (profileId: string) => void;
  diagnostics: DiagnosticEntry[];
  onClearDiagnostics: () => void;
  theme: ThemeChoice;
  onSetTheme: (theme: ThemeChoice) => void;
  /** Opens the anchored model popup at the click point (App owns the floating
   * chrome — a `position: fixed` panel inside this surface would be trapped by
   * the section's fadeRise transform). */
  /** The point the panel opens at, and the button it opens FROM — which is where
   * focus goes back to when the panel is done with (App wires the return). */
  onOpenModelPopup?: (anchor: PopupAnchor, trigger: HTMLElement) => void;
  /**
   * Writes one sentence into the composer and returns to chat — the person still
   * presses Send (App's `seedAsk`, the widgets surface's "use this idea" idiom).
   * The Automations section's Arm / Disarm actions are its other caller, and the
   * only route from this page to the arming ceremony: arming is a TOOL the gate
   * cards, never an RPC this webview can invoke. Optional, so a partial caller
   * (older tests) still renders — the actions are simply absent then.
   */
  onAskAddison?: (text: string) => void;
  /** Opens the Restore points modal ("All restore points" → "open"). */
  onOpenRestorePoints?: () => void;
  /**
   * A DOM id to scroll into view once, when the page opens (the first-run
   * "Start setup" button routes here focused on the API-keys section). Cleared by
   * `onScrolled` after it's honored so a later Settings visit lands at the top.
   */
  scrollTarget?: string | null;
  onScrolled?: () => void;
}

/** Stable ids so first-run's "Start setup" and the "not set up yet" row can
 * scroll to the right section inside the surface's own scroller. */
export const API_KEYS_SECTION_ID = "settings-api-keys";
const LOCAL_MODEL_SECTION_ID = "settings-local-model";

const SETTINGS_DESCRIPTION =
  "Everything lives on this computer. Nothing leaves it without asking you first.";

// The API-key provider rows (multi-provider, owner decision 2026-07-18). ``kind``
// picks the row's affordance: a provider key ("key"), or the custom
// OpenAI-compatible server row with a base-URL + optional key ("custom").
type ProviderKind = "key" | "custom";
const KEY_PROVIDERS: { id: string; label: string; kind: ProviderKind }[] = [
  { id: "anthropic", label: "Anthropic", kind: "key" },
  { id: "openai", label: "OpenAI", kind: "key" },
  { id: "google", label: "Google", kind: "key" },
  { id: "custom", label: "Your own server", kind: "custom" },
];

// Printable-ASCII, no whitespace — catches clipboard damage (smart quotes, a "…"
// from a truncated copy, a non-breaking space) at the door before it's stored.
const KEY_SHAPE = /^[\x21-\x7E]+$/;

// Google's free tier surfaced as INFORMATION, not a routing flag (contract D3):
// one plain sentence under the Google row saying where a free key comes from. No
// cloud model is ever marked "free" — the free chip fires only for
// free-by-construction local models — so this is information, not a flag.
//
// It is deliberately NOT an <a href>. THE WEBVIEW CANNOT OPEN A URL. The Rust
// shell registers exactly three commands for the webview (main.rs:
// send_to_core, store_provider_key, delete_provider_key); `shell.openExternal`
// is a CORE→shell method reached only by the `open_link` tool, and Markdown.tsx
// states the standing rule as "the webview must never open URLs itself, and must
// never call any shell.* IPC method". An anchor here would render, invite a
// click, and do nothing — a dead control in a Settings panel, which is worse than
// plain text. So the address is shown as SELECTABLE mono text the person can copy
// into their own browser, and the sentence says to open it there.
const GOOGLE_KEY_URL_TEXT = "aistudio.google.com/apikey";

/** The three appearance choices, in the order the row cycles through them. */
const THEME_CYCLE: { value: ThemeChoice; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "Match this computer" },
];

// The connected-models union for the custom chain builder: every cloud model
// (attributed to its provider when the payload names one) plus every configured
// local model. Same data the model picker consumes.
function routingModels(models: ModelSelection): RoutingCardModel[] {
  const cloud: RoutingCardModel[] = models.cloudModels.map((m) => ({
    id: m.id,
    label: m.providerLabel ? `${m.label} · ${m.providerLabel}` : m.label,
  }));
  const localRole = models.roles.find((r) => r.role === "local" && r.configured);
  const local: RoutingCardModel[] = (localRole?.models ?? []).map((m) => ({
    id: m.id,
    label: m.label,
  }));
  return [...cloud, ...local];
}

/** How far above a scrolled-to section to stop: the surface's fade mask eats the
 * first 32px of the column, so landing exactly on the label would land under it. */
const SCROLL_MARGIN = 36;

/**
 * Scroll one section to the top of the SURFACE's own scroller.
 *
 * Deliberately not `scrollIntoView` and deliberately not `behavior: "smooth"`:
 * the surface is a nested scroll container behind a mask, and a smooth request
 * that silently does nothing (as it does in some engines and under
 * reduced-motion) would leave first-run's "Start setup" landing on the top of
 * Settings with no explanation. An instant landing always works, and the
 * surface's own fadeRise is the motion this moment already has.
 */
function scrollToSection(id: string): void {
  const el = document.getElementById(id);
  const surface = document.getElementById(SURFACE_ID);
  if (!el || !surface) return;
  const delta = el.getBoundingClientRect().top - surface.getBoundingClientRect().top;
  surface.scrollTop = Math.max(0, surface.scrollTop + delta - SCROLL_MARGIN);
}

/** The resolved default cloud model: the stored pick when it is still in the
 * catalog, else the catalog's own default. Mirrors useModelSelection. */
export function resolveCloudModel(
  cloudModels: CloudModel[],
  picked?: string,
): CloudModel | undefined {
  if (picked) {
    const hit = cloudModels.find((m) => m.id === picked);
    if (hit) return hit;
  }
  return cloudModels.find((m) => m.default) ?? cloudModels[0];
}

export function SettingsPage({
  connected,
  pinned,
  models,
  skills,
  snapshots,
  guards,
  routing,
  workspace,
  mcp,
  automations,
  profile,
  onSetProfile,
  diagnostics,
  onClearDiagnostics,
  theme,
  onSetTheme,
  onOpenModelPopup,
  onAskAddison,
  onOpenRestorePoints,
  scrollTarget,
  onScrolled,
}: Props) {
  // Honor a one-shot scroll request (first-run "Start setup" → API keys). The
  // timeout lets the surface paint before we scroll, then we clear the request.
  //
  // HAZARD: `onScrolled` must be a STABLE reference. The cleanup cancels the
  // timer, so a callback re-created on every parent render would cancel and
  // reschedule forever and the scroll would never happen (App memoises it).
  useEffect(() => {
    if (!scrollTarget) return;
    const t = setTimeout(() => {
      scrollToSection(scrollTarget);
      onScrolled?.();
    }, 60);
    return () => clearTimeout(t);
  }, [scrollTarget, onScrolled]);

  // Developer/Custom only, keyed off the ACTIVE PROFILE and never the policy mode
  // — the same gate the workspace-trust section uses, for the same reason.
  const developerSurface =
    profile?.activeProfile === "developer" || profile?.activeProfile === "custom";
  const showWorkspace = Boolean(workspace) && developerSurface;
  const showMcp = Boolean(mcp) && developerSurface;

  return (
    <Surface title="Settings" description={SETTINGS_DESCRIPTION} pinned={pinned}>
      <SurfaceSection label="Where Addison thinks">
        <WhereAddisonThinks
          roles={models.roles}
          cloudModels={models.cloudModels}
          defaultRole={models.selectedRole}
          defaultCloudModel={models.selectedCloudModel}
          onChangeDefaultRole={models.handleChangeDefaultRole}
          onOpenModelPopup={onOpenModelPopup}
          onGoToApiKeys={() => scrollToSection(API_KEYS_SECTION_ID)}
          onGoToLocalModels={() => scrollToSection(LOCAL_MODEL_SECTION_ID)}
        />
      </SurfaceSection>

      {routing && (
        <SurfaceSection label="Which model answers">
          <RoutingCard connected={connected} routing={routing} models={routingModels(models)} />
        </SurfaceSection>
      )}

      <SurfaceSection id={API_KEYS_SECTION_ID} label="API keys">
        <ApiKeys
          connected={connected}
          providers={models.providers}
          onConnect={models.handleConnectProvider}
          onRemove={models.handleRemoveProvider}
        />
      </SurfaceSection>

      <SurfaceSection id={LOCAL_MODEL_SECTION_ID} label="Run a model on this computer">
        <LocalModelSetup
          connected={connected}
          roles={models.roles}
          setup={models.localSetup}
          onStartSetup={models.handleStartLocalSetup}
        />
      </SurfaceSection>

      <SurfaceSection label="Routines">
        <RoutineLibrary
          exposeRoutinePlan={profile?.flags.exposeRoutinePlan}
          developer={profile?.mode === "open"}
          refreshKey={profile?.activeProfile}
        />
      </SurfaceSection>

      <SurfaceSection label="Skills">
        <SkillsSection connected={connected} skills={skills} />
      </SurfaceSection>

      <SurfaceSection label="Profile">
        <ProfileCard
          connected={connected}
          profile={profile}
          onSetProfile={onSetProfile}
          theme={theme}
          onSetTheme={onSetTheme}
        />
      </SurfaceSection>

      {/* The Custom-profile guard panel — shown ONLY while Custom is the active
          profile (never merely because the mode is OPEN). It sits between Profile
          and the rest so the person who just chose Custom sees both the guards
          they can loosen and, further down, the way back (G3/G4). */}
      {profile?.activeProfile === "custom" && (
        <SurfaceSection label="How careful Addison is">
          <CustomGuardPanel connected={connected} guards={guards} />
        </SurfaceSection>
      )}

      {/* Workspace trust — the coding-harness boundary (Phase-2 step 5). Shown
          ONLY on the Developer and Custom surfaces (keyed off the active profile,
          never the policy mode); Simple never sees it. */}
      {showWorkspace && workspace && (
        <SurfaceSection label="Folders Addison may work in">
          <WorkspaceTrustPanel connected={connected} workspace={workspace} />
        </SurfaceSection>
      )}

      {/* Tool servers — the MCP client's configuration (Phase-2 step 7, phase 1).
          Same Developer/Custom gate as workspace trust, and it sits beside it
          because both answer "what may Addison reach". Nothing here connects to
          anything yet; the panel says so in its own first line. */}
      {showMcp && mcp && (
        <SurfaceSection label="Tool servers">
          <McpServersPanel connected={connected} mcp={mcp} />
        </SurfaceSection>
      )}

      {/* Automations — what Addison has written down for THIS COMPUTER to run on a
          schedule (Phase-2 step 8, phase 4 of four). UNLIKE the two sections above
          it renders in EVERY profile, and that is the phase-4 correction: a saved
          automation is configuration, not an ability, so Simple LISTS it — visibly
          inert, saying why, in the core's own sentence — instead of hiding it. A
          profile switch that empties a page of somebody's own saved work reads as
          Addison having deleted it, which is the failure the 2026-08-06 artifact
          decision reversed (docs/SAFETY.md owns that rule). What Simple does not
          get is any way to USE one: the arming controls and the command text are
          keyed off the Developer surface below, and the tools that author and arm
          are refused at dispatch outside OPEN whatever this page draws. */}
      {automations && (
        <SurfaceSection label="Automations">
          <AutomationsSection
            connected={connected}
            automations={automations}
            developerSurface={developerSurface}
            onAsk={onAskAddison}
          />
        </SurfaceSection>
      )}

      {/* Restore points sit after the "how freely Addison may act" controls on
          purpose: the person who has just changed one should see the way back in
          the same breath (G3). */}
      <SurfaceSection label="Restore points">
        <RestorePointsSection
          connected={connected}
          snapshots={snapshots}
          onOpenAll={() => onOpenRestorePoints?.()}
        />
      </SurfaceSection>

      <SurfaceSection label="Diagnostics">
        <Diagnostics
          raw={Boolean(profile?.flags.rawDiagnostics)}
          diagnostics={diagnostics}
          onClear={onClearDiagnostics}
        />
      </SurfaceSection>
    </Surface>
  );
}

// --- Where Addison thinks --------------------------------------------------
function WhereAddisonThinks({
  roles,
  cloudModels,
  defaultRole,
  defaultCloudModel,
  onChangeDefaultRole,
  onOpenModelPopup,
  onGoToApiKeys,
  onGoToLocalModels,
}: {
  roles: RoleOption[];
  cloudModels: CloudModel[];
  defaultRole: ModelRole;
  defaultCloudModel?: string;
  onChangeDefaultRole: (role: ModelRole) => void;
  /** The point the panel opens at, and the button it opens FROM — which is where
   * focus goes back to when the panel is done with (App wires the return). */
  onOpenModelPopup?: (anchor: PopupAnchor, trigger: HTMLElement) => void;
  onGoToApiKeys: () => void;
  onGoToLocalModels: () => void;
}) {
  const cloudConfigured = roles.some((r) => r.role === "primary" && r.configured);
  const localRole = roles.find((r) => r.role === "local" && r.configured);
  const localModels = localRole?.models ?? [];
  const cloudName = resolveCloudModel(cloudModels, defaultCloudModel)?.label;
  const canChangeModel = cloudModels.length + localModels.length > 0 && Boolean(onOpenModelPopup);

  return (
    <>
      {/* "Not connected" outranks "default": a row that says `default ✓` while no
          provider is set up would be telling someone their assistant is ready. */}
      <SurfaceRow
        name={cloudName ? `Cloud — ${cloudName}` : "Cloud"}
        value={
          !cloudConfigured ? "not connected yet" : defaultRole === "primary" ? "default ✓" : undefined
        }
        action={
          !cloudConfigured ? "add a key" : defaultRole === "primary" ? undefined : "use by default"
        }
        actionAriaLabel={cloudConfigured ? "Use the cloud model by default" : "Add a provider key"}
        onAction={
          !cloudConfigured
            ? onGoToApiKeys
            : defaultRole === "primary"
              ? undefined
              : () => onChangeDefaultRole("primary")
        }
      />
      <SurfaceRow
        name={
          localRole
            ? `On this computer${localModels[0] ? ` — ${localModels[0].label}` : ""}`
            : "On this computer"
        }
        value={
          !localRole ? "not set up yet" : defaultRole === "local" ? "default ✓" : undefined
        }
        action={!localRole ? "set up" : defaultRole === "local" ? undefined : "use by default"}
        actionAriaLabel={
          localRole ? "Use the model on this computer by default" : "Set up a model on this computer"
        }
        onAction={
          !localRole
            ? onGoToLocalModels
            : defaultRole === "local"
              ? undefined
              : () => onChangeDefaultRole("local")
        }
      />
      <SurfaceRow
        name="Cloud model"
        value={cloudName ?? "none yet"}
        action={canChangeModel ? "change" : undefined}
        actionAriaLabel="Change the default cloud model"
        onAction={
          canChangeModel && onOpenModelPopup
            ? (event: MouseEvent<HTMLButtonElement>) => {
                const trigger = event.currentTarget;
                const rect = trigger.getBoundingClientRect();
                onOpenModelPopup({ x: rect.right, y: rect.top + rect.height / 2 }, trigger);
              }
            : undefined
        }
      />
    </>
  );
}

// --- API keys --------------------------------------------------------------
// Exported for the step-4 Google free-tier-line test (step4.test.tsx). It is
// still only rendered from within this page.
export function ApiKeys({
  connected,
  providers,
  onConnect,
  onRemove,
}: {
  connected: boolean;
  providers: ProviderInfo[];
  onConnect: (provider: string, key: string, baseUrl?: string) => Promise<void>;
  onRemove: (provider: string) => Promise<void>;
}) {
  const byId = new Map(providers.map((p) => [p.id, p]));
  return (
    <>
      <SurfaceRow wrap name="Keys go straight to your computer's keychain and are never shown again — not even here." />
      {KEY_PROVIDERS.map((p) => (
        <ProviderRow
          key={p.id}
          def={p}
          info={byId.get(p.id)}
          connected={connected}
          onConnect={onConnect}
          onRemove={onRemove}
        />
      ))}
    </>
  );
}

function ProviderRow({
  def,
  info,
  connected,
  onConnect,
  onRemove,
}: {
  def: { id: string; label: string; kind: ProviderKind };
  info: ProviderInfo | undefined;
  connected: boolean;
  onConnect: (provider: string, key: string, baseUrl?: string) => Promise<void>;
  onRemove: (provider: string) => Promise<void>;
}) {
  const isConnected = info?.connected === true;
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(info?.baseUrl ?? "");
  const [status, setStatus] = useState<"idle" | "working" | "error">("idle");
  const [error, setError] = useState("");
  // "Replace" on a connected row, or the expand on a disconnected one.
  const [editing, setEditing] = useState(false);
  // A connect attempt stores the key BEFORE validating; if the validate fails the
  // key is still saved, so the row keeps offering Remove to clear it.
  const [removable, setRemovable] = useState(false);

  const kind = def.kind;
  const needsKey = kind !== "custom"; // custom key is optional

  async function connect() {
    const trimmedKey = key.trim();
    const trimmedUrl = baseUrl.trim();
    if (needsKey && !trimmedKey) return;
    if (trimmedKey && !KEY_SHAPE.test(trimmedKey)) {
      setStatus("error");
      setError("That doesn't look like a complete API key — copy the whole key and paste it again.");
      return;
    }
    if (kind === "custom" && !/^https?:\/\/.+/.test(trimmedUrl)) {
      setStatus("error");
      setError("Enter a web address that starts with http:// or https://.");
      return;
    }
    setStatus("working");
    setError("");
    if (trimmedKey) setRemovable(true); // the key is about to be stored
    try {
      await onConnect(def.id, trimmedKey, kind === "custom" ? trimmedUrl : undefined);
      setKey("");
      setEditing(false);
      setRemovable(false);
      setStatus("idle");
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Couldn't connect. Check the key and try again.");
    }
  }

  async function remove() {
    setStatus("working");
    setError("");
    try {
      await onRemove(def.id);
      setKey("");
      setEditing(false);
      setRemovable(false);
      setStatus("idle");
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Couldn't remove the key.");
    }
  }

  const working = status === "working";
  const value = isConnected
    ? kind === "custom"
      ? "connected ✓"
      : "key saved ✓"
    : kind === "custom"
      ? `OpenAI-compatible · ${info?.baseUrl || "http://…"}`
      : "not connected";

  return (
    <SurfaceRow
      name={def.label}
      value={value}
      actions={
        isConnected ? (
          <>
            <RowAction
              tone="muted"
              ariaLabel={`Replace the ${def.label} key`}
              disabled={working}
              onClick={() => setEditing((v) => !v)}
            >
              {editing ? "cancel" : "replace"}
            </RowAction>
            <RowAction
              tone="danger"
              ariaLabel={
                kind === "custom" ? `Disconnect ${def.label}` : `Remove the ${def.label} key`
              }
              disabled={working}
              onClick={() => void remove()}
            >
              {kind === "custom" ? "disconnect" : "remove"}
            </RowAction>
          </>
        ) : (
          // Expanding is allowed even while the engine is down: the field and its
          // Save stay disabled, and the row says why. A section that cannot even
          // be opened for a look is not "a quiet placeholder", it is a dead page.
          <RowAction
            ariaLabel={kind === "custom" ? `Connect ${def.label}` : `Add the ${def.label} key`}
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "cancel" : kind === "custom" ? "connect" : "add key"}
          </RowAction>
        )
      }
    >
      {editing && (
        <div className="mt-2.5 flex flex-col gap-2">
          {kind === "custom" && (
            <input
              type="text"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={baseUrl}
              onChange={(e) => {
                setBaseUrl(e.target.value);
                if (status !== "idle") setStatus("idle");
              }}
              placeholder="http://localhost:1234/v1"
              aria-label={`${def.label} address`}
              disabled={!connected || working}
              className="w-full border-b border-line bg-transparent py-1.5 font-mono text-[11px] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
            />
          )}
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              if (status !== "idle") setStatus("idle");
            }}
            placeholder={kind === "custom" ? "Key (optional)…" : `Paste your ${def.label} key…`}
            aria-label={`${def.label} key`}
            disabled={!connected || working}
            className="w-full border-b border-line bg-transparent py-1.5 font-mono text-[11px] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
          />
          <div className="flex items-baseline gap-5">
            <RowAction
              disabled={
                !connected ||
                working ||
                (needsKey && !key.trim()) ||
                (kind === "custom" && !baseUrl.trim())
              }
              onClick={() => void connect()}
            >
              {working ? "Checking…" : kind === "custom" ? "Connect" : "Save"}
            </RowAction>
            {/* A failed connect still stored the key — offer to clear it. */}
            {removable && !isConnected && (
              <RowAction tone="danger" disabled={working} onClick={() => void remove()}>
                Remove the saved key
              </RowAction>
            )}
          </div>
          {status === "error" && (
            <p className="m-0 text-[12px] leading-[1.55] text-ink-soft">{error}</p>
          )}
          {status !== "error" && (isConnected || removable) && (
            <p className="m-0 text-[12px] leading-[1.55] text-muted">
              Checked with one tiny request, then locked away in the keychain.
            </p>
          )}
          {!connected && (
            <p className="m-0 text-[12px] leading-[1.55] text-muted">
              You can add a key once Addison&rsquo;s engine is connected.
            </p>
          )}
        </div>
      )}

      {/* Google free-tier info line (contract D3/D5). The address is selectable
          text, not a link — see GOOGLE_KEY_URL_TEXT above for why the webview
          cannot open one, and why a dead anchor would be the dishonest option. */}
      {def.id === "google" && (
        <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-muted">
          Google's API has a free tier — a free key works here. Open{" "}
          <span className="select-all font-mono text-[11px] text-ink">{GOOGLE_KEY_URL_TEXT}</span>{" "}
          in your browser to get one.
        </p>
      )}
    </SurfaceRow>
  );
}

// --- Profile (+ Appearance) ------------------------------------------------
// Exported for the step-2 disclosure/confirm tests (guards.test.tsx). It is still
// only rendered from within this page.
export function ProfileCard({
  connected,
  profile,
  onSetProfile,
  theme,
  onSetTheme,
}: {
  connected: boolean;
  profile: ProfileState | null;
  onSetProfile: (profileId: string) => void;
  theme: ThemeChoice;
  onSetTheme: (theme: ThemeChoice) => void;
}) {
  // A profile in Simple→Developer confirmation, held until the user confirms or
  // cancels. Switching BACK reduces what Addison can do, so it needs no
  // confirmation and never sets this.
  const [confirming, setConfirming] = useState<string | null>(null);
  // The "Advanced…" disclosure that reveals the Custom profile, and the two-step
  // confirm before Custom is actually turned on. Custom is deeper and more
  // permissive than Developer, so it never sits in the plain control and never
  // switches on with a single click.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [customStep, setCustomStep] = useState<0 | 1 | 2>(0);
  const mode = profile?.mode ?? (profile?.activeProfile === "developer" ? "open" : "safe");

  // Advanced profiles (only Custom sets `advanced`) never render as ordinary
  // options — they live behind the disclosure.
  const advancedProfiles = (profile?.profiles ?? []).filter((p) => p.advanced);
  const basicProfiles = (profile?.profiles ?? []).filter((p) => !p.advanced);
  const customProfile = advancedProfiles[0];
  const isCustomActive = Boolean(customProfile && profile?.activeProfile === customProfile.id);
  // Auto-reveal the disclosure when Custom is already in use, so the active
  // profile is never hidden from the person who is standing in it.
  const showAdvanced = advancedOpen || isCustomActive;

  const themeIndex = Math.max(
    0,
    THEME_CYCLE.findIndex((t) => t.value === theme),
  );

  const appearanceRow = (
    <SurfaceRow
      name="Appearance"
      value={THEME_CYCLE[themeIndex].label}
      action="change"
      actionAriaLabel="Change how Addison looks"
      onAction={() => onSetTheme(THEME_CYCLE[(themeIndex + 1) % THEME_CYCLE.length].value)}
    />
  );

  if (!connected || !profile || profile.profiles.length === 0) {
    return (
      <>
        <SurfaceRow
          name={
            connected
              ? "Profile options will appear here in a moment."
              : "Your profile choices appear here once Addison's engine is connected."
          }
        />
        {appearanceRow}
      </>
    );
  }

  // The one switch the row offers. From Simple that is Developer (a step UP in
  // what Addison may do, so it asks first); from anywhere else it is Simple (a
  // step down, which needs no ceremony). Custom is reached through the
  // disclosure below, never from this row.
  const activeLabel =
    profile.profiles.find((p) => p.id === profile.activeProfile)?.label ?? "Simple";
  const isSimple = profile.activeProfile === "simple";
  const switchTarget = isSimple
    ? (basicProfiles.find((p) => p.id !== "simple") ?? null)
    : (basicProfiles.find((p) => p.id === "simple") ?? null);

  function handleSwitch(id: string) {
    // In SAFE mode the switch is a step UP in what Addison may do, so ask first.
    // Otherwise it is a step down and goes straight through.
    if (mode === "safe") setConfirming(id);
    else onSetProfile(id);
  }

  return (
    <>
      <SurfaceRow
        name="Profile"
        value={`${activeLabel} · local`}
        action={switchTarget && !confirming ? `switch to ${switchTarget.label}` : undefined}
        onAction={switchTarget ? () => handleSwitch(switchTarget.id) : undefined}
      >
        {/* Honest, mode-scoped description — the profile changes what Addison is
            ALLOWED to do, not just what it shows (owner decision 2026-07-19). */}
        <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-muted">
          {mode === "open"
            ? "Addison can run commands and scripts on this computer, acts without asking except for destructive actions, and some actions can't be undone."
            : "Addison asks before anything it does, and everything can be undone."}
        </p>
        {/* Simple→Developer confirmation — inline, never a browser confirm(). */}
        {confirming && (
          <RowConfirm
            confirmLabel="Switch"
            onConfirm={() => {
              onSetProfile(confirming);
              setConfirming(null);
            }}
            onCancel={() => setConfirming(null)}
          >
            Developer profile lets Addison act more freely on this computer. You can switch back
            anytime.
          </RowConfirm>
        )}
      </SurfaceRow>

      {/* Advanced… — the disclosure that reveals the Custom profile. Quiet and
          text-level, matching the surface's idiom for opt-in depth. Until it is
          opened, Custom is not in the DOM at all. */}
      {customProfile && !showAdvanced && (
        <SurfaceRow name="More ways to tune this" action="Advanced…" onAction={() => setAdvancedOpen(true)} />
      )}
      {customProfile && showAdvanced && (
        <SurfaceRow
          name={customProfile.label}
          value={isCustomActive ? "in use ✓" : undefined}
          action={isCustomActive || customStep >= 1 ? undefined : "turn on"}
          actionAriaLabel={`Turn on the ${customProfile.label} profile`}
          onAction={isCustomActive ? undefined : () => setCustomStep(1)}
        >
          {/* The core-authored honest description (contract D8). */}
          <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-muted">
            {customProfile.description}
          </p>
          {/* Two-step inline confirm before Custom is turned on — never a browser
              confirm(), and profile.set fires only at the end. */}
          {customStep === 1 && !isCustomActive && (
            <RowConfirm
              confirmLabel="Continue"
              onConfirm={() => setCustomStep(2)}
              onCancel={() => setCustomStep(0)}
            >
              Custom is for advanced users. You choose how often Addison asks before acting. Ready
              to continue?
            </RowConfirm>
          )}
          {customStep === 2 && !isCustomActive && (
            <RowConfirm
              confirmLabel="Turn on Custom"
              onConfirm={() => {
                onSetProfile(customProfile.id);
                setCustomStep(0);
              }}
              onCancel={() => setCustomStep(0)}
            >
              Turn on the Custom profile now? You can switch back to Simple or Developer anytime.
            </RowConfirm>
          )}
        </SurfaceRow>
      )}

      {profile.flags.headlessCli && (
        <SurfaceRow wrap
          name="For scripts: Addison's engine speaks JSON-RPC on stdio."
          value="python -m agent_core.main"
        />
      )}

      {/* Appearance — three choices; "Match this computer" follows the OS
          light/dark preference and tracks it live. */}
      {appearanceRow}
    </>
  );
}

// --- Automations -----------------------------------------------------------
// The work Addison has written down for THIS COMPUTER to run on a schedule
// (Phase-2 step 8, phase 4 of four). Rendered in EVERY profile since phase 4; what
// the profile decides is what a row may DO, not whether it is on screen.
//
// SIMPLE GETS LISTED, DISABLED ROWS THAT SAY WHY — the artifact rule
// (docs/SAFETY.md, "Artifact disabling"). Three decisions make that concrete, and
// each one is the sentence beside it in that document:
//
//   * THE ROW'S REASON IS THE CORE'S OWN, rendered verbatim from `unavailable`.
//     This side never writes that sentence and never decides that a row is
//     unavailable — the marker is display only, and dispatch is what refuses. Nor
//     is it read off `created_in_mode`: the stamp says where a thing was born, not
//     what it needs (the routines gap in KNOWN-GAPS is the cautionary entry).
//   * NO ARMING CONTROL OUTSIDE THE DEVELOPER SURFACE. Arm and Disarm are the
//     capability, and a control that could only ever come back refused is worse
//     than none. Remove STAYS in every profile: removing is a tightening, a profile
//     switch must never trap configuration somebody wants gone (plan §1, phase 1),
//     and it is the one way a Simple person can stop a job their computer is
//     running — `automation.remove` disarms first, core-side, and refuses the whole
//     removal if it cannot.
//   * THE COMMAND TEXT IS NOT PRINTED IN SIMPLE. It is printed in Developer
//     because the keyword ceremony exists to make somebody read it before arming;
//     in Simple there is no arming to read for, and a shell command is precisely
//     the developer vocabulary SAFETY.md keeps off the Simple surface ("a command
//     widget's command text is not printed in the Simple rail"). What is left is
//     what the person themselves wrote down: the name, the schedule in plain
//     words, and the truth about whether it is running.
//
// ARMED-NESS IS STILL ASKED IN SIMPLE, and that is the honest choice rather than
// the tidy one: a job armed in Developer keeps running after the profile switch,
// and this is the surface that would otherwise say nothing about it. The line is a
// statement about the person's own computer, not an affordance — it teaches no
// capability and offers no control.
//
// EVERY ROW SAYS WHETHER IT IS ARMED, AND THE OPERATING SYSTEM IS WHAT SAYS SO.
// `automation.status` is asked once when the section loads and never again: not
// stored, not polled, not checked at startup (plan §5.6). A row is the record; the
// OS holds the truth, so a G3 restore, a reinstall or somebody deleting the job
// file by hand all converge on the honest answer with no special case. When the
// answer never came, the rows say NOTHING about armed-ness rather than guessing the
// friendlier of the two — a surface that quietly claims "not armed" about a job
// that is running is the one failure this section cannot afford.
//
// The line is per ROW rather than per section on purpose: it is a statement about
// that automation's state, and two rows may now genuinely differ.
//
// ARM AND DISARM ARE ASKS, NOT CALLS. Arming is a TOOL (`arm_automation`) that goes
// through the ordinary gate plus the typed code; there is no `automation.arm` on
// the Frontend→Core surface and there deliberately never was one, because a button
// that installs a recurring job is exactly the reflex the ceremony exists to break.
// So these actions write a sentence into the composer and hand the person back to
// chat, where they press Send and answer the card that comes back.
//
// The schedule sentence comes from the CORE and is printed as it arrives. This
// component has the numbers (`schedule`) and deliberately does not use them: a
// second renderer of one fact is how a surface ends up saying "Every day at 7:5",
// on the one line somebody reads before letting a command run while they sleep.
//
// STATE COMES FROM `useAutomations`, which App owns — the tool-server pattern, and
// no longer the self-fetching one. `automations` is a snapshot-captured table, so a
// G3 restore can add or remove rows underneath this page, and every other captured
// table is re-read by App's `onRestored` closure. The ask for what the OS is
// RUNNING stays here, in this section's own effect: the hook is mounted at launch,
// and "asked when the surface loads" would quietly become "asked every time Addison
// opens" if it moved into the hook's mount (plan §5.6, and the hook says so too).

/** What a row says when the OS is not running it. Frozen copy — the frontend test
 * pins it byte-for-byte. Phase 2 said "…once you arm it" while arming did not
 * exist; the sentence flips here, in the commit that makes arming real (plan §7),
 * and it still promises nothing: nothing runs until the person arms it. */
const AUTOMATION_NOT_ARMED = "Not armed — nothing runs until you arm it.";

/** What a row says when the OS IS running it. The same truth, in the same words, as
 * the arming card's own warning — somebody who armed it yesterday should recognise
 * the sentence they agreed to. */
const AUTOMATION_ARMED =
  "Armed — your computer runs this on its own schedule, even when Addison is closed.";

/** Off macOS. One plain sentence, the same temperament as the seatbelt's non-mac
 * disclosure: it says what this computer can and cannot do, and the section then
 * offers no Arm action at all rather than a control that would only ever refuse. */
const AUTOMATION_ARMING_UNSUPPORTED =
  "Arming isn't available on this computer, so these stay written down and never run.";

/** When the OS could not be asked at all — the call itself failed. Says the honest
 * thing (that Addison does not know), and the rows stay silent about armed-ness
 * rather than claiming the comfortable half of an answer nobody got. The core's own
 * sentence is preferred whenever it managed to send one. */
const AUTOMATION_STATUS_UNKNOWN =
  "Addison couldn't check which of these your computer is running.";

/** The sentences the Arm / Disarm actions write into the composer. The person reads
 * them, presses Send, and answers the card that comes back — the ceremony is on the
 * card, and this is only how somebody gets there from a settings row. */
const ARM_REQUEST = (name: string) => `Arm the automation "${name}".`;
const DISARM_REQUEST = (name: string) => `Disarm the automation "${name}".`;

/** Said when there are none. It names the way to get one — asking — because there
 * is deliberately no "New automation" button: an automation is written by talking
 * to Addison, the same way a routine or a widget is. */
const AUTOMATIONS_EMPTY = "No automations yet. Ask Addison to set one up.";
// THE SIMPLE VARIANTS. Both of the Developer sentences invite the capability this
// profile refuses — "Ask Addison to set one up" asks for a tool that is `open_only`
// and can only come back refused, and "nothing runs until you arm it" is a
// second-person instruction to do what the row above it says you cannot. SAFETY.md
// names exactly this shape: "a vocabulary that teaches one, an affordance that
// invites one". Withholding the command text and the controls and then leaving the
// prose was the gap (phase-4 review).
const AUTOMATIONS_EMPTY_SIMPLE = "No automations saved.";
const AUTOMATION_NOT_ARMED_SIMPLE = "Not running.";

/** Before the first answer arrives. A slow fetch must never render as "none yet",
 * which is a claim about the person's own saved work that this surface cannot make
 * until it has asked. */
const AUTOMATIONS_LOADING = "Looking for your automations…";

// --- THE ORPHAN: a job the computer runs that nothing here saved ------------
// A G3 restore is REPLACE-ALL, so restoring a point from before an automation was
// written deletes its row while the job file stays installed and the computer goes on
// running it at every login. The row was the only thing that could name that job or
// reach it with a control, so it became invisible AND unstoppable (KNOWN-GAPS, closed
// 2026-08-08). RECONCILE-ON-RESTORE is the fix, and it lives here because here is
// where the two answers already meet: the OS's armed labels and the saved rows.
//
// Never by blocking a restore and never by disarming during one — an arming decision
// must not live inside the one action G3 promises is always available. Addison shows
// the leftover and changes nothing until the person presses the button.

/** `^com\.addison\.auto\.[a-z0-9][a-z0-9-]{0,39}$` — the labels Addison MINTS, and
 * nothing else. The same rule the core (`automations.label_is_addisons_own`) and the
 * Rust shell (`automation.rs::label_is_valid`) each enforce on their own side; the
 * core refuses anything else outright, so this is not the enforcement — it is what
 * keeps somebody's UNRELATED launchd job from being rendered as Addison's business in
 * the first place. A person with their own scheduled jobs must not open Settings and find
 * Addison listing them. */
const ADDISON_AUTOMATION_LABEL = /^com\.addison\.auto\.[a-z0-9][a-z0-9-]{0,39}$/;

/** The armed labels with no saved row behind them, in the order the OS reported.
 *
 * BOTH ANSWERS HAVE TO BE ANSWERS, and each one's ways of not being one are kept
 * apart. On the OS side: `null` is "never asked" and an `error` is "could not find
 * out" — inventing an orphan out of either would put a row on screen naming a job
 * that may not exist; `supported:false` IS a real answer and says nothing is
 * installed at all (arming does not exist off macOS), so there is nothing to
 * reconcile. On the ROWS side: the hook keeps the last-known list when a fetch fails,
 * so `rowsFailed` is the difference between "nothing is saved" and "Addison could not
 * find out what is saved" — and reading the second as the first would render every
 * one of somebody's real automations as an orphan on the first load after a failure.
 * Neither direction of guess is available here. */
function orphanedLabels(
  status: AutomationStatus | null,
  rows: Automation[],
  rowsFailed: boolean,
): string[] {
  if (rowsFailed) return [];
  if (!status || status.error || !status.supported) return [];
  const saved = new Set(rows.map((row) => row.label));
  return status.armed.filter(
    (label) => ADDISON_AUTOMATION_LABEL.test(label) && !saved.has(label),
  );
}

/** The orphan row's name. It states the two things that ARE known — the computer runs
 * it, and Addison has no saved copy — and claims nothing else, because nothing else is
 * left: without a row there is no name, no schedule and no command to show. */
const ORPHAN_NAME = "Running, but not saved here";
/** Why it is there and what can be done about it. Says the honest limit out loud
 * rather than leaving a person to wonder where the details went. */
const ORPHAN_EXPLANATION =
  "Your computer is running this on a schedule, but there's no saved copy of it here — " +
  "going back to an earlier restore point can leave one behind. Addison can't show what " +
  "it runs, only switch it off.";

// Exported for the step-8 tests (automations.test.tsx). It is still only rendered
// from within this page.
export function AutomationsSection({
  connected,
  automations,
  developerSurface,
  onAsk,
}: {
  connected: boolean;
  /** The saved rows, what the OS last said, and the removal handler (useAutomations,
   * owned by App so a G3 restore can re-read the list while this page is open). */
  automations: AutomationsCardState;
  /** Whether the ACTIVE PROFILE is Developer or Custom — never the policy mode, and
   * never a row's `created_in_mode` stamp. It decides only what a row may DO here:
   * the arming actions and the command text. Every row is listed in every profile. */
  developerSurface: boolean;
  /** Writes a sentence into the composer and returns to chat (App's `seedAsk`).
   * Absent for a partial caller — the Arm / Disarm actions are then simply not
   * offered, which is honest: there is nowhere for them to lead. */
  onAsk?: (text: string) => void;
}) {
  const {
    automations: rows,
    automationsLoaded: loaded,
    automationsFailed: rowsFailed,
    status,
    statusFailed,
    busy,
    error,
    refreshAutomations,
    refreshArmedState,
    handleRemove,
    handleDisarmOrphan,
  } = automations;
  // Which row is one press away from being removed. The two-press idiom the skills,
  // routine and tool-server rows use; never a browser confirm(). A removal takes
  // away the only copy of the command somebody wrote — the core refuses one it
  // cannot mint a restore point for, and this is the same care one layer up. It
  // stays in the SECTION, like the tool-server panel's, because it is a fact about
  // this screen and not about the configuration.
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);
  // The same idiom for the orphan rows, keyed by LABEL because that is all an orphan
  // has. Two presses here for a reason of its own: switching one off is the END of
  // that job — with no row there is nothing to arm again, so an accidental press is
  // not recoverable the way a saved automation's Disarm is.
  const [confirmingOrphan, setConfirmingOrphan] = useState<string | null>(null);
  // The reconciliation, computed from the two answers this section already holds.
  const orphans = orphanedLabels(status, rows, rowsFailed);

  useEffect(() => {
    if (!connected) return;
    // The ROWS again as well as the OS. The hook read them when App mounted it, but
    // `create_automation` writes rows from CHAT, and this page unmounts between
    // visits — without a re-read here, an automation somebody just asked Addison
    // for is missing from the very screen they open to see it, until a restart or
    // a removal happens to re-read the list (found by the post-merge review of
    // phase 4; phase 3's self-fetching section never had the gap). A local read,
    // which the hook's own header calls safe on any visit.
    //
    // ...AND AGAIN ON EVERY PROFILE CHANGE, which is what `developerSurface` is
    // doing in the dependencies. `unavailable` is computed CORE-SIDE from the mode
    // at the moment of the fetch, so rows fetched under one profile carry the other
    // profile's answer until something asks again — and the control that switches
    // profile is on THIS PAGE. Simple→Developer left every row tagged "Waiting" and
    // printing "…waiting in Developer profile" while Developer was active, with the
    // command hidden and no way to arm; Developer→Simple left rows with no marker
    // at all, so the controls vanished with nothing to show for it — the shape
    // SAFETY.md's artifact rule and this section's own fail-closed comment exist to
    // prevent.
    //
    // Keyed off the PROP THE ROWS ARE DRAWN FROM rather than off a refresh bolted
    // to the profile-switch handler: `developerSurface` is the same value that
    // decides what every row renders, so the two cannot drift, and it re-reads no
    // matter which route changed the profile — this page's control, a G3 restore
    // putting a whole configuration back, or an engine restart. A refresh in the
    // handler covers the button and silently misses the other two. It is also
    // exactly the boolean the marker derives from (Simple→SAFE→marked,
    // Developer/Custom→OPEN→unmarked), so a Developer↔Custom switch, which cannot
    // change a single row, fetches nothing.
    refreshAutomations();
  }, [connected, developerSurface, refreshAutomations]);

  useEffect(() => {
    if (!connected) return;
    // The OS ask stays ONCE, when the section loads — not on every list refresh,
    // not on a profile change, never on a timer and never at startup: none of those
    // change what launchd holds, and a surface that keeps asking is a surface taking
    // an action nobody just caused (the MCP temperament). Its own effect, so that
    // the row re-read above can be keyed on more than this one is. The hook owns the
    // ANSWER; the ask belongs to the moment somebody opened the page.
    refreshArmedState();
  }, [connected, refreshArmedState]);

  async function remove(automation: Automation) {
    if (confirmingRemove !== automation.id) {
      setConfirmingRemove(automation.id);
      return;
    }
    setConfirmingRemove(null);
    await handleRemove(automation.id);
  }

  async function switchOffOrphan(label: string) {
    if (confirmingOrphan !== label) {
      setConfirmingOrphan(label);
      return;
    }
    setConfirmingOrphan(null);
    await handleDisarmOrphan(label);
  }

  if (!connected) {
    return <SurfaceRow wrap name="These settings appear here once Addison's engine is connected." />;
  }

  if (!loaded) {
    return <SurfaceRow wrap name={AUTOMATIONS_LOADING} />;
  }

  return (
    <>
      {error && <SurfaceRow wrap name={error} />}

      {/* What this computer can do about arming, said once. The core's own sentence
          is preferred whenever it sent one — the same rule the removal path follows. */}
      {status?.error && <SurfaceRow wrap name={status.error} />}
      {status && !status.supported && !status.error && (
        <SurfaceRow wrap name={AUTOMATION_ARMING_UNSUPPORTED} />
      )}
      {statusFailed && <SurfaceRow wrap name={AUTOMATION_STATUS_UNKNOWN} />}

      {rows.length === 0 ? (
        <SurfaceRow wrap name={developerSurface ? AUTOMATIONS_EMPTY : AUTOMATIONS_EMPTY_SIMPLE} />
      ) : (
        rows.map((automation) => {
          // The OS's answer, or nothing at all. `null` is a third state and not a
          // falsy "not armed": it is the difference between knowing a job is idle
          // and never having asked.
          //
          // AN `error` IS NOT AN ANSWER. The core keeps three outcomes apart — this
          // computer cannot arm (`supported:false`, and then nothing IS armed, so
          // the rows may say so), the OS answered, and Addison could not find out
          // (an `error` beside an empty list). Reading that third one as "nothing is
          // armed" would tell somebody their automation was off while it ran.
          const armed = status && !status.error ? status.armed.includes(automation.label) : null;
          // Can this profile use the row at all? TWO independent answers, and both
          // must say yes — the core's display marker (what the surface SAYS) and the
          // active profile (what this page may OFFER). Either alone would be enough
          // on a matched build; together, a core that forgot to mark a row and a
          // page that has not heard of a future reason both fail toward the inert
          // row rather than toward a control that could only come back refused.
          const usable = !automation.unavailable && developerSurface;
          return (
            <SurfaceRow
              key={automation.id}
              // The routine library's own annotation for a row that is merely
              // waiting for another profile — same words, same weight, so a person
              // who has seen one recognises the other.
              tag={automation.unavailable ? <WaitingTag /> : undefined}
              name={automation.name}
              // The schedule in the machine-fact slot, in the core's words. Mono,
              // because when a job runs is a fact and not prose.
              value={automation.scheduleSentence}
              actions={
                // Every control here is named after its own automation: a column of
                // identical buttons is the shape in which somebody arms the wrong one.
                <>
                  {usable && onAsk && armed === true && (
                    <RowAction
                      tone="muted"
                      ariaLabel={`Disarm ${automation.name}`}
                      onClick={() => onAsk(DISARM_REQUEST(automation.name))}
                    >
                      Disarm…
                    </RowAction>
                  )}
                  {usable && onAsk && armed === false && status?.supported && (
                    <RowAction
                      ariaLabel={`Arm ${automation.name}`}
                      onClick={() => onAsk(ARM_REQUEST(automation.name))}
                    >
                      Arm…
                    </RowAction>
                  )}
                  {/* Remove stays in EVERY profile. It only ever takes something
                      away, a profile switch must never trap configuration somebody
                      wants gone, and the core disarms a running job before it
                      forgets the row — so this is also how a Simple person switches
                      one off. */}
                  <RowAction
                    tone="danger"
                    disabled={busy}
                    ariaLabel={`Remove ${automation.name}`}
                    onClick={() => void remove(automation)}
                  >
                    {confirmingRemove === automation.id ? "Really remove?" : "Remove"}
                  </RowAction>
                </>
              }
            >
              {/* The exact text that would run, whole and unshortened — reading it is
                  the point, and the typed code exists to make them read it. NOT on a
                  surface that cannot arm: the reading is FOR the ceremony, and a
                  shell command is the developer vocabulary SAFETY.md keeps out of
                  Simple (the command widget's text is withheld there for the same
                  reason). The name and the plain-words schedule stay — they are what
                  the person wrote down. */}
              {usable && (
                <p className="m-0 mt-1 break-all font-mono text-[11px] text-muted">
                  {automation.command}
                </p>
              )}
              {/* THE CORE'S OWN SENTENCE, rendered verbatim — never one written here,
                  and never a row disabled with nothing to show for it (the parser
                  drops a marker that cannot say why). */}
              {automation.unavailable && (
                <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                  {automation.unavailable.message}
                </p>
              )}
              {armed !== null && (
                <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                  {armed
                    ? AUTOMATION_ARMED
                    : usable
                      ? AUTOMATION_NOT_ARMED
                      : AUTOMATION_NOT_ARMED_SIMPLE}
                </p>
              )}
            </SurfaceRow>
          );
        })
      )}

      {/* WHAT THE COMPUTER IS RUNNING THAT NOTHING HERE SAVED. Rendered AFTER the
          saved list, because it is not part of it: the person's own automations come
          first, and this is the exception underneath them. It appears in EVERY
          profile — a tightening is never profile-gated (Simple keeping Remove is the
          precedent), and a Simple person is exactly the one who would otherwise have
          no way at all to stop a job Developer armed. */}
      {orphans.map((label) => (
        <SurfaceRow
          key={label}
          wrap
          name={ORPHAN_NAME}
          actions={
            // Named after the label — the only name this job has — so a screen reader
            // never meets a column of identical "Switch off" buttons. `danger`,
            // because with no row behind it this is the end of that job and there is
            // nothing here that could start it again.
            <RowAction
              tone="danger"
              disabled={busy}
              ariaLabel={`Switch off ${label}`}
              onClick={() => void switchOffOrphan(label)}
            >
              {confirmingOrphan === label ? "Really switch off?" : "Switch off"}
            </RowAction>
          }
        >
          {/* The label, whole. It is the one fact left about this job — the name of
              the file the computer is running it from — and it is a machine fact, so
              it reads in mono like a command does. NOT a command: there is no row, so
              nothing on this side knows what it runs, and the sentence below says so
              rather than leaving a blank where an answer should be. */}
          <p className="m-0 mt-1 break-all font-mono text-[11px] text-muted">{label}</p>
          <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
            {ORPHAN_EXPLANATION}
          </p>
        </SurfaceRow>
      ))}
    </>
  );
}

// --- Diagnostics -----------------------------------------------------------
function Diagnostics({
  raw,
  diagnostics,
  onClear,
}: {
  /** Developer/Custom: the raw ring is rendered. Simple never sees raw text. */
  raw: boolean;
  diagnostics: DiagnosticEntry[];
  onClear: () => void;
}) {
  if (!raw || diagnostics.length === 0) {
    return <SurfaceRow name="Engine errors" value={countLabel(raw, diagnostics.length)} />;
  }
  return (
    <>
      <SurfaceRow
        name="Engine errors"
        value={countLabel(raw, diagnostics.length)}
        action="clear"
        onAction={onClear}
      />
      {diagnostics.map((d, i) => (
        <SurfaceRow
          key={`${d.at}-${i}`}
          name={d.message}
          value={new Date(d.at).toLocaleTimeString()}
        >
          <pre className="m-0 mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[10.5px] leading-[1.6] text-ink-soft">
            {d.raw}
          </pre>
        </SurfaceRow>
      ))}
    </>
  );
}

function countLabel(raw: boolean, count: number): string {
  if (!raw) return "nothing to show yet";
  if (count === 0) return "nothing to show yet";
  return count === 1 ? "1 recent" : `${count} recent`;
}
