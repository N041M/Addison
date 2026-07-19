// Composer — the message-entry card (Fern direction; design-brief-fern README §2).
//
// A rounded `surface` card with a soft shadow, spanning the full width below the
// chat column and widget rail. The textarea sits above a row that pairs the
// model pill (ModelSelector, a muted text button) with the fern Send button;
// Send flips to Stop while a turn runs. A 12px dim hint sits below the card.
// Extracted from ChatThread so the message column and rail can scroll between the
// header and this fixed composer.

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { ModelRole } from "../types/protocol";
import type { CloudModel, RoleOption } from "../types/ui";
import { ModelSelector } from "./ModelSelector";

interface Props {
  connected: boolean;
  isWorking: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  roles: RoleOption[];
  cloudModels: CloudModel[];
  selectedRole: ModelRole;
  selectedCloudModel?: string;
  selectedLocalModel?: string;
  selectedEffort?: string;
  onSelectModel: (role: ModelRole, modelId: string) => void;
  onSelectEffort: (effort: string) => void;
  /** One-shot prefill from a rewind's edit-and-resend; nothing runs until Send. */
  draftSeed?: string | null;
  onDraftSeedUsed?: () => void;
  /** Bump to focus the textarea without prefilling (first-run "say hello" nudge). */
  focusSignal?: number;
}

export function Composer({
  connected,
  isWorking,
  onSend,
  onStop,
  roles,
  cloudModels,
  selectedRole,
  selectedCloudModel,
  selectedLocalModel,
  selectedEffort,
  onSelectModel,
  onSelectEffort,
  draftSeed,
  onDraftSeedUsed,
  focusSignal,
}: Props) {
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow the textarea from one line up to ~8 lines, then scroll. Runs on
  // every draft change (including the rewind prefill and the post-send reset).
  function autoGrow(el: HTMLTextAreaElement | null) {
    if (!el) return;
    el.style.height = "auto";
    const max = 8 * 24; // ~8 lines at the 24px line box below
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }
  useEffect(() => {
    autoGrow(textareaRef.current);
  }, [draft]);

  // Rewind's edit-and-resend: the rewound message's text lands here, once.
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
    onSend(text);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    // Full-width with 16px side padding below md; the desktop 44px gutters
    // restore at md. The bottom padding folds in the phone safe-area inset
    // (0 on desktop, so it stays the plain 20px there).
    <div className="px-4 pt-3.5 pb-[calc(env(safe-area-inset-bottom)+1.25rem)] md:px-[44px]">
      <div className="mx-auto w-full max-w-[840px] rounded-card border border-line bg-surface px-3.5 pb-2 pt-2.5 shadow-soft focus-within:border-fern">
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={isWorking}
          rows={1}
          placeholder={connected ? "Write to Addison…" : "Addison's engine isn't connected yet."}
          aria-label="Message to Addison"
          className="block max-h-[192px] w-full resize-none overflow-y-auto bg-transparent px-1 py-0.5 text-[15px] leading-6 text-ink placeholder:text-faint focus:outline-none disabled:opacity-60"
        />
        <div className="mt-1.5 flex items-center justify-between gap-x-3 px-1">
          <ModelSelector
            roles={roles}
            cloudModels={cloudModels}
            selectedRole={selectedRole}
            selectedCloudModel={selectedCloudModel}
            selectedLocalModel={selectedLocalModel}
            selectedEffort={selectedEffort}
            onSelectModel={onSelectModel}
            onSelectEffort={onSelectEffort}
            disabled={isWorking}
          />
          <div className="ml-auto">
            {isWorking ? (
              <button
                type="button"
                onClick={onStop}
                className="rounded-sm border border-line bg-surface px-5 py-2 text-[13.5px] font-semibold text-ink-soft hover:border-danger hover:text-danger max-md:min-h-[44px] max-md:px-6"
              >
                Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={submit}
                disabled={!draft.trim()}
                className="rounded-sm bg-fern px-[26px] py-[9px] text-[13.5px] font-semibold text-on-accent hover:bg-fern-deep disabled:cursor-not-allowed disabled:opacity-50 max-md:min-h-[44px] max-md:px-7"
              >
                Send
              </button>
            )}
          </div>
        </div>
      </div>
      {/* The hint line is hidden below md (the mobile shots show none). */}
      <p className="mx-auto mt-2 hidden max-w-[840px] text-xs text-faint md:block">
        Press Enter to send. Shift+Enter starts a new line. Addison asks first, and
        anything it does can be undone.
      </p>
    </div>
  );
}
