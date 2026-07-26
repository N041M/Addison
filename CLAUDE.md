# CLAUDE.md

Guidance for working in this repository. Read the specs before non-trivial
work — this file is the short version, they are authoritative:

- `docs/addison-design-doc.md` — product/UX rationale (the *why*)
- `docs/addison-engineering-spec.md` — build brief
- `docs/addison-scope-amendment-2026-07.md` — **the 2026-07-20 scope amendment:
  butler identity; Developer = coding harness / Simple = companion / new Custom
  profile; the guaranteed-rollback floor (G3); widgets buildable in all modes,
  capability-gated; MCP client; routing strategies; free / no-frontier models.
  Where it and the two specs differ, the amendment wins.**

## What this is

Addison is a local-first desktop **butler** — **approachable by default and
powerful on request** (scope amendment 2026-07-20). Its default audience is
non-technical users (personas "Mira", 54, and "Petr", 68 — design-doc §5), served
by the **Simple** profile as an all-in-one **companion**. Technical users get the
opt-in **Developer** profile: a **Claude-Code-class coding-agent harness** (real
project work — read/edit files, run builds/tests, iterate) with Addison's safety +
QoL layered on. A third **Custom** profile (deep in Settings, behind extra
confirmation) lets advanced users tune the *prompting* guards — never the floors.
A profile reshapes the *surface, capability tier, and prompting* — it **never**
removes a global floor (see invariants). Simple is the default; Developer/Custom
are opt-in. When adding a capability, ask which profile/tier surfaces it — do not
leak developer affordances into Simple.

Three processes, three trust levels (spec §1.3):

- **`shell/`** — Tauri 2.x (Rust). Highest trust: OS keychain, file picker,
  updater. Supervises the Agent Core; relays IPC. Never runs model instructions.
- **`agent_core/`** — Python 3.12. Orchestration loop, tool registry, permission
  gate, routines, SQLite. No OS permissions of its own — every filesystem/OS
  effect goes back through the shell via IPC.
- **`shell/src/`** — React + TS frontend. Lowest trust: renders state, never sees
  API keys, never touches the network directly.

Shell ↔ Core talk over **JSON-RPC 2.0 over stdio**.

## Safety model — rules (full text: [docs/SAFETY.md](docs/SAFETY.md))

**`docs/SAFETY.md` owns this topic.** What follows is the enforceable short form.
Do not restate the reasoning here or anywhere else — link. Correcting one floor
used to mean editing thirteen files.

**Two policy modes, derived 1:1 from the active Profile** (`agent_core/policy.py`,
`mode_for_profile`) — there is no separately-persisted mode:

- **Simple → SAFE.** Today's behaviour, byte-for-byte. All four SAFE invariants hold.
- **Developer → OPEN.** Real command execution exists (`run_command`, dev-only); a
  `dev_only` tool may register at HIGH without `undo()`; routines and widgets may
  carry a `command` step/kind; the gate auto-allows non-destructive actions and
  prompts ONLY for destructive ones. Fewer prompts, **not** no gate.
- **Custom → OPEN + a `GuardConfig` overlay.** Two settings-backed *prompting*
  guards, never floors. Weakening one mints the G4 anchor first, and refuses the
  change if the anchor cannot mint.

**Four GLOBAL floors, never relaxed in any mode. Flag a conflict, never work around it:**

- **G1** — API keys never reach the frontend/webview or SQLite. Keychain only, read
  at the moment of use. Do not touch this machinery.
- **G2** — Addison never triggers itself. It may *author* automation the OS runs;
  arming a powerful action needs a user-typed keyword prefix (designed, **not built**).
- **G3** — Guaranteed rollback. Snapshots (auto before risky changes, plus
  on-command) always allow a one-action restore to the last verified-working config.
  **Currently overclaimed in OPEN** — `run_command` can delete the floor's own
  files; see SAFETY.md and [step 5.5](docs/step-5.5-containment-plan.md).
- **G4** — Turning a guard OFF in Custom mints a permanent, undeletable snapshot
  anchor recording the app build. A build *reference*, never bytes; restoring a
  previous binary is not implemented (Phase-3 updater).

**Four SAFE-mode invariants (Simple profile — hold byte-for-byte):**

1. **No arbitrary code/shell execution.** No `eval`, no Lua sandbox, no raw-code
   field. OPEN's `run_command` and the two `open_only` file tools are absent from
   `registry.visible_tools(SAFE)` and refused at dispatch outside OPEN.
