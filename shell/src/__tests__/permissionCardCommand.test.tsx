// THE COMMAND ON A CONSENT CARD — H9, 2026-09-04.
//
// This card is the control that authorises a destructive command, and until today
// it showed one the reader could not actually read. Three defects, one cause:
//
//   1. The command rendered in a `truncate` class — one line, ellipsis — with the
//      whole of it only in a `title=` tooltip. The core caps a detail at
//      MAX_PERMISSION_DETAIL_CHARS (120) PRECISELY so all of it can be shown, and
//      hover is not consent: it is unreachable from a keyboard and a screen reader,
//      and `git status --short && rm -rf ~/Documents/…` read as
//      `git status --short && rm -r…` is a different command from the one approved.
//   2. The command was recovered by `description.indexOf("run: ")` — the FIRST
//      occurrence anywhere — so an ordinary SAFE sentence containing those two
//      words ("This routine will run: it needs your calendar to do that.") drew
//      English in the mono block whose whole visual grammar means "this is the
//      exact command".
//   3. Structurally: the webview re-parsed an English sentence the core composes in
//      main.py. Two hardcoded strings in two languages with nothing connecting
//      them — reword the core and the mono block silently disappeared, with zero
//      test failures.
//
// The fix is a STRUCTURED FIELD: `permission.requestGrant` carries `command`, and
// this side renders it whole and reads no prefix out of any sentence, ever again.
//
// THE PAYLOADS HERE ARE THE REAL ONES.
// shell/src/__tests__/fixtures/permission.requestGrant.json is generated from the
// core's own card builder (agent_core/main.py `build_permission_card`) by
// tests/ipc_fixtures.py, and tests/test_ipc_fixture_drift.py fails if the core
// stops producing that shape. So this suite cannot prove the frontend renders
// something the core does not send.
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { normalizePermission } from "../App";
import { PermissionCard } from "../components/PermissionCard";
import type { PermissionRequest } from "../types/protocol";

import runCardFixture from "./fixtures/permission.requestGrant.json";
import proseCardFixture from "./fixtures/permission.requestGrant.prose.json";

// globals:false → testing-library's automatic afterEach cleanup isn't registered.
afterEach(cleanup);

const RUN_CARD: PermissionRequest = runCardFixture as PermissionRequest;
const PROSE_CARD: PermissionRequest = proseCardFixture as PermissionRequest;

/** The core's own cap. The fixture command is exactly this long — that is what the
 * Python side asserts when it generates the file — so "the whole thing is on
 * screen" is a claim with a number behind it rather than a look at the output. */
const MAX_PERMISSION_DETAIL_CHARS = 120;

describe("pc-01 · the command renders WHOLE", () => {
  it("puts all 120 characters in the DOM as text", () => {
    const command = RUN_CARD.command!;
    // The fixture carries a full-length command the tool did NOT have to cut —
    // MAX_PERMISSION_DETAIL_CHARS exactly. Not quite the longest string that can
    // arrive: one the tool cut is a character longer (120 plus the ellipsis it
    // added), which the Python side pins. What this length is for is being long
    // enough that a card which truncates would visibly truncate it, so if it ever
    // stops holding, the artifact was regenerated from a different call and the
    // assertions below stop testing truncation at all.
    expect(command).toHaveLength(MAX_PERMISSION_DETAIL_CHARS);

    const { container } = render(<PermissionCard request={RUN_CARD} onRespond={vi.fn()} />);
    // As TEXT, not as an attribute: `getByText` matches on rendered text content.
    expect(screen.getByText(command)).toBeTruthy();
    expect(container.textContent).toContain(command);
    // And the tail specifically — the half a one-line ellipsis eats.
    expect(container.textContent).toContain(command.slice(-40));
  });

  it("carries no `truncate` anywhere on the card, and no title at all", () => {
    const { container } = render(<PermissionCard request={RUN_CARD} onRespond={vi.fn()} />);

    // Nothing on this card clips text. Asserted over the whole card rather than the
    // command block alone: the class moving to a wrapper would truncate it just the
    // same, and would leave a block-level assertion green.
    expect(container.querySelectorAll(".truncate")).toHaveLength(0);
    for (const el of Array.from(container.querySelectorAll<HTMLElement>("*"))) {
      expect(el.className).not.toContain("truncate");
    }

    // No tooltip stands in for the text. A `title` is not consent — it is not
    // reachable from a keyboard or a screen reader, and it is not the thing the
    // reader is looking at when they press Allow. NONE at all on this card, which
    // is stricter than "none holding the command" and is the state to hold.
    expect(container.querySelectorAll("[title]")).toHaveLength(0);
  });

  it("wraps instead, and keeps a multi-line command's own line breaks", () => {
    const block = renderCommandBlock(RUN_CARD);
    expect(block.className).toContain("whitespace-pre-wrap");
    expect(block.className).toContain("break-words");
    expect(block.className).toContain("font-mono");

    // A command with a newline in it hides its tail for exactly the reason a long
    // one does, so the block preserves the break rather than collapsing it.
    const multiline: PermissionRequest = {
      ...RUN_CARD,
      command: "cd ~/Projects/site \\\n  && rm -rf dist \\\n  && npm run build",
    };
    expect(renderCommandBlock(multiline).textContent).toBe(multiline.command);
  });

  it("renders the core's lead sentence and never composes one of its own", () => {
    render(<PermissionCard request={RUN_CARD} onRespond={vi.fn()} />);
    expect(screen.getByText(RUN_CARD.description)).toBeTruthy();
    expect(screen.getByText(RUN_CARD.label)).toBeTruthy();
    // The command is NOT in the sentence — the core sends two values, and this side
    // shows them as two things.
    expect(RUN_CARD.description).not.toContain(RUN_CARD.command!);
  });
});

