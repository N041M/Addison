# Attachments: image and document input — plan

**Status: PROPOSED 2026-08-23. Nothing here is built. The owner decisions in §5
are open; phases build in order once they are answered.** This document owns the
subject; [`KNOWN-GAPS.md`](KNOWN-GAPS.md)'s "No file-attach/drop UI" entry and the
`read_file` vision-gate machinery both fold into it when it ships.

The ask (owner, 2026-08-23): a person should be able to hand Addison an image or
a document as INPUT — attach a photo, a PDF, a Word file, a spreadsheet export to
a message and have the model actually see it. The design-doc promised this shape
from the start (§7.4.1 "files you explicitly select or drag in", §4.1.1 item A's
vision gate); what shipped is half a mechanism with no door.

## 1. What exists today, stated honestly

Four facts, verified against the tree on 2026-08-23. Anything built here stands
on them.

- **The tool half exists and is unreachable.** `read_file` (LOW, read-only, in
  the SAFE view) takes an opaque `file_handle` that only `shell.pickFile` can
  mint, and the handle→path map lives shell-side and dies with the session — the
  core never sees a raw path, which is the right security shape and is kept. But
  nothing in the UI calls `shell.pickFile`, so no handle has ever existed:
  [`KNOWN-GAPS.md`](KNOWN-GAPS.md) has carried "`read_file` unreachable from
  chat" since steps 4–5.
- **The shell's extraction is a stub wearing a bigger label.** The tool's own
  description says "PDF, Word, image, csv, text";
  `filesystem.rs::read_scoped_handle` extracts UTF-8 text and raster images
  (base64) and refuses everything else with *"Addison can't read that kind of
  file yet."* The label has been ahead of the truth since v1.
- **There is no image path to any model.** `providers/base.py::Message.content`
  is a plain `str`, and a tool result is serialized whole by
  `orchestrator._result_as_text` — so a picked image would cross as base64
  *inside a JSON string*: unreadable by every model and charged to the turn as
  hundreds of thousands of text tokens. `PICKED_FILE_SIZE_BOUND`'s comment in
  `filesystem.rs` states this plainly ("v1 has NO image-block path") and sizes
  the 1 MiB cap around it.
- **The guards are real and stay.** The vision gate
  (`orchestrator._gate_image_result`: plain warning + manual model switch, never
  an automatic one — §4.1.1 item A), `ProviderCapabilities.vision` on all four
  adapters (Ollama probes the running model rather than assuming), the size and
  non-regular-file refusals judged from a stat before a byte is read, and the
  handle-scope refusal of unminted handles.

## 2. The design

### 2.1 Attachments are user-message parts, not tool calls

An attachment rides the message the person sends:
`conversation.sendMessage {text, attachments: [{handle, name}]}`. The core
resolves each handle through the existing `shell.readScopedFile`, and the user
`Message` carries the results as typed parts beside its text. Adapters translate
parts natively — every vision API takes images in a *user* message (Anthropic
image blocks, OpenAI image_url parts, Google inline_data, Ollama's `images`
field), which is exactly where provider support is uniform. Tool-result image
blocks are NOT uniform (OpenAI tool messages are text-only), which is one of two
reasons attachments do not go through a `read_file` tool call; the other is that
a round trip asking the model to fetch a file the person already handed over is
a wasted turn and a confusing transcript.

`read_file` itself stays registered and truthful for the model-initiated case,
and its extraction improves for free because the shell/core split below serves
both callers. No card on attach: the tool is LOW and read-only, the person
picked the file in an OS dialog this session, and a card asking permission to
read what they just handed over is the "Not now" confusion worn by a new
surface. (This is the same provenance line the MCP phase-4 decision drew:
person-picked files may reach a model's eyes; bytes a program pushed in may
not. Attachments are the strongest provenance there is.)

`Message` grows `attachments: tuple[Attachment, ...] = ()` — a parallel field,
not a rewrite of `content`, so every existing adapter path is byte-identical
when no attachment is present. `Attachment` is a frozen dataclass:
`{kind: "image" | "document", name, media_type, data_b64 | text}`.

### 2.2 Extraction: bytes in the shell, parsing in the core

The shell keeps doing what it does today — resolve the handle, stat-guard,
read the bytes, refuse oversize — and hands the CORE
`{kind: "image"|"text"|"binary", media_type, content}`, adding only the
`media_type` guess and the new `binary` kind (base64, currently refused).
Parsing PDF and Word into text happens core-side, in a new
`agent_core/extraction.py`.

The reason is the trust gradient, and it is worth stating because the reflex
runs the other way ("the shell already has the file"). Parsing a hostile file
format is the classic way in, and the shell is the HIGHEST-trust process —
keychain, file dialogs, updater. The core is the least-privileged place that
can do the work: no OS permissions of its own, every effect gated back through
IPC. New parser dependencies belong there or nowhere. Extraction failures
become one plain sentence ("Addison couldn't read anything out of that file"),
never a stack trace.

Formats, v1: PDF (text extraction — no OCR; a scanned PDF honestly yields "no
text Addison could read" plus the page count), `.docx` (word/document.xml text
via `zipfile` + `xml.etree`, both stdlib), csv/tsv/plain text (already work),
and the raster images `is_image_path` already names. Legacy `.doc` is refused
with the existing sentence. Whether PDF parsing gets a vetted pure-Python
dependency (`pypdf`) or ships text-layer-only via stdlib is owner decision §5.1
— stdlib-first is the convention and the convention is the default.

### 2.3 The vision gate moves to send time