2. **Every `risk_tier != LOW` tool has a real `undo()`**, enforced at registration
   in `tools/registry.py`, which raises otherwise. Never satisfy this with a no-op:
   a tool that genuinely cannot be undone stays LOW and read-only. **This is the
   single most important test in the codebase.** The only exception is a
   `allow_missing_undo` registration, never in the SAFE view.
3. **A Routine never gets permissions beyond what the user granted live.** Same
   registry and gate instances as the live orchestrator, in both modes —
   `visible_tools(mode)` is a filtered view, never a second registry.
4. **Widgets are capability-gated, not code.** Buildable in every mode; the mode
   gates the capability. SAFE widgets come from a safe, non-destructive vocabulary
   and are non-destructive by construction.

**Two rules that are easy to get wrong:**

- **Artifact hiding** — routines/widgets created in OPEN are hidden and disabled in
  SAFE (`created_in_mode`), and return untouched when Developer is active again.
- **Snapshots are NEVER hidden by mode (C6).** `created_in_mode` ships on
  `config_snapshots` but is **display only**. No list, restore, prune or delete
  query may filter on it, in any mode. A source-level test enforces this.

## Module boundary rule (spec §2)

`agent_core/tools/`, `agent_core/providers/`, and `agent_core/routines/` **must
not import from each other**. They are all consumed by `orchestrator.py`, the
only module allowed to know about all three. This is what lets the Routine engine
replay tool calls through the exact same registry + gate as the live loop.

## Conventions

- **Python**: 3.12, stdlib-first. SQLite via `sqlite3`/SQLAlchemy Core, no heavy
  ORM. `httpx` for HTTPS. Ruff, line length 100. Dataclasses mirror the SQL
  schema 1:1.
- **Provider-agnostic orchestrator**: never branch on the concrete provider with
  `isinstance`. Handle capability differences via `ProviderCapabilities`.
- **Per-request model routing**: the orchestrator calls `model_router.resolve()`
  each turn — there is no single `self.active_provider`. Multiple roles
  (PRIMARY, LOCAL) can be configured and reachable at once (spec §4.1.1).
- **Plain language, no jargon** in any user-facing string (tool labels,
  permission cards, errors). No stack traces reach the user — errors become a
  plain message + one suggested next step.
- **UI (step 7+): distinct, non-AI look — the dark "correspondence-instrument"
  direction (v4, adopted 2026-07-26 from the owner's design handoff)**.
  **`docs/design-brief-dark/` is authoritative** — `README.md` + `prototype.html`
  are the designer's pixel-perfect reference, `IMPLEMENTATION.md` records the
  binding prototype→app mapping (demo content is never shipped; real features are
  restyled, never de-wired); the tokens in **shell/tailwind.config.js** implement
  it. The direction: a **calm, text-first dark UI** — near-black paper
  (`#0C0C0D`), hairline separators, one **violet accent** (`#B4A9F5`) reserved
  for actions, selection rails, and live state (never decoration), **system type
  only** ('Helvetica Neue' UI beside `ui-monospace` machine facts — the earlier
  bundled-font exception is retired, no @font-face). Shape rule: selection/active
  is a **2px accent left rail**; sections sit on 2px rules; hairline-row surfaces,
  with floating chrome (popovers/menus/modal, 5–8px radii) the only
  bordered-panel elements. Signature motion: the **character-scramble**
  (`shell/src/lib/scramble.ts`) + fadeRise/fadeDrop transitions, all no-ops under
  `prefers-reduced-motion`. **Dark is the designed reference; light is a derived
  translation** — the theme stays class-driven three-way (light/dark/system,
  default **system**, persisted as `addison.theme`). Still never generic-AI
  styling beyond the sanctioned flat accent (no gradients, glassmorphism,
  sparkle/bot icons, shimmer) and never a model vendor's branding. This
  **supersedes the Fern "warm correspondence" direction (v3,
  `docs/design-brief-fern`, kept as history)**, which superseded the dark
  terminal-adjacent look, which superseded design-doc §7.1's cool-slate palette;
  §7.1's layout/IA and accessibility rules are unchanged (personas 54 and 68
  still govern legibility).
- **IPC types are hand-synced**: keep `agent_core/protocol.py` and
  `shell/src/types/protocol.ts` in lockstep (codegen is Phase 3, not v1).

## Build order