describe("pc-02 · prose that merely contains \"run: \" is never drawn as a command", () => {
  it("renders no machine-fact block for a sentence-shaped card", () => {
    // The real core payload for a tool that words its own consequence line. There
    // IS a detail behind it; there is no `command`, because the shape decides.
    expect(PROSE_CARD.description).toContain("run: ");
    expect(PROSE_CARD.command).toBeUndefined();

    const { container } = render(<PermissionCard request={PROSE_CARD} onRespond={vi.fn()} />);
    expect(container.querySelector("[data-consent-command]")).toBeNull();
    expect(container.querySelector(".font-mono")).toBeNull();
    // The sentence survives WHOLE, in the prose ink — nothing was sliced off it.
    expect(screen.getByText(PROSE_CARD.description)).toBeTruthy();
  });

  it("still shows no command block when the sentence is the file tools' one", () => {
    const fileCard: PermissionRequest = {
      toolId: "write_project_file",
      label: "Addison would like to change a file",
      description: "It wants to change the file “shopping.txt”. You can undo this afterwards.",
      riskTier: "medium",
    };
    const { container } = render(<PermissionCard request={fileCard} onRespond={vi.fn()} />);
    expect(container.querySelector("[data-consent-command]")).toBeNull();
  });

  it("shows the delete preview as prose, below the command and outside it", () => {
    const withPreview: PermissionRequest = {
      ...RUN_CARD,
      preview: "About to delete 1,240 files in 12 folders.",
    };
    const { container } = render(<PermissionCard request={withPreview} onRespond={vi.fn()} />);
    const block = container.querySelector("[data-consent-command]")!;
    // Prose ABOUT the command is never inside the block that means "this is the
    // exact command" — the reason it is a field of its own.
    expect(block.textContent).toBe(RUN_CARD.command);
    const previewNode = screen.getByText(withPreview.preview!);
    expect(previewNode.className).not.toContain("font-mono");
    expect(
      block.compareDocumentPosition(previewNode) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

// The plan's ids stop above. Its `pc-03` is "a core reword silently disables the
// chip", which has no case of its own any more: nothing on this side reads the
// core's sentence at all, and pc-01's "renders the core's lead sentence and never
// composes one of its own" is what holds it. What follows is the dead card, which is
// the same field seen after Stop.
describe("the expired card", () => {
  it("shows the command muted, whole, and with nothing to press", () => {
    render(<PermissionCard request={RUN_CARD} onRespond={vi.fn()} expired />);
    const block = document.querySelector("[data-consent-command]")!;
    expect(block.textContent).toBe(RUN_CARD.command);
    expect(block.className).toContain("text-muted");
    expect(block.className).not.toContain("truncate");
    expect(block.getAttribute("title")).toBeNull();
    // A dead card is the record of what was asked, not a thing that can be answered.
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps the delete preview too, as prose below the command", () => {
    // Same reason the command survives Stop: what was nearly approved is exactly
    // the thing worth being able to read afterwards, and how much it would have
    // taken is half of that. It had been dropped from this card alone.
    const withPreview: PermissionRequest = {
      ...RUN_CARD,
      preview: "About to delete 1,240 files in 12 folders.",
    };
    const { container } = render(
      <PermissionCard request={withPreview} onRespond={vi.fn()} expired />,
    );
    const block = container.querySelector("[data-consent-command]")!;
    const previewNode = screen.getByText(withPreview.preview!);
    // Prose, in the dead card's ink — never inside the block that means "this is
    // the exact command", and never styled as one.
    expect(previewNode.className).toContain("text-muted");
    expect(previewNode.className).not.toContain("font-mono");
    expect(
      block.compareDocumentPosition(previewNode) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("the expired arming card", () => {
  // THE REGRESSION THIS CARD IS THE WHOLE REASON FOR. `arm_automation`'s per-call
  // detail is the automation's NAME, so while the core attached a card-level
  // `command` to every detail-bearing card, a stopped arming card read "This time it
  // wants to run: Tidy up downloads" — a name in the block whose visual grammar
  // means "this is the exact command", which is the exact lie the field exists to
  // prevent. The core now sends no card-level command on an arming card; this
  // fixture carries one ANYWAY, because this side must read truthfully whatever
  // arrives.
  const AUTOMATION_NAME = "Tidy up downloads";
  const ARMED_COMMAND = "/usr/bin/find /Users/mira/Downloads -mtime +30 -delete";
  const EXPIRED_ARMING: PermissionRequest = {
    toolId: "arm_automation",
    label: "Addison would like to switch on an automation",
    description: "This time it wants to run:",
    riskTier: "high",
    command: AUTOMATION_NAME,
    arming: {
      nonce: "ACD-EFG",
      automationName: AUTOMATION_NAME,
      scheduleSentence: "Every Monday at 7:30",
      command: ARMED_COMMAND,
      installPath: "~/Library/LaunchAgents/com.addison.auto.tidy-downloads.plist",
      warnings: ["This will run on its own schedule even when Addison is closed."],
      attemptsLeft: 3,
    },
  };

  it("draws the command the OS would have run, never the automation's name", () => {
    const { container } = render(
      <PermissionCard request={EXPIRED_ARMING} onRespond={vi.fn()} expired />,
    );
    const blocks = container.querySelectorAll("[data-consent-command]");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].textContent).toBe(ARMED_COMMAND);
    expect(blocks[0].className).toContain("text-muted");
    // The name is on the dead card as PROSE — which automation this was is worth
    // reading back — and never inside the block. Mutation: render
    // `request.description` for an arming card, and the name vanishes.
    expect(blocks[0].textContent).not.toContain(AUTOMATION_NAME);
    expect(container.textContent).toContain(`${AUTOMATION_NAME} — Every Monday at 7:30`);
    expect(container.textContent).not.toContain("This time it wants to run:");
  });

  it("is dead: no buttons and no code box", () => {
    // The keyword card's live half is its code box, and a ceremony nothing can
    // accept is worse than no ceremony at all.
    render(<PermissionCard request={EXPIRED_ARMING} onRespond={vi.fn()} expired />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});

describe("the wire → the card", () => {
  it("carries `command` across the process boundary", () => {
    // Everything else can be right and the reader still sees consent to an unnamed
    // command if the field is dropped as the frame is parsed. This is the ONE path
    // from `permission.requestGrant` (and from the `permission.pending` re-sync) to
    // the component.
    expect(normalizePermission(runCardFixture)).toEqual(RUN_CARD);
    expect(normalizePermission(proseCardFixture)).toEqual(PROSE_CARD);
  });

  it("leaves the key off entirely when the core sends none", () => {
    expect(
      normalizePermission({ toolId: "t", label: "L", description: "D", riskTier: "low" }),
    ).toEqual({ toolId: "t", label: "L", description: "D", riskTier: "low" });
  });

  it("refuses a command that is not a usable string", () => {
    // Same defensive footing as every other parser here: core payloads are parsed,
    // never trusted to be well-formed.
    for (const junk of [null, 42, {}, [], ""]) {
      const parsed = normalizePermission({
        toolId: "t",
        label: "L",
        description: "D",
        riskTier: "low",
        command: junk,
      });
      expect(parsed.command).toBeUndefined();
    }
  });

  it("carries the delete preview too, which had been dropped here", () => {
    // Found while wiring `command` through the same function: the sentence the core
    // walks the filesystem to compute (5.6) reached no card at all, because this
    // normaliser never copied it.
    const parsed = normalizePermission({
      ...runCardFixture,
      preview: "About to delete 4 files in 1 folder.",
    });
    expect(parsed.preview).toBe("About to delete 4 files in 1 folder.");
  });
});

/** The one command block on a rendered card. Fails loudly rather than returning
 * null, so a test that stops finding it says so instead of asserting on nothing. */
function renderCommandBlock(request: PermissionRequest): HTMLElement {
  cleanup();
  const { container } = render(<PermissionCard request={request} onRespond={vi.fn()} />);
  const block = container.querySelector<HTMLElement>("[data-consent-command]");
  expect(block).toBeTruthy();
  return block!;
}
