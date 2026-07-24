// Custom profile + the two prompting guards (Phase-2 step 2) — the frontend
// guards. Four halves:
//
//   (a) `parseGuards` fails CLOSED — an off-vocabulary guard value is coerced to
//       a known-safe one, never trusted, so the strictness comparison can't be
//       fed a value it doesn't understand.
//   (b) The Custom guard panel, rendered for real: the frozen intro + option copy
//       byte-for-byte, a WEAKENING move gated behind the permanent-anchor confirm
//       while a TIGHTENING move saves straight through, and a refused save shown
//       as one plain sentence.
//   (c) The Profile card's "Advanced…" disclosure: Custom is not in the DOM until
//       the disclosure is opened, and turning it on is gated behind a two-step
//       inline confirm (never window.confirm), so profile.set fires only at the
//       very end.
//   (d) The page-level gate: the guard-panel CARD renders only while the active
//       profile is Custom.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { parseGuards } from "../ipc/client";
import { CustomGuardPanel } from "../components/CustomGuardPanel";
import { ProfileCard, SettingsPage } from "../components/SettingsPage";
import type { GuardsCardState } from "../hooks/useGuards";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsState, ProfileState } from "../types/ui";

// globals:false → testing-library's automatic cleanup isn't registered.
afterEach(cleanup);

// --- Frozen copy (contract D8) — byte-for-byte. -----------------------------
const PANEL_INTRO =
  "These settings change how often Addison asks you before acting. " +
  "They never change Addison's ability to go back to a working setup.";
const WEAKENING_CONFIRM =
  "Addison will ask you less often before acting. Before this changes, Addison " +
  "saves a permanent restore point of the last setup it saw working — it can't " +
  "be deleted, and you can always go back to it.";
const CARD_PER_INVOCATION =
  "Always ask — Addison asks every time before anything that can't be undone.";
const CARD_SESSION =
  "Ask once — after you approve a risky action, Addison won't ask about that tool " +
  "again until you close Addison.";
const SCOPE_NONE = "Ask about everything — Addison asks before every kind of action.";
const SCOPE_NON_DESTRUCTIVE =
  "Ask only for risky actions — everyday actions go ahead; anything that can't be " +
  "undone asks first.";
const SCOPE_EVERYTHING =
  "Never ask — Addison acts without asking, including things that can't be undone, " +
  "like deleting files.";
const CUSTOM_DESCRIPTION =
  "Custom — for advanced users. Addison can do everything the Developer profile " +
  "allows, and you choose how often it asks you first. Going back to a working " +
  "setup always stays possible.";

// ---------------------------------------------------------------------------
// (a) parseGuards
// ---------------------------------------------------------------------------
describe("parseGuards", () => {
  it("round-trips a realistic guards.get payload", () => {
    expect(
      parseGuards({
        destructiveCard: "session",
        autoGrantScope: "everything",
        defaults: { destructiveCard: "per_invocation", autoGrantScope: "non_destructive" },
        active: true,
      }),
    ).toEqual({
      destructiveCard: "session",
      autoGrantScope: "everything",
      defaults: { destructiveCard: "per_invocation", autoGrantScope: "non_destructive" },
      active: true,
    });
  });

  it("coerces an off-vocabulary value to a known-safe one, never trusting it", () => {
    const parsed = parseGuards({
      destructiveCard: "whenever",
      autoGrantScope: "always",
      defaults: { destructiveCard: "session", autoGrantScope: "everything" },
      active: true,
    });
    // Unknown → the wire's declared default (itself validated).
    expect(parsed.destructiveCard).toBe("session");
    expect(parsed.autoGrantScope).toBe("everything");
  });

  it("falls back to the known defaults when the wire omits them", () => {
    const parsed = parseGuards({ active: false });
    expect(parsed.defaults).toEqual({
      destructiveCard: "per_invocation",
      autoGrantScope: "non_destructive",
    });
    expect(parsed.destructiveCard).toBe("per_invocation");
    expect(parsed.autoGrantScope).toBe("non_destructive");
  });

  it("marks active only on a strict true", () => {
    expect(parseGuards({ active: true }).active).toBe(true);
    expect(parseGuards({ active: 1 }).active).toBe(false);
    expect(parseGuards({ active: "yes" }).active).toBe(false);
    expect(parseGuards({}).active).toBe(false);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", []]) {
      expect(parseGuards(junk)).toEqual({
        destructiveCard: "per_invocation",
        autoGrantScope: "non_destructive",
        defaults: { destructiveCard: "per_invocation", autoGrantScope: "non_destructive" },
        active: false,
      });
    }
  });
});

// ---------------------------------------------------------------------------
// (b) the Custom guard panel
// ---------------------------------------------------------------------------
const GUARDS: GuardsState = {
  destructiveCard: "per_invocation",
  autoGrantScope: "non_destructive",
  defaults: { destructiveCard: "per_invocation", autoGrantScope: "non_destructive" },
  active: true,
};

