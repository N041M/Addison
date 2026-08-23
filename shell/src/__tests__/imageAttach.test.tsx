// Attaching a picture, from the two surfaces a person actually touches: the
// composer that holds the pending pictures, and the thread that shows them once
// they are sent (image-attach plan §6, phase 4).
//
// What this file is for. Phases 1–3 built the block path, the shell's decode and
// the wire, and every one of them is tested from Python — but ALL of it is
// unreachable without the ＋, and none of those tests can see a chip, a thumbnail,
// or an id going out on a send. The four things that can only go wrong here:
//
//   * the ids. A send names ids and nothing else; a chip that survives its own
//     send would offer a spent one, and a chip that vanishes on a REFUSED send
//     costs somebody four photographs they must now find again.
//   * the wire. `attachments` is omitted, not empty, when there are none — an
//     ordinary send's params must stay byte-for-byte what they were before this
//     feature existed, because that is the frame every other test in the tree was
//     written against.
//   * the warning. It is a warning and never a gate (spec §4.1.1 item A): it
//     appears only where the answer is KNOWN to be no, and it disables nothing.
//   * the pictures themselves. `data:` URIs, because the pinned CSP refuses
//     `blob:` and object URLs by name (tests/test_csp_is_pinned.py).
//
// Each test below was written against its own mutation; the mapping is on each
// one. Mermaid is stubbed for the thread half (it drags an async renderer into
// jsdom and nothing here is about diagrams).

import { describe, it, expect, vi, afterEach, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { Composer } from "../components/Composer";
import { ChatThread, resetThreadStaggerForTests } from "../components/ChatThread";
import { ipc } from "../ipc/client";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { TurnState } from "../hooks/useTurn";
import type { DisplayMessage } from "../types/ui";

vi.mock("../components/MermaidDiagram", () => ({ MermaidDiagram: () => null }));

// The real client, with the two new picker calls stubbed. `sendMessage` is left
// REAL on purpose: the frame test below is about the params it builds, and a stub
// would only prove the stub.
vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    ipc: {
      ...actual.ipc,
      pickAttachment: vi.fn(),
      discardAttachment: vi.fn(async () => ({ ok: true })),
    },
  };
});

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => undefined) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => {}) }));

const pickAttachment = ipc.pickAttachment as unknown as ReturnType<typeof vi.fn>;
const discardAttachment = ipc.discardAttachment as unknown as ReturnType<typeof vi.fn>;
const invoked = invoke as unknown as ReturnType<typeof vi.fn>;

/** One picture as `conversation.pickAttachment` hands it back: an id to name at
 *  send time, and the bytes for the thumbnail. 43,008 bytes is exactly 42 KB. */
const PICK = {
  attachmentId: "att-1",
  name: "receipt.png",
  mediaType: "image/png",
  byteSize: 43008,
  dataB64: "iVBORw0KGgo=",
};

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  pickAttachment.mockReset();
  pickAttachment.mockResolvedValue(PICK);
  discardAttachment.mockClear();
  invoked.mockClear();
  resetThreadStaggerForTests();
});

afterEach(() => {
  cleanup();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});

// ---------------------------------------------------------------------------
// The composer.
// ---------------------------------------------------------------------------

function modelsWith(over: Partial<ModelSelection> = {}): ModelSelection {
  return {
    roles: [],
    cloudModels: [],
    selectedRole: "primary",
    selectedCloudModel: undefined,
    selectedLocalModel: undefined,
    selectedEffort: undefined,
    handleSelectModel: vi.fn(),
    handleSelectEffort: vi.fn(),
    ...over,
  } as unknown as ModelSelection;
}

function renderComposer(
  over: Partial<React.ComponentProps<typeof Composer>> = {},
  sendResult: boolean = true,
) {
  const handleSend = vi.fn(async () => sendResult);
  const handleStop = vi.fn();
  const setStatusBanner = vi.fn();
  const turn = { isWorking: false, handleSend, handleStop } as unknown as TurnState;
  const view = render(
    <Composer
      connected
      turn={turn}
      models={modelsWith()}
      setStatusBanner={setStatusBanner}
      {...over}
    />,
  );
  return { ...view, handleSend, handleStop, setStatusBanner };
}

const attachButton = () => screen.getByRole("button", { name: "Attach a picture" });
const sendButton = () => screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
const textarea = () => screen.getByLabelText("Message to Addison") as HTMLTextAreaElement;

async function attachOne() {
  fireEvent.click(attachButton());
  await waitFor(() => expect(screen.getByText(PICK.name)).toBeTruthy());
}

