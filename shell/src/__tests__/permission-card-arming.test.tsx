// The arming card — the keyword gate's face (Phase-2 step 8, phase 3 of four).
//
// This is the ONE surface in the app where a person hands a command to the
// operating system to run on its own schedule, when Addison is closed and outside
// Addison's sandbox. docs/step-8-automation-plan.md §3 says what that makes this
// card: the code's friction exists to make somebody READ the preview they are
// copying from, and "the preview is the defence" against the one attack a code
// cannot stop — a person who types it for a job they never understood. So the
// preview must carry the WHOLE truth, and the tests below are mostly about that.
//
// Four things they hold:
//
//   (a) The preview renders complete — name, the core's schedule sentence, the
//       EXACT command whole and untruncated, where the file goes, and the core's
//       warning sentences verbatim. Anything dropped here is consent to something
//       unread.
//   (b) The webview never decides whether the code matched. A wrong code still
//       submits, and what was typed goes to the core VERBATIM. A client-side check
//       would be a second source of truth for a security decision — and would teach
//       the next reader that this side is trusted here, which it is not.
//   (c) Nothing makes the code one click away: no copy button, no autofill, no
//       prefilled box. The friction IS the feature.
//   (d) The ORDINARY consent card is untouched. A card that has no `arming` renders
//       exactly what it rendered before this phase and answers with `allow` alone.
//
// Copy authored on THIS side (the field names, the code label, the attempts lines,
// the Arm button) is pinned byte-for-byte below, the way automations.test.tsx pins
// phase 2's. Copy the CORE owns (the label, the warnings, the schedule sentence)
// is pinned as "renders what arrived, unchanged".

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { PermissionCard } from "../components/PermissionCard";
import { parseArming } from "../ipc/client";
import type { PermissionRequest } from "../types/protocol";

afterEach(cleanup);

// --- Frozen copy — byte-for-byte, this side's own -----------------------------
const FIELD_NAME = "Automation";
const FIELD_WHEN = "When it runs";
const FIELD_COMMAND = "What it runs";
const FIELD_WHERE = "Where it's saved";
const CODE_LABEL = "Type this code to arm it";
const CODE_NO_HELP = "Addison can't type this for you.";
const ARM_BUTTON = "Arm it";
const ATTEMPTS_ONE = "One more try — after that you'll need to ask Addison again.";
const ATTEMPTS_TWO = "2 more tries — after that you'll need to ask Addison again.";

// --- Frozen copy the CORE owns (plan §3's two sentences, which "must survive every
// redesign"). Rendered verbatim; this side never rewrites, merges or softens one. --
const WARNING_SCHEDULE = "This will run on its own schedule even when Addison is closed.";
const WARNING_SANDBOX = "It runs outside Addison's sandbox.";

const NONCE = "ACD-EFG";
const COMMAND = "/usr/bin/find /Users/mira/Downloads -mtime +30 -delete && /usr/bin/say done";
const INSTALL_PATH = "~/Library/LaunchAgents/com.addison.auto.tidy-downloads.plist";

function armingRequest(over: Partial<NonNullable<PermissionRequest["arming"]>> = {}) {
  return {
    toolId: "arm_automation",
    label: "Addison would like to arm an automation",
    description: "Your computer will run this on a schedule.",
    riskTier: "high" as const,
    arming: {
      nonce: NONCE,
      automationName: "Tidy up downloads",
      scheduleSentence: "Every Monday at 7:30",
      command: COMMAND,
      installPath: INSTALL_PATH,
      warnings: [WARNING_SCHEDULE, WARNING_SANDBOX],
      attemptsLeft: 3,
      ...over,
    },
  };
}

/** The code box, by its visible label — which is also its accessible name. */
function codeBox(): HTMLInputElement {
  return screen.getByLabelText(CODE_LABEL) as HTMLInputElement;
}

function armButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: ARM_BUTTON }) as HTMLButtonElement;
}

