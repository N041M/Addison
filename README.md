# Addison

**A local-first AI agent that's approachable by default and powerful when you ask it to be.**

Addison is a desktop app that opens to a chat window. Behind that window is an
agent that can search the web, read files you hand it, do calculations, and
draft emails for you. By default it's built for someone who has never installed
a developer tool, never seen an API key, and will close anything that shows an
error code — but a single opt-in **Developer profile** unfolds the same engine
for technical users, without ever making the simple experience busier.

## Why this exists

Agent harnesses today (Claude Code, OpenClaw, and the like) are genuinely
powerful, but every one of them assumes a user who is comfortable with a
terminal, config files, and API keys. That leaves out almost everyone: the
parent, the small-shop owner, the grandparent who just wants help drafting an
email or summarizing a PDF.

The bet behind Addison is that **the hard problem isn't the agent — it's the
packaging and the trust.** Wiring an LLM to some tools is a weekend's work.
Making a non-technical person feel *safe* running an app that can touch their
files and browse the web on their behalf — without ever showing them a stack
trace — is the real, multi-month effort. That is the problem Addison is built
around.

## What it's trying to be

- **Zero-terminal setup.** Download → double-click → chat window opens. No CLI
  ever surfaces.
- **No API-key hunting on day one.** A conversational Setup Assistant greets you
  and walks you through getting configured; you can have your first real
  conversation before you've touched a key.
- **Visible, revocable permissions.** Every tool the agent can use is opt-in,
  explained in plain language, and shown live while it runs ("Reading
  invoice_march.pdf…").
- **Local-first.** Your conversations and memory live on your own device by
  default. Nothing is uploaded unless you turn on sync.
- **Recoverable by design.** A single "undo" reverses anything the agent did to
  your files — because every action that changes something is reversible by
  construction, not by best-effort cleanup after the fact.
- **Plain-language failure.** Errors become a sentence and a suggested next step,
  never a stack trace.

## Profiles: approachable by default, powerful on request

The way most agent tools fail is by serving developers and non-technical users
through one undifferentiated interface — too complex for the newcomer, too padded
for the developer. Addison uses **profiles** to avoid that: a single switch that
reshapes the surface without ever forking the engine or weakening the safety
model.

- **Simple (default).** The experience above, in full: guided setup, a narrow
  tool set, plain-language prompts, no jargon, no config. Nothing developer-facing
  ever intrudes here. You can live in this profile forever.
- **Developer (opt-in).** The same local-first, undo-safe engine with more of it
  exposed — bring-your-own-key up front, automations editable as plans, a
  headless/CLI entry point for scripting, raw diagnostics, and higher-risk tools
  behind an explicit opt-in.

The rule that makes this work: **a profile changes what's shown and what's on by
default — never the security model.** Per-action consent, guaranteed undo, and
key isolation hold identically in both. Arbitrary shell access is never in the
default set in *any* profile. So welcoming developers makes the simple experience
*simpler*, not busier — the power lives behind its own profile instead of on
everyone's screen.

## What it deliberately is *not*

- **No arbitrary shell or code execution by default, in any profile.** Tools are a
  small, typed allow-list (search, read a file you chose, calculate, draft a
  message). Higher-risk capabilities exist only behind an explicit opt-in and stay
  gated and undoable — never "run any command" as a default.
- **No always-on / scheduled autonomy.** The agent acts when you ask it to.
- **Not a server product.** It's a desktop app; the Developer profile exposes a
  headless/CLI entry point for scripting, but hosting isn't the point.

## Who it's for

- **By default:** someone comfortable with email, Word, and Excel but who has never
  used a terminal — and wants help with everyday things: "summarize this," "draft
  this reply," "look this up," "add these numbers and save it as a document."
- **Via the Developer profile:** technical users who want a local-first, auditable,
  undo-safe agent they can script and extend — without giving up the trust model or
  reaching for a heavier developer harness.

## How it's built

Three parts, kept at three different trust levels so the safety model is
enforced by architecture, not convention:

- **Desktop shell** — Rust (Tauri). Holds the real OS permissions (keychain,
  file picker) and supervises the agent. Small binary, no bundled runtime the
  user has to install.
- **Agent core** — Python. Runs the conversation loop, the tool set, the
  permission gate, local memory (SQLite), and undo. It has no OS permissions of
  its own — every file or system action routes back through the shell.
- **Frontend** — React. Renders the chat and the permission cards; it never sees
  your API keys and never talks to the network directly.

API keys, when you add your own, live in your operating system's keychain — never
in the app's files, never in the frontend, never in this repository.

## Status

Early scaffold. The safety-critical foundations are working and tested:

- Local database schema and data model
- A tool registry that **mechanically refuses to register any state-changing
  tool that can't be undone** — the backbone of the whole safety model
- The permission gate that gates every tool call
- The orchestration loop, model router, and undo manager

Everything else — the provider integrations, the desktop shell wiring, the
reusable "Routines" feature, and local-model support — is scaffolded and being
built out in sequence.

## License

Not yet chosen.

---

*Detailed product and engineering design documents exist but are kept private
during early development.*