function guardsStateWith(over: Partial<GuardsCardState> = {}): GuardsCardState {
  return {
    guards: GUARDS,
    guardsLoaded: true,
    busy: false,
    error: null,
    refreshGuards: vi.fn(),
    handleSave: vi.fn(async () => {}),
    ...over,
  };
}

function renderPanel(state: GuardsCardState) {
  render(<CustomGuardPanel connected={true} guards={state} />);
}

describe("the Custom guard panel", () => {
  it("shows the frozen intro and both guards' option copy, byte-for-byte", () => {
    renderPanel(guardsStateWith());
    expect(screen.getByText(PANEL_INTRO)).toBeTruthy();
    for (const copy of [
      CARD_PER_INVOCATION,
      CARD_SESSION,
      SCOPE_NONE,
      SCOPE_NON_DESTRUCTIVE,
      SCOPE_EVERYTHING,
    ]) {
      expect(screen.getByText(copy)).toBeTruthy();
    }
  });

  it("contains exactly the two guards — no third control", () => {
    renderPanel(guardsStateWith());
    // Each guard is one role="group". A third would mean a control that isn't one
    // of the two prompting guards crept into a safety panel (contract Scope).
    expect(screen.getAllByRole("group")).toHaveLength(2);
  });

  it("shows a quiet line before the guards have loaded", () => {
    renderPanel(guardsStateWith({ guards: null, guardsLoaded: false }));
    expect(screen.queryByText(PANEL_INTRO)).toBeNull();
    expect(screen.getByText("Loading your settings…")).toBeTruthy();
  });

  it("saves a TIGHTENING move straight through — no weakening confirm", () => {
    // Current non_destructive; picking `none` asks MORE often (tightening).
    const state = guardsStateWith();
    renderPanel(state);
    fireEvent.click(screen.getByText(SCOPE_NONE));
    expect(screen.queryByText(WEAKENING_CONFIRM)).toBeNull();
    expect(state.handleSave).toHaveBeenCalledWith({ autoGrantScope: "none" });
  });

  it("also tightens the card guard straight through (session → per_invocation)", () => {
    const state = guardsStateWith({
      guards: { ...GUARDS, destructiveCard: "session" },
    });
    renderPanel(state);
    fireEvent.click(screen.getByText(CARD_PER_INVOCATION));
    expect(screen.queryByText(WEAKENING_CONFIRM)).toBeNull();
    expect(state.handleSave).toHaveBeenCalledWith({ destructiveCard: "per_invocation" });
  });

  it("gates a WEAKENING move behind the frozen permanent-anchor confirm", () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    // Current per_invocation; picking `session` asks LESS often (weakening).
    const state = guardsStateWith();
    renderPanel(state);

    fireEvent.click(screen.getByText(CARD_SESSION));
    // Step one only warns; nothing has been saved yet.
    expect(state.handleSave).not.toHaveBeenCalled();
    expect(screen.getByText(WEAKENING_CONFIRM)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(state.handleSave).toHaveBeenCalledWith({ destructiveCard: "session" });
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("also gates the broader scope weakening (non_destructive → everything)", () => {
    const state = guardsStateWith();
    renderPanel(state);
    fireEvent.click(screen.getByText(SCOPE_EVERYTHING));
    expect(state.handleSave).not.toHaveBeenCalled();
    expect(screen.getByText(WEAKENING_CONFIRM)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(state.handleSave).toHaveBeenCalledWith({ autoGrantScope: "everything" });
  });

  it("lets the person back out of a weakening without saving", () => {
    const state = guardsStateWith();
    renderPanel(state);
    fireEvent.click(screen.getByText(CARD_SESSION));
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(state.handleSave).not.toHaveBeenCalled();
    expect(screen.queryByText(WEAKENING_CONFIRM)).toBeNull();
  });

  it("renders a refused save as one plain sentence, not a stack trace", () => {
    const refusal =
      "Addison couldn't save the permanent restore point that goes with lowering a " +
      "safeguard, so nothing was changed. Try again in a moment.";
    renderPanel(guardsStateWith({ error: refusal }));
    const text = document.body.textContent ?? "";
    expect(text).toContain(refusal);
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("Error:");
  });
});

// ---------------------------------------------------------------------------
// (c) the Profile card's Advanced… disclosure + two-step confirm
// ---------------------------------------------------------------------------
const PROFILE_WITH_CUSTOM: ProfileState = {
  activeProfile: "simple",
  mode: "safe",
  profiles: [
    { id: "simple", label: "Simple", description: "Approachable by default." },
    { id: "developer", label: "Developer", description: "Power on request." },
    { id: "custom", label: "Custom", description: CUSTOM_DESCRIPTION, advanced: true },
  ],
  flags: {
    exposeRoutinePlan: false,
    rawDiagnostics: false,
    headlessCli: false,
    byokFirstOnboarding: false,
  },
};

function renderProfileCard(profile: ProfileState, onSetProfile = vi.fn()) {
  render(
    <ProfileCard
      connected={true}
      profile={profile}
      onSetProfile={onSetProfile}
      theme="light"
      onSetTheme={vi.fn()}
    />,
  );
  return onSetProfile;
}

describe("the Profile card's Advanced… disclosure", () => {
  it("keeps Custom out of the DOM until the disclosure is opened", () => {
    renderProfileCard(PROFILE_WITH_CUSTOM);
    // The basic profiles are the plain segmented control; Custom is not shown.
    expect(screen.getByRole("button", { name: "Simple" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Developer" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Custom" })).toBeNull();
    expect(screen.queryByText(CUSTOM_DESCRIPTION)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Advanced…" }));
    // Now Custom and its honest description appear.
    expect(screen.getByRole("button", { name: "Custom" })).toBeTruthy();
    expect(screen.getByText(CUSTOM_DESCRIPTION)).toBeTruthy();
  });

  it("gates turning Custom on behind a two-step confirm — profile.set fires only at the end", () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const onSet = renderProfileCard(PROFILE_WITH_CUSTOM);

    fireEvent.click(screen.getByRole("button", { name: "Advanced…" }));
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    // Step one: not yet switched.
    expect(onSet).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    // Step two: still not switched.
    expect(onSet).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Turn on Custom" }));
    expect(onSet).toHaveBeenCalledTimes(1);
    expect(onSet).toHaveBeenCalledWith("custom");
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("lets the person back out of the first confirm step without switching", () => {
    const onSet = renderProfileCard(PROFILE_WITH_CUSTOM);
    fireEvent.click(screen.getByRole("button", { name: "Advanced…" }));
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(onSet).not.toHaveBeenCalled();
    // The step-two commit button is gone.
    expect(screen.queryByRole("button", { name: "Turn on Custom" })).toBeNull();
  });

  it("auto-reveals the disclosure and marks Custom in use when it is the active profile", () => {
    renderProfileCard({ ...PROFILE_WITH_CUSTOM, activeProfile: "custom", mode: "open" });
    // No click needed — the active profile is never hidden from the person in it.
    expect(screen.getByText(CUSTOM_DESCRIPTION)).toBeTruthy();
    expect(screen.getByText("In use")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (d) the page-level gate: the guard-panel CARD only under Custom
// ---------------------------------------------------------------------------

// A stub props bag for SettingsPage. connected:false keeps the sub-cards on their
// quiet placeholders (no IPC), which is all we need to prove the card gate.
function renderSettings(profile: ProfileState) {
  const noop = vi.fn();
  const models = {
    roles: [],
    rolesLoaded: true,
    cloudModels: [],
    providers: [],
    selectedRole: "primary",
    selectedCloudModel: undefined,
    selectedLocalModel: undefined,
    selectedEffort: undefined,
    localSetup: null,
    setLocalSetup: noop,
    refreshRoles: noop,
    refreshProviders: noop,
    effectiveLocalModel: undefined,
    effectiveCloudModel: undefined,
    handleSelectModel: noop,
    handleSelectEffort: noop,
    handleChangeDefaultCloudModel: noop,
    handleChangeDefaultRole: noop,
    handleStartLocalSetup: noop,
    handleConnectProvider: noop,
    handleRemoveProvider: noop,
  };
  const skills = {
    skills: [],
    skillsLoaded: true,
    refreshSkills: noop,
    handleCreateSkill: vi.fn(async () => {}),
    handleUpdateSkill: vi.fn(async () => {}),
    handleToggleSkill: vi.fn(async () => {}),
    handleDeleteSkill: vi.fn(async () => {}),
  };
  const snapshots = {
    snapshots: [],
    snapshotsLoaded: true,
    lastWorkingId: undefined,
    lastWorkingLabel: undefined,
    lastWorkingProfileChange: undefined,
    warning: undefined,
    notice: null,
    busy: false,
    refreshSnapshots: noop,
    handleCreateSnapshot: vi.fn(async () => {}),
    handleRestoreLastWorking: vi.fn(async () => {}),
    handleRestoreSnapshot: vi.fn(async () => {}),
    handleDeleteSnapshot: vi.fn(async () => {}),
  };
  const guards = guardsStateWith();
  render(
    <SettingsPage
      connected={false}
      models={models as unknown as ModelSelection}
      skills={skills as unknown as SkillsState}
      snapshots={snapshots as unknown as SnapshotsState}
      guards={guards}
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
      onOpenMenu={noop}
    />,
  );
}

const GUARD_CARD_TITLE = "How careful Addison is";

describe("the guard-panel card gate", () => {
  it("renders the guard panel card only when the active profile is Custom", () => {
    renderSettings({ ...PROFILE_WITH_CUSTOM, activeProfile: "custom", mode: "open" });
    expect(screen.getByText(GUARD_CARD_TITLE)).toBeTruthy();
  });

  it("does not render the guard panel card under Developer", () => {
    renderSettings({ ...PROFILE_WITH_CUSTOM, activeProfile: "developer", mode: "open" });
    expect(screen.queryByText(GUARD_CARD_TITLE)).toBeNull();
  });

  it("does not render the guard panel card under Simple", () => {
    renderSettings({ ...PROFILE_WITH_CUSTOM, activeProfile: "simple", mode: "safe" });
    expect(screen.queryByText(GUARD_CARD_TITLE)).toBeNull();
  });
});
