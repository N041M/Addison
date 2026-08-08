# Phase 3 — The Review Surface: a bespoke IDE for the Developer/OPEN profile

**Status:** PLAN, approved 2026-07-25. Not started — but **UNBLOCKED as of
2026-08-07**: all three prerequisites have landed (step 6 widget capability tiers
and step 7 the MCP client on 2026-08-06/07, and step 8 the automation keyword gate
on 2026-08-07, all four of its phases). Nothing is waiting on anything now; the
surface waits only on somebody starting it.
[`../ROADMAP.md`](../ROADMAP.md) owns status — trust it over this line.

> **This redefines what "Phase 3" means.** Before this plan, Phase 3 meant packaging /
> signing / notarisation / auto-updater / binary restore / Secure-Enclave identity, and
> four documents scoped it that way (`addison-design-doc.md` §11, `architecture.md`,
> `addison-engineering-spec.md` §11's Phase-3 note, `HANDOFF.md`). This plan adds a
> Developer *surface* to that phase. **That redefinition has since been written into
> those documents** (`ee38dbe`, 2026-07-25 — see [`BUILD-LOG.md`](BUILD-LOG.md)), so
> amendment §14's authoritative-docs-first rule is already discharged for it; the line
> numbers this paragraph used to carry are gone because they were stale within a week.

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

### Measure, then widen — do not guess

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

2. **`shell.readWorkspaceFile` has no size ceiling.** `filesystem.rs:261` `read_workspace_path`
   does a bare `std::fs::read` — `UNDO_SIZE_BOUND` (256 KiB) guards only the write path's
   prior capture at line 238. A 500 MB file is reachable *today* through the shipped
   `read_project_file` tool and would wedge the line-delimited bridge. Fix it with a
   refusal (not truncation) for the tool path. **This tightens a shipped tool — it is its
   own PR, not a rider on this one.**

3. **`UndoManager.prune()` has zero call sites** (verified: the only `prune()` call in the
   tree is `snapshot_manager.py:620` calling its own). `action_snapshots` grows without
   bound. Invisible today; the moment you list it, the list is unbounded and includes
   edits whose files may no longer exist. **But wiring it as written would be worse** —
   `prune_action_snapshots` spans reverted *and* unreverted rows, so a 20-action startup
   prune would delete unreverted rows and make live on-disk changes both unlistable and
   unrevertable. Recommended resolution: apply the recency arm to **reverted rows only**,
   keep the age arm, and bound `listEdits` independently. This is a genuine conflict
   between §4.5 retention and this feature — flag it for an owner call rather than
   deciding it inside the build.

---

## Docs first — Phase 1 before Phase 2, per amendment §14

The repo's own rule is authoritative docs before code, and this wave needs it more than
most: **"Phase 3" is currently defined as packaging / signing / notarisation / auto-updater
/ binary restore / Secure-Enclave identity** in four places (`docs/addison-design-doc.md`
§11, `docs/architecture.md:126–129`, `docs/addison-engineering-spec.md:1285`,
`docs/HANDOFF.md:1137`). Adding a Developer surface to Phase 3 is a redefinition and must
be written as one, in the style of the 2026-07-20 amendment inserts.

Also required, because these are the two sentences this wave most strains:

- `docs/addison-engineering-spec.md:840` — *"The Developer profile deliberately reuses
  surfaces that already exist for other reasons… exposing it is a packaging decision, not
  new capability."* The code screen is the **first Developer surface that is not a reuse**.
  Name the departure explicitly, in the style of the step-5 owner-decision notes.
- `docs/addison-design-doc.md:310` — *"Developer mode adds surfaces, never a different
  skin."* Honored: the screen is in the same dark direction throughout and Monaco is themed
  from the repo's existing palette (below). Say so, so it reads as deliberate.

Doc deliverables: a Phase-3 scope note; updates to design-doc §11 / architecture.md /
engineering-spec §1.4-style entry / HANDOFF; a **code-surface addendum to
`docs/design-brief-dark/IMPLEMENTATION.md`** (mono at 12px for a document surface, the diff alpha ladder,
the accessibility tension below); and a new `docs/TESTING-CHECKLIST.md` §13x section.

> Note §13c is currently **owed to Phase-2 step 5** — the harness shipped without one.
> Claim the next free letter at build time rather than hardcoding it now.

Two adjacent drifts worth fixing while in these files (small, optional): design-doc §9's
floors table at line 792 still calls the G4 anchor *"binary-capturing"*, contradicting the
owner-decision note 24 lines above it; and design-doc §11 item 4 still routes
"folder-scoped workspace grant" to Phase 3 though it shipped in Phase-2 step 5.

---

## Build

### 1. Read paths — RPC, never a registry tool

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

### 2. The diff — from data that already exists

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

### 3. Per-file revert — the sharp edge

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

### 4. Frontend

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

### 5. Skinning Monaco to the dark direction — one palette, zero new tokens

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
a full-file surface read by personas aged 54 and 68. Ship `fontSize: 12, lineHeight: 19`,
matching `.markdown-body pre code`'s existing 12px (consistency with how code already looks
here beats inventing a size token), and put an **editor zoom control on the follow-up
list** rather than pretending 12px settles it.

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
| `shell.listWorkspaceDirectory` / `readWorkspaceFileForView` / `canRestoreWorkspaceFiles` added to `ipc.rs::tests::rejects_shell_methods_from_the_webview` | The webview reaching the shell directly. |

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

1. **`UndoManager.prune()`** conflicts with the review surface (prerequisite 3). Recommended:
   recency arm on reverted rows only.
2. ~~**"The floor protects Addison's DATA, not Addison's CODE"**~~ — **ANSWERED
   2026-08-06, ahead of this wave, and as recommended.** The running app's bundle joined
   the protected set (`filesystem.rs::addison_app_bundle`), so the seatbelt denies writes
   to `/Applications/Addison.app` exactly as it denies the data dirs. A *developer's*
   checkout is still writable by design. [`KNOWN-GAPS.md`](KNOWN-GAPS.md) owns that entry
   and states what remains open (the wording, not the code). The surface still makes it
   vivid: if the repo sits under a trusted root, the person will *see* `policy.py` in the
   file tree.
3. **`style-src 'unsafe-inline'` applies to the Simple profile too.** Mitigations belong in
   the same PR: confirm/strip `<style>` and `style=` from mermaid's injected SVG, and give
   `PermissionCard` a hardened container (own stacking context, `isolation: isolate`, no
   model-stylable ancestor).
