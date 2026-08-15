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
import { useState } from "react";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { ToolsSurface } from "../components/ToolsSurface";
import { SnapshotsSurface, groupSnapshotsByRecency } from "../components/SnapshotsSurface";
import { RestorePointsModal } from "../components/RestorePointsModal";
import { ModelPopup } from "../components/ModelPopup";
import { Surface, SurfaceRow, SurfaceSection } from "../components/Surface";
import { PermissionCard } from "../components/PermissionCard";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { ProviderInfo } from "../ipc/client";
import type { McpServer, RoleOption, Snapshot, WorkspaceRoot } from "../types/ui";

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
const SERVER: McpServer = {
  id: "m1",
  name: "Notes",
  url: "https://notes.example",
  enabled: true,
  status: "ok",
  tools: [{ name: "search_notes", description: "Find a note." }],
};

function renderTools(over: Partial<Parameters<typeof ToolsSurface>[0]> = {}) {
  const onStopTrusting = vi.fn();
  render(
    <ToolsSurface
      connected={true}
      providers={PROVIDERS}
      roles={[]}
      trustedRoots={[]}
      showToolServers={false}
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

  it("lists trusted folders in EVERY profile, tool servers only on the Developer surface", () => {
    // FLIPPED 2026-08-12. This used to assert the opposite for folders ("hides
    // trusted folders unless the profile is Developer or Custom"), which was right
    // while Settings hid the granting panel from Simple. Simple has that panel now
    // and its file tools genuinely read inside these folders, so a Tools page that
    // left them out would understate Addison's reach on the one page whose job is
    // stating it. The Developer gate survives for tool servers, which Simple truly
    // cannot reach.
    renderTools({ trustedRoots: ROOTS, showToolServers: false, mcpServers: [SERVER] });
    expect(screen.getByText("/Users/someone/project")).toBeTruthy();
    expect(screen.queryByText("search_notes")).toBeNull();
    cleanup();
    renderTools({ trustedRoots: ROOTS, showToolServers: true, mcpServers: [SERVER] });
    expect(screen.getByText("/Users/someone/project")).toBeTruthy();
    expect(screen.getByText("search_notes")).toBeTruthy();
  });

  it("counts a tool server among the things it can reach", () => {
    // "Connected" is a claim about the whole page, and a Developer who had added
    // a server before any provider key read "Nothing yet" directly above that
    // server's own discovered tools.
    renderTools({ providers: [], showToolServers: true, mcpServers: [SERVER] });
    expect(
      screen.queryByText("Nothing yet — Addison can only reach what you connect below."),
    ).toBeNull();
    expect(screen.getByText("search_notes")).toBeTruthy();
  });

  it("revokes the folder it names", () => {
    const onStopTrusting = renderTools({ trustedRoots: ROOTS, showToolServers: true });
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
// (b2) the consent card itself — the question, the consequence, the command
// ---------------------------------------------------------------------------
//
// This is the control that authorises a destructive command, and CLAUDE.md's
// destructive-prompt rule makes the exact command text a REQUIREMENT of every
// destructive invocation: "the card carries the exact command text so the user
// knows precisely what they are approving each time". Nothing pinned the
// consequence sentence or the command chip, so dropping either rendered a card
// that asks for consent without saying what to. Both are the core's strings,
// rendered verbatim — the card never composes prose of its own.

const RUN_REQUEST = {
  toolId: "run_command",
  label: "Addison would like to run a command",
  description: "This changes files on your computer. It will run: rm -rf ./build",
  riskTier: "high" as const,
};

/** Fixed heights are the only measurable size in jsdom (no layout, and Tailwind
 * isn't compiled here), so the tap target is read off the class. */
function tapHeightPx(el: HTMLElement): number {
  const match = /(?:^|\s)h-\[(\d+(?:\.\d+)?)px\]/.exec(el.className);
  return match ? Number(match[1]) : 0;
}

describe("the consent card", () => {
  it("renders the core's consequence sentence, verbatim", () => {
    render(
      <PermissionCard
        request={{
          toolId: "read_web_page",
          label: "Addison would like to read a page",
          description: "It will fetch example.com.",
          riskTier: "low",
        }}
        onRespond={vi.fn()}
      />,
    );
    // Without the consequence the card asks for consent to an unnamed effect.
    expect(screen.getByText("It will fetch example.com.")).toBeTruthy();
  });

  it("shows the exact command on a destructive card, as a machine fact", () => {
    render(<PermissionCard request={RUN_REQUEST} onRespond={vi.fn()} />);
    // The lead keeps the core's wording up to and including "run:"; the command
    // itself is split off into the mono chip, and is never dropped or shortened.
    expect(screen.getByText("This changes files on your computer. It will run:")).toBeTruthy();
    const chip = screen.getByText("rm -rf ./build");
    expect(chip.className).toContain("font-mono");
    // Truncated on screen, so the full text has to survive somewhere reachable.
    expect(chip.getAttribute("title")).toBe("rm -rf ./build");
  });

  it("gives both answers a real target, with Allow the dominant one", () => {
    render(<PermissionCard request={RUN_REQUEST} onRespond={vi.fn()} />);
    const allow = screen.getByRole("button", { name: "Allow" });
    const notNow = screen.getByRole("button", { name: "Not now" });
    // WCAG 2.2 SC 2.5.8: 24×24 CSS px is the floor. These were 14.5px tall on
    // desktop — the mobile-only rescue (max-md:min-h-[44px]) never applied.
    expect(tapHeightPx(allow)).toBeGreaterThanOrEqual(24);
    expect(tapHeightPx(notNow)).toBeGreaterThanOrEqual(24);
    // …and the 44px mobile target survives.
    expect(allow.className).toContain("max-md:h-11");
    expect(notNow.className).toContain("max-md:h-11");
    // One obvious choice: Allow carries the accent FILL, "Not now" does not, so
    // the two are not distinguished by hue alone.
    expect(allow.className).toContain("bg-accent");
    expect(notNow.className).not.toContain("bg-accent");
  });

  it("still says exactly what the core said — no copy of its own", () => {
    const onRespond = vi.fn();
    render(<PermissionCard request={RUN_REQUEST} onRespond={onRespond} />);
    expect(screen.getByText(RUN_REQUEST.label)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    expect(onRespond).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(onRespond).toHaveBeenCalledWith(false);
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
    // The tree holds the rows; the panel around it holds the position, the
    // scrollport and the footer line.
    const panel = screen.getByRole("tree").parentElement as HTMLElement;
    // x = right − 250, still closed-form. The vertical placement is not: it is
    // the click point less the selected row's MEASURED centre inside the panel,
    // because headings and folded rows sit between the two. jsdom lays nothing
    // out, so every row measures as a zero-height sliver at the panel's top and
    // the panel lands on the click point exactly — the arithmetic itself is
    // pinned in modelPopupGroups.test.tsx, over synthetic measurements.
    expect(panel.style.left).toBe("650px");
    expect(panel.style.top).toBe("400px");
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
    const panel = screen.getByRole("tree").parentElement as HTMLElement;
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
    fireEvent.click(screen.getByRole("treeitem", { name: /Most capable/ }));
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
  it("carries the real save control", () => {
    const state = snapshotsState();
    render(<RestorePointsModal connected={true} snapshots={state} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "save one now" }));
    expect(state.handleCreateSnapshot).toHaveBeenCalledTimes(1);
  });

  // The footer's undo claim is MODE-SCOPED. "everything can be undone" is true
  // under SAFE — SAFE-2 makes a real undo() a registration requirement above LOW
  // — and false under OPEN, where `run_command` is that rule's one explicit
  // exemption and Settings already says "…some actions can't be undone" two
  // sections away. This test used to freeze the SAFE sentence for every mode,
  // which is what let the modal contradict the profile card.
  it("claims everything can be undone only in SAFE mode", () => {
    render(
      <RestorePointsModal
        connected={true}
        snapshots={snapshotsState()}
        mode="safe"
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByText("everything can be undone · restores never delete your files"),
    ).toBeTruthy();
  });

  it("stops claiming everything can be undone in OPEN mode", () => {
    render(
      <RestorePointsModal
        connected={true}
        snapshots={snapshotsState()}
        mode="open"
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByText("some actions can't be undone · restores never delete your files"),
    ).toBeTruthy();
    expect(document.body.textContent).not.toContain("everything can be undone");
  });

  it("treats an absent mode as SAFE, like ProfileState.mode does", () => {
    // An old core sends no mode at all, and a core with no OPEN mode is a core
    // where the SAFE claim holds.
    render(<RestorePointsModal connected={true} snapshots={snapshotsState()} onClose={vi.fn()} />);
    expect(
      screen.getByText("everything can be undone · restores never delete your files"),
    ).toBeTruthy();
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

// ---------------------------------------------------------------------------
// (e) the modal's focus, which `aria-modal="true"` makes load-bearing
// ---------------------------------------------------------------------------
//
// `aria-modal="true"` tells assistive tech that everything outside the dialog is
// gone. That is a PROMISE about where focus can be, and it was not kept: opening
// moved no focus at all, two Tabs walked out to the header behind the scrim (with
// nothing announced, because those stops are hidden), and closing dropped focus
// on the floor. On the one surface a person opens when something has already gone
// wrong. jsdom implements no sequential focus navigation, which is exactly why
// the component handles Tab itself rather than leaning on the browser — the wrap
// under test here is the wrap that ships.

/** The modal plus a real opener outside it, so the close path has somewhere to
 * hand focus back to. */
function ModalHarness() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        open restore points
      </button>
      {open && (
        <RestorePointsModal
          connected={true}
          snapshots={snapshotsState()}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

/** Open it the way a person does: the trigger has focus when it is pressed
 * (jsdom's click doesn't focus, a browser's does). */
function openModal() {
  render(<ModalHarness />);
  const trigger = screen.getByRole("button", { name: "open restore points" });
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
}

describe("the restore points modal's focus", () => {
  it("moves focus into the dialog when it opens", () => {
    openModal();
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("keeps Tab and Shift+Tab inside the dialog", () => {
    openModal();
    const dialog = screen.getByRole("dialog");
    const save = screen.getByRole("button", { name: "save one now" });
    const remove = screen.getByRole("button", { name: /^Remove/ });

    // First Tab enters at the near end rather than escaping to the page.
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Tab" });
    expect(document.activeElement).toBe(save);

    // Forward from the last stop wraps to the first, never out to the header.
    remove.focus();
    fireEvent.keyDown(remove, { key: "Tab" });
    expect(document.activeElement).toBe(save);
    expect(dialog.contains(document.activeElement)).toBe(true);

    // And backwards from the first wraps to the last.
    fireEvent.keyDown(save, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(remove);
  });

  it("gives focus back to whatever opened it", () => {
    const trigger = openModal();
    expect(document.activeElement).not.toBe(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Close restore points" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("gives focus back on the scrim path too", () => {
    const trigger = openModal();
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(document.activeElement).toBe(trigger);
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
    const section = screen.getByText("Saved so far").parentElement as HTMLElement;
    expect(
      within(section).getByText(
        "None yet. Addison saves the first one as soon as it has something to remember.",
      ),
    ).toBeTruthy();
  });
});
