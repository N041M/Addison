# Step 7 — MCP client

**Status: PROPOSED, not started (2026-08-06).** `ROADMAP.md` owns scheduling.
The spec's shape is §4.12; this plan turns it into a build order and names the
two decisions still open. Where this plan and §4.12 differ, §4.12's *banner* is
right that its details were never settled — that is what this document settles.

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

This is the one genuinely open question (§5). Everything else below is
transport-agnostic.

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

1. **Config only, no tools.** `mcp_servers` table (snapshot-CAPTURED — it is
   reversible config), add/remove/list RPC, secrets to the keychain per the
   provider-key pattern. `test_capture_scope_covers_every_schema_table` forces
   the capture decision to be explicit. Ships doing nothing, which is the point.
2. **Connect + discovery.** Speak MCP, list tools, register them namespaced and
   dev-only. Nothing is callable yet; the picker and the tools surface show what
   was found.
3. **Dispatch.** Invoke through the existing gate, with `tool_audit` on every
   outcome and a per-call deadline. **The deadline is not optional** — a hung
   server must not stall a turn, which is the same lesson `run_command` taught
   when it held the IPC pump for thirty seconds.
4. **Output handling.** MCP results are untrusted text heading for a model, so
   they cross the same redaction seam as command output.
5. *(Later, separately)* stdio transport under the seatbelt; SAFE admission via a
   user-promoted allowlist.

## 5. Decisions needed before phase 1

1. **Transport for v1 — HTTP only, stdio under the seatbelt, or stdio under a
   real containment environment?** *Recommendation: HTTP first.* It lets step 7
   land without opening an unsandboxed execution path, needs no new highest-trust
   surface, and keeps the client in the core where the module boundary already
   fits. The cost is real: many popular MCP servers are stdio, so HTTP-only is a
   smaller step 7 in practice.

   The two stdio paths, in order of weight:

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
2. **Where a server's tools appear in Developer.** The tools surface lists native
   tools today; MCP tools are the first that arrive from outside and can vanish
   when a server goes away. Same disabled-card treatment as step 6's artifacts,
   or a separate section?

## 6. What this step does NOT include

- No MCP **server** or gateway, in any phase.
- **No SAFE admission.** Deferred by owner decision; nothing here depends on it.
- No automatic trust of a server's declared risk or undo-ability. Ever, in v1.
- No keyword gate — that is step 8, and it governs *running* powerful actions.

## 7. The deferral this makes load-bearing

Untrusted-content screening (design-doc §11, v2) was re-affirmed as deferred on
2026-08-06 with a dated trigger. **MCP is its third trigger**, after free/gray-area
endpoints and the sandbox's deliberate outbound network. An MCP tool's output is
attacker-controlled text arriving in a model's context, and phase 4's redaction
pass is a credential backstop, not a screen. This does not block step 7 — it is
the thing to re-read when phase 3 lands.
