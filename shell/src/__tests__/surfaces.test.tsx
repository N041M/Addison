// The full-page surfaces (dark direction, phase 3) — the four properties that
// are promises rather than styling, and would fail silently if they broke:
//
//   (a) TOOLS SHOWS ONLY WHAT IS REAL. This is the page a person opens to check
//       what their assistant is wired into, so a fabricated row here is a lie in
//       the worst place (IMPLEMENTATION.md, standing rule 1) — and workspace
//       trust is a Developer/Custom surface, never leaked to Simple through it.
//   (b) A PENDING CONSENT CARD IS VISIBLE ON A SURFACE. The widget rail normally
//       carries it and is collapsed to zero width on a surface, so without the
//       pinned slot a question that is holding a turn open would be invisible.
//   (c) THE MODEL POPUP LANDS UNDER THE POINTER AND STAYS ON SCREEN. The
//       arithmetic (x − 250, y − 14 − index × 29, clamped ≥12px) is the whole
//       design of the control and is otherwise unobservable from a test.
//   (d) THE RESTORE-POINTS MODAL CLOSES ON THE SCRIM AND THE ✕, AND A CLICK
//       INSIDE IT DOES NOT. A mis-click on a row must not throw the list away.
//
// Plus the Snapshots surface's recency grouping, which must never render a group
// it has no rows for — an empty "Today" heading claims Addison saved nothing
// today, which is an assertion, not an absence.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { ToolsSurface } from "../components/ToolsSurface";
import { SnapshotsSurface, groupSnapshotsByRecency } from "../components/SnapshotsSurface";
import { RestorePointsModal } from "../components/RestorePointsModal";
import { ModelPopup } from "../components/ModelPopup";
import { Surface, SurfaceRow, SurfaceSection } from "../components/Surface";
import { PermissionCard } from "../components/PermissionCard";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { ProviderInfo } from "../ipc/client";
import type { RoleOption, Snapshot, WorkspaceRoot } from "../types/ui";

afterEach(cleanup);

// ---------------------------------------------------------------------------
// (a) the Tools surface — real data only
// ---------------------------------------------------------------------------
const PROVIDERS: ProviderInfo[] = [
  { id: "anthropic", label: "Anthropic", connected: true },
  { id: "openai", label: "OpenAI", connected: false },
];
const LOCAL_ROLE: RoleOption[] = [
  { role: "local", label: "On this computer", configured: true, models: [{ id: "llama", label: "Balanced" }] },
];
const ROOTS: WorkspaceRoot[] = [{ directory: "/Users/someone/project", grantedAt: 1_700_000_000 }];

function renderTools(over: Partial<Parameters<typeof ToolsSurface>[0]> = {}) {
  const onStopTrusting = vi.fn();
  render(
    <ToolsSurface
      connected={true}
      providers={PROVIDERS}
      roles={[]}
      trustedRoots={[]}
      showTrustedFolders={false}
      onAddKey={vi.fn()}
      onStopTrusting={onStopTrusting}
      {...over}
    />,
  );
  return onStopTrusting;
}