**The v1 sequence (spec §11, steps 1–11) is complete and merged**: schema +
dataclasses, `ToolRegistry` + the undo check, the `PermissionGate`,
`AnthropicProvider` + `ModelRouter` + the orchestration loop, the remaining tools
and their `undo()`, the `UndoManager`, the Tauri shell + IPC, Routines, the Setup
Assistant relay, Ollama + the full router, and Profiles. **No file is marked
`TODO(step N)` any more** — that sequence now records the order the system was
built in, not work outstanding. The live sequence is the **Phase-2 order** below.

Also shipped past the numbered sequence: the UI wave, and
the **widget rail** — declarative routine/stat widgets
(`agent_core/widgets.py`, invariant 4) plus the `usage_log` token/latency
substrate (§4.8) that feeds the token meter + connections rows. The wave is
complete through its final PR: multi-provider API keys, the three-column
app shell + in-window Settings, widgets/tray, class-driven dark mode, the
**first-run block** (`FirstRunBanner.tsx` — setup steps, launch-only skip,
time-of-day greeting) with the favicon bundled from `shell/public/`, and a
both-themes QA pass (TESTING-CHECKLIST §13). *(That wave shipped under the Fern
direction; the surfaces were restyled to the dark v4 direction on 2026-07-26 —
the pine card, the serif voice, the bundled fonts and the bell mark are all
retired. The features themselves were restyled, never de-wired.)* Also shipped:
the **mode-scoped safety backend** (owner decision 2026-07-19, `agent_core/policy.py`)
— the SAFE/OPEN split derived 1:1 from the profile, `run_command` (dev-only),
mode-aware `ToolRegistry.visible_tools` + `PermissionGate.authorize`, routine/widget
`command` kinds + `created_in_mode` hiding — together with the frontend that goes
with it: Settings copy for the profiles and modes (honest about what OPEN relaxes
and the global floors that never do), the auto-grant/destructive-prompt UI, and the
`mode` field carried on `profile.get`/`profile.set`.

Profiles (step 11) also derive the policy mode (`policy.py`): Developer = OPEN mode
reshapes the visible tool set and the gate's prompting, but NEVER the global floors.
The permission gate is mode-aware (`authorize`), not profile-blind — the earlier
"never the permission gate" framing is superseded by the mode-scoped model above.

Also shipped alongside step 1: **`read_web_page`** (`agent_core/tools/read_web_page.py`)
— LOW, read-only, in the **Simple** tool set, because answering *from* a page rather
than handing over a link is the companion's core job. It is the first SAFE tool that
sends a request to an address the **model** picks, so every URL and every redirect hop
is vetted by **resolved IP** and the connection is **pinned** to the address that was
vetted (SSRF + DNS-rebinding closed). Outward reach is bounded by **visibility, not
per-site grants** (owner decision 2026-07-20): `permission_detail` names the site and
the Activity Panel shows it on every granted call, in both modes and on the routine
path as well. The grant is still per tool id, and the panel names the *requested*
host — both are tracked in `docs/HANDOFF.md`, not silently accepted.

