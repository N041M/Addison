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
import type { ArtifactUnavailable, ProfileState } from "../types/ui";

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
