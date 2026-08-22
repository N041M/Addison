# Messaging channels: controlling Addison from a phone

**Status: DECIDED AND SCHEDULED 2026-08-22.** Proposed and answered the same day:
the owner took all eleven decisions in §5 (each records its answer beside the
recommendation), messaging channels left [`CLAUDE.md`](../CLAUDE.md)'s
*"Do NOT build yet"* list, and the build order is **phases 1–3 as written**, with
phase 4 **deferred** — the owner's horizon for approving actions from a phone is a
bespoke phone app (§5, decisions 2–3), not the reflected card. Two answers added
scope this document did not propose, and each is recorded where it can be held:
a **menu-bar popup chat window with background operation** (decision 4 — approved
in direction, and it gets its own design section here before anything builds it),
and a **queue-or-decline setting** for messages that arrived while the Mac slept
(decision 8). [`ROADMAP.md`](../ROADMAP.md) owns status from here.

The ask it answers, in the owner's words: OpenClaw-style control of Addison from a
phone. WhatsApp was named. The design is **transport-plural by construction** — a
second channel is a new adapter, not a new subsystem — because the platform question
and the safety question are separable and only the second one is hard.

Its origin is design-doc §7.10 (*Messaging Channel Integration*), which scoped the
subject in 2026-07 and got four things right that this plan keeps: inbound text is
untrusted; pairing is required before any channel ships; Telegram's Bot API is the
low-friction first target; and the higher-risk tool tiers stay desk-only. **It also
made one assumption this plan rejects**, and the rejection is the reason this design
is buildable now rather than after a backend: §7.10 says an always-on requirement
"pushes the managed-proxy backend from *nice* to *required infrastructure*", and
design-doc §11's Phase-5 entry builds messaging "on the managed-proxy backend already
running since Phase 2." **There is no managed proxy in this tree and this plan needs
none.** The cost of not having one is a real constraint, stated at full strength in
§6: v1 answers your phone while your Mac is awake and Addison is running, and not
otherwise. That is a smaller promise than §7.10 imagined and it is one the local-first
architecture can actually keep.

Two halves, because the ask had two: **§3 is the complete API surface** — every
module, class, function, RPC method, table and IPC command, with what each one does.
**§4 is the build order.** §5 is what the owner has to decide, §6 what this costs
even when it works.

Everything in §3 marked *(proposed)* is a name this document is inventing. Everything
in backticks without that marker is a name that is in the tree today and was read
before it was written down here.

---

## 1. The floors, first

Nothing below moves a floor. Stated one line each, because a design that touches
the network, an outside server, a person's identity and the tool registry in the
same feature is exactly the design that should say so before it says anything else.
[`SAFETY.md`](SAFETY.md) owns all four.

- **G1 — a channel token never reaches the webview or SQLite.** It goes to the OS
  keychain through the shell, under its own account namespace, and the core reads it
  at the moment of use. §3.9.
- **G2 — Addison never triggers itself, and never starts a conversation.** Every
  remote turn traces to a message a paired human sent. Addison sends no first
  message, no notification, no nudge, ever. Nothing here hands a callback to a clock;
  the service thread waits on a network read for a person's words and originates no
  work of its own. §3.4 states the argument at length, because this is the floor the
  feature comes nearest to.
- **G3 — the channel's configuration is reversible config** and is snapshot-captured
  like a provider endpoint or an MCP server row. **A pairing is not configuration**
  and is deliberately excluded from capture, so a restore can never put back an
  authorization somebody revoked. §3.8.
- **G4 — nothing here is a guard**, so nothing here mints an anchor. There is no
  Custom dial for the remote floor, for the reason step 8 gave for the arming code
  ([`step-8-automation-plan.md`](step-8-automation-plan.md) §5.9): landing a
  capability does not oblige a dial in the same step.

And the SAFE invariants, in the same shape:

1. **No arbitrary code or shell execution.** `run_command`, the three automation
   tools and every tool discovered from an outside tool server are `open_only` and
   already absent from `visible_tools(SAFE)`. The remote view (§3.6) is a **subset
   of the SAFE view in every mode**, which is a strictly stronger statement, and it
   is asserted by a test rather than by prose.
2. **Every `risk_tier != LOW` tool has a real `undo()`.** Untouched. The remote view
   admits only `RiskTier.LOW` ids, so the question does not arise there, and nothing
   in this plan registers a tool at all.
3. **A remote turn never gets permissions beyond what the person granted live.**
   Same `ToolRegistry` instance, same `PermissionGate` instance, same
   `orchestrator.run_turn`. `registry.remote_tools(mode)` *(proposed)* is a filtered
   view over the same registry, never a second one — the invariant-3 shape, applied
   to a second surface instead of a second caller.
4. **Widgets** are not in this story.

---

## 2. The constraints that shape everything

Four properties of this tree decided most of the design. Three were expected. The
fourth was not, and it is the reason §3.4 looks the way it does.

**a. The Agent Core has no OS permissions of its own** (spec §1.3), and the seatbelt
grants outbound network and nothing inbound. So: **outbound only. No listener, no
inbound port, no webhook, no tunnel.** This is the MCP stance in a different costume
— *"a server row holds a URL and never a command"*
([`step-7-mcp-plan.md`](step-7-mcp-plan.md) §5) — and it is what lets the whole
transport live in the core beside `httpx`, needing **no new shell surface at all**
except the keychain pair in §3.9. Telegram's Bot API is chosen first because it has
an outbound-only mode: `getUpdates` long-polling, where the client asks and the
server holds the request open until there is something to say. A webhook would need
a listener, a public address and a certificate, and it is not proposed in any phase.