describe("the Tools surface", () => {
  it("lists connected providers, and offers a key for the ones without", () => {
    renderTools();
    expect(screen.getByText("Anthropic")).toBeTruthy();
    expect(screen.getByText("connected")).toBeTruthy();
    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add a key for OpenAI" })).toBeTruthy();
  });

  it("invents nothing — no IDE, Files, Calendar, Email or Browser rows", () => {
    renderTools({ providers: [], roles: [] });
    const text = document.body.textContent ?? "";
    for (const fake of ["IDE", "Files", "Calendar", "Email", "Browser"]) {
      expect(text).not.toContain(fake);
    }
    // And it says so plainly rather than showing an unexplained blank.
    expect(
      screen.getByText("Nothing yet — Addison can only reach what you connect below."),
    ).toBeTruthy();
  });

  it("shows a ready local model as something Addison can reach", () => {
    renderTools({ roles: LOCAL_ROLE });
    expect(screen.getByText("Balanced")).toBeTruthy();
    expect(screen.getByText("on this computer")).toBeTruthy();
  });

  it("hides trusted folders unless the profile is Developer or Custom", () => {
    // Trust rows outlive a profile switch core-side, so Tools must apply the same
    // gate Settings does — otherwise it is a back door to a surface Simple hides.
    renderTools({ trustedRoots: ROOTS, showTrustedFolders: false });
    expect(screen.queryByText("/Users/someone/project")).toBeNull();
    cleanup();
    renderTools({ trustedRoots: ROOTS, showTrustedFolders: true });
    expect(screen.getByText("/Users/someone/project")).toBeTruthy();
  });

  it("revokes the folder it names", () => {
    const onStopTrusting = renderTools({ trustedRoots: ROOTS, showTrustedFolders: true });
    fireEvent.click(screen.getByRole("button", { name: "Stop trusting /Users/someone/project" }));
    expect(onStopTrusting).toHaveBeenCalledWith("/Users/someone/project");
  });
});

