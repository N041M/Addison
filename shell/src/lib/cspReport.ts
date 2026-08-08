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
// This is installed BEFORE the app renders and the ring's only subscriber arrives
// with the engine, so `pushDiagnostic` holds what it is given while nobody is
// listening and replays it to the first subscriber. Without that, every violation
// between page load and engine-ready — the blocked worker and the blocked
// stylesheet, which is the class this exists to catch — was discarded by the very
// ordering that makes the early install worth doing.
//
// NOTHING here is a security control. A violation report is the browser telling us
// what it ALREADY refused; the refusal is the policy, and the policy is in
// shell/src-tauri/tauri.conf.json (pinned by tests/test_csp_is_pinned.py).

import { pushDiagnostic } from "../ipc/client";

/**
 * Tauri's own IPC endpoints, which `connect-src 'self'` does not admit.
 *
 * `scripts/ipc-protocol.js` invokes commands with `fetch(convertFileSrc(cmd, 'ipc'))`
 * — `ipc://localhost` on macOS and Linux, `http(s)://ipc.localhost` on Windows — and
 * Tauri does NOT inject those sources into the policy: `manager::set_csp` augments
 * only `script-src` and `style-src` (verified against tauri 2.11.5). So that fetch is
 * blocked, Tauri falls back to `window.ipc.postMessage`, and the app runs exactly as
 * it always has — the old `default-src 'self'` blocked it identically. Nothing is
 * broken; it is only newly AUDIBLE, because this reporter did not exist before.
 *
 * One violation per webview load would then sit in Diagnostics forever (the fallback
 * latches on first failure and never retries), which is precisely the noise that
 * teaches a reader to ignore the list — and the entry it would mask is the blocked
 * worker or stylesheet this reporter exists to surface.
 *
 * A NAMED ALLOWANCE, never a blanket filter: both the directive and the endpoint must
 * match, and the endpoint must match at a `/` or string boundary, so
 * `https://evil.example/?u=ipc://localhost` is still reported. Widening `connect-src`
 * to admit these for real is the alternative, and it is the owner's call — recorded in
 * docs/KNOWN-GAPS.md, and refused by tests/test_csp_is_pinned.py on purpose so that
 * adopting it cannot happen quietly.
 */
const TAURI_IPC_ENDPOINTS = ["ipc://localhost", "http://ipc.localhost", "https://ipc.localhost"];

function isTauriIpcViolation(blockedURI: string, violatedDirective: string): boolean {
  // WebKit puts the whole directive INCLUDING its sources in `violatedDirective`;
  // Chromium puts the bare name there. Compare the first token so both agree.
  if (violatedDirective.trim().split(/\s+/)[0] !== "connect-src") return false;
  // A non-network scheme may be reported as the scheme alone.
  if (blockedURI === "ipc") return true;
  return TAURI_IPC_ENDPOINTS.some(
    (endpoint) => blockedURI === endpoint || blockedURI.startsWith(`${endpoint}/`),
  );
}

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
    // The app's own IPC, blocked by design and reported on every launch — see
    // TAURI_IPC_ENDPOINTS. Dropped so a real violation is still visible in a list
    // somebody will actually read.
    if (isTauriIpcViolation(blockedURI, violatedDirective)) return;
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
