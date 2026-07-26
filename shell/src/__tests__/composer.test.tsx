// The composer — the one control every turn in the app goes through, and until
// this file the largest untested surface in the shell. A mutation pass applied
// 11 surgical mutations to Composer.tsx (Enter/Shift+Enter inverted, the draft
// left uncleared after a send, Send/Stop swapped, the Stop handler dropped, the
// growth cap raised to 1000px, "everything can be undone" flipped to "nothing
// can be undone") and ALL 11 survived the whole 238-test suite — nothing was
// watching any of it. Every test below was written against its own mutation and
// verified RED with that mutation applied.
//
// What is deliberately NOT asserted: measured layout. jsdom implements no
// layout, so `scrollHeight` is 0 and the auto-grow effect can only ever compute
// 0px. The growth cap is therefore checked as the STYLE the component sets
// (`max-height`), which is the thing a mutation would change, not as a height
// the browser worked out.
//
// The hook bundles are stubbed down to the fields Composer actually destructures
// (`turn` → isWorking/handleSend/handleStop; `models` → the ModelSelector props).
// With an empty catalog ModelSelector falls back to its inert placeholder list,
// which is what a disconnected design-review render shows — good enough here,
// because nothing in this file is about the model menu.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { Composer } from "../components/Composer";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { TurnState } from "../hooks/useTurn";

// globals:false → testing-library's automatic afterEach cleanup isn't registered.
afterEach(cleanup);

/** The exact microcopy under the composer. Frozen: it is Addison's central
 *  promise, permanently on screen, and a mutation to it is a lie the user
 *  reads on every turn. */
const MICROCOPY = "enter to send · everything can be undone";

const MODELS = {
  roles: [],
  cloudModels: [],
  selectedRole: "primary",
  selectedCloudModel: undefined,
  selectedLocalModel: undefined,
  selectedEffort: undefined,
  handleSelectModel: vi.fn(),
  handleSelectEffort: vi.fn(),
} as unknown as ModelSelection;

/** A turn bundle carrying only what Composer reads off it, plus direct handles
 *  on the two spies so a test can assert on them after the cast. */
function stubTurn(isWorking = false) {
  const handleSend = vi.fn();
  const handleStop = vi.fn();
  return {
    handleSend,
    handleStop,
    turn: { isWorking, handleSend, handleStop } as unknown as TurnState,
  };
}

function renderComposer(over: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const { turn, handleSend, handleStop } = stubTurn();
  const view = render(<Composer connected turn={turn} models={MODELS} {...over} />);
  return { ...view, handleSend, handleStop };
}

const textarea = () => screen.getByLabelText("Message to Addison") as HTMLTextAreaElement;
const sendButton = () => screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;

function type(text: string) {
  fireEvent.change(textarea(), { target: { value: text } });
}

describe("sending a message", () => {
  it("sends on Enter and adds a line on Shift+Enter", () => {
    // The primary interaction in the app, and the one the frozen microcopy
    // promises in writing ("enter to send"). With the condition inverted, Enter
    // silently does nothing and Shift+Enter fires the turn.
    const { handleSend } = renderComposer();
    type("  tidy my downloads  ");

    fireEvent.keyDown(textarea(), { key: "Enter", shiftKey: true });
    expect(handleSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea(), { key: "Enter" });
    // Trimmed: leading/trailing whitespace is never part of the message.
    expect(handleSend).toHaveBeenCalledWith("tidy my downloads");
    expect(handleSend).toHaveBeenCalledTimes(1);
  });

  it("clears the draft once it has been sent", () => {
    // A draft left in the box after a send means the next Enter re-sends the
    // previous message — the user's second question becomes their first one
    // again, and Addison answers something nobody asked twice.
    const { handleSend } = renderComposer();
    type("what did you change?");
    fireEvent.keyDown(textarea(), { key: "Enter" });

    expect(textarea().value).toBe("");
    fireEvent.keyDown(textarea(), { key: "Enter" });
    expect(handleSend).toHaveBeenCalledTimes(1);
  });

  it("refuses a blank or whitespace-only draft, by button and by Enter", () => {
    // An empty send costs a real API call and puts an empty user turn in the
    // transcript. Both routes into submit() have to refuse it.
    const { handleSend } = renderComposer();
    expect(sendButton().disabled).toBe(true);

    type("   \n  ");
    expect(sendButton().disabled).toBe(true);

    fireEvent.keyDown(textarea(), { key: "Enter" });
    expect(handleSend).not.toHaveBeenCalled();
  });
});

