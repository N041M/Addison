// Consent card — the plain-language permission prompt (design-doc §7.4,
// engineering-spec §4.3; DARK direction card language — a flat `panel` block
// with a 1px `rail` border and a 7px radius).
//
// Rendered when the Core emits `permission.requestGrant`; blocks the tool call
// until the user answers. It sits in the widget rail (rail open) or inline in the
// thread (rail hidden). For the primary personas (54–68, non-technical) the
// answer must be ONE obvious choice: an accent Allow, a plain "Not now", no risk
// codes or jargon. The tool provides its own plain label (the question) +
// description (the consequence); we never show a stack trace or a raw tool id.
//
// EVERY SENTENCE HERE IS THE CORE'S, VERBATIM. The redesign changed the skin and
// nothing else: the question, the consequence and the exact command text are
// rendered as sent, and both answers stay one press away.
//
// ---------------------------------------------------------------------------
// THE ARMING VARIANT (step 8 phase 3) — the keyword gate's face.
// ---------------------------------------------------------------------------
// When `request.arming` is present this card is the one place in the app where a
// person hands a command to the OPERATING SYSTEM to run on its own schedule, when
// Addison is closed and outside Addison's sandbox. Everything different about it
// follows from one sentence in docs/step-8-automation-plan.md §3: the code exists
// to make somebody READ the preview they are copying from, and "the preview is the
// defence" against the one attack the code cannot stop — a person who types it for
// a job they never understood.
//
// So the preview is the whole truth and not a summary: the name, the schedule in
// the core's own words, the EXACT command whole and unwrapped-in-full (never the
// ordinary card's truncating chip — a shortened command defeats the ceremony at its
// one moment), where the file goes, and the core's own warning sentences rendered
// verbatim. Then, and only then, the code and the box to retype it into.
//
// TWO RULES THIS FILE MUST KEEP:
//
//   1. NOTHING HERE COMPARES THE CODE. The typed text goes back on
//      `permission.respond` as `typed` and the CORE decides
//      (`agent_core/automation_nonce.py`, `hmac.compare_digest` after
//      normalisation). A client-side check would be a second source of truth for a
//      security decision and — worse — would teach the next reader that this side
//      is trusted here. The submit button is disabled while the box is EMPTY, which
//      is an affordance about emptiness and not a judgement about the value: a
//      wrong code submits, costs an attempt, and is answered by the core.
//   2. NOTHING HERE MAKES THE CODE ONE CLICK AWAY. No copy button, no autofill, no
//      `select-all`, no prefilled box, no autofocus. The friction IS the feature.
//      The code is ordinary selectable text — a person may still select and copy it
//      by hand, which is their choice; what this card must never do is make that
//      the path of least resistance.

import { useId, useState, type FormEvent, type ReactNode } from "react";
import type { PermissionRequest } from "../types/protocol";

interface Props {
  request: PermissionRequest;
  /**
   * `typed` is the arming card's code box, sent VERBATIM — untrimmed, uncased,
   * unnormalised. The core normalises and compares; a second normaliser here is a
   * place where the two could one day disagree.
   */
  onRespond: (allow: boolean, typed?: string) => void;
}

// Per-invocation destructive cards (OPEN/Developer mode) describe the exact
// command each time, phrased by the core as "…run: <command>". When we see that
// shape we split the command off and set it as a machine fact (mono, inset chip)
// so it reads as data, not prose. SAFE-mode cards have no "run: " and render
// exactly as before.
const RUN_PREFIX = "run: ";

// ---------------------------------------------------------------------------
// The hardened container (2026-08-08, Phase-3 review surface, owner decision 3).
//
// The app's CSP now carries `style-src 'self' 'unsafe-inline'` so Monaco can
// position its view lines. Script execution is untouched, so what that widening
// admits is CSS, and the residual risk it names is VISUAL SPOOFING — CSS that
// overlays, hides or re-labels UI. This card is the UI that matters: it is the
// surface a person reads before allowing Addison to act.
//
// Two things are done about it here, and it is worth being precise about which
// problem each one solves, because neither solves the whole of it:
//
//   * `isolate` (isolation: isolate) + `relative` gives the card its OWN stacking
//     context. Within its parent it is a single, atomic layer: nothing nested
//     inside a sibling can interleave with the card's internals, and the card's
//     own painting order cannot be rearranged from outside without also moving
//     the card. It does NOT stop a global `display:none` aimed at this element,
//     and nothing at this layer could.
//   * `data-consent-card` is the handle the tests use to assert the structural
//     half, which is the half that actually holds: the card NEVER has a
//     model-stylable ancestor. It renders in the widget rail, in the thread's
//     footer slot, or on App's fixed consent layer — always a SIBLING of the
//     message list, never a descendant of `.markdown-body` or a diagram.
//
// The real mitigation is upstream and is not in this file: the one path by which
// markup the app did not author reaches the DOM (`MermaidDiagram.tsx`) now has
// every stylesheet and style attribute stripped out of it (`lib/sanitizeSvg.ts`).
// This container is defence in depth behind that, not instead of it.
const CONSENT_CONTAINER = { "data-consent-card": "" } as const;
const CONSENT_CLASS =
  "relative isolate animate-[fadeRise_.2s_ease_both] rounded-[7px] border border-rail bg-panel";

