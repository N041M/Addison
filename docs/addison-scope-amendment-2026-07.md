# Addison — Scope Amendment (2026-07-20)

**Status:** ADOPTED 2026-07-20 (Phase 1 — docs). Owner greenlit. The authoritative
docs (`CLAUDE.md`, `docs/architecture.md`, `docs/data-model.md`, `docs/flows.md`,
`docs/classes.md`, `docs/addison-design-doc.md`, `docs/addison-engineering-spec.md`)
are being updated to match. Code follows in the phased order in §14.

> **Update 2026-07-20 — Phase-2 step 1 has shipped.** The snapshot/restore subsystem
> (G3) is built. Two owner decisions taken during that step **correct wording in this
> document**, and the corrections are inline where the original claims were made:
> the anchor **records a build reference, it does not capture or restore the app
> binary** (§3.1, §3.3, §12, §13 Q8 — binary restore is now a Phase-3 updater item),
> and §13's Q2, Q4 and Q8 are resolved and marked as such. Where this document and the
> inline decision notes differ, **the notes win** — they describe what exists.

**Amends:** the mode-scoped safety model (owner decision 2026-07-19), the v1
scope lines on scheduling and auto-routing, and the product framing of the
Simple/Developer split. It does **not** repeal the existing safety invariants;
it adds one global floor, reinterprets one, and sharpens the identity around
them.

**One-line summary:** Addison is a butler. Its Developer surface becomes a
real coding-agent harness (Claude-Code-class) and its Simple surface an
all-in-one companion for non-technical people — both standing on a single,
non-negotiable guarantee: **you can always roll Addison back to a working
state.** Alongside, Addison stops requiring a paid frontier model to be useful.

---

## 1. Why this amendment exists (the motivating story)

A non-technical person set up OpenClaw after considerable effort and had it
build the equivalent of an Addison widget — a monitor that checked a PC↔WhatsApp
connection and notified on drop/recovery. It worked. He then asked it to "make
the models run as cheaply as possible," and that single sweeping request broke
his setup **permanently**. The built-in rewind did not work, and — not being an
IT person — he had no way back.

Three lessons drive this amendment:

1. **Safety, for Addison, means guaranteed recovery.** Not "we ask before
   risky things" (though we do), but "no request — from the user or the model —
   can leave Addison in a state you can't get out of." The failure mode to
   design against is *unrecoverability*, and the anti-pattern to never repeat is
   *a rollback that doesn't fire.*
2. **The power is worth wanting.** People want a tool that can build the monitor.
   The barrier isn't capability; it's the cliff you fall off when something goes
   wrong. Remove the cliff and the power becomes safe to offer widely.
3. **The two audiences want the same core, at different surface heights.** A
   developer wants a harness; a non-technical person wants a companion. Both
   want it to never brick itself.

---

## 2. Identity: Addison is a butler

A butler acts when asked and never uninvited, handles the messy parts so you
don't have to, remembers how you like things, and — crucially — is *discreet
and reversible*: a good butler never puts the house in a state you can't
restore. This is already encoded in Addison's mark (the service bell: "ring, and
Addison comes"), its "approachable by default, powerful on request" thesis, and
the guidance-skills + memory work (a butler that learns your preferences).

The amendment sharpens the two surfaces:

- **Developer profile → OPEN mode → a coding-agent harness.** Peer to Claude
  Code / OpenClaw / Hermes in capability (agentic loop over a real project:
  read/edit files, run builds and tests, iterate), with Addison's safety and
  quality-of-life guarantees layered on. The differentiator is not "another
  harness" — it is *"the harness you cannot brick, and cannot fall out of."*
- **Simple profile → SAFE mode → an all-in-one companion.** For people who lack
  the technical prowess (or desire) to stand up one of those harnesses. Same
  unbrickable base; a calmer, plain-language surface.
- **Custom profile → a user-tuned surface** (§7) that sits between them, reached
  only deep in Settings behind extra confirmation.

The product line, in one sentence each: *Developer* = Claude Code you can't
brick. *Simple* = a capable companion on the same floor. *Custom* = "I know what
I'm doing" — with the one guarantee that never turns off.

---

## 3. The central new invariant — G3: guaranteed rollback

> **G3 (NEW, GLOBAL FLOOR).** Neither the user nor the model can drive Addison
> into an unrecoverable configuration. At all times there exists a one-action
> restore to a **last-known-working** state, and that restore path is itself
> unbreakable.

This is the load-bearing addition. It is realised with **app-state snapshots**:

### 3.1 What a snapshot is