If a message carries an image and the resolved provider reports
`vision=False`, the turn does not start: the composer keeps the message and the
existing gate sentence is shown ("…Switch to a vision-capable model and try
again."), reworded only if the owner asks. Manual switch, never automatic —
§4.1.1 item A's rule, unchanged. This check needs the RESOLVED candidate, so it
lives where `model_router.resolve()` answers, not in the frontend; the routing
chain may also simply skip non-vision candidates for image-bearing turns —
that is the availability half of routing (shipped) doing its job, not the
deferred confidence half. `_gate_image_result` stays for the tool path.

### 2.4 Screening and the poisoned document

Extracted document text is screened (`agent_core/screening.py`) exactly as a
web page is, note idiom and audit kind included — pending owner decision §5.2,
because decision 5 of 2026-08-13 said local file reads are NOT screened for
now. The argument for flipping it here: an attached PDF is very often a file
that arrived by email, which is the poisoned-document channel the screening
deferral named as its fourth trigger, and the Knowledge entry in KNOWN-GAPS
already flags this exact boundary as the reason to revisit. The honest limit is
the standing one: screening is six shapes, prose-shaped instructions pass, and
an IMAGE cannot be screened at all — instruction text inside a picture reaches
a vision model unmarked, and the plan says so rather than implying otherwise.

### 2.5 Persistence, budget, caps

- **Persistence** (owner decision §5.3): attachments are stored on the message
  row (`message_attachments` table, bytes capped by the same 1 MiB bound), so a
  reloaded conversation still shows the image and a continuation can re-carry
  it. The alternative — session-only, history shows a name-only placeholder —
  is cheaper and honest, but makes G3-restored and reopened chats quietly
  lose what the person showed Addison.
- **Context budget**: attachment tokens ride the existing measurement where the
  provider reports usage truthfully; the estimate for images is per-provider
  and rough, and the budget manager's conservatism already absorbs rough.
- **Caps**: `PICKED_FILE_SIZE_BOUND` (1 MiB) stands for v1, which refuses many
  phone photos — stated as the honest cost, with shell-side downscaling
  recorded as the phase-3 fix (it needs an image codec in the shell, which is
  §2.2's trust argument pointing the other way, so it is its own decision
  later, not a rider).

### 2.6 Surfaces

Attach button in the composer (plain glyph, no sparkle) + drag-drop onto the
thread. Drop is a SHELL event (Tauri's file-drop fires shell-side): the shell
mints handles from the dropped paths and emits `{handle, name}` to the webview
— the webview still never sees a raw path, same invariant as the picker.
Attached files render as chips above the composer (name + remove) and, in the
thread, as a thumbnail (image) or a name chip (document) on the sent message —
dark-v4 idiom, hairline chrome, no accent. **Simple gets all of it** (pending
§5.4): a photo of a letter is the single most Mira-shaped input there is, the
path is read-only and person-picked, and every refusal is already a plain
sentence.

## 3. Phases

1. **The door, and documents.** `shell.pickFile` wired to a composer attach
   button + shell-side drop; `sendMessage` attachments; `Attachment` on
   `Message`; core-side extraction (PDF/docx/csv/text); document text as a
   labeled part every provider renders as text (works with zero adapter
   changes); screening per §5.2; persistence per §5.3; protocol.py/protocol.ts
   in lockstep; the `read_file` label made true. Ships alone: documents work
   end-to-end before images do.
2. **Images for real.** Adapter-side image parts (four adapters, each in its
   own idiom, no `isinstance` — the part translation lives per-adapter exactly
   as tool-call translation does); the send-time vision gate; thumbnails in
   thread and history. `_gate_image_result` keeps the tool path honest.
3. **Recorded, not scheduled:** downscaling oversize images, Anthropic-native
   PDF blocks where capability allows (replacing extraction for that provider
   only), clipboard-paste of images, audio (the capability flag exists and
   nothing else does), OCR.

Each phase is one PR train with the standard rigor: gates on the merged result,
mutation-proof the load-bearing tests, adversarial pass over the fixes.

## 4. Deliberately not built

- **No automatic model switching** on an image (v2, §4.1.1's own line).
- **No MCP/tool-server images** — unchanged; that is the promoted-allowlist
  conversation and it is not this one.
- **No folder attach, no persistent file access** — a handle is one file, one
  session; standing access to a folder is workspace trust, a different feature
  with a different ceremony.
- **No OCR and no image screening** in any near phase; the limit is stated in
  §2.4 rather than solved.

## 5. Owner decisions (each blocks the phase named)

1. **PDF parsing dependency** (blocks phase 1): admit `pypdf` (pure-Python,
   vetted, real text extraction) into the core, or ship stdlib-only (docx/csv/
   text now, PDF refused with the honest sentence until decided)?
   *Recommendation: admit `pypdf`; a PDF-less attachments feature fails the
   most common case people will try first.*
2. **Screen attached-document text?** (blocks phase 1): flips one edge of the
   2026-08-13 decision 5 (local file reads are not screened). To be clear about
   what is being bought: screening MARKS the six instruction shapes it knows and
   nothing else — a backstop, with the cards still the only authority
   ([untrusted-screening-plan.md](untrusted-screening-plan.md) owns that
   statement). *Recommendation: yes; an attached file is often one that arrived
   by email — the standing channel the screening deferral's fourth trigger
   named — and the cost is one note in front of marked text.*
3. **Persist attachment bytes on the message row?** (blocks phase 1).
   *Recommendation: yes, capped; a chat that forgets what it was shown reads
   as data loss.*
4. **Simple profile in v1?** (blocks phase 1's surface). *Recommendation: yes —
   strongest persona fit in the queue; nothing here is a developer affordance.*
5. **Raise the 1 MiB image cap via shell-side downscale?** (blocks only phase
   3; phase 1–2 ship at 1 MiB). *Recommendation: defer, revisit with real
   refusal counts.*