describe("picking a picture", () => {
  it("opens the core's picker and shows what came back as a chip", async () => {
    // MUTATION: drop the `setAttachments` push in `attach()` — the picker opens,
    // the person watches nothing happen, and the send that follows carries no
    // picture. Nothing else in the suite notices.
    renderComposer();
    await attachOne();

    expect(pickAttachment).toHaveBeenCalledTimes(1);
    // Name and size in the strip's machine-fact voice, and the picture itself as a
    // `data:` URI — never `blob:`, which the pinned CSP refuses by name.
    expect(screen.getByText("42 KB")).toBeTruthy();
    const thumb = document.querySelector("[data-attachment-chips] img") as HTMLImageElement;
    expect(thumb.getAttribute("src")).toBe(`data:image/png;base64,${PICK.dataB64}`);
  });

  it("shows the core's own sentence when a pick is refused", async () => {
    // MUTATION: swallow the error in `attach()`'s catch. A person who closed the
    // picker, chose a file that will not decode, or asked for a fifth picture is
    // then told nothing at all — and the sentence explaining which of those
    // happened is the only thing the core sent back.
    pickAttachment.mockRejectedValueOnce(new Error("That's already four pictures."));
    const { setStatusBanner } = renderComposer();

    fireEvent.click(attachButton());
    await waitFor(() => expect(setStatusBanner).toHaveBeenCalledWith("That's already four pictures."));
    expect(document.querySelector("[data-attachment-chips]")).toBe(null);
  });

  it("frees the slot in the core when the ✕ takes a chip off", async () => {
    // MUTATION: drop the `discardInCore` call in `removeAttachment`. The chip goes
    // and the core goes on holding the bytes — four invisible slots later, the ＋
    // refuses a picture for a reason nothing on screen can explain.
    renderComposer();
    await attachOne();

    fireEvent.click(screen.getByRole("button", { name: `Remove ${PICK.name}` }));

    expect(discardAttachment).toHaveBeenCalledWith(PICK.attachmentId);
    expect(screen.queryByText(PICK.name)).toBe(null);
  });

  it("drops the pending pictures when a new chat clears the composer", async () => {
    // MUTATION: ignore `clearAttachmentsSignal`. A picture picked for one chat then
    // rides the first message of the next one — the `composerSeed` bug, with the
    // person's own photograph in it.
    const { rerender, handleSend, handleStop } = renderComposer({ clearAttachmentsSignal: 0 });
    await attachOne();

    const turn = { isWorking: false, handleSend, handleStop } as unknown as TurnState;
    rerender(
      <Composer connected turn={turn} models={modelsWith()} clearAttachmentsSignal={1} />,
    );

    await waitFor(() => expect(screen.queryByText(PICK.name)).toBe(null));
    // Freed, not merely hidden: `conversation.load` does not clear the core's
    // pending set, so the frontend has to say so.
    expect(discardAttachment).toHaveBeenCalledWith(PICK.attachmentId);
  });
});

describe("sending a picture", () => {
  it("hands the ids to the turn and lets the chips go", async () => {
    // MUTATION: pass `undefined` instead of the picks, or leave the chips in place.
    // The first sends a message whose pictures are still sitting in the composer;
    // the second offers ids the next send would be refused for naming.
    const { handleSend } = renderComposer();
    await attachOne();
    fireEvent.change(textarea(), { target: { value: "what does this say?" } });

    fireEvent.click(sendButton());

    expect(handleSend).toHaveBeenCalledWith("what does this say?", [PICK]);
    await waitFor(() => expect(screen.queryByText(PICK.name)).toBe(null));
  });

  it("allows a picture with no words at all", async () => {
    // MUTATION: leave `canSend` on the draft alone. A person sending just a photo —
    // the ordinary case the core relaxed its empty-text guard for (plan §5) — finds
    // Send greyed out and no way to send what they attached.
    const { handleSend } = renderComposer();
    expect(sendButton().disabled).toBe(true);

    await attachOne();

    expect(sendButton().disabled).toBe(false);
    fireEvent.click(sendButton());
    expect(handleSend).toHaveBeenCalledWith("", [PICK]);
  });

  it("keeps the chips when the send was refused", async () => {
    // MUTATION: clear the chips unconditionally (drop the `ok !== false` restore).
    // Phase 3 spends an id at the point of no return and at no refusal above it, so
    // a refused send's pictures are still held by the core — throwing the chips
    // away makes the person find every one of them again to fix a model choice.
    const { handleSend } = renderComposer({}, false);
    await attachOne();

    fireEvent.click(sendButton());
    expect(handleSend).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByText(PICK.name)).toBeTruthy());
  });

  it("carries no `attachments` key at all on an ordinary send", async () => {
    // MUTATION: always include the key (`attachments: attachments ?? []`). This is
    // the wire, not the component: an ordinary message's params must stay
    // byte-for-byte what they were before this feature existed. The real
    // `ipc.sendMessage` builds the frame; only Tauri's `invoke` is stubbed.
    void ipc.sendMessage("hello", "primary", "model-1", undefined);
    await waitFor(() => expect(invoked).toHaveBeenCalled());

    const frame = (invoked.mock.calls[0][1] as { frame: { params: Record<string, unknown> } })
      .frame;
    expect(frame.params).toEqual({
      text: "hello",
      role: "primary",
      modelId: "model-1",
      effort: undefined,
    });
    expect("attachments" in frame.params).toBe(false);
  });

  it("names the ids, and only the ids, when there are pictures", async () => {
    // MUTATION: send the bytes (`attachments: picks`) rather than the ids. The
    // webview is the lowest-trust process; what it holds must never be able to
    // become what the model saw.
    void ipc.sendMessage("look", "primary", "model-1", undefined, ["att-1", "att-2"]);
    await waitFor(() => expect(invoked).toHaveBeenCalled());

    const frame = (invoked.mock.calls[0][1] as { frame: { params: Record<string, unknown> } })
      .frame;
    expect(frame.params.attachments).toEqual(["att-1", "att-2"]);
  });
});

