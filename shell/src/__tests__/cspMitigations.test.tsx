// What the CSP widening cost, and what was done about it in the same change.
// (Phase-3 review-surface plan, "The one thing to understand before starting: the
// CSP", and owner decision 3.)
//
// THE CHANGE. `shell/src-tauri/tauri.conf.json` went from a single
// `default-src 'self'` to an explicit policy that adds ONE relaxation —
// `style-src 'self' 'unsafe-inline'` — and four tightenings. The relaxation is
// unavoidable for Monaco: it positions every view line, cursor and selection
// overlay with an inline `style="…"` attribute, and a nonce does not apply to
// style attributes at all. `tests/test_csp_is_pinned.py` pins the policy itself.
//
// THE COST. `script-src` stays `'self'`, so this admits no code execution and no
// new exfiltration path. What it admits is CSS, and the residual risk is therefore
// VISUAL SPOOFING: CSS that overlays, hides or re-labels UI. In an app whose safety
// model is *a consent card the person reads before approving*, that is a nameable
// threat.
//
// THE MITIGATION, in three parts, one per section below:
//   1. The one path by which markup this app did not author reaches the DOM —
//      mermaid's rendered SVG — is stripped of every stylesheet, style attribute
//      and event handler before injection. Those were BLOCKED by the old policy,
//      so this keeps diagrams rendering exactly as they do today and closes the
//      path the widening would otherwise open.
//   2. A source-level pin, so the sanitizer cannot be bypassed by a second
//      injection site added later without this failing.
//   3. The consent card's own container, and the structural fact underneath it:
//      it is never a descendant of anything a model can style.
//
// WHAT NONE OF THIS COVERS, stated here because a mitigation that oversells itself
// is worse than none: the widening is GLOBAL, so a Simple-profile session carries
// `'unsafe-inline'` for a screen it can never reach; nothing here constrains CSS
// delivered by a first-party asset, which `'self'` admits by definition; and the
// live `securitypolicyviolation` check belongs to the manual pass, since only a
// real webview enforces a policy.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { stripInjectedCss } from "../lib/sanitizeSvg";
import { installCspViolationReporter } from "../lib/cspReport";
import { pushDiagnostic, subscribeDiagnostics } from "../ipc/client";
import { PermissionCard } from "../components/PermissionCard";
import { ChatThread } from "../components/ChatThread";
import type { DisplayMessage } from "../types/ui";

afterEach(cleanup);

// jsdom has no layout, so no scrollIntoView; the thread keeps the newest message
// in view with it.
beforeEach(() => {
  Element.prototype.scrollIntoView = () => {};
});

// ===========================================================================
// 1. The injected-markup sanitizer
// ===========================================================================

