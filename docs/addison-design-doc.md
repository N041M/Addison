# Design Doc: Consumer Agent Harness ("Addison")
**A local-first, zero-config AI agent harness for non-technical users**

Author: Ronald Karel Grant
Status: Draft v0.1
Date: 2026-07-12

---

> **Amendment — 2026-07-20 (butler identity + guaranteed rollback).** This doc is
> amended by `docs/addison-scope-amendment-2026-07.md` (owner-approved,
> 2026-07-20). In one line: **Addison is a butler** — a good one never puts the
> house in a state you can't restore. Three shifts follow; the affected sections
> are updated in place below (look for the **Amended 2026-07-20** notes), the rest
> of the doc stands unchanged:
>
> 1. **Identity sharpens to a butler with two surfaces on one floor.** The
>    *Developer* surface becomes a real coding-agent harness (Claude-Code-class);
>    the *Simple* surface an all-in-one companion for non-technical people; and a
>    new third **Custom** profile exposes tunable *prompting* guards behind extra
>    confirmation. (§1, §3, §5, §7.11, §13.)
> 2. **Safety is redefined as guaranteed recovery.** A new global floor — **G3** —
>    guarantees a one-action rollback to a last-known-working state, realised with
>    app-state snapshots (automatic *and* on-command, keys always excluded). Four
>    inviolable floors now stand: G1 (keys), G2 (no self-trigger), G3 (rollback),
>    and the undeletable-anchor rule. (§9.)
> 3. **Scope widens, carefully.** Scheduling and auto-routing come off the
>    non-goals list without repealing G2: Addison *authors* OS-run automation but
>    never triggers itself, and powerful/armed actions need a user-typed keyword;
>    auto-routing ships as four bounded strategies; widgets become buildable in
>    every mode (capability-gated); Addison gains an MCP *client* (not a server);
>    and it stops requiring a paid frontier key to be useful. (§4, §7, §11, §14.)
>
> The motivating story defines what "safety" means here: a non-technical person's
> OpenClaw setup was permanently broken by a single "make it cheap" request, and
> its built-in rewind never fired — so for Addison, *safety is a recovery that
> always fires* (§9). Nothing here repeals an existing invariant; where the
> amendment leaves a detail open it is marked **Phase-2**.

---

## 1. Executive Summary

OpenClaw-style agent harnesses (OpenClaw, Hermes Agent, OpenHarness, Claude Code) give an LLM hands, memory, and tools — but every one of them assumes a user comfortable with a terminal, config files, and API keys. This doc specifies a harness with the same core capability (chat with an agent that can use tools, remember context, and act) but designed from the ground up for someone who has never opened a terminal.

The working name is **Addison**. It ships as a single-file installer, opens to a chat window, and is usable within 60 seconds of download with zero manual configuration.

The central design bet: **the hard problem here is not agent orchestration, it's product packaging and trust.** Model calls and tool-use loops are a solved problem you can build in a weekend for a single provider. Making a random non-technical relative feel safe running an app that can read their files and browse the web on their behalf, without them ever seeing a stack trace, is the actual multi-month effort — and supporting many models, including fully local ones, adds real scope on top of that rather than being free (§7.3).

**Amended 2026-07-20 — Addison is a butler.** The identity behind "approachable by
default, powerful on request" is now named: a butler acts only when asked, handles
the messy parts, remembers how you like things, and is *discreet and reversible* —
a good butler never leaves the house in a state you can't restore. Two surfaces
stand on one unbrickable floor: the **Developer** profile is a genuine
coding-agent harness (peer to Claude Code / OpenClaw in capability — read/edit
files, run builds and tests, iterate), and the **Simple** profile is an
all-in-one companion for people who lack the prowess or desire to stand one up. A
third, opt-in **Custom** profile (§7.11) lets advanced users tune the *prompting*
guards. The differentiator is not "another harness" — it is *the harness you
cannot brick, and cannot fall out of* (§9, G3). Alongside, Addison no longer
requires a paid frontier key to be worth using (§7.5).

---

## 2. Problem Statement

Existing harnesses fail non-technical users at four points:

| Failure point | Example | Consequence |
|---|---|---|
| Installation | `git clone`, `npm install`, `.env` files | User gives up before first message |
| Credentials | "Paste your Anthropic API key" | User doesn't know what an API key is |
| Trust/safety | Agent has raw shell access | One bad tool call breaks the OS, or user is too scared to grant permissions and disables everything |
| Mental model | Config YAML, system prompts, "skills" as folders | User doesn't know what's wrong when it doesn't work, and has no vocabulary to fix it |

Every one of these is solvable independently. Nobody has bundled the solutions into one product aimed at, say, a parent, a small shop owner, or a grandparent — as opposed to a developer who happens to want a lighter setup.

---

## 3. Goals

1. **Zero-terminal setup.** Download → double-click installer → chat window opens. No CLI ever surfaces.
2. **No API key hunting on day one.** User can start chatting immediately via a bounded free trial (§7.5) or a guided one-click key flow; "bring your own key" is available but never required to have the very first conversation. The trial is explicitly an onboarding ramp, not a standing free tier — sustained use is expected to run on the user's own subscription (§7.5, §8).
3. **Visible, revocable permissions.** Every tool the agent can use is opt-in, explained in plain language, and shown as an active indicator while running ("Addison is reading your Downloads folder").
4. **Local-first data.** Conversation history and memory live on-device by default; nothing is uploaded unless the user turns on sync.
5. **Recoverable failure.** Errors are translated into plain language with a suggested next step, never a stack trace.
6. **Cross-platform single binary.** macOS, Windows, and Linux, each as one downloadable installer.
7. **One-command recovery.** If the agent does something unwanted — a bad tool call, a file change the user didn't intend, a corrupted local state — a single built-in command undoes it. No debugging vocabulary required from the user. See §7.9 (Rewind & Self-Repair).
8. **Model-agnostic.** The agent core is not hardwired to one provider. Cloud models (Anthropic, OpenAI, Google, others) and local models (via Ollama/llama.cpp) are all pluggable behind one interface, chosen from a plain-language settings screen rather than a config file. See §7.3.
9. **Audience-adaptive via Profiles.** One product serves both the non-technical primary personas *and* technical users/developers, through an opt-in **Profile** that reshapes the surface and default capabilities over one shared engine and one unchanged safety model. "Simple" is the default; "Developer" is opt-in. See §7.11. This must make the non-technical experience *better* — nothing developer-facing intrudes on the default — not merely broaden who's served.

A note on tension: goal 8 pulls somewhat against goals 1 and 7. Local models require real hardware (RAM/VRAM), multi-gigabyte downloads, and produce meaningfully worse tool-calling reliability than the frontier cloud models this whole product is designed around (§7.3.2) — none of which is "zero-terminal, 60-second setup" for persona Mira or Petr. The resolution adopted here: model choice is a power-user surface, not the default path. A managed cloud default keeps the onboarding promise in §8 intact for the primary personas; local/BYO-provider support lives in Settings for the subset of users (privacy-conscious, cost-conscious, or just technical) who want it. This is flagged explicitly rather than quietly deprioritized, since it's a real scope increase — see §11 for how it's sequenced into the roadmap.

**Amended 2026-07-20 — two goals added, one sharpened.** The butler framing (§1)
firms the recovery goals into hard guarantees and adds a keyless-usefulness goal:

10. **Guaranteed rollback (the safety definition).** At all times there exists a
    one-action restore to a **last-known-working** state, and that restore path is
    itself unbreakable — neither the user nor the model can drive Addison into an
    unrecoverable configuration. This is a superset of goal 7 (which undoes a
    *tool action*); goal 10 restores whole-app *configuration* via snapshots, and
    is the load-bearing global floor **G3** (§9). "Safety" for Addison means this.
11. **Useful without a paid frontier key.** The companion persona (§5) will not
    set up frontier billing, so Addison must do real work on legitimate free/local
    models — and let a user add new OpenAI-compatible endpoints by simply asking
    (§7.3, §7.5). Legit sources only, in-app; gray-area routers are documented on
    GitHub, never surfaced or endorsed inside the product.

Goal 9 (audience-adaptive via Profiles) is sharpened by the same amendment: the
opt-in surface is now a genuine **coding-agent harness** for the Developer
profile, the default a calm **companion** for Simple, with a third opt-in
**Custom** profile for tuning the prompting guards (§7.11). The shared safety
model is no longer merely "unchanged" — it is *mode-scoped* and gains the four
floors of §9, none of which any profile can switch off.

## 4. Non-Goals (v1)

- Enterprise governance/audit trails — different buyer, different doc.
- Multi-agent orchestration (ClawTeam-style) — out of scope until the single-agent product is solid.
- Headless/server *deployment* as a product — the desktop app is the primary surface. (A headless/CLI entry point into the Agent Core is exposed under the Developer profile, §7.11 — but Addison is not a server.)
- Fully arbitrary shell access as a default tool in **any** profile — see §9 (Security Model) and §7.11. Higher-risk tools stay opt-in, gated, and undoable; a profile never turns the safety model off.

Note: multi-channel messaging (WhatsApp, Telegram, SMS) was originally a hard non-goal but is now planned for **Phase 4** (§7.10, §11) once the local desktop trust model is proven. It remains out of scope for v1.

**Amended 2026-07-20 — two former non-goals reconciled (scheduling, auto-routing).**
The butler amendment moves both off the non-goals list without weakening the
principles that put them there:

- **Scheduling / always-on autonomy — reconciled, not repealed.** The floor **G2**
  ("Addison never triggers itself") stands, byte-for-byte. What changes is that
  Addison may now *author* OS-level automation — write a `launchd`/`cron` entry or
  a small watcher script — exactly as Claude Code can scaffold a cron job; the
  **OS** runs it on its schedule, Addison itself still fires nothing autonomously.
  Arming or running such a powerful action requires a **user-typed keyword prefix**
  (exact syntax TBD, §14) — which, because observed content can never type a
  keystroke into your composer, doubles as a structural prompt-injection barrier
  (§9). This is what makes the motivating monitor (background poll + notify)
  buildable while keeping G2 intact.
