// Your documents — the Settings face of the knowledge base (knowledge retrieval,
// phase 3 of three), in the dark direction's row idiom. It renders in EVERY
// profile: the tool behind it is LOW and read-only, so Simple searches its own
// documents too (owner decision 2, 2026-08-24), and a page listing somebody's own
// files must never empty itself on a profile switch.
//
// THE STANDING LINE SAYS WHERE THE WORDS GO, and that is the honest half of this
// feature. A person who adds a document is entitled to know two things before
// they do it: the searchable copy stays on this computer, and the passages that
// match a question are sent to whichever model answers — exactly like anything
// else they type into a chat. Neither half may be dropped as the feature matures.
//
// A ROW NEVER CLAIMS MORE THAN THE CORE SAID. "Ready. 3 passages." appears only
// on a row the core marked indexed; a document whose file changed says so instead
// of quietly answering questions from stale text; a failed row prints the core's
// own sentence and offers the way back. `onDisk` is computed while the list is
// answered and never stored, so a row cannot go on insisting a file is there
// after it has gone (Surface.tsx's standing rule 1).
//
// ADDING AND UPDATING BOTH OPEN THE OS PICKER. There is no path field on this
// page and no way for it to name a file: the person picks, every time, including
// when Addison re-reads a document it already knows. That is what keeps a
// standing index from becoming a standing licence to read a path.
//
// Removing is a two-press confirm on the row (the SkillsSection idiom) plus one
// sentence, because there IS a consequence to explain here that the tool servers
// do not have: the knowledge tables are excluded from restore points, so this is
// the rare removal a restore cannot undo.

import { useState } from "react";
import type { KnowledgeCardState } from "../hooks/useKnowledge";
import type { KnowledgeDocument } from "../types/ui";
import { RowAction, SurfaceRow } from "./Surface";

// --- Frozen plain-language copy ---------------------------------------------

/** The panel's standing line. Three sentences, and the third is the one that
 * cannot be cut: retrieval means a passage of the person's document travels to
 * the model that answers, and somebody deciding whether to add their tenancy
 * agreement needs that said plainly, at the moment they are deciding. */
const STANDING_LINE =
  "Give Addison a document and it can find the right part when you ask, instead of " +
  "reading the whole file. It keeps a searchable copy on this computer. When you ask " +
  "about a document, the parts that match are sent to the model that answers, like " +
  "anything else you say in a chat.";

const ADD_ACTION = "Add a document…";

/** While a picker is open and a document is being indexed. It names the wait
 * rather than leaving a page that does nothing for a minute: a local embedding
 * pass over a couple of megabytes has no progress to report, and silence there
 * reads as a broken app. */
const BUSY_LINE =
  "Addison is reading the document and building its index. A big file can take a minute.";

const LOADING_LINE = "Looking for your documents…";
const EMPTY_LINE = "No documents yet.";
const ANOTHER_LINE = "Another document";
const NOT_CONNECTED_LINE = "Your documents appear here once Addison's engine is connected.";

/** Beside the second press of Remove. The consequence the two-press confirm
 * cannot carry on its own: everywhere else in this app a restore point is the way
 * back, and the knowledge tables are deliberately not in one (owner decision 4,
 * 2026-08-24), so this removal is the one that stays done. */
const REMOVAL_IS_PERMANENT =
  "Removing a document is permanent — a restore point won't bring it back. The file on " +
  "your computer is left alone.";

/** What a row says about itself, under its path. Exactly one sentence per state,
 * chosen from what the core sent and never from a guess. */
export function knowledgeStatusLine(doc: KnowledgeDocument): string {
  if (doc.status === "failed") {
    // The core's own words, verbatim. The fallback is for a failed row that
    // arrived without them — rare, and still better than a silent row with a
    // "Try again" button on it and nothing to explain why.
    return doc.detail ?? "Addison couldn't read this document.";
  }
  if (doc.status === "pending") return "Addison hasn't finished reading this.";
  if (doc.onDisk === "changed") return "This file has changed since Addison read it.";
  if (doc.onDisk === "missing") return "Addison can't find this file any more.";
  const ready =
    doc.chunkCount === 1 ? "Ready. 1 passage." : `Ready. ${doc.chunkCount} passages.`;
  // Said only when there is something to say. The screening layer marks writing
  // shaped like an instruction, and a person is told the count on the page where
  // they could still remove the document (untrusted screening, 2026-08-13).
  if (doc.flaggedChunks > 0) {
    return (
      ready +
      ` ${doc.flaggedChunks} of them contain writing shaped like an instruction; ` +
      "Addison treats those as information."
    );
  }
  return ready;
}

