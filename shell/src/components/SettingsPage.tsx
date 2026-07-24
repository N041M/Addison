// Settings — the in-window settings page (Fern direction; design-brief-fern
// README §4, handoff §4). Replaces the old right-hand SettingsDrawer: it routes
// in-window in the chat-column area (the widget rail is hidden while it's open;
// the sidebar stays). Two independent flex columns (16px gap) of cards that flow
// down each column; under ~900px they stack into one column.
//
// This is a RE-HOUSING of the drawer, not a rewrite — every existing IPC wiring
// (key save, role/default-model change, routines, local-model setup + its
// progress subscription, profile switch, theme) is preserved exactly. What
// changed is layout and styling to the Fern card language.
//
//   Column A: Where Addison thinks · API keys · Routines
//   Column B: Run a model on this computer · Skills · Profile (+ Appearance) ·
//             Restore points · Diagnostics
//
// API keys is multi-provider (owner decision 2026-07-18): one mapped row each
// for anthropic | openai | google | custom (an OpenAI-compatible server).

import { useEffect, useState } from "react";
import type { ModelRole } from "../types/protocol";
import type { CloudModel, ProfileState, RoleOption } from "../types/ui";
import type { DiagnosticEntry, ProviderInfo } from "../ipc/client";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { RoutingCardState } from "../hooks/useRouting";
import type { ThemeChoice } from "../lib/theme";
import { RoutineLibrary } from "./RoutineLibrary";
import { SkillsSection } from "./SkillsSection";
import { SnapshotsCard, SaveSnapshotButton } from "./SnapshotsCard";
import { CustomGuardPanel } from "./CustomGuardPanel";
import { RoutingCard, type RoutingCardModel } from "./RoutingCard";
import { LocalModelSetup } from "./LocalModelSetup";

interface Props {
  connected: boolean;
  /**
   * Status banners, rendered below the Settings header and above the cards,
   * aligned to the content column (owner request 2026-07-19 — banners sit
   * inside a screen's content, never above its header).
   */
  notice?: React.ReactNode;
  /**
   * The model-selection bundle (useModelSelection): roles + cloud catalog, the
   * default role/model picks, provider connections, and the local-setup flow.
   */
  models: ModelSelection;
  /** The skills bundle (useSkills): the list + create/edit/toggle/remove handlers. */
  skills: SkillsState;
  /** The restore-points bundle (useSnapshots) — the G3 floor's Settings face. */
  snapshots: SnapshotsState;
  /** The Custom-profile guard bundle (useGuards). Its card renders only while the
   * active profile is Custom (Phase-2 step 2). */
  guards: GuardsCardState;
  /** The routing bundle (useRouting; Phase-2 step 3). Optional so a partial
   * caller (older tests) still renders — the routing card is simply omitted then.
   * The card itself decides toggle vs. full from the core's `surface`. */
  routing?: RoutingCardState;
  profile: ProfileState | null;
  onSetProfile: (profileId: string) => void;
  diagnostics: DiagnosticEntry[];
  onClearDiagnostics: () => void;
  theme: ThemeChoice;
  onSetTheme: (theme: ThemeChoice) => void;
  /** Opens the mobile navigation drawer (the ☰ in the below-md top bar). */
  onOpenMenu: () => void;
  /**
   * A DOM id to scroll into view once, when the page opens (the first-run
   * "Start setup" button routes here focused on the API-keys card). Cleared by
   * `onScrolled` after it's honored so a later Settings visit lands at the top.
   */
  scrollTarget?: string | null;
  onScrolled?: () => void;
}

// Stable id on the API-keys card so first-run's "Start setup" can scroll to it.
export const API_KEYS_SECTION_ID = "settings-api-keys";

// The API-key provider rows (multi-provider, owner decision 2026-07-18). ``kind``
// picks the row's affordance: a direct password input ("key"), an outlined
// "Add key" button that expands to one ("collapsed"), or the custom
// OpenAI-compatible server row with a base-URL + optional key ("custom").
type ProviderKind = "key" | "collapsed" | "custom";
const KEY_PROVIDERS: { id: string; label: string; kind: ProviderKind }[] = [
  { id: "anthropic", label: "Anthropic", kind: "key" },
  { id: "openai", label: "OpenAI", kind: "key" },
  { id: "google", label: "Google", kind: "collapsed" },
  { id: "custom", label: "Your own server", kind: "custom" },
];

