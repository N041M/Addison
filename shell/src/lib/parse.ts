// Shared defensive parsing helpers for free-form JSON-RPC payloads.
//
// The Python core's result/notification shapes aren't pinned in protocol.ts, so
// the frontend coerces them carefully at every boundary. These two helpers were
// duplicated across App.tsx, ipc/client.ts, types/ui.ts, and
// components/RoutineLibrary.tsx; collapsing them here keeps the coercion rules in
// one place. This module is intentionally dependency-free — it sits below the
// low-level modules (types/ui.ts, ipc/client.ts) that import from it, so it must
// never import from anything that imports it.

// Type-only import: erased at build, so this file keeps its runtime
// dependency-free stance (types/ui.ts still imports `asRecord` from here at
// runtime; nothing flows back the other way once the types are stripped).
import type {
  ArtifactUnavailable,
  KnowledgeDocument,
  KnowledgeDocumentOnDisk,
  KnowledgeDocumentStatus,
  ProfileState,
} from "../types/ui";

/** Narrow an unknown value to a plain record, or null if it isn't an object. */
export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

// Parse `profile.get` defensively, like the other core payloads. `activeProfile`
// defaults to "simple" and every flag defaults to false, so a partial or missing
// payload degrades to the protected Simple surface rather than exposing anything.
// (Moved here from App.tsx so it can be unit-tested; behavior is unchanged.)
export function normalizeProfile(result: unknown): ProfileState | null {
  const obj = asRecord(result);
  if (!obj) return null;
  const profiles = Array.isArray(obj.profiles)
    ? obj.profiles.flatMap((p) => {
        const rp = asRecord(p);
        if (!rp || typeof rp.id !== "string") return [];
        return [
          {
            id: rp.id,
            label: typeof rp.label === "string" ? rp.label : rp.id,
            description: typeof rp.description === "string" ? rp.description : "",
            // Only an explicit `true` marks a profile advanced (kept behind the
            // disclosure). Absent on Simple/Developer → ordinary options, so their
            // serialized shape is unchanged.
            ...(rp.advanced === true ? { advanced: true } : {}),
          },
        ];
      })
    : [];
  const flags = asRecord(obj.flags) ?? {};
  // The policy mode ("safe" | "open") the active profile runs under (policy.py).
  // Anything unrecognized falls back to "safe" — an unknown surface never
  // escalates the safety model.
  const mode = obj.mode === "open" ? "open" : "safe";
  return {
    activeProfile: typeof obj.activeProfile === "string" ? obj.activeProfile : "simple",
    profiles,
    mode,
    flags: {
      exposeRoutinePlan: flags.exposeRoutinePlan === true,
      rawDiagnostics: flags.rawDiagnostics === true,
      headlessCli: flags.headlessCli === true,
      byokFirstOnboarding: flags.byokFirstOnboarding === true,
    },
  };
}

/**
 * Parse the `unavailable` marker a `routine.list` / `widget.list` row carries
 * when the active profile can't use it (owner decision 2026-08-06 — such rows are
 * listed and disabled, not hidden). Undefined when the key is absent, which is
 * the shape of every usable row and of every payload from an older core.
 *
 * A row is only treated as unavailable when it says WHY in a sentence a person
 * can read: no `message`, no disabled state. A row disabled with nothing to show
 * for it is the "where did my stuff go?" bug wearing a different hat — the person
 * would see their routine sitting there, inert, with no explanation.
 *
 * `reason` is passed through as a plain string, unknown slugs included: it is a
 * machine-readable label for a cause the core owns, and this frontend must not
 * decide that a cause it has not heard of means the row is fine.
 */
export function normalizeUnavailable(raw: unknown): ArtifactUnavailable | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  const { reason, message } = record;
  if (typeof message !== "string" || !message.trim()) return undefined;
  return { reason: typeof reason === "string" ? reason : "", message };
}

/** One fill-in-each-time routine variable, as surfaced by the core. */
interface RoutineVariable {
  name: string;
  prompt: string;
  default: string | null;
}

// Normalize a routine's free-form `variables` payload into a typed list. Accepts
// the raw field value (which may not be an array), drops any entry without a
// string name, and fills a plain-language prompt fallback. Shared by the routine
// proposal, the rail's routine copy, and the Routines library.
export function normalizeVariables(raw: unknown): RoutineVariable[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((v) => {
    const rv = asRecord(v);
    if (!rv || typeof rv.name !== "string") return [];
    return [
      {
        name: rv.name,
        prompt: typeof rv.prompt === "string" ? rv.prompt : `Value for ${rv.name}?`,
        default: typeof rv.default === "string" ? rv.default : null,
      },
    ];
  });
}

// ---------------------------------------------------------------------------
// Your documents — the knowledge base's rows (knowledge retrieval, phase 3).
//
// Defensive in the same way `parseMcpServers` is, and failing in the same
// direction: every unrecognised field lands on "Addison hasn't got that far",
// never on a claim that a document is indexed and searchable. The page exists so
// a person can see what Addison has read; overstating there is the one way for it
// to be useless.
// ---------------------------------------------------------------------------