**b. The worker thread is the only SQLite thread.** `JsonRpcServer`'s docstring
(`agent_core/main.py`) says it plainly: the read loop parses one frame per line and
dispatches; a single worker thread (`_worker_loop`) runs turns one at a time; **all
SQLite access is confined to the worker thread.** A method opts off the read loop by
being registered through `enqueue(kind)` in `_build_dispatch_table`, which puts
`(kind, params, request_id)` on `self._queue`. `mcp.refresh` is there for exactly
this reason, and the comment beside `_MCP_JOBS` names it: a connect-and-walk on the
read loop *"would hold the IPC pump for as long as a stranger's server felt like
taking — the run_command stall, with somebody else's hand on the clock."* The two
existing thread-per-request handlers (`workspace.pickDirectory`'s `folder-picker`,
`model.startLocalSetup`'s `local-setup`) are legal only because they are store-free,
and `main.py` says so at the code. **So the channel service polls on its own thread
and touches no store; it hands each inbound message to the worker as a job, and the
turn runs where every other turn runs.**

**c. The server holds exactly one active conversation.** `self.conversation` is a
single `Conversation`, swapped wholesale by `conversation.new` / `conversation.load`,
with a 1:1 alignment invariant against `_message_ids` that rewind depends on. **This
is the constraint that broke the obvious design.** "Remote turns land in their own
conversation" cannot mean *switch the active conversation and switch it back* — that
would change what is on the person's screen, and it would race the alignment
invariant. What makes it work is a fact about the orchestrator's signature:
`Orchestrator.run_turn(conversation, requested_role=None, model_name=None,
effort=None, mode=PolicyMode.SAFE)` takes a **`Conversation` object, not an id**. So
a remote turn can be run against a `Conversation` the channel job owns, on the same
orchestrator, without `self.conversation` ever moving. §3.4 and §3.5.

**d. A permission card blocks forever.** `_ask_once` registers a waiter and calls
`event.wait()` with **no timeout**; the only things that resolve it are
`permission.respond` and `conversation.stop`, both from the read loop. A remote turn
that raises a card with nobody at the desk would park the worker thread — and with
it every desktop turn, every store read, the whole queue. **This is the single most
important consequence in the document**, and it is why the v1 remote floor is defined
as *the set of calls that cannot reach a card* rather than as *the set of calls that
are not very dangerous*. §3.6.

---

## 3. Part one: the surface

### 3.1 Module layout, and where it sits under the boundary rule

```
agent_core/channels/__init__.py       (proposed)
agent_core/channels/adapter.py        (proposed)  the protocol + the dataclasses
agent_core/channels/telegram.py       (proposed)  the first adapter
agent_core/channel_service.py         (proposed)  the poll loop and the hand-off
agent_core/channel_pairing.py         (proposed)  pairing state over automation_nonce
agent_core/rpc/channels.py            (proposed)  ChannelsMixin
```

**The boundary rule** (spec §2, [`CLAUDE.md`](../CLAUDE.md)): `agent_core/tools/`,
`agent_core/providers/` and `agent_core/routines/` must not import from each other;
`orchestrator.py` and the outer `JsonRpcServer` are the only modules allowed to know
about all three. `channels/` is **a fourth sibling and imports none of the three.**
It knows `httpx`, `agent_core/screening.py` and its own dataclasses. That is the same
placement `agent_core/mcp_client.py` took and for the same stated reason: a thing
that is eventually consumed by all three may not live inside one of them.

`channel_service.py` is **top-level and is constructed in `main.py`**, beside the
orchestrator and the registry, and is handed the orchestrator it will use. That
placement is not a convenience: architecture.md already says the outer
`JsonRpcServer` is what wires everything, and the service is a second caller of a
turn, which makes it exactly the kind of thing that belongs where the wiring is.
`tests/test_module_boundaries.py` gains the assertion that `channels/` imports none
of the three sibling packages.

### 3.2 `agent_core/channels/adapter.py` *(proposed)* — the transport contract

Everything transport-specific is behind this file. Nothing above it knows the word
Telegram.

```python
@dataclass(frozen=True)
class InboundMessage:
    channel_id: str      # the channels row this arrived on
    chat_id: str         # the transport's id for the conversation
    sender_id: str       # the transport's id for the human; what pairing binds
    sender_label: str    # a display name, for the paired-devices list. Untrusted text.
    text: str            # what they typed. Untrusted text.
    received_at: int     # unix seconds, Addison's clock, not the transport's
    update_id: str       # the transport's own cursor for this message
```

**`sender_label` and `text` are attacker-controlled**, and are treated the way a
tool server's names and prose are: control characters stripped, length capped, never
put through the markdown renderer, and — for `text` — screened before a model sees
it (§3.5).

```python
@dataclass(frozen=True)
class ChannelLimits:
    max_message_chars: int      # what one outbound message may carry
    max_poll_seconds: int       # the longest a single poll may be held open
    supports_typing_hint: bool  # whether the transport has a "working on it" signal
```

Limits are **the adapter's answer, not a constant in the service**, because the
splitting rule above it (§3.3) has to be right for whatever transport is underneath.

```python
class ChannelAdapter(Protocol):
    kind: str                       # "telegram"; matches the channels.kind CHECK
    limits: ChannelLimits

    def verify_token(self, token: str) -> VerifiedIdentity: ...
    def poll(self, token: str, cursor: str | None, seconds: int) -> PollResult: ...
    def send(self, token: str, chat_id: str, text: str) -> str: ...
    def working_hint(self, token: str, chat_id: str) -> None: ...
```

A `Protocol`, not a base class, matching how `Tool` and `ShellBridge` are declared in
`agent_core/tools/base.py`. Each method, and what it promises:

- **`verify_token(token) -> VerifiedIdentity`.** One small request that both
  validates the credential and returns the identity behind it — **`provider.connect`'s
  rule exactly**: *that request's reply is the answer*, one call doing both jobs. This
  is the pattern whose absence made a connected Google key offer two models and answer
  `404` to every message ([`CLAUDE.md`](../CLAUDE.md), multi-provider), so it is
  written into the contract rather than left to each adapter. Returns the bot's own
  display name so the Settings row can say *which* bot is connected. Raises
  `ChannelAuthFailed` on a rejected token and `ChannelUnavailable` on anything else;
  never returns a bare bool.
- **`poll(token, cursor, seconds) -> PollResult`.** Ask the transport for anything
  new, holding the request open up to `seconds` (bounded by
  `limits.max_poll_seconds`), and return `PollResult(messages, next_cursor,
  dropped)`. It **must return an empty list rather than raise on an idle window** —
  "nothing happened" is the ordinary case and an exception is not how the ordinary
  case is spelled. `dropped` counts messages the adapter refused for shape (no text,
  a media-only message, an oversized body), and is a floor and not a total, the same
  honesty rule MCP's `skipped` keeps.
- **`send(token, chat_id, text) -> str`.** Deliver one message, return the
  transport's id for it. The **caller** has already split `text` to
  `limits.max_message_chars`; `send` refuses an oversized string rather than
  truncating one, because a silent cut in the transport layer is a cut nobody can
  report.
- **`working_hint(token, chat_id)`.** Best-effort "Addison is thinking" signal where
  the transport has one; a no-op where it does not. It may never raise: a failed
  courtesy must not fail a turn.

**Failure vocabulary**, one exception each, all raised by adapters and caught by the
service: `ChannelAuthFailed` (the token is wrong or was revoked — the channel stops
and says so), `ChannelUnavailable` (network, timeout, 5xx — backoff and retry),
`ChannelRefused` (the transport said no to this specific send — surfaced once, never
retried in a loop). Every one carries a plain sentence Addison wrote; **a transport's
own error text is never shown**, which is `mcp_client`'s rule and is here for the
same reason.

**Backoff is the adapter's, not the provider machinery's.** `agent_core/providers/`
has `request_with_retry` (at most one retry, no backoff) and the orchestrator holds
`_cooldowns` per provider id. Neither is reused: a channel is not a model provider,
and sharing the cooldown map would let a Telegram outage cool down an Anthropic key.
An adapter carries its own bounded, growing backoff over consecutive
`ChannelUnavailable`s, resets it on the first good poll, and reports the current
state through `channel.status` so a person can see the difference between *quiet*
and *broken*.

### 3.3 `agent_core/channels/telegram.py` *(proposed)* — `TelegramAdapter`

The only file in the design that knows a vendor's API shape.

- **`kind = "telegram"`**, `limits = ChannelLimits(max_message_chars=…,
  max_poll_seconds=…, supports_typing_hint=True)`. The two numbers are read from
  Telegram's published limits at build time and written down at the code with a link,
  not guessed here: a number in a design document is a claim somebody will maintain
  badly, and [`CONVENTIONS.md`](CONVENTIONS.md) owns that rule.
- **`verify_token`** → `getMe`. The reply carries the bot's username, which becomes
  the "connected as" line.
- **`poll`** → `getUpdates` with `offset` and `timeout`, filtered to message updates
  carrying text. **The offset is the acknowledgement**: Telegram drops an update once
  you ask past it, so the cursor advances only after the messages have been handed to
  the service, never before. The consequence is stated rather than hidden: delivery is
  **at-least-once**, and a crash between hand-off and the next poll re-delivers. On a
  read-only remote floor a duplicated turn costs a duplicated answer; the moment
  anything with an effect joins the floor, deduplication by `update_id` becomes a
  requirement rather than a nicety, and §5 asks for it as a decision.
- **`send`** → `sendMessage`. Long answers are split by the service, not here.
- **`working_hint`** → `sendChatAction` with the typing action, re-issued on the
  cadence Telegram documents for it while a turn runs.

**Streaming shape, decided: there is none, and that is the design.** The desktop
streams because a person is watching it happen. A phone is where you read an answer.
An edit-per-delta design (`editMessageText` on a timer) spends a rate budget nobody
here can measure, produces a message that rewrites itself under the reader's thumb,
and adds a failure mode (an edit rejected mid-answer) with no good recovery. So: the
typing hint while the turn runs, then **one message when the turn is done**, split at
`limits.max_message_chars` on a paragraph break, then a line break, then a hard cut,
with a plain continuation marker on every part but the last. Edit-based streaming is
a named later option and nothing in the design depends on its absence.

**WhatsApp, plainly.** There is no official personal-account API. The two ways people
do it are Meta's **Business API** (a verified business, per-message cost, an approved
template regime that fits a butler badly) and **unofficial bridges** against the
personal client, which are against WhatsApp's terms and get numbers banned. The
project already has a settled posture for exactly this shape of thing, and it is
reused verbatim rather than re-argued: gray-area services are **the person's own
choice, documented on GitHub only, never surfaced or endorsed in-app** — the
OmniRoute/LiteLLM stance ([`CLAUDE.md`](../CLAUDE.md), multi-provider). A
`WhatsAppBusinessAdapter` is a legitimate later adapter if the owner wants to pay for
one; a bridge adapter is not shipped, not listed, and not linked from any surface.
The adapter protocol is what makes either of those a file rather than a project.

