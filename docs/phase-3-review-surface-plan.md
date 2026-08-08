# Phase 3 — The Review Surface: a bespoke IDE for the Developer/OPEN profile

**Status:** PLAN, approved 2026-07-25. **BUILT — all five Build sections landed
2026-08-08** (§1 read paths, §2 the diff, §3 per-file revert, §4 the screen, §5 the
Monaco skin). What remains is not code: the **§13c manual pass** in
[`TESTING-CHECKLIST.md`](TESTING-CHECKLIST.md), which is the only place the CSP is
enforced by a real webview, and the follow-up list at the end of §5's shipped notes.
Everything the plan waited on had landed first: the three sequencing prerequisites
(step 6 widget capability tiers and step 7 the MCP client on 2026-08-06/07, step 8's
four phases on 2026-08-07) and the three fixes listed under "Prerequisites" below, each
in its own PR on 2026-08-08 — the resolved permission detail, the read ceiling, and the
prune wiring. [`../ROADMAP.md`](../ROADMAP.md) owns status — trust it over this line.

> **This redefines what "Phase 3" means.** Before this plan, Phase 3 meant packaging /
> signing / notarisation / auto-updater / binary restore / Secure-Enclave identity, and
> four documents scoped it that way (`addison-design-doc.md` §11, `architecture.md`,
> `addison-engineering-spec.md` §11's Phase-3 note, `HANDOFF.md`). This plan adds a
> Developer *surface* to that phase, and **the redefinition is now written into every
> document that defines the phase** — completed 2026-08-08, which discharges amendment
> §14's authoritative-docs-first rule for it.
>
> It took three passes, not the one this paragraph used to claim, and that is why the
> fact is now a row in [`../tests/doc_claims.py`](../tests/doc_claims.py) rather than a
> promise: `ee38dbe` (2026-07-25 — see [`BUILD-LOG.md`](BUILD-LOG.md)) reached the
> design doc and `HANDOFF.md`, a later docs pass reached the engineering spec,
> `architecture.md` was missed entirely for two weeks, and `ROADMAP.md` — which owns
> status — grew a *fifth* packaging-only definition after this plan was written. No line
> numbers are given for those documents: the ones this paragraph used to carry were
> stale within a week.

## Context

Phase-2 step 5 shipped the coding harness: two OPEN-only, path-bounded file tools
(`read_project_file`, `write_project_file`), a `workspace_trust` table, and a
`workspace.*` RPC. Addison can now read and edit a real project directory.

What it cannot do is **show you what it did**. Today the only evidence of an edit is
a one-line Activity Panel entry naming a basename, and the only recovery is a LIFO
"Undo last action" button in the chat header. The amendment's promise for this
profile is *"the harness you cannot brick, and cannot fall out of"* (§2) — but a
person cannot judge whether they need to roll back a change they were never shown.
G3 gives a guaranteed way back for *config*; nothing gives a way back for *code* that
the person can actually see first.

This builds that surface: a file tree, a read-only viewer, and a real diff of every
edit Addison has made that is still live on disk, with per-file revert. It is a
**flow and trust layer, not a new execution surface** — amendment §8.1's own words for
what the harness is supposed to be.

### Scope, as decided

| | |
|---|---|
| **In** | File tree over trusted roots · read-only Monaco viewer · Monaco diff (before/after) of Addison's edits · per-file revert · Developer/Custom only |
| **Out** | User typing into files · save · integrated terminal · run/test button · hunk-level revert · language services (no completions, no diagnostics) |
| **Engine** | Monaco, with a CSP widening (owner decision; I recommended against it and was overruled — the plan below narrows and pins the widening as far as it goes) |
| **Placement** | Third screen: `screen: "chat" \| "settings" \| "code"` in `shell/src/App.tsx:82`, replacing the chat column inside `<main>`; sidebar persists |
| **Sequencing** | After Phase-2 steps 6 (widget tiers), 7 (MCP client), 8 (keyword gate) |

"Editing" is deliberately deferred, not abandoned. Review-first means this whole wave
adds **zero new execution surface** and **zero new model capability** — the model gains
nothing from it, which is what makes it shippable at this size.

---

## The one thing to understand before starting: the CSP

`shell/src-tauri/tauri.conf.json:24` is today the single directive `default-src 'self'`.
It is a load-bearing part of the webview's lowest-trust posture: nothing the window
renders may be fetched from anywhere but the app itself. (An earlier draft of this plan
cited bundled fonts as the illustration. That is no longer true — the dark v4 direction
retired the bundled faces, and `shell/src/styles.css:25` states there is no `@font-face`
anywhere and the app uses system stacks only.)

**Monaco cannot run under it.** `style-src 'unsafe-inline'` is unavoidable and a nonce
cannot substitute — Monaco positions every view line, cursor, and selection overlay with
inline `style="…"` attributes, and nonces do not apply to style attributes at all.

Three consequences, all of which the build must handle rather than absorb silently:

1. **The CSP is global.** One window, one webview, one policy. A Simple-profile user
   gets `style-src 'unsafe-inline'` too, for a screen they can never reach. That is the
   honest cost of this decision and it belongs in the PR description.
2. **The residual risk is visual spoofing, not code execution or exfiltration.**
   `script-src` stays `'self'` — no `unsafe-eval`, no `unsafe-inline` (Monaco's ESM build
   does not need them; the `unsafe-eval` folklore comes from the AMD `loader.js`, which
   ESM does not use). Exfiltration is closed by keeping `img-src 'self' data:` and
   `font-src 'self'`. What remains is CSS that can overlay or hide UI — and in an app
   whose safety model is *a consent card the person reads before approving*, that is a
   nameable threat. Mitigate in the same PR, not after.
3. **`MermaidDiagram.tsx` is the live injection path.** It uses `dangerouslySetInnerHTML`
   on mermaid's SVG, which contains a `<style>` block that today's CSP silently blocks.
   Widening `style-src` will unblock it — diagrams will change appearance in a PR whose
   description says nothing about diagrams unless you look for it.

### Bright line

**If the measurement phase shows Monaco needs `script-src 'unsafe-eval'` or
`'unsafe-inline'`, Monaco does not ship.** Fall back to the bespoke
`<pre>` + highlight.js viewer (highlight.js is already bundled via `rehype-highlight`,
and the token theme already exists at `shell/src/styles.css:481–571`). Do not widen
`script-src` to make a rendering choice work.

### Measure, then widen — do not guess — **DONE 2026-08-08**

> **What actually happened, in the plan's own order.** Step A shipped and stayed:
> `shell/src/lib/cspReport.ts` pushes `{blockedURI, violatedDirective, sourceFile}` into
> the diagnostics ring, installed from `main.tsx` before the app renders. It is
> permanent, not scaffolding — a CSP is a floor the app cannot see enforced from the
> inside any other way, and a blocked worker is silent in the DOM and loud only in a
> devtools console nobody has open in a packaged build. Step B landed the target literal
> below, byte-for-byte. Step C is `tests/test_csp_is_pinned.py`, with the equality
> assertion and both structural rules.
>
> **The measurement itself was discharged by construction, and that is a weaker claim
> than running it.** A policy is enforced by a real webview and by nothing else, and
> this build could not run one — so what was relied on is the narrow fact that ESM
> Monaco plus a `?worker` worker needs `style-src 'unsafe-inline'` and nothing else,
> with the pin test holding the bright line permanently. **The live check is
> [`TESTING-CHECKLIST.md`](TESTING-CHECKLIST.md) §13c**, on all three platforms, and it
> is the outstanding work of this wave. The build output backs the narrow fact as far as
> a build can: no `blob:` appears anywhere in `dist/`, and the worker is referenced as
> `new Worker("/assets/editor.worker-….js")`.

**Step A.** Land a `securitypolicyviolation` listener in `shell/src/main.tsx` that pushes
`{blockedURI, violatedDirective, sourceFile}` into the existing `subscribeDiagnostics`
ring (`shell/src/ipc/client.ts`). Ship Monaco behind a flag under the **current** CSP.
Every needed relaxation announces itself by name.

**Step B.** Widen to exactly what fired. Target literal:

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; worker-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'
```

Note this is **net-tighter globally**, not only looser: `object-src`/`frame-src` go from
inherited `'self'` to `'none'`, and `base-uri`/`form-action` — which do *not* fall back to
`default-src` and are therefore currently unrestricted — get locked down. `base-uri 'none'`
matters more once `style-src` is loose.

`worker-src 'self'` is technically redundant (it falls back through `child-src` →
`script-src` → `default-src`), but write it explicitly so a reviewer sees `'self'`, **not
`blob:`**, stated as a decision. Bundle the editor worker via Vite's `?worker` suffix —
never `?worker&inline` (which produces `blob:`), never `MonacoEnvironment.getWorkerUrl`
(the AMD recipe, which is where every "Monaco needs blob:" answer online comes from).

**Step C.** Pin it. New `tests/test_csp_is_pinned.py`, in the style of
`test_no_snapshot_query_filters_on_created_in_mode`: read `tauri.conf.json` and assert
(1) exact equality with the reviewed literal, **and** (2) two independent structural
rules that survive a legitimate re-pin — `script-src` never admits `'unsafe-eval'` or
`'unsafe-inline'`; no directive admits `*`, `http:`, `https:`, or `blob:`, and `data:`
only in `img-src`. Equality alone teaches the next contributor to paste the new string.
The docstring must state what it does **not** prove: it pins the authored string, and
cannot see Tauri's runtime augmentation of the directive map.

> Tauri parses `app.security.csp` into a directive map and injects its own nonces and IPC
> sources. Today everything lands in `default-src`; once explicit `script-src`/`connect-src`
> exist, Tauri's additions retarget to those. Smoke-test on all three platforms, not just macOS.

---

## Prerequisites — land these before the surface

These are small, and each is a correctness problem the review surface makes worse rather
than one it introduces.

1. ~~**The permission card names the raw arg, not the resolved path.**~~ **LANDED
   2026-08-08.** Both file tools did `Path(args["path"]).name` on the **unresolved**
   argument while `affected_path` resolved, so inside a trusted root `path="notes.txt"`
   symlinked to `secrets.env` carded as *"notes.txt"* and wrote `secrets.env` —
   confinement cannot catch it, both files being inside trust. Each `permission_detail`
   now asks `call_affected_path`, the very function the boundary asks, and answers None
   rather than the sentinel when nothing resolves; basename-only and the fixtures are
   unchanged, as predicted. So the code screen this plan builds and the Activity Panel
   will agree about which file Addison touched, which is why it was a prerequisite. Two
   things the entry did not foresee: `call_affected_path` swallowed three exception types
   and not `RuntimeError`, which `Path.expanduser()` raises for an unknown `~user` — a
   live turn-crash, fixed in the same commit — and the tracking entry it pointed at is in
   `BUILD-LOG.md`, not `HANDOFF.md` (that file was rewritten on 2026-08-07).

2. ~~**`shell.readWorkspaceFile` has no size ceiling.**~~ **CLOSED 2026-08-08**, in its own
   PR as this entry asked. `read_workspace_path` did a bare `std::fs::read` — `UNDO_SIZE_BOUND`
   (256 KiB) guarded only the write path's prior capture — so a 500 MB file was reachable
   *today* through the shipped `read_project_file` tool and would have wedged the
   line-delimited bridge. `filesystem.rs` now carries its own `READ_SIZE_BOUND` (256 KiB,
   deliberately a second constant: the undo bound asks what can round-trip as an undo
   payload, this one asks what may cross the bridge and land whole in one model turn) and
   **refuses** an oversized read in plain language — never truncates, because a harness that
   reads half a file and then edits from it is worse than one that read nothing. The size is
   judged from metadata *before* any bytes are read, so the refusal costs no memory; the write
   path's prior capture got the same pre-check, with its own bound and its own message
   unchanged, because it loaded the whole file before refusing it too.

3. ~~**`UndoManager.prune()` has zero call sites.**~~ **RESOLVED 2026-08-08 — owner
   decision, taking this entry's recommendation.** The recency arm applies to **reverted
   rows only**; the age arm is kept as its co-condition; bounding `listEdits` is deferred
   to the surface build, where it is already written (Build §2, "The diff — from data that
   already exists": `reverted = 0`, `_MAX_EDITS = 200`). `prune()` now has exactly one call site —
   `main.JsonRpcServer._ensure_built`, the once-per-launch worker-thread build — which is
   §4.5's "on startup" and is also the only place a prune cannot race: every store touch
   is confined to that thread and no job has been dequeued yet, so nothing is mid-undo and
   no `record()` is in flight. (Contrast `SnapshotManager`, which prunes *inside* capture
   and therefore needs its `prune=False` reentrancy escape.)

   Both halves of the recency arm are scoped to reverted rows — the keep-set too, not just
   the delete — so a burst of live edits can never push older reverted rows out of the
   window. **The cost the decision accepts, stated rather than hidden:** an unreverted row
   is now deleted by nothing, so §4.5's retention no longer bounds that subset of the
   table. That is deliberate — the only tool retention has is deletion, and for a row
   describing a change still on disk deletion *is* the harm — but "`action_snapshots`
   grows without bound" is therefore narrowed, not closed: it is bounded for history and
   unbounded for live edits, one row per live edit. If that ever needs a bound, it is a
   *reconciliation* (drop rows whose file is gone or whose prior no longer applies), never
   a recency prune. Four mutation-proven tests in `tests/test_undo_manager.py` hold the
   line, the load-bearing one being that unreverted rows survive however old and however
   many they are — the exact thing the naive wiring would have broken.

---

## Docs first — Phase 1 before Phase 2, per amendment §14 — **DONE 2026-08-08**

The repo's own rule is authoritative docs before code, and this wave needed it more than
most: **"Phase 3" was defined as packaging / signing / notarisation / auto-updater /
binary restore / Secure-Enclave identity** in every document that scoped the phase, and
adding a Developer review surface to it is a redefinition that has to be written as one,
in the style of the 2026-07-20 amendment inserts. That is now done, ahead of any surface code.
What follows is the record of what each deliverable was and where it landed, kept because
the next Phase-3 reader needs to know the redefinition exists and where it is stated.

**The redefinition** is in `docs/addison-design-doc.md` §11, `docs/architecture.md`,
`docs/addison-engineering-spec.md` §11's Phase-3 note, `docs/HANDOFF.md`, and
[`../ROADMAP.md`](../ROADMAP.md), which owns status. `architecture.md` and `ROADMAP.md`
were written from scratch on 2026-08-08 — the first had never received the redefinition
at all, the second had grown its own packaging-only definition since this plan was
written — and the other three were amended where they had gone stale about sequencing.
`phase-3-includes-the-review-surface` in
[`../tests/doc_claims.py`](../tests/doc_claims.py) now holds the line, so the sixth
document to define the phase fails the suite instead of drifting quietly.

The two sentences this wave most strains, both now answered where they live:

- `addison-engineering-spec.md` §4.7 — *"The Developer profile deliberately reuses
  surfaces that already exist for other reasons… exposing it is a packaging decision, not
  new capability."* The code screen is the **first Developer surface that is not a reuse**,
  and the departure is now named beneath that sentence in the style of the step-5
  owner-decision notes: what the sentence protected (zero new model capability, zero new
  execution surface) survives, which is what makes the departure affordable.
- `addison-design-doc.md` §7.1 — *"Developer mode adds surfaces, never a different
  skin."* Honored, and said so beneath the sentence: the screen is the dark direction
  throughout and Monaco is themed from the repo's existing palette (§5 below), so no
  second look and no new hue enters the app for a Developer-only screen.

**The code-surface addendum** is the last section of
[`design-brief-dark/IMPLEMENTATION.md`](design-brief-dark/IMPLEMENTATION.md) — one
palette and zero new tokens, the diff alpha ladder, the re-theme-on-flip rule, and the
12px tension recorded rather than settled. It corrects one number in §5 below while it is
at it: `.markdown-body pre code` is **11.5px** in the tree, not 12px, so 12px is a
deliberate step up from the nearest precedent rather than a match to it.

**The manual pass** is `docs/TESTING-CHECKLIST.md` **§13c**, which was the next free
letter (13a is the G3 restore floor, 13b the Custom guards). The note this paragraph used
to carry still stands and is repeated there: **§13c was owed to Phase-2 step 5** — the
harness shipped with no manual section at all — and that debt is **still unpaid**. The
harness's section now takes §13d.

The two adjacent drifts this section listed as optional were **both already fixed before
this wave** and are recorded here so nobody hunts them again: design-doc §9's floors table
stopped calling the G4 anchor *"binary-capturing"* on 2026-07-26, and §11 item 4 already
routes "folder-scoped workspace grant" to Phase-2 step 5. Two others were found in their
place and fixed instead — design-doc §11 was still describing step 8's arming as unbuilt
in two passages, and both the design doc's and ROADMAP's Phase-3 notes still called the
surface blocked on a step that had landed.

---

## Build

### 1. Read paths — RPC, never a registry tool — **BUILT 2026-08-08**

A user-driven browse is not the model acting. Routing it through the registry would hand
the model a `list_directory` capability as a side effect **and** put a permission card in
front of a click the person just made. Both wrong. Precedent: snapshot restore is an RPC
path and never a tool.

All handlers go in `agent_core/rpc/workspace.py::WorkspaceMixin` — already "the sole
camelCase mapper for its namespace at the wire boundary."

```
workspace.listDirectory {directory}
  -> {directory, root, entries: [{name, kind, size, escapes}], truncated}
workspace.readFile {path}
  -> {path, root, content, bytes, truncated}
```

No `depth` parameter — one level per call, expansion-driven. A depth knob is how a full
repo walk gets requested by accident.

New Rust bridge methods in `shell/src-tauri/src/filesystem.rs`, beside the step-5 block:
`shell.listWorkspaceDirectory`, `shell.readWorkspaceFileForView`. Both open with
`refuse_addison_data_dir` — the shell's independent floor, unchanged.

**Confinement, in each core handler, in this order and no other:**

1. Mode gate — `self._mode() is PolicyMode.OPEN`, else refuse. **Load-bearing, not
   decorative:** trust rows persist and nothing revokes them on a profile switch, so
   without this a Simple-profile webview could browse a folder trusted under Developer.
   Precedent: `agent_core/rpc/widgets.py:139`.
2. Resolve **once** — `os.path.realpath(os.path.expanduser(raw))`.
3. `self._is_trusted_path(resolved)` → `rpc/workspace.is_trusted` → match-a-root **then**
   `policy.workspace_trust_allows` floor. Unchanged, reused as-is.
4. Pass **only the resolved value** to the bridge. Never re-read `params["directory"]`
   inside the call — that is the TOCTOU gap step 5 closed.

**Do not add these to `tools/base.py::ShellBridge`.** That Protocol's docstring says it is
"exactly the surface the v1 tools need — nothing broader," and these are not for tools.
Add to `agent_core/shell_bridge.py::IpcShellBridge` only, with a comment in `base.py`
saying why.

**Symlinks** are the new exposure. Rust must use `symlink_metadata` for `kind`, not
`metadata` — otherwise `project/link -> ~/.ssh` renders as an expandable directory and the
person clicks it before anything refuses. Emit `kind: "symlink"` and `escapes: bool`,
computed **core-side** by realpath + `is_trusted` (one predicate, never duplicated in
Rust). `escapes` is a UI honesty affordance — dim it, say *"this points outside the folder
you trusted"* — not the boundary; the boundary is that the follow-up call refuses at (3).

**Large directories:** a hard `MAX_DIR_ENTRIES = 500` cap **in Rust** (that is where the
bytes are, same reasoning as `UNDO_SIZE_BOUND`; a 200k-entry `node_modules` listing is a
multi-megabyte single line and `agent_process.rs` reads with uncapped `BufReader::lines()`).
Lazy, never recursive. **Do not hide `.git` or `node_modules`** — hiding is a lie about
what is on disk, and telling the truth is this surface's only value. Render them collapsed
and never auto-expand.

**Viewer reads need their own bridge method** because the tool and the viewer want
*opposite* semantics for oversize: the tool must **refuse** (a truncated file fed to the
model is a correctness hazard — it "reads" a file, sees half, rewrites it, destroys the
tail), the viewer should **truncate and say so**. `VIEW_SIZE_BOUND = UNDO_SIZE_BOUND`
(256 KiB), so any file Addison could have edited is a file the viewer can show whole.
Truncate on a **char boundary** — a byte cut through a multi-byte character turns a text
file into a binary one. Binary detection needs no new code: `String::from_utf8` already
fails and the existing plain sentence is the right copy.

#### What shipped, and the decisions taken while building it

Everything above, as written — the two RPC handlers on `WorkspaceMixin`, the two Rust
methods beside the step-5 block, the four-step confinement order, `escapes` computed
core-side, the 500-entry cap in Rust, and the viewer's truncate-on-a-char-boundary. Plus
the KNOWN-GAPS name race, which was scheduled into this section and is now closed. The
decisions that were not already written down, each stated at the code as well:

- **A refusal answers `{ok: false, error}`; a success carries no `ok` at all.**
  `grantTrust`'s shape for the refusal, the plan's literal shape for the answer. Five
  frozen sentences, none of which names a mechanism ("the Developer profile" is a thing
  on a settings screen; "OPEN mode" is not), and the not-trusted one is deliberately
  SHARED by both handlers and by the escaping-symlink case — naming the shortcut would
  be a worse sentence, since what the person needs to know is that it leads outside what
  they trusted.
- **Absolute paths only**, exactly as `grantTrust` already is. `realpath` would otherwise
  complete a relative path against the CORE PROCESS's working directory — a folder
  nobody chose and no surface shows. This is also what a `~someone` the OS cannot look up
  becomes: `os.path.expanduser` hands it back unchanged (unlike `Path.expanduser`, which
  RAISES — the crash fixed on the tool path on 2026-08-08), so it stays relative and is
  answered with "give me the full path", which is the true thing to say about it.
- **The trust rows are read ONCE per listing for `escapes`**, through the same pure
  `is_trusted` the boundary asks — a store round-trip per row would put 500 identical
  queries on the worker thread behind every click. The BOUNDARY still asks
  `_is_trusted_path` itself, in the plan's order; only the display work shares a read.
- **`root` is the LONGEST matching root** (nested roots name the nearer one) and is
  display-only. `null` — a root revoked between two calls — is a rendering answer, never
  an authorization.
- **An entry whose target cannot be resolved is marked `escapes: true`.** The direction
  that dims a row rather than the one that invites a click.
- **The listing is sorted in Rust BEFORE the cap**, so a truncated folder answers "the
  first 500 by name" rather than "500 the OS happened to hand back first" — otherwise a
  person cannot tell a missing file from an unlucky one.
- **The viewer reads with a bounded `take`, not `fs::read`.** Metadata is consulted first
  (it is what `bytes` reports — the FILE's size, never the excerpt's), but the read itself
  is capped at the bound plus one byte, so a file that grows between the two calls cannot
  cost this process more than that byte. The source-order pin
  (`every_size_ceiling_is_judged_before_any_bytes_are_read`) grew a fourth entry with its
  own read marker for that reason.
- **`NOT_TEXT_TO_READ` is worded once** in `filesystem.rs` and raised by both read paths —
  `TOO_BIG_TO_EDIT`'s rule, for the same reason: the person must not be able to tell
  which one refused.
- **The name race (KNOWN-GAPS) is closed by a SECOND HOOK, not a second parameter.** A
  path-bounded tool implements `permission_detail_for_path(resolved_path)` and never sees
  `args` at that seam, so it structurally cannot resolve a second time; the orchestrator
  and the routine engine resolve once, above their refusal branches, and hand that value
  to `call_permission_detail`. The widget rail passes nothing and says why (its only tool
  has no `affected_path` at all). Proven by a tool whose `affected_path` answers a
  DIFFERENT path the second time it is asked — stricter than a real symlink swap, because
  it fails on ANY second resolution rather than on an unlucky one.

**Not in this section, on purpose:** no frontend consumer (that is §4 — §1 ships the
types and the generated fixtures so §4 has something to parse against), no `listEdits`,
no diff, no revert, no CSP change.

### 2. The diff — from data that already exists — **BUILT 2026-08-08**

Verified: `write_project_file.py:127` already records
`undo_payload = {"path", "existed", "prior"}`. The BEFORE state is in the database for
every edit, and because the shell refuses binary and >256 KiB priors before writing,
**every BEFORE is text and ≤256 KiB**. AFTER is the file on disk. No new capture table,
no `snapshots/scope.py` change (`action_snapshots` is already excluded).

**One gap, and it matters:** "the file on disk now" is not "what Addison wrote." If the
person edited the file themselves afterwards, the diff attributes their work to Addison
and Revert throws it away with no warning. Add one field:

```python
"wrote_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
```

No migration (JSON in a TEXT column), no dataclass change. Old rows lack the key →
`onDiskChanged: null` → the UI says *"Addison can't tell whether this file changed since,"*
which is honest.

```
workspace.listEdits {}          -> {edits: [Edit], truncated}   # metadata only
workspace.readEditDiff {path}   -> {path, before, after, beforeTruncated, afterTruncated}
```

`listEdits` is metadata only, deliberately: pushing every before/after into one payload
means a 20-file turn ships ~10 MB across two process boundaries on one JSON line.

`Edit` carries `path`, `root`, `relativePath`, `snapshotIds` (newest first — the revert
chain), `writes`, `created`, `firstWrittenAt`, `lastWrittenAt`, `revertable`,
`onDiskChanged`, `missing`. The UI renders `relativePath` by default.

**Scope is filesystem, not conversation:** `tool_id = 'write_project_file' AND reverted = 0`,
ordered `created_at DESC, rowid DESC` (same tiebreaker as `recent_unreverted_snapshots`),
capped at `_MAX_EDITS = 200`. `action_snapshots` has no `conversation_id` anyway, but the
deeper reason is that the question this surface answers is *"what has Addison changed that
is still changed"* — conversation scoping would hide an edit from an earlier chat that is
still live on disk, which is exactly the edit a person most needs to find. For the same
reason, **rows whose trust has since been revoked are still listed**, with `root: null`.

**A file written N times collapses into one `Edit`**: BEFORE = the `prior` of the *oldest*
unreverted snapshot; `onDiskChanged` compares disk against the *newest* `wrote_sha256`.
Group by `os.path.normpath(os.path.realpath(path))` **without casefold** — do not reuse
`policy._canonical`; HANDOFF already flags its unconditional casefold, and here it would
merge two genuinely different files into one revert target.

#### What shipped, and the decisions taken while building it

Everything above, as written: `wrote_sha256` on the write's `undo_payload` (no migration,
no dataclass change), both handlers on `WorkspaceMixin`, the `Edit` field list, the
filesystem-not-conversation scope with `_MAX_EDITS = 200`, revoked-trust rows still listed
with `root: null`, the N-writes collapse, and the grouping key in
`file_revert.revert_key` — **which no longer works the way the paragraph above says, and
should not; see the two corrections at the end of this block.** The decisions that were
not already written down:

- **The mode gate applies to all three methods, and `readEditDiff`'s disk read is
  confined by MEMBERSHIP rather than by live trust.** §1's four steps hold with step 3
  substituted, and the substitution is stronger where it matters: the resolved parameter
  must MATCH the group key of an unreverted `write_project_file` chain, so the only paths
  that ever reach the filesystem are ones Addison itself wrote and has not yet put back —
  a closed set this process produced, not "anywhere under a folder you trusted". Live
  trust is deliberately NOT the test, because the plan requires a revoked-trust edit to
  stay listed and (per the shell's session ledger, which never asked about trust either)
  stay revertable; asking here would have produced a surface that shows a change, offers
  to put it back, and refuses to show you what it is. The shell keeps its own data-dir
  floor underneath, as always.
- **A NEW shell method, `shell.digestWorkspaceFiles`, because the plan's own reasoning
  requires one.** `onDiskChanged` needs the bytes on disk, and the core has no filesystem
  of its own (§1.3). Reading each file back across the bridge to hash it core-side would
  ship exactly the megabytes `listEdits` is metadata-only to avoid — and then discard
  them. So the hashing happens where the bytes already are and 64 characters cross the
  seam. One batch call for the whole list, keyed by path, with its own ceiling
  (`DIGEST_SIZE_BOUND`) and `sha256: null` for anything it cannot judge.
- **A batch answer is a MAP keyed by path, never an array positioned against the request.**
  Both new shell methods. An array couples two processes to an ordering, and the failure
  when they stop agreeing is silent and precisely wrong: a Revert offered for a file that
  cannot take one. A key that is absent reads as the cautious answer on the core side.
- **`onDiskChanged` is TRI-STATE and `missing` is separate.** `null` is a real answer —
  an old row, a file the shell cannot judge, or no shell at all — and collapsing it into
  `false` is the one wrong reading that lets a revert discard somebody's own work
  silently. A file that is GONE reports `missing` with `onDiskChanged: null`: its absence
  is a change, but not the change that sentence describes.
- **Neither shell question may fail the listing.** They answer `{}` on a refusal, and the
  row degrades to "not revertable / can't tell". A review surface that goes blank because
  one auxiliary answer failed is the least honest outcome available.
- **`beforeTruncated` is always `false` in this tree** and ships anyway. A stored BEFORE
  is whole by construction (the shell refuses to overwrite a file whose prior exceeds its
  capture bound), so the field exists only so that moving that bound cannot make the left
  pane quietly incomplete with nowhere to say so.
- **`relativePath` falls back to the WHOLE path when there is no root** (trust revoked
  between the write and now). A bare basename for a file nobody can place is less useful
  than the long answer, and the long answer is true.
- **The index is a line in `schema.sql`'s index block**, which `executescript` runs on
  every open — the repo's existing evolution path for an index, and complete for one,
  since an index holds no row that is not already in the table. `EXPLAIN QUERY PLAN` in a
  test proves the query uses it and needs no temp B-tree for the ORDER BY.

**CORRECTED 2026-08-08 — the grouping key asks the FILESYSTEM, and the instruction above
that told it not to was wrong.** "Group by `os.path.normpath(os.path.realpath(path))`
**without casefold**" is right about `policy._canonical` and wrong about the volume:
Addison ships on macOS, whose default filesystem is case-INSENSITIVE, so `Notes.md` and
`notes.md` are ONE file — and grouping by the spelling gave that one file TWO chains.
Write v0→v1 under one spelling and v1→v2 under the other, revert them in the order the
list offers, and the file lands on v1 with v0 unreachable: precisely the resurrection §3's
chain collapse exists to make impossible by construction. Casefolding is the opposite
error, and only on a case-sensitive volume (APFS case-sensitive, an external or network
mount), where the two names *are* two files and a merge writes one's prior bytes into the
other. Both guesses are wrong on one volume each, so the key now ASKS: `st_dev`+`st_ino`
from an `lstat` of the recorded name — `os.path.samefile` semantics, one path at a time —
with the exact stored path as the tiebreak when there is nothing at that name to ask
about. The residual that tiebreak leaves is in [`KNOWN-GAPS.md`](KNOWN-GAPS.md). It is a
GROUPING key only; the next correction owns what crosses to the shell.

**CORRECTED 2026-08-08 — what crosses to the shell is the RECORDED path.** The membership
bullet above is right that only a path matching a row ever reaches the filesystem, but the
code took that path from a fresh `realpath` of the stored value at READ time. A shortcut
appearing at a written path afterwards therefore moved every later call onto whatever it
pointed at — the diff's shell read, the digest, the ledger question and the revert's write
— and `readEditDiff` shipped the full text of a file outside every trusted folder to the
webview. Membership had been decided on a re-resolution of the stored value rather than on
the value that was confined, and those are not the same test. So: `FileEdit.path` is now
`undo_payload["path"]` itself, exactly as `WriteProjectFileTool.undo()` uses that key of
that row; the parameter is resolved by `_edit_resolve` (the folders it names, never its
last component) so a click asks the question the rows answer; and because the shell
follows a symlink on both the read and the write it is handed, `file_revert.replaced_by_a_link`
refuses before either happens. The shell-side half of that is in KNOWN-GAPS.

**CORRECTED 2026-08-08 — `listEdits` reads the trust rows once.** `revertable` and
`onDiskChanged` were batched from the start; the third per-row question was not.
`_edit_payload` asked `_trusted_root_for` per edit, which is a store round trip per row —
up to 200 on the worker thread behind one click, each asking the identical question, in a
method whose sibling `_browse_entries` states the opposite rule for its 500 entries. The
roots are now read once by the caller and passed in.

### 3. Per-file revert — the sharp edge — **BUILT 2026-08-08**

`UndoManager.undo_last(n)` is LIFO. "Revert this file" is out-of-order, and out-of-order
against a LIFO stack is where this goes wrong.

**The hazard, concretely.** File `F` = `v0`. Addison writes `v1` (S1, prior `v0`) then `v2`
(S2, prior `v1`). Revert S1 alone → disk is `v0`, but S2 is still unreverted with
prior `v1`. The person then hits "Undo last action" → `undo_last(1)` takes S2 and writes
`v1`. **The undo button just resurrected content the person deliberately reverted away
from.** Nothing notices.

**Semantics: revert the entire unreverted chain for that path down to its oldest
unreverted prior.** This lands the file on a state that **actually existed on disk** — the
direct analogue of the snapshot floor's "restore lands somewhere that actually ran" — and
it leaves zero unreverted rows for that path, so the hazard evaporates by construction
rather than by care. The diff's BEFORE pane *is* the oldest prior, so the UI and the
semantics agree: Revert produces exactly what the person is looking at.

Three sharpenings:

- **Compute the target once; do one write.** Do not replay N undos — that does N shell
  writes, briefly puts intermediate states on disk, and can strand the file mid-chain.
- **Write first, mark second.** A failed write marks nothing (edits stay listed and live);
  a failed mark leaves rows claiming unreverted for a file already at its prior, and
  re-reverting is idempotent. That asymmetry is the correct way round — state it.
- **No hunk-level or partial revert, ever.** It would require writing a byte combination
  that never existed on disk, which is precisely what "lands somewhere that actually ran"
  forbids. Put it in the docstring so it reads as a decision, not an omission.

**Never touch `UndoManager._redo_stack`** — not push, not clear. `WriteProjectFileTool` has
no `redo()`, so a pushed entry makes `can_redo()` true and surfaces a "Do that again"
control that always fails; and clearing would discard a legitimately redoable `save_file`
undo belonging to a different mechanism.

**Warn before clobber:** if `onDiskChanged`, the confirm must say so — *"You've changed
this file since Addison did. Reverting will replace what's there now with the version from
before Addison's first change."* Inline two-step confirm, never `window.confirm()`, per the
`WorkspaceTrustPanel` pattern.

#### The restart problem — design for honesty, not a workaround

`FileState.workspace_written` in `filesystem.rs` is a **session** ledger,
`Default`-constructed at `main.rs:30`. On restart it is empty, while `action_snapshots`
rows survive indefinitely. So after any restart, `restore_workspace_path` refuses every
path: *"Addison can only undo a file change it made."*

**This is already broken today for `undo.undoLastAction`** — the chat UI accidentally hides
it because `hasUndoableActions` only flips true on a live `tool.activityUpdate` in the
current session. The review surface removes that accidental protection: it reads the
database, so it will render Revert next to every historic edit and every one will fail.

Do **not** persist the ledger (its whole security property is being session-scoped and
unsteerable) and do **not** widen the check to "inside a currently-trusted root"
(filesystem.rs documents the opposite on purpose: "the ledger is session, not trust").

Instead add a pure query with no filesystem effect —
`shell.canRestoreWorkspaceFiles {paths} -> {restorable}` — populate `Edit.revertable` from
it, and render a non-revertable edit read-only with a plain line: *"Addison changed this
before the app was last restarted, so it can't put it back for you. The earlier version is
on the left; you can copy it."* The BEFORE text is right there, so it degrades into
something useful rather than a dead button. Give `undo.undoLastAction` the same honesty in
the same pass.

*(Intended future path, not v1: `shell.adoptWorkspacePath {path, expectedSha256}` re-ledgers
a path only if its current bytes hash to what the core recorded at write time — recovering
the restart case without widening to arbitrary paths. This is the strongest argument for
`wrote_sha256`.)*

#### Where the code lives: **not** `UndoManager`

`UndoManager` is the §4.5 one-tool-call mechanism — LIFO, tool-agnostic, redo-stack-owning,
`record()`-coupled to the orchestrator. A per-path, out-of-order, chain-collapsing,
redo-stack-untouching, `write_project_file`-only revert is a **third** mechanism. CLAUDE.md
already draws exactly this line for `SnapshotManager` ("complementary, independent, and
they never call each other").

New `agent_core/snapshots/file_revert.py`, beside `undo_manager.py` (same table, right
package): `FileRevertManager.pending_edits(limit=200)` and `.revert_path(path)`, over
frozen `FileEdit` / `FileRevertResult` dataclasses. Two new store methods in the existing
`--- action snapshots ---` section of `agent_core/memory/store.py`:
`unreverted_snapshots_for_tool(tool_id, limit)` and `mark_snapshots_reverted(ids)` — the
latter **one statement, one commit**, so a crash can never leave half a chain marked while
the file already sits at its prior.

Wire `workspace.revertFile` through `_WORKSPACE_JOBS` + `_worker_loop`; build
`FileRevertManager` in `_ensure_built` beside `UndoManager`; declare it on `ServerContext`.
Running on the worker means revert automatically serialises behind an in-flight turn — no
extra locking needed. Disable Revert while `turn.isWorking` anyway, for honesty.

#### What shipped, and the decisions taken while building it

Everything above: `agent_core/snapshots/file_revert.py` with frozen `FileEdit` /
`FileRevertResult` dataclasses, the whole-chain semantics, compute-once-write-once,
write-first-mark-second, the no-hunk-revert decision in the docstring, the two store
methods (`mark_snapshots_reverted` one statement and one commit), the pure
`shell.canRestoreWorkspaceFiles` query, the worker wiring, and `undo.undoLastAction`'s
matching honesty. The decisions that were not already written down:

- **The manager holds a store and a bridge and nothing else.** No registry, no
  `UndoManager`, no policy — so the redo stack is not reachable from it even by accident.
  That makes the plan's "never touch `_redo_stack`" structural rather than a rule someone
  has to remember, and it moves the only place the rule CAN be broken to the RPC handler,
  where both objects are in scope. The test therefore drives the RPC, not the manager: a
  manager-level test survived the mutation ("clear the redo stack so the UI is
  consistent") that a handler-level one kills.
- **ONE window (`_MAX_EDITS = 200` rows) for the list, the diff and the revert**, so the
  three cannot disagree about where a chain begins — a diff whose BEFORE is not what
  Revert produces would be worse than no diff. The plan's cap is on ROWS, which is the
  conservative reading, and a window can therefore cut a long chain. That is safe in the
  one direction that matters and the module says why: rows arrive newest-first, so a row
  outside the window is strictly OLDER than every row inside it — the revert still lands
  on a real earlier state, and any leftovers can only move the file FURTHER BACK, never
  forward. Forward is the resurrection this design exists to prevent.
- **A failed MARK answers `ok: false` with its own sentence** ("Addison put the file back,
  but couldn't update its own record of the change…"). The file genuinely is back, so the
  sentence says so; the rows genuinely still claim otherwise, so it is not a success. Re-
  reverting recomputes the same target and writes the same bytes, and the test drives
  exactly that convergence.
- **`undo.undoLastAction` ASKS before it attempts.** If the next unreverted row is a
  `write_project_file` edit the shell's ledger no longer holds, it answers a plain
  sentence and marks nothing — no failed write, no consumed row, so the review surface can
  still show the earlier version. Scoped to that one tool, because it is the one ledger
  there is a pure query for: `save_file`'s undo has a session ledger of its own with no
  such query, and it keeps today's failure rather than being given a guess about a
  mechanism this code did not ask. Stated at the code.
  **CORRECTED 2026-08-08:** it asked about a RE-RESOLVED path rather than the one the
  attempt would use. Once a shortcut stood at the written path, the shell was asked about
  a name its ledger had never held, answered no, and the person was told "Addison changed
  that file before the app was last restarted" — false, with no restart anywhere in it,
  and permanent, because a pre-check marks nothing and so the sentence came back every
  time they pressed Undo. It now asks about `undo_payload["path"]`, the exact string
  `undo()` hands the shell: a pre-check that does not ask about what the attempt will do
  is not a pre-check.
- **The refusals are two sentences, not one.** "Addison hasn't made a change to that file
  that's still in place" (the diff) and "Addison has no change of its own to put back for
  that file" (the revert). Neither is the not-trusted sentence, because neither is that
  situation: the folder may be perfectly trusted and the file simply untouched.

### 4. Frontend — **BUILT 2026-08-08**

**The screen.** Widen `screen` in `App.tsx:82` — and note **three** call sites, one of
which is a dead end: `Sidebar.tsx:27` types the prop `"chat" | "settings"` (verified) and
drives active-row state at line 135, so widening only App is a type error; widening both
without a Sidebar nav entry leaves **no way to reach the screen**; and `App.tsx:562`'s
Escape handler checks `screen === "settings"` and must become `screen !== "chat"`.

**Gate on `activeProfile`, not `mode`** — `"developer" | "custom"`. That is the codebase's
repeated insistence and the precedent is `SettingsPage.tsx:310`'s workspace-trust card.

**Move the slots, don't duplicate them.** `workBlock` / `consentBlock` are assembled once
at `App.tsx:584–629`; route them into the code screen so the permission card and Activity
Panel follow the person there. Anything else means two consent surfaces.

**Monaco loading.** Dynamic import, mirroring `MermaidDiagram.tsx`'s precedent, with a
quiet placeholder. Import `monaco-editor/esm/vs/editor/editor.api` — **not** the bare
package, which drags in every language contribution and all four language services; that
single choice is the biggest size lever and it makes "no language workers" structurally
true rather than a config claim. **Do not use `@monaco-editor/react`** — it loads Monaco
from jsDelivr by default: instant CSP violation and a network dependency in a local-first
app. Add `optimizeDeps.include` for the api entry to `vite.config.ts` or dev cold start
crawls over monaco's thousands of ESM files.

**Editor options — two are security-relevant, not cosmetic:**

- `links: false` — Monaco's link detector makes URLs clickable and hands them to
  `openerService`. `Markdown.tsx` states the standing rule that the webview never opens
  URLs itself. Must be off.
- `contextmenu: false` — the default menu offers Cut/Paste and the webview has no
  clipboard capability.
- Plus `readOnly: true`, `domReadOnly: true` (without it the backing textarea is still
  writable and IME/paste fires input events), `automaticLayout: true`, `minimap: false`,
  `quickSuggestions: false`, `codeLens: false`, `occurrencesHighlight: "off"`, `ariaLabel`.
- Never add `require-trusted-types-for 'script'` later without allowlisting Monaco's
  policy names.

**Layout at 1000×720 / `minWidth: 640`.** Side-by-side diff needs ~2× a usable pane; at
1000px minus sidebar minus tree you have ~500px, i.e. two 250px panes. Use Monaco's
built-in `renderSideBySideInlineBreakpoint` (~700) with
`useInlineViewWhenSpaceIsLimited: true` — don't hand-roll it. Below `md`, the tree becomes
a drawer via the existing `MobileDrawer` + `useMediaQuery("(max-width: 767.98px)")`
structural swap; plan the tree as collapsible-to-a-rail from the start, because
`SettingsPage` survives 1000px only by being a single scroll column and a two-pane diff
will not.

#### What shipped, and the decisions taken while building it

Everything above: the third screen and its nav entry, the Developer/Custom gate on the
active profile, the moved `workBlock`/`consentBlock` slots, the ESM-only Monaco with its
`?worker` worker, and the `renderSideBySideInlineBreakpoint` + `MobileDrawer` layout.
The CSP was widened to the target literal and pinned. New files:
`components/CodeSurface.tsx`, `components/CodeEditor.tsx`, `hooks/useCodeReview.ts`,
`lib/monaco.ts`, `lib/monacoTheme.ts`, `lib/cspReport.ts`, `lib/sanitizeSvg.ts`.

**Two things this section got wrong about the tree it was describing**, both corrected
rather than worked around:

- **There is no `screen` state and there never was.** `App.tsx` holds `view: View`
  (`types/ui.ts`), and the union already had FIVE members — `chat | settings | tools |
  snapshots | widgets` — not two. So `"code"` was added to `View`, and `SURFACE_TITLES`
  (a `Record<Exclude<View, "chat">, string>`) made the missing title a type error rather
  than a blank header, which is the good version of this mistake.
- **The Escape handler already read `view !== "chat"`.** It was widened when the third
  and fourth surfaces landed; the plan was describing the two-surface tree. Nothing to
  do, and the test that would have caught the plan's version is in
  `codeScreen.test.tsx` regardless. The third call site the plan warned about — a
  widened type with no way to reach the screen — was real and is why the nav row exists.

The decisions that were not already written down:

- **The nav row's GATE IS THE HANDLER.** `Sidebar` renders the "Code" row only when it
  is given an `onOpenCode`, and App passes one only under Developer/Custom. Simple has
  no row rather than a disabled row — a disabled control invites the question, and the
  answer ("switch profiles") is not one this row should be teaching.
- **The screen is gated in TWO places, and the second is the one that holds.** A profile
  can change under an open screen (Settings, or a G3 restore putting a configuration
  back), so the render refuses to draw the surface the moment `activeProfile` stops
  qualifying, and an effect then returns to chat. Gating only the nav entry would have
  left whoever was already standing there.
- **The rail's `work` slot stands down on a surface, exactly as `consent` already did.**
  The Activity Panel was previously rendered into a rail that a surface collapses to
  zero width and marks `inert` — in the DOM, visible to nobody. The code screen renders
  it itself. The consent card needed no change at all: `SurfaceConsentLayer` already
  hoists it onto a fixed layer for every surface, so it followed the person here for
  free, and "never duplicated" is a test rather than a convention.
- **It is NOT a `<Surface>`.** That component is a 580px centred reading column and a
  two-pane diff will not survive inside one. It keeps the two contracts App's view
  machine depends on — the `SURFACE_ID` root and `data-surf` text — so entering and
  leaving animate like every other screen.
- **An escaping entry is dimmed AND inert, whatever its kind.** The plan asked for
  dimming; making the row do nothing as well costs nothing and removes the click the
  core would only refuse. A symlink that does NOT escape is shown as a shortcut and is
  also not opened from here: this side does not know whether it points at a file or a
  folder, and guessing puts the wrong control on screen.
- **Every pane fetch carries a sequence number.** Clicking three files quickly would
  otherwise leave whichever answer arrived last under whichever header arrived first —
  a viewer showing one file's text under another file's name, on the one screen whose
  entire job is to be exact about which file is which.
- **Each trusted root opens ONE level when the screen first shows it.** Never recursive,
  and `.git`/`node_modules` are listed and left collapsed. A tree that shows only
  collapsed root names looks broken; a tree that walks a repo is the accident the
  missing `depth` parameter exists to prevent.
- **`optimizeDeps.include` alone was not enough — the import specifier changed.** The
  plan names `monaco-editor/esm/vs/editor/editor.api`, which is the pre-0.53 path;
  monaco 0.56 ships an `exports` map (`"./*": "./esm/vs/*.js"`), so the same entry is
  `monaco-editor/editor/editor.api`. Same decision, current spelling. `src/vite-env.d.ts`
  was added so both tsconfigs can see Vite's `?worker` declaration.
- **The basic-language REGISTRATIONS ship with it, and the language services do not.**
  `editor.api` alone registers no grammars at all, so §5's whole token map would have
  had nothing to colour. `basic-languages/monaco.contribution` adds ~80 registrations of
  a few hundred bytes each, and every grammar is a lazy chunk fetched the first time a
  file of that type is opened. These are Monarch tokenizers on the main thread — not the
  four worker-backed language services the api entry exists to exclude. JSON is the one
  common type with no Monarch grammar upstream and renders as plain text.
- **`worker.format` is `iife`, stated rather than defaulted.** A module worker would ask
  `new Worker(url, {type: "module"})` of three different webviews this build cannot
  check. The worker has no dynamic imports, so a classic script costs nothing.
- **A failed editor load degrades to plain text, not to a spinner.** The text is what
  the person came for; the diff's fallback shows BOTH panes stacked, because showing
  only "after" would hide the state Revert lands on.

**Sizes, since the plan calls the api entry the biggest lever — measured against a
build of `origin/master`, not estimated.** The initial bundle goes 658 kB → 681 kB
(199 kB → 206 kB gzip): **+23 kB, and none of it is Monaco.** That is the screen's own
code, which App imports statically like every other surface. The editor is a lazy chunk
of 2,681 kB (695 kB gzip), plus 80 kB of its own CSS (13 kB gzip), an emitted 273 kB
worker, and ~80 language-grammar chunks of 2–20 kB each — none of which is fetched by
anyone who never opens the screen. Two facts about the built output are worth recording
because they are the parts a reviewer cannot check by reading source: `blob:` appears
nowhere in `dist/`, and the TypeScript language service is not in the editor chunk.

### 5. Skinning Monaco to the dark direction — one palette, zero new tokens — **BUILT 2026-08-08**

The design brief has no vocabulary for code. But `shell/src/styles.css:481–571` **already**
carries a full highlight.js token theme, tuned toward the violet accent, for both themes
(`--hl-comment/keyword/string/number/title/attr/builtin/type/name/link/addition/deletion`,
declared at lines 485–511). New `shell/src/lib/monacoTheme.ts` reads **those exact**
variables** and converts them, so the repo has one code palette rather than two. Monaco
takes hex only, so the space-separated RGB channels must be converted; every read needs a
fallback because **jsdom does not apply stylesheets** and `getPropertyValue` returns `""`
in vitest — without fallbacks every theme test would silently assert nothing.

Set `base: dark ? "vs-dark" : "vs"` and **`inherit: false`**, so nothing leaks from the VS
defaults and unmapped tokens fall to `editor.foreground` — code, not confetti. Map by
Monaco's token-prefix matching: `comment`→`--hl-comment` (italic, matching the existing
`.hljs-comment` rule), `keyword`→`--hl-keyword`, `string`/`regexp`→`--hl-string`,
`number`/`constant`→`--hl-number`, `type`→`--hl-type`, `variable`/`tag`→`--hl-name`,
`attribute.name`/`key`→`--hl-attr`, `function`/`predefined`→`--hl-title`,
`delimiter`/`operator`→`--c-muted`, `invalid`→`--c-danger`. **Weights: 400 only.** The app
is on system stacks now (`ui-monospace, 'SF Mono', Menlo`), so a `fontStyle: "bold"` would
resolve to a real face rather than a synthetic one — but the direction's mono is a *machine
voice*, one weight, and bolding tokens would reintroduce the emphasis the palette
deliberately carries in hue alone.

Chrome maps onto existing tokens with no additions: `editor.background`→`--c-panel`
(matching `.markdown-body pre`, so code already looks like this here),
`editorLineNumber.foreground`→`--c-faint`, `editor.lineHighlightBackground`→`--c-line`
(the hairline/row-separator value — the closest thing to a raised row the direction has),
`editor.selectionBackground`→`--c-accent` at low alpha (there is no tint token: the accent
is a single flat colour and selection elsewhere is a 2px rail, which an editor cannot use),
`editorWhitespace.foreground`→`--c-ghost`, `editorCursor.foreground`→`--c-accent`. Zero
`editor.lineHighlightBorder` or Monaco boxes the current line.

**Do not reach for `--c-surface`, `--c-hair`, `--c-fern-tint`, `--c-dash` or `--c-fern`** —
this section named them before the 2026-07-26 restyle and **none of them exist**. The
palette is `paper / panel / line / rail / track / track-hi / ink / ink-soft / muted / faint
/ disabled / ghost / accent / on-accent / danger` (`styles.css:31–91`).

**Diff is the one genuinely new concept, and it still needs no new variables.**
`--hl-addition`/`--hl-deletion` exist as *foregrounds*; derive the tinted backgrounds as an
alpha ladder over them (line ~.10 light / .16 dark; intra-line text ~.22 / .30; overview
~.70). Keep the ladder as named constants in `monacoTheme.ts`, **not** in `styles.css` —
putting it in the token file would create the second palette this approach exists to avoid.

Worth knowing: `--hl-deletion` is `180 84 78` light / `226 166 166` dark — **byte-identical
to `--c-danger` in both themes** (re-verified against the restyled palette). The deletion
tint is literally the danger color
at low alpha, so no third hue enters the app and the "one accent plus danger" rule survives.

**Re-theme on the light/dark flip, and do it properly.** `MermaidDiagram.tsx` reads the dark
class once per session; that is tolerable for a diagram and **wrong for a persistent editor
the person is looking at when they flip Appearance**. `defineTheme` must be called again,
not just `setTheme` — the hex values are baked at define time. `App.tsx:476–496` already
computes `resolved` inside `apply()` (line 483) but discards it; lift it into state
(`setResolvedTheme(resolved)`, one new line) and prop-drill it, rather than adding a
`MutationObserver` as a second source of truth for a fact App already knows. This also
covers the `"system"` case of the OS flipping while the app is open.

**Font size — a real tension, recorded not hidden.** The brief caps mono at 10–12px for
"machine facts, never prose," and a code viewer *is* machine facts — but 12px is small for
a full-file surface read by personas aged 54 and 68. Ship `fontSize: 12, lineHeight: 19`
and put an **editor zoom control on the follow-up list** rather than pretending 12px
settles it. *(This line justified 12px as "matching `.markdown-body pre code`'s existing
12px" until 2026-08-08; that rule is **11.5px** in the tree. So 12px is a deliberate step
up from the nearest precedent rather than a match to it — defensible for a surface read
as a document instead of as an excerpt inside a message, and still better than inventing
a size token.)*

#### What shipped, and the decisions taken while building it

Everything above, in `shell/src/lib/monacoTheme.ts`: the `--hl-*` reads with the
channels-to-hex conversion, `base` + `inherit: false`, the token map exactly as written,
weight 400 with italic on comments alone, the chrome mapped onto existing tokens with no
additions, the alpha ladder as named constants in that file rather than in `styles.css`,
selection as the accent at low alpha, and `fontSize: 12` / `lineHeight: 19`. The
re-theme is the one line this section asked for: `App.tsx`'s `apply()` now calls
`setResolvedTheme(resolved)` on the value it was already computing, and that value is
prop-drilled to the screen. No `MutationObserver` anywhere. The decisions that were not
already written down:

- **`editor.foreground` had to be mapped, and the section does not name it.**
  `inherit: false` is what makes unmapped tokens fall to it, so leaving it unset would
  have made "code, not confetti" resolve to whatever `vs-dark` happens to use. It takes
  `--c-ink`, which is already the app's text.
- **`--hl-builtin` and `--hl-link` are deliberately unmapped.** The section's map names
  neither, and Monaco has no token that corresponds to either; inventing one would put a
  colour on screen that no rule in `styles.css` puts anywhere else. They stay in the
  token file for the markdown blocks that do use them.
- **TWO theme names, not one redefined in place.** `applyMonacoTheme` defines both and
  then selects one, so `setTheme` always has a name change to act on and a flip can
  never depend on `defineTheme`'s re-application behaviour.
- **ONE pair of hex literals exists, and they are `--c-ink`.** Every read needs a
  fallback because Monaco THROWS on an invalid colour and jsdom answers `""` for every
  custom property — a missing fallback is a crash in the rig, not a wrong hue. A
  twelve-entry fallback table would have been the second palette this approach exists to
  avoid, so one neutral foreground per theme is the whole of it, and the degenerate
  theme it produces is monochrome rather than wrong.
- **A garbled value is treated as unreadable.** `rgb(1,2,3)`, a channel over 255, an
  empty string: all fall back rather than being passed through, because "passed through"
  means an exception inside `defineTheme` at the moment somebody opens a file.
- **`minimap: false` is spelled `minimap: {enabled: false}`.** Monaco's option is an
  object; same decision, the API's spelling. (The plan's other option names are exact.)

**On the follow-up list, not folded in as though it were settled:** an editor zoom
control (the 12px tension this section records); JSON highlighting, which would mean
admitting the JSON language service and its worker; and the plan's own deferred item,
`shell.adoptWorkspacePath`, which would recover the post-restart revert case.

---

## Verification

**Every gate green**, from the repo root: `./scripts/gates.sh`. That script is the one
executable definition of the list and CI calls the same script
([`VERIFICATION.md`](VERIFICATION.md) §1) — this line used to name four gates by hand
and was missing four of them, which is exactly how the prose copies came to disagree
with CI.

**Tests this wave owes**, following the repo's own standard (mutation-proven; *"for each
test added, name the mutation it kills"*):

| Test | Kills |
|---|---|
| `tests/test_csp_is_pinned.py` | A silent CSP widening. Equality **plus** the two structural rules. |
| Registry pin — `ast`-parse `main.py::build_registry`, assert the tool-id set equals a frozen literal | Someone adding `list_directory` as a tool, handing the model the capability this design exists to avoid. |
| Confinement: browse a symlink escaping a trusted root; browse the data dir; browse under Simple with a live trust row | The mode gate being dropped as "redundant"; the floor being skipped on the new read paths. |
| Path tests using a **symlinked alias** tmp_path | pytest's `tmp_path` is already realpath'd — step-5 tests could not see the resolve-once mechanism at all (HANDOFF rigor lesson 11). |
| Revert: the S1/S2 out-of-order scenario; both interleavings from the chain-revert analysis; write-fails-marks-nothing; mark-fails-is-idempotent | The chain collapse degrading to per-snapshot revert. |
| Redo stack untouched by `revert_path` | A "Do that again" control that always fails. |
| `revertable: false` after a simulated empty ledger | The dead-button regression. |
| Monaco theme: set `--hl-*` explicitly on `document.documentElement.style`, assert dark ≠ light | The fallback silently swallowing an empty `getPropertyValue`. |
| A generated fixture in `tests/ipc_fixtures.py` for **every** new payload | The `roots`/`folders` incident — both suites green, wire mismatch shipped. |
| `shell.listWorkspaceDirectory` / `readWorkspaceFileForView` / `canRestoreWorkspaceFiles` / `digestWorkspaceFiles` added to `ipc.rs::tests::rejects_shell_methods_from_the_webview` | The webview reaching the shell directly. |
| `EXPLAIN QUERY PLAN` over `unreverted_snapshots_for_tool`'s exact query | `action_snapshots` — which retention no longer bounds for unreverted rows — being full-scanned once per click. |

**Not to build** (`docs/test-hardening-plan.md` §5): no DOM snapshot tests, no jsdom
assertions about dark mode, no WebDriver/Playwright harness over the packaged app.

**Test-infrastructure cost, up front:** Monaco cannot run in jsdom (needs real layout,
`ResizeObserver`, `matchMedia`), so every code-screen vitest must mock the dynamic import —
the MermaidDiagram shape. Budget for it explicitly; discovered late, this is how a screen
ends up untested.

**Manual** — new `TESTING-CHECKLIST` §13x, following the §13a/§13b pattern (exact
user-facing copy quoted inline as the assertion; a narrow-window clause with the ≥44px
rule; a both-themes clause). Must include: **re-run the mermaid both-themes pass**, since
widening `style-src` will change diagram rendering.

**End-to-end**: `npm run tauri dev` from `shell/`, Developer profile, grant a trusted root,
have Addison edit two files, confirm both appear in the review surface with correct diffs,
revert one, restart the app, confirm the other shows the honest non-revertable line rather
than a failing button.

---

## Owner decisions this plan surfaces

1. ~~**`UndoManager.prune()`** conflicts with the review surface (prerequisite 3).~~
   **ANSWERED 2026-08-08, as recommended** — recency arm on reverted rows only, age arm
   kept, `listEdits` bounded in the surface build. Built the same day; prerequisite 3
   above owns what shipped and what it costs.
2. ~~**"The floor protects Addison's DATA, not Addison's CODE"**~~ — **ANSWERED
   2026-08-06, ahead of this wave, and as recommended.** The running app's bundle joined
   the protected set (`filesystem.rs::addison_app_bundle`), so the seatbelt denies writes
   to `/Applications/Addison.app` exactly as it denies the data dirs. A *developer's*
   checkout is still writable by design. [`KNOWN-GAPS.md`](KNOWN-GAPS.md) owns that entry
   and states what remains open (the wording, not the code). The surface still makes it
   vivid: if the repo sits under a trusted root, the person will *see* `policy.py` in the
   file tree.
3. ~~**`style-src 'unsafe-inline'` applies to the Simple profile too.**~~ **ANSWERED
   2026-08-08 by building both mitigations in the same change, and the first one is
   STRIP rather than confirm.** `shell/src/lib/sanitizeSvg.ts` parses mermaid's rendered
   SVG into an inert `<template>` and removes every `<style>`, `style=`, `on*` and
   `<script>` before injection. Stripping costs nothing that the app had: `style-src
   'self'` was already blocking both, so this keeps diagram rendering byte-identical to
   the shipped app while closing the path the widening would otherwise open — and it is
   a real path, because a flowchart's `classDef` becomes a `<style>` block that applies
   to the WHOLE document and its `style A fill:…` becomes an attribute that can pin one
   node over the window. `PermissionCard` got the hardened container (`isolation:
   isolate`, its own stacking context, `data-consent-card`), and the structural half is
   now a test: the card is never a descendant of `.markdown-body` or a diagram.

   **What the mitigation does NOT cover, stated rather than implied.** The widening is
   global, so a Simple session carries `'unsafe-inline'` for a screen it can never reach
   — that cost is unchanged and is the honest price of this decision. `isolation:
   isolate` cannot stop a global rule aimed at the card by selector, and nothing at that
   layer could; what stops one is there being no path for such a rule to arrive, which
   is the sanitizer's job. Nothing here constrains CSS delivered by a first-party asset,
   which `'self'` admits by definition. And a SECOND injection site added later would
   bypass all of it — so `src/__tests__/cspMitigations.test.tsx` asserts at source level
   that the app has exactly one `dangerouslySetInnerHTML` and that it routes through the
   sanitizer.
