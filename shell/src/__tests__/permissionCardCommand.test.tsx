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
    // The fixture is the longest command that ever legitimately arrives. If this
    // ever stops being true the artifact was regenerated from a different call, and
    // the test below stops testing truncation at all.
    expect(command).toHaveLength(MAX_PERMISSION_DETAIL_CHARS);

    const { container } = render(<PermissionCard request={RUN_CARD} onRespond={vi.fn()} />);
    // As TEXT, not as an attribute: `getByText` matches on rendered text content.
    expect(screen.getByText(command)).toBeTruthy();
    expect(container.textContent).toContain(command);
    // And the tail specifically — the half a one-line ellipsis eats.
    expect(container.textContent).toContain(command.slice(-40));
  });

  it("carries no `truncate` anywhere on the card, and no title holding the command", () => {
    const command = RUN_CARD.command!;
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
    // reader is looking at when they press Allow. Checked over every element that
    // has one, and there should be none at all on this card.
    const titled = Array.from(container.querySelectorAll<HTMLElement>("[title]"));
    expect(titled).toHaveLength(0);
    for (const el of titled) {
      expect(el.getAttribute("title")).not.toContain(command);
    }
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

describe("pc-03 · the expired card", () => {
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
