// Turn lifecycle — the message thread, the running turn's working/activity
// state, the permission prompt, and Send/Retry/Stop. Extracted from App.tsx as
// a mechanical move: the logic — especially the currentTurnRef race guards that
// drop late results from stopped/superseded turns — is unchanged.

import { useEffect, useRef, useState } from "react";
import type { ModelRole, PermissionRequest, ActivityUpdate } from "../types/protocol";
import type { DisplayMessage } from "../types/ui";
import { ipc, parseAnsweredWith, type RawError } from "../ipc/client";
import { asRecord } from "../lib/parse";
import {
  createStreamScramble,
  isMotionEnabled,
  revealAdvanceFor,
  type StreamScramble,
} from "../lib/scramble";

interface UseTurnArgs {
  connected: boolean;
  setStatusBanner: (text: string | null) => void;
  /** The active role + picks, from useModelSelection. */
  selectedRole: ModelRole;
  selectedLocalModel?: string;
  selectedEffort?: string;
  effectiveLocalModel: (role: ModelRole, picked?: string) => string | undefined;
  effectiveCloudModel: () => string | undefined;
  /** From useWidgets: draft a widget after a turn that asked for one. */
  maybeProposeWidget: (userText: string) => void;
  /** From useOffers: draft an add-a-server or make-it-cheaper card after a turn
   * whose USER text asked for one. Same rule as the widget draft — only the
   * person's own words may arm a card, never the model's reply. */
  maybeProposeOffers: (userText: string) => void;
  /** From useConversations / useWidgets: post-turn refreshers. */
  refreshConversations: (adopt?: boolean) => void;
  refreshStats: () => void;
}