function splitCommand(description: string): { lead: string; command: string | null } {
  const at = description.indexOf(RUN_PREFIX);
  if (at === -1) return { lead: description, command: null };
  const command = description.slice(at + RUN_PREFIX.length).trim();
  if (!command) return { lead: description, command: null };
  return { lead: description.slice(0, at + RUN_PREFIX.length).trimEnd(), command };
}

export function PermissionCard({ request, onRespond }: Props) {
  // The keyword gate is a different card, not a decorated one: its preview is the
  // defence, so it renders its own reading order rather than squeezing four facts
  // into the consequence sentence.
  if (request.arming) {
    return <ArmingCard request={request} arming={request.arming} onRespond={onRespond} />;
  }
  const { lead, command } = splitCommand(request.description);
  return (
    <div
      {...CONSENT_CONTAINER}
      className={CONSENT_CLASS + " px-3.5 py-3"}
    >
      <p className="m-0 text-[12px] font-medium leading-[1.45] text-ink">{request.label}</p>
      <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-ink-soft">{lead}</p>
      {command && (
        <p
          title={command}
          className="m-0 mt-2 truncate rounded-[4px] bg-paper px-2 py-1 font-mono text-[10.5px] text-ink"
        >
          {command}
        </p>
      )}
      {/* Both answers are REAL targets, on every screen. These two buttons are
          what authorises a destructive command, and they were 14.5px tall on
          desktop — under WCAG 2.2 SC 2.5.8's 24×24 floor, and separated only by
          hue, for readers of 54 and 68. They are now 30px (the send button's
          desktop size) and 44px under `md`, with Allow carrying the accent FILL
          so the one obvious choice stays obvious without the copy changing.
          Every sentence on this card is still the core's, verbatim. */}
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={() => onRespond(true)}
          className="inline-flex h-[30px] shrink-0 items-center justify-center rounded-menu bg-accent px-3.5 text-[12px] font-medium text-on-accent transition-opacity hover:opacity-90 max-md:h-11"
        >
          Allow
        </button>
        <button
          type="button"
          onClick={() => onRespond(false)}
          className="inline-flex h-[30px] shrink-0 items-center justify-center rounded-menu px-2.5 text-[12px] text-muted transition-colors hover:bg-track hover:text-ink max-md:h-11"
        >
          Not now
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The arming card (step 8 phase 3)
// ---------------------------------------------------------------------------

/** The core's attempt budget (`agent_core/automation_nonce.MAX_ATTEMPTS`), mirrored
 * so a FIRST ask says nothing about tries — a counter on a card nobody has answered
 * yet reads as a threat, and there is nothing to count yet. Below it, the count is
 * the one thing a person who mistyped needs. Mirrored rather than sent, and the
 * mismatch is harmless in both directions: a smaller core budget shows the line one
 * ask early (true, if premature), a larger one hides it for one ask (the ask nobody
 * has got wrong yet). */
const ARMING_MAX_ATTEMPTS = 3;

// Field names. MINE, not the core's — the core sends the facts, this side names the
// slots — so they are pinned byte-for-byte in permission-card-arming.test.tsx.
// Written for a 54- and a 68-year-old reading once, at speed: four questions in the
// order somebody asks them, each answerable by the line beside it.
const FIELD_NAME = "Automation";
const FIELD_WHEN = "When it runs";
const FIELD_COMMAND = "What it runs";
const FIELD_WHERE = "Where it's saved";

/** The box's label, and its accessible name. "to arm it" rather than "to confirm":
 * the verb has to name the consequence, because this is the sentence somebody reads
 * instead of the four above it when they are in a hurry. */
const CODE_LABEL = "Type this code to arm it";

/** Why there is no copy button. Says the true reason (the code is the reading), and
 * says the other true thing in passing: Addison cannot answer this for you. */
const CODE_NO_HELP = "Addison can't type this for you.";

const ARM_BUTTON = "Arm it";

/** After a wrong code. Neither sentence says the code was wrong — this side never
 * learned that; the core lowered the count and this renders the count. */
function attemptsLine(left: number): string | null {
  if (!Number.isInteger(left) || left < 1 || left >= ARMING_MAX_ATTEMPTS) return null;
  return left === 1
    ? "One more try — after that you'll need to ask Addison again."
    : `${left} more tries — after that you'll need to ask Addison again.`;
}

function ArmingCard({
  request,
  arming,
  onRespond,
}: {
  request: PermissionRequest;
  arming: NonNullable<PermissionRequest["arming"]>;
  onRespond: (allow: boolean, typed?: string) => void;
}) {
  const [typed, setTyped] = useState("");
  const codeId = useId();
  const inputId = useId();

  function submit(event: FormEvent) {
    event.preventDefault();
    // Emptiness only. NOT a comparison: whatever is in the box goes to the core
    // exactly as typed, and the core is what decides whether it matched.
    if (!typed.trim()) return;
    onRespond(true, typed);
  }

  const attempts = attemptsLine(arming.attemptsLeft);

  return (
    // A <form>, so Enter in the code box answers the card — the same press that
    // sends a message everywhere else in this app.
    <form
      onSubmit={submit}
      {...CONSENT_CONTAINER}
      className={CONSENT_CLASS + " px-3.5 py-3"}
    >
      <p className="m-0 text-[12px] font-medium leading-[1.45] text-ink">{request.label}</p>

      {/* The preview, top-down in the order a person asks: what is it, when does it
          run, what does it run, where does it live. A definition list, so the pairing
          survives a screen reader; stacked rather than columned, because the command
          needs the full width and must never be cut. */}
      <dl className="m-0 mt-2.5 flex flex-col gap-2">
        <Field label={FIELD_NAME}>
          <span className="text-[12px] leading-[1.55] text-ink">{arming.automationName}</span>
        </Field>
        <Field label={FIELD_WHEN}>
          {/* The core's sentence, printed as it arrives. Mono because when a job runs
              is a fact, and because the Automations section prints it the same way. */}
          <span className="font-mono text-[11px] text-ink">{arming.scheduleSentence}</span>
        </Field>
        <Field label={FIELD_COMMAND}>
          {/* WHOLE. No `truncate`, no `title` fallback standing in for the text: this
              is the line the ceremony exists to make somebody read, and a tooltip is
              not readable by a keyboard or a screen reader. It wraps instead. */}
          <span className="block whitespace-pre-wrap break-all rounded-[4px] bg-paper px-2 py-1 font-mono text-[11px] text-ink">
            {arming.command}
          </span>
        </Field>
        <Field label={FIELD_WHERE}>
          <span className="block break-all font-mono text-[10.5px] text-muted">
            {arming.installPath}
          </span>
        </Field>
      </dl>

      {/* The core's warning sentences, verbatim and one per line. This side never
          rewrites, merges or softens them — they are the two facts that make arming
          different from every other card, and the core owns their wording. */}
      {arming.warnings.length > 0 && (
        <ul className="m-0 mt-2.5 list-none border-t border-line p-0 pt-2.5">
          {arming.warnings.map((warning, i) => (
            <li key={i} className="mt-1 text-[12px] leading-[1.55] text-ink first:mt-0">
              {warning}
            </li>
          ))}
        </ul>
      )}

      {/* The live step, and the one accent rail on this card (the shape rule's 2px
          left rail marks what is active). The accent is otherwise spent only on the
          Arm button — never on the code, which is a fact to read and not an action. */}
      <div className="mt-2.5 border-t border-line pt-2.5">
        <div className="border-l-2 border-accent pl-3">
          <label htmlFor={inputId} className="block text-[12px] leading-[1.55] text-ink">
            {CODE_LABEL}
          </label>
          {/* Big, mono, letter-spaced: read off this surface and typed into the next
              one, possibly via paper. No scramble — the signature motion resolves
              text through wrong characters, and this is the one string in the app
              where a wrong character in flight is a mistyped code. */}
          <p id={codeId} className="m-0 mt-1.5 font-mono text-[18px] tracking-[.22em] text-ink">
            {arming.nonce}
          </p>
          <input
            id={inputId}
            type="text"
            autoComplete="off"
            spellCheck={false}
            aria-describedby={codeId}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            // `max-md:py-3` keeps the box a real touch target on a narrow window,
            // the way the two answers below it are (WCAG 2.2 SC 2.5.8).
            className="mt-2 w-full border-b border-line bg-transparent py-1.5 font-mono text-[13px] tracking-[.18em] text-ink focus:border-track-hi max-md:py-3"
          />
          <p className="m-0 mt-1.5 text-[11px] leading-[1.55] text-muted">{CODE_NO_HELP}</p>
          {attempts && (
            <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-ink-soft">{attempts}</p>
          )}
        </div>
      </div>

      {/* Same two answers, same targets as every other card (WCAG 2.2 SC 2.5.8).
          "Not now" is never disabled and never needs the code: refusing must always
          be the easiest thing on this card. */}
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <button
          type="submit"
          disabled={!typed.trim()}
          className="inline-flex h-[30px] shrink-0 items-center justify-center rounded-menu bg-accent px-3.5 text-[12px] font-medium text-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 max-md:h-11"
        >
          {ARM_BUTTON}
        </button>
        <button
          type="button"
          onClick={() => onRespond(false)}
          className="inline-flex h-[30px] shrink-0 items-center justify-center rounded-menu px-2.5 text-[12px] text-muted transition-colors hover:bg-track hover:text-ink max-md:h-11"
        >
          Not now
        </button>
      </div>
    </form>
  );
}

/** One preview fact: its name above it, in the muted 11px the surfaces use for a
 * label, and the fact itself below at full width. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] leading-[1.45] text-muted">{label}</dt>
      <dd className="m-0 mt-0.5">{children}</dd>
    </div>
  );
}