- **Automatic model routing — now in scope, as bounded strategies.** Previously
  deferred to v2 (§11), auto-routing ships as **four named strategies**
  (quality-first — the default and *strong-first, degrade-down*; cost-first;
  local-only; balanced) plus a Developer-only Custom builder. It is capped: the
  companion sees only a "prefer quality / prefer free" toggle, a free model's
  answer is labelled as such, and fallback is graceful over a handful of
  connected providers, never OmniRoute-style farming (§7.3, §11).

Still hard non-goals in v1, unchanged: enterprise governance, multi-agent
orchestration, headless server-as-a-product, and arbitrary shell as a *default*
tool in any profile. Addison also does **not** become an MCP server/gateway — only
an MCP *client* (§7.4).

---

## 5. Target User & Personas

**Primary persona — "Mira," 54, runs a small accounting practice.**
Comfortable with Word, Excel, email. Has never used a terminal. Wants help drafting client emails, summarizing PDFs, and looking things up online. Will abandon anything that shows an error code.

**Secondary persona — "Petr," 68, retired, tech-curious.**
Uses a tablet more than a laptop. Wants a research/writing assistant and occasional web lookups. Needs large, clear UI and forgiving interactions (undo, confirm-before-act).

**Secondary audience — technical users & developers (now in scope, via Profiles — §7.11).**
Wants the same local-first, undo-safe agent but with BYOK up front, Routines editable as plans, headless/CLI access, and raw diagnostics — without the onboarding padding aimed at Mira. Previously an explicit non-target; brought into scope *not* by complicating the default experience but by an opt-in Developer profile that keeps the two surfaces cleanly separated. The thing to avoid is still real: serving both audiences through one undifferentiated UI is how these products drift back into being complex. Profiles are precisely the mechanism for not doing that — the non-technical default stays simple *because* the developer surface lives behind its own profile rather than being bolted onto everyone's screen.

**Amended 2026-07-20 — the butler serves both at different surface heights.** Mira
and Petr are unchanged and remain the primary personas. The amendment sharpens
what each audience is *getting* from the same butler on the same unbrickable floor
(§9):

- **The companion (Simple → Mira, Petr).** An all-in-one assistant for someone who
  will not — and should not have to — stand up a coding harness. Calm,
  plain-language, non-destructive by construction. This is a capable companion on
  the same floor, not a toy.
- **The harness (Developer → the technical user).** A real coding-agent loop over
  a project directory (§7.11), peer to Claude Code / OpenClaw, with Addison's
  rollback and quality-of-life layered on: *the harness you can't brick.* This is
  the audience motivated by the opening story of the amendment — the person who
  wants to build the monitor without falling off the cliff when a request goes
  wrong.
- **The tuner (Custom).** Sitting between them, reached only deep in Settings
  behind extra confirmation, for the "I know what I'm doing" user who wants to
  lower *prompts* — never the floors (§7.11). Its own posture: *the one guarantee
  that never turns off.*

---

## 6. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Desktop Shell (Tauri)                │
│  ┌───────────────┐   ┌────────────────────────────────┐ │
│  │  Chat Window   │   │  Permission / Activity Panel    │ │
│  │  (React + TS)  │   │  (what the agent is doing now)  │ │
│  └───────┬────────┘   └───────────────┬──────────────────┘│
│          │ IPC (Tauri commands)       │                    │
│  ┌───────▼─────────────────────────────▼─────────────────┐│
│  │              Agent Core (Rust or Python sidecar)        ││
│  │  - Orchestration loop (plan → tool call → observe)      ││
│  │  - Provider abstraction (Anthropic API primary)         ││
│  │  - Tool registry + permission gate                      ││
│  │  - Memory manager                                        ││
│  └───────┬─────────────────┬─────────────────┬─────────────┘│
│          │                 │                 │              │
│  ┌───────▼──────┐  ┌───────▼───────┐ ┌───────▼──────────┐  │
│  │ SQLite (local │  │ Sandboxed Tool │ │ Managed Key /     │  │
│  │ memory + logs)│  │ Runtime (WASM/ │ │ Proxy Service     │  │
│  │               │  │ subprocess w/  │ │ (optional, cloud) │  │
│  │               │  │ scoped perms)  │ │                   │  │
│  └───────────────┘  └────────────────┘ └───────────────────┘│
└─────────────────────────────────────────────────────────┘
```

Everything left of the dotted trust boundary (chat window, agent core, local memory) runs on-device. The only network calls are: (a) model API calls, (b) explicit tool calls the user has approved (e.g., web search), (c) optional sync/telemetry, opt-in only.

---

## 7. Component Design

**Amended 2026-07-20 — new components from the butler amendment.** Five additions
land across the component design; each is detailed where it belongs, but collected
here for orientation. All ship in **Phase 2** (post-greenlight, §11); details the
amendment leaves open are marked **Phase-2** at §14.

- **Snapshot / restore subsystem (the safety floor).** A point-in-time copy of
  Addison's *mutable state* (settings, provider config, routing choice, skills,
  widgets, routines) — taken automatically before any risky/sweeping change *and*
  on command — that powers the one-action rollback of goal 10 / floor G3. It
  explicitly excludes API keys (G1 holds) and, for ordinary snapshots, the app
  binary. Full mechanics live in §9.
- **Widgets, buildable in every mode, capability-gated.** Definitive owner
  decision: a widget can be *built* in any mode; the mode gates only what a widget
  may *do*. SAFE/companion widgets come from a **safe, non-destructive vocabulary**
  — the existing routine/stat/command launchers **plus interactive display kinds**
  (to-do / checklist, note, counter, timer) rendered by trusted Addison components
  over Addison's own safe storage: no shell, no arbitrary code/eval, so the
  no-code invariant and the webview CSP still hold. Higher tiers (Developer /
  Custom) add **code-backed / system-capable** widgets — monitors, scripts, tools
  that touch the machine (the story's connection monitor) — governed by
  workspace-trust (§7.11), per-tool `undo()` (§7.9), the snapshot floor (§9), and
  the keyword gate to run or arm one. Restated invariant: a widget never exceeds
  its mode's capability tier, and a SAFE-tier widget is non-destructive by
  construction.
- **Free / no-frontier-required models + add-by-prompt endpoints.** Only
  legitimate free and local models are surfaced in-app; the OpenAI-compatible
  "custom server" provider (§7.3.1) is the extension hook, and a user can add a new
  endpoint simply by asking Addison ("add this endpoint"), registered as
  reversible, snapshotted provider-config data (keys per G1). Gray-area aggregating
  routers are the user's own choice, documented on the project's GitHub only —
  never named, surfaced, or endorsed inside the app.
- **MCP as a *client* (not a server).** Addison consumes external MCP
  servers/tools through its **existing tool registry and permission gate** — never
  a side channel: gated, logged, undo-aware. In the harness they run under
  workspace-trust; in the companion they are constrained to read-only or genuinely
  undo-able tools (the undo-at-registration rule keeps a mutating, no-undo MCP tool
  out of the SAFE view automatically). Connecting a server is reversible config,
  shared with add-an-endpoint plumbing. Addison is deliberately **not** an MCP
  server or gateway (§4).
- **The "make it cheaper" flow (the exact request that bricked the story's user).**
  When the user asks to spend less, Addison *orchestrates and previews* two
  reversible changes — proposing a cost-guidance skill and switching the default
  model / routing toward cost-first — takes an auto-snapshot before applying, and
  leaves a one-click Restore to the last verified-working state. The bricking
  scenario becomes structurally impossible: previewed, reversible, floored by G3.

### 7.1 Desktop Shell

**Choice: Tauri over Electron.** Given you're already building Almanac as a local-first modular app, Tauri fits the same mold: ~10x smaller binary than Electron, a Rust backend you can trust with filesystem/permission logic, and a webview frontend so you reuse React skills from your Team Lead/frontend role. Electron remains the fallback if a required native integration (e.g., a specific OS-level automation library) only ships JS bindings.

Frontend: React + TypeScript, Tailwind for styling. Single window, three regions:
- **Message thread** (chat itself)
- **Activity strip** (collapsible — shows live tool calls: "Searching the web…", "Reading `invoice_march.pdf`…")
- **Settings drawer** (permissions, model, memory controls) — never required to reach on first run

**Amended 2026-07 — dark restyle.** At the owner's decision, Addison's visual
direction has been restyled to a **dark, terminal-adjacent theme** (minimal chrome,
system-monospace accents, one restrained steel-blue accent lifted for dark). This
supersedes the light "cool-slate" palette described below; **`shell/tailwind.config.js`
tokens are authoritative** for the actual colours. The layout/IA (single window, three
regions), the accessibility rules (comfortable size, high contrast, generous spacing
for personas 54 and 68), the sharp-corners rule, and the "never a generic-AI or
vendor look" constraints in this section are **unchanged** — only the surface palette
moved from light to dark.

**Amended 2026-07 (v3) — the "Fern" redesign.** The visual direction has moved
again, from the dark terminal look to **"Fern"**: warm paper neutrals + one
fern-green accent, a serif "correspondence" message body (Source Serif 4) beside a
Public Sans UI, a blocky-vs-rounded shape rule, and a light-default **class-driven
dark mode**. The in-repo brief at **`docs/design-brief-fern/`** (README + `.dc.html`
prototypes) is now authoritative for tokens, type, shape, and copy; it supersedes
both the dark-restyle note above and the cool-slate palette below. As before, only
the surface/type/shape move — the layout/IA and accessibility rules of this section
are unchanged. (Note the shape rule itself changed: rounded cards/pills are now part
of the system — "rounded = ownable/actionable" — so the earlier sharp-corners rule no
longer applies.)

**Visual design direction (binding for the step-7 build).** Addison must not look like
a generic AI chat product. Explicitly avoid the default aesthetic AI-generated frontends
converge on: purple/indigo gradients, glassmorphism, sparkle/bot iconography, shimmer
"thinking" effects, dark hero panels, and the centered-bubbles "assistant app" template.
Aim instead for a calm, distinct, almost non-AI look — closer to a well-made everyday
desktop utility (a good mail or notes app) than to a chatbot. Addison must also never
resemble a model vendor's product identity (e.g. warm cream + terracotta reads as
Anthropic; deep purple as generic AI). Simple is correct: a quiet cool-slate neutral
palette with one deep steel-blue accent, sharp corners (no rounded cards/buttons), no
decorative taglines or filler text, real typographic hierarchy tuned for readability
(the primary personas are 54 and 68 — comfortable base size, high contrast, generous
spacing), obvious navigation, no decorative motion. Personality comes from plain, warm
language (§7.4, §9's no-jargon rule) — not from visual AI tropes. This direction
is profile-independent: Developer mode adds surfaces, never a different skin.

### 7.2 Agent Core

The orchestration loop is the "easy" part referenced in the earlier conversation:

```
loop:
  send conversation + system prompt + tool schema to model
  if model returns tool_use:
      check permission gate
      if not yet granted: pause, surface consent UI, wait
      execute tool in sandbox
      append tool_result to conversation
  else:
      stream text to UI, loop ends (turn complete)