**Scope amendment (2026-07-20) — Phase-2 build order**, after this doc pass and in
dependency order (amendment §14): (1) **DONE — the snapshot/restore subsystem**
(floor G3; `agent_core/snapshots/`, the `config_snapshots` table, the `snapshot.*`
RPC namespace, the auto-capture hooks (seven at step 1; nine sites today) + the
verified-working site, the sidecar
cold-start recovery path, and the Settings "Restore points" card. Its single most
important test, `test_restore_always_works_from_a_broken_config`, passes; the
subsystem is described above under the floors), (2) **DONE (2026-07-24) — the
Custom profile + guard model + the G4 anchor caller** (`policy.py`: Custom derives
OPEN with a `GuardConfig` overlay; two settings-backed prompting guards —
`guard_destructive_card` per_invocation>session, `guard_auto_grant_scope`
none>non_destructive>everything; weakening a guard mints the undeletable anchor
FIRST via `guards.set`, with fingerprint dedupe, and refuses the change if the
anchor cannot mint; a destructive "Ask once" approval lives in a dedicated
session set the SAFE path structurally never reads, and every profile switch
revokes all grants; guards are EFFECTIVE only under Custom — Simple/Developer
stay byte-for-byte), (3) **DONE (2026-07-24) — routing strategies**
(quality-first default / cost-first / local-only / Developer custom chain —
**Balanced cut from v1 by owner decision**, see amendment §10.1; the companion
sees one prefer-quality/prefer-free toggle; a structured provider exception
hierarchy in `providers/base.py` distinguishes unavailable from rejected/auth
so fallback never amplifies a bad request; fallback is per-send continuation
with cross-provider mid-turn advance forbidden in v1, a per-provider cooldown
and a real per-attempt deadline, all module constants; the FREEZE is the chain
head = the user's standing default, never overridden by rank; `local_only`
outranks even the explicit picker and is resolved BEFORE the Setup-Assistant
relay branch — no model call leaves the machine; `answeredWith` + the
"Answered with a free model." chip when `free && routed`; usage rows now carry
the RESOLVED per-attempt identity, fixing a pre-existing mis-attribution),
(4) **DONE (2026-07-24) — free-model endpoints** (add-an-endpoint-by-prompt +
"make it cheaper"; the whole pinned-request mechanism factored out of
`read_web_page` into `agent_core/net_vetting.py` so the `provider.connect`
validation GET adopts the same SSRF/rebinding defence instead of growing a weaker
copy — resolve → vet → connect to the vetted IP with the name in `Host` + TLS SNI
→ follow no redirects → re-vet every hop, with the vetting DECISION as a parameter
so the LAN endpoint policy and the public-web policy share one mechanism; both
flows are propose/confirm RPCs whose fields are **core-derived or canned**, never
model-authored; `costPlan.apply` REFUSES if its restore point cannot be minted and
persists skill + strategy in ONE atomic commit; the free chip stays Ollama-only —
**no cloud model ever claims free**, Google's free tier is information, not a
routing flag),
(5) **DONE (2026-07-24) — harness + workspace-trust** (OPEN; two typed,
path-bounded, OPEN-only file tools + a `workspace_trust` table and `workspace.*`
RPC. **CONFINEMENT is a predicate separate from prompting**: a path-bounded tool
whose resolved path is outside every trusted root is hard-refused *before*
`execute`, LOW and MEDIUM alike, which is permission-to-TOUCH; the gate's
`trusted` bool is only permission-to-skip-the-card. The path is resolved ONCE and
handed to `execute` via `ExecutionContext.resolved_path`, never re-read from
`args` — check one path, act on another is the TOCTOU gap. The registry's
`dev_only` split into `open_only` (visibility) + `allow_missing_undo` (the
exemption) so `write_project_file` is hidden from SAFE **and** undo-enforced at
registration. **Owner decision 2026-07-24: trust suppresses cards ONLY for the
typed, path-bounded, undoable file tools — `run_command` ALWAYS cards**, its
`affected_path` is None so confinement never governs it. Trust is **excluded from
snapshots** on the `tool_grants` precedent — see G3's "never captured" list.
Routine steps and command widgets pass `trusted=False` unconditionally), (6)
**widget capability tiers + expanded safe vocabulary** (to-do/checklist, note,
timer), (7) **MCP client** tools via the registry + gate, (8) the **automation
keyword gate** + author-OS-run automation.
**Steps 1–5 are built. What remains is 5.5, 6, 7 and 8** — 6 is companion-facing,
7 and 8 add capability through the existing registry + gate, and those three are
independent enough to be taken in any order.

**(5.5) — containment for the OPEN harness**
([docs/step-5.5-containment-plan.md](docs/step-5.5-containment-plan.md), proposed
2026-07-26). Not a new capability: it is step 5's unfinished half. Step 5 shipped
`run_command` and did **not** re-establish the property design-doc §9's first
mitigation ("capability allow-list, not a shell") was protecting — see the G3
scope correction above. Contents: move `run_command` behind the **ShellBridge**
(today it is the only tool that reaches the OS without crossing it, contra spec
§1.3, which is *why* it has no second enforcement layer); a **Seatbelt** profile
generated from the live `workspace_trust` roots, so trust finally bounds the shell
and not just the typed file tools; a **pre-gate denylist** that cannot be approved
away, checked at the same site as CONFINEMENT; **secret redaction** on tool output
before it reaches a provider; and a `tool_audit` table (**excluded** from
snapshots, on the `tool_grants` precedent). A sandbox is **not** a guard — it never
appears in the Custom panel and has no toggle, and this changes blast radius, never
prompting: `run_command` still always cards. **Step 7 is downstream of the audit
log and of closing amendment §13's MCP SAFE-constraint question** — §8.5 promises
MCP tools are "gated, logged, undo-aware" and there is no log, and a server's
self-declared "read-only" cannot admit a tool to SAFE without breaking invariant 2
through a path the registry check cannot see.

For *status* — what is built versus planned — [ROADMAP.md](ROADMAP.md) is the
single source; this section is the reasoning, and the two have drifted before.

