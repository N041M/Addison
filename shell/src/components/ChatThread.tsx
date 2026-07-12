// Chat thread — the message region (design-doc §7.1).
// Each past assistant/user message hosts Rewind + Retry affordances (§7.9.1);
// while the agent works, the send button is replaced by Stop (see RewindControls).

import type { ChatMessage } from "../types/protocol";

interface Props {
  messages: ChatMessage[];
  onRewindTo: (messageId: string) => void; // conversational rewind, §7.9 mechanism 1
}

export function ChatThread({ messages, onRewindTo }: Props) {
  // TODO(step 7): render streamed messages; expose Rewind on hover per message.
  return (
    <div className="chat-thread">
      {messages.map((m) => (
        <div key={m.id} className={`msg msg-${m.role}`}>
          {m.content}
          {m.role !== "tool" && (
            <button className="rewind-btn" onClick={() => onRewindTo(m.id)}>
              Rewind to here
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