```

Language choice: a Rust core (matches Tauri, gives you a single compiled binary, avoids bundling a Python runtime for end users) that shells out to a minimal set of sandboxed helper processes for specific tools. Given your stronger day-to-day fluency in Python, an alternative is a Python sidecar bundled via PyInstaller — simpler for you to iterate on, at the cost of a larger installer and slower startup. **Recommendation: prototype the agent loop in Python first (fast iteration), port the hot path to Rust once the tool/permission model stabilizes.** This mirrors how you sequenced PyQuest/Sentiment Signal — get correctness first, optimize the shipped artifact second.

### 7.3 Provider Abstraction

Originally scoped as a thin interface kept mostly for future flexibility; now a core piece of the product surface, since model choice — including fully local, offline models — is a stated goal (§3, goal 8).

```python
class ModelProvider(Protocol):
    def send(self, messages: list[Message], tools: list[ToolSchema]) -> ModelResponse: ...
    def capabilities(self) -> ProviderCapabilities: ...  # does it support native tool-calling? vision? what's its context window?
```

Every provider implementation reports a `ProviderCapabilities` struct up front. The agent core reads this before deciding how to run the loop — this is the mechanism that lets one orchestration loop (§7.2) work across wildly different backends without special-casing each one inline.

#### 7.3.1 Cloud providers

v1 targets the two or three providers with mature, reliable native tool-calling: Anthropic, OpenAI, and (pending evaluation) Google. Each gets a thin adapter translating Addison's internal tool schema into that provider's function-calling format and translating responses back. This is mechanical work, not a research problem — the format differences (Anthropic's `tool_use` blocks vs. OpenAI's `function_call`) are well documented and stable.

#### 7.3.2 Local providers

Local models run through **Ollama** as the primary integration target — it already handles model download/management, quantization, and exposes an OpenAI-compatible API, which means the OpenAI adapter from §7.3.1 is reusable with a different base URL rather than needing a bespoke integration. A direct `llama.cpp` binding is a possible fallback if Ollama's overhead or licensing becomes a problem, but Ollama is the pragmatic default — building a model runtime from scratch is out of scope for this product.

Local support introduces problems that don't exist for cloud models, and the design has to handle each explicitly rather than assuming "local just works the same, but private":

- **Hardware gating.** On first selecting "Use a local model," Addison checks available RAM/VRAM and disk space, and only offers models it has reasonable confidence will run acceptably — no letting a user on an 8GB laptop pick a 70B model and silently grind to a halt. Plain-language sizing: "This model needs about 16GB of memory. Your computer has 8GB, so it's not available" rather than exposing parameter counts or quantization levels.
- **Tool-calling reliability varies sharply.** Most small/local models (under ~13B parameters) either lack native function-calling or are unreliable at it compared to Claude/GPT-class models. Two mitigations: (a) prefer models Ollama tags as tool-capable, and (b) implement a prompt-based fallback tool-call parser (structured output coaxed via prompt + regex/JSON extraction) for models without native support, clearly labeled in the UI as "Basic tool support" so the user's expectations are calibrated rather than the agent just quietly failing more often.
- **No implicit cost/quality parity.** The settings screen should show, in plain terms, the real tradeoff per model: cloud models are faster and more capable but send data off-device and (outside the free tier) cost money per use; local models are private and free after download but slower, need capable hardware, and are less reliable at multi-step tool use. This is presented as a genuine tradeoff table, not a marketing pitch for either side.
- **Offline mode is real but partial.** A fully local model plus local-only tools (read a dropped-in file, do math, summarize text already in context) works with no internet at all. Any tool that inherently needs the network (web search, sending an email) still needs connectivity regardless of which model is answering — this distinction should be surfaced plainly rather than implying "local = fully offline" unconditionally.
- **Model lifecycle is now a supported feature, not a hidden implementation detail.** Downloading, updating, and deleting local models needs its own small UI (progress bar, storage used, "remove this model" action) — this is meaningfully more surface area than the cloud path, which has none of this by definition.

#### 7.3.3 Model selection UX

One settings screen, three tiers, no config file:

1. **"Just work" (default)** — the managed cloud tier from §7.5, option 3. No model name shown at all; abstracted away entirely for the primary personas.
2. **"Use my own key"** — pick a cloud provider from a short list, paste a key, done. Capability differences between providers are hidden behind sensible defaults (Addison picks a good default model per provider) unless the user opens "Advanced."
3. **"Run locally"** — the flow in §7.3.2, gated by the hardware check, clearly framed as the private/offline/technical option rather than the default recommendation.

#### 7.3.4 Multiple local models, capability gating, and task routing

The "Run locally" tier is not limited to a single model. A user can download and keep several local models at once — a common real-world power-user setup is a larger vision-capable model alongside a smaller, faster text-only one — and pick between them per message from a dropdown under "Local." A Routine (engineering spec §6) can also pin a step to a specific local model by name. This is the direct answer to how people actually run local stacks today (e.g. Ollama with a 14B vision model and an 8B text model side by side).

**Capability gating (v1).** Models differ in what they can take as input — some analyze images, most small local ones can't. Each provider reports its capabilities up front (native tool-calling, context window, and now vision/audio), and Addison uses that to avoid a silent failure: drop an image while a text-only model is active and Addison tells you plainly and offers to switch to a model that can see it, rather than handing the image to a model that will just make something up. Same "name the limit, don't fail silently" principle as the Setup Assistant (§7.5.1).

**Automatic task routing (planned for v2, deliberately deferred).** v1 keeps every model choice explicit and user-made — no hidden decision about where a message goes (§9). v2 will add an *optional* layer on top that routes each turn automatically by task: a quick lookup to a small/cheap/local model, a hard or long-context task to a stronger one, an image or audio input to a capable model. This is planned, not accidental — the vision/audio capability flags and the multiple-local-model support in v1 exist partly to be the foundation it is built on. The guardrails carry over unchanged: auto-routing stays **off by default**, is always overridable by the manual picker, and always shows which model it picked and why. It belongs in v2 rather than v1 precisely because "Addison quietly chose a different model" is exactly the kind of unaccountable decision v1 refuses to make until the trust model is proven.

#### 7.3.5 Model cascade (draft → refine) — an optional module, not core

A frequently-requested pattern: a cheap, fast model drafts an output (code, a document) and a stronger model verifies, fixes, and polishes it. Addison supports this **as an optional module built on the Routine engine (engineering spec §6) — not as behaviour wired into the orchestrator or the model router.** A cascade is just a two-step declarative Routine: step 1 pinned to a cheap model, step 2 pinned to a strong one, each passing through the same tool registry and permission gate as everything else. There is no new core capability, no new tool, and no code field — it inherits the entire security model by construction.

Keeping it a module rather than a built-in is deliberate:
- The core stays minimal. The orchestrator resolves one model per turn (§7.3.3) and has no knowledge that a cascade exists.
- It's opt-in and lives in the Developer profile (§7.11); nothing about it intrudes on the Simple default.
- Users can inspect, edit, delete, or fork it like any other Routine.

**Be honest about the economics — it is not a guaranteed saving.** A cascade only saves money when the strong refiner *edits* the draft (emits a small patch) rather than *rewriting* it: the draft cost, plus the extra input tokens the strong model spends reading the draft, must be outweighed by the reduction in the refiner's (expensive) output tokens. When drafts are poor and get rewritten from scratch, the cascade costs *more* than just using the strong model directly. And an LLM verifying an LLM is not reliably cheaper than generating fresh — deep verification burns reasoning tokens. The genuinely cheap, reliable verifier for code is a compiler/linter/test-runner, which v1 deliberately doesn't have (no code execution, §9) — so v1's cascade is a quality play (a second, stronger pass) at least as much as a cost play. Surface this tradeoff plainly wherever the module is offered; never sell it as free savings.

**Sequencing:** the v1 substrate is per-step pinning to a *specific named model* (so a step can target "the cheap model" or "the strong model" explicitly). The cascade module itself — a shipped draft→refine Routine template — is v2, alongside automatic routing (§7.3.4). Both are optional layers over the same engine.

### 7.4 Tool / Skill System

Every tool declares, up front, in plain language, what it can touch:

```json
{
  "id": "read_documents",
  "label": "Read files you choose",
  "description": "Addison can open files you explicitly select or drag in. It cannot browse your folders on its own.",
  "scope": "explicit-file-picker",
  "risk_tier": "low"
}
```

Risk tiers drive the consent UI:
- **Low** (web search, read a dropped-in file) — approve once, remembered
- **Medium** (write/save a file, send an email draft) — confirm each distinct action, plain-language preview before it happens
- **High** (anything resembling shell/system control) — **not included in v1's default toolset at all.** If added later, it requires an explicit, scary-looking opt-in ("Advanced Mode") gated behind a warning, not a default capability.

This is the direct opposite of OpenClaw's model, which defaults toward broad system access for power users. That's the correct tradeoff for developers; it's a liability for Mira.

#### 7.4.1 V1 Tool Set

The general system above is the framework; this is the concrete list for launch. Kept deliberately narrow — every tool here earns its place either by being genuinely core to "help me with everyday stuff" or by being safely undo-able per §7.9. Anything not on this list is a Phase 2+ decision, not an oversight.

| Tool | Risk tier | Undo path | Why it's in v1 |
|---|---|---|---|
| **Web search** | Low | N/A (read-only) | Core capability; the single most common reason a non-technical user opens the app |
| **Read a dropped-in file** (PDF, docx, image, csv, txt) | Low | N/A (read-only) | Covers "summarize this," "what does this say" — the second most common ask |
| **Read clipboard content** (only when user explicitly pastes) | Low | N/A (read-only) | Lets a user paste an email/message in without saving a file first |
| **Calculator / unit conversion** | Low | N/A (no external effect) | Common, low-stakes, builds trust early with zero risk |
| **Save output as a new file** (e.g., "save this as a Word doc on my Desktop") | Medium | Delete the newly created file | High-value, and the undo path is trivial since it only ever creates — never overwrites — a file |
| **Draft an email or message** (composes only, opens in the user's own mail/messaging app for them to review and send) | Medium | Discard the draft | Delivers most of the value of "send my email" without ever taking the irreversible step itself — Addison never presses send |
| **Open a link in the browser** | Low | N/A (no state change in Addison) | Natural follow-on to web search results |

> **Note on images:** reading an image in is low-risk and always available, but *analyzing* its contents needs a vision-capable model. If the active model can't see images, Addison says so and offers to switch (§7.3.4) rather than guessing.

Explicitly **not** in v1, even though they're common asks: sending email/messages directly, deleting or overwriting existing files, editing files in place, calendar writes, any form of system/shell command. Each of these either lacks a clean undo path today or crosses into "high risk" per §7.4 — they're Phase 3+ candidates once the rewind system (§7.9) and the trust track record justify widening scope, not launch-blocking gaps.

### 7.5 Credentials & Cost Model

Four options, not mutually exclusive — pick based on how you want to fund inference cost, now mapped directly onto the three-tier model selector in §7.3.3. Hard constraint carried through this section: **the zero-key path must not cost you anything outright**, and it exists solely to get someone through the first conversation — it is not intended as a way to do ongoing work. Sustained use is expected to run on the user's own subscription/key, per goal 2 in §3.

1. **Bring your own key (BYOK).** Settings → "Advanced" → paste a cloud provider's API key. Zero cost to you. This is the intended steady state for anyone actually doing real work with Addison, not a fallback.
2. **Guided key creation.** A wizard that opens the chosen provider's console in-browser, waits for the user to copy a key, and validates it with one test call — this is what the Setup Assistant (option 3) walks the user into directly, not a separate cold path. See §7.5.1.
3. **Setup Assistant (zero-key, zero-cost, purpose-bounded, not preview-bounded).** The free model's job in this mode is narrower and more useful than "give the user a taste of chat": it actively walks the user through configuring Addison — explaining each tool/permission conversationally as it's requested (§7.4), and explicitly naming what it *can't* do yet because it isn't set up ("I can search the web once you allow that," "I'm running on a basic free model right now — connect your own key and I'll be faster and much more capable"). The natural end of this mode isn't a message count running out, it's setup being complete: permissions granted, and either a key added or a deliberate choice to continue on the limited free path. This reframes the trial from "sample the product" to "a guide whose whole job is to get you set up," which is both a better first impression and a narrower, more predictable scope to engineer for (§7.5.1).
4. **Local model, no cloud cost.** Once §7.3.2 ships, a user with adequate hardware pays nothing and sends nothing off-device. Distinct from option 3: this is a permanent zero-cost path a user can choose to stay on, not an onboarding ramp — a different audience than the one option 3 is designed for.

**Recommendation for v1:** ship option 3 as the day-one default, with a generous-but-finite message cap on the setup conversation itself as a safety net (setup shouldn't need more than a dozen or so exchanges) rather than that cap being the actual UX — the real end condition is "you're configured," not "you've run out." Once configured, everything hands off to option 1/2 for ongoing use. This satisfies the "no API key on day one" goal without the product ever pretending the free model is meant for real ongoing work, and gives the free model a job it's actually well-suited to (narrow, guided, explanatory) rather than open-ended assistance it may handle unreliably.

#### 7.5.1 Setup Assistant — Engineering Spec

This is the engineering detail for option 3, with the same non-negotiable isolation requirement as before — **nothing here should ever touch your personal Anthropic account or a user's BYOK key** — plus the framing that now shapes every decision below: **the free model's job is to configure Addison, not to be Addison.** That's a narrower, more predictable task than open-ended assistance, and it changes what "done" means.

**The core idea**

A handful of providers offer genuinely free, no-credit-card tiers for specific models — usually smaller open-weight models, rate-limited. Addison's Setup Assistant relays requests to one or more of these, rotating across providers for resilience. The relay itself is a small stateless service on free-tier serverless hosting — not a model, not a GPU, nothing that needs monitoring for uptime. Because the task is scoped to "help this person get configured," not general-purpose help, both the total volume and the reasoning difficulty are smaller than the earlier "generic free preview" framing implied — a real gain, not just a rebrand.

**What the Setup Assistant actually does**

Its system prompt constrains it to a specific job, distinct from Addison's normal operating mode:

1. **Greets and orients.** Briefly explains what Addison can do, in plain language, without a wall of text.
2. **Walks through permissions conversationally, as they come up.** Rather than a settings checklist, tools get introduced in context — "Want me to be able to search the web for things like this? I'll ask before every search unless you'd rather I not ask each time" — reusing the same consent-card mechanism from §7.4, just narrated rather than presented cold.
3. **Names its own limits explicitly, proactively.** This is the core of what was asked for: rather than silently failing or being vaguely worse, it says outright what it can't do yet and why — "I'm running on a small free model right now, so I'm not great at multi-step tasks yet," "I can't remember things between conversations until you're on your own key," "For things like drafting a detailed email, you'll get much better results once you're connected to your own account." Each of these callouts links directly to the relevant action (add a key, enable a tool) rather than just being a disclaimer.
4. **Offers the guided key-creation wizard (option 2) inline**, at the natural moment — not gated behind running out of messages, but offered as soon as the assistant identifies something it can't do well on the free model. The user can accept immediately, defer, or decline and keep going on the free path.
5. **Hands off once configured.** The moment a key is added (or the user explicitly chooses to continue without one), the conversation continues uninterrupted — same window, same history — just now running on the newly configured provider (§7.3) instead of the Setup Assistant's free source. This is a `ModelProvider` swap under the hood (§7.3), invisible as a mechanism to the user beyond "responses are noticeably better now."

**Trust zone separation (unchanged)**

| Zone | Holds | Never touches |
|---|---|---|
| Your personal account | Your own day-to-day API usage | The Addison backend, in any form |
| Setup Assistant relay | Dedicated (not personal) free-tier API keys for one or more providers' no-cost model offerings, held in the serverless platform's own secret store | Any paid account of yours; the client app, in any form |
| User's BYOK key | The user's own account, entered only in "Use my own key" mode | Addison's backend, ever — see isolation guarantee below |

**Bounding it: purpose completion, with a safety-net cap**

The real end condition is "the user is configured," not a message count — but a hard ceiling still exists as a backstop, since a conversational agent can't be trusted to always self-terminate correctly:

- A generous cap (e.g., 20-30 messages — genuine setup conversations, including someone asking follow-up questions, shouldn't need more) closes the loop if a setup conversation somehow goes long without resolving. This is a much simpler number to reason about than the earlier "time or message count, whichever first" trial design, because the task itself is naturally short.
- If the cap is reached without the user completing setup, the assistant wraps up plainly: "We're at the end of the free setup conversation — want to add your own key now, or keep going with fewer capabilities?" rather than a hard cutoff mid-sentence.
- No daily reset — this is a one-time, per-install flow. Once setup is complete (or explicitly declined), the Setup Assistant's job is done; it doesn't resurface as a recurring free-chat allowance.

**Which free sources — pick more than one**

| Source type | Cost | Typical constraint | Notes |
|---|---|---|---|
| A provider's own free-tier API for a smaller model | $0, no card required for some providers | Requests-per-minute limits, sometimes daily caps | Fine even as a single source given the small, bounded task — but still worth a second for resilience |
| Free-tagged models on a multi-model routing service | $0 | Explicitly deprioritized/slower under load | Convenient abstraction over several underlying free offers |

A useful side effect of the narrower task: a small free model is genuinely well-suited to "explain tools, ask permission, point the user at setup steps" — this is closer to scripted conversation with light reasoning than to the open-ended tool-using work Addison does once fully configured, so the tool-calling reliability concerns from §7.3.2 matter much less here. The Setup Assistant mostly doesn't need real tool-calling at all — its "actions" are mostly conversational (asking for permission, opening the key wizard), not agentic tool execution.

**Anonymous device identity (no signup, no email)** — unchanged:

1. On first launch, the app generates a device keypair locally and stores the private key in the OS keychain — never in a plain file, never in SQLite.
2. The public half registers with the relay on first use, receiving a signed device token — identifies a device, not a person.
3. Every request is signed with the device's private key; the relay verifies server-side before it's forwarded.

**Request flow**

```
Desktop client              Addison relay                  Free-tier provider(s)
     │                       (serverless, free tier)         (rotated, with failover)
     │  signed request +          │                                  │
     │  device token ─────────────▶                                  │
     │                            │  1. verify signature              │
     │                            │  2. check setup-session cap       │
     │                            │     (one-time, generous ceiling)  │
     │                            │  3. if at cap → prompt to wrap    │
     │                            │     up / offer key wizard         │
     │                            │  4. if OK → forward to Provider A ─▶
     │                            │                              ◀────  or rate-limit error
     │                            │  5. on failure, retry  ───────────▶ Provider B
     │                            │     automatically                  (failover)
     │                            │                              ◀────  response
     │  ◀───────────────────────── │  6. stream response back          │
