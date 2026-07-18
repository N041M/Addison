// Chat thread — the message region (Fern direction; docs/design-brief-fern).
//
// Correspondence, not chat bubbles: full-width, left-aligned rows with a
// small-caps sender label above each (YOU faint / ADDISON fern-deep) and the
// message body in the Source Serif 4 "correspondence" voice (17px/1.7). 26px
// between turns, no borders, no bubbles. Streamed assistant text is appended to
// an in-progress message and finalized when the sendMessage response lands
// (handled in App). This component also hosts the composer — a rounded `surface`
// card with the model pill beside the fern Send button, Retry on the last
// answer, and the per-message "Rewind to here" affordance (design-doc §7.9.1).

import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ModelRole, PermissionRequest } from "../types/protocol";
import type { CloudModel, DisplayMessage, RoleOption } from "../types/ui";
import { PermissionCard } from "./PermissionCard";
import { ModelSelector } from "./ModelSelector";
import { Markdown } from "./Markdown";

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
  cloudModels: CloudModel[];
  selectedRole: ModelRole;
  selectedCloudModel?: string;
  selectedLocalModel?: string;
  selectedEffort?: string;
  onSelectModel: (role: ModelRole, modelId: string) => void;
  onSelectEffort: (effort: string) => void;
  /**
   * Developer profile only: when a turn fails and the core supplied raw error
   * text, show it in a collapsed "Technical details" block under the plain
   * message. Off (and absent) for Simple, so its thread is byte-identical.
   */
  showTechnicalDetails?: boolean;
  /** The collapsible activity strip, rendered between the thread and composer. */
  activityStrip?: ReactNode;
  /**
   * One-shot composer prefill: rewinding to one of the user's messages pulls
   * its text back into the box for editing — nothing runs until they Send.
   */
  draftSeed?: string | null;
  onDraftSeedUsed?: () => void;
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
  cloudModels,
  selectedRole,
  selectedCloudModel,
  selectedLocalModel,
  selectedEffort,
  onSelectModel,
  onSelectEffort,
  showTechnicalDetails = false,
  activityStrip,
  draftSeed,
  onDraftSeedUsed,
}: Props) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Keep the newest content in view without any fancy motion.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, permission, isWorking]);

  // Rewind's edit-and-resend: the rewound message's text lands here, once.
  useEffect(() => {
    if (draftSeed != null && draftSeed !== "") {
      setDraft(draftSeed);
      onDraftSeedUsed?.();
    }
  }, [draftSeed, onDraftSeedUsed]);

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
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-[26px] px-6 py-8">
          {visible.map((m) => (
            <MessageRow
              key={m.id}
              message={m}
              canRewind={m.role === "user" && Boolean(m.storeId)}
              canRetry={m.id === lastAssistantId && retryAvailable}
              onRewindTo={onRewindTo}
              onRetry={onRetry}
              showTechnicalDetails={showTechnicalDetails}
            />
          ))}

          {permission && (
            <PermissionCard request={permission} onRespond={onRespondPermission} />
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {activityStrip}

      <div className="border-t border-line bg-paper">
        <div className="mx-auto w-full max-w-3xl px-6 py-4">
          <div className="rounded-card border border-line bg-surface shadow-soft focus-within:border-fern">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={isWorking}
              rows={2}
              placeholder={connected ? "Write to Addison…" : "Addison's engine isn't connected yet."}
              aria-label="Message to Addison"
              className="block w-full resize-none bg-transparent px-4 py-3 text-base text-ink placeholder:text-faint focus:outline-none disabled:opacity-60"
            />
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-3 pb-3">
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
                    className="rounded-sm border border-line bg-surface px-5 py-2.5 text-sm font-semibold text-ink-soft hover:border-danger hover:text-danger"
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={submit}
                    disabled={!draft.trim()}
                    className="rounded-sm bg-fern px-[26px] py-[9px] text-sm font-semibold text-on-accent hover:bg-fern-deep disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Send
                  </button>
                )}
              </div>
            </div>
          </div>
          <p className="mt-2 text-xs text-faint">
            Press Enter to send. Shift+Enter starts a new line. Addison asks first,
            and anything it does can be undone.
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
  showTechnicalDetails: boolean;
}

function MessageRow({
  message,
  canRewind,
  canRetry,
  onRewindTo,
  onRetry,
  showTechnicalDetails,
}: RowProps) {
  const label = SENDER_LABEL[message.role] ?? message.role;
  const isAddison = message.role === "assistant";
  const showWriting = message.pending && message.content.length === 0;
  const showRaw = showTechnicalDetails && message.failed && Boolean(message.raw);

  return (
    <div className="group">
      <div className="flex items-baseline justify-between gap-3">
        <span
          className={
            "text-[10.5px] font-semibold uppercase tracking-[0.1em] " +
            (isAddison ? "text-fern-deep" : "text-faint")
          }
        >
          {label}
        </span>
        {canRewind && (
          <button
            type="button"
            onClick={() => message.storeId && onRewindTo(message.storeId)}
            className="text-xs font-medium text-muted opacity-0 transition-opacity hover:text-fern-deep focus:opacity-100 group-hover:opacity-100"
          >
            Rewind to here
          </button>
        )}
      </div>

      {showWriting ? (
        <p className="mt-1.5 font-serif text-[17px] italic leading-[1.7] text-muted">
          Addison is writing…
        </p>
      ) : isAddison && !message.failed ? (
        // Assistant answers render as markdown (with mermaid + code highlighting).
        // The markdown body inherits the serif "correspondence" voice. User input
        // and failed turns stay plain text — never markdown-render what the user
        // typed, and keep error copy verbatim.
        <div className="mt-1.5 font-serif text-[17px] leading-[1.7] text-ink">
          <Markdown content={message.content} pending={message.pending} />
        </div>
      ) : (
        <p
          className={
            "mt-1.5 whitespace-pre-wrap font-serif text-[17px] leading-[1.7] " +
            (message.failed ? "text-danger" : "text-ink")
          }
        >
          {message.content}
        </p>
      )}

      {showRaw && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium text-muted hover:text-ink-soft">
            Technical details
          </summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded border border-line bg-surface px-3 py-2 font-mono text-xs text-ink-soft">
            {message.raw}
          </pre>
        </details>
      )}

      {canRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 text-sm font-medium text-muted hover:text-fern-deep"
        >
          Retry this answer
        </button>
      )}
    </div>
  );
}