export function useTurn({
  connected,
  setStatusBanner,
  selectedRole,
  selectedLocalModel,
  selectedEffort,
  effectiveLocalModel,
  effectiveCloudModel,
  maybeProposeWidget,
  maybeProposeOffers,
  refreshConversations,
  refreshStats,
}: UseTurnArgs) {
  // An empty thread is empty. The redesign RETIRED the seeded "welcome" line:
  // an invitation is not something Addison already said, so an untouched chat
  // shows ChatThread's greeting stack instead (docs/design-brief-dark,
  // "Screens → Chat empty state").
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [isWorking, setIsWorking] = useState(false);
  const [permission, setPermissionState] = useState<PermissionRequest | null>(null);
  // The card outlived its turn (KNOWN-BUGS #4, owner decision 2026-08-09: THE CARD
  // DIES WITH ITS TURN). Stop leaves the card on screen — a question that was asked
  // does not un-ask itself, and blanking it would leave a person wondering what
  // Addison had wanted — but the card becomes inert: no Allow, no Not now, one
  // plain sentence saying it ended. Held beside `permission` rather than folded
  // into it because `PermissionRequest` is the CORE's shape and this is a fact
  // about this window's turn, which the core never sent.
  //
  // This flag is presentation. `conversation.stop` is the enforcement: the core
  // refuses a late `permission.respond` whatever this side renders.
  const [permissionExpired, setPermissionExpired] = useState(false);

  /**
   * Show a card — or take one away. The ONLY way `permission` is written, so the
   * expired flag can never outlive the card it described: a fresh card arriving
   * after a stopped one (the next turn's first ask) would otherwise render dead on
   * arrival, which is the same bug with the answers reversed.
   */
  function setPermission(next: PermissionRequest | null) {
    setPermissionExpired(false);
    setPermissionState(next);
  }

  const [currentActivity, setCurrentActivity] = useState<ActivityUpdate | null>(null);
  const [activities, setActivities] = useState<ActivityUpdate[]>([]);
  const [lastUserText, setLastUserText] = useState<string | null>(null);

  // --- Streamed text: the truth, and the decoration over it -----------------
  // `messages` always holds the TRUE streamed text — that is what is committed
  // to state, what a retry/rewind reads, and what lands in the store. The
  // scramble is a DISPLAY overlay held here: ChatThread renders `streamDisplay`
  // in place of the pending message's content while it is non-null, and the
  // moment the turn settles the overlay is dropped and the real content shows.
  // Scrambled glyphs must never be able to reach the message content — see
  // `__tests__/useTurn.test.tsx`, which pins exactly that.
  const [streamDisplay, setStreamDisplay] = useState<string | null>(null);
  // WHICH message the overlay decorates. The streaming path could key off the
  // `pending` flag, but the reveal below outlives it — the answer has landed,
  // so the message is settled while its text is still resolving — and an id is
  // the only thing that stays true across that moment.
  const [streamMessageId, setStreamMessageId] = useState<string | null>(null);
  const streamRef = useRef<StreamScramble | null>(null);
  const streamTextRef = useRef("");
  // True only while a finished answer is resolving. The turn's `finally` clears
  // the overlay as part of settling, which would kill a reveal on the frame it
  // started; this is what tells it to leave the reveal alone.
  const revealingRef = useRef(false);
  // Whether the engine is idle at the end of everything it has been handed. It
  // reports this itself (`onDone`), and the turn's `finally` needs the answer:
  // an engine still resolving text at settle time is animating text that has
  // ALREADY fully arrived, which is a reveal in everything but name and must be
  // allowed to finish. Nothing running counts as caught up.
  const caughtUpRef = useRef(true);

  function endStream() {
    streamRef.current?.stop();
    streamRef.current = null;
    streamTextRef.current = "";
    revealingRef.current = false;
    caughtUpRef.current = true;
    setStreamDisplay(null);
    setStreamMessageId(null);
  }

  /** Hand a settled message back to its normal (markdown) rendering. */
  function releaseOverlay() {
    revealingRef.current = false;
    streamTextRef.current = "";
    streamRef.current = null;
    setStreamDisplay(null);
    setStreamMessageId(null);
  }

  // Nothing else stops the animation when this hook goes away. The engine is a
  // plain 38ms interval holding a closure over this hook's setters, so a reveal
  // running when the webview swaps the chat out (or the app tears down) would
  // keep ticking and keep calling setState on a dead hook, forever. Refs only —
  // an unmount cleanup must not touch state.
  useEffect(() => {
    return () => {
      streamRef.current?.stop();
      streamRef.current = null;
      revealingRef.current = false;
    };
  }, []);

  /**
   * Reveal a FINISHED answer with the scramble, instead of having it appear in
   * one frame. The core doesn't stream, so without this every reply plops in
   * whole (owner request 2026-07-26); the prototype animates its canned reply
   * the same way.
   *
   * The overlay is display only — `messages` already holds the true text before
   * this runs, so a rewind, retry, copy or store read can never see a scrambled
   * glyph, and an interrupted reveal costs the reader nothing but the animation.
   */
  function revealFinalText(messageId: string, text: string) {
    if (!isMotionEnabled() || !text) return;
    streamRef.current?.stop();
    streamTextRef.current = text;
    revealingRef.current = true;
    setStreamMessageId(messageId);
    streamRef.current = createStreamScramble((frame) => setStreamDisplay(frame), {
      advanceChars: revealAdvanceFor(text.length),
      // Hand the message back to its normal rendering (markdown, links, code)
      // the instant the text has fully resolved. `releaseOverlay` also drops the
      // spent engine: `appendStreamedText` only builds one when `streamRef` is
      // empty, so an engine left sitting here is one a later chunk gets pushed
      // into — at the reveal's rate, from the reveal's own leading edge, with no
      // `streamMessageId` set. That path is now closed at the other end too (a
      // chunk with no live turn is dropped); this keeps "streamRef holds an
      // engine that can still be pushed to" true on its own rather than by the
      // other end's grace.
      onDone: () => {
        caughtUpRef.current = true;
        releaseOverlay();
      },
    });
    streamRef.current.push(text);
  }

  /**
   * A `conversation.streamChunk` delta arrived. Appends it to the pending
   * message (the true text) and, when motion is on, feeds the scramble engine
   * so the tail resolves out of the noise as it lands.
   */
  function appendStreamedText(text: string) {
    if (!text) return;
    // Which message this chunk belongs to. Keyed on the running turn, NOT on the
    // `pending` flag: the flag is cleared the moment the result lands while the
    // overlay lives on through the reveal, so a chunk arriving in that gap used
    // to be committed to nothing and displayed anyway — the reader watched a
    // sentence resolve that no message held, and it vanished when the overlay
    // dropped. No live turn means no message to append to, so the chunk is
    // dropped rather than shown: the display may lag the truth, never exceed it.
    const targetId = currentTurnRef.current;
    if (!targetId) return;
    setMessages((prev) =>
      prev.map((m) => (m.id === targetId ? { ...m, content: m.content + text } : m)),
    );
    if (!isMotionEnabled()) {
      // Reduced motion / motion off: no overlay at all, so the text just
      // appends. Cheaper than running an engine that emits the input unchanged.
      setStreamDisplay(null);
      return;
    }
    streamTextRef.current += text;
    caughtUpRef.current = false;
    if (!streamRef.current) {
      setStreamMessageId(targetId);
      // No `advanceChars`: the answer's full length is unknown while it is still
      // arriving, so the engine paces itself against the backlog instead.
      streamRef.current = createStreamScramble((frame) => setStreamDisplay(frame), {
        onDone: () => {
          caughtUpRef.current = true;
          // Catching up mid-turn is a PAUSE between deltas, not the end of the
          // answer: keep the engine and the overlay so the next chunk resumes
          // from the leading edge. Releasing here would drop the engine, and the
          // next chunk would build a fresh one and push the whole accumulated
          // prefix into it — re-animating the answer from character zero.
          //
          // Once the turn has settled there is no next chunk, so the engine has
          // just landed on the final text and the message can go back to its
          // normal rendering. `revealingRef` is what the turn's `finally` sets to
          // say so.
          if (revealingRef.current) releaseOverlay();
        },
      });
    }
    streamRef.current.push(streamTextRef.current);
  }
  // Identifies the turn whose IPC result may still touch shared turn state (the
  // assistant message, isWorking, the activity line). Stop and every new turn
  // reassign it, so a result arriving late from an abandoned turn — Stop ends the
  // turn's consent, not its work, so results keep landing (see handleStop) — is dropped
  // instead of resurrecting stopped text or re-enabling the composer mid-turn.
  const currentTurnRef = useRef<string | null>(null);

  // --- Turn lifecycle -------------------------------------------------------
  async function runTurn(text: string, opts: { isRetry?: boolean } = {}) {
    const assistantId = uid();
    const userId = uid();
    currentTurnRef.current = assistantId;
    setMessages((prev) => {
      const base = opts.isRetry
        ? dropTrailingAssistant(prev)
        : [...prev, { id: userId, role: "user", content: text } as DisplayMessage];
      return [...base, { id: assistantId, role: "assistant", content: "", pending: true }];
    });

    setLastUserText(text);
    setActivities([]);
    setCurrentActivity(null);
    setPermission(null);
    setIsWorking(true);
    endStream();

    try {
      // Deliver the *effective* model for the active role. For "local", fall
      // back to the first model when the dropdown was never touched (the picker
      // shows it as selected). For cloud, send the picked model + its effort
      // level; effort never applies to local models (§4.1.1 B).
      const isLocal = selectedRole === "local";
      const modelId = isLocal
        ? effectiveLocalModel("local", selectedLocalModel)
        : effectiveCloudModel();
      const effort = isLocal ? undefined : selectedEffort;
      const res = await ipc.sendMessage(text, selectedRole, modelId, effort);
      // Stopped or superseded by a newer turn while we were waiting — drop this
      // result so it can't overwrite "(Stopped.)" or a later turn's answer.
      if (currentTurnRef.current !== assistantId) return;
      const finalText = extractFinalText(res);
      // The core's persisted ids: what "Rewind to here" must anchor on.
      const ids = asRecord(res);
      const userStoreId = typeof ids?.userMessageId === "string" ? ids.userMessageId : undefined;
      const assistantStoreId =
        typeof ids?.assistantMessageId === "string" ? ids.assistantMessageId : undefined;
      // Which model actually answered (Phase-2 step 3). Fails closed to undefined,
      // so a malformed block simply shows no free-model chip.
      const answeredWith = parseAnsweredWith(res);
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id === assistantId) {
            return {
              ...m,
              pending: false,
              content: finalText ?? m.content,
              storeId: assistantStoreId,
              answeredWith,
            };
          }
          if (m.id === userId) {
            return { ...m, storeId: userStoreId };
          }
          return m;
        }),
      );
      // The reply arrived with no `streamChunk` at all, so the text would appear
      // in a single frame. Reveal it with the scramble instead.
      //
      // In production this branch does not run: the answer reaches the webview as
      // `conversation.streamChunk` notifications and the RPC result carries only
      // ids, so `streamTextRef` is already full and `finalText` is null. The
      // scramble for that case is the engine `appendStreamedText` started, which
      // the `finally` below now lets finish. This stays as the fallback for a
      // reply that does carry its text in the result.
      if (!streamTextRef.current && finalText) {
        revealFinalText(assistantId, finalText);
      }
      // Composer path: if the user's own words asked for a widget, a model server,
      // or a cheaper setup, draft the matching card from the just-finished turn.
      // Nothing is saved or applied until they press the card's button.
      //
      // Isolated on purpose. The answer is already on screen at this point, so a
      // drafter that throws must not fall into the catch below — `content ||
      // message` would keep the text but stamp the turn `failed: true`, telling
      // the person their answer went wrong when it did not. A card that failed to
      // draft is a card that doesn't appear, which is the intended quiet failure.
      try {
        maybeProposeWidget(text);
        maybeProposeOffers(text);
      } catch {
        /* no card — never a failed turn */
      }
    } catch (err) {
      // Same guard on the failure path: an abandoned turn's error must not
      // replace the stopped message or a newer turn's content.
      if (currentTurnRef.current !== assistantId) return;
      const message = err instanceof Error ? err.message : "Something went wrong.";
      // Developer-only: the client attaches the real exception text as `.raw`.
      // We keep it on the message; ChatThread renders it only when the
      // raw-diagnostics flag is on, so the plain message is all Simple ever sees.
      const raw = (err as RawError | undefined)?.raw;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                pending: false,
                failed: true,
                // The core and the IPC client both send complete plain-language
                // sentences with a next step — render them as-is, no re-wrapping.
                content: m.content || message,
                raw: typeof raw === "string" ? raw : undefined,
              }
            : m,
        ),
      );
    } finally {
      // Only the still-current turn clears the working/activity state; an
      // abandoned turn's cleanup would otherwise re-enable the composer and hide
      // the activity line while a newer turn is still running.
      if (currentTurnRef.current === assistantId) {
        currentTurnRef.current = null;
        setIsWorking(false);
        setCurrentActivity(null);
        // The answer is settled: drop the display overlay so the message shows
        // its real content (which the result may just have replaced wholesale)
        // — UNLESS that content is currently being revealed, which is an
        // animation over text that has already landed. It ends itself.
        //
        // An engine still mid-resolve at this moment is exactly that case, and
        // this is where every real turn lands. The core's last `streamChunk`
        // arrives immediately before it returns the result, so the engine is
        // pushed to and the turn settles within the same handful of milliseconds
        // — and killing it here meant one 15-character frame of noise and then
        // the entire answer, which is why replies appeared to arrive whole (owner
        // report 2026-07-26). Nothing further is coming for this turn, so the
        // engine is finishing a reveal: promote it to one and let its `onDone`
        // release the overlay.
        //
        // `caughtUpRef` has to be TRUE-when-idle for this to be safe, and the
        // engine is the only thing that can say so: it reports every landing, not
        // just its first. When it reported only the first, an answer whose stream
        // paused and resumed left this flag stuck at false, promoted a finished
        // engine to a reveal, and the overlay never came down — the answer sat in
        // plain pre-wrap text with the cursor blinking after it (owner screenshot
        // 2026-08-06; `scramble.ts`, the re-arm in `push`).
        if (!caughtUpRef.current && streamRef.current) revealingRef.current = true;
        if (!revealingRef.current) endStream();
        // A turn just landed: refresh the sidebar so a new chat's auto-title
        // appears, and adopt the launch conversation as current if we didn't
        // know its id yet. Usage changed too, so refresh the token meter.
        refreshConversations(true);
        refreshStats();
      }
    }
  }

  function handleSend(text: string) {
    if (!connected) {
      setStatusBanner("Addison's engine isn't connected yet, so I can't reply.");
      return;
    }
    void runTurn(text);
  }

  function handleRetry() {
    if (!connected || isWorking || !lastUserText) return;
    void runTurn(lastUserText, { isRetry: true });
  }

  function handleStop() {
    // Stop halts the webview turn: it stops accepting streamed text and re-enables
    // the input. Abandon the turn so its still-in-flight result can't land later
    // and overwrite the "(Stopped.)" message (the core finishes the step it is on
    // — there is still no mid-step interrupt).
    //
    // THE CARD DIES WITH ITS TURN (KNOWN-BUGS #4). Two things, and they are not
    // the same thing:
    //   * `conversation.stop` tells the CORE, which refuses every pending card and
    //     will not raise another for this turn. That is the enforcement, and it
    //     holds whatever this window renders;
    //   * the expired flag greys the card here, so nobody presses Allow on a
    //     question that can no longer be answered. That is presentation.
    // The card is deliberately NOT removed: it says what Addison was asking, and
    // now says that the asking ended.
    if (permission) setPermissionExpired(true);
    ipc.stopTurn().catch(() => {
      /* The card is inert either way; nothing to retry and nothing to say. */
    });
    currentTurnRef.current = null;
    setIsWorking(false);
    setCurrentActivity(null);
    // Stop shows what actually arrived, not a half-scrambled tail of it.
    endStream();
    setMessages((prev) =>
      prev.map((m) =>
        m.pending
          ? { ...m, pending: false, content: m.content || "(Stopped.)" }
          : m,
      ),
    );
  }

  // The turn-scoped half of App's resetTransientState (clearing a switched-away
  // conversation's in-flight state); App adds its own transient bits on top.
  function resetTurn() {
    currentTurnRef.current = null;
    setIsWorking(false);
    setActivities([]);
    setCurrentActivity(null);
    setPermission(null);
    setLastUserText(null);
    endStream();
  }

  return {
    messages,
    setMessages,
    isWorking,
    permission,
    permissionExpired,
    setPermission,
    activities,
    setActivities,
    currentActivity,
    setCurrentActivity,
    lastUserText,
    streamDisplay,
    streamMessageId,
    appendStreamedText,
    handleSend,
    handleRetry,
    handleStop,
    resetTurn,
  };
}

export type TurnState = ReturnType<typeof useTurn>;

// ---------------------------------------------------------------------------
// Small pure helpers (moved with the turn logic from App.tsx).
// ---------------------------------------------------------------------------
function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `m-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function dropTrailingAssistant(list: DisplayMessage[]): DisplayMessage[] {
  const copy = [...list];
  while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
  return copy;
}

function extractFinalText(result: unknown): string | null {
  const obj = asRecord(result);
  if (!obj) return typeof result === "string" ? result : null;
  if (typeof obj.text === "string") return obj.text;
  if (typeof obj.content === "string") return obj.content;
  const msg = asRecord(obj.message);
  if (msg && typeof msg.content === "string") return msg.content;
  return null;
}