describe("while a turn is running", () => {
  it("shows Stop instead of Send, and Stop really stops", () => {
    // The v1 IPC contract has no core-side cancel, so this button is the only
    // way a person can end a turn that is taking too long. Swapped controls or
    // a dropped handler both leave them with no way out — and both ship green
    // without this test.
    const { turn, handleStop } = stubTurn(true);
    render(<Composer connected turn={turn} models={MODELS} />);

    expect(screen.queryByRole("button", { name: "Send" })).toBe(null);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(handleStop).toHaveBeenCalledTimes(1);
  });

  it("locks the textarea and says what Addison is doing", () => {
    // The disabled textarea is what stops a second message being typed into a
    // turn that cannot accept it; the placeholder is the only on-screen word
    // for why the box went quiet.
    const { turn } = stubTurn(true);
    render(<Composer connected turn={turn} models={MODELS} />);

    expect(textarea().disabled).toBe(true);
    expect(textarea().placeholder).toBe("Addison is working…");
  });

  it("refuses a submit even when a draft survived into the running turn", () => {
    // The disabled textarea is the visible guard; the `isWorking` check inside
    // submit() is the one that holds when a draft is already in the box as the
    // turn starts (a seeded draft, or a re-render landing mid-keystroke).
    // Without it a second turn is dispatched on top of the first.
    const { turn, handleSend, handleStop } = stubTurn(false);
    const { rerender } = render(<Composer connected turn={turn} models={MODELS} />);
    type("and also empty the trash");

    const running = { isWorking: true, handleSend, handleStop } as unknown as TurnState;
    rerender(<Composer connected turn={running} models={MODELS} />);

    fireEvent.keyDown(textarea(), { key: "Enter" });
    expect(handleSend).not.toHaveBeenCalled();
  });

  it("goes back to Send when the turn ends", () => {
    // The other half of the swap: an inverted condition passes the two
    // assertions above and fails here.
    const { turn } = stubTurn(false);
    render(<Composer connected turn={turn} models={MODELS} />);

    expect(screen.queryByRole("button", { name: "Stop" })).toBe(null);
    expect(sendButton().disabled).toBe(true); // no draft yet
    expect(textarea().disabled).toBe(false);
    expect(textarea().placeholder).toBe("Write to Addison…");
  });
});

describe("the promise under the composer", () => {
  it("reads exactly 'enter to send · everything can be undone'", () => {
    // Two claims in ten words, both load-bearing: how to send, and Addison's
    // central promise. Flipped to "nothing can be undone" it contradicts the
    // whole product, sits on screen permanently, and nothing else notices.
    renderComposer();
    expect(screen.getByText(MICROCOPY).textContent?.trim()).toBe(MICROCOPY);
  });
});

describe("a seeded draft", () => {
  it("lands in the box, is consumed once, and never sends itself", () => {
    // The seed carries rewind's edit-and-resend text and the empty-state
    // suggestion chips. Nothing may run until the person presses Send: a seed
    // that auto-sent would turn "edit this message" into "resend it unedited".
    const onDraftSeedUsed = vi.fn();
    const { handleSend } = renderComposer({
      draftSeed: "Plan the weekend",
      onDraftSeedUsed,
    });

    expect(textarea().value).toBe("Plan the weekend");
    // Consumed exactly once, or App re-seeds the box on every later render and
    // overwrites whatever the person has since typed.
    expect(onDraftSeedUsed).toHaveBeenCalledTimes(1);
    expect(handleSend).not.toHaveBeenCalled();
    // It is a real draft, so it is sendable — the seed is a prefill, not a
    // read-only preview.
    expect(sendButton().disabled).toBe(false);
  });
});

describe("the composer's own box", () => {
  it("caps the textarea's growth at the on-grid maximum", () => {
    // The cap keeps a long draft from swallowing the thread. jsdom has no
    // layout (scrollHeight is 0), so the assertion is on the style the
    // component sets rather than a measured height. The number is on the line
    // grid on purpose (9px + 2px pads + 7 × 22.5px lines): raise it and a
    // max-height draft ends on a sliced half-line.
    renderComposer();
    expect(textarea().style.maxHeight).toBe("168.5px");
  });

  it("marks focus with a 2px accent rule, not a hairline shade change", () => {
    // WCAG 2.4.11: the composer's textarea opts out of the global focus ring
    // (styles.css) on the grounds that the row's top rule indicates focus
    // instead. Measured, the old indicator was a 1px hairline going 1.14:1 →
    // 1.55:1 against the page in light mode — a 1.35:1 state change, i.e. no
    // visible indicator at all for the 54- and 68-year-old readers this app is
    // for. It is now 2px in `accent` (4.83:1 light / 9.25:1 dark against the
    // page; 4.22:1 / 7.06:1 against the idle rule).
    //
    // Tailwind classes are not compiled in jsdom, so this is a class contract,
    // not a computed style: it exists to stop the fix being quietly reverted to
    // a shade change.
    renderComposer();
    const row = textarea().parentElement as HTMLElement;
    expect(row.className).toContain("focus-within:border-t-2");
    expect(row.className).toContain("focus-within:border-accent");
  });
});