```

**Setup → subscription handoff**

This is the part that directly answers "after setup, the user is switched to their subscription":

- The handoff is triggered by the *user completing key entry* (via the inline wizard), not by running out of a quota — configuration completion is the event, not exhaustion.
- The conversation window, history, and any permissions already granted carry over unchanged — adding a key mid-conversation doesn't reset anything the Setup Assistant already helped configure.
- From that point forward, every request routes through the user's own key via `DirectAPIProvider` (§7.3.3) — the Setup Assistant's free relay is never touched again for that install unless the user later removes their key.
- If the user declines a key and keeps going, the app is honest about what that means going forward (limited capability, the "Basic tool support" labeling from §7.3.2) rather than silently downgrading without explanation.

**Abuse control (small surface, purpose-scoped)**

- **Server-side, one-time setup-session cap**, enforced by the relay, never trusted from the client.
- **Reinstall abuse is possible but low-stakes**, same reasoning as before — without accounts there's no fully robust prevention, but the value being farmed (a short setup conversation) isn't worth much effort to steal, so this isn't worth over-engineering.
- **Global throughput cap** on the relay matching the free providers' actual limits, so a burst of new installs can't get the whole pool rate-limited or banned.

**Isolation guarantee from BYOK** — unchanged: the `ModelProvider` abstraction (§7.3) keeps separate implementations (`SetupAssistantProvider` in place of the earlier `OnboardingTrialProvider`, and `DirectAPIProvider` for BYOK) so a user's own key is structurally never sent to Addison's relay, and there's no setup-assistant-side key for it to be confused with either.

**Worth flagging plainly:** the dependency-on-a-third-party's-free-offer risk still exists, but its blast radius stays small — a provider throttling or pulling their free tier degrades the setup conversation for new installs, not an ongoing service anyone relies on for real work. Multi-source redundancy is still worth it, mainly because a broken first five minutes is the worst possible moment for this to fail, not because the ongoing stakes are high.

### 7.6 Memory

Two tiers, mirroring the pattern you already used designing Almanac's modules:

- **Session memory** — full transcript of the current conversation, always available, cleared or kept per user preference.
- **Long-term memory** — a small SQLite table of user-confirmed facts ("remembers you prefer short summaries," "knows your business is called X"). Written only when the agent proposes a memory and the user accepts a one-tap prompt — never silently. This sidesteps the trust problem stealth-memory systems create, and gives you a natural settings screen: "Here's everything Addison remembers about you," each item deletable individually.

Schema sketch:

```sql
CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  started_at INTEGER,
  title TEXT
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations(id),
  role TEXT CHECK(role IN ('user','assistant','tool')),
  content TEXT,
  created_at INTEGER
);