### 3.4 `agent_core/channel_service.py` *(proposed)* — `ChannelService`

The service owns one thread per enabled channel and no state that matters.

```python
class ChannelService:
    def __init__(self, adapters, token_for, enqueue_turn, notify, screen_text): ...
    def start(self, channel_id: str) -> None: ...
    def stop(self, channel_id: str) -> None: ...
    def stop_all(self) -> None: ...
    def status(self, channel_id: str) -> ChannelStatus: ...
    def deliver(self, channel_id: str, chat_id: str, text: str) -> None: ...
    def _poll_loop(self, channel_id: str) -> None: ...
```

Everything it needs is **injected**, which is what keeps it store-free and testable
against a fake transport: `token_for(channel_id)` reaches the keychain through the
bridge, `enqueue_turn(job)` is `main.py`'s queue, `notify(method, params)` is
`_notify`, `screen_text` is `agent_core/screening.py`'s `screen`.

- **`start(channel_id)`** starts one daemon thread, `name=f"channel-{kind}"`,
  `target=self._poll_loop`. **It must be an explicit `target=`**: a subclass of
  `Thread` is refused outright by `tests/test_g2_no_self_trigger.py`, which reads
  every `threading.Thread(...)` construction in `agent_core/` and asserts the target
  is in a reviewed set. Adding `self._poll_loop` to `_REVIEWED_THREAD_TARGETS` — with
  the sentence saying what hands it its work — **is a required, deliberate,
  reviewable step of phase 2**, not a test fixup. It is the only place in this design
  where the floor's letter has to be argued rather than merely satisfied, and the
  argument is written below.
- **`_poll_loop(channel_id)`** is the whole runtime: read the token, call
  `adapter.poll(...)`, hand each message to `enqueue_turn`, advance the cursor, repeat;
  on `ChannelUnavailable` back off and repeat; on `ChannelAuthFailed` stop the loop
  and notify. It **touches no store, ever** (constraint b), which is what makes it
  legal as a thread at all — the same rule `main.py` states beside the folder picker.
- **`deliver(channel_id, chat_id, text)`** is the send side: split to the adapter's
  limit and send the parts in order, catching `ChannelRefused` and reporting once. It
  is called from the worker thread when a turn finishes, and it is store-free too.
- **`stop_all()`** is called when the core shuts down and when the profile leaves
  Developer.

**The G2 argument, at full strength.** G2 says Addison never triggers itself. The
poll loop repeats, so the sentence deserves more than an assertion:

- **It hands no callback to a clock.** `tests/test_g2_no_self_trigger.py` bans
  `Timer`, `scheduler`, `enterabs`, `alarm`, `call_later`, `call_at`, `create_task`,
  `ensure_future` and `run_coroutine_threadsafe` anywhere in `agent_core/`, by AST
  scan and through aliases. The loop uses none of them; it blocks on a socket read
  that a person's message is what ends.
- **It originates no work.** A poll that finds nothing does nothing — no turn, no
  tool, no model call, no row. The only thing that produces work is a message a
  human typed, which is the same relationship the read loop has to the keyboard. What
  the loop is, precisely, is a second inbound edge on a process that already has one.
- **Addison still never speaks first.** No proactive message, no notification, no
  scheduled digest, no "you asked me to remind you". If a later version ever wants
  one, it is a new owner decision against this floor and not an extension of this
  feature.
- **What the loop genuinely costs**, and it is the honest half: a thread that repeats
  is a thing that must be switched off, so `stop`/`stop_all` are part of the contract
  and not an afterthought, and the enabled state is the person's, visible in Settings
  and one click from off.

**How a turn is handed over.** The service does not call the orchestrator. It puts a
job on the same queue every RPC method uses:

```python
enqueue_turn(("channel_turn", {"channelId": ..., "chatId": ...,
                               "senderId": ..., "text": ...}, None))
```

`request_id` is `None` because nothing is waiting for a JSON-RPC reply — the answer
goes to a phone, not to the webview. The worker's `if kind == ...` chain gains one
arm, `_run_channel_turn` *(proposed, on `ChannelsMixin`)*, and from there a remote
turn is an ordinary turn on the ordinary thread. **Everything follows from that
one choice**: SQLite stays on one thread; remote turns serialize behind desk turns
and vice versa; `conversation.stop` reaches a remote turn the same way it reaches a
desk turn; the audit rows, the undo stack and the snapshot hooks all work because
nothing about them was special-cased.

### 3.5 `_run_channel_turn` *(proposed)* — what a remote message becomes

On the worker thread, in order:

1. **Resolve the pairing.** `sender_id` is looked up in `channel_pairings`. **An
   unknown sender is ignored in silence** — no reply, no error, no read receipt.
   A reply is an oracle: it tells a stranger who guessed a bot name that the bot is
   real, that it is running, and that somebody is behind it. The only thing an
   unpaired message produces is a counter on `channel.status`, so the person can see
   that strangers are knocking. If the pairing is *pending* (§3.7), the message is
   handled as a pairing attempt instead of a turn.
2. **Screen the text.** `screen(text)` from `agent_core/screening.py`, and
   `mark_untrusted(text, verdict)` on the copy the model is handed. This is the
   **sixth origin** and the first that is not a tool result: today the orchestrator
   seam screens results carrying `content_origin == "external"` (`web_search`,
   `read_web_page`, `run_command`, and MCP results), and `routine.importPreview` /
   `routine.importConfirm` screen a file somebody else wrote.
   [`untrusted-screening-plan.md`](untrusted-screening-plan.md)'s decision 5 is the
   one that governs: local material reached through the person's own consent is not
   screened, and the revisit condition is a **standing channel for text the person
   did not write**. A messaging channel is that condition in its plainest form — the
   chat can carry a forwarded message, a pasted page, a quoted email — so inbound
   text is screened and the plan's origin list gains a line when this is built. It
   remains a backstop and not a boundary: prose in a shape nobody enumerated passes
   unmarked, it changes nothing at the gate, and the plan owns that statement.
3. **Find or make the conversation.** One conversation row per channel, created on
   first use, titled in plain words (*"From your phone"*). It is loaded into a
   `Conversation` object the job owns, and `self.conversation` is **not touched** —
   the desktop's thread, its `_message_ids` alignment and its rewind anchors are all
   untouched by construction (constraint c). Whether this conversation may ever see
   the desktop's history is **owner decision 3**; the default written here is no.
4. **Run the turn.** `orchestrator.run_turn(remote_conversation, mode=self._mode(),
   surface=TurnSurface.REMOTE, stream_to=None)` — the same orchestrator, the same
   registry, the same gate. `surface` and `stream_to` are two new optional parameters
   *(proposed)*; **with `surface=TurnSurface.DESK` and `stream_to=None` the path is
   byte-identical to today's**, which is the idiom
   [`model-assignments-plan.md`](model-assignments-plan.md) §2.2 uses and the same
   freeze the routing chain's head got.
   `TurnSurface` *(proposed)* is an enum in `agent_core/policy.py`, beside
   `PolicyMode`, because both `tools/registry.py` and `orchestrator.py` need it and
   `policy.py` is what they already share.
5. **Answer.** `channel_service.deliver(...)` with the assistant's final text.
6. **Tell the desk what happened.** A `channel.remoteTurn` notification *(proposed)*
   carrying the phase and a short summary. **It is deliberately not
   `conversation.streamChunk` and not `tool.activityUpdate`**: both of those are read
   by the frontend as belonging to the thread on screen, and a phone turn's words
   appearing inside somebody's desktop conversation is the failure this whole section
   exists to avoid. The channel panel renders it instead.

**Streaming is off for a remote turn** because `stream_to=None` and the relay
`_DeltaRelay(self.stream_to_frontend)` is constructed per run. Without that
parameter, a remote turn's deltas would be pushed into the desktop thread by the
server-level `stream_to_frontend=self._emit_stream_chunk` wiring, which is the same
class of mistake as reusing the active conversation.

### 3.6 The remote floor: `REMOTE_TOOL_IDS` and `remote_tools(mode)` *(proposed)*

This is the heart of the document.

