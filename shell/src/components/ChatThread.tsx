// Chat thread — the scrollable message column (Fern direction; design-brief-fern).
//
// Correspondence, not chat bubbles: left-aligned rows with a small-caps sender
// label above each (YOU faint / ADDISON fern-deep) and the message body in the
// Source Serif 4 "correspondence" voice (17px/1.7). 26px between turns, no
// borders, no bubbles. Streamed assistant text is appended to an in-progress
// message and finalized when the sendMessage response lands (handled in App).
//
// This component is JUST the message column now: it owns its own scroll (hidden
// scrollbar, its content capped at 580px). The header, widget rail, and composer
// are laid out around it by App. A `footer` slot renders after the messages,
// inside the scroll — App puts the consent card and "Addison's work" block there
// when the widget rail is hidden.

import { useEffect, useRef, type ReactNode } from "react";
import type { DisplayMessage } from "../types/ui";
import { Markdown } from "./Markdown";

interface Props {
  messages: DisplayMessage[];
  onRetry: () => void;
  /** Whether the last answer can be regenerated (a real turn has happened). */
  retryAvailable: boolean;
  onRewindTo: (messageId: string) => void;
  /**
   * Developer profile only: when a turn fails and the core supplied raw error
   * text, show it in a collapsed "Technical details" block under the plain
   * message. Off (and absent) for Simple, so its thread is byte-identical.
   */
  showTechnicalDetails?: boolean;
  /** Rendered before the messages, inside the scroll (first-run banner + greeting). */
  header?: ReactNode;
  /** Rendered after the last message, inside the scroll (consent + work inline). */
  footer?: ReactNode;
}

const SENDER_LABEL: Record<string, string> = {
  user: "You",
  assistant: "Addison",
};

export function ChatThread({
  messages,
  onRetry,
  retryAvailable,
  onRewindTo,
  showTechnicalDetails = false,
  header,
  footer,
}: Props) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Correspondence view shows the human turns; live tool steps live in the
  // widget rail / work block, so tool messages aren't repeated here.
  const visible = messages.filter((m) => m.role !== "tool");

  // Keep the newest content in view without any fancy motion. Skip it entirely
  // when there are no messages yet: on first run the header (pine banner +
  // greeting) is the content, and it must stay at the top rather than being
  // scrolled out of sight.
  useEffect(() => {
    if (visible.length === 0) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, header, footer]);
  const lastAssistantId = [...visible].reverse().find((m) => m.role === "assistant" && !m.pending)?.id;

  return (
    <div className="no-scrollbar flex min-h-0 w-full max-w-[580px] flex-1 flex-col gap-[26px] overflow-y-auto py-[30px]">
      {header}

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

      {footer}

      <div ref={bottomRef} />
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
    // A gentle opacity fade as each turn arrives (opacity only — never shifts
    // layout). Disabled wholesale under prefers-reduced-motion (styles.css).
    <div className="group animate-[fade-in_200ms_ease]">
      <div className="flex items-baseline justify-between gap-3">
        <span
          className={
            "text-label font-semibold uppercase tracking-caps-wider " +
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
        <p className="mt-1.5 font-serif text-message italic leading-[1.7] text-muted">
          Addison is writing…
        </p>
      ) : isAddison && !message.failed ? (
        // Assistant answers render as markdown (with mermaid + code highlighting).
        // The markdown body inherits the serif "correspondence" voice. User input
        // and failed turns stay plain text — never markdown-render what the user
        // typed, and keep error copy verbatim.
        <div className="mt-1.5 font-serif text-message leading-[1.7] text-ink">
          <Markdown content={message.content} pending={message.pending} />
        </div>
      ) : (
        <p
          className={
            "mt-1.5 whitespace-pre-wrap font-serif text-message leading-[1.7] " +
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