CREATE TABLE memory_facts (
  id TEXT PRIMARY KEY,
  fact TEXT,
  source_conversation_id TEXT,
  confirmed_by_user INTEGER DEFAULT 0,
  created_at INTEGER
);

CREATE TABLE tool_grants (
  tool_id TEXT PRIMARY KEY,
  granted_at INTEGER,
  scope_details TEXT
);
```

**Long conversations (v2 — engineering-spec §4.8).** A third concern sits between the two tiers: what happens when one conversation grows past what a model can affordably re-read every turn. The v2 answer is a quiet continuation — Addison condenses the older part of the chat into a summary, carries the confirmed memory facts and the recent turns forward, and says so in one plain sentence in the thread ("I've condensed the earlier part of our chat — nothing was deleted"). It is never silent (no hidden decisions, §9), never writes long-term memory without confirmation, and never deletes the original transcript — the full history stays on disk and remains searchable. v1 deliberately ships only the storage substrate for this; the automatic behaviour is a v2 feature so that its boundary marker can be designed into the real chat UI rather than bolted on.

### 7.7 Updates

Tauri's built-in updater (signed release manifests) — silent background download, prompt to restart. No manual "check GitHub for a new release" step, which is where most self-hosted tools lose non-technical users after week one.

### 7.8 Setup — Deep Dive

"Easier to set up" is a first-class requirement, not a side effect of good packaging, so it gets its own spec rather than living implicitly inside §8.

- **One link, not a matrix.** The download page detects OS + architecture from the user-agent and shows a single button ("Download for your Mac"), not a grid of six installer choices. A small "other platforms" link is available but never the default path.
- **No dependency bundling gaps.** Statically linked Rust binary — no "install Python first," no "requires Visual C++ Redistributable," no separate runtime the user has to know exists. This is a hard requirement on the Tauri/Rust choice in §7.2, not just a nice-to-have.
- **Minimize privilege prompts.** Install to the user's local app-data directory, not a system directory — avoids the admin/sudo password prompt entirely on all three platforms. If a future tool genuinely needs elevated access, that's requested at the moment it's needed, explained in one sentence, never during install.
- **No restart required.** Install → app opens automatically → first message is possible immediately. No "please restart your computer to finish installing."
- **Silent first-run initialization.** SQLite DB creation, local key storage setup (OS keychain), and default permission state are created invisibly on first launch, shown only as a brief "Getting ready…" state under two seconds, never as a wizard with multiple screens to click through.
- **Works if the user does nothing else.** Every step beyond "download, open" is optional. The zero-key managed tier (§7.5, option 3) is what makes this true end-to-end — if setup is seamless but step 2 is "now go get an API key," the seam just moved.
- **Uninstall is equally simple.** Single entry in the OS's standard uninstall list; removes the app and offers (doesn't force) to delete local data. Non-technical users judge trustworthiness partly by how easy an exit is, not just entry.

### 7.9 Rewind & Self-Repair

Non-technical users can't debug a stuck or broken agent, so Addison needs a built-in "undo everything back to a good state" command that doesn't require understanding what went wrong.

**Two distinct mechanisms, one user-facing button:**

1. **Conversational rewind** — reverts the last N turns of the chat itself (message history), same idea as editing/regenerating a message, but framed as a single visible action: a "Rewind" button attached to any past message that resets the conversation to that point. Removes the model's confusion after a bad turn without the user needing to explain what went wrong.

2. **Action rewind (the "unfuck it" button)** — every medium/high-risk tool action (§7.4) that touches the filesystem or external state creates an automatic, versioned snapshot *before* it executes — a lightweight local backup, not a system restore point. A single command reverses the most recent N actions, restoring any files or settings the agent touched, independent of the conversation state. This is the actual safety net: even if the user can't articulate what broke, "put it back the way it was" always works because every mutating action is inherently reversible by construction, not by best-effort cleanup logic bolted on afterward.

Design implications:
- Every tool that mutates state (`write_file`, `delete_file`, `save_setting`) must implement a paired `undo()` at registration time — this is a constraint on the tool interface itself (§7.4), not an optional add-on. A tool without a defined undo path is automatically capped at "low risk / read-only" until one exists.
- Snapshots are stored locally (diffs or full copies, whichever is cheaper per file type) with a rolling retention window (e.g., last 20 actions or 7 days, configurable) so this doesn't grow unbounded on disk.
- The rewind/self-repair control lives in the Activity Panel (§7.1) at all times, not buried in Settings — it's the panic button, and panic buttons need to be where the panic is.
- A harder "Reset Addison" exists one level down in Settings for the rare case of genuinely corrupted local state (e.g., a malformed SQLite file) — this clears app state but explicitly does not touch the versioned file snapshots, so a full reset still leaves user files recoverable.
- This whole feature is a natural fit for local-first, versioned storage — closer in spirit to how you're already thinking about Almanac's data model than a bolt-on, so the underlying snapshot mechanism may be worth designing once and sharing between the two projects.

#### 7.9.1 V1 Commands

Same principle as the tool list in §7.4.1: a short, concrete set for launch rather than an open-ended command palette. All are UI actions (buttons, not typed slash-commands) — a non-technical user shouldn't need to know a command exists, let alone its name, to find it.

| Command | What it does | Where it lives |
|---|---|---|
| **Stop** | Interrupts the agent mid-turn — cancels an in-progress tool call immediately. This has to exist even before rewind: the first line of defense is being able to halt something before it finishes, not just clean up after. | Always visible while the agent is actively working (replaces the send button) |
| **Rewind** | Conversational rewind — resets the chat to an earlier point (§7.9, mechanism 1) | Attached to each past message on hover/tap |
| **Undo last action** | Action rewind — reverts the most recent file/state change the agent made (§7.9, mechanism 2), the "unfuck it" button | Activity Panel, always visible while any undoable actions exist |
| **Retry** | Regenerates the last response without touching anything before it — for when the answer was just mediocre, not wrong in a way that needs undoing | Attached to the agent's last message |
| **What do you remember about me?** | Surfaces the full contents of long-term memory (§7.6) in plain language, each item individually deletable | Settings, and answerable as a normal chat question too |
| **Forget this conversation** | Clears the current session's message history; does not touch long-term memory or file snapshots | Chat menu |
| **Show what you just did** | Plain-language activity log of recent tool calls ("Searched the web for X," "Saved a file called Y") — the transparency counterpart to Undo, for when the user wants to understand before deciding whether to revert | Activity Panel |
| **Reset Addison** | The harder reset described in §7.9 — clears corrupted app state, leaves file snapshots and user files untouched | Settings, one level down, deliberately less prominent than Undo |

**Explicitly not typed/slash commands in v1.** A command syntax (`/rewind 3`) is a natural fit for a developer tool and actively wrong for this persona — it reintroduces the exact "you need to know special vocabulary" problem the whole product is designed to avoid. Every command here is a labeled button with a one-line plain-language description, discoverable by looking, not by knowing.

### 7.10 Messaging Channel Integration (Phase 4)

Deferred from v1 (§4) but scoped here since it changes several earlier decisions once it's built.

- **Trust model shift.** Anything arriving over WhatsApp/Telegram from an outside contact is untrusted input by default — same handling as web content in §9, but now the "attacker" can be anyone with the linked number, not just a malicious webpage.
- **Identity/authorization.** Needs an explicit pairing step (e.g., a one-time code shown in the desktop app, sent once to the messaging account being linked) so a stranger who somehow has the number can't just start issuing commands to the user's agent.
- **Always-on requirement.** Messaging only works if something is listening even when the desktop app is closed — this pushes the managed-proxy backend (§7.5, option 3) from "nice for zero-key onboarding" to "required infrastructure," since a purely local desktop process can't receive a WhatsApp message while asleep.
- **Platform choice matters.** Telegram's Bot API is free, well-documented, and has no business-verification friction — a natural first target. WhatsApp requires the Business API, a verified business, and per-message costs from Meta — realistically a later, costlier addition once Telegram proves the pattern works. SMS sits in between (cheap gateways exist, but per-message cost is real at any scale).
- **Reduced tool surface over messaging.** Even once channels exist, the higher-risk tool tiers (§7.4) likely stay desktop-only at first — a stray WhatsApp message shouldn't be able to trigger a file-deletion the way a deliberate desktop chat can, until the identity/authorization story above is fully hardened.

### 7.11 Profiles (audience-adaptive surface)

Addison serves two audiences from one product: the non-technical primary personas (Mira, Petr) and technical users/developers who want the same local-first, undo-safe agent without the guard rails slowing them down (§5, goal 9). The mechanism is a **Profile** — a single switch that reshapes the *surface and default capabilities*, layered over one shared engine and one unchanged safety model.

**The load-bearing principle:** a Profile changes presentation and defaults, never the security posture. Every invariant in §9 (per-action consent, capability allow-list, keys never in the frontend) and §7.9 (undo-at-registration, no privilege escalation) holds identically in every profile. A profile is *configuration*, not a second codebase and not a way to switch safety off. This is what keeps "also for developers" from quietly undoing the entire trust model the product is built on.

**Simple (default).** Everything else in this doc, unchanged: the Setup Assistant onboarding (§7.5.1), the narrow tool set (§7.4.1), plain-language permission cards, translated errors (goal 5), no jargon, no config or code surfaces. This is the *protected* default and must never be degraded by the existence of the other profiles — developer affordances are simply not rendered here. A non-technical user can live in this profile forever and never learn another exists.

**Developer (opt-in).** Same engine, more of it exposed:
- **Model/BYOK configuration up front**, shortening or skipping the Setup Assistant (§7.5.1).
- **Routines editable as their declarative plan**, not only authored conversationally. The declarative-not-code constraint (engineering spec §6.1) still holds, so exposing the plan is safe — there is no code field to expose.
- **Headless / CLI access** to the Agent Core's JSON-RPC surface, for scripting and CI. This moves headless use from a flat non-goal (§4) to a Developer-profile capability — still not the default, still the same permission-gated core, not a separate server product.
- **Raw diagnostics** — real error text, activity/token/context inspection — instead of the translated-for-Mira messages.
- **Higher-risk tool tiers** unlockable through the existing explicit "Advanced Mode" opt-in (§7.4). Arbitrary shell stays out of the default set in *every* profile; if ever added it is a clearly-labeled, individually-opted-in HIGH-tier tool routed through the same gate and undo, never something a profile flips on wholesale.

**Why this is a better experience, not just a bigger one.** The failure mode these products hit is serving developers and non-technical users through one undifferentiated surface — which ends up too complex for Mira and too padded for the developer. Profiles let each audience get a surface tuned to them so the non-technical default gets *simpler* (nothing developer-facing intrudes), and the developer gets the power without forking or rebuilding the app. The rescope succeeds only if Mira's experience is no worse for developers now being welcome.

**Engineering shape (see the engineering spec).** A `Profile` parameterizes (a) which tools the `ToolRegistry` registers and their default grant verbosity, (b) which onboarding path runs, (c) frontend feature flags (routine-plan editor, raw logs, CLI hints), and (d) the default model-config path. It resolves at startup from a stored setting and is switchable in Settings. It does **not** touch the `PermissionGate`. Default is Simple; the user is never forced to pick a profile to begin.

**Amended 2026-07-20 — the profile now derives a policy mode, and gains a third
option (Custom).** Two shifts update this section. First (already an owner decision,
2026-07-19, and now assumed by the butler amendment): the profile is the single
source of truth for a **policy mode** derived 1:1 from it — Simple → **SAFE**,
Developer → **OPEN** — so the gate *is* mode-aware. The earlier "a profile does not
touch the `PermissionGate`" wording is superseded: what a profile must never touch
is the **global floors** (G1 keys, G2 no self-trigger, G3 rollback, the
undeletable-anchor rule; §9), not the gate's *prompting*. Second, the butler
amendment sharpens the two surfaces and adds a third:

- **Developer → OPEN is a coding-agent harness.** Not a chat with `run_command`
  but a genuine agentic loop over a **real project directory**: read/edit/create
  files, run builds and tests, iterate. To keep the gate from prompting on every
  edit, OPEN gains a **workspace-trust** model: the user grants a project
  directory (an explicit, snapshotted act); *inside* it OPEN acts freely (the gate
  still runs and logs, it just doesn't prompt); *outside* it (system paths, the
  keychain, the network beyond configured providers) destructive actions still
  raise the per-invocation card. Every mutating tool keeps its `undo()`, and the
  workspace sits under the snapshot floor (§9) — fine-grained undo *and* whole-
  config restore.
- **Custom → a user-tuned surface.** A third profile, reachable only deep in
  Settings behind additional questioning. The user may loosen or tighten the
  *prompting* guards (the per-invocation destructive card, the auto-grant scope,
  the workspace-trust boundary, the keyword gate's strictness) — and **never** the
  floors, which are absent from the Custom panel entirely. The contract: turning
  any guard *off* mints an **undeletable anchor** (§9), so lowering your own
  protections always leaves a guaranteed way back. You can lower the prompts; you
  can never lower the floor. *(Open question §14: whether Custom is reachable from
  Simple directly or only via Developer; current lean is reachable-but-deep
  regardless.)*

The load-bearing principle above holds unchanged in spirit — a profile is
configuration, not a second codebase or a way to switch safety off — but is now
stated more precisely: profiles reshape *surface, capability tier, and prompting*;
they cannot reshape the floors.

---

## 8. Onboarding Flow (first 60 seconds)

1. Download page detects OS, offers the single right installer (no "choose your platform" grid with six options).
2. Installer runs, app opens directly into chat — no account screen blocking the first message.
3. The Setup Assistant (§7.5, §7.5.1) opens the conversation itself, driving the process rather than waiting to be asked: brief plain-language intro, then straight into walking the person through what Addison can do.
4. As each tool/permission comes up, it's requested conversationally in the moment it's relevant, using the same consent-card mechanism from §7.4 — "Want me to be able to search the web for things like this?" — not a wall of toggles up front.
5. The assistant proactively names what it can't do well yet and why, pointing at the specific fix — "I'm on a small free model right now, so multi-step tasks may be shaky. Want to connect your own key? Takes about a minute" — offered at the moment it's relevant, not gated behind running out of a quota.
6. If the user takes that offer, the guided key-creation wizard (§7.5, option 2) opens inline; once a key is added, the same conversation continues uninterrupted, now running on the user's own subscription (§7.5.1, "Setup → subscription handoff"). If they decline, the assistant says so plainly and keeps going within the free model's real limits.
7. Settings/advanced options exist but are never required to reach for the core loop to work.

This flow is the actual deliverable of the "easier setup" requirement — it's a UX spec as much as a technical one, and it should be storyboarded and user-tested with someone like Mira before writing the permission-gate code, not after.

---

## 9. Security & Sandboxing Model

Threat model is different from OpenClaw's: the user isn't defending a server from a remote attacker, they're trusting an agent running with their own OS permissions to not do something destructive by mistake (bad tool call, prompt injection from a malicious webpage/document, model error).

Mitigations:
- **Capability allow-list, not a shell.** Tools are individual typed functions (`read_file(path)`, `web_search(query)`), not "run arbitrary command." This eliminates most of the attack surface OpenClaw explicitly warns about (per the Register/Zylon coverage in the search results above — "endless supply of security flaws" tracks directly back to broad shell/computer-control access).
- **Filesystem scope by picker, not by path.** The agent never gets a raw path string to open; it gets a handle to whatever the OS-native file picker returned, so it structurally cannot wander outside what the user selected.
- **Destructive actions require re-confirmation with a preview.** "Delete `invoice_march.pdf`?" always shows the actual filename, never a batched/summarized action.
- **Prompt-injection awareness.** Content pulled from tool results (web pages, documents) is marked as untrusted data in the model context, and the system prompt instructs the model not to treat instructions found inside tool output as commands from the user — the same pattern used in Claude's own tool-result handling.
- **No auto-run on schedule in v1.** OpenClaw's "wakes up on a schedule" capability is exactly the kind of always-on autonomy that raises the stakes of a mistake; deliberately deferred until the permission and trust model above is proven.

**Amended 2026-07-20 — safety is redefined as guaranteed recovery (G3), and the
model gains four inviolable floors.** The mitigations above are all *prevention* —
ask before risky things, bound the blast radius. The butler amendment adds the
missing half: *recovery that always fires.* The failure mode to design against is
**unrecoverability**, and the anti-pattern never to repeat is a **rollback that
doesn't fire** (the motivating story: a single "make it cheap" request permanently
bricked a non-technical user's OpenClaw setup, and its rewind failed).

**The organizing principle — reversible data vs. inviolable machinery.** The
apparent contradiction ("the user shouldn't be able to alter Addison" vs. "the user
can add endpoints, tune guards, ask Addison to reconfigure itself") resolves
cleanly along one line:

- **Reversible data/config** — provider endpoints, model choices, routing strategy,
  cost settings, which prompting guards are on, skills, widgets, routines. The user
  *and* the model may change all of it, **because every such change is
  auto-snapshotted and one-action reversible.** This is data, not code.
- **Inviolable machinery** — Addison's own code, the orchestration / gate / registry
  machinery, and the four floors below. Never alterable by user or model, in any
  mode.

Risky-but-reversible changes (disabling a guard, adding a raw endpoint) are legal
but live deep in Settings behind additional questioning — *friction, not a wall.*
An honesty note: model-driven "additional questioning" is friction a determined
user clicks through and a prompt-injection could try to talk around, so it is
**not** the safety net. The snapshot is.

**G3 — guaranteed rollback (new global floor).** *Neither the user nor the model
can drive Addison into an unrecoverable configuration. At all times there exists a
one-action restore to a last-known-working state, and that restore path is itself
unbreakable.* Realised with **app-state snapshots**:

- **What a snapshot is** — a point-in-time copy of Addison's *mutable state*:
  settings (active profile, theme, routing choice, guard toggles), provider config
  (which endpoints, non-secret metadata, selected models, routing strategy), and
  the declarative artifacts (skills, widgets, routines).
- **What it excludes** — **API keys / the OS keychain** (owner decision), so a
  rollback can never move, expose, or clobber a key and **G1 stays intact**; the
  **app binary** for *ordinary* snapshots (they restore state, not code); and the
  conversation transcript (history is append-only and orthogonal).
- **When taken** — **automatically before any risky or sweeping change** (a guard
  toggle, an endpoint change, a bulk "make it cheaper" reconfiguration, a mode
  switch), so recovery never depends on the user remembering; **and on command**,
  from a Settings control or by asking Addison ("snapshot now") to mark a state
  known-good before experimenting.
- **Restore targets the last *verified-working* state** — a config marked good
  after a turn completed successfully against it — *not* merely "the state before
  the last edit." That is the difference between real recovery and the story's dead
  end: Restore always lands somewhere that actually ran.

**The undeletable anchor (new rule).** Normally snapshots are housekeeping and
deletable. But the moment a safety guard is **turned off in Custom mode and saved**
(§7.11), Addison mints an **undeletable** snapshot of the last verified-working
state — neither user nor model can remove it, and it persists even if the guard is
switched back on. This anchor **also captures the app binary** (a complete
known-good *build + config*, unlike ordinary state-only snapshots), so a weakened
session that corrupts more than config still has a whole-app state to return to.
Keys are excluded even here (G1 holds). So the act of lowering your own protections
*always* leaves a guaranteed way back.

**The keyword gate as an injection defense.** Running or arming a powerful/elevated
action (OS-run automation, a code-backed widget) requires the user to type a
**specific keyword prefix** (syntax TBD, §14). Because the prefix is *user-typed*,
content Addison merely observes — a web page, a file, a tool result — **cannot
supply it**: observed content can instruct the model, but it cannot type a keystroke
into your composer. The prefix is therefore simultaneously an "are you sure" and a
structural barrier that aligns the elevated-action boundary with the one thing
injected content can never forge.

**The four floors — none of which any mode or guard can switch off:**

| Floor | Statement |
|---|---|
| **G1** | API keys never reach the frontend/webview or SQLite; keychain-only (snapshots exclude keys, reinforcing this). |
| **G2** | Addison never triggers itself; it may *author* OS-run automation, but the OS runs it — no scheduler in Addison (§4). |
| **G3** | Guaranteed one-action rollback to a last-verified-working state; the restore path is unbreakable. |
| **Anchor** | Weakening a guard in Custom mode mints an undeletable, binary-capturing recovery point. |

**Impossible for anyone, in any mode:** editing Addison's code, removing a floor,
deleting the undeletable anchor, or reaching a state with no restore. This is how
"let advanced users tune guards" coexists with "no one can brick Addison." (See
§7.9 for the fine-grained per-action undo that complements whole-config restore,
and §7.11 for the mode/profile model these floors sit under.)

---

## 10. Tech Stack Summary

| Layer | Choice | Rationale |
|---|---|---|
| Shell | Tauri | Small binary, Rust-backed permission logic, reuses your React experience |
| Frontend | React + TypeScript + Tailwind | Matches your existing frontend background |
| Agent core (prototype) | Python | Fast iteration, matches Sentiment Signal/PyQuest stack |
| Agent core (shipped) | Rust (post-stabilization) | Single binary, no bundled runtime |
| Local storage | SQLite | Matches Almanac's local-first approach |
| Model provider | Anthropic, OpenAI, Google (cloud) + Ollama (local) behind one interface | Cloud adapters are mechanical translation work; Ollama exposes an OpenAI-compatible API, reusing the OpenAI adapter |
| Local model runtime | Ollama | Handles model download, quantization, and serving — avoids building a model runtime from scratch |
| Updates | Tauri updater | Signed, silent, no manual steps |
| Free-tier relay (no self-hosted model) | Small serverless function (free-tier hosting) + Redis/KV for queue & device state | Lightweight — a relay/router, not a model host; no ops burden |

---

## 11. Roadmap

**Phase 0 — Prototype (2-3 weeks, part-time)**
Python agent loop, 3 tools (web search, read a dropped file, save a file), CLI-only, no permission UI yet. Goal: prove the orchestration loop and tool schema, not the product.

**Phase 1 — MVP Desktop App (4-6 weeks)**
Tauri shell wrapping the prototype loop, permission-card UI, SQLite memory, BYOK-only credentials against a single cloud provider (Anthropic). Goal is validating the core UX, not provider breadth yet. Ship to a handful of non-technical testers (exactly the audience you already do casual outreach to for Almanac feedback).

**Phase 2 — Setup Assistant (2-3 weeks)**
Build the free relay (§7.5.1): integrate at least one, ideally two, free hosted-model sources, deploy the relay on free-tier serverless hosting, write and tune the Setup Assistant's system prompt (what it explains, when it names its own limits, when it offers the key wizard), and build the mid-conversation handoff into `DirectAPIProvider` once a key is added. Smaller and more predictable in scope than an open-ended free chat tier would be, since the task is narrow — guide setup, not do general work — but the system-prompt tuning and handoff UX are real design work and shouldn't be rushed, since this conversation is most new users' actual first impression of the product.

**Phase 3 — Polish & distribution**
Installer signing/notarization for macOS/Windows, auto-update pipeline, a real landing page, expand tool set carefully (email drafting, calendar look-ups) — always tiered by risk per §7.4. Rewind/self-repair (§7.9) ships no later than this phase — it's a trust prerequisite for widening the tool set, not a nice-to-have that comes after.

**Phase 4 — OpenAI/Google cloud adapters**
Add OpenAI and Google adapters (§7.3.1) alongside Anthropic. This is deliberately sequenced after the trust-building phases (rewind, polish) rather than the MVP.

**Revision — local model support moved into v1, not deferred to Phase 4.** The engineering implementation spec (`addison-engineering-spec.md`) pulls Ollama integration and the `LOCAL` model role into the initial build, sequenced as the *last* v1 implementation step (after the core loop, tool set, undo system, and BYOK/Setup Assistant path all work) rather than into a later roadmap phase — see that document's §4.1, §4.1.2, and §11 for the concrete architecture (a `ModelRouter` resolving per-request between `PRIMARY` and `LOCAL` roles, both configurable and reachable at once) and build order. The hardware-gating and "Basic tool support" caveats from §7.3.2 below still apply in full; only the *timing* changed, not the caution around local-model reliability.

**Phase 5 — Messaging channels**
Telegram integration first (lowest friction, free Bot API), built on the managed-proxy backend already running since Phase 2. WhatsApp and SMS evaluated afterward based on real user demand and Meta's Business API cost. Requires the pairing/authorization flow in §7.10 before any channel ships, not after.

**Planned for v2 — automatic, capability- and difficulty-aware model routing.** With multiple local models and the vision/audio capability flags shipping in v1 (§7.3.4), v2 adds an optional layer that picks the model per turn by task difficulty and required capability — off by default and always overridable. Deliberately sequenced after v1's explicit-only routing so the "no hidden decisions" contract (§9) is established before any automatic choice is introduced. The **Model Cascade** module (§7.3.5) — a cheap-draft → strong-refine pipeline expressed as a Routine, not built into the core — is planned for the same phase, on the same substrate.

**Adopted from the 2026-07 ecosystem survey.** Six features from the OpenClaw /
Hermes / Cowork / Manus / Goose survey (sourced dossier, 2026-07-17) clear all
three bars — persona fit (§5), full compatibility with every safety invariant
(§9; engineering-spec §8), and not already shipped or planned:

1. **Scoped consent on the permission card** — "Allow just this once" vs
   "Always allow this" as an explicit choice on the card itself (seen in Manus
   and Cowork). Pure UX over the existing PermissionGate grant/scope_details
   machinery; the gate itself is untouched. → *UI/UX polish (next phase).*
2. **Plain-language cost & usage visibility** — an optional per-reply usage
   footer and a Settings "What this costs" view (seen in OpenClaw and Cowork).
   Builds trust for cost-anxious users; fits the calm-utility look. →
   *UI/UX polish (next phase).*
3. **Conversation list & local search** — multiple conversations
   (new/rename/archive) plus full-text search over the transcripts Addison
   already stores in SQLite (Hermes's FTS approach; OpenClaw's session
   management). "Find that thing we talked about" is a core persona need; all
   local, user-deletable. → *UI/UX polish → Phase 3.*
4. **Folder-scoped workspace grant** — an explicitly user-granted folder
   ("Addison may use Documents/Addison") enforced in the Rust shell,
   complementing the picker-scoped handles (seen in Manus and Cowork). Widens
   file usefulness without widening trust: still shell-brokered, still
   undo-backed, plainly bounded. → *Phase 3 (expand tool set carefully).*
5. **Routine sharing (export/import)** — a routine's declarative plan as a
   file another Addison can import (Goose's portable Recipes; skill-registry
   ecosystems). Plans are data (§7.9/no-code invariant); an imported routine
   carries **zero permissions** and asks like any first run — §6.4's
   no-escalation rule travels with it. No marketplace. → *v2.*
6. **Untrusted-content screening** — an advisory defense-in-depth layer that
   inspects tool-returned content (web results, file text) for instruction-like
   payloads before the model consumes it, building on the untrusted-wrapping
   Addison already does (Goose's injection detection / adversary-reviewer
   pattern). Screening advises and flags; the permission gate remains the only
   authority. → *v2.*

**Explicitly deferred beyond Phase 5:** multi-agent orchestration, scheduled/always-on agents, shell-level tool access.

**Amended 2026-07-20 — the butler wave (docs first, then code).** The scope
amendment sequences its own work as *authoritative docs updated before any code*,
then code in dependency order with the **safety floor built first**. As a roadmap
track (post-greenlight):

1. **Snapshot / restore subsystem (G3)** — the floor everything else leans on,
   built and hardened first; its single most important test is *"restore always
   works, even from a broken config."* Includes automatic + on-command snapshots
   and the app-binary capture used by Custom anchors (§9).
2. **Custom profile + guard model** (`policy.py`) + the undeletable-anchor rule
   (§7.11).
3. **Routing strategies** (four named + Custom) + the companion prefer-quality /
   prefer-free toggle + free-model disclaimer + graceful fallback/cooldown (§7.3).
4. **Free-model endpoints** — first-class legit free/local + add-by-prompt
   (shared plumbing with connecting an MCP server, step 7).
5. **Harness + workspace-trust** (OPEN) — the trust boundary the powerful
   capabilities below depend on (§7.11).
6. **Widget capability tiers + expanded vocabulary** — safe interactive kinds
   (to-do/checklist, note, timer) with trusted renderers and safe storage
   (buildable in all modes), capability-tier gating, capability-aware guidance.
7. **MCP client integration** — external tools through the registry + gate,
   mode-scoped (OPEN under workspace-trust; SAFE read-only / undo-able only).
8. **Automation keyword gate** + author-OS-run automation (§4, §9).

Steps 3–4 are companion-facing and independent of the harness, so they can run in
parallel with 5–8 once 1–2 land. Each step stays independently testable behind the
same gate as today. Note this **supersedes** the earlier "automatic model routing
is v2" line above for the *bounded-strategy* form — the four strategies ship in
this wave; unbounded/confidence-based auto-selection depth remains a Phase-2 open
question (§14).

---

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Setup Assistant's system prompt is tuned wrong (pushes the key wizard too pushy/salesy, or too passively to ever convert) | Either annoys new users or fails to convert them to sustained BYOK use, defeating the point | Tune conversationally with real Phase 1 testers (§14), not by guessing tone in this doc |
| Setup-session farming via reinstalls | Someone repeats the free setup conversation instead of ever adding a key | Low-stakes by design (§7.5.1) — the value being farmed is a short guided conversation, not worth heavy engineering to fully prevent |
| Setup → subscription handoff feels like a sales pitch rather than a natural next step | Users get defensive about the key wizard, hurting trust right at the first real interaction | Offer, don't push — the assistant names a real limitation and links directly to the fix (§7.5.1), rather than a generic upgrade prompt; declining stays a first-class, respected option |
| Prompt injection via a malicious webpage/document leads to unwanted tool use | User trust breaks after one bad incident | Strict capability allow-list (§9) means worst case is bounded — no shell access to escalate |
| Tauri + Rust learning curve slows solo development | Timeline slips | Prototype in Python first (§7.2) so the product concept is validated before investing in the Rust rewrite |
| Non-technical users still hit an error they can't self-resolve | Support burden, churn | Every error state gets a plain-language message + one suggested action, written and tested with actual non-technical testers, not written by you assuming their vocabulary |
| Feature creep toward "OpenClaw for beginners" (schedules, shell tools) | Loses the focus that makes it approachable | Keep the non-goals list (§4) visible in every planning conversation |
| Rewind/snapshot system has gaps (a mutating tool ships without a real `undo()`) | User hits the one case where "put it back" doesn't work, breaking the core trust promise | Enforce the undo-at-registration constraint (§7.9) in code, not just convention — a tool with no undo path is mechanically capped at read-only risk tier |
| Messaging channel widens the attack surface before the pairing/authorization flow is solid | Someone other than the owner issues commands to the agent | Gate Phase 5 launch on the identity/authorization design in §7.10 explicitly, don't let "Telegram is easy to integrate" pull the channel forward before that's done |
| Local/small models silently underperform on tool use, degrading the "just works" promise | Non-technical users who wander into "Run locally" get a worse experience and blame the whole product | Hardware gating + explicit "Basic tool support" labeling (§7.3.2), and keep local models out of the default onboarding path entirely |
| Supporting many providers becomes a maintenance burden (each updates its API independently) | Broken adapters after a provider ships a breaking change, discovered by users first | Keep the adapter surface minimal and well-tested; treat provider count as a deliberate, reviewed decision each time, not something added casually |
| A free provider throttles, changes, or discontinues its free tier | Trial onboarding degrades or breaks for new users, since it depends on someone else's offer | Integrate multiple independent free sources with failover (§7.5.1); impact is now bounded to first-run experience, not an ongoing service people depend on |

---

## 13. Comparison to Existing Options

*Updated 2026-07-17 from the ecosystem survey (sourced dossier; adopted features
in §11). Columns beyond the original two reflect the survey's primary sources.*

| | OpenClaw | Hermes Agent (Nous) | Claude Cowork | Manus "My Computer" | Goose (Block) | Addison (v1 as shipped) |
|---|---|---|---|---|---|---|
| Setup | CLI onboard wizard, self-hosted daemon | curl/PowerShell install + wizard; native desktop app | Part of Claude app; paid plans | Desktop app, sign-in, authorize folders | Desktop app + CLI + embeddable API | Single installer (Phase 3); Setup Assistant onboarding (client shipped) |
| Target user | Developers/power users | Developers/researchers | Mainstream paid users | Non-technical professionals + power users | General purpose, dev-leaning | Non-technical by default; Developer via opt-in profile (§7.11, shipped) |
| Tool access | Broad: terminal, browser, files, channels | 40–60+ tools; six terminal backends | Files, browser control, computer use, MCP | Local terminal commands, apps, files in authorized folders | 70+ MCP extensions | Narrow typed allow-list, risk-tiered, shell-brokered; no shell/exec ever |
| Consent model | Full access for main session; pairing for others; approvals UI (2026.7) | Command approval; per-session bypass toggle | Manual / auto-screened / skip modes; deletion always explicit; app blocklist | Per-command approval; "Always allow" vs "Allow once"; folder scoping | Tool permission controls + injection detection + adversary reviewer | Per-tool permission cards; LOW remembered, MEDIUM per-action; profile never changes the gate (§8.7, invariant-tested) |
| Undo/recovery | Session archive/restore; no per-action undo | `/undo` command | None per-action; approval-first | Not documented | Not documented | Versioned action rewind with real `undo()` enforced at registration + conversational rewind (shipped) |
| Model support | Any provider/local, per-task | Any (Portal/OpenRouter/OpenAI/vLLM); model+effort+fast picker | Claude models | Not documented | 15+ providers incl. Ollama | Dynamic per-key model list with capability-derived effort levels (shipped) + Ollama local pool (shipped) |
| Automation | Skills + cron/webhooks/triggers | Self-writing skills; cron; subagents | Saved + scheduled cloud tasks | Scheduled tasks, always-on machines | YAML Recipes + subrecipes; subagents | Declarative Routines through the live gate/registry; manual trigger only in v1 (§7.9/§6) |
| Memory | JSONL history | FTS5 search + summarization + user model | Account-saved sessions | Not documented | Not documented | Confirmed-facts only, user-visible/deletable (§7.6); full local transcript |
| Cost visibility | Per-reply usage footers; spend dashboards | Not documented | Usage settings; "costs more" notice | Not documented | Not documented | Not yet — adopted for UI/UX polish (§11 survey adoptions) |

Worth explicitly re-checking Claude Cowork's current feature set before committing to this build — if it already covers the "non-technical, agentic, no CLI" niche well enough, the better use of your time may be building something that plugs into it (a custom tool/connector) rather than a parallel harness.

**Amended 2026-07-20 — the positioning line, sharpened.** After the butler
amendment, the table's differentiators resolve to one sentence: **Addison is the
harness you can't brick, plus a companion side.** Against Claude Code / OpenClaw /
Hermes, the Developer profile is a peer coding-agent harness (agentic loop over a
real project) — but the one none of them offer is *guaranteed, one-action rollback
to a last-working state* (G3, §9): the story that motivated this whole amendment is
a capable harness that permanently bricked on a single "make it cheap" request with
no way back. Against Cowork and Manus, the Simple profile is the calmer,
non-technical companion — but standing on that same unbrickable floor, with legit
free/local models so it needs no paid frontier key. In the comparison table's own
terms, Addison's edge is concentrated in the **Undo/recovery** row (whole-config
restore, not just per-action or session archive) and the **Automation** row (author
OS-run automation, never self-triggering — G2 intact — versus the survey field's
built-in schedulers).

---

## 14. Open Questions

1. Is a desktop app the right form factor, or does a hosted web app remove even the installer step? (Tradeoff: hosted means you own inference cost for every user, always, with no offline/local-first story.)
2. How much of Almanac's local-first storage layer can be shared/reused here, versus being a genuinely separate codebase?
3. How pushy should the Setup Assistant be about offering the key wizard — surfaced the moment a limitation is hit, or only after the user has explored a bit? Worth testing both against real Phase 1 testers rather than guessing, since this is the moment most likely to determine whether someone converts to sustained use or bounces.
4. Does the target persona (Mira, Petr) actually want an open-ended chat agent, or would a narrower set of guided workflows ("summarize this," "draft this email") test better than a blank chat box? Worth a small round of user interviews before Phase 1.
5. For action rewind (§7.9): full-copy snapshots are simpler to implement correctly but cost more disk space; diff-based snapshots are cheaper but riskier to get wrong for binary files (PDFs, images). Worth prototyping both against real test files before committing.
6. For messaging (Phase 5): is Telegram-first the right call, or does the target persona overwhelmingly already live in WhatsApp, making Telegram a technically-easier but practically-unused first channel? Worth checking with the same testers from Phase 1 rather than assuming.
7. For model support (§7.3): is OpenAI or Google the higher-value second cloud provider to add first, and is that decision driven by user demand or just "which adapter is easiest to build"? Worth resisting the latter as the deciding factor.
8. Local models (§7.3.2) serve a meaningfully different user than personas Mira/Petr — more privacy-conscious, more technical, likely price-sensitive. Is that user actually part of Addison's target market, or a different product wearing the same shell? Worth being explicit about before investing in Phase 4, since it changes who Phase 4 is user-tested with.

**Amended 2026-07-20 — open questions from the butler amendment (to resolve during
the docs/spec update; all Phase-2).**

9. **Keyword-gate syntax** — the exact user-typed prefix (`!run`, `arm:`, `sudo:`…)
   and the precise set of actions it gates (running/arming powerful or OS-automation
   actions in the harness, not ordinary chat) (§4, §9).
10. **Snapshot retention** — how many ordinary snapshots to keep, and how anchors
    accumulate (every weakening, or a single most-recent working anchor) (§9).
11. **Custom reachability** — from Simple directly, or only via Developer first
    (current lean: reachable-but-deep regardless of starting profile) (§7.11).
12. **Verified-working definition** — precisely which "successful turn" marks a
    config good (any completed turn, or one with no error and no rolled-back
    action?) (§9).
13. **Auto-routing depth now vs. v2** — how much confidence-based escalation ships
    in the bounded strategies now versus stays substrate for later (§7.3, §11).
14. **MCP tools in SAFE** — the exact companion constraint (read-only only? a
    curated allowlist? dev-only?) and how MCP tool metadata declares undo-ability
    (§7.4).
15. **Widget capability tiers & vocabulary** — the exact safe interactive kinds,
    how a widget spec *declares* the capabilities it needs, how the tier check maps
    capabilities → mode, and how code-backed widgets are managed alongside
    declarative ones (§7 note, §7.9).
16. **Anchor binary capture** — how the app binary is captured/restored in practice
    (version pin? copy-on-write?) without bloating storage (§9).
