// Composer — the message row at the foot of the chat column (DARK direction;
// docs/design-brief-dark, "Screens → Composer").
//
// No card, no border box: a borderless 15px textarea over a single 1px top rule
// that brightens from `track` to `track-hi` while the row has focus. To its
// right sit the two controls the person actually reaches for — the model label
// (mono, opens the per-message menu) and a 30px circle that is Send, and becomes
// the real Stop while a turn runs. One line of mono microcopy sits under it.
//
// It carries NO horizontal padding of its own: `main` supplies the 40px gutter
// (16px below md), and doubling it was the misalignment phase 1 flagged — the
// composer row and the thread above it must share one left edge.
//
// The Stop control is not in the prototype and is not decoration: the v1 IPC
// contract has no core-side cancel, so Stop is what lets a person end a turn
// that is taking too long (useTurn.handleStop). It keeps its aria-label.

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { TurnState } from "../hooks/useTurn";
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

export function Composer({
  connected,
  turn,
  models,
  draftSeed,
  onDraftSeedUsed,
  focusSignal,
}: Props) {
  const { isWorking, handleSend, handleStop } = turn;
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
      <div className="mx-auto w-full max-w-[840px] border-t border-track px-0.5 pt-1.5 transition-colors duration-200 focus-within:border-track-hi">
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
          className="block w-full resize-none overflow-y-auto border-0 bg-transparent pb-[2px] pt-[9px] text-[15px] leading-[1.5] text-ink outline-none placeholder:text-disabled disabled:text-muted"
          style={{ maxHeight: `${MAX_TEXTAREA_PX}px` }}
        />
        <div className="flex items-center justify-end gap-3 pb-[5px] pt-1">
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