## Multi-provider (owner decision 2026-07-18 — overrides spec §10 "Anthropic only")

OpenAI, Google (Gemini), and an OpenAI-compatible **custom server** are now v1,
alongside Anthropic. Keys are stored per **provider id** (`anthropic | openai |
google | custom`) in the OS keychain (Rust `store_provider_key`/`delete_provider_key`,
account = `provider-key:{provider}`; the legacy `provider-key:primary` Anthropic
entry auto-migrates on first read). The core reads a key via
`keychain.getProviderKey {provider}` at the moment of use only — keys never reach
the webview or SQLite (`provider.list`/`connect` responses carry status/metadata
ONLY). `provider.connect` validates with one tiny request (Anthropic: `GET /v1/models`;
OpenAI/custom: `GET {base}/v1/models`; Google: `GET /v1beta/models`), then folds the
provider's models into the single picker union. Non-secret connection metadata lives
in the `provider_config` table; the custom base URL is the ONE permitted `http://`
case (validated http(s)://). The orchestrator stays provider-agnostic — capability
differences via `ProviderCapabilities`, never `isinstance`.

**Routing & free models (scope amendment 2026-07-20).** Routing gains **three**
named strategies — quality-first (default; strong→weak degrade), cost-first and
local-only — plus a Developer custom chain. (The amendment drafted a fourth,
**balanced**; it was **CUT from v1** by owner decision, §10.1, because it was
provably identical to cost-first at two-model pools. `ROUTING_STRATEGIES` in
`providers/router.py` is the authority.) The companion sees a single
prefer-quality/prefer-free toggle. Strong-first with graceful fallback + provider
cooldown; a visible "answered with a free model" disclaimer when a free model
answers. Addison must be useful **without a paid frontier key** (local Ollama +
legitimate free cloud tiers); new endpoints are extensible and addable by prompting
Addison (reversible config, keys per G1). Gray-area aggregating routers
(OmniRoute/LiteLLM) are the user's own choice — documented on GitHub only, never
surfaced or endorsed in-app. **MCP is a *client* capability** (consume external
tools through the existing registry + gate; SAFE admits only read-only/undo-able
ones), never a server/gateway.

## Do NOT build yet (spec §10; reconciled with the 2026-07-20 amendment)

Still deferred: **fully-automatic task classification** for routing (the *choice
logic* that picks a strategy per task — v2; the four *named* strategies below ship
now), the Context Budget Manager / automatic long-conversation continuation (**v2**
— spec §4.8; v1 ships only the schema substrate, orchestrator machinery, never a
registry tool), messaging channels, Routine step-editing UI, a Rust rewrite of the
Agent Core, and the two v2 items from the 2026-07 ecosystem survey — Routine
export/import **sharing** and untrusted-content screening (design-doc §11) — do not
pull them forward. (Untrusted-content screening becomes load-bearing once
free/gray-area endpoints and MCP tools are in play — still v2.)

Also deferred, and specifically **not** to be solved inside the snapshot
subsystem: **restoring a previous app binary** (owner decision 2026-07-20 — a
**Phase-3 updater** item; G4 promises a config anchor that records its build, see
above). Building a downgrade path into the recovery floor would put a second,
uncoordinated binary-replacement mechanism on a collision course with
`updater.rs`, and it would be the one piece of the floor that could itself brick
the app. Of the step-1 deferrals, only **`tool_grants` capture** is still open (and
would then need to be an INTERSECT). The rest have LANDED and are no longer
deferrals: the `snapshot_now` tool and the anchor-minting caller
(`rpc/guards.py`), `_valid_http_url` credential hardening (`rpc/providers.py`),
and the permanent distrust of Addison's own data directory
(`policy.py::_protected_dirs`). The ledger with each one's reasoning is in
`docs/HANDOFF.md`.

**Pulled forward by the amendment** (build per the Phase-2 order above, not
opportunistically): the **named routing strategies** + custom, free/no-frontier
models + extensible endpoints, the **snapshot/rollback** subsystem (now built), the **Custom**
profile, the **coding harness + workspace-trust**, **capability-tiered widgets**,
the **MCP client**, and OS-authored automation behind the **keyword gate**.
Scheduling is still **not** Addison triggering itself (G2) — Addison authors, the
OS runs.

## Commands

```bash
# Agent Core (from agent_core/)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest ../tests/ -q          # safety-invariant tests must pass

# Shell (from shell/)
npm install
npm run tauri dev
```