/** The three states a row may arrive in — the whole of `KnowledgeDocumentStatus`.
 * Anything else becomes "pending", which reads as "Addison hasn't finished
 * reading this" and offers Try again. Falling back to "indexed" would put "Ready.
 * 3 passages." under a document nothing can search. */
const KNOWLEDGE_STATUSES = new Set<string>(["pending", "indexed", "failed"]);

/** The four on-disk answers, whole. Anything else becomes "unknown" — the value
 * the core itself sends when it could not compare (no shell bridge, or no digest),
 * and the only one that claims nothing. "missing" would tell somebody their file
 * had gone; "changed" would send them to a picker for no reason. */
const KNOWLEDGE_ON_DISK = new Set<string>(["same", "changed", "missing", "unknown"]);

/** A count that is a real, non-negative number, or 0. A junk count must not become
 * "Ready. NaN passages." on a row somebody is reading. */
function knowledgeCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

/** One `knowledge.list` row, or `null` when it isn't usable.
 *
 * Fails CLOSED on `parseMcpServerRow`'s reasoning for the two fields a row cannot
 * work without: no id means Remove and Update would have nothing to send, and no
 * name means a row whose Remove button is a mystery.
 *
 * It does NOT drop a row for a missing `path`, which is where it parts company
 * with the MCP parser. There, the address is a claim about what Addison reaches,
 * so a bad one is worth losing the row over. Here the path is only where the
 * person's own file sits — and a dropped row is an indexed document that goes on
 * answering questions while the one page that could remove it pretends it does not
 * exist. The row renders without its path instead.
 */
function parseKnowledgeDocumentRow(value: unknown): KnowledgeDocument | null {
  const row = asRecord(value);
  if (!row || typeof row.id !== "string" || !row.id) return null;
  if (typeof row.displayName !== "string" || !row.displayName) return null;
  const status =
    typeof row.status === "string" && KNOWLEDGE_STATUSES.has(row.status)
      ? (row.status as KnowledgeDocumentStatus)
      : "pending";
  const onDisk =
    typeof row.onDisk === "string" && KNOWLEDGE_ON_DISK.has(row.onDisk)
      ? (row.onDisk as KnowledgeDocumentOnDisk)
      : "unknown";
  return {
    id: row.id,
    displayName: row.displayName,
    path: typeof row.path === "string" ? row.path : "",
    status,
    // The sentence rides only on the row it explains. A `detail` left on an
    // indexed row by an older core would otherwise print a past failure under a
    // document that is working.
    detail: status === "failed" && typeof row.detail === "string" ? row.detail : null,
    chunkCount: knowledgeCount(row.chunkCount),
    flaggedChunks: knowledgeCount(row.flaggedChunks),
    byteSize: knowledgeCount(row.byteSize),
    addedAt: knowledgeCount(row.addedAt),
    indexedAt:
      typeof row.indexedAt === "number" && Number.isFinite(row.indexedAt) ? row.indexedAt : null,
    onDisk,
  };
}

/** Parse `knowledge.list` → the documents Addison has been given, newest first
 * (the core's order, which this side never re-sorts). Unusable rows are dropped;
 * junk never throws. */
export function parseKnowledgeDocuments(result: unknown): KnowledgeDocument[] {
  const obj = asRecord(result);
  const list = obj && Array.isArray(obj.documents) ? (obj.documents as unknown[]) : [];
  const out: KnowledgeDocument[] = [];
  for (const item of list) {
    const row = parseKnowledgeDocumentRow(item);
    if (row) out.push(row);
  }
  return out;
}

/** `knowledge.add` / `knowledge.reindex` / `knowledge.remove` → what happened.
 *
 * THREE OUTCOMES, and the third is why this is not the mcp mutation shape.
 * `ok:true` means it landed (add and reindex carry the row they wrote).
 * `ok:false` with `error` is a refusal in the core's own plain sentence, which the
 * panel prints verbatim. `ok:false` with `cancelled` is the person closing the
 * file picker — not a failure, not an error line, and nothing to say about it at
 * all; flattening it into the refusal branch would answer a deliberate "never
 * mind" with a sentence explaining what went wrong.
 *
 * An `ok:true` whose row is unusable keeps its `ok` and simply carries no
 * document: every caller re-reads the list afterwards, so the truth arrives a
 * moment later either way, and degrading to a failure would print an error about
 * something that worked. */
export interface KnowledgeMutationResult {
  ok: boolean;
  document?: KnowledgeDocument;
  cancelled?: boolean;
  error?: string;
}

export function parseKnowledgeMutation(result: unknown): KnowledgeMutationResult {
  const obj = asRecord(result);
  if (obj?.ok === true) {
    const document = parseKnowledgeDocumentRow(obj.document);
    return document ? { ok: true, document } : { ok: true };
  }
  return {
    ok: false,
    // Only ever true on a refusal, and only when the core says so — a missing
    // field means an ordinary refusal, which does get its line.
    cancelled: obj?.cancelled === true ? true : undefined,
    error: typeof obj?.error === "string" ? obj.error : undefined,
  };
}