/** The row's re-read control, or null when there is nothing to re-read. Both
 * labels send the same `knowledge.reindex`; what differs is what the person is
 * being offered — a file that moved on is UPDATED, a read that failed is TRIED
 * AGAIN, and a file Addison cannot find gets neither, because opening a picker
 * on a file that is not there would be an errand with no end. */
function reindexLabel(doc: KnowledgeDocument): string | null {
  if (doc.status === "failed" || doc.status === "pending") return "Try again";
  if (doc.onDisk === "changed") return "Update";
  return null;
}

export function KnowledgePanel({
  connected,
  knowledge: state,
}: {
  connected: boolean;
  knowledge: KnowledgeCardState;
}) {
  const { documents, documentsLoaded, busy, error, notice, handleAdd, handleReindex, handleRemove } =
    state;

  // Which row is one press away from being removed. Same two-press idiom the
  // skills and tool-server rows use; never a browser confirm().
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);

  if (!connected) {
    return <SurfaceRow wrap name={NOT_CONNECTED_LINE} />;
  }

  function add() {
    setConfirmingRemove(null);
    void handleAdd();
  }

  function remove(doc: KnowledgeDocument) {
    if (confirmingRemove !== doc.id) {
      setConfirmingRemove(doc.id);
      return;
    }
    setConfirmingRemove(null);
    void handleRemove(doc.id, doc.displayName);
  }

  return (
    <>
      <SurfaceRow wrap name={STANDING_LINE} />

      {/* A refusal in the core's own already-plain words — never a stack trace.
          A closed picker reaches neither of these lines: the hook keeps "never
          mind" out of both, so cancelling shows nothing at all. */}
      {error && <SurfaceRow wrap name={error} />}

      {/* The outcome of the last removal. Stays put rather than fading. */}
      {notice && <SurfaceRow wrap name={notice} />}

      {/* The wait, named. Every control below is disabled while it is up. */}
      {busy && <SurfaceRow wrap name={BUSY_LINE} />}

      {!documentsLoaded ? (
        <SurfaceRow wrap name={LOADING_LINE} />
      ) : documents.length === 0 ? (
        <SurfaceRow
          name={EMPTY_LINE}
          action={ADD_ACTION}
          actionDisabled={busy}
          actionAriaLabel="Add a document"
          onAction={add}
        />
      ) : (
        documents.map((doc) => {
          const reindex = reindexLabel(doc);
          return (
            <SurfaceRow
              key={doc.id}
              name={doc.displayName}
              // Two controls on some rows, so the row composes them itself: a
              // single action slot would have made a person choose which of the
              // two a row is for.
              actions={
                <>
                  {reindex && (
                    <RowAction
                      onClick={() => void handleReindex(doc.id)}
                      disabled={busy}
                      // A column of identical "Update" buttons is the shape in
                      // which somebody updates the wrong document.
                      ariaLabel={`${reindex} ${doc.displayName}`}
                    >
                      {reindex}
                    </RowAction>
                  )}
                  <RowAction
                    tone="danger"
                    onClick={() => remove(doc)}
                    disabled={busy}
                    ariaLabel={`Remove ${doc.displayName}`}
                  >
                    {confirmingRemove === doc.id ? "Really remove?" : "Remove"}
                  </RowAction>
                </>
              }
            >
              {/* Where the file is, in the machine-fact mono. Two documents called
                  "Notes.txt" are told apart by nothing else. */}
              {doc.path && (
                <p className="m-0 mt-1 break-all font-mono text-[11px] text-muted">{doc.path}</p>
              )}
              <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                {knowledgeStatusLine(doc)}
              </p>
              {confirmingRemove === doc.id && (
                <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                  {REMOVAL_IS_PERMANENT}
                </p>
              )}
            </SurfaceRow>
          );
        })
      )}

      {documents.length > 0 && (
        <SurfaceRow
          name={ANOTHER_LINE}
          action={ADD_ACTION}
          actionDisabled={busy}
          actionAriaLabel="Add a document"
          onAction={add}
        />
      )}
    </>
  );
}
