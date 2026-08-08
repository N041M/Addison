// Stripping CSS out of injected markup — the mitigation that pays for the CSP
// widening (Phase-3 review-surface plan, owner decision 3).
//
// THE THREAT, stated plainly. The app's policy now carries `style-src 'self'
// 'unsafe-inline'`, because Monaco positions every view line and cursor with an
// inline `style="…"` attribute and a nonce cannot cover a style attribute. Script
// execution is untouched at `'self'`, so what the widening actually admits is CSS —
// and the residual risk is therefore VISUAL SPOOFING: CSS that overlays, hides or
// re-labels UI. In an app whose safety model is *a consent card the person reads
// before approving*, that is a nameable threat rather than a theoretical one.
//
// The app has exactly ONE path by which markup it did not author reaches the DOM:
// `MermaidDiagram.tsx`'s `dangerouslySetInnerHTML` over mermaid's rendered SVG. That
// SVG is model-influenced — a flowchart's `style A fill:#f9f` and `classDef` land in
// a `style` attribute and a `<style>` block respectively — and a `<style>` element
// injected anywhere in the document applies to the WHOLE document, so a diagram
// could carry a rule that selects the consent card. A `style` attribute cannot
// select anything else, but `position:fixed;inset:0` on one diagram node covers the
// window just as well.
//
// So both go. What that costs is nothing: `style-src 'self'` blocked both of these
// already, in the shipped app, which is why the plan warned that widening would
// "change how diagrams look". Stripping them keeps diagram rendering byte-identical
// to what people have been looking at, and closes the injection path instead of
// opening it. The `<style>` block mermaid emits is its own theme, not the person's
// content, so nothing a *user* asked for is lost either.
//
// WHAT THIS DOES NOT COVER, and belongs in the PR description rather than in a
// comment nobody reads: the widening is global, so a Simple-profile session carries
// `'unsafe-inline'` for a screen it can never reach; this closes the one injection
// path that exists TODAY and cannot stop a future one (see the source-level pin in
// `src/__tests__/cspMitigations.test.tsx`, which fails when a second
// `dangerouslySetInnerHTML` appears); and it says nothing about CSS delivered by a
// compromised first-party asset, which `'self'` admits by definition.

/** Attributes that carry behaviour rather than data, dropped wholesale. */
const EVENT_ATTR = /^on/i;

/**
 * Return `markup` with every stylesheet, style attribute and event handler removed.
 *
 * Parsed into an inert `<template>` rather than matched with a regular expression:
 * a regex over markup loses to nesting, to attribute values containing `>`, and to
 * every encoding trick there is, and this is the one function in the app whose job
 * is to be right about hostile input. A template's content is parsed by the real
 * HTML parser and is inert — no resource fetches, no script execution, no layout —
 * so nothing runs on the way through.
 *
 * `<script>` and `<link>` are removed too. Mermaid's `securityLevel: "strict"` is
 * supposed to have taken those already; a sanitizer that trusts an upstream library
 * to have sanitized is not one.
 */
export function stripInjectedCss(markup: string): string {
  if (typeof document === "undefined") return "";
  const template = document.createElement("template");
  template.innerHTML = markup;
  const root = template.content;

  root.querySelectorAll("style, script, link").forEach((el) => el.remove());

  root.querySelectorAll("*").forEach((el) => {
    // `getAttributeNames()` is a live-ish snapshot; copy before mutating.
    for (const name of [...el.getAttributeNames()]) {
      if (name.toLowerCase() === "style" || EVENT_ATTR.test(name)) {
        el.removeAttribute(name);
      }
    }
  });

  return template.innerHTML;
}