// ---------------------------------------------------------------------------
// (b) a pending consent card is visible on a surface
// ---------------------------------------------------------------------------
describe("a surface's pinned slot", () => {
  it("shows a pending permission card above the page's own title", () => {
    render(
      <Surface
        title="Settings"
        pinned={
          <PermissionCard
            request={{
              toolId: "t",
              label: "Addison would like to read a page",
              description: "It will fetch example.com.",
              riskTier: "low",
            }}
            onRespond={vi.fn()}
          />
        }
      >
        <SurfaceSection label="Somewhere">
          <SurfaceRow name="A row" />
        </SurfaceSection>
      </Surface>,
    );
    const card = screen.getByText("Addison would like to read a page");
    const title = screen.getByText("Settings");
    // Both answers are one press away, on the page the person is standing on.
    expect(screen.getByRole("button", { name: "Allow" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Not now" })).toBeTruthy();
    // DOCUMENT_POSITION_FOLLOWING: the title comes after the card.
    expect(card.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (c) the anchored model popup
// ---------------------------------------------------------------------------
const POPUP_OPTIONS = [
  { key: "a", label: "Most capable", note: "quality", selected: false, onPick: vi.fn() },
  { key: "b", label: "Balanced", note: "quality", selected: true, onPick: vi.fn() },
  { key: "c", label: "On this computer", note: "local", selected: false, onPick: vi.fn() },
];

describe("the model popup", () => {
  it("opens so the SELECTED row sits at the click point", () => {
    render(
      <ModelPopup anchor={{ x: 900, y: 400 }} options={POPUP_OPTIONS} onClose={vi.fn()} />,
    );
    const panel = screen.getByRole("listbox");
    // x = right − 250; y = centre − 14 − (selected index 1) × 29.
    expect(panel.style.left).toBe("650px");
    expect(panel.style.top).toBe("357px");
    expect(panel.style.width).toBe("270px");
  });

  it("never lets a corner of itself off the screen", () => {
    // A trigger near the top-left with a late selection would compute negative
    // coordinates; both are clamped to the 12px margin.
    render(
      <ModelPopup
        anchor={{ x: 100, y: 10 }}
        options={POPUP_OPTIONS.map((o, i) => ({ ...o, selected: i === 2 }))}
        onClose={vi.fn()}
      />,
    );
    const panel = screen.getByRole("listbox");
    expect(panel.style.left).toBe("12px");
    expect(panel.style.top).toBe("12px");
  });

  it("picks a model and closes on an outside click", () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(
      <ModelPopup
        anchor={{ x: 400, y: 300 }}
        options={[{ key: "a", label: "Most capable", note: "quality", selected: false, onPick }]}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole("option", { name: /Most capable/ }));
    expect(onPick).toHaveBeenCalledTimes(1);

    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// (d) the Restore points modal + the Snapshots surface
// ---------------------------------------------------------------------------
const ROW: Snapshot = {
  id: "s1",
  createdAt: Math.floor(Date.now() / 1000),
  trigger: "auto",
  reason: "turn_verified",
  reasonLabel: "Working setup",
  verifiedWorking: true,
  undeletable: false,
  capturesBinary: false,
};

function snapshotsState(over: Partial<SnapshotsState> = {}): SnapshotsState {
  return {
    snapshots: [ROW],
    snapshotsLoaded: true,
    lastWorkingId: "s1",
    lastWorkingLabel: "Working setup",
    lastWorkingProfileChange: undefined,
    warning: undefined,
    notice: null,
    restoredId: undefined,
    busy: false,
    refreshSnapshots: vi.fn(),
    handleCreateSnapshot: vi.fn(async () => {}),
    handleRestoreLastWorking: vi.fn(async () => {}),
    handleRestoreSnapshot: vi.fn(async () => {}),
    handleDeleteSnapshot: vi.fn(async () => {}),
    ...over,
  };
}

describe("the restore points modal", () => {
  it("carries the frozen footer note and the real save control", () => {
    const state = snapshotsState();
    render(<RestorePointsModal connected={true} snapshots={state} onClose={vi.fn()} />);
    expect(
      screen.getByText("everything can be undone · restores never delete your files"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "save one now" }));
    expect(state.handleCreateSnapshot).toHaveBeenCalledTimes(1);
  });

  it("closes on the ✕ and on the scrim, but never on a click inside", () => {
    const onClose = vi.fn();
    render(<RestorePointsModal connected={true} snapshots={snapshotsState()} onClose={onClose} />);

    // A click on a row must not be read as "dismiss".
    fireEvent.click(screen.getByText("Working setup"));
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Close restore points" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe("the Snapshots surface", () => {
  it("promises what the core actually does — before anything RISKY, not every change", () => {
    render(<SnapshotsSurface connected={true} snapshots={snapshotsState()} />);
    const text = document.body.textContent ?? "";
    expect(text).toContain("before anything risky");
    expect(text).not.toContain("before every change");
  });

  it("renders only the recency groups that have rows", () => {
    const now = new Date("2026-07-26T12:00:00Z");
    const at = (iso: string): Snapshot => ({
      ...ROW,
      id: iso,
      createdAt: Math.floor(new Date(iso).getTime() / 1000),
    });
    const groups = groupSnapshotsByRecency(
      [at("2026-07-26T09:00:00Z"), at("2026-05-01T09:00:00Z")],
      now,
    );
    expect(groups.map((g) => g.label)).toEqual(["Today", "Older"]);
  });

  it("keeps the permanent-row semantics of the modal, row for row", () => {
    const anchor: Snapshot = {
      ...ROW,
      id: "anchor",
      reasonLabel: "Before turning a guard off",
      undeletable: true,
    };
    render(
      <SnapshotsSurface
        connected={true}
        snapshots={snapshotsState({ snapshots: [anchor, ROW] })}
      />,
    );
    expect(screen.getByText("Permanent")).toBeTruthy();
    // The anchor offers a by-id restore and no Remove; the ordinary row the
    // reverse. Same rule as the modal, because it is literally the same rows.
    expect(screen.getAllByRole("button", { name: /^Restore this one/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /^Remove/ })).toHaveLength(1);
  });

  it("says so plainly when there is nothing saved yet", () => {
    render(
      <SnapshotsSurface
        connected={true}
        snapshots={snapshotsState({ snapshots: [], lastWorkingId: undefined })}
      />,
    );
    const section = screen.getByText("Restore points").parentElement as HTMLElement;
    expect(
      within(section).getByText(
        "None yet. Addison saves the first one as soon as it has something to remember.",
      ),
    ).toBeTruthy();
  });
});