describe("stripInjectedCss", () => {
  it("removes a <style> block, which is the one that escapes the diagram", () => {
    // A `<style>` element injected ANYWHERE applies to the whole document, so a
    // diagram carrying one could select the permission card by class and hide it.
    // A mermaid `classDef` becomes exactly this. Kills: injecting mermaid's SVG
    // whole once `style-src` admits inline styles.
    const out = stripInjectedCss(
      '<svg><style>[data-consent-card]{display:none}</style><g><text>hi</text></g></svg>',
    );
    expect(out).not.toContain("<style");
    expect(out).not.toContain("display:none");
    expect(out).toContain("hi");
  });

  it("removes style ATTRIBUTES too — one is enough to cover the window", () => {
    // A style attribute cannot select another element, but `position:fixed;
    // inset:0;z-index:9999` on one node covers the screen just as well. A mermaid
    // `style A fill:…` lands here. Kills: stripping only `<style>` on the reasoning
    // that attributes are element-scoped.
    const out = stripInjectedCss(
      '<svg><rect style="position:fixed;inset:0;z-index:9999"></rect></svg>',
    );
    expect(out).not.toContain("style=");
    expect(out).not.toContain("position:fixed");
  });

  it("removes scripts and event handlers, without trusting mermaid to have done it", () => {
    // `securityLevel: "strict"` is supposed to have taken these already. A
    // sanitizer that trusts an upstream library to have sanitized is not one.
    const out = stripInjectedCss(
      '<svg><script>fetch("//x")</script><g onclick="steal()"><text>hi</text></g></svg>',
    );
    expect(out).not.toContain("<script");
    expect(out).not.toContain("onclick");
    expect(out).toContain("hi");
  });

  it("keeps the geometry, which is what the diagram actually is", () => {
    // Kills: over-stripping into an empty box. Mermaid computes layout into
    // attributes (`viewBox`, `x`, `transform`), not into CSS, so a stripped SVG is
    // the same picture — which is the point: today's CSP already blocks the CSS,
    // so this changes NOTHING about how diagrams look.
    const out = stripInjectedCss(
      '<svg viewBox="0 0 10 10"><g transform="translate(2,3)"><rect x="1" width="4"></rect></g></svg>',
    );
    // Note the CASE: `viewBox` survives, which is the SVG attribute-adjustment
    // step of the HTML parser doing its job. A sanitizer that lower-cased it would
    // hand back a picture with no coordinate system.
    expect(out).toContain('viewBox="0 0 10 10"');
    expect(out).toContain('transform="translate(2,3)"');
    expect(out).toContain('x="1"');
  });

  it("drops a link target that leaves the document, and keeps the one that does not", () => {
    // Not a hole being closed: `script-src 'self'` refuses a `javascript:` URL and
    // this webview opens no URLs at all. It is this file's stated stance — trust
    // nothing upstream — applied to the last attribute class that was taken on
    // trust. Kills: leaving `href`/`xlink:href` untouched, and kills the
    // over-correction that strips the fragment references SVG draws itself with
    // (`<use href="#arrow">` is how an arrowhead gets on screen).
    const out = stripInjectedCss(
      '<svg><a href="javascript:steal()"><text>hi</text></a>' +
        '<image xlink:href="https://example.test/pixel.png"></image>' +
        '<use href="#arrowhead"></use></svg>',
    );
    expect(out).not.toContain("javascript:");
    expect(out).not.toContain("example.test");
    expect(out).toContain('href="#arrowhead"');
    expect(out).toContain("hi");
  });

  it("is not fooled by nesting or by a '>' inside an attribute", () => {
    // Why this parses rather than matching a regular expression: a regex over
    // markup loses to nesting and to attribute values, and this is the one function
    // in the app whose job is to be right about hostile input.
    const out = stripInjectedCss(
      '<svg><g data-label="a > b" style="fill:red"><style>x{y:z}</style></g></svg>',
    );
    expect(out).not.toContain("style");
    expect(out).toContain('data-label="a > b"');
  });
});

describe("MermaidDiagram routes its output through the sanitizer", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("injects the stripped SVG, not mermaid's own", async () => {
    // The end-to-end half of the pin below: the component could import the
    // sanitizer and then forget to use its result.
    vi.doMock("mermaid", () => ({
      default: {
        initialize: () => {},
        render: async () => ({
          svg: '<svg><style>body{display:none}</style><text>diagram</text></svg>',
        }),
      },
    }));
    const { MermaidDiagram } = await import("../components/MermaidDiagram");
    const { container } = render(<MermaidDiagram code="graph TD; A-->B" />);
    await waitFor(() => expect(container.querySelector("text")).toBeTruthy());
    expect(container.querySelector("style")).toBeNull();
    expect(container.innerHTML).not.toContain("display:none");
    vi.doUnmock("mermaid");
  });
});

// ===========================================================================
// 2. The source-level pin
// ===========================================================================

const SRC = join(process.cwd(), "src");

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "__tests__") continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) sourceFiles(path, out);
    else if (/\.(ts|tsx)$/.test(entry)) out.push(path);
  }
  return out;
}