describe("the model that can't look at pictures", () => {
  const BLIND = modelsWith({
    selectedCloudModel: "m-blind",
    cloudModels: [{ id: "m-blind", label: "Text Only 1", vision: false }],
  } as unknown as Partial<ModelSelection>);

  const WARNING = "This model can't look at pictures.";

  it("says so once a picture is pending, and disables nothing", async () => {
    // MUTATION: drop the `vision === false` branch. The person attaches a photo to
    // a model that cannot see it and learns so only from the core's refusal, after
    // the message is in their transcript.
    const { handleSend } = renderComposer({ models: BLIND });
    expect(screen.queryByText(WARNING)).toBe(null);

    await attachOne();

    // Not accent — the accent on this strip belongs to Send, and this is neither an
    // action nor live state.
    expect((screen.getByText(WARNING) as HTMLElement).className).not.toContain("accent");
    // A WARNING, never a gate: the send still goes, and the core's refusal is the
    // enforcement (spec §4.1.1 item A).
    expect(sendButton().disabled).toBe(false);
    fireEvent.click(sendButton());
    expect(handleSend).toHaveBeenCalledWith("", [PICK]);
  });

  it("says nothing when the answer is merely unknown", async () => {
    // MUTATION: treat a missing flag as false (`!row?.vision`). A local model
    // carries no flag at all and a routed turn has no pick to read — warning on
    // either teaches people to ignore the line that is right.
    renderComposer({
      models: modelsWith({
        selectedCloudModel: "m-unknown",
        cloudModels: [{ id: "m-unknown", label: "Something New" }],
      } as unknown as Partial<ModelSelection>),
    });
    await attachOne();
    expect(screen.queryByText(WARNING)).toBe(null);
  });
});

// ---------------------------------------------------------------------------
// The thread.
// ---------------------------------------------------------------------------

function renderThread(messages: DisplayMessage[]) {
  return render(
    <ChatThread
      messages={messages}
      onRetry={() => {}}
      onContinue={() => {}}
      retryAvailable={false}
      onRewindTo={() => {}}
    />,
  );
}

const WITH_PICTURE: DisplayMessage = {
  id: "u1",
  role: "user",
  content: "what does this say?",
  attachments: [
    { id: "att-1", name: "receipt.png", mediaType: "image/png", dataB64: "iVBORw0KGgo=" },
  ],
} as DisplayMessage;

describe("a message that carries pictures", () => {
  it("draws the picture as a data: URI above the words, named beneath", () => {
    // MUTATION: render the block after the text, or drop the `data:` prefix. The
    // second is the one that matters: an object URL would be refused by the pinned
    // CSP and the person's own picture would be a broken frame in their transcript.
    renderThread([WITH_PICTURE]);

    const img = document.querySelector("[data-message-pictures] img") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("data:image/png;base64,iVBORw0KGgo=");
    expect(img.getAttribute("alt")).toBe("receipt.png");
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(screen.getByText("receipt.png")).toBeTruthy();
    // Above the text, which is the order the message was written in.
    const block = document.querySelector("[data-message-pictures]") as HTMLElement;
    const text = document.querySelector("[data-msg-text]") as HTMLElement;
    expect(block.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("leaves no empty paragraph under a picture sent without words", () => {
    // MUTATION: render the paragraph unconditionally. A picture-only message —
    // ordinary, and exactly what the core relaxed its empty-text guard for — then
    // carries a blank line the reader has to account for.
    renderThread([{ ...WITH_PICTURE, content: "" } as DisplayMessage]);

    expect(document.querySelector("[data-message-pictures] img")).not.toBe(null);
    expect(document.querySelector("[data-msg-text]")).toBe(null);
  });

  it("renders a message with no pictures exactly as it always did", () => {
    // MUTATION: render the picture block for every message (drop the length check).
    // The regression guard: every message in the app that has no attachment must be
    // the row it was before this feature existed.
    renderThread([{ id: "u2", role: "user", content: "tidy my downloads" } as DisplayMessage]);

    expect(document.querySelector("[data-message-pictures]")).toBe(null);
    const text = document.querySelector("[data-msg-text]") as HTMLElement;
    expect(text.textContent).toBe("tidy my downloads");
    expect((text.parentElement as HTMLElement).className).toContain("whitespace-pre-wrap");
  });
});