A snapshot is a point-in-time copy of Addison's **mutable state**:

- Settings (active profile, theme, mode-relevant flags, routing choice, custom-guard toggles).
- Provider configuration (which providers/endpoints are configured, their non-secret metadata, the selected/default models, routing strategy).
- Skills, widgets, routines (the declarative artifacts).
- The relevant `provider_config` / settings / skills / widgets / routines rows.

A snapshot explicitly **excludes**:

- **API keys / the OS keychain** (owner decision, this amendment). Keys never
  enter a snapshot; restoring config leaves the keychain exactly as it is. This
  keeps **G1 (key isolation)** intact — a rollback can never move, expose, or
  clobber a key. (Consequence: after a restore, whatever keys are in the
  keychain remain; a restored provider config re-binds to them by provider id.)
- The **app binary / installed version**. **Partial exception:** the Custom-mode
  undeletable anchor (§3.3) additionally records a build **reference**, so a restore
  can say plainly whether the app itself has changed since. *(Owner decision
  2026-07-20 — see the correction in §3.3. This bullet originally promised that the
  anchor captured the binary and gave "a complete known-good build + config" to fall
  back to; it does not.)*
- The conversation transcript itself (history is append-only and orthogonal;
  rollback is about configuration, not erasing chats).

### 3.2 When snapshots are taken — automatically, and on command

The friend's rewind failed partly because recovery depended on him, so the
*automatic* trigger is the floor — but the user can also take one deliberately:

- **Auto-snapshot before any risky or sweeping change** — before a guard toggle,
  a provider/endpoint change, a bulk "make it cheaper"-style reconfiguration, or
  a mode switch. No user action required. (This is the guarantee; it never
  depends on the user remembering.)
- **On-command snapshot** — the user can create one deliberately at any time,
  either from a Settings control or by asking Addison ("snapshot now" / a
  snapshot command). Useful for marking a state you *know* is good before you go
  experimenting. On-command snapshots are ordinary (deletable) unless taken as
  part of weakening a guard in Custom mode (§3.3).
- **"Known-working" marking.** A configuration is marked *verified-working* after
  a turn completes successfully against it. **Restore targets the last
  verified-working state**, not merely "the state before the last edit" — so
  "Restore" always lands somewhere that actually ran, which is the difference
  between real recovery and the friend's dead end.

### 3.3 Deletability — and the undeletable anchor

- **Normally, snapshots are deletable.** In Simple and Developer, snapshots are
  housekeeping; the user may clear old ones.
- **Weakening safety mints a permanent anchor.** The moment a safety guard is
  **turned off in Custom mode and the settings are saved**, Addison creates an
  **undeletable** snapshot of the last verified-working state. Neither the user
  nor the model can remove it; it persists even if the guard is later switched
  back on. So the act of lowering your own protections *always* leaves behind a
  guaranteed way back. (Multiple weakenings may create multiple anchors;
  retention policy for anchors is an open question — §13.)
