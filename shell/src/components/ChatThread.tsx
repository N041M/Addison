// Chat thread — the message region (design-doc §7.1).
//
// Correspondence, not chat bubbles: full-width, left-aligned rows with a small
// sender label above each ("You" / "Addison"), roomy line-height, high contrast.
// Streamed assistant text is appended to an in-progress message and finalized
// when the sendMessage response lands (handled in App). This component also
// hosts the composer at the bottom with the model selector beside it, the
// Stop/Send control, Retry on the last answer, and the per-message "Rewind to
// here" affordance (design-doc §7.9.1).

import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ModelRole, PermissionRequest } from "../types/protocol";
import type { DisplayMessage, RoleOption } from "../types/ui";
import { PermissionCard } from "./PermissionCard";
import { ModelSelector } from "./ModelSelector";

interface Props {
  messages: DisplayMessage[];
  isWorking: boolean;
  connected: boolean;
  permission: PermissionRequest | null;
  onRespondPermission: (allow: boolean) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onRetry: () => void;
  /** Whether the last answer can be regenerated (a real turn has happened). */
  retryAvailable: boolean;
  onRewindTo: (messageId: string) => void;
  roles: RoleOption[];
  selectedRole: ModelRole;
  selectedLocalModel?: string;
  onSelectRole: (role: ModelRole) => void;
  onSelectLocalModel: (modelId: string) => void;
  /** The collapsible activity strip, rendered between the thread and composer. */
  activityStrip?: ReactNode;
}

const SENDER_LABEL: Record<string, string> = {
  user: "You",
  assistant: "Addison",
};

export function ChatThread({
  messages,
  isWorking,
  connected,
  permission,
  onRespondPermission,
  onSend,
  onStop,
  onRetry,
  retryAvailable,
  onRewindTo,
  roles,
  selectedRole,
  selectedLocalModel,
  onSelectRole,
  onSelectLocalModel,
  activityStrip,
}: Props) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Keep the newest content in view without any fancy motion.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, permission, isWorking]);

  // Correspondence view shows the human turns; live tool steps live in the
  // Activity strip, so tool messages aren't repeated here.
  const visible = messages.filter((m) => m.role !== "tool");
  const lastAssistantId = [...visible].reverse().find((m) => m.role === "assistant" && !m.pending)?.id;

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
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="thread-scroll min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-6 py-6">
          {visible.map((m) => (
            <MessageRow
              key={m.id}
              message={m}
              canRewind={m.role === "user"}
              canRetry={m.id === lastAssistantId && retryAvailable}
              onRewindTo={onRewindTo}
              onRetry={onRetry}
            />
          ))}

          {permission && (
            <PermissionCard request={permission} onRespond={onRespondPermission} />
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {activityStrip}

      <div className="border-t border-line bg-surface">
        <div className="mx-auto w-full max-w-3xl px-6 py-4">
          <div className="border border-line bg-paper/60 focus-within:border-muted">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={isWorking}
              rows={2}
              placeholder={
                connected
                  ? "Tell Addison what you'd like help with…"
                  : "Addison's engine isn't connected yet."
              }
              aria-label="Message to Addison"
              className="block w-full resize-none bg-transparent px-4 py-3 text-base text-ink placeholder:text-muted focus:outline-none disabled:opacity-60"
            />
            <div className="flex items-center justify-between gap-3 px-3 pb-3">
              <ModelSelector
                roles={roles}
                selectedRole={selectedRole}
                selectedLocalModel={selectedLocalModel}
                onSelectRole={onSelectRole}
                onSelectLocalModel={onSelectLocalModel}
                disabled={isWorking}
              />
              <div className="ml-auto">
                {isWorking ? (
                  <button
                    type="button"
                    onClick={onStop}
                    className="border border-line bg-surface px-5 py-2.5 text-base font-semibold text-ink-soft hover:border-danger hover:text-danger"
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={submit}
                    disabled={!draft.trim()}
                    className="bg-accent px-6 py-2.5 text-base font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Send
                  </button>
                )}
              </div>
            </div>
          </div>
          <p className="mt-2 text-xs text-muted">
            Press Enter to send. Shift+Enter starts a new line.
          </p>
        </div>
      </div>
    </div>
  );
}

interface RowProps {
  message: DisplayMessage;
  canRewind: boolean;
  canRetry: boolean;
  onRewindTo: (messageId: string) => void;
  onRetry: () => void;
}

function MessageRow({ message, canRewind, canRetry, onRewindTo, onRetry }: RowProps) {
  const label = SENDER_LABEL[message.role] ?? message.role;
  const showWriting = message.pending && message.content.length === 0;

  return (
    <div className="group border-b border-line/70 py-4 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          {label}
        </span>
        {canRewind && (
          <button
            type="button"
            onClick={() => onRewindTo(message.id)}
            className="text-xs font-medium text-muted opacity-0 transition-opacity hover:text-accent-dark focus:opacity-100 group-hover:opacity-100"
          >
            Rewind to here
          </button>
        )}
      </div>

      {showWriting ? (
        <p className="mt-1 text-base italic text-muted">Addison is writing…</p>
      ) : (
        <p
          className={
            "mt-1 whitespace-pre-wrap text-base leading-relaxed " +
            (message.failed ? "text-danger" : "text-ink")
          }
        >
          {message.content}
        </p>
      )}

      {canRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 text-sm font-medium text-muted hover:text-accent-dark"
        >
          Retry this answer
        </button>
      )}
    </div>
  );
}
