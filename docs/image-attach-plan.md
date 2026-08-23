# Image attach (the composer's front door, and a real image path behind it)

**Status: DECIDED AND SCHEDULED 2026-08-23** (proposed and answered the same day;
phases 1–4 scheduled). This file owns the subject: attaching a picture to a chat
message, carrying it to a vision model as a real image block, and showing it in
the thread. The KNOWN-GAPS entry *"No file-attach/drop UI → `read_file`
unreachable from chat"* is the origin of the front-door half; the step-10
capability flags (`ProviderCapabilities.vision`, spec §4.1.1 item A) are the
origin of the gate it rides through. Anything else that mentions attaching an
image links here.

The one sentence to carry away: **an attachment is the person's own content,
picked with their own hands, and it grants nothing** — no new tool exists, the
model cannot ask for an attachment, and the only thing that changed for the
model is that a message it was already going to receive can now carry pixels.

## 1. What is true today, and the part that surprised us

- **There is no image path to any model.** A picked image is base64-encoded by
  the shell (`filesystem.rs::read_scoped_handle`) and then flattened into a
  *text* tool-result string (`orchestrator._result_as_text`); all four provider
  adapters send `str(content)`. The 1 MiB pick bound is justified in its own
  docblock *by* that absence ("charged to the turn as base64 TEXT"). So the
  existing `read_file` → `_gate_image_result` machinery gates a path that no
  model can actually *see* — the gate is real, the payload is noise. "Image
  support" therefore means building the block path, not just the button.
- **The provenance machinery is right and is reused, not rebuilt.**
  `shell.pickFile` mints a session-scoped opaque handle (never a path);
  `read_scoped_file` refuses non-regular files and oversize picks. The webview
  is forbidden to call `shell.*` (spec §1.3); the one sanctioned shape is a
  Frontend→Core RPC that fans out to the shell, which is how routine import
  already picks a file (`rpc/routines.py`).
- **The CSP is not a blocker.** `img-src 'self' data:` is pinned and argued for
  in `tests/test_csp_is_pinned.py`; `blob:` and `asset:` are refused by name.
  Everything here renders as `data:` URIs and the policy does not change.
- **`ProviderCapabilities.vision` exists and is honest**: Anthropic, OpenAI
  (and the custom server via the same adapter) and Google say `True`; Ollama
  asks the model (`"vision" in declared`); the Setup Assistant relay omits it.
  Nothing surfaces it to the frontend today.

## 2. The owner's decisions (2026-08-23, recorded beside their recommendations)

1. **Entry point: the attach button only.** A ＋ control in the composer strip
   opening the native picker. Drag-drop (Tauri's native drag-drop event,
   shell-side paths) and paste (clipboard bytes arriving in the webview — the
   lowest-trust process, a new trust shape) are both **deferred**, each one its
   own decision when wanted. *(Recommended: button.)*
2. **Display: inline thumbnail.** A restrained, height-capped thumbnail in the
   user message row, name and size in mono beneath. The brief's "Assets: None"
   governs chrome, not a person's own content. *(Recommended: thumbnail.)*