- **The anchor records the app build it was minted on** — a short
  `{"version", "identifier"}` reference, never bytes and never a path. A restore whose
  build differs from the one running says so in plain language and changes settings
  only. Keys are **still** excluded (G1 holds even here).

  > **Owner decision, 2026-07-20 (supersedes this amendment's original wording).**
  > As written, this bullet said the anchor "also captures the app binary" and called
  > it a *complete known-good build + config* restore point. **That is not what was
  > built, and the promise has been narrowed to what the code does.** Phase-2 step 1
  > ships the *capture* half as a version pin — a reference string in `binary_ref` —
  > and **no binary restore path at all**. Reasons, in order of weight: (1) the repo
  > must not carry a floor its own tests do not cover, which is the exact
  > anti-pattern this amendment was written against; (2) re-installing a prior build
  > is the Tauri updater's job, and `updater.rs` is an unwired stub — a second,
  > uncoordinated binary-replacement mechanism inside the recovery floor would be on a
  > collision course with it, and would be the one piece of the floor that could
  > itself brick the app; (3) a bundle copy is 50–150 MB per anchor, which would force
  > anchor eviction and contradict "undeletable" (§13 Q2's answer depends on anchors
  > staying cheap). **Binary restore is tracked as a Phase-3 updater item.** G4 now
  > reads: *lowering your own protections always leaves a guaranteed way back to a
  > working **configuration**, on a snapshot that records the build it was minted on.*
  > `CLAUDE.md`, `docs/architecture.md`, `docs/data-model.md`, `docs/classes.md` and
  > `docs/addison-engineering-spec.md` were corrected to match in the same pass.

### 3.4 Why G3 is a floor, in every mode

G3 never turns off — not in Simple, not in Developer, not in Custom, not for any
guard toggle. Custom mode can loosen *prompts* (§7); it can never remove the
ability to restore. G3 joins the standing global floors:

| Floor | Statement | Status |
|---|---|---|
| **G1** | API keys never reach the frontend/webview or SQLite; keychain-only. | Unchanged; reinforced (snapshots exclude keys). |
| **G2** | No autonomous self-triggering / scheduling by Addison. | Reinterpreted, still a floor (§9). |
| **G3** | Guaranteed one-action rollback to a last-working state; restore is unbreakable. | **New.** |

---

## 4. The organizing principle: reversible data vs. inviolable machinery

The apparent contradiction — "the user shouldn't be able to alter Addison" vs.
"the user can add endpoints / tune guards / ask Addison to reconfigure itself" —
resolves cleanly:

- **Reversible data/config** — provider endpoints, model choices, routing
  strategy, cost settings, which guards are on, skills, widgets, routines. The
  user *and* the model may change all of it, **because every such change is
  auto-snapshotted and one-action reversible** (§3). This is *data*, not code.
- **Inviolable machinery** — Addison's own code, the orchestration/gate/registry
  machinery, and the four floors (G1, G2, G3, and the undeletable-anchor rule).
  Never alterable by user or model, in any mode.

Risky reversible changes (disabling guards, adding a raw endpoint) are legal but
live **deep in Settings behind additional questioning** — friction, not a wall.
The friction reduces accidents; **the snapshot is the actual guarantee.** (An
important honesty note: model-driven "additional questioning" is friction that a
determined user clicks through and that a prompt-injection could try to talk
around — so it is *not* relied on as the safety net. G3 is.)

---

## 5. What "alter Addison" is allowed to mean

Because of §4, the following are all **allowed**, each as a reversible,
snapshotted, one-click-undoable change:

- The user asking Addison, in plain language, to **add a model endpoint** ("add
  this OpenAI-compatible server") — Addison registers a provider config (base
  URL + key, stored per G1). This is declarative data, so it does not violate
  "can't alter Addison."
- **Reconfiguring for cost** (§11).
- **Tuning guards** in Custom mode (§7).

The following remain **impossible for anyone**: editing Addison's code, removing
a floor, deleting the undeletable anchor, or reaching a state with no restore.

---

## 6. Free / no-frontier-required models

**Principle:** Addison must be genuinely useful **without a paid frontier API
key.** This is central to the companion persona (who will not set up frontier
billing) and a convenience for the developer (cheap iteration).

Addison is already partway there — Ollama local models are free, private, and
keyless, and the Setup Assistant relay gives a keyless first run. The gap is
users on weak hardware who can't run a capable local model; **legitimate free
cloud tiers** (e.g. official free tiers of major providers) fill it.

### 6.1 In-app: legitimate free/local only

- Only **legitimate** free and local models are offered, surfaced, or endorsed
  in the app. No gray-area/ToS-circumventing sources appear anywhere in the UI.
- The existing **OpenAI-compatible "custom server" provider is the extension
  hook** — it already lets Addison point at any compatible endpoint.

### 6.2 Extensible endpoints, addable by prompting

- New endpoints can be added by the user **prompting Addison directly** ("add
  this endpoint"). Addison registers it as reversible provider-config data
  (snapshotted; keys per G1). The infrastructure is built so future endpoints
  slot in without code changes.

### 6.3 Gray-area routers: documented on GitHub only

- Aggregating routers (OmniRoute, LiteLLM, etc.) — including ones whose "free"
  access is ToS-gray — are **the user's own choice**, documented in the project
  README/GitHub as "you can point Addison's custom endpoint at one of these,"
  and are **never surfaced, named, or endorsed inside the app.** Addison the
  product does not pick locks for free tokens.

### 6.4 What we deliberately do NOT adopt from OmniRoute

OmniRoute is the project that motivated the free-model idea, but it is a
maximalist developer gateway — the opposite of Addison's minimal, approachable,
single-user posture. We take the *principle* (run without a paid frontier key;
graceful cross-model fallback; routing strategies), not the product:

- **268-provider / 500-model aggregation & free-tier farming** — against the
  approachable-minimal philosophy.
- **TLS/JA3-JA4 fingerprint spoofing to bypass IP blocking** — detection
  evasion; off-brand and not built, ever.
- **Team quota-sharing / DRR scheduler** — Addison is single-user, local-first.
- **MCP/A2A as a *server or gateway*** — Addison is not a gateway that others
  route through. **But Addison *is* an MCP *client*** (it consumes external MCP
  tools) — that is in scope and new in this amendment; see §8.5. (A2A remains
  out of scope.)
- **11-engine token compression** — complexity the calm companion doesn't need
  (simple context management, already a v2 line item, suffices).

---

## 7. The mode model, revised: Simple / Developer / Custom

Today the mode is **derived 1:1 from the profile** (`policy.py`,
`mode_for_profile`): Simple→SAFE, Developer→OPEN, with no separately-persisted
mode. This amendment adds a **third profile, Custom**, whose *prompting* guards
are user-tunable — and only its prompting guards.

- **Simple → SAFE** — the companion. Byte-for-byte today's SAFE behaviour where
  unchanged (see §12 for what shifts).
- **Developer → OPEN** — the harness (§8).
- **Custom → user-tuned** — reachable only deep in Settings, behind additional
  questioning. The user may loosen or tighten the *prompting* guards (the
  per-invocation destructive card, the auto-grant scope, the workspace-trust
  boundary, the keyword gate's strictness). The user may **never** touch the
  floors: G1, G2, G3, and the undeletable-anchor rule are not in the Custom
  panel at all.

**The Custom-mode safety contract:**

1. It lives deep, behind extra confirmation (accidents are unlikely).
2. Turning any guard **off** mints the undeletable anchor (§3.3) — a permanent,
   guaranteed way back (recovery is always possible).
3. The floors are absent from the panel (the most dangerous things simply cannot
   be switched off).

This is how "let advanced users disable guards" coexists with "no one can brick
Addison": you can lower the prompts, but you can never lower the floor, and
lowering a prompt guarantees you a recovery point.

*~~Open question (§13)~~ — resolved as the lean (Phase-2 step 2, 2026-07-24):
reachable from any profile, behind an "Advanced…" disclosure and a two-step
inline confirm. See §13 Q3.*

---

## 8. Developer/OPEN as a coding-agent harness

"Act closely to Claude Code / OpenClaw" means a genuine agentic coding loop, not
a chat with `run_command`. Concretely:

### 8.1 Capabilities

- An agentic loop over a **real project directory**: read, edit, and create
  files; run builds, tests, and tooling; read their output; iterate.
- The existing typed tools + `run_command` (dev-only) remain the substrate; the
  harness is a *flow and trust* layer on top, not a new execution surface.

### 8.2 Workspace-trust — reconciling the loop with the gate

Today OPEN mode auto-grants non-destructive calls and raises a **per-invocation
card for every destructive one**, with no memory between them. That is right for
a chat butler and *hostile* to a coding loop (dozens of edits/runs).

Resolution — a **workspace-trust** model:

- The user grants a **project directory** (an explicit, snapshotted act).
- **Within** the trusted workspace, OPEN mode acts freely — edits and runs flow
  without a card per action (the gate still *runs and logs* on every call; it
  just doesn't *prompt* within the trusted scope).
- **Outside** the workspace (system paths, other directories, the keychain, the
  network beyond configured providers), destructive actions still raise the
  per-invocation card exactly as today.
- Trust is scoped, revocable, and snapshotted; revoking it or leaving the
  directory restores prompting.

This keeps the gate's guarantee ("it runs and logs on every call") while making
the harness usable. The per-invocation card is not weakened globally; it is
*scoped* to a directory the user deliberately trusted.

### 8.3 Undo still applies

Every mutating tool keeps its `undo()` (invariant 2). File edits, in particular,
remain individually undoable, and the workspace sits under G3's snapshot floor —
so a coding session that goes sideways has both fine-grained undo and a
whole-config restore.

### 8.4 Widgets are buildable in every mode — capability is what's gated

Definitive owner decision (this amendment), superseding earlier drafts:
**widgets are buildable in *all* modes.** The mode gates the *capability* a
widget may use, not whether one can be built at all. A SAFE user who asks for a
quick to-do widget gets it built and shown; what a widget may *do* rises with the
tier.

- **SAFE / companion — buildable, non-destructive.** SAFE-tier widgets are drawn
  from a **safe capability set** and **can never harm the user's machine or
  Addison itself.** Concretely this is an *expanded declarative vocabulary* — the
  existing launchers (routine / stat / command) **plus interactive display
  widgets** (to-do / checklist, note, counter, timer, …) rendered by *trusted
  Addison components* and backed by Addison's own safe storage. No shell, no
  destructive filesystem access, no system reach, and **no arbitrary code or
  eval** — the widget's behaviour comes from the bounded vocabulary and Addison's
  own renderers, so **SAFE-1 (no arbitrary code) and the webview CSP still hold.**
  "Build me a to-do widget" fires here and produces a real, usable checklist.
- **Higher tiers (Developer / Custom) — buildable *and* powerful.** On top of the
  SAFE set, the harness can build **code-backed / system-capable widgets** —
  monitors, scripts, tools that touch the machine or network (the friend's
  connection monitor). These may be destructive, so they are governed by
  workspace-trust (§8.2), per-tool `undo()` (§8.3), the snapshot floor (§3), and
  — to *run or arm* one — the keyword gate (§9). Scheduled ones still go through
  "Addison authors, the OS runs" (§9).

**The invariant, restated:** a widget can **never exceed its mode's capability
tier**, and **a SAFE-tier widget is non-destructive by construction** — it cannot
reach anything that could harm the machine or Addison. Rollback (G3), undo, and
the gate apply to widgets in every tier.

**Phase-2 consequences:**
- Expand the widget vocabulary with **safe interactive kinds** (to-do/checklist,
  note, counter/timer) + trusted renderers + safe backing storage — this is how a
  SAFE widget is functional *without* code.
- Add **capability tiers** to widget validation: a widget declares the
  capabilities it needs; the tier check gates it (SAFE admits only the
  non-destructive set; higher tiers admit code-backed/system capability).
- Make the `primary.txt` guidance **capability-aware**: in *every* mode Addison
  can build safe widgets (to-do, etc.); only destructive/system widgets are
  higher-tier — superseding the earlier "can't build a custom app as a widget"
  wording, which is now wrong even for SAFE.

### 8.5 External tools via MCP (Addison as a client)

Owner decision (this amendment): **Addison should work with MCP.** The
distinction that matters: Addison is an MCP **client** — it *consumes* external
MCP servers/tools — **not** an MCP server or gateway (the OmniRoute-style thing
we still decline, §6.4).

- MCP tools are surfaced through Addison's **existing tool registry and
  permission gate** — never a side channel. Same rules: gated, logged,
  undo-aware.
- **Mode-scoped:** in OPEN/harness, MCP tools run under workspace-trust + the
  gate (and the keyword gate to *run* powerful ones). In SAFE/companion they are
  constrained to **read-only or genuinely undo-able** tools — a mutating MCP tool
  with no `undo()` cannot be LOW-risk, so **invariant 2 keeps it out of the SAFE
  view** automatically. The exact SAFE constraint (read-only only? curated
  allowlist? dev-only?) is an open question (§13).
- Connecting an MCP server is **reversible config** (like adding an endpoint,
  §5) — snapshotted, addable by prompting, revocable. It shares the
  add-an-endpoint plumbing.

---

## 9. Automation model — the G2 resolution

The friend's monitor needs background polling + autonomous notification, which
Addison forbids in **both** modes today (no scheduling/autonomous triggering).
The resolution keeps G2 as a floor while enabling the use case:

> **Addison authors; the OS runs; Addison never triggers itself. Powerful/armed
> actions require a user-typed keyword prefix.**

- Addison may **write and set up** OS-level automation — a `launchd`/`cron`
  entry, a small watcher script — exactly as Claude Code can scaffold a cron
  job. The **OS** runs it on its schedule; **Addison itself never fires
  anything autonomously.** G2 ("no autonomous self-triggering by Addison")
  therefore holds unchanged.
- **The keyword gate.** Running/arming a powerful or elevated action requires the
  user to type a **specific keyword prefix** in front of the message (e.g.
  `!run …` or an `arm:` prefix — exact syntax TBD, §13). Ordinary chat is
  unaffected.
- **Why the keyword is also an injection defense.** Because the prefix is
  **user-typed**, content Addison merely *observes* (a web page, a file, a tool
  result) **cannot supply it.** Observed content can instruct the model, but it
  cannot type your keyword into your composer. So the prefix is simultaneously an
  "are you sure" and a structural barrier against prompt-injection escalation —
  it aligns the "elevated action" boundary with the one thing injected content
  can never forge: a keystroke from the human.

---

## 10. Model routing

Brings forward the v1 substrate (capability flags + explicit picker) toward the
v2 auto-selection, in a bounded, on-brand form.

### 10.1 Four named strategies + Custom

Curated from OmniRoute's 18, four that mean something to a person:

- **Quality-first** — strongest capable model, **degrade down** to cheaper/free
  on unavailability, rate-limit, or budget. *(Default.)*
- **Cost-first** — cheapest capable model, escalate only when needed.
- **Local-only** — never leaves the machine (privacy); uses local models only.
- **Balanced** — a middle policy weighing capability, cost, and latency.

Plus a **Custom** routing builder — Developer only.

### 10.2 Exposure per surface

- **Companion (Simple):** a single, simple **"prefer quality / prefer free"**
  toggle. Default is quality-first. No jargon, no strategy zoo.
- **Developer:** the full strategy picker + the Custom builder.

### 10.3 Quality floor + free-model transparency

- Routing is **strong-first, degrade-down** by default (the inverse of
  OmniRoute's cheap-first) — the companion should not silently get a worse
  answer.
- When a **free** model answers, a visible **"answered with a free model"**
  disclaimer is shown, so trust is never quietly traded for cost.
- Escalation: when the picked model is unavailable/rate-limited (and, later,
  when confidence is low), routing falls forward to a stronger model rather than
  failing — the graceful-fallback pattern taken from OmniRoute's resilience
  layers, scaled to Addison's handful of connected providers, with plain-language
  notes ("X was busy, so I used your local model") and a light provider cooldown
  instead of hammering a failing endpoint.

---

## 11. The "make it cheaper" flow

The exact request that bricked the friend must become the *safest* thing to ask.
When the user says "make this use less money" / "make the models as cheap as
possible," Addison **orchestrates**, and **previews**, two reversible changes:

1. **Writes/proposes a guidance skill** (the primitive already shipped) — e.g.
   "keep answers brief; avoid re-reading large files; don't reach for the most
   expensive model unless the task needs it."
2. **Optimizes model selection** — switches the default role's model (and/or the
   routing strategy toward cost-first) to cheaper/free models.

Both are **reversible config**, so:

- Addison **shows the plan** ("I'll add this guidance note and switch your
  default to [cheaper model] — apply?") rather than silently rewiring.
- An **auto-snapshot** is taken before applying.
- **One-click "Restore"** returns to the last verified-working state.

The bricking scenario is structurally impossible: the change is previewed,
reversible, and floored by G3.

---

## 12. Invariant ledger — what changes, what holds

| Invariant (current) | Effect of this amendment |
|---|---|
| **G1** — keys keychain-only, never webview/SQLite | **Unchanged, reinforced.** Snapshots exclude the keychain (§3.1). |
| **G2** — no scheduling / autonomous triggering | **Reinterpreted, still a floor.** Addison never self-triggers; it may *author* OS-run automation; powerful actions need the keyword gate (§9). |
| **G3** — guaranteed rollback | **New global floor** (§3). Snapshots take automatically *and* on command; keys always excluded. |
| Undeletable-anchor-on-weakening (**G4** in `CLAUDE.md` and in code — the same rule) | **New rule** (§3.3), enforced in Custom mode; the anchor **records the app build it was minted on** (a reference, not the binary — owner decision 2026-07-20, §3.3), keys still excluded. Restoring a binary is a Phase-3 updater item. |
| **SAFE-1** — no arbitrary code/shell in SAFE | **Unchanged for Simple.** `run_command` remains dev-only; the harness lives in OPEN. |
| **SAFE-2** — every non-LOW tool has real `undo()` | **Unchanged.** Reinforced by workspace edits being undoable (§8.3). |
| **SAFE-3** — routines gain no privilege beyond granted | **Unchanged.** |
| **SAFE-4** — widgets are declarative specs, never code | **Reinterpreted: widgets are buildable in all modes, capability-gated (§8.4).** SAFE-tier widgets come from a safe, non-destructive vocabulary (launchers + interactive display kinds) rendered by trusted components — no arbitrary code/eval, so SAFE-1 and the CSP still hold. Higher tiers add code-backed/system-capable widgets under workspace-trust + undo + snapshot + keyword gate. Surviving guarantee: a widget never exceeds its mode's tier, and SAFE widgets are non-destructive by construction. |
| **MCP tools (client)** — *(new capability)* | Addison consumes external MCP tools via the **existing registry + gate** (§8.5). OPEN runs them under workspace-trust; SAFE admits only read-only / undo-able ones (invariant 2 enforces this). Not an MCP server/gateway. |
| Mode derived 1:1 from profile | **Amended.** A third profile (Custom) adds user-tunable *prompting* guards; the derivation still yields SAFE/OPEN behaviour, with Custom as a tuned overlay whose floors are fixed (§7). |
| Per-invocation destructive card (OPEN) | **Scoped, not weakened.** Within a trusted workspace it does not prompt (still logs); outside, unchanged (§8.2). |

**Net:** four global floors after this amendment — **G1, G2, G3, and the
undeletable-anchor rule** — none of which any mode or guard can switch off.

---

## 13. Open questions (to resolve during doc/spec update)

1. **Keyword-gate syntax** — exact prefix (`!run`, `arm:`, `sudo:`…), and the
   precise set of actions it gates (my read: running/arming powerful or
   OS-automation actions in the harness, not ordinary chat).
2. ~~**Snapshot retention**~~ — **RESOLVED (Phase-2 step 1).** Keep the most recent
   **50 or 30 days, whichever keeps more** (the same idiom as the undo window), with
   two exemptions written into the SQL rather than left to a caller: permanent rows,
   and **the newest TWO verified-working rows**. Retention here is not housekeeping —
   a rule that can prune the last verified rows leaves the one-action restore with no
   target, i.e. G3 silently off with no error anywhere, which is the friend's failure
   reintroduced by the recovery machinery itself. Two rather than one, and the second
   is load-bearing: the restore walk skips any verified row whose fingerprint matches
   the current config, so with only one exempt row that row could be exactly the one
   the walk skips, leaving nothing to restore to. **Every weakening mints a new
   anchor; anchors never prune and never count against the budget.** The alternative
   ("the single most-recent working anchor") was rejected: it requires *replacing* an
   undeletable row, which would create the codebase's only
   `DELETE … WHERE undeletable = 1` — the exact statement G4 says must not exist. Its
   stated worry was storage, and Q8's answer removes it: an anchor is a few KB.
3. ~~**Custom reachability**~~ — **RESOLVED (Phase-2 step 2, 2026-07-24), as the
   lean:** reachable from ANY profile, deep + questioned. `profile.get` marks the
   Custom entry `advanced: true`; the frontend renders it only behind an
   "Advanced…" disclosure, and selecting it runs a two-step inline confirm
   carrying the honest capability description before `profile.set` fires. If the
   owner ever wants stronger "questioning", it is a frontend-only change.
4. ~~**Verified-working definition**~~ — **RESOLVED (Phase-2 step 1).** **Any turn
   whose response was sent** — execution reached `_respond({"ok": True, …})`. A tool
   failure is deliberately *not* a turn failure (the orchestrator turns a tool
   exception into a failed `ToolResult` and continues), and the "no rolled-back
   action" variant was rejected because it couples config health to file-level regret
   through an independent mechanism with an unbounded window. The sub-decision the
   docs had never made matters more: the mark does **not** flag the pre-change row —
   that config never ran — it captures the **current** config as a new verified row,
   deduped by fingerprint. **Honest residual:** this predicate is satisfied by
   configurations that are *degraded* rather than dead, which is the whole "make it
   cheaper" class. The mitigation is that `restore_last_working()` never targets a
   config identical to the present one, so **each click steps back one distinct proven
   configuration** — but if the user makes two bad changes and a turn answers after
   each, **one click lands on the first bad config and they must click again.** That
   is bounded, visible (the card names the target before the click), and was chosen
   over a stronger predicate that would have to observe the future. *(Related, and
   also decided: the **genesis** row is written `verified_working = 1` on a fresh
   database, before any turn has run. Strictly nothing proved it — but G3 requires a
   restore target to exist at all times, including during onboarding, and refusing the
   mark would leave both G3 and G4 unsatisfiable in that window.)*
5. **Auto-routing depth now vs. v2** — how much of confidence-based escalation
   ships now vs. stays substrate.
6. **MCP tools in SAFE** — the exact companion constraint (read-only only? a
   curated allowlist? dev-only?), and how MCP tool metadata declares undo-ability.
7. **Widget capability tiers & vocabulary** — the exact safe interactive kinds
   (to-do/checklist, note, timer, …), how a widget spec *declares* the
   capabilities it needs, how the tier check maps capabilities → mode, and how
   code-backed widgets are listed/managed alongside declarative ones.
8. ~~**Anchor binary capture**~~ — **RESOLVED (owner decision 2026-07-20): a version
   pin, and capture only.** `binary_ref` holds `{"version", "identifier"}`, fetched
   from the shell via a new `shell.appBuildRef` call — never bytes, never a path (an
   earlier draft also carried the executable path; dropped, because nothing read it,
   it goes stale on any move or reinstall, and it would write the user's account name
   into a plaintext sidecar and into every permanent anchor). Copy-on-write was
   rejected: APFS `clonefile` is platform-specific, degrades silently to a full copy
   across filesystems and volumes, and would make an anchor's size depend on the
   user's disk layout — not something a floor should rest on. **Restoring a binary
   does not ship and is a Phase-3 updater item** — see the decision note in §3.3.

**Also resolved in step 1, though it was never listed here** — and it deserved to be,
because it was a larger threat to G3 than any question above. The engineering spec's
provisional snapshot DDL commented that `created_in_mode` "mirrors existing artifact
hiding". **That comment was overridden, not implemented.** Snapshots are recovery
machinery, not artifacts: hiding OPEN/Custom-created rows in SAFE would hide the way
back from precisely the user who most needs it — someone who weakened a guard in
Custom, broke something, switched to Simple, and now opens Restore points to an empty
list. The column ships **for display only** and never filters a list, restore, prune,
or delete query in any mode, held by a behavioural test *and* a source-level one that
fails if the column ever appears in a filter position.

---

## 14. Phasing — docs first, then code

**Phase 0 — this document** (done): the shared design, awaiting greenlight.

**Phase 1 — authoritative docs (no code).** On greenlight, update every doc that
matters to reflect the shift, *before touching code*:

- `CLAUDE.md` — identity, the G1/G2/G3 + anchor floors, the Simple/Developer/
  Custom model, harness/workspace-trust, automation keyword gate, routing, free
  models, the revised build order.
- `docs/architecture.md` — trust boundaries gain the snapshot subsystem and
  workspace-trust; the mode section gains Custom.
- `docs/data-model.md` — snapshot/anchor tables; provider-config/routing fields;
  known-working marking.
- `docs/flows.md` — new flows: snapshot + restore; make-it-cheaper orchestration;
  add-endpoint-by-prompt; workspace-trust grant; keyword-gated powerful action;
  routing/degrade-with-disclaimer.
- `docs/classes.md` — the snapshot manager, the routing strategies, the mode/
  guard model.

**Phase 2 — code (later, in dependency order).** Re-ordered per owner feedback,
safety floor first, then companion-facing, then the dev-harness track (which the
code-widget and MCP steps depend on):

1. **Snapshot/restore subsystem** (G3) — **SHIPPED 2026-07-20.** The floor
   everything else leans on; built and hardened *first*, with the single most
   important test being "restore always works, even from a broken config." Includes
   automatic + on-command snapshots and the app **build reference** recorded by Custom
   anchors. Two lines of this item did **not** ship in step 1, both deliberately and
   both with an owner decision behind them: **restoring a binary** (Phase-3 updater —
   §3.3), and **asking Addison for a snapshot in plain language** (step 2, as a LOW,
   capture-only tool — step 1 shipped the Settings control and the RPC method).
   `mint_anchor()` is fully implemented with no caller, because the Custom guard toggle
   that mints an anchor is step 2.
2. **Custom profile + guard model** (`policy.py`) + the undeletable-anchor rule.
3. **Routing strategies** (4 + custom) + companion prefer-quality/prefer-free
   toggle + free-model disclaimer + graceful fallback/cooldown.
4. **Free-model endpoints** — first-class legit free/local + add-by-prompt
   (shared plumbing with connecting an MCP server, step 7).
5. **Harness + workspace-trust** (OPEN) — the trust boundary the powerful
   capabilities below depend on.
6. **Widget capability tiers + expanded vocabulary** — safe interactive kinds
   (to-do/checklist, note, timer) with trusted renderers + safe storage
   (buildable in all modes); capability-tier gating so SAFE stays non-destructive
   and higher tiers add code-backed/system-capable widgets; make `primary.txt`
   capability-aware.
7. **MCP client integration** — external tools surfaced through the registry +
   gate, mode-scoped (OPEN under workspace-trust; SAFE read-only/undo-able only).
8. **Automation keyword gate** + author-OS-run automation.

Each code step remains independently testable and ships behind the same gate as
today. (Steps 3–4 are companion-facing and independent of the harness, so they
can proceed in parallel with 5–8 once 1–2 land.)

---

## 15. What this amendment does not do

- It does not repeal any existing safety invariant.
- It does not bundle, endorse, or ship any gray-area model source.
- It does not give Addison a scheduler (G2 holds).
- It does not put API keys anywhere new (G1 holds; snapshots exclude them, even
  the Custom anchor).
- It does not let SAFE-tier widgets do anything destructive to the machine or to
  Addison — SAFE widgets are buildable but **non-destructive by construction**
  (§8.4); code-backed / system-capable widgets are higher-tier only.
- It does not make Addison an MCP **server or gateway** — only an MCP **client**
  (§8.5).
- It does not turn the companion into a developer tool — Simple stays calm and
  plain-language; the harness, code-backed widgets, and unconstrained MCP tools
  are opt-in via Developer.