**What the registry actually offers.** `ToolDefinition` has exactly five fields:
`id`, `label`, `description`, `risk_tier`, `parameters_schema`. There is **no
`destructive` field** — destructiveness is a per-call question,
`tools/base.call_is_destructive(tool, args)`, which asks the tool's optional
`is_destructive(args)` and otherwise answers "yes if `risk_tier is HIGH`". The
registry's own flags live in private sets (`_open_only`, `_removable`,
`_not_callable`, `_live_only`) and `visible_tools(mode)` filters on exactly two of
them: `_open_only` (only in SAFE) and `_not_callable` (in every mode). `RiskTier` has
three values and `LOW` is documented at the code as *read-only, no undo needed*.

**Why a predicate alone is not enough.** The obvious floor — *LOW and not
`open_only`* — admits every LOW tool registered today: `calculator`, `web_search`,
`read_web_page`, `read_file`, `read_project_file`, `read_clipboard`, `open_link`,
`snapshot_now`. Three of those are wrong for a phone and the reasons are not about
risk tiers:

- **`read_clipboard`** would let a message sent to a bot return whatever is on the
  clipboard of an unattended Mac, over a transport's servers. It is read-only and it
  is an exfiltration path.
- **`open_link`** is LOW because it is not undoable-in-the-file-system sense, but it
  makes something happen on a screen nobody is sitting at.
- **`read_file` / `read_project_file`** are the same shape as the clipboard with a
  larger surface: local file contents leaving the machine through a chat server, on
  the strength of one message. They are properly in the SAFE view for a person at
  the keyboard; a phone is a different consent.

So the floor is not a tier test. **It is a CLOSED SET of tool ids, hard-coded, with
the tier test asserted over it by a test.** That is invariant 4's own lesson —
widget kinds are a closed set the code owns, because *a spec never declares its own
capabilities* — applied to the same problem one layer down. It is also the answer
step 7 gave when a stranger's server declared its own risk: a self-declared
`remote_ok` flag on a tool would be that trust hole moved indoors.

```python
# agent_core/tools/registry.py                                    (proposed)
REMOTE_TOOL_IDS: frozenset[str] = frozenset({
    "calculator",
    "web_search",
    "read_web_page",
})

def remote_tools(self, mode: PolicyMode) -> list[ToolDefinition]:
    """The view a turn that arrived over a messaging channel is offered.

    An INTERSECTION with ``visible_tools(mode)``, never a union: a tool the mode
    already hides can never appear here, so this view is a subset of the desk's
    view in every mode, and adding an id to the set above can only ever take
    something out of the desk's view and put it in a smaller one.
    """
    return [d for d in self.visible_tools(mode) if d.id in REMOTE_TOOL_IDS]
```

**The four properties a test asserts about that set** *(proposed:
`tests/test_channel_remote_floor.py`)*, each of which is what makes the list safe to
edit later:

1. Every id in it is registered, and its `risk_tier is RiskTier.LOW`.
2. Every id in it is absent from the registry's `_open_only` set — so nothing
   dev-only, nothing from an outside tool server, nothing that runs a command, in
   any mode.
3. `call_is_destructive(tool, {})` is `False` for each, and no tool in the set
   defines `is_destructive` at all — so no argument can make one of these calls
   destructive.
4. **The set is a subset of the ids in `visible_tools(PolicyMode.SAFE)`.** This is
   the sentence worth having: *a remote turn is never offered a tool Simple could
   not be offered.* It is one assertion and it holds the whole floor.

**Where it is enforced: at dispatch, not at display.** The artifact-disabling lesson
is explicit that a marker is never the enforcement and dispatch wins if the two
disagree. So `remote_tools(mode)` decides what the model is *offered*, and a second,
independent check refuses a `tool_use` naming anything else before the gate and
before any effect:

```python
def refuse_if_not_remote(self, tool_id: str, surface: TurnSurface) -> str | None:
    """One plain sentence when a REMOTE turn names a tool the remote floor omits."""
```

modelled on `refuse_if_dev_only_outside_open` / `refuse_if_not_callable` /
`refuse_if_live_only`, which is the shape every other pre-gate refusal in this
registry already takes, with its sentence as a module constant beside
`DEV_ONLY_REFUSAL` and friends. The sentence says what the person can do instead,
because a refusal with no next move comes back as a blocked task —
`LIVE_ONLY_REFUSAL`'s own rule:

> *"That's something Addison does at your computer. It's saved as a request — it will
> be waiting on your screen when you're back."*

**And the card that must never be raised.** Constraint (d): a card blocks the worker
thread forever. Under Developer's default guards (`auto_grant_scope =
"non_destructive"`) a non-destructive call is auto-granted and no card appears, so the
floor's tools pass the gate without one. **But guards are settings.** Under the Custom
profile a person can set `auto_grant_scope = "none"`, at which point
`PermissionGate.authorize` routes even a LOW call to `_safe_flow`, which asks — and
the phone turn would park the whole core. So:

- **The channel refuses to run a turn while `auto_grant_scope == "none"`**, checked
  at start and re-checked per turn because guards change under a running service.
  The phone gets one plain sentence (*"You've asked Addison to check with you before
  every action, so it can't answer from your phone."*) and the desk gets a status
  line. This is a **narrowing**, which is the permitted direction.
- The general fix — a per-turn permission handler that can answer without a desk —
  is precisely what phase 4 is, which is why phase 4 is the phase that makes this
  restriction go away rather than a phase that works around it.

### 3.7 Pairing: `agent_core/channel_pairing.py` *(proposed)*

**Pairing is the authorization boundary, and message content never is.** No keyword,
no prefix, no "only obey messages that start with", nothing a message can say about
itself. Step 8's reasoning is the whole argument and it transfers unchanged: a fixed
prefix *"is forgeable by anything that can write English"*, and the fix is a code
minted at the moment of asking, which no observed content could have written down in
advance.

**The flow.** The desktop shows the code; the phone sends it.

1. `channel.beginPairing {id}` mints a code and returns it with an expiry. The
   desktop displays it beside one sentence about what pairing means.
2. The person messages the bot with that code from their phone.
3. The service sees a message from an unknown sender while a pairing is open,
   compares, and on a match writes a `channel_pairings` row binding that
   `sender_id`. The bot replies once, confirming.
4. Anything else from an unknown sender is still ignored in silence, including a
   wrong code — the attempt budget is spent, and nothing is said.

**Why this direction and not the reverse** (Addison sends a code to a number the
person types in): sending requires already knowing an address, which is the thing
pairing exists to establish, and a bot cannot message a person who has not messaged
it first on most transports anyway. Desktop-shows / phone-sends also puts the secret
on the trusted screen and the proof on the wire, which is the correct way round.

**The code itself is `agent_core/automation_nonce.py`, reused, not reimplemented.**
That module is pure and stateless: `mint()` returns six characters from
`ALPHABET = "ACDEFGHJKMNPQRTUVWXYZ23479"` (lookalikes already removed, for personas
54 and 68) grouped as `ABC-DEF`; `normalise(typed)` strips separators and upcases;
`matches(typed, expected)` compares with `hmac.compare_digest` after normalisation;
`MAX_ATTEMPTS = 3`. **There is no expiry in that module and there must not be one
added to it** — its arming caller holds the attempt budget in `_ask_with_keyword`'s
locals, and lifetime belongs to whoever holds the state. So:

```python
@dataclass
class PendingPairing:
    channel_id: str
    code: str
    expires_at: int
    attempts_left: int

