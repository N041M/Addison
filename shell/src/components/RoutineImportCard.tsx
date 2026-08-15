// The card that asks whether to add a routine somebody else wrote.
//
// It is the RoutineProposalCard wearing a different question, on purpose: the two
// are the same object (here is a plan, here is what it would do, say yes or no),
// and a person who has met one should not have to learn a second shape. Same flat
// `panel`, same 1px `rail` border and 7px radius, same numbered plan on a 2px
// rail, same accent confirm beside a muted dismiss. No new visual vocabulary.
//
// WHAT THIS COMPONENT MAY NOT DO. Every sentence it shows comes from the core, and
// the three `assurances` are rendered VERBATIM and in full, always. They are what
// an honest description of a stranger's file consists of, and softening one, or
// dropping one because the card felt long, would leave the person agreeing to
// something they were not told. `shell/src/__tests__/routineSharing.test.tsx`
// asserts them word for word for that reason. The same rule covers `screeningNote`:
// it is one plain sentence the core wrote, shown as it arrived, and it is absent
// exactly when the core did not send it. This surface invents no reassurance of its
// own and adds no "looks fine" of any kind: nothing here has checked anything.

export interface RoutineImportPreview {
  name: string;
  description: string;
  /** Already numbered and plain-verbed by the core ("1. Search the web"). */
  steps: string[];
  variables: { name: string; prompt: string; default: string | null }[];
  /** The plan uses abilities Simple does not have, so the row will land disabled. */
  needsDeveloper: boolean;
  /** Present only when the file's wording read like an instruction to Addison. */
  screeningNote?: string;
  /** The three mandatory sentences. Rendered in full, in order, never edited. */
  assurances: string[];
}

interface Props {
  preview: RoutineImportPreview;
  onAdd: () => void;
  onCancel: () => void;
  busy?: boolean;
}

export function RoutineImportCard({ preview, onAdd, onCancel, busy = false }: Props) {
  return (
    <section
      aria-label="Add this shared routine?"
      className="mt-2.5 animate-[fadeRise_.2s_ease_both] rounded-[7px] border border-rail bg-panel px-3.5 py-3"
    >
      <h3 className="m-0 text-[12px] font-medium text-ink">Add this shared routine?</h3>
      <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-muted">
        Someone made this on their own computer. Here's what it says it does:
      </p>

      <p className="m-0 mt-2.5 text-[12px] leading-[1.55] text-ink">{preview.name}</p>
      {preview.description && (
        <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">{preview.description}</p>
      )}

      <ol className="m-0 mt-2.5 list-none border-l-2 border-rail p-0 pl-3.5">
        {preview.steps.map((step) => (
          <li key={step} className="text-[12px] leading-[1.6] text-ink-soft">
            {step}
          </li>
        ))}
      </ol>

      {preview.variables.length > 0 && (
        <p className="m-0 mt-2.5 text-[12px] leading-[1.55] text-muted">
          Each time it runs, Addison will ask:{" "}
          {preview.variables.map((v) => `"${v.prompt}"`).join(" ")}
        </p>
      )}

      {/* Said BEFORE the assurances, because it changes what saying yes gets you:
          the row lands listed and switched off. The existing waiting-row rendering
          in the library is what the person will then see. */}
      {preview.needsDeveloper && (
        <p className="m-0 mt-2.5 text-[12px] leading-[1.55] text-ink-soft">
          This routine needs the Developer profile to run. It will be listed, and
          switched off, until then.
        </p>
      )}

      {/* One sentence, the core's own, and only when the core sent it. It names no
          rule and quotes nothing of what was found. */}
      {preview.screeningNote && (
        <p className="m-0 mt-2.5 text-[12px] leading-[1.55] text-ink-soft">
          {preview.screeningNote}
        </p>
      )}

      <ul className="m-0 mt-3 list-none border-l-2 border-rail p-0 pl-3.5">
        {preview.assurances.map((sentence) => (
          <li key={sentence} className="mb-1.5 text-[12px] leading-[1.55] text-muted last:mb-0">
            {sentence}
          </li>
        ))}
      </ul>

      <div className="mt-3 flex items-baseline gap-5">
        <button
          type="button"
          onClick={onAdd}
          disabled={busy}
          className="text-[12px] text-accent transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-disabled max-md:min-h-[44px]"
        >
          {busy ? "Adding…" : "Add it"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-[12px] text-muted transition-colors hover:text-ink max-md:min-h-[44px]"
        >
          Don't add it
        </button>
      </div>
    </section>
  );
}