3. **Size: shell-side downscale.** The shell resizes/re-encodes large images (to
   the vendors' own 1568px long edge, JPEG) before anything crosses the stdio
   pump, so a phone photo simply works. The 1 MiB text-pick bound is untouched;
   the image path gets its own bounds (§4). *(Recommended: downscale.)*
4. **Phone photos: deferred.** A Telegram photo from a paired phone is a
   different provenance than a person-picked file (the bytes come via
   Telegram's servers, from a program nobody audited). The adapter goes on
   counting media-only messages in `PollResult.dropped`; the captioned-photo
   silent discard stays as-is and is recorded in KNOWN-GAPS with this
   provenance argument. *(Recommended: defer.)*

Two defaults ratified with the decisions, both forced by recorded rules:

- **Every profile gets it.** Image-attach is companion-grade; the person-picked
  file is exactly the trusted shape the step-7 phase-4 provenance entry names,
  and no new tool or gate surface exists to leak (§6).
- **`vision=False` warns and never switches** (spec §4.1.1 item A). The
  composer says so at attach time when the explicitly-picked model can't look
  at pictures; the core refuses at send with a plain sentence either way. The
  warning is never the enforcement; the refusal is (§5).

## 3. Phase 1 — the image block path (core + providers)

`Message` (`providers/base.py`) gains `images: tuple[ImageAttachment, ...] = ()`
beside its `content: str` — a parts union was considered and rejected: every
consumer of `content` today assumes a string, and an optional tuple that is
empty everywhere but a user turn with attachments leaves all of them untouched.
`ImageAttachment` is `{media_type: str, data_b64: str}` and is **frozen** (what
the person previewed is byte-for-byte what is sent), media types closed to
**png / jpeg / gif / webp** — the set all four vision APIs accept, named by
`ALLOWED_IMAGE_MEDIA_TYPES` in `providers/base.py`. The shell enforces it at
encode time (§4: decoding IS the validation, and the re-encode lands on one of
the four by construction). The adapters were to *assert* it as well and
deliberately do not: an assertion there can only turn a shell bug into a stack
trace in the middle of somebody's sentence, which the house rule forbids. The one
place a bad type can still be answered with a plain sentence is §5's
`pickAttachment`, and that is the constant's second reader.

Each adapter translates a user message carrying images into its own block
shape; a `tool`/`assistant` message never carries them in v1:

- **Anthropic**: `content` becomes a block list — `image` blocks
  (`source: {type: "base64", media_type, data}`) then one `text` block.
- **OpenAI / custom**: `content` parts — `image_url` with a
  `data:{media_type};base64,{data}` URL, then `text`.
- **Google**: `parts` — `inline_data {mime_type, data}`, then `text`.
- **Ollama**: the message's `images: [b64, …]` key (raw base64, no `data:` prefix).

**ANTHROPIC IS PROVEN AGAINST THE REAL API — the others are not yet.** On
2026-08-23 the owner ran `scripts/check_image_wire.py`, which drives these
adapters (never a hand-written request) and sends a flat purple square with the
question *"What is the single dominant colour of this image?"* — a word the prompt
never contains, so an answer from the text alone cannot pass. Anthropic answered
**purple**. That is the first evidence in this feature that is not a test agreeing
with its author: the block shape is accepted, the base64 is right, the
image-before-text order works, and the pixels genuinely arrived at a model.

**What it does not cover, stated so the green does not spread:** OpenAI, Google
and Ollama are still documentation-checked only, and Google is the one most worth
running (snake_case in an otherwise camelCase API). The harness feeds a synthetic
PNG straight to the adapters, so it says nothing about phase 2's decode and
downscale, the picker, the composer, persistence, or the thread — those need the
app.

**All four shapes were checked against the vendors' own documentation** the same
day, because until then every one of them was asserted only against tests written
from the same belief that produced the code — a suite that cannot disagree with
its author. What the check settled, recorded here so the next reader does not
repeat it:

- Anthropic's block is exactly the shape above, and **images before text is the
  vendor's own recommendation**, not a guess we made — the ordering the adapters
  already used.
- Google really is **snake_case** (`inline_data` / `mime_type`) on the REST
  endpoint, despite that API being camelCase almost everywhere else. The oddity
  had a comment in the adapter reading as though somebody had noticed a
  discrepancy and talked themselves out of it; it turns out they were right.
- The supported set is **PNG, JPEG, GIF, WebP** at Anthropic and at OpenAI alike,
  which is `ALLOWED_IMAGE_MEDIA_TYPES` entry for entry — the closed four were
  guessed correctly.
- **Only a GIF's first frame is ever used** ("animations are unsupported"), by
  both vendors. §4's decision to re-encode one frame and accept the animation
  loss is not a compromise: it is what the API does anyway.
- Size ceilings are far above ours (10 MB base64 at Anthropic direct; 5 MB on
  Bedrock/Vertex), so the 2 MiB encoded bound is never the binding one.

**The turn gate.** Before dispatching a turn whose *new user message* carries
images, the orchestrator asks the resolved provider's capabilities; on
`vision=False` the turn is refused with one plain sentence ("The model
answering right now can't look at pictures. Switch to one that can and send it
again.") and nothing is sent. Same rule, same voice as `_gate_image_result`,
which stays untouched for the tool path. History replay is quieter: when an
*older* message's images reach a text-only model mid-conversation (routing
degraded, the person switched), the adapter drops the pixels and substitutes
`[picture]` in the text, one per image — a degraded answer beats a refused turn
the person did nothing to cause, and the disclosure line (§5) says who answered.
The marker lost its filename when phase 1 was built: `ImageAttachment` carries
`media_type` and `data_b64` and no name (a name is display-only, so it rides the
attachment *record* in §5 and never the wire), and an invented one is worse than
none — a model told the file was "receipt.png" will answer about a receipt it
never saw. The degrade is implemented **only in `ollama_provider`**, the one
adapter whose answer to "can you see" varies per model; for the three cloud
adapters `vision` is True by construction, so the same code there would be dead.

## 4. Phase 2 — the shell (downscale and the image read)

Two new commands in `filesystem.rs`, both Core→Shell like their siblings:

- **`shell.pickImage {} → {fileHandle, name, byteSize}`** — `rfd` picker with
  an image-extension filter, minting the same session-scoped handle
  `pickFile` does. A separate command rather than a parameter so `pickFile`'s
  contract doesn't grow a mode.
- **`shell.readPickedImage {fileHandle} → {content, mediaType, name, byteSize,
  width, height}`** — resolves the handle, refuses non-regular files, refuses
  originals over **24 MiB before reading** (a bound against absurdity, not a
  budget), decodes with the `image` crate — **decoding is the validation**; a
  file that doesn't parse as an image is refused with a plain sentence, which
  retires extension-guessing for this path — then downscales anything over
  1568px on its long edge and re-encodes: JPEG (quality 80) for opaque images,
  PNG where alpha exists. The encoded result must land under **2 MiB** or the
  shell steps the quality down (60, 40) and then the long edge (1200, 800)
  until it does, refusing plainly if the smallest step still won't fit. What
  crosses the pump is base64 of *that*.

  **Pass-through is the exception**, and it needs all three conditions at once
  (phase 2 built it wider than this file first said — it named only GIF/WebP,
  which would have re-encoded every small screenshot for nothing): a picture
  already one of the four media types **by decoded format, never by extension**,
  already under 2 MiB, and already within the long edge crosses byte-for-byte.
  Re-encoding a small photo is pure loss, and a PNG screenshot of text — the
  most common thing anybody attaches — is exactly what a JPEG round-trip ruins.
  A GIF that fails any of the three loses its animation: what is re-encoded is
  the first frame, which is what a model looks at anyway.

The `PICKED_FILE_SIZE_BOUND` docblock is rewritten in the same PR: the 1 MiB
bound remains the *text* pick's bound and its base64-as-text justification now
names this plan as the image path that made it text-only.

## 5. Phase 3 — the wire, the send, and what is remembered

- **`conversation.pickAttachment {} → {attachmentId, name, mediaType, byteSize,
  dataB64}`** (Frontend→Core, mirrored in both protocol files). The core calls
  `shell.pickImage` + `shell.readPickedImage`, mints its own attachment id, and
  caches the encoded image in memory keyed by that id. **Read once, at pick**:
  what the person previewed is byte-for-byte what is sent, and a file edited
  between pick and send changes nothing. The webview gets the base64 *for
  display* and never sends bytes back — at send time it names ids only, so
  nothing the webview holds can become "what the model saw". The cache dies
  with the process and is cleared on send and on `conversation.new`. It is also
  **capped at the same 4**, which phase 3 added: the pending set exists only to
  become a message, so a fifth pick is refused in a sentence rather than growing
  memory, and an abandoned pick costs one of four slots and nothing more.
  A **worker job**, not an inline handler — it opens a modal dialog and then
  decodes, and the read loop has to stay free to deliver `permission.respond` and
  `conversation.stop` (`model.startLocalSetup`'s 2026-08-22 move, and
  `routine.importPreview`'s shape).
- **`conversation.discardAttachment {attachmentId} → {ok}`** (phase 3, not in the
  original plan). The ✕ on a composer chip frees the slot it was holding. An id
  the core is not holding is a silent no-op: there is nothing to say about a thing
  that is already gone, and clicking ✕ twice must not produce an error.
- **`conversation.sendMessage` gains `attachments?: [id, …]`** (cap: **4**).
  Unknown or already-spent ids refuse the send with a plain sentence. The
  empty-text guard (closed 2026-08-08) is **relaxed by exactly one case**:
  empty text with attachments present becomes the message "" + pictures — a
  person sending just a photo is ordinary; empty text with no attachments
  still refuses.
- **Persistence**: a `message_attachments` table (id, conversation id, message
  id, name, media type, byte size, base64 content) written with the message
  row. `conversation.load` messages gain `attachments: [{id, name, mediaType,
  dataB64}]`, so the thumbnail survives a reopen and history replay can carry
  the image blocks forward. Downscaled-only (≤2 MiB each, ≤4 per message), so
  the store grows by bounded, person-caused amounts. No credential ever has a
  path into this table; G1 is not in play.
- ~~**The context budget** counts each image as a flat 1,600-token estimate.~~
  **CUT in phase 3, because there is nothing to count in.** This plan assumed a
  turn-size estimator that walks messages; there is none, and never was. §4.8
  measures a turn from the **provider's own usage report**
  (`orchestrator._report_context_usage`: `input_tokens + output_tokens`, handed to
  `assess_budget`), and that number already counts the pictures — each provider
  bills its own tiling, which is exactly the thing a flat 1,600 could only
  approximate. Adding an estimate on top would not improve the measurement, it
  would double-count it, and `context_budget.py` would grow a seam it does not
  have (it is two pure functions over data, and reads no message content at all).
  So the budget is unchanged and its arithmetic stays the provider's.
- **`model.availableRoles` model rows gain `vision: bool`** (from the owning
  adapter's capabilities), the `truncation_finish_reasons` pattern: a capability
  carried out as structured data, never prose. `models_catalog.PROVIDER_VISION`
  holds the four answers and a test asks the four adapters themselves, so the copy
  cannot drift. **Local models carry no such field**, which is phase 3's one
  amendment here: Ollama's answer is per model (`POST /api/show`), the list path
  does not fetch it, and adding a request per row to a list path to find out is
  the wrong trade. Absent means *unknown*, and the composer says something only
  where it knows the answer is no.

## 6. Phase 4 — the frontend

- **The composer**: a ＋ control (the glyph vocabulary already owns ＋) at the
  left of the controls strip — the strip's first left-aligned member — calling
  `pickAttachment`. Pending attachments render as a chip row above the
  textarea: a small thumbnail, mono name + size, a ✕ to remove. `canSend`
  becomes `text || attachments`. New-chat clears pending attachments (the
  `composerSeed` precedent, same line in `App.tsx`). While a pick is open the
  button disables; nothing else blocks.
- **The warning line**: when an attachment is pending and the explicitly
  picked model reports `vision: false`, one quiet mono 10.5px `disabled` line
  in the strip — "This model can't look at pictures." No accent (the accent
  is for actions, selection, live state), nothing disabled: the person can
  still send, and the core's refusal sentence is the enforcement. When
  routing (no explicit pick) will decide, the composer says nothing — it
  cannot know, and a guess that is wrong trains people to ignore the line.
- **The thread**: user rows (which never pass through Markdown) grow an
  attachment block ahead of the text — thumbnails as `data:` URIs,
  height-capped ~240px, hairline-bordered, name in mono beneath, in the 2px
  left-rail idiom. No lightbox in v1. The optimistic message carries the
  previews so the picture appears the moment Send is pressed.

## 7. What this deliberately does not touch

- **No new tool, no gate change, no registry change.** `pickAttachment` is a
  person-driven RPC like `workspace.pickDirectory`, absent from the registry;
  no model-addressed surface can mint, list, or read an attachment. SAFE
  invariants 1–4 are untouched byte-for-byte.
- **The `read_file` tool path is unchanged** — still base64-text, still gated
  by `_gate_image_result`. Upgrading tool results to image blocks is real
  work per provider (OpenAI's tool role takes no images) and waits for a
  reason; KNOWN-GAPS records it.
- **Screening does not run on attachments, deliberately.** Screening exists
  for *external* origins; a file the person picked with their own hands is
  the trusted shape (the step-7 phase-4 provenance rule), same as the text
  they type. An image that lies is between the person and their picture.
- **MCP image parts stay refused; Telegram media stays dropped** (owner
  decision 4). Both entries in KNOWN-GAPS gain a pointer here.
- **CSP, G1–G4: no changes.** Verified against the pinned policy; `data:` in
  `img-src` predates this plan.

## 8. Build order

Four PRs off `master`, in phase order (1: providers + gate; 2: shell; 3: wire
+ store; 4: frontend), each green through `./scripts/gates.sh` before merge,
docs (BUILD-LOG, ROADMAP, KNOWN-GAPS strike + new entries) riding with the
last. Mutation checks on what matters: the turn gate (a `vision=False` refusal
must die when the check is removed), the adapters' block shapes, the shell's
decode-refusal, the spent-id refusal, and the empty-text relaxation's narrow
edge.

## 9. Limits that survive success

- A text-only model mid-history gets `[picture]`, not pixels and not a filename
  (§3) — degrade, disclosed by the "Answered by" line, never an auto-switch.
- A picture sent while there is **no key yet** routes to the Setup Assistant relay
  (§4.6), which cannot see, so phase 1's gate refuses it — *after* the message is
  persisted. Left as it is deliberately: nothing external was called, the message
  and its picture are in the person's own transcript to send again once a key
  exists, and the refusal sentence is in the thread saying why.
- A **§4.8 continuation** copies the last few turns verbatim into a new
  conversation, pictures included. Those copies are written as new rows and lose
  the person's filename (`Message.images` carries none — §3), so a continued chat
  shows the picture and not its name. The bytes are duplicated, exactly as the
  carried text is. **The fix, if it is ever wanted, is a row-to-row SQL copy**
  keyed off the old message id, which would carry the filename across for free;
  it was not built because the duplication is bounded by the same four-per-message
  ceiling as everything else here.
- **`conversation.load` ships every attachment's full base64, every time a chat is
  opened** — up to ~2.7 MB per picture, for thumbnails drawn at 240px. Switching
  between two picture-heavy chats pays it each way. The honest fix is a second,
  small thumbnail column written at pick time (the shell already decodes and
  resizes there) with the full bytes fetched on demand; the model's own history
  half would go on reading the full rows. Recorded rather than built: it is a
  schema change, and the wire it would change is the one phase 3 just settled.
- **Every later turn of a picture-bearing conversation re-sends those pictures to
  the provider.** History is replayed whole, so a photo attached once is ingested
  again on every turn that follows it — real money and real latency, honestly
  counted by §4.8's budget (which reads the provider's own usage report) but not
  reduced by anything. Two candidate fixes, both owner calls because both change
  what the model receives: an Anthropic `cache_control` breakpoint on the last
  history block (cache-reads instead of re-ingestion, no behaviour change), or
  degrading pictures older than N turns to the `[picture]` marker the Ollama path
  already has.
- **A text-only CUSTOM server still fails, and the gate cannot know.** `custom` is
  somebody's own OpenAI-compatible endpoint, so Addison no longer claims it can
  see (§5: the row ships no `vision` field at all, and the composer stays quiet).
  But quiet is not the same as safe: attaching a picture to a text-only llama.cpp
  or vLLM server sends `image_url` parts it will refuse, and because history
  replays whole, **every later turn of that conversation refuses too**. The fix
  that would close it is a capability probe against the configured server, which
  is a network call with its own failure modes and its own owner decision; what
  ships instead is the absence of a false claim.
- Attachments live in SQLite as base64; a person who attaches many large
  photos grows their database by up to **~10.7 MiB a message**, bounded but real.
  (Four pictures × the 2 MiB *encoded* ceiling is 8 MiB of image bytes, and the
  column stores base64, which is four thirds of that. The figure read 8 MiB until
  2026-08-23, when checking the arithmetic found it had been written from the byte
  bound and not from what is actually stored — a third light, in a file whose whole
  discipline is that a number is a claim.)
- The composer's warning appears only for explicit picks; strategy-routed
  turns learn from the refusal sentence instead.
- `read_file`'s image path remains the old text shape (§7).
