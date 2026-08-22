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
3. **Size: shell-side downscale.** The shell resizes/re-encodes large images
   (≤1600px long edge, JPEG) before anything crosses the stdio pump, so a phone
   photo simply works. The 1 MiB text-pick bound is untouched; the image path
   gets its own bounds (§4). *(Recommended: downscale.)*
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
`ImageAttachment` is `{media_type: str, data_b64: str}`, media types closed to
**png / jpeg / gif / webp** (the set all four vision APIs accept; the shell
enforces it at encode time, the adapters assert it).

Each adapter translates a user message carrying images into its own block
shape; a `tool`/`assistant` message never carries them in v1:

- **Anthropic**: `content` becomes a block list — `image` blocks
  (`source: {type: "base64", media_type, data}`) then one `text` block.
- **OpenAI / custom**: `content` parts — `image_url` with a
  `data:{media_type};base64,{data}` URL, then `text`.
- **Google**: `parts` — `inline_data {mime_type, data}`, then `text`.
- **Ollama**: the message's `images: [b64, …]` key.

**The turn gate.** Before dispatching a turn whose *new user message* carries
images, the orchestrator asks the resolved provider's capabilities; on
`vision=False` the turn is refused with one plain sentence ("The model
answering right now can't look at pictures. Switch to one that can and send it
again.") and nothing is sent. Same rule, same voice as `_gate_image_result`,
which stays untouched for the tool path. History replay is quieter: when an
*older* message's images reach a text-only model mid-conversation (routing
degraded, the person switched), the adapter drops the pixels and substitutes
`[picture: {name}]` in the text — a degraded answer beats a refused turn the
person did nothing to cause, and the disclosure line (§5) says who answered.

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
  1600px on its long edge and re-encodes: JPEG (quality 80) for opaque images,
  PNG where alpha exists, GIF/WebP under the pixel bound pass through. The
  encoded result must land under **2 MiB** or the shell steps the quality/size
  down until it does. What crosses the pump is base64 of *that*.

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
  with the process and is cleared on send and on `conversation.new`.
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
- **The context budget** counts each image as a flat 1,600-token estimate — a
  stated approximation (provider tiling differs), erring high. The honest
  limit rides in this plan: the budget's image arithmetic is an estimate and
  says so at the constant.
- **`model.availableRoles` model rows gain `vision: bool`** (from the owning
  adapter's capabilities; Ollama's per-model answer where it has one), the
  `truncation_finish_reasons` pattern: a capability carried out as structured
  data, never prose.

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

- A text-only model mid-history gets `[picture: name]`, not pixels — degrade,
  disclosed by the "Answered by" line, never an auto-switch.
- The budget's image cost is a flat estimate.
- Attachments live in SQLite as base64; a person who attaches many large
  photos grows their database by up to ~8 MiB a message, bounded but real.
- The composer's warning appears only for explicit picks; strategy-routed
  turns learn from the refusal sentence instead.
- `read_file`'s image path remains the old text shape (§7).
