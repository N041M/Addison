// Shared defensive parsing helpers for free-form JSON-RPC payloads.
//
// The Python core's result/notification shapes aren't pinned in protocol.ts, so
// the frontend coerces them carefully at every boundary. These two helpers were
// duplicated across App.tsx, ipc/client.ts, types/ui.ts, and
// components/RoutineLibrary.tsx; collapsing them here keeps the coercion rules in
// one place. This module is intentionally dependency-free — it sits below the
// low-level modules (types/ui.ts, ipc/client.ts) that import from it, so it must
// never import from anything that imports it.

/** Narrow an unknown value to a plain record, or null if it isn't an object. */
export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

/** One fill-in-each-time routine variable, as surfaced by the core. */
export interface RoutineVariable {
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