// Printable-ASCII, no whitespace — catches clipboard damage (smart quotes, a "…"
// from a truncated copy, a non-breaking space) at the door before it's stored.
const KEY_SHAPE = /^[\x21-\x7E]+$/;

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

function formatAdded(addedAt?: number): string {
  if (!addedAt) return "";
  try {
    return new Date(addedAt * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

export function SettingsPage({
  connected,
  notice,
  models,
  skills,
  snapshots,
  guards,
  routing,
  profile,
  onSetProfile,
  diagnostics,
  onClearDiagnostics,
  theme,
  onSetTheme,
  onOpenMenu,
  scrollTarget,
  onScrolled,
}: Props) {
  // Honor a one-shot scroll request (first-run "Start setup" → API keys). The
  // timeout lets the page paint before we scroll, then we clear the request.
  useEffect(() => {
    if (!scrollTarget) return;
    const t = setTimeout(() => {
      const el = document.getElementById(scrollTarget);
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
      // Draw the eye to the first key input without stealing it destructively.
      el?.querySelector<HTMLInputElement>("input")?.focus({ preventScroll: true });
      onScrolled?.();
    }, 60);
    return () => clearTimeout(t);
  }, [scrollTarget, onScrolled]);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-screen="settings">
      {/* Below md: the same top-bar idiom as the chat screen — ☰ opens the
          drawer, centered title, balancing spacer. One navigation language
          across sibling screens (no separate "Back to chat" affordance). */}
      <header className="flex items-center gap-2 border-b border-line px-4 pt-[env(safe-area-inset-top)] md:hidden">
        <button
          type="button"
          onClick={onOpenMenu}
          aria-label="Menu"
          className="flex h-11 w-11 shrink-0 items-center justify-center text-glyph text-ink-soft"
        >
          ☰
        </button>
        <span className="min-w-0 flex-1 truncate text-center text-control font-semibold text-ink-soft">
          Settings
        </span>
        <span aria-hidden className="h-11 w-11 shrink-0" />
      </header>
      {/* md+: serif title only — the always-visible sidebar is the way back
          (its Settings row is lit; any conversation or ＋ New chat returns to
          the chat screen, and Escape does too). */}
      <header className="hidden items-baseline border-b border-line px-4 py-3.5 md:flex md:px-[44px]">
        <h2 className="font-serif text-title font-medium text-ink">Settings</h2>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-[44px] md:py-[30px]">
        {/* The notice shares the cards' scroll container — same coordinate
            space, same scrollbar — so its margins match the card row exactly
            (outside the container it ran wider by the scrollbar width). */}
        {notice && (
          <div className="mx-auto mb-4 flex w-full max-w-[880px] flex-col gap-2 md:-mt-2.5">
            {notice}
          </div>
        )}
        {/* Self-balancing columns (owner request 2026-07-19): CSS multicol
            distributes whole cards by height, so a tall card (e.g. API keys
            with four providers) never leaves a hole under a short opposite
            column — later cards wrap up into the shorter side automatically.
            Reading order: down the first column, then the second. Below 900px
            it's the same DOM in one stacked column. */}
        <div className="mx-auto max-w-[880px] gap-4 min-[900px]:columns-2">
          <CardSlot>
            <WhereAddisonThinks
              connected={connected}
              roles={models.roles}
              cloudModels={models.cloudModels}
              defaultRole={models.selectedRole}
              defaultCloudModel={models.selectedCloudModel}
              onChangeDefaultRole={models.handleChangeDefaultRole}
              onChangeDefaultCloudModel={models.handleChangeDefaultCloudModel}
            />
          </CardSlot>
          {routing && (
            <CardSlot>
              <Card
                title="Which model answers"
                subtitle="Choose how Addison decides which model to use for a reply."
              >
                <RoutingCard connected={connected} routing={routing} models={routingModels(models)} />
              </Card>
            </CardSlot>
          )}
          <CardSlot>
            <ApiKeys
              connected={connected}
              providers={models.providers}
              onConnect={models.handleConnectProvider}
              onRemove={models.handleRemoveProvider}
            />
          </CardSlot>
          <CardSlot>
            <Card title="Routines" subtitle="Steps Addison saved for you. Run them here or from a widget.">
              <RoutineLibrary
                exposeRoutinePlan={profile?.flags.exposeRoutinePlan}
                developer={profile?.mode === "open"}
                refreshKey={profile?.activeProfile}
              />
            </Card>
          </CardSlot>
          <CardSlot>
            <Card title="Run a model on this computer">
              <LocalModelSetup
                connected={connected}
                roles={models.roles}
                setup={models.localSetup}
                onStartSetup={models.handleStartLocalSetup}
              />
            </Card>
          </CardSlot>
          <CardSlot>
            <Card
              title="Skills"
              subtitle="Short notes telling Addison how you like things done. Turn one on and Addison keeps it in mind."
            >
              <SkillsSection connected={connected} skills={skills} />
            </Card>
          </CardSlot>
          <CardSlot>
            <ProfileCard
              connected={connected}
              profile={profile}
              onSetProfile={onSetProfile}
              theme={theme}
              onSetTheme={onSetTheme}
            />
          </CardSlot>
          {/* The Custom-profile guard panel — shown ONLY while Custom is the active
              profile (never merely because the mode is OPEN). It sits between
              Profile and Restore points so the person who just chose Custom sees
              both the guards they can loosen and the way back, together (G3/G4). */}
          {profile?.activeProfile === "custom" && (
            <CardSlot>
              <Card
                title="How careful Addison is"
                subtitle="Choose how often Addison checks with you before it acts."
              >
                <CustomGuardPanel connected={connected} guards={guards} />
              </Card>
            </CardSlot>
          )}
          {/* Restore points sit directly after Profile on purpose: the person who
              has just changed how freely Addison may act should see the way back
              in the same breath (G3). */}
          <CardSlot>
            <Card
              title="Restore points"
              subtitle="Addison saves one before anything risky, so you can always go back to a setup that worked."
              action={<SaveSnapshotButton connected={connected} snapshots={snapshots} />}
            >
              <SnapshotsCard connected={connected} snapshots={snapshots} />
            </Card>
          </CardSlot>
          {profile?.flags.rawDiagnostics && (
            <CardSlot>
              <Diagnostics diagnostics={diagnostics} onClear={onClearDiagnostics} />
            </CardSlot>
          )}
        </div>
      </div>
    </div>
  );
}

// --- Card shell ------------------------------------------------------------

/** One card's slot in the self-balancing settings columns: keeps the card in
 * one piece across a column break and owns the vertical rhythm (multicol can't
 * use flex gap, so spacing lives here). */
function CardSlot({ children }: { children: React.ReactNode }) {
  return <div className="mb-4 break-inside-avoid">{children}</div>;
}

function Card({
  id,
  title,
  subtitle,
  action,
  children,
}: {
  id?: string;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-4 rounded-card border border-line bg-surface px-[22px] py-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-body font-semibold text-ink">{title}</h3>
        {action}
      </div>
      {subtitle && <p className="mt-1 text-meta text-muted">{subtitle}</p>}
      <div className="mt-3.5">{children}</div>
    </section>
  );
}

// --- Where Addison thinks --------------------------------------------------
function WhereAddisonThinks({
  connected,
  roles,
  cloudModels,
  defaultRole,
  defaultCloudModel,
  onChangeDefaultRole,
  onChangeDefaultCloudModel,
}: {
  connected: boolean;
  roles: RoleOption[];
  cloudModels: CloudModel[];
  defaultRole: ModelRole;
  defaultCloudModel?: string;
  onChangeDefaultRole: (role: ModelRole) => void;
  onChangeDefaultCloudModel: (modelId: string) => void;
}) {
  const cloudConfigured = roles.some((r) => r.role === "primary" && r.configured);
  const localRole = roles.find((r) => r.role === "local" && r.configured);
  const localConfigured = Boolean(localRole);

  // The resolved default cloud model (the stored pick if still in the catalog,
  // else the catalog default) — its plain name rides on the Cloud row.
  const cloudValue =
    (defaultCloudModel && cloudModels.some((m) => m.id === defaultCloudModel)
      ? defaultCloudModel
      : (cloudModels.find((m) => m.default) ?? cloudModels[0])?.id) ?? "";
  const cloudName = cloudModels.find((m) => m.id === cloudValue)?.label;
  const localName = localRole?.models?.[0]?.label;

  return (
    <Card
      title="Where Addison thinks"
      subtitle="The default. You can still switch per message from the box where you type."
    >
      <div className="flex flex-col gap-2">
        <SelectableRow
          selected={defaultRole === "primary"}
          disabled={!cloudConfigured}
          onClick={() => onChangeDefaultRole("primary")}
          label={cloudName ? `Cloud — ${cloudName}` : "Cloud"}
        />
        <SelectableRow
          selected={defaultRole === "local"}
          disabled={!localConfigured}
          onClick={() => onChangeDefaultRole("local")}
          label={
            localConfigured
              ? `On this computer${localName ? ` — ${localName}` : ""}`
              : "On this computer — not set up yet"
          }
        />
      </div>

      {/* Change which cloud model is the default. Kept from the drawer so the
          model-change wiring is preserved; shown only when there's a real choice. */}
      {cloudConfigured && cloudModels.length > 1 && (
        <div className="mt-3">
          <label htmlFor="default-cloud-model" className="block text-fine font-medium text-muted">
            Cloud model
          </label>
          <select
            id="default-cloud-model"
            value={cloudValue}
            onChange={(e) => onChangeDefaultCloudModel(e.target.value)}
            className="mt-1 block w-full rounded-sm border border-line bg-paper px-3 py-2 text-control text-ink"
          >
            {cloudModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <p className="mt-3.5 text-fine text-faint">
        {connected
          ? "Cloud models come from the providers under "
          : "Once Addison's engine is connected, cloud models come from the providers under "}
        <strong className="font-semibold">API keys</strong>, below.
      </p>
    </Card>
  );
}

function SelectableRow({
  selected,
  disabled,
  onClick,
  label,
}: {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected}
      className={
        "flex items-center justify-between rounded border px-3.5 py-[11px] text-left text-row font-medium max-md:min-h-[44px] " +
        (selected
          ? "border-fern bg-fern-tint text-fern-deep"
          : "border-line bg-paper text-ink hover:border-muted disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line")
      }
    >
      <span>{label}</span>
      {selected && <span aria-hidden="true">✓</span>}
    </button>
  );
}

// --- API keys --------------------------------------------------------------
function ApiKeys({
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
    <Card
      id={API_KEYS_SECTION_ID}
      title="API keys"
      subtitle="Connect any provider. Keys go straight to your computer's keychain and are never shown again — not even here."
    >
      <div className="flex flex-col gap-2">
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
      </div>
      <p className="mt-3 text-fine text-faint">
        Addison uses whichever provider the model you pick belongs to. Models from
        every connected provider appear together in the picker by the message box.
      </p>
    </Card>
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
  // "Replace" on a connected row, or the expand on a collapsed ("Add key") row.
  const [editing, setEditing] = useState(false);
  // A connect attempt stores the key BEFORE validating; if the validate fails the
  // key is still saved, so the row keeps offering Remove to clear it.
  const [removable, setRemovable] = useState(false);

  const kind = def.kind;
  const needsKey = kind !== "custom"; // custom key is optional
  const showInput = !isConnected && (kind !== "collapsed" || editing);

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
  const focusBorder = "border-fern"; // expanded/active input → fern border (design §4)

  return (
    <div className="rounded-[8px] border border-line bg-paper px-[14px] py-2.5">
      <div className="flex items-center justify-between gap-2.5">
        <div className="min-w-0">
          <p className="text-action font-semibold text-ink">{def.label}</p>
          {isConnected ? (
            <p className="mt-px text-fine text-fern-deep">
              ✓ Key saved{info?.addedAt ? ` · added ${formatAdded(info.addedAt)}` : ""}
            </p>
          ) : kind === "custom" ? (
            <p className="mt-px font-mono text-label text-faint">
              OpenAI-compatible · {info?.baseUrl || "http://…"}
            </p>
          ) : (
            <p className="mt-px text-fine text-faint">Not connected</p>
          )}
        </div>
        {isConnected && (
          <div className="flex shrink-0 gap-3">
            <button
              type="button"
              onClick={() => setEditing(true)}
              disabled={working}
              className="rounded-sm border border-line bg-surface px-3.5 py-1.5 text-meta font-semibold text-ink hover:border-muted disabled:opacity-50 max-md:min-h-[44px] max-md:px-4"
            >
              Replace
            </button>
            <button
              type="button"
              onClick={() => void remove()}
              disabled={working}
              className="text-xs font-medium text-muted hover:text-danger disabled:opacity-50"
            >
              Remove
            </button>
          </div>
        )}
        {/* Collapsed (Google) disconnected row → an outlined "Add key" button. */}
        {!isConnected && kind === "collapsed" && !editing && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            disabled={!connected}
            className="shrink-0 rounded-sm border border-line bg-transparent px-3.5 py-1.5 text-xs font-medium text-ink-soft hover:border-muted disabled:opacity-50 max-md:min-h-[44px] max-md:px-4"
          >
            Add key
          </button>
        )}
      </div>

      {showInput && (
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
              disabled={!connected || working}
              className={
                "min-w-0 rounded-sm border bg-surface px-3 py-2 font-mono text-hint text-ink placeholder:text-faint disabled:opacity-60 max-md:min-h-[44px] " +
                (baseUrl ? focusBorder : "border-line")
              }
            />
          )}
          <div className="flex gap-2">
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={key}
              onChange={(e) => {
                setKey(e.target.value);
                if (status !== "idle") setStatus("idle");
              }}
              placeholder={
                kind === "custom" ? "Key (optional)…" : `Paste your ${def.label} key…`
              }
              disabled={!connected || working}
              className={
                "min-w-0 flex-1 rounded-sm border bg-surface px-3 py-2 text-control text-ink placeholder:text-faint disabled:opacity-60 max-md:min-h-[44px] " +
                (key ? focusBorder : "border-line")
              }
            />
            <button
              type="button"
              onClick={() => void connect()}
              disabled={
                !connected ||
                working ||
                (needsKey && !key.trim()) ||
                (kind === "custom" && !baseUrl.trim())
              }
              className="shrink-0 rounded-sm bg-fern px-4 py-2 text-meta font-semibold text-on-accent hover:bg-fern-deep disabled:cursor-not-allowed disabled:opacity-50 max-md:min-h-[44px] max-md:px-5"
            >
              {working ? "Checking…" : kind === "custom" ? "Connect" : "Save"}
            </button>
          </div>
          {status === "error" && <p className="text-fine text-danger">{error}</p>}
          {status !== "error" && (isConnected || removable) && (
            <p className="text-fine text-faint">
              Checked with one tiny request, then locked away in the keychain.
            </p>
          )}
          {/* A failed connect still stored the key — offer to clear it. */}
          {removable && !isConnected && (
            <button
              type="button"
              onClick={() => void remove()}
              disabled={working}
              className="self-start text-fine font-medium text-muted hover:text-danger disabled:opacity-50"
            >
              Remove the saved key
            </button>
          )}
          {!connected && (
            <p className="text-fine text-muted">
              You can add a key once Addison's engine is connected.
            </p>
          )}
        </div>
      )}
    </div>
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
  // cancels. Switching BACK (Developer→Simple) reduces what Addison can do, so it
  // needs no confirmation and never sets this.
  const [confirming, setConfirming] = useState<string | null>(null);
  // The "Advanced…" disclosure that reveals the Custom profile, and the two-step
  // confirm before Custom is actually turned on. Custom is deeper and more
  // permissive than Developer, so it never sits in the plain segmented control and
  // never switches on with a single click.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [customStep, setCustomStep] = useState<0 | 1 | 2>(0);
  const mode = profile?.mode ?? (profile?.activeProfile === "developer" ? "open" : "safe");

  // Advanced profiles (only Custom sets `advanced`) never render as ordinary
  // options — they live behind the disclosure. Simple/Developer are the plain
  // segmented control.
  const advancedProfiles = (profile?.profiles ?? []).filter((p) => p.advanced);
  const basicProfiles = (profile?.profiles ?? []).filter((p) => !p.advanced);
  const customProfile = advancedProfiles[0];
  const isCustomActive = Boolean(customProfile && profile?.activeProfile === customProfile.id);
  // Auto-reveal the disclosure when Custom is already in use, so the active
  // profile is never hidden from the person who is standing in it.
  const showAdvanced = advancedOpen || isCustomActive;

  function handlePick(id: string) {
    if (!profile || id === profile.activeProfile) return;
    // In SAFE mode the only other basic profile is Developer/OPEN — a step UP in
    // what Addison may do, so ask first. Otherwise switch straight away.
    if (mode === "safe") {
      setConfirming(id);
    } else {
      onSetProfile(id);
    }
  }

  return (
    <Card title="Profile" subtitle="How freely Addison can act on this computer.">
      {!connected || !profile || profile.profiles.length === 0 ? (
        <p className="text-hint text-muted">
          {connected
            ? "Profile options will appear here in a moment."
            : "Your profile choices appear here once Addison's engine is connected."}
        </p>
      ) : (
        <>
          <div
            role="group"
            aria-label="Profile"
            className="flex gap-0.5 rounded border border-line bg-paper p-[3px]"
          >
            {basicProfiles.map((p) => {
              const active = p.id === profile.activeProfile;
              return (
                <button
                  key={p.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => handlePick(p.id)}
                  className={
                    // Constant font-medium in both states so selecting never
                    // re-flows the label width; the active cue is bg + color.
                    "flex-1 rounded-sm px-0 py-2 text-control font-medium transition-colors max-md:min-h-[44px] " +
                    (active
                      ? "bg-fern-tint text-fern-deep"
                      : "bg-transparent text-muted hover:text-ink-soft")
                  }
                >
                  {p.label}
                </button>
              );
            })}
          </div>
          {/* Honest, mode-scoped description — the profile now changes what Addison
              is ALLOWED to do, not just what it shows (owner decision 2026-07-19). */}
          <p className="mt-2.5 text-fine leading-[1.55] text-faint">
            {mode === "open"
              ? "Addison can run commands and scripts on this computer, acts without asking except for destructive actions, and some actions can't be undone."
              : "Addison asks before anything it does, and everything can be undone."}
          </p>
          {/* Simple→Developer confirmation — inline (never a browser confirm()),
              matching the PermissionCard look. */}
          {confirming && (
            <div className="mt-3 rounded-card bg-fern-tint px-[15px] py-[13px]">
              <p className="text-fine leading-relaxed text-ink-soft">
                Developer profile lets Addison act more freely on this computer. You can switch
                back anytime.
              </p>
              <div className="mt-2.5 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => {
                    onSetProfile(confirming);
                    setConfirming(null);
                  }}
                  className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep"
                >
                  Switch
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(null)}
                  className="text-xs font-medium text-ink-soft hover:text-muted"
                >
                  Not now
                </button>
              </div>
            </div>
          )}
          {/* Advanced… — the disclosure that reveals the Custom profile. Quiet,
              text-level, matching the Settings idiom for opt-in depth. Until it is
              opened, Custom is not in the DOM at all. */}
          {customProfile && (
            <div className="mt-3 border-t border-hair pt-3">
              {!showAdvanced ? (
                <button
                  type="button"
                  onClick={() => setAdvancedOpen(true)}
                  className="text-fine font-medium text-ink-soft hover:text-fern-deep"
                >
                  Advanced…
                </button>
              ) : (
                <div className="rounded-card border border-line bg-paper px-[15px] py-[13px]">
                  <div className="flex items-baseline justify-between gap-3">
                    <button
                      type="button"
                      aria-pressed={isCustomActive}
                      disabled={isCustomActive}
                      onClick={() => setCustomStep(1)}
                      className={
                        "rounded-sm px-2.5 py-1 text-control font-medium transition-colors " +
                        (isCustomActive
                          ? "bg-fern-tint text-fern-deep"
                          : "text-muted hover:text-ink-soft")
                      }
                    >
                      {customProfile.label}
                    </button>
                    {isCustomActive && (
                      <span className="border-l-2 border-fern pl-1.5 text-tag font-semibold uppercase tracking-caps-wide text-fern-deep">
                        In use
                      </span>
                    )}
                  </div>
                  {/* The core-authored honest description (contract D8). */}
                  <p className="mt-2 text-fine leading-relaxed text-faint">
                    {customProfile.description}
                  </p>
                  {/* Two-step inline confirm before Custom is turned on — never a
                      browser confirm(), and profile.set fires only at the end. */}
                  {customStep >= 1 && !isCustomActive && (
                    <div className="mt-3 rounded-card bg-fern-tint px-[15px] py-[13px]">
                      {customStep === 1 ? (
                        <>
                          <p className="text-fine leading-relaxed text-ink-soft">
                            Custom is for advanced users. You choose how often Addison asks before
                            acting. Ready to continue?
                          </p>
                          <div className="mt-2.5 flex flex-wrap items-center gap-3">
                            <button
                              type="button"
                              onClick={() => setCustomStep(2)}
                              className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep"
                            >
                              Continue
                            </button>
                            <button
                              type="button"
                              onClick={() => setCustomStep(0)}
                              className="text-xs font-medium text-ink-soft hover:text-muted"
                            >
                              Not now
                            </button>
                          </div>
                        </>
                      ) : (
                        <>
                          <p className="text-fine leading-relaxed text-ink-soft">
                            Turn on the Custom profile now? You can switch back to Simple or
                            Developer anytime.
                          </p>
                          <div className="mt-2.5 flex flex-wrap items-center gap-3">
                            <button
                              type="button"
                              onClick={() => {
                                onSetProfile(customProfile.id);
                                setCustomStep(0);
                              }}
                              className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep"
                            >
                              Turn on Custom
                            </button>
                            <button
                              type="button"
                              onClick={() => setCustomStep(0)}
                              className="text-xs font-medium text-ink-soft hover:text-muted"
                            >
                              Not now
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {profile.flags.headlessCli && (
            <p className="mt-2.5 text-fine text-muted">
              For scripts: Addison's engine speaks JSON-RPC on stdio — run{" "}
              <code className="font-mono text-label text-ink-soft">python -m agent_core.main</code>{" "}
              from the repo.
            </p>
          )}
        </>
      )}

      {/* Appearance — below a hair divider, moved here from the old drawer. Three
          choices; "Match this computer" follows the OS light/dark preference and
          tracks it live. The label sits above a full-width segmented control (the
          third label is too long to sit inline), matching the Profile control. */}
      <div className="mt-4 border-t border-hair pt-3.5">
        <span className="text-control text-ink-soft">Appearance</span>
        <div
          role="group"
          aria-label="Appearance"
          className="mt-2 flex gap-0.5 rounded border border-line bg-paper p-[3px]"
        >
          {(
            [
              ["light", "Light"],
              ["dark", "Dark"],
              ["system", "Match this computer"],
            ] as const
          ).map(([value, label]) => {
            const active = theme === value;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={active}
                onClick={() => onSetTheme(value)}
                className={
                  // Constant font-medium so the label never re-flows when
                  // selected; state is carried by bg + color, eased calmly.
                  "flex-1 rounded-sm px-2 py-1.5 text-hint font-medium leading-tight transition-colors max-md:min-h-[44px] " +
                  (active ? "bg-fern-tint text-fern-deep" : "bg-transparent text-muted hover:text-ink-soft")
                }
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>
    </Card>
  );
}

// --- Diagnostics (Developer only) ------------------------------------------
function Diagnostics({
  diagnostics,
  onClear,
}: {
  diagnostics: DiagnosticEntry[];
  onClear: () => void;
}) {
  return (
    <Card
      title="Diagnostics"
      subtitle="The most recent raw errors from the engine, newest first."
      action={
        diagnostics.length > 0 ? (
          <button
            type="button"
            onClick={onClear}
            className="shrink-0 rounded-sm border border-line bg-transparent px-3 py-1.5 text-xs font-medium text-ink-soft hover:border-muted"
          >
            Clear
          </button>
        ) : undefined
      }
    >
      {diagnostics.length === 0 ? (
        <p className="text-hint text-muted">Nothing to show yet.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {diagnostics.map((d, i) => (
            <li key={`${d.at}-${i}`} className="rounded border border-line bg-paper p-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-meta font-medium text-ink">{d.message}</span>
                <span className="shrink-0 font-mono text-label text-muted">
                  {new Date(d.at).toLocaleTimeString()}
                </span>
              </div>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-label text-ink-soft">
                {d.raw}
              </pre>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
