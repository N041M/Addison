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
// (Developer/Custom only) · Restore points · Diagnostics.
//
// TWO THINGS THAT ARE NOT STYLING, and must survive any future edit here:
//   * G1 — a key typed into a row goes to the OS keychain through the Rust
//     command and nowhere else. `provider.list` carries status only; nothing on
//     this page ever holds or displays a key.
//   * The Custom profile is never one click away. It lives behind the
//     "Advanced…" disclosure and a two-step confirm, with the core's own honest
//     description in between (Phase-2 step 2).

import { useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";
import type { Automation, AutomationStatus, ModelRole } from "../types/protocol";
import type { CloudModel, ProfileState, RoleOption } from "../types/ui";
import { ipc, type DiagnosticEntry, type ProviderInfo } from "../ipc/client";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { RoutingCardState } from "../hooks/useRouting";
import type { WorkspaceCardState } from "../hooks/useWorkspace";
import type { McpServersCardState } from "../hooks/useMcpServers";
import type { ThemeChoice } from "../lib/theme";
import type { PopupAnchor } from "./ModelPopup";
import {
  RowAction,
  RowConfirm,
  Surface,
  SurfaceRow,
  SurfaceSection,
  SURFACE_ID,
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
          schedule (Phase-2 step 8, phase 2 of four). Same Developer/Custom gate as
          the two sections above, for the same reason and one more: an automation's
          payload is a shell command, which Simple has no place for (plan §5.3), and
          the tool that writes one is `dev_only` and refused at dispatch outside OPEN
          independently of this gate. Phase 4 replaces the gate with a
          listed-but-disabled treatment, so a Simple person sees their saved rows and
          cannot use them — the artifact rule. What is armed here is what the
          OPERATING SYSTEM says is armed, asked when the section loads. */}
      {developerSurface && (
        <SurfaceSection label="Automations">
          <AutomationsSection connected={connected} onAsk={onAskAddison} />
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
// (Phase-2 step 8, phase 3 of four). Rendered on the Developer/Custom surfaces
// only — the page-level gate above decides that, on the active profile and never
// the policy mode, exactly as tool servers and trusted folders do.
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
// Self-fetching, like RoutineLibrary and unlike the tool-server panel — there is no
// state here for App to own and nothing else in the app reads this list yet. Phase
// 4 moves the fetch into a hook so a G3 restore can re-read it while Settings is
// open; the armed-ness ask moves with it.

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

/** Before the first answer arrives. A slow fetch must never render as "none yet",
 * which is a claim about the person's own saved work that this surface cannot make
 * until it has asked. */
const AUTOMATIONS_LOADING = "Looking for your automations…";

/** When a removal doesn't land and the core said nothing usable about why. The
 * core's own sentence is preferred whenever there is one. */
const AUTOMATION_REMOVE_FAILED = "Addison couldn't remove that automation just now.";

// Exported for the step-8 tests (automations.test.tsx). It is still only rendered
// from within this page.
export function AutomationsSection({
  connected,
  onAsk,
}: {
  connected: boolean;
  /** Writes a sentence into the composer and returns to chat (App's `seedAsk`).
   * Absent for a partial caller — the Arm / Disarm actions are then simply not
   * offered, which is honest: there is nowhere for them to lead. */
  onAsk?: (text: string) => void;
}) {
  const [automations, setAutomations] = useState<Automation[]>([]);
  // What the OPERATING SYSTEM said when this section loaded. `null` is "no answer" —
  // never "nothing armed", which is the guess that would let a running job render as
  // a quiet draft.
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [statusFailed, setStatusFailed] = useState(false);
  // "not asked yet" vs "asked" — see AUTOMATIONS_LOADING.
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which row is one press away from being removed. The two-press idiom the skills,
  // routine and tool-server rows use; never a browser confirm(). A removal takes
  // away the only copy of the command somebody wrote — the core refuses one it
  // cannot mint a restore point for, and this is the same care one layer up.
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);

  const refresh = useCallback(() => {
    ipc
      .listAutomations()
      .then((rows) => {
        setAutomations(rows);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the section; still stop the
        // looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    if (!connected) return;
    refresh();
    // ONCE, when the section loads. Not on every list refresh and never on a timer:
    // removing a row does not change what launchd holds, and a surface that keeps
    // asking is a surface taking an action nobody just caused (the MCP temperament).
    ipc
      .getAutomationStatus()
      .then((next) => {
        setStatus(next);
        setStatusFailed(false);
      })
      .catch(() => {
        setStatus(null);
        setStatusFailed(true);
      });
  }, [connected, refresh]);

  async function remove(automation: Automation) {
    if (confirmingRemove !== automation.id) {
      setConfirmingRemove(automation.id);
      return;
    }
    setConfirmingRemove(null);
    setBusy(true);
    setError(null);
    try {
      const result = await ipc.removeAutomation(automation.id);
      // A refusal is a resolved {ok:false} carrying the core's already-plain
      // sentence — the row is gone already, or a restore point could not be saved,
      // in which case nothing was removed and saying so is the whole point.
      if (!result.ok) setError(result.error ?? AUTOMATION_REMOVE_FAILED);
    } catch {
      setError(AUTOMATION_REMOVE_FAILED);
    } finally {
      setBusy(false);
      // Either way: the list on screen is now a guess, and the core holds the truth.
      refresh();
    }
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

      {automations.length === 0 ? (
        <SurfaceRow wrap name={AUTOMATIONS_EMPTY} />
      ) : (
        automations.map((automation) => {
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
          return (
            <SurfaceRow
              key={automation.id}
              name={automation.name}
              // The schedule in the machine-fact slot, in the core's words. Mono,
              // because when a job runs is a fact and not prose.
              value={automation.scheduleSentence}
              actions={
                // Every control here is named after its own automation: a column of
                // identical buttons is the shape in which somebody arms the wrong one.
                <>
                  {onAsk && armed === true && (
                    <RowAction
                      tone="muted"
                      ariaLabel={`Disarm ${automation.name}`}
                      onClick={() => onAsk(DISARM_REQUEST(automation.name))}
                    >
                      Disarm…
                    </RowAction>
                  )}
                  {onAsk && armed === false && status?.supported && (
                    <RowAction
                      ariaLabel={`Arm ${automation.name}`}
                      onClick={() => onAsk(ARM_REQUEST(automation.name))}
                    >
                      Arm…
                    </RowAction>
                  )}
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
                  the point, and the typed code exists to make them read it. */}
              <p className="m-0 mt-1 break-all font-mono text-[11px] text-muted">
                {automation.command}
              </p>
              {armed !== null && (
                <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                  {armed ? AUTOMATION_ARMED : AUTOMATION_NOT_ARMED}
                </p>
              )}
            </SurfaceRow>
          );
        })
      )}
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