def begin(channel_id: str) -> PendingPairing: ...
def offer(pending: PendingPairing, sender_id: str, typed: str) -> PairingOutcome: ...
```

`begin` calls `automation_nonce.mint()`. `offer` checks expiry first, then
`automation_nonce.matches`, then decrements. `PairingOutcome` is one of matched /
wrong / expired / exhausted, and **only `matched` produces any outbound message**.
Pending pairings live in memory on the service and are gone on restart, which is
correct: an open pairing window is a moment, not a setting.

**What the code defends, at its real strength.** It stops a stranger who knows the
bot's name from becoming the operator, and it stops observed content from
pre-scripting the pairing, because a code that did not exist when the instruction was
written cannot be quoted in it. **What it does not defend**: a person who is shown a
code and types it somewhere else, and a phone that is already unlocked in somebody
else's hand (§6).

**Revocation is the whole control surface.** `channel.pairings {id}` lists paired
devices with the label the transport gave and when they paired; `channel.revokePairing
{pairingId}` deletes the row. Revocation answers **in every profile**, because a
tightening must never be trapped by a profile switch — the rule
[`SAFETY.md`](SAFETY.md) owns and step 8 phase 4 followed when Simple kept Remove and
only Remove.

### 3.8 Data

Two tables in `agent_core/memory/schema.sql`, shaped on `mcp_servers`:

```sql
CREATE TABLE IF NOT EXISTS channels (                        -- proposed
    id            TEXT PRIMARY KEY,      -- uuid4
    kind          TEXT NOT NULL CHECK(kind IN ('telegram')),
    name          TEXT NOT NULL,         -- the person's own label for this channel
    enabled       INTEGER NOT NULL DEFAULT 0,
    token_present TEXT NOT NULL DEFAULT 'unknown'
                      CHECK(token_present IN ('present','absent','unknown')),
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_pairings (                -- proposed
    id         TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    sender_id  TEXT NOT NULL,            -- the transport's id for the human
    label      TEXT NOT NULL,            -- display name. Untrusted text, capped.
    paired_at  INTEGER NOT NULL
);
```

- **`kind` carries a CHECK and there is no command column**, for the reason step 7's
  `transport CHECK` has one: the schema is where "this row can never name a program"
  is enforced, not a comment. A second transport widens the CHECK in the commit that
  adds its adapter.
- **`token_present` mirrors `provider_config.secret_presence`** in vocabulary and
  purpose: a non-secret three-state record of whether a credential is believed to
  exist, so a surface can say *"no token saved"* without anybody reading the
  keychain to render a list. Like `secret_presence`, **it is excluded from capture**
  (`_EXCLUDED_COLUMNS` in `agent_core/snapshots/scope.py` already carries the
  provider one) — a restored row must not claim a token that the keychain, which no
  snapshot touches, may no longer hold. This is the automations lesson in its
  general form: **a restore may put a ROW back and never the thing outside SQLite
  that the row describes.**
- **No token column, no chat id column, no message column.** The token is in the
  keychain (§3.9). Message text is never persisted outside the ordinary
  `messages` rows of the remote conversation, and `conversations` and `messages`
  are already excluded from capture.

**Snapshot scope, decided explicitly**, because
`tests/test_snapshots.py::test_capture_scope_covers_every_schema_table` forces every
new table into `_CAPTURED_TABLES` or `_EXCLUDED_TABLES` with a stated reason:

- **`channels` → CAPTURED** (`id, kind, name, enabled` — not `token_present`, not
  `created_at` if the existing tuples' convention says otherwise at build time). It
  is reversible configuration, exactly like `mcp_servers` and `automations`.
- **`channel_pairings` → EXCLUDED**, and this is a decision rather than an omission.
  A pairing is not configuration; it is an **authorization**. G3 promises that one
  action restores your configuration, and an authorization somebody deliberately
  revoked must not come back inside that one action. Step 8 solved the same problem
  by asking the OS what was armed, because the OS held the truth. **Here nothing
  outside SQLite holds the truth — the row *is* the authorization** — so the only
  honest answer is to keep it out of the capture, and the cost is small and
  symmetrical: after a restore, no phone is paired and pairing again costs one code
  and one message. The reason string in `_EXCLUDED_TABLES` says exactly that.

**What a restore therefore does**, written down so the next reader does not have to
derive it: the channel rows come back, `enabled` comes back, the token does not (no
snapshot has ever carried a secret), `token_present` reads `unknown` and the surface
says so, and **no device is paired**. The channel is off until the person turns it
on, which is the same shape as an automation row that is not armed.

`docs/data-model.md` gains both tables as ER entries in the same commit — its
coverage is test-enforced against the schema.

### 3.9 Keychain: a parallel command, not a borrowed one

**Decided: `store_channel_key` / `delete_channel_key` *(proposed)*, alongside the
provider pair, same `SERVICE`, new account namespace `channel-key:{kind}`, and
`keychain.getChannelKey` on the core→shell side.** The alternative — calling
`store_provider_key("telegram", token)` — was rejected after reading the function,
and the reasons are specific rather than aesthetic:

- `store_provider_key_blocking` runs the token through **provider machinery**: a
  `MintLedger`, `save_verdict`'s replace-detection and rollback record, `cache_evict`,
  `failure_forget` and `repair_lost_forget`, all keyed by provider id. A channel
  token in that ledger is a channel token participating in the provider key-repair
  story, which was built around one very particular failure and should not grow a
  second tenant.
- The read path `read_provider_key_from_os` has a branch for
  `LEGACY_ANTHROPIC_ACCOUNT` (`provider-key:primary`) and its copy-then-delete
  migration. Nothing about a channel should be able to reach that code.
- The Rust command takes `provider: String` **with no closed-set check**, so nothing
  today stops an arbitrary account string from being written under the provider
  namespace. Handing that generality a second caller turns a typed surface into a
  general keychain writer, which is the step-8 rule in one sentence: *a shell surface
  that accepted raw XML for LaunchAgents would be `run_command` with extra steps.*

What **is** reused, because it is genuinely general: the `SERVICE` constant, `Entry`,
`write_credential`, `os_guard`, the `spawn_blocking` shape (a keychain call can park
on a password dialog and must never hold the main thread), and `normalised_key`,
whose rules — non-empty, no line breaks, no invisible characters — fit a bot token
without change. The new commands are registered in `main.rs`'s
`tauri::generate_handler!` list beside the three that exist.

On the core side, `keychain.getChannelKey {kind} -> {key}` is added to
`keychain::handle`'s match in `shell/src-tauri/src/keychain.rs`, which is already
routed off the shell's main loop by `dispatch_off_loop`'s `KEYCHAIN_PREFIX` branch —
so a channel key read needs **no change to the shell's dispatch at all**. On the
Python side, `IpcShellBridge` gains `get_channel_key(kind: str) -> str`, modelled on
`get_provider_key`, using `_KEYCHAIN_TIMEOUT` and returning `result.get("key", "")`.

**The account stays keyed by KIND for v1 — decided 2026-08-22, building phase 2.**
Phase 1 found the consequence this section had not stated: `channels` permits several
rows of one transport, so two Telegram connections share one saved token
([`KNOWN-GAPS.md`](KNOWN-GAPS.md) holds the finding and the two honest behaviours
phase 1 gave it). Re-keying the account by CHANNEL ID was considered and rejected for
v1: the shared token only becomes a real problem when two connections of one transport
can both be *live*, and owner decision 11 permits exactly one enabled channel at a
time — so multi-channel v2 is the feature that would revisit this, and it should
revisit it here.

**G1 holds by construction**: the token is written by the webview straight to the OS
keychain and never travels back; the core reads it at the moment of use; `channel.list`
carries `token_present` and never a key; nothing writes it to SQLite; and no snapshot
has ever carried a secret.

### 3.10 The RPC surface

New `ChannelsMixin(ServerContext)` in `agent_core/rpc/channels.py` *(proposed)*,
added to `JsonRpcServer`'s mixin list in `agent_core/main.py` and to the closed set
`docs/architecture.md` names (test-enforced by
`test_architecture_names_every_rpc_namespace_module`). Every method below is a
constant on `class Method` in `agent_core/protocol.py` **and** a key in the `Method`
object literal in `shell/src/types/protocol.ts`; `tests/test_protocol_drift.py`
compares the two sets and fails on either side alone.

Webview → Core:

| Method | Shape | What it does |
|---|---|---|
| `channel.list` | `{} -> {channels: [row]}` | Every saved channel: id, kind, name, enabled, `tokenPresent`, paired-device count, and the live status if the service has one. Answers in **every profile** — the rows are inert and hiding somebody's saved configuration on a profile switch is the failure the 2026-08-06 artifact decision reversed. |
| `channel.add` | `{kind, name} -> {ok, channel}` | Writes one row, `enabled=0`, `token_present='unknown'`. Developer-only, and `add` is what enforces the profile boundary — the `mcp.add` pattern exactly. Connects to nothing. |
| `channel.remove` | `{id} -> {ok}` | Stops the loop, deletes the token, drops the pairings by cascade, deletes the row. Answers in every profile: removal is a tightening and is never trapped. |
| `channel.connect` | `{id} -> {ok, connectedAs}` | `verify_token` through the adapter. A **worker job**, never the read loop, for `mcp.refresh`'s stated reason — it reaches the network and a stranger's server must not hold the IPC pump. Records `token_present`. Still starts no loop. |
| `channel.setEnabled` | `{id, enabled} -> {ok}` | Starts or stops the poll loop and writes the row. The one control that makes a channel live. |
| `channel.status` | `{id} -> {state, connectedAs?, lastPollAt?, backoff?, unknownSenders, error?}` | What the service knows: running / stopped / backing off / stopped-because-the-token-was-rejected, the count of messages from unknown senders, and one plain error sentence. Never a transport's own error text. |
| `channel.beginPairing` | `{id} -> {code, expiresAt}` | Mints the pairing code and opens the window. |
| `channel.cancelPairing` | `{id} -> {ok}` | Closes it. |
| `channel.pairings` | `{id} -> {pairings: [row]}` | The paired devices, with labels and dates. |
| `channel.revokePairing` | `{pairingId} -> {ok}` | Deletes one. Answers in every profile. |
| `channel.pendingRequests` | `{} -> {requests: [row]}` | The desk queue (§3.11). |
| `channel.dismissRequest` | `{requestId} -> {ok}` | Removes one from it. |

Core → Webview (notifications):

| Method | Shape | What it does |
|---|---|---|
| `channel.stateChanged` | `{id, state, error?}` | The service's state moved; the panel re-renders without polling. |
| `channel.requestQueued` | `{request}` | A remote turn asked for something the floor omits and it is now on the desk queue. |
| `channel.remoteTurn` | `{id, phase, summary?}` | A phone turn started / finished, for the panel. **Not** `conversation.streamChunk`, **not** `tool.activityUpdate` (§3.5). |

Core → Shell:

| Method | Shape | What it does |
|---|---|---|
| `keychain.getChannelKey` | `{kind} -> {key}` | Reads the token at the moment of use. Handled by the existing `keychain.*` off-loop branch. |

Which of these are worker jobs and which are inline is not a matter of taste: every
one that touches the store or the network is a `_CHANNEL_JOBS` *(proposed)* entry
folded into the dispatch table by `enqueue`, exactly like `_MCP_JOBS` and
`_AUTOMATION_JOBS`. Nothing in this namespace runs on the read loop, and nothing in
it starts a thread from an RPC handler: the only threads are the service's, started
by `channel.setEnabled` and stopped by it.

### 3.11 The desk queue

When a remote turn names a tool the floor omits, two things happen: the model is
told, in one plain sentence, that this is something Addison does at the computer; and
the request is recorded so the person sees it when they are back.

```python
@dataclass(frozen=True)
class PendingRequest:                                          # proposed
    id: str
    channel_id: str
    asked_at: int
    tool_label: str      # the tool's plain-language label, never its id
    what_was_asked: str  # the person's own message, capped
```

- **It lives in memory on the service, not in a table.** Two reasons, both borrowed.
  The MCP catalog is held in memory because *a stranger's text in a captured table is
  copied into every later snapshot payload and plaintext sidecar, forever*; a queue
  row would carry a message somebody typed on a phone and a tool's arguments, which
  is the same shape. And the queue is a set of moments, not configuration: it exists
  to be read once. If the app restarts, the queue is empty, and since v1 only answers
  a phone while the app is running (§6) there is nothing incoherent about that.
  Whether it should survive a restart is **owner decision 7**.
- **A queued request is a RECORD, not a resumable action.** The desk shows it and
  offers *"Ask this here"*, which composes the message into the desktop conversation
  for the person to run live, with the ordinary card. It does **not** offer "approve
  and run it now". Replaying a stored request through a button would be a second
  dispatch path, raising a card written for a moment that has passed, for arguments a
  model chose in a context nobody is looking at any more. The whole design's rule is
  one registry, one gate, one dispatch, and this is where it would have been quietly
  broken.
- **Expiry** is a bounded age and a bounded count, both on the service; the oldest
  falls off. Nothing about the queue can grow without limit from the far end of a
  network.

### 3.12 Frontend

The MCP pairing is the template, and it is followed exactly: a hook
`shell/src/hooks/useChannels.ts` *(proposed)* exporting `useChannels` and
`type ChannelsCardState = ReturnType<typeof useChannels>`; a panel
`shell/src/components/ChannelsPanel.tsx` *(proposed)*; and a
`<SurfaceSection label="Your phone">` block in
`shell/src/components/SettingsPage.tsx`, rendered only in Developer — the same
treatment `"Tool servers"` gets, and for the same reason
[`model-assignments-plan.md`](model-assignments-plan.md) §2.7 states: a Settings
surface for a capability a profile lacks is profile surface, not a disabled artifact.
Nobody's work is being hidden here.

The section carries, in this order:

1. **The privacy sentence**, before the token field, not after it, and shown every
   time the section renders rather than once at setup:

   > *"Messages you send from your phone travel through Telegram's servers, the way
   > any other Telegram message does. Everything else stays on this computer."*

   It is one sentence, it is plain, and it is true. Addison is local-first and this
   is the one feature that moves a person's words off the machine on purpose;
   [`SAFETY.md`](SAFETY.md)'s temperament is that a cost like that is stated where the
   choice is made.
2. **The channel row**: name, connected-as, the enable switch, status in plain words.
3. **Paired devices**, each with a Revoke.
4. **What Addison will and will not do from a phone**, as a short standing list
   rather than a link — the remote floor in the person's own vocabulary
   (*"look things up on the web"*, *"do the maths"* / *"anything that changes a file,
   runs a command, or touches your computer waits until you're back"*).
5. **Pending requests**, when there are any.

Copy is plain-language throughout, per the personas: not "remote floor", not "tool
tier", not "pairing nonce" — *"the code Addison shows you"*.

---

## 4. Part two: the build order

Four phases. Each lands green, is independently useful, and is **inert without the
next one** — the discipline steps 7 and 8 both used. Every phase names what it
deliberately does not ship, because that is the half that stops a phase from quietly
becoming two.

### Phase 1 — Configuration, and nothing connects

**Ships.** The `channels` table (captured) and `channel_pairings` (excluded), both
declared in `agent_core/snapshots/scope.py`. `channel.list` / `add` / `remove`.
`ChannelsMixin` and its `_CHANNEL_JOBS` entries. The Rust `store_channel_key` /
`delete_channel_key` commands, the `keychain.getChannelKey` arm, and
`IpcShellBridge.get_channel_key`. The Settings section, Developer-only, with the
privacy sentence and a token field that saves to the keychain. The protocol constants
on both sides. ER entries in `docs/data-model.md`; the namespace in
`docs/architecture.md`.

This is the MCP phase-1 shape: **it stores an address and a credential and nothing
happens.** That is the point — the reversible-config half, the G1 half and the
capture decision all land before anything can reach a network.

**Deliberately does not ship.** Any network call at all. `verify_token` is not wired,
so a saved token is unvalidated and the row says `token_present: 'unknown'` until
phase 2 can ask. No adapter, no thread, no pairing.

**Tests.** `tests/test_channels.py` (the rows, the profile boundary on `add`, the
cascade on `remove`, the snapshot capture and restore, the `token_present` exclusion
from capture); additions to `tests/test_snapshots.py` for the scope declaration;
`shell/src/__tests__/channels.test.tsx` (the section, the profile gate, the privacy
sentence); a `channel.list` payload fixture joining the generated-parity machinery
the way `mcp.list` and `automation.list` did; Rust tests in `keychain.rs` pinning
`account_for_channel("telegram") == "channel-key:telegram"` beside the existing
`account_for_provider` test.

**Gates.** `./scripts/gates.sh` — all three jobs; the Rust one matters here because
of the new commands.

**Safety re-check.** G1: the token's only path is webview → Rust → keychain, and
nothing reads it yet. G2: nothing runs. G3: capture declared for both tables, restore
tested, `token_present` proven not to survive a snapshot. G4: no guard. SAFE 1–4:
no tool registered, no view changed, `visible_tools` untouched.

### Phase 2 — Connect, pair, and answer with words only

**Ships.** `channels/adapter.py` and `channels/telegram.py`. `channel_service.py`
with the poll loop and `_REVIEWED_THREAD_TARGETS` updated in
`tests/test_g2_no_self_trigger.py`, with the sentence that says what hands the thread
its work. `channel.connect`, `channel.setEnabled`, `channel.status`,
`channel.beginPairing` / `cancelPairing` / `pairings` / `revokePairing`.
`channel_pairing.py` over `automation_nonce`. `_run_channel_turn`, the dedicated
conversation, screening at the door, and `deliver`. `TurnSurface` in `policy.py`,
and `run_turn`'s two new optional parameters with the byte-identical-when-unset test.
`REMOTE_TOOL_IDS = frozenset()` — **empty**, and `remote_tools(mode)` returning `[]`.
The `channel.stateChanged` / `channel.remoteTurn` notifications and the panel that
renders them.

The claim this phase makes, and only this: **a paired phone can hold a conversation
with Addison, and Addison can use no tools at all while doing it.** An empty tool
view is not a placeholder — it is the strongest possible version of the phase, and it
proves every seam (the thread, the hand-off, the conversation isolation, the
screening, the splitting, the pairing) with the tool question factored out entirely.

**Deliberately does not ship.** Any tool. Any queue. Any card. `run_command` is
unreachable twice over (an empty remote view; the pre-gate refusal), which is the
two-layer shape MCP phase 2 used and is what makes phase 3 a switch rather than a
rebuild.

**Tests.** `tests/test_channel_pairing.py` (mint, match, expiry, the attempt budget,
**silence on every non-match**, revocation in every profile).
`tests/test_channel_turn.py` against an `httpx.MockTransport` adapter: an unknown
sender produces no outbound request at all; a paired sender's message runs a turn;
the turn's messages land in the remote conversation and `self.conversation` is
unchanged; a long answer is split and ordered; screening marks an
instruction-shaped message and the kinds are reported; `auto_grant_scope == "none"`
refuses with the plain sentence; the empty tool view is what the provider was
offered. Additions to `tests/test_g2_no_self_trigger.py` (the reviewed target) and
`tests/test_module_boundaries.py` (`channels/` imports none of the three siblings).
`tests/test_orchestrator.py` gains the byte-identical-when-DESK pin.
`shell/src/__tests__/channels.test.tsx` grows the pairing and status surfaces.

**Gates.** All three.

**Safety re-check.** G1: the token is read per poll through the bridge and never
stored or logged. **G2: the argument of §3.4, made in the commit** — the reviewed
thread target, the AST ban still green, and the explicit statement that Addison sends
no first message. G3: nothing new captured; a restore leaves the channel off and
unpaired, tested. G4: no guard. SAFE 1: the remote view is empty, so trivially a
subset of the SAFE view. SAFE 2: no tool registered. SAFE 3: one registry, one gate,
one orchestrator — asserted by identity in the test, not by reading. SAFE 4: untouched.

### Phase 3 — The remote floor and the desk queue

**Ships.** `REMOTE_TOOL_IDS` gains its three ids and the four properties of §3.6 are
asserted. `refuse_if_not_remote` and its plain sentence, wired at **both** dispatch
paths, before the gate. The pending queue, `channel.pendingRequests`,
`channel.dismissRequest`, `channel.requestQueued`, and the desk surface with *"Ask
this here"*. The standing what-Addison-will-do list in the panel.

The claim: **a phone can ask Addison to look something up, and everything else comes
back as a plain sentence and a note waiting on the desk.**

**Deliberately does not ship.** Any approval from a phone. Any write, any file, any
command, any MCP tool, in any profile, by any path. Any replay of a queued request.
Any persistence of the queue.

**Tests.** `tests/test_channel_remote_floor.py` — the four assertions over the set;
every LOW tool **not** on the list is refused at dispatch on a REMOTE turn (a table
test over the registry, so a newly-registered LOW tool is refused by default and its
admission is a deliberate edit); a REMOTE turn naming `run_command` is refused before
the gate and leaves a `tool_audit` row; the refusal sentence names the next move.
Additions to `tests/test_channel_turn.py` for the queue's shape, cap and age-out, and
for *"Ask this here"* producing a desktop message and never a dispatch.
`tests/test_tool_registry.py` gains the subset-of-SAFE assertion, because that is the
sentence the floor is really made of and it belongs with the registry's own tests.

**Gates.** All three.

**Safety re-check.** G1–G4 unchanged; nothing here touches a secret, a clock, a
capture or a guard. SAFE 1: strengthened and now load-bearing — the subset assertion
is what says a phone is never offered more than Simple. SAFE 2: only LOW ids admitted,
so the undo question cannot arise. SAFE 3: `remote_tools` is a filtered view over the
same registry; a test asserts it is not a second registry by identity. SAFE 4:
untouched.

### Phase 4 — A card on the phone *(DEFERRED, owner decision 2 of 2026-08-22: the
horizon for approving actions from a phone is a bespoke phone app, not this)*

**Ships only if that horizon changes.** The gate reflects its own card to the phone: the
card's title and description **as the gate composed them**, plus a freshly minted
code. Replying with that code approves **that one invocation** and nothing else.
Mechanically this is `_ask_with_keyword`'s shape with the answer arriving from a
different edge, and it is what supplies the per-turn permission handler that phase 3's
`auto_grant_scope == "none"` restriction is waiting for.

**Why the card must be gate-authored and never model-authored**, stated because it is
the entire security content of the phase: a person approving over a channel is
reading text on a phone with no other context. If a model could compose or influence
that text, an injected instruction in a web page could persuade the model to write a
description of the action that is not the action — the classic *"say it is a harmless
lookup"* — and the code would be typed against a lie. The card's words come from
`ToolDefinition.label`, `_card_consequence`'s output and the tool's own
`permission_sentence`, which are Addison's strings; the model's contribution to the
card is the arguments, and those are shown, not narrated. This is
`step-7-mcp-plan.md`'s position on a server's own words in a card — Addison's
sentence first, position is the boundary — arriving at the same answer from a
different direction.

**Deliberately does not ship, in any version of this phase.** Destructive calls stay
desk-only, unconditionally, whatever the code says: the phone raises the floor's
ceiling by one step and never removes it. Arming an automation over a channel is
refused outright — the arming code exists so somebody reads a preview at their own
computer, and `live_only` already refuses a stored, replayable spec for exactly that
reason. Nothing here auto-allows anything: **there is no version of this design in
which a remote action runs without a person answering for it.**

**Tests.** A dedicated suite for the reflected card: the text matches what the desk
card would have carried, byte-for-byte, from a fixture shared with the desk path; a
wrong code denies and spends an attempt; an expired code denies; a code approves
exactly one invocation and a second call re-asks; a destructive call is refused
before a code is ever minted; the code never enters the transcript, the model's
context, `tool_audit` or any store — the property `automation_nonce`'s caller already
keeps and the one most likely to be lost in a second implementation.

**Gates.** All three, plus a fresh read of the honest-limits section, because this
phase changes what a stolen phone is worth (§6).

---

## 5. Decisions for the owner — ANSWERED 2026-08-22

All eleven were answered the day the plan was written. Each entry keeps the
question and the recommendation as history, and records the owner's answer as the
decision. Where an answer added scope the plan had not proposed, that is said
plainly rather than folded in quietly.

1. **Transport order.** Telegram first, WhatsApp as an adapter only if a Business
   API account is something the owner wants to pay for and operate; no bridge
   adapter, documented on GitHub only, never surfaced in-app (the OmniRoute stance).
   *Recommended: yes.*
   **ANSWERED: Telegram is the default, and other connections stay allowed** — the
   adapter protocol remains plural and a second transport is a welcome later
   adapter, under the same bridge stance, which the answer did not overrule.
2. **Phase 4: build it, or stop at phase 3?** *Recommended: decide after living with
   phase 3.* Phase 3 is genuinely useful on its own and phase 4 is the only part of
   this design that lets a phone cause an effect.
   **ANSWERED: the channel stays conversational — the phone can query and be
   answered (phases 1–3) — and approving actions from a phone waits for a bespoke
   phone app**, which the owner named as the horizon. Phase 4 is therefore
   **deferred, not scheduled**: it stays in this document as the design that would
   be reached for if the reflected-card path is ever wanted before or instead of
   an app. A bespoke phone app is a new product surface with its own trust story
   and is deliberately NOT designed here.
3. **May a remote turn see the desktop's conversation history?** *Recommended: no,
   and the default written into §3.5 is no.*
   **ANSWERED: no — until an app is made.** The messaging channel never sees
   desktop history; whether a bespoke app should is that app's design question,
   not this plan's.
4. **Background / menu-bar mode**, so a channel answers with the window closed.
   *No recommendation; it is a product question.*
   **ANSWERED: yes — and with a menu-bar popup chat window.** This is approved
   direction and **added scope**: a resident presence that answers channels with
   the main window closed, plus a small popup chat on the menu-bar item. It is a
   different feature with its own trust questions (a process that lives when
   nobody opened it; a second chat surface on the desktop), so **it gets its own
   design section in this document before any phase builds it** — it is not part
   of phases 1–3, and nothing in them depends on it.
5. **What is on the remote floor's list?** The plan proposes `calculator`,
   `web_search`, `read_web_page`; omissions argued in §3.6.
   **ANSWERED: the three as proposed.**
6. **Custom-guard interaction.** Refuse to answer a phone while
   `auto_grant_scope == "none"`, rather than raising a card nobody is there to
   answer.
   **ANSWERED: as recommended.** (With phase 4 deferred, the restriction simply
   stands; the sentence the phone gets is §3.6's.)
7. **Does the desk queue survive a restart?**
   **ANSWERED: no**, as recommended (§3.11's reasoning).
8. **Messages that arrived while the Mac was asleep.** *Recommended: decline each
   with one sentence.*
   **ANSWERED: a SETTING — queue them, or decline them.** Added scope, small and
   contained: a per-channel choice between answering held messages on wake and
   declining each with the plain sentence. **Default: decline** (the recommended
   direction), because the safe behaviour should be the out-of-box one; the
   setting lives with the channel row and is ordinary captured configuration.
   Phase 2 ships the default; the setting itself may land in phase 2 or 3,
   whichever diff it fits.
9. **Duplicate delivery.** *Recommended: accept for phase 3; dedupe mandatory in
   phase 4.*
   **ANSWERED: as recommended.** (Phase 4's deferral makes the second half moot
   until it isn't.)
10. **Profile surfacing.** *Recommended: dev-only for v1, widen on evidence.*
    **ANSWERED: as recommended.**
11. **The module name `automation_nonce.py`** — rename, or leave it?
    *Recommended: leave it.*
    **ANSWERED — the owner's answer went to a different question**, and both halves
    are recorded: the rename stands as recommended (leave it), and the answer given
    — *"allow an option for multiple connections as a v2/future step"* — is a real
    decision this list had not asked: **v1 runs ONE enabled channel at a time;
    multiple simultaneously-enabled channels are v2.** The schema and the service
    already permit several rows and several threads, so this is a v1 *surface*
    restriction (enabling one channel disables the others, said plainly), kept so
    the first release has one pairing story and one status line to get right.

## 6. Honest limits

What this costs when it is working exactly as designed. None of these are bugs and
none of them are closed by a later phase unless it says so.

- **The transport's servers see the conversation.** A Telegram bot conversation is
  not end-to-end encrypted; it is stored on Telegram's servers and readable by
  Telegram. Every word a person sends from their phone and every word Addison sends
  back transits and rests there. Addison is local-first and this is the one feature
  that is deliberately not, which is why the sentence in §3.12 is on the screen where
  the choice is made and not in a settings page nobody opens. A different transport
  moves this cost; no transport removes it.
- **A stolen unlocked phone is a paired identity.** Pairing binds a sender id, not a
  person. If somebody has the phone, they have the channel, and the only control is
  revocation from the desk. The remote floor is what bounds the damage, which is the
  real argument for keeping that list short, and it is the reason phase 4 is an owner
  decision rather than an obvious improvement.
- **Screening is a backstop and not a boundary.** Inbound text is screened by
  `agent_core/screening.py`'s six enumerated rules; text in a shape nobody enumerated
  passes unmarked, and a mark changes nothing at the permission gate.
  [`untrusted-screening-plan.md`](untrusted-screening-plan.md) owns that statement at
  its real strength and this document does not raise it.
- **Two of the three floor tools reach the open web.** `web_search` and
  `read_web_page` mean a remote turn can pull a stranger's page into a model's context
  with nobody watching the screen. Their results are screened like any external tool
  result, which is the same backstop with the same limits. This is the strongest
  argument that the floor's list should stay a closed set somebody edits deliberately.
- **The Mac must be awake and Addison must be running.** No proxy, no daemon, no
  background service. A sleeping laptop answers nothing, and the honesty of that is
  the price of not standing up infrastructure between a person's phone and their own
  computer. Design-doc §7.10 assumed the opposite and §11 assumed a proxy that does
  not exist; this plan's answer is the smaller promise, and owner decision 4 is where
  a bigger one would be made.
- **A remote turn and a desk turn share one worker thread.** A long remote turn makes
  the desktop wait, and a long desktop turn makes the phone wait. That is the price
  of the single-SQLite-thread rule and it is the right price — the alternative is a
  second store path, which is the class of change this repo has been most careful to
  avoid.
- **A transport outage is silent to the person on the phone.** The desk shows
  backoff on `channel.status`; the phone shows a message that was sent and not
  answered, which looks exactly like Addison thinking. There is no delivery receipt
  and no "I got your message" ack, because an ack is an outbound message per inbound
  message and doubles what transits a stranger's servers. A person whose phone goes
  quiet has to look at their Mac, and that is not a great answer.
- **Delivery is at-least-once.** A crash between hand-off and cursor advance
  re-delivers (§3.3, owner decision 9).
- **Nothing here makes Addison reachable *from* a phone when it is not running**,
  and no phase of this plan changes that. That is the same sentence as the fifth
  bullet, written again on purpose, because it is the one a reader most wants not to
  have read.

## 7. What this plan does NOT include

- **No listener, no webhook, no inbound port, no tunnel**, in any phase.
- **No proactive message.** Addison never sends the first message on a channel: no
  reminders, no digests, no notifications. G2, and it is not a scheduling limitation
  that a later phase relaxes.
- **No auto-allow of a remote action, ever.** Phase 4 adds a way to say yes; there is
  no design here in which nobody says it.
- **No arming an automation from a phone**, in any phase.
- **No SAFE admission** and no Simple surface in v1 (owner decision 10).
- **No message channel as a tool.** Nothing registers a `send_message` tool; a model
  cannot decide to message somebody. `draft_message` already exists and already
  composes without sending, and that stays the shape.
- **No storage of message content beyond the ordinary conversation rows**, which are
  excluded from snapshot capture like every other conversation.
- **No second gate, no second registry, no second dispatch path.**

## 8. What would flip if this were built

Registered before the fact, the way step 8 §7 was, because the value is that a
landing diff has a list at all:

- [`CLAUDE.md`](../CLAUDE.md) — the *"Do NOT build yet"* list loses "messaging
  channels". **DONE 2026-08-22**, when the owner answered §5 and scheduled
  phases 1–3.
- [`ROADMAP.md`](../ROADMAP.md) — status, in plain words, per phase. **The
  scheduling entry landed 2026-08-22**; per-phase status follows the builds.
- [`KNOWN-GAPS.md`](KNOWN-GAPS.md) — the pointer entry this document adds became a
  status line on 2026-08-22, and any limit in §6 that a person hits becomes its
  own row.
- [`SAFETY.md`](SAFETY.md) — G2's text gains the sentence about an inbound edge that
  is not the keyboard, and SAFE invariant 3 gains the remote view as a second example
  of a filtered view over the same registry. Both belong to that file and are written
  nowhere else.
- [`untrusted-screening-plan.md`](untrusted-screening-plan.md) — the origin list
  gains a sixth entry and decision 5's revisit note gains a second refinement.
- `docs/architecture.md` — the `rpc/` namespace list (a closed, test-enforced set)
  gains `channels`, and the Rust module description gains the two keychain commands.
- `docs/data-model.md` — ER entries for both tables, plus the capture decision in
  the notes, since coverage there is test-enforced.
- `docs/flows.md` — a flow for "a message arrives from a paired phone", since its
  RPC method names are test-enforced against `agent_core/protocol.py`.
- `docs/addison-design-doc.md` §7.10 — the always-on/managed-proxy paragraph becomes
  history with a pointer here, and §11's Phase-5 entry loses the backend it names.
- [`tests/doc_claims.py`](../tests/doc_claims.py) — at least two rows worth minting
  in the commit that makes them true: *the remote view is a subset of the SAFE view*,
  and *pairings are never restored*. Both are the kind of fact that is correct when
  written and falsified by a change three directories away, which is what a row is
  for.
