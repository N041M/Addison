// Composer — the message row at the foot of the chat column (DARK direction;
// docs/design-brief-dark, "Screens → Composer").
//
// No card, no border box: a borderless 15px textarea over a single 1px top rule
// in `track`. To its right sit the two controls the person actually reaches for
// — the model label (mono, opens the per-message menu) and a 30px circle that is
// Send, and becomes the real Stop while a turn runs. One line of mono microcopy
// sits under it.
//
// THAT TOP RULE IS THE COMPOSER'S FOCUS INDICATOR, and it is the only one: the
// textarea opts out of the global focus ring in styles.css, because a 2px
// rectangle around text that has no box of its own is a border the design does
// not have. The brief specifies the focused rule as `track` → `track-hi`; that
// was measured at 1.14:1 idle → 1.55:1 focused against the page in light mode
// (dark: 1.31 → 1.75) — a 1px hairline with a 1.35:1 state change, which fails
// WCAG 2.4.11 and, for readers of 54 and 68, is not an indicator at all. So
// focus paints the rule 2px in `accent` instead (4.83:1 light / 9.25:1 dark
// against the page; 4.22:1 / 7.06:1 against the idle rule). The extra pixel of
// border is taken back out of the padding on the same selector, so nothing in
// the row moves when it lights up. Accessibility floor over brief fidelity —
// the carve-out in styles.css claims it "removes a duplicate indicator, never
// the only one", and this is what makes that sentence true.
//
// It carries NO horizontal padding of its own: `main` supplies the 40px gutter
// (16px below md), and doubling it was the misalignment phase 1 flagged — the
// composer row and the thread above it must share one left edge.
//
// THE ANSWERING MODEL IS DISCLOSED IN THAT STRIP, left of the picker (owner
// directive 2026-08-21). The complaint it answers: a turn can be answered by a
// model the person did not pick — routing degrades, a strategy chooses — and
// until now nothing on screen said so, which made the picker read as broken.
// The line states the fact and nothing else ("Answered by <label>"); it does not
// editorialise, does not claim a fault, and is NOT accent-coloured, because the
// accent belongs to actions, selection and live state, never to a disclosure.
//
// It is DERIVED FROM THE THREAD, never stashed: the newest assistant message
// that carries `answeredWith` (contract D5, `ipc/client.parseAnsweredWith`).
// That is the whole staleness fix — `answeredWith` rides on a send REPLY, so it
// exists only for turns answered in this session, and the messages array is
// replaced wholesale when a conversation is opened or a new chat starts. Loaded
// history carries no such fact, so the line simply is not there. A separate
// piece of state would have to be remembered to be cleared, and the one bug this
// feature must not have is a previous chat's model still on screen.
//
// The Stop control is not in the prototype and is not decoration: it is what lets
// a person end a turn that is taking too long (useTurn.handleStop). The v1 IPC
// contract still has no way to interrupt the core MID-STEP — the tool call it
// started finishes — so what Stop ends is the webview's turn and the turn's
// CONSENT: `conversation.stop` refuses every pending permission card and forbids
// another (KNOWN-BUGS #4). It keeps its aria-label.

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { TurnState } from "../hooks/useTurn";
import type { DisplayMessage } from "../types/ui";
import { ModelSelector } from "./ModelSelector";

interface Props {
  connected: boolean;
  /** The turn-lifecycle bundle (useTurn): isWorking + Send/Stop handlers. */
  turn: TurnState;
  /** The model-picker bundle (useModelSelection) for the model label + menu. */
  models: ModelSelection;
  /** One-shot prefill (rewind's edit-and-resend, a suggestion chip, a widget
   * idea); nothing runs until Send. */
  draftSeed?: string | null;
  onDraftSeedUsed?: () => void;
  /** Bump to focus the textarea without prefilling (first-run "say hello" nudge). */
  focusSignal?: number;
}

/** The textarea grows to this and then scrolls. Not the prototype's 180: the
 * cap sits ON the line grid (9px + 2px pads + 7 × 22.5px lines), so a
 * max-height draft ends on a whole line instead of a mid-line slice (user
 * report, 2026-07-26). */
const MAX_TEXTAREA_PX = 168.5;

/**
 * The label of the model that answered most recently in THIS conversation, or
 * null when nothing on screen carries the fact (a fresh chat, loaded history, or
 * a session in which every turn failed before an answer landed — the core clears
 * `answeredWith` before a run and never attaches it on the error path).
 *
 * Walks back over the thread rather than reading the last message: the newest
 * turn may be a user message, a still-pending answer or a failed one, and none of
 * those un-say which model gave the last real answer. A blank label is treated as
 * no fact at all — "Answered by " is worse than silence.
 */
function lastAnsweredLabel(messages: DisplayMessage[] | undefined): string | null {
  if (!messages) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message.role !== "assistant") continue;
    const label = message.answeredWith?.label?.trim();
    if (label) return label;
  }
  return null;
}

