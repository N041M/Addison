// The webview reporting its own Content-Security-Policy violations.
//
// This ships PERMANENTLY, not as scaffolding for the Monaco measurement pass. The
// CSP is a floor the app cannot see enforced from the inside any other way: a
// blocked stylesheet, a blocked worker or a blocked fetch is silent in the DOM and
// loud only in a devtools console nobody has open in a packaged build. Every
// relaxation the app would need therefore announces itself BY NAME here instead of
// being discovered as "the editor renders wrong on Windows".
//
// It pushes into the existing developer diagnostics ring (ipc/client.ts) rather
// than inventing a channel: the entries are read in Settings → Diagnostics, which
// only the Developer profile renders. A Simple session captures them the same way
// and shows nobody, exactly as it already does for raw core errors.
//
// NOTHING here is a security control. A violation report is the browser telling us
// what it ALREADY refused; the refusal is the policy, and the policy is in
// shell/src-tauri/tauri.conf.json (pinned by tests/test_csp_is_pinned.py).

import { pushDiagnostic } from "../ipc/client";

/**
 * The fields worth keeping off a violation, and no more.
 *
 * `blockedURI` can be a full URL the page tried to reach, so this text is treated
 * as developer-only for the same reason `error.data.raw` is: it is diagnostic
 * detail, never a sentence to show a person in Simple.
 */
interface ViolationFacts {
  blockedURI: string;
  violatedDirective: string;
  sourceFile: string;
}

function facts(event: Event): ViolationFacts {
  // Read defensively rather than through `SecurityPolicyViolationEvent`: jsdom does
  // not implement the interface, and WebKit and Chromium disagree about which of
  // `violatedDirective` / `effectiveDirective` is populated. A missing field is a
  // less useful report, never a crash in the reporter for a report.
  const e = event as unknown as Record<string, unknown>;
  const str = (key: string): string => (typeof e[key] === "string" ? (e[key] as string) : "");
  return {
    blockedURI: str("blockedURI"),
    violatedDirective: str("violatedDirective") || str("effectiveDirective"),
    sourceFile: str("sourceFile"),
  };
}

/**
 * Start listening. Returns a teardown function; `main.tsx` calls this once at
 * startup and never tears it down, and the return value exists so a test can.
 *
 * Listens on `document` in the CAPTURE phase: the event is fired at the element
 * that caused the violation and bubbles, so a listener on `window` alone misses
 * nothing today but a stopped-propagation handler in between would. There is no
 * such handler; capture makes that not a thing to check.
 */
export function installCspViolationReporter(
  target: EventTarget = document,
): () => void {
  const onViolation = (event: Event) => {
    const { blockedURI, violatedDirective, sourceFile } = facts(event);
    pushDiagnostic({
      // Plain enough to read in a list, and it names the DIRECTIVE, because that
      // is the whole content of the answer: which line of the policy fired.
      message: `The app's content policy blocked something (${violatedDirective || "unknown directive"}).`,
      raw: [
        `violatedDirective=${violatedDirective || "?"}`,
        `blockedURI=${blockedURI || "?"}`,
        `sourceFile=${sourceFile || "?"}`,
      ].join(" "),
      at: Date.now(),
    });
  };
  target.addEventListener("securitypolicyviolation", onViolation, true);
  return () => target.removeEventListener("securitypolicyviolation", onViolation, true);
}