describe("the app has exactly one place markup it did not author reaches the DOM", () => {
  it("finds one dangerouslySetInnerHTML, in MermaidDiagram, behind the sanitizer", () => {
    // Kills: a SECOND injection site added later. The sanitizer above closes the
    // path that exists today and can do nothing about one added tomorrow, so the
    // count is what holds the line — this fails on the commit that adds one, in a
    // file whose header explains why that matters now that `style-src` is loose.
    const files = sourceFiles(SRC);
    // The walk finding nothing would make this test pass while proving nothing.
    expect(files.length).toBeGreaterThan(20);
    const injecting = files.filter((f) =>
      /dangerouslySetInnerHTML=\{/.test(readFileSync(f, "utf8")),
    );
    expect(injecting.map((f) => f.replace(SRC + "/", ""))).toEqual([
      "components/MermaidDiagram.tsx",
    ]);
    const source = readFileSync(injecting[0], "utf8");
    expect(source).toContain('from "../lib/sanitizeSvg"');
    expect(source).toContain("stripInjectedCss(out)");
  });
});

// ===========================================================================
// 3. The consent card
// ===========================================================================

const REQUEST = {
  toolId: "write_project_file",
  label: "Let Addison change notes.txt?",
  description: "Addison would like to write to notes.txt.",
  riskTier: "medium" as const,
};

describe("the consent card's container", () => {
  it("is a stacking context of its own, and findable", () => {
    // `isolate` + `relative` make the card ONE atomic layer within its parent:
    // nothing nested in a sibling can interleave with its internals. It does NOT
    // stop a global rule aimed at this element, and nothing at this layer could —
    // which is why section 1 exists. Kills: dropping the container while keeping
    // the claim.
    const { container } = render(<PermissionCard request={REQUEST} onRespond={() => {}} />);
    const card = container.querySelector("[data-consent-card]");
    expect(card).toBeTruthy();
    expect(card?.className).toContain("isolate");
    expect(card?.className).toContain("relative");
  });

  it("is never rendered INSIDE model-authored content", () => {
    // The structural half, and the half that actually holds: the card is a SIBLING
    // of the message list — it rides in the thread's footer slot, in the widget
    // rail, or on App's fixed consent layer. Kills: someone moving it into a
    // message row, where a rendered answer's own markup (and its diagram) would be
    // an ancestor of the thing the person is being asked to approve.
    const messages: DisplayMessage[] = [
      {
        id: "m1",
        role: "assistant",
        content: "Here is a plan.\n\n```mermaid\ngraph TD; A-->B\n```\n",
      },
    ];
    const { container } = render(
      <ChatThread
        messages={messages}
        onRetry={() => {}}
        retryAvailable={false}
        onRewindTo={() => {}}
        footer={<PermissionCard request={REQUEST} onRespond={() => {}} />}
      />,
    );
    const card = container.querySelector("[data-consent-card]");
    expect(card).toBeTruthy();
    expect(card?.closest(".markdown-body")).toBeNull();
    expect(card?.closest(".mermaid-diagram")).toBeNull();
    // And it is still the real card, answerable.
    expect(screen.getByText("Let Addison change notes.txt?")).toBeTruthy();
  });
});

// ===========================================================================
// The violation reporter — it ships permanently, not as measurement scaffolding
// ===========================================================================

describe("securitypolicyviolation reporting", () => {
  /** Take whatever an earlier test left waiting for a subscriber, so each test below
   * starts with an empty buffer. Subscribing IS the drain — see `pushDiagnostic`. */
  beforeEach(() => {
    subscribeDiagnostics(() => {})();
  });

  /** Fire one violation at the installed reporter and return what reached the ring. */
  function report(blockedURI: string, violatedDirective: string) {
    const seen: { message: string; raw: string }[] = [];
    const stop = installCspViolationReporter();
    const unsubscribe = subscribeDiagnostics((entry) => seen.push(entry));
    const event = new Event("securitypolicyviolation", { bubbles: true });
    Object.assign(event, { blockedURI, violatedDirective, sourceFile: "" });
    document.dispatchEvent(event);
    stop();
    unsubscribe();
    return seen;
  }

  it("says nothing about the app's own IPC, which the policy blocks by design", () => {
    // Tauri invokes commands with fetch(convertFileSrc(cmd,'ipc')) and does NOT inject
    // those sources into the CSP (set_csp augments only script-src and style-src), so
    // `connect-src 'self'` blocks it, Tauri falls back to postMessage, and the app runs
    // as it always has — the old `default-src 'self'` blocked it identically. What is
    // new is that this reporter would file it on every launch, and the entry it would
    // teach a reader to scroll past is the blocked worker this exists to surface.
    //
    // Kills: dropping the isTauriIpcViolation guard.
    expect(report("ipc://localhost/plugin:event|listen", "connect-src 'self'")).toHaveLength(0);
    expect(report("http://ipc.localhost/plugin:path|resolve", "connect-src")).toHaveLength(0);
    expect(report("ipc", "connect-src 'self'")).toHaveLength(0);
  });

  it("still reports a real violation, a lookalike URI, and the same URI elsewhere", () => {
    // The three ways a blanket filter would go wrong, each its own mutation: matching
    // by substring (the lookalike), ignoring the directive (the third case), or
    // suppressing connect-src wholesale (the first).
    expect(report("https://cdn.example/x.js", "script-src 'self'")).toHaveLength(1);
    expect(report("https://evil.example/?u=ipc://localhost", "connect-src 'self'")).toHaveLength(1);
    expect(report("ipc://localhost", "img-src 'self' data:")).toHaveLength(1);
  });

  it("puts the blocked URI, the directive and the source file into the diagnostics ring", () => {
    // Kills: deleting the listener once Monaco worked. A CSP is a floor the app
    // cannot see enforced from the inside any other way — a blocked stylesheet or
    // worker is silent in the DOM and loud only in a devtools console nobody has
    // open in a packaged build. Every relaxation the app would need announces
    // itself BY NAME here instead of being found as "it renders wrong on Windows".
    //
    // INSTALLED FIRST, SUBSCRIBED SECOND — the order `main.tsx` and App actually
    // run in. Subscribing first is the one ordering in which a reporter that drops
    // what it catches still passes, which is how the discard below survived review.
    const seen: { message: string; raw: string }[] = [];
    const stop = installCspViolationReporter();
    const unsubscribe = subscribeDiagnostics((entry) => seen.push(entry));

    const event = new Event("securitypolicyviolation", { bubbles: true });
    Object.assign(event, {
      blockedURI: "blob:",
      violatedDirective: "worker-src",
      sourceFile: "app://assets/monaco.js",
    });
    document.dispatchEvent(event);

    expect(seen).toHaveLength(1);
    expect(seen[0].message).toContain("worker-src");
    expect(seen[0].raw).toContain("blockedURI=blob:");
    expect(seen[0].raw).toContain("sourceFile=app://assets/monaco.js");

    stop();
    document.dispatchEvent(new Event("securitypolicyviolation", { bubbles: true }));
    expect(seen).toHaveLength(1);
    unsubscribe();
  });

  it("survives an event with none of the fields it wants", () => {
    // WKWebView, WebView2 and WebKitGTK disagree about which of
    // `violatedDirective` / `effectiveDirective` is populated, and jsdom implements
    // neither. Kills: reading through `SecurityPolicyViolationEvent` and throwing
    // inside the reporter — a crash while reporting a crash.
    const seen: { raw: string }[] = [];
    const stop = installCspViolationReporter();
    const unsubscribe = subscribeDiagnostics((entry) => seen.push(entry));
    document.dispatchEvent(new Event("securitypolicyviolation", { bubbles: true }));
    expect(seen).toHaveLength(1);
    expect(seen[0].raw).toContain("violatedDirective=?");
    stop();
    unsubscribe();
  });

  it("keeps what it catches BEFORE anything is listening, and replays it", () => {
    // The reporter is installed in `main.tsx` before the app renders, precisely so a
    // violation raised while the first chunk loads is captured — and the ring's only
    // subscriber is inside an App effect gated on the engine being connected, which
    // lands much later. Fanning out to live handlers alone therefore DISCARDED
    // exactly the class this exists to catch: the blocked worker and the blocked
    // stylesheet at load. Kills: `pushDiagnostic` dropping an entry when nobody is
    // subscribed, and kills a replay that fires before the subscriber is added.
    const stop = installCspViolationReporter();
    const event = new Event("securitypolicyviolation", { bubbles: true });
    Object.assign(event, { blockedURI: "blob:", violatedDirective: "worker-src" });
    document.dispatchEvent(event);

    const seen: { message: string; raw: string }[] = [];
    const unsubscribe = subscribeDiagnostics((entry) => seen.push(entry));
    expect(seen).toHaveLength(1);
    expect(seen[0].raw).toContain("blockedURI=blob:");
    stop();
    unsubscribe();
  });

  it("catches the gap on a disconnect too, not only the one at startup", () => {
    // App tears its subscription down and re-adds it on every engine disconnect, so
    // "nobody is listening" is a state this window enters again and again — not a
    // startup special case. Kills: buffering only until the first subscriber ever
    // appears.
    const first: unknown[] = [];
    const unsubscribeFirst = subscribeDiagnostics((entry) => first.push(entry));
    unsubscribeFirst();
    pushDiagnostic({ message: "while nobody was listening", raw: "raw", at: 1 });
    const second: { message: string }[] = [];
    const unsubscribeSecond = subscribeDiagnostics((entry) => second.push(entry));
    expect(second.map((e) => e.message)).toEqual(["while nobody was listening"]);
    expect(first).toHaveLength(0);
    unsubscribeSecond();
  });

  it("cannot grow without end on a machine whose engine never connects", () => {
    // The other half of the same decision: with no subscriber ever, every violation
    // would otherwise accumulate for the life of the window. Twenty is far more than
    // a working build produces and more than the Settings panel keeps; full means the
    // OLDEST goes, which is the panel's own preference. Kills: an unbounded array.
    for (let i = 0; i < 25; i += 1) {
      pushDiagnostic({ message: `violation ${i}`, raw: "raw", at: i });
    }
    const seen: { message: string }[] = [];
    const unsubscribe = subscribeDiagnostics((entry) => seen.push(entry));
    expect(seen).toHaveLength(20);
    expect(seen[0].message).toBe("violation 5");
    expect(seen[19].message).toBe("violation 24");
    unsubscribe();
  });
});