export function Composer({
  connected,
  turn,
  models,
  draftSeed,
  onDraftSeedUsed,
  focusSignal,
}: Props) {
  const { isWorking, handleSend, handleStop } = turn;
  const answeredLabel = lastAnsweredLabel(turn.messages);
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow from one line to the cap, then scroll. Runs on every draft change
  // (including the prefill and the post-send reset).
  function autoGrow(el: HTMLTextAreaElement | null) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_PX)}px`;
  }
  useEffect(() => {
    autoGrow(textareaRef.current);
  }, [draft]);

  // The mount-time measurement can land before the stylesheet (or the window's
  // real width) is settled, and because the effect above only runs when the
  // draft changes, a wrong first answer would stick for the whole session — a
  // one-line composer 180px tall. Re-measure once the frame has painted, and
  // again whenever the window resizes.
  useEffect(() => {
    const frame = requestAnimationFrame(() => autoGrow(textareaRef.current));
    const onResize = () => autoGrow(textareaRef.current);
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  // A seeded draft lands here, once, and takes focus with it.
  useEffect(() => {
    if (draftSeed != null && draftSeed !== "") {
      setDraft(draftSeed);
      onDraftSeedUsed?.();
      textareaRef.current?.focus();
    }
  }, [draftSeed, onDraftSeedUsed]);

  // First-run "say hello" nudge: focus the textarea (no prefill) when the signal
  // bumps. Guarded on > 0 so the initial mount doesn't steal focus.
  useEffect(() => {
    if (focusSignal && focusSignal > 0 && !isWorking) {
      textareaRef.current?.focus();
    }
  }, [focusSignal, isWorking]);

  function submit() {
    const text = draft.trim();
    if (!text || isWorking) return;
    setDraft("");
    handleSend(text);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const canSend = Boolean(draft.trim()) && !isWorking;
  const placeholder = !connected
    ? "Addison's engine isn't connected yet."
    : isWorking
      ? "Addison is working…"
      : "Write to Addison…";

  return (
    <div className="relative pb-[calc(env(safe-area-inset-bottom)+22px)]">
      {/* One bordered box, two stacked rows: the FULL-WIDTH textarea over a
          right-aligned controls strip (model label + send). The prototype put
          the controls beside the text, but a tall draft then drags a
          text-wide empty column down its whole right side — the owner asked
          for the text to wrap around the controls instead (2026-07-26). The
          strip beneath is how a textarea can honestly do that: every line
          gets the full 840px, and the controls sit where the eye expects
          them, under the last line. Resting height stays two-deep (one text
          line + the strip), so the Settings-level alignment holds. */}
      {/* `focus-within:pt-[5px]` is not a nudge: it pays for the second pixel of
          border (pt-1.5 = 6px → 5px), so lighting the rule up never shifts the
          text under it. See the focus note in the file header. */}
      <div className="mx-auto w-full max-w-[840px] border-t border-track px-0.5 pt-1.5 transition-colors duration-200 focus-within:border-t-2 focus-within:border-accent focus-within:pt-[5px]">
        <textarea
          ref={textareaRef}
          data-composer=""
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={isWorking}
          rows={1}
          placeholder={placeholder}
          aria-label="Message to Addison"
          // `bespoke-scroll` (styles.css) swaps the native overlay bar — which
          // drew over the text once the textarea went full-width — for the 4px
          // floated thumb; the stable gutter reserves its lane up front so the
          // text never reflows the moment a draft starts to scroll.
          className="bespoke-scroll block w-full resize-none overflow-y-auto border-0 bg-transparent pb-[2px] pt-[9px] text-[15px] leading-[1.5] text-ink outline-none [scrollbar-gutter:stable] placeholder:text-disabled disabled:text-muted"
          style={{ maxHeight: `${MAX_TEXTAREA_PX}px` }}
        />
        {/* pt-3, not a token nudge: the visual space above the send button is
            the point of the stacked layout (owner request 2026-07-26). */}
        <div className="flex items-center justify-end gap-3 pb-[5px] pt-3">
          {/* The answering-model disclosure. Deliberately the SAME mono
              machine-fact idiom as the picker beside it (10.5px, `disabled`)
              rather than the fainter `ghost` used for the microcopy below: this
              is the sentence the owner asked for because nothing was saying it,
              and a line nobody can read would not be saying it either — the
              readers are 54 and 68. It stays a plain span, so the only thing
              separating it from the label is that one of them is a button.
              Truncated with the full sentence on `title` so a long model name
              can't push the picker off a narrow window. Never aria-hidden: a
              screen reader reads it in the strip, before the picker it is
              about. */}
          {answeredLabel && (
            <span
              data-answered-by=""
              title={`Answered by ${answeredLabel}`}
              className="min-w-0 max-w-[120px] truncate font-mono text-[10.5px] text-disabled md:max-w-[220px]"
            >
              Answered by {answeredLabel}
            </span>
          )}
          <ModelSelector
            roles={models.roles}
            cloudModels={models.cloudModels}
            selectedRole={models.selectedRole}
            selectedCloudModel={models.selectedCloudModel}
            selectedLocalModel={models.selectedLocalModel}
            selectedEffort={models.selectedEffort}
            onSelectModel={models.handleSelectModel}
            onSelectEffort={models.handleSelectEffort}
            disabled={isWorking}
          />
          {isWorking ? (
            <button
              type="button"
              onClick={handleStop}
              title="Stop"
              aria-label="Stop"
              className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full border border-track bg-transparent text-[11px] text-disabled transition-colors hover:text-ink max-md:h-11 max-md:w-11"
            >
              <span aria-hidden="true">■</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!canSend}
              title="Send"
              aria-label="Send"
              className={
                "flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full border text-[15px] transition-colors duration-200 max-md:h-11 max-md:w-11 " +
                (canSend
                  ? "border-accent bg-accent text-on-accent"
                  : "cursor-not-allowed border-track bg-transparent text-disabled")
              }
            >
              <span aria-hidden="true">↑</span>
            </button>
          )}
        </div>
      </div>
      <p
        data-scramble="960"
        className="m-0 mx-auto mt-2.5 w-fit text-center font-mono text-[10px] text-ghost"
      >
        enter to send · everything can be undone
      </p>
    </div>
  );
}
