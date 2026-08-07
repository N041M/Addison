# Step 7 — MCP client

**Status: STARTED — phases 1, 2 and 3 of five are BUILT (2026-08-06, 2026-08-07,
2026-08-07).** `ROADMAP.md` owns scheduling. The spec's shape is §4.12; this plan
turns it into a build order and settles the decisions §4.12's banner admits were
never made. Where this plan and §4.12 differ, §4.12's *banner* is right that its
details were never settled — that is what this document settles.

**Both decisions are now ANSWERED** — see §5. Decision 1 (transport): HTTP only
for v1. Decision 2 (where a server's tools appear): a section per server on the
Tools surface, never interleaved with native tools. Phase 2 added three scoping
decisions of its own (§4.2) and phase 3 four more (§4.3).

Addison is a **client**: it consumes external MCP servers/tools. Never a server,
never a gateway (the OmniRoute-style thing, declined and still declined).

---

## 1. What is already decided

- **Dev-only for v1** (owner, 2026-08-06). MCP tools never enter the SAFE view.
  A user-promoted allowlist is the upgrade path, and it is a *later* decision
  with no code depending on it.
- **Through the existing registry and gate, never a side channel** (§4.12).
- **Reversible config** — a server connection is snapshotted, revocable, and
  addable by prompting, sharing the add-an-endpoint plumbing (§4.11).
- **The audit dependency is discharged.** `tool_audit` (step 5.5 item 4) is what
  made "gated, logged, undo-aware" satisfiable; MCP tools inherit it by going
  through the same dispatch.

The dev-only decision is what unblocked this step. The hard part was never the
client — it was admitting a stranger's self-declared risk into SAFE, and that
question is now deferred rather than answered, which costs nothing today.

## 2. The constraint that shapes everything

**The Agent Core has no OS permissions of its own** (spec §1.3). So the transport
decides the architecture, not the other way round:

- **Streamable HTTP** — no process, no OS effect. The core already speaks HTTPS
  to providers via `httpx`, so an HTTP MCP client lives in the core and needs
  **no new shell surface at all**.
- **stdio** — Addison launches an arbitrary executable and talks to its pipes.
  That is *arbitrary code execution by another name*: the thing SAFE invariant 1
  forbids outright, and in OPEN it would run **outside the seatbelt** that step
  5.5 built, unless it is routed through the shell the way `run_command` was.

This was the decision everything else waited on, and it is **answered: HTTP only
for v1** (§5). Everything else below is transport-agnostic.

## 3. Tool admission — how an MCP tool becomes callable

Every discovered tool registers with the flags the registry already has:

```python
registry.register(McpTool(...), dev_only=True)   # open_only + allow_missing_undo
```

- `open_only` keeps it out of `visible_tools(SAFE)` and refuses it at dispatch
  outside OPEN — SAFE invariant 1 and the dev-only decision, mechanically.
- `allow_missing_undo` is required and honest: an MCP tool has no `undo()`, and
  the exemption exists for exactly this shape (`run_command` is its only current
  holder). Invariant 2 is *not* weakened — a mutating tool with no undo simply
  cannot be LOW, so it can never reach SAFE whatever a server claims.
- **Tier: HIGH and destructive, unconditionally, in v1.** A server declares its
  own risk and that cannot be taken on trust, so every MCP call cards per
  invocation. Refining this is what the promoted-allowlist decision is *for*;
  guessing it now would be the trust hole in a different place.

**Namespaced ids are a safety requirement, not tidiness.** An MCP server can
declare a tool called `save_file`. Registered bare, it would shadow a native tool
id, and every existing grant, audit row and risk rule keyed by that id would
silently point at a stranger's code. Ids are therefore `mcp:<server>:<tool>`,
and registration must refuse a collision rather than replace.

## 4. Build order — each phase lands green and is independently useful

1. **Config only, no tools — BUILT 2026-08-06.** The `mcp_servers` table
   (snapshot-CAPTURED — it is reversible config), `mcp.list`/`add`/`remove`, and
   the URL check at the store boundary. Ships doing nothing, which is the point.
   What landed, and the two places it differs from the sketch above:
   - **No secret, and no half of one.** The keychain sentence in the original
     sketch was dropped rather than partly built: phase 1 connects to nothing, so
     a token has no consumer, no reader and no way to be validated. The door is
     left open exactly where the provider-key pattern already points — a token
     goes to the OS keychain through the shell when phase 2 needs one, never into
     SQLite and never back to the webview (G1). What phase 1 *does* enforce is the
     half that could not wait: a URL carrying a sign-in name, password, key-shaped
     path segment, query or fragment is refused at the door, because this table is
     captured and anything stored in it is copied into every later payload and
     sidecar in plain text. That check is `rpc/providers._base_url_problem`,
     called rather than re-derived, with ONE rule added on top — plain `http://`
     is narrowed from "the custom-server case at large" to "a server on this
     computer".
   - **The surface is Developer-only, and `add` is what enforces it.** `mcp.list`
     and `mcp.remove` answer in every profile: the rows are inert, so listing them
     grants nothing, and hiding somebody's saved configuration on a profile switch
     is the failure the 2026-08-06 artifact decision reversed
     ([SAFETY.md](SAFETY.md) owns that rule). A tightening must never be trapped
     either, so removal always works.
   `test_capture_scope_covers_every_schema_table` forced the capture decision to
   be explicit, as designed. Tests: `tests/test_mcp_servers.py`,
   `shell/src/__tests__/mcp.test.tsx`, plus the generated `mcp.list` payload
   fixture both sides share.
2. **Connect + discovery — BUILT 2026-08-07.** Speak MCP, list tools, register
   them namespaced and dev-only; the tools surface shows what was found. The claim
   this phase made, and the only one: **Addison could now SEE what a stranger's
   server offers and use none of it.** (Phase 3, later the same day, is what
   changed the second half — see below.)

   What shipped:
   - **`agent_core/mcp_client.py`** — a minimal Streamable HTTP client: POST
     `initialize` (offering a current protocolVersion, accepting the server's if
     older), echo the `Mcp-Session-Id` back on every later request, send
     `notifications/initialized`, then walk `tools/list` through `nextCursor`. An
     SSE answer is parsed for its single JSON-RPC event — a minimal parser, never a
     subscription. It reuses `net_vetting`'s resolve → vet → **pin** path rather
     than growing a weaker copy; that module gained `method`/`content` (MCP speaks
     POST) and `same_origin_only` (a hop off the endpoint's origin is refused, not
     followed). It is top-level for `net_vetting`'s own reason: an MCP tool is
     eventually consumed by all three of `tools/`, `providers/` and `routines/`, so
     the client may not live in one of them.
   - **`agent_core/mcp_catalog.py`** — admission and the in-memory catalog. Ids are
     `mcp:<server>:<tool>`, registration is `dev_only=True`, tier HIGH and
     destructive unconditionally (§3), and a collision **refuses** that tool and
     reports it.
   - **Two new registry dimensions**, both off by default so every native
     registration is unchanged: `removable` (only a discovered tool may ever be
     `unregister`ed — this is the first way anything has left the registry, and an
     unconditional version would be a supported route to deleting `save_file`'s
     undo-enforced registration at runtime) and `not_callable` (absent from
     `visible_tools` in EVERY mode, and refused at dispatch).
   - **`mcp.refresh {id}`**, Developer-only, on the worker thread — the
     `provider.connect` pattern, because a stranger's server must never hold the
     IPC pump the way `run_command` once did. `mcp.list` rows gained `status`,
     `checkedAt`, `toolCount`, `tools` and `error`; optional fields are omitted
     rather than sent as null.
   - **Surfaces.** The Settings panel gained a per-row status line and "Check now";
     the Tools surface gained a section per server (Decision 2 below).

   **Nothing was callable, mechanically, in two layers** — until phase 3 turned the
   one constant on (`mcp_catalog.MCP_TOOLS_ARE_CALLABLE`). Both layers remain, and
   both are what turning it back off operates through: (a) `visible_tools(mode)` is
   the list offered to the model, and a `not_callable` id is absent from it in every
   mode; (b) if a `tool_use` names one anyway, BOTH dispatch paths refuse before the
   gate and before any network is touched, in the same shape as
   `refuse_if_dev_only_outside_open`. Nothing sets `not_callable` today; it is the
   shape the next discovered-before-its-dispatch-exists tool inherits.

   **Everything a server sends is untrusted text**, and the caps are at the client
   boundary rather than at each surface: a bounded number of tools per server and
   pages per walk, a bounded response body, a name that must already look like an
   identifier (skipped **and counted** otherwise — a repaired name is an id the
   server never offered), control characters stripped from every string, and a
   truncated description. A server's own error text is never shown; every failure
   is one of `mcp_client`'s plain sentences.

   Tests: `tests/test_mcp_discovery.py` (the protocol against
   `httpx.MockTransport`, admission, both not-callable layers, the RPC surface, and
   the import-graph guard), `shell/src/__tests__/mcp.test.tsx`, plus the shared
   `mcp.list` fixture, now carrying one row per discovery state.

   **Three scoping decisions, made 2026-08-07:**

   - **No tokens or auth in this phase.** Phase 1 left the keychain door open "when
     phase 2 needs one"; phase 2 as built does not. Connecting without credentials
     covers the local-server case an HTTP-only v1 already optimises for, so a 401
     or 403 gets one plain sentence — *"This server asks for a sign-in Addison
     doesn't support yet."* — and there is no token column, no header field and no
     keychain write. G1's surface is untouched, and the door stays documented for
     the moment a consumer exists.
   - **Discovery is ON DEMAND only.** No auto-connect at core start, no background
     refresh, no timer: a saved row stays inert until somebody asks ("Check now" /
     `mcp.refresh`). Addison makes no network request the person did not just
     cause — the same temperament as reversible config.
   - **The discovered catalog is held IN MEMORY, never persisted.** A server's
     catalog is the server's truth, not Addison's configuration, and `mcp_servers`
     is snapshot-CAPTURED — writing a stranger's names and prose there would copy
     attacker-controlled text into every later snapshot payload and plaintext
     sidecar, forever, to answer a question only the server can answer honestly.
     After a core restart the panel says the server has not been checked yet,
     because it has not.
3. **Dispatch — BUILT 2026-08-07.** Invoke through the existing gate, with
   `tool_audit` on every outcome and a per-call deadline. **The deadline is not
   optional** — a hung server must not stall a turn, which is the same lesson
   `run_command` taught when it held the IPC pump for thirty seconds. The claim
   this phase makes: **an MCP tool is invoked through the ordinary gate, its answer
   is redacted and capped before a model sees it, every outcome leaves a durable
   row, and a hung server costs a wait rather than a turn.**

   What shipped:
   - **`mcp_client.call_tool`** — `tools/call` over the same session, vetting and
     cap machinery discovery uses (same-origin pinning, `Mcp-Session-Id` echo,
     SSE-or-JSON answers, bounded response body). Only `content` items of type
     `text` are read; an answer with nothing textual in it becomes one plain
     sentence rather than an empty string, and a malformed answer fails closed.
     `isError: true` is believed — it says nothing about permission, only whether
     the thing the model asked for worked — and produces a failed result whose text
     still crosses redaction and the cap.
   - **`DiscoveredTool.schema`** — the server's `inputSchema`, admitted only within
     bounds (must BE a `type: object` schema, ≤16 KB serialized, ≤8 levels deep,
     checked iteratively) and replaced by `{"type":"object","properties":{}}`
     otherwise, **with the tool still admitted**: a bad schema costs the model its
     hints, never the person the tool.
   - **`McpTool.execute`** — the call, through the existing registry and gate with
     no special case at either dispatch site. HIGH + destructive still means a card
     per invocation in OPEN; SAFE still refuses above the gate. The permission
     card's description carries the provenance — *"This comes from the tool server
     X, which you added. Addison can't know what it will do, so it asks every
     time."* — which is also why the tool declares no `permission_detail`: a detail
     REPLACES the description on the card, and that sentence may not be replaceable.
   - **`tool_audit` on every outcome, in BOTH dispatch paths**, and the vocabulary
     migration that made it possible: `outcome` gained `not_callable` (retiring the
     KNOWN-GAPS entry phase 2 opened) and `failed` (the gate said yes and the call
     never landed — a different history from "approved, and it ran", and the only
     place anyone can learn which it was). SQLite cannot ALTER a CHECK, so
     `Store._migrate_tool_audit_outcomes` rebuilds the table by rename-copy-drop,
     preserving every existing row.
   - **Surfaces.** The per-tool line on the Tools surface and the Settings panel's
     standing line both changed from *"Addison can't use these"* to what protects
     the person now that it can: **it asks first, every time.** Simple renders no
     tool-server section at all, which is unchanged and is the honest answer — the
     tools are Developer-only.

   **Four decisions, made 2026-08-07:**

   - **Results cross the redaction seam NOW, a deliberate half-pull of phase 4.**
     The seam was scheduled for phase 4, but shipping dispatch without it would put
     credential-bearing server output in front of a model between two merges, and
     `tool_audit.redacted` already existed expecting it. Every result's text is
     redacted, the kinds ride back on the `ToolResult` so both dispatch paths can
     record them, and phase 4 keeps the REST of output handling: content-type
     breadth, structured content, and size/shape policy toward the model beyond
     this phase's flat cap. §7's trigger came with the seam and was re-read here
     rather than deferred to phase 4 — server text reaches a model from the moment
     dispatch exists, not from the moment output handling is finished.
   - **A result is capped on its way to the model.** A server can return megabytes.
     `MAX_RESULT_CHARS` (8000 — twice `run_command`'s, because a command's output
     is incidental to what was asked and a tool's answer IS what was asked) with a
     plain marker when it bites. **Redaction runs BEFORE the cut**, on
     `run_command`'s hard-won precedent: every redaction rule is anchored on a
     vendor prefix plus a minimum body, so a cut through a credential leaves a head
     that matches nothing afterwards and travels intact.
   - **One call, one session, one budget.** A fresh initialize → initialized → call
     inside a single ~15s deadline, with strict socket timeouts inside it. No
     long-lived connections, no background sessions, no reuse across calls — a
     session id is one server's handle on one person's wait, and a pool of them
     would be state outliving the turn that authorised it, held open to a program
     nobody here has audited. The cost is two extra round trips per call, and it
     was accepted. A server needing state ACROSS calls is a v2 conversation.
   - **Auth stays unsupported.** A 401/403 during dispatch answers with the same
     plain sentence discovery uses. Two sentences for one situation would have a
     person hunting for a difference that does not exist.

   Tests: `tests/test_mcp_dispatch.py` (the call, the answer, the gate, every audit
   outcome in both dispatch paths, the migration, and the address resolved at the
   moment of use), plus the phase-2 layer tests updated in place per the notes their
   authors left — `tests/test_mcp_discovery.py` now asserts an `mcp:` id IS offered
   to the model in OPEN and still never in SAFE.
4. **Output handling.** What phase 3 did not pull forward: content-type breadth
   (images, embedded resources), `structuredContent`, the size and shape policy
   toward the model beyond phase 3's flat cap, and the §7 re-read below. The
   redaction seam itself moved to phase 3 — see its decision 1.
5. *(Later, separately)* stdio transport under containment (§5 keeps both paths);
   SAFE admission via a user-promoted allowlist.

## 5. Decisions

1. **Transport for v1 — ANSWERED 2026-08-06 (owner): HTTP ONLY.** Streamable
   HTTP, no stdio.

   The reasoning, because it is what keeps the rest of the design where it is:
   the core already speaks HTTPS to providers through `httpx`, so an HTTP MCP
   client lives in the Agent Core and needs **no new shell surface at all** —
   nothing has to be added to the highest-trust process. stdio would mean the
   core launching an arbitrary executable, and the core has no OS permissions of
   its own (spec §1.3): the process would run **outside** the seatbelt step 5.5
   built, which is precisely the boundary that step exists to hold. So a server
   row stores a **URL and never a command**, and nothing in phase 1 can spawn a
   process — enforced in the schema (`transport CHECK`, no command column) and in
   the import graph of `agent_core/rpc/mcp.py`, both mutation-proven.

   The cost is real and was accepted: many popular MCP servers are stdio, so
   HTTP-only is a smaller step 7 in practice.

   **stdio is not rejected, it is scheduled** — the two paths below stay the
   documented later option (phase 5), in order of weight:

   - **Reuse `exec.rs`'s seatbelt profile.** Cheapest, and it inherits a boundary
     that is already mutation-proven. The mismatch is lifecycle: that profile was
     built for ONE-SHOT commands, and a long-lived server needs drains, process
     groups and restart rethought — the setsid-orphan and pump-stall bugs are
     both from that machinery and both were about a process outliving its call.
   - **A containment environment (VM or equivalent).** Heavier, and it earns its
     weight for a reason the seatbelt does not cover: an MCP server is *foreign
     code whose author you do not trust and whose behaviour you cannot audit*,
     which is a different problem from confining a command the person just
     approved. Note the honest framing this produces — the objection to stdio
     stops being "it is unsafe" and becomes "it needs containment we have not
     built", which is a scheduling answer rather than a safety one.

   **Not to be confused with running commands in a VM to preview their effects.**
   That is a different proposal, it is rejected, and `ROADMAP.md` records why
   (a side-effecting command would run twice, and outbound network is granted).
   Containment here isolates code; it never predicts what code will do.
2. **Where a server's tools appear in Developer — ANSWERED 2026-08-07: a SEPARATE
   SECTION PER SERVER on the Tools surface, never interleaved with native tools.**

   The reasoning, because it is what the surface has to keep true:

   - **Namespacing exists because a foreign tool must never be mistaken for a
     native one** (§3), and the UI has to draw the same line the registry draws. A
     section per server is where that line is visible; a row in the native list is
     where it disappears.
   - **A section carries provenance and server-level state in ONE place** — "from
     your tool server X", plus never checked / unreachable / N tools found. Those
     are facts about the *server*, and there is nowhere to put them if its tools
     are scattered.
   - **Step 6's disabled-card treatment answers a different sentence.** It says
     "a thing you made that your profile can't use". An MCP tool is "a stranger's
     tool your server offered", which is not the same thing at all — and borrowed
     here it would spread one server going offline across the native list as
     unexplained dead rows, with nowhere to name the server that went away.

   Every row in a section says, in plain words, what protects the person. Until
   phase 3 that was *"Addison can see this tool but can't use it yet"* — the same
   sentence the core answered with if anything named one. Since dispatch shipped it
   is *"Addison asks you before each use."*, which is the sentence that carries the
   weight now: these tools CAN run, and none of them runs without the person saying
   so. Names and descriptions render as plain text through React's own escaping; a
   server's prose is never put through the markdown renderer the thread uses.

## 6. What this step does NOT include

- No MCP **server** or gateway, in any phase.
- **No SAFE admission.** Deferred by owner decision; nothing here depends on it.
- No automatic trust of a server's declared risk or undo-ability. Ever, in v1.
- No keyword gate — that is step 8, and it governs *running* powerful actions.

## 7. The deferral this makes load-bearing

Untrusted-content screening (design-doc §11, v2) was re-affirmed as deferred on
2026-08-06 with a dated trigger. **MCP is its third trigger**, after free/gray-area
endpoints and the sandbox's deliberate outbound network. An MCP tool's output is
attacker-controlled text arriving in a model's context, and redaction is a
credential backstop, not a screen.

**PHASE 3 PULLED THAT TRIGGER, and the re-read happened here (2026-08-07).** A
server's text now reaches a model in three places it did not before: the tool
DESCRIPTION and its bounded `inputSchema`, both sent as tool definitions the moment
an `mcp:` id enters `visible_tools(OPEN)`, and the tool's ANSWER, which is the
whole point of a call. **Screening remains v2** — that decision is unchanged and
this phase does not reopen it. What phase 3 records instead is the backstop it
actually shipped, stated at its real strength and no higher:

- **Redaction** (`agent_core/redaction.py`) removes the credential shapes somebody
  has enumerated, naming each one in the text and in the audit row. It is a pattern
  matcher: a secret in a format nobody has listed passes untouched. It reduces
  exposure and does not eliminate it, and no document may say otherwise.
- **Caps** bound what a single answer can do to a context: 512 KB at the wire,
  8000 characters toward the model, 16 KB per schema, 100 tools per server.
- **The gate** is the layer that actually holds: every call cards, per invocation,
  in OPEN only. An injected instruction that persuades a model to call a tool
  server still has to persuade the person reading the card.

None of that screens for a prompt injection, and pretending otherwise is the
failure mode this section exists to prevent. What has changed since 2026-08-06 is
that the deferral is now genuinely load-bearing rather than theoretical: it is on
the v2 list in CLAUDE.md and [KNOWN-GAPS.md](KNOWN-GAPS.md), with three triggers
behind it and the third one live.

**For the record, since it was checked rather than assumed at the time:** phase 2
did not pull this trigger. A server's names and descriptions were attacker-
controlled text that reached a *person* and never a model — an `mcp:` id was absent
from `visible_tools(mode)` in every mode, so no tool definition Addison sent to a
provider carried a word a server wrote. The bounds phase 2 applied (the caps, the
identifier rule, the control-character strip) were there because that text reached
a SCREEN, which is a smaller problem with a smaller answer.