// ---------------------------------------------------------------------------
// (a) the preview — the whole truth, not a summary
// ---------------------------------------------------------------------------
describe("the arming card's preview", () => {
  it("shows the name, the schedule, the command, the path and both of the core's warnings", () => {
    render(<PermissionCard request={armingRequest()} onRespond={vi.fn()} />);
    // The question the core asked, still the first thing on the card.
    expect(screen.getByText("Addison would like to arm an automation")).toBeTruthy();
    // Each fact under a name of its own, in the order somebody asks them.
    expect(screen.getByText(FIELD_NAME)).toBeTruthy();
    expect(screen.getByText("Tidy up downloads")).toBeTruthy();
    expect(screen.getByText(FIELD_WHEN)).toBeTruthy();
    expect(screen.getByText("Every Monday at 7:30")).toBeTruthy();
    expect(screen.getByText(FIELD_COMMAND)).toBeTruthy();
    expect(screen.getByText(COMMAND)).toBeTruthy();
    expect(screen.getByText(FIELD_WHERE)).toBeTruthy();
    expect(screen.getByText(INSTALL_PATH)).toBeTruthy();
    // The core's sentences, verbatim and one line each — these two are what make
    // arming different from every other card in the app.
    expect(screen.getByText(WARNING_SCHEDULE)).toBeTruthy();
    expect(screen.getByText(WARNING_SANDBOX)).toBeTruthy();
  });

  it("reads top-down in the order a person asks: name, when, what, where, then the code", () => {
    render(<PermissionCard request={armingRequest()} onRespond={vi.fn()} />);
    const order = [
      screen.getByText("Tidy up downloads"),
      screen.getByText("Every Monday at 7:30"),
      screen.getByText(COMMAND),
      screen.getByText(INSTALL_PATH),
      screen.getByText(WARNING_SCHEDULE),
      screen.getByText(NONCE),
    ];
    for (let i = 0; i < order.length - 1; i += 1) {
      // DOCUMENT_POSITION_FOLLOWING: each element comes after the one before it.
      expect(
        order[i].compareDocumentPosition(order[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("never truncates the command it is asking somebody to read", () => {
    // The ordinary card truncates its command chip and keeps the whole string in a
    // `title`. That is wrong HERE, at the one moment the whole point is the reading:
    // a tooltip is not reachable by keyboard or screen reader, and a shortened
    // command defeats the ceremony exactly where it is supposed to work.
    render(<PermissionCard request={armingRequest()} onRespond={vi.fn()} />);
    const chip = screen.getByText(COMMAND);
    expect(chip.className).not.toContain("truncate");
    expect(chip.className).toContain("font-mono");
    expect(chip.textContent).toBe(COMMAND);
  });

  it("renders a command as text, never as markup", () => {
    // A command is text a model wrote at the authoring door. React escapes children;
    // this is the test that fails the day somebody reaches for dangerouslySetInnerHTML
    // or routes this through the markdown renderer.
    const hostile = "<img src=x onerror=alert(1)> && echo **bold**";
    render(<PermissionCard request={armingRequest({ command: hostile })} onRespond={vi.fn()} />);
    expect(screen.getByText(hostile)).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("strong")).toBeNull();
  });

  it("renders the core's warnings verbatim, whatever they say", () => {
    // The core owns this copy. If it changes a sentence, the card says the new
    // sentence — it has no list of its own to fall back to and no wording to prefer.
    render(
      <PermissionCard
        request={armingRequest({ warnings: ["Only this.", "And this."] })}
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByText("Only this.")).toBeTruthy();
    expect(screen.getByText("And this.")).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain(WARNING_SANDBOX);
  });
});

// ---------------------------------------------------------------------------
// (b) the code round-trip — and the comparison this side never makes
// ---------------------------------------------------------------------------
describe("the arming card's code box", () => {
  it("shows the code and offers a box to retype it into", () => {
    render(<PermissionCard request={armingRequest()} onRespond={vi.fn()} />);
    expect(screen.getByText(NONCE)).toBeTruthy();
    const box = codeBox();
    expect(box.tagName).toBe("INPUT");
    // Empty, always. A prefilled box is the ceremony answering itself.
    expect(box.value).toBe("");
  });

  it("sends what was typed, verbatim, with the approval", () => {
    const onRespond = vi.fn();
    render(<PermissionCard request={armingRequest()} onRespond={onRespond} />);
    // Deliberately not the shown form: lowercase, spaced instead of hyphenated. The
    // CORE normalises and compares (hmac.compare_digest after normalisation); a
    // second normaliser here is a place where the two could one day disagree.
    fireEvent.change(codeBox(), { target: { value: "acd efg" } });
    fireEvent.click(armButton());
    expect(onRespond).toHaveBeenCalledWith(true, "acd efg");
  });

  it("does not decide whether the code matched — a wrong code still goes to the core", () => {
    // THE WHOLE OF (b). If this card ever compares, the comparison becomes a second
    // source of truth for a security decision, and a reader learns that this side is
    // trusted here. It is not: the core mints the code and the core compares it.
    const onRespond = vi.fn();
    render(<PermissionCard request={armingRequest()} onRespond={onRespond} />);
    fireEvent.change(codeBox(), { target: { value: "WRONG-99" } });
    // Not disabled, not warned about, not swallowed.
    expect(armButton().disabled).toBe(false);
    fireEvent.click(armButton());
    expect(onRespond).toHaveBeenCalledWith(true, "WRONG-99");
    expect(document.body.textContent ?? "").not.toMatch(/doesn.t match|incorrect|wrong code/i);
  });

  it("asks for something before it will submit, and nothing more", () => {
    // Emptiness is an affordance, not a judgement: with nothing typed there is
    // nothing to send, so the button waits. One character is enough to enable it —
    // the card has no opinion about WHICH character.
    const onRespond = vi.fn();
    render(<PermissionCard request={armingRequest()} onRespond={onRespond} />);
    expect(armButton().disabled).toBe(true);
    fireEvent.click(armButton());
    expect(onRespond).not.toHaveBeenCalled();
    fireEvent.change(codeBox(), { target: { value: "x" } });
    expect(armButton().disabled).toBe(false);
  });

  it("refusing needs no code, and sends none", () => {
    // "Not now" must always be the easiest thing on this card.
    const onRespond = vi.fn();
    render(<PermissionCard request={armingRequest()} onRespond={onRespond} />);
    const notNow = screen.getByRole("button", { name: "Not now" }) as HTMLButtonElement;
    expect(notNow.disabled).toBe(false);
    fireEvent.click(notNow);
    expect(onRespond).toHaveBeenCalledWith(false);
    expect(onRespond.mock.calls[0]).toEqual([false]);
  });

  it("answers on Enter, the same press that sends a message everywhere else", () => {
    const onRespond = vi.fn();
    const { container } = render(
      <PermissionCard request={armingRequest()} onRespond={onRespond} />,
    );
    fireEvent.change(codeBox(), { target: { value: "ACD-EFG" } });
    fireEvent.submit(container.querySelector("form") as HTMLFormElement);
    expect(onRespond).toHaveBeenCalledWith(true, "ACD-EFG");
  });
});

// ---------------------------------------------------------------------------
// (c) the friction is the feature
// ---------------------------------------------------------------------------
describe("the arming card's friction", () => {
  it("never offers to copy the code or fill it in", () => {
    render(<PermissionCard request={armingRequest()} onRespond={vi.fn()} />);
    // Two controls on this card, and they are both answers. No "copy", no "paste
    // it for me", no "use this code" — plan §3: retyping six characters is exactly
    // the friction that makes somebody read the preview above it.
    const buttons = screen.getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual([ARM_BUTTON, "Not now"]);
    expect(screen.queryByRole("button", { name: /copy|fill|paste|use this code/i })).toBeNull();
    // Not focused on arrival either: focus in the box invites typing before reading.
    expect(document.activeElement).not.toBe(codeBox());
    // And the box is never seeded with the answer by any route.
    expect(codeBox().value).toBe("");
    expect(
      [...document.querySelectorAll("input")].some((i) => (i as HTMLInputElement).value === NONCE),
    ).toBe(false);
  });

  it("says why it will not help, in one plain sentence", () => {
    render(<PermissionCard request={armingRequest()} onRespond={vi.fn()} />);
    expect(screen.getByText(CODE_NO_HELP)).toBeTruthy();
  });

  it("says how many tries are left once one has been used", () => {
    render(<PermissionCard request={armingRequest({ attemptsLeft: 2 })} onRespond={vi.fn()} />);
    expect(screen.getByText(ATTEMPTS_TWO)).toBeTruthy();
    cleanup();
    render(<PermissionCard request={armingRequest({ attemptsLeft: 1 })} onRespond={vi.fn()} />);
    expect(screen.getByText(ATTEMPTS_ONE)).toBeTruthy();
  });

  it("says nothing about tries on a first ask", () => {
    // A counter on a card nobody has answered yet reads as a threat, and there is
    // nothing to count. (3 is the core's MAX_ATTEMPTS.)
    render(<PermissionCard request={armingRequest({ attemptsLeft: 3 })} onRespond={vi.fn()} />);
    expect(document.body.textContent ?? "").not.toMatch(/more tr(y|ies)/i);
  });
});

// ---------------------------------------------------------------------------
// (d) the ordinary card, unchanged
// ---------------------------------------------------------------------------
const ORDINARY: PermissionRequest = {
  toolId: "run_command",
  label: "Addison would like to run a command",
  description: "This changes files on your computer. It will run: rm -rf ./build",
  riskTier: "high",
};

describe("the ordinary consent card", () => {
  it("is untouched by the arming variant: same two answers, same allow-alone reply", () => {
    const onRespond = vi.fn();
    render(<PermissionCard request={ORDINARY} onRespond={onRespond} />);
    expect(screen.getByText("This changes files on your computer. It will run:")).toBeTruthy();
    const chip = screen.getByText("rm -rf ./build");
    expect(chip.className).toContain("truncate");
    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    // ALLOW ALONE. Nothing about arming rides an ordinary answer, so the core's
    // existing round-trip sees exactly the payload it always saw.
    expect(onRespond.mock.calls[0]).toEqual([true]);
  });

  it("has no code box and no arming copy", () => {
    render(<PermissionCard request={ORDINARY} onRespond={vi.fn()} />);
    expect(screen.queryByLabelText(CODE_LABEL)).toBeNull();
    expect(document.querySelector("input")).toBeNull();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain(CODE_NO_HELP);
    expect(text).not.toContain(FIELD_COMMAND);
  });
});

// ---------------------------------------------------------------------------
// (e) the parser — which way this one fails
// ---------------------------------------------------------------------------
describe("parseArming", () => {
  it("keeps the ceremony when a fact is missing, because there is still a code", () => {
    // Fail-closed here means keeping the CEREMONY, not dropping it: an arming
    // payload missing a fact is still an arming request, and rendering it as a plain
    // Allow card would put a one-press approval on the one action in the app that
    // must never have one.
    expect(parseArming({ nonce: "ACD-EFG", command: "echo hi" })).toEqual({
      nonce: "ACD-EFG",
      automationName: "",
      // The core's own "nothing saved" line — never a schedule invented here.
      scheduleSentence: "No schedule saved yet.",
      command: "echo hi",
      installPath: "",
      warnings: [],
      // A first ask, so the card says nothing about tries.
      attemptsLeft: 3,
    });
  });

  it("drops it when there is no code, because there is nothing to type", () => {
    // And then the core refuses the answer for want of a match, which is the only
    // place that decision is ever made.
    for (const junk of [
      null,
      undefined,
      42,
      "ACD-EFG",
      {},
      { nonce: "" },
      { nonce: "   " },
      { nonce: 123456 },
    ]) {
      expect(parseArming(junk)).toBeUndefined();
    }
  });

  it("renders only the warnings that are sentences, and never adds one", () => {
    const parsed = parseArming({ nonce: "ACD-EFG", warnings: ["Real.", "", 7, null, ["x"]] });
    expect(parsed?.warnings).toEqual(["Real."]);
    expect(parseArming({ nonce: "ACD-EFG" })?.warnings).toEqual([]);
  });

  it("treats an unusable attempt count as a first ask", () => {
    for (const junk of [undefined, "two", -1, 1.5, NaN]) {
      expect(parseArming({ nonce: "ACD-EFG", attemptsLeft: junk })?.attemptsLeft).toBe(3);
    }
    expect(parseArming({ nonce: "ACD-EFG", attemptsLeft: 1 })?.attemptsLeft).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// (f) the wire — `typed` reaches the core, and only from an arming answer
// ---------------------------------------------------------------------------
// The card hands what was typed to its `onRespond`; this is the other half, where
// that string becomes a `permission.respond` frame. Worth its own test because the
// failure is invisible from both ends: a card that collects the code perfectly and
// an ipc layer that drops it look exactly like a person who mistyped.

async function respondFrame(
  args: [toolId: string, allow: boolean, typed?: string],
): Promise<Record<string, unknown>> {
  const sent: Record<string, unknown>[] = [];
  vi.doMock("@tauri-apps/api/core", () => ({
    invoke: vi.fn(async (_cmd: string, payload: { frame: Record<string, unknown> }) => {
      sent.push(payload.frame);
    }),
  }));
  vi.doMock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => {}) }));
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  vi.resetModules();
  try {
    const { ipc } = await import("../ipc/client");
    // The promise never settles (nothing answers the frame); the frame is the point.
    void ipc.respondToPermission(...args);
    // `call` awaits its event listeners before it invokes, so let the microtasks run.
    await new Promise((r) => setTimeout(r, 0));
    return sent[0];
  } finally {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    vi.doUnmock("@tauri-apps/api/core");
    vi.doUnmock("@tauri-apps/api/event");
    vi.resetModules();
  }
}

describe("permission.respond", () => {
  it("carries the typed code, verbatim, on an arming answer", async () => {
    const frame = await respondFrame(["arm_automation", true, " acd efg "]);
    expect(frame.method).toBe("permission.respond");
    // Untrimmed, uncased, unnormalised: the core normalises and compares, and a
    // second normaliser on this side is a place where the two could disagree.
    expect(frame.params).toEqual({ toolId: "arm_automation", allow: true, typed: " acd efg " });
  });

  it("sends exactly what it always sent when there is no code", async () => {
    const frame = await respondFrame(["run_command", true]);
    // No `typed` KEY at all — not the key holding `undefined`. `toStrictEqual`,
    // because an ordinary card's answer must be the payload the core has always
    // received and not a new shape that happens to serialise the same today.
    expect(frame.params).toStrictEqual({ toolId: "run_command", allow: true });
    expect("typed" in (frame.params as object)).toBe(false);
  });
});
