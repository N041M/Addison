# Secrets and the keychain — the plan

**Status: PROPOSED, not scheduled.** Drafted 2026-07-31 as a ground-up vault
redesign; **revised the same day to a repair-first plan** after a six-lens
adversarial review (60 findings), two live spikes, and two verifications that
undercut the rewrite's own justification. §15 records what changed and why.
Owner decisions: §14. ROADMAP owns scheduling.

**The recommendation in one line: repair the existing integration — the three
changes in §4 kill every measured symptom — and keep the encrypted vault as a
documented destination with named triggers, not as the next step.**

---

## 1. Requirements

Owner, 2026-07-31:

- **R1 — plain "Allow" must be enough.** One press works for the session; no
  second popup, even where *Always Allow* would also have solved it.
- **R2 — "Always Allow" must work**, across launches *and rebuilds*.
- **R3 — at most ONE popup per app start.**

Derived, from this repo's standards:

- **R4 — zero popups in the steady state.** One is the ceiling, not the target.
- **R5 — a dialog, when one must appear, is explained first and anchored to a
  deliberate act**; a declined dialog leaves the app in a state that says so —
  never the 74 seconds of silent keylessness the 07-31 trace caught.
- **R6 — every floor holds.** G1 (keys never in the webview or SQLite, read at
  the moment of use), G3's spirit (nothing resurrects a deleted key), the
  module-boundary rule, the three-process trust gradient.

**Scope honesty, up front: popups are not 1:1 with OS accesses.** The 07-31
trace showed one `SecKeychainFindGenericPassword` blocking until *two* dialogs
were answered — macOS's ACL + partition sequence against a foreign item. No
app-side design can promise a popup count the OS attaches to a single access.
What is guaranteeable is **at most one promptable OS access per session per
item, and zero for items the app owns**. R1/R3 therefore hold absolutely in the
app's normal life and degrade to one explained sequence in the foreign-item
edges — which §4.2's self-heal actively converges back to zero.

## 2. What the current system taught

`keychain.rs` is 1,101 lines (438 code, 663 comment — and those comments are a
ledger of bugs already paid for), 6 statics, 40 tests, plus nine core-side
probe/getter symbols and a 342-line failure-mode test file.

1. **The root cause of every popup was identity, not access.** macOS keys an
   item's ACL to the requesting app's code signature; the ad-hoc dev binary
   changed identity every rebuild. Mitigated 07-31 by `sign-and-run.sh`.
2. **One access can raise two dialogs** — ACL + partition, live-traced. Caching
   cannot fix a count the OS attaches to one access.
3. **Presence was asked of the secret store.** A "connected" dot polled the OS
   every 60 s per provider; that one decision generated `KEY_CACHE`,
   `FAILED_READS`, the never-cache-`nothing-saved` rule, and three probe
   variants. **This is the single largest source of the integration's size.**
4. **The three-way outcome is sacred.** *Found* / *nothing saved* /
   *unreadable* must never collapse — collapsing is what silently routed a
   message to the external relay (07-25).
5. **A dismissed dialog must not cost the session silently.**
6. **Dialogs must be serialized and re-checked under the lock.**
7. **Orphaned items resurrect** (`provider-key:primary`).
8. **A trace with hard no-secret rules is worth its weight** — it stays, and
   §5.6 promotes part of it into a shipped diagnostic.
9. **`keyring` hid what mattered** *at diagnosis time* — no attributes-only
   query, no ACL control, and it silently chose the legacy SecKeychain API.
   (§4.4 keeps it anyway, on measured terms.)
10. **(Spike 1, 07-31.) Under a self-signed identity, creator trust does not
    survive a rebuild.** An item created by an "Addison Dev"-signed binary read
    back silently from that binary (29 ms) and **prompted from a rebuilt binary
    signed with the same certificate**. Without a team ID there is no stable
    partition for trust to anchor to, so the Chrome/VS Code zero-prompt steady
    state needs an **Apple-issued identity** (Phase 3). Dev floor: silent across
    relaunches, one sequence per *rebuild* — matching the owner's own report.
11. **(Spike 2, 07-31.) The data-protection keychain is measured, not assumed:**
    `kSecUseDataProtectionKeychain` fails `-34018 errSecMissingEntitlement`
    under current signing. The Phase-3 deferral now has an error code.

## 3. The decision: repair, don't replace

The first draft proposed replacing all of this with one keychain item holding a
master key plus an encrypted vault file (the Chrome Safe Storage pattern). Two
verifications on 2026-07-31 undercut its own case:

- **A non-secret home for presence already exists.** `provider_config`
  (`schema.sql`) is in the tree with a `connected` column and the comment *"API
  keys are NEVER stored here. This table only holds non-secret connection
  metadata."* The vault's manifest was invented to answer a question the schema
  already answers.
- **`keyring` already covers four platforms** — its backends are `macos.rs`,
  `ios.rs`, `windows.rs`, `secret_service.rs`, `keyutils.rs`. Only **Android**
  is missing. The "you only need a store-one-master-key primitive per OS"
  portability argument was overstated.

Mapping symptoms to causes settles it:

| Symptom | Cause | Fix | Needs a vault? |
|---|---|---|---|
| Two dialogs per read | foreign item | stable signing | no — shipped |
| Dialogs return after each rebuild | foreign item | signing + **self-heal** (§4.2) | no |
| 8 OS reads in 20 ms, forever, on a 60 s timer | presence asked of the OS | **presence in `provider_config`** (§4.1) | no |
| 1,500 lines, 9 symbols, 6 statics | consequence of the above | the above | no |

Not one measured problem requires an encrypted file. The vault remains the
better *destination* — §10 keeps it, with the triggers that would justify the
trip — but going now would spend weeks and discard 663 lines of hard-won
annotations to buy advantages that mostly matter inside a window self-heal
closes, while adding failure modes (corrupt envelope, torn write, stale backup,
joint loss) that do not exist today.

**Nothing here is wasted if the vault ever happens.** The `CredentialStore`
seam, `Intent`, and presence-off-the-OS are prerequisites either way; the vault
then adds an envelope, a file layer, and a migration.

## 4. The three core changes

### 4.1 Presence leaves the keychain

`provider_config.connected` (already in the schema, already non-secret) becomes
the authority for *"is a key saved for this provider?"* Written on
store/delete/connect; read by everything that renders a dot, gates routing, or
answers `provider.list` / `stats.get` / `availableRoles`.

**Deleted by this alone:** `KEY_CACHE`-as-presence, `FAILED_READS` and its
never-cache-`nothing-saved` rule, the 60-second OS poll, and three of the nine
core probes (68 references in `keychain.rs`, plus most of its 40 tests, exist to
make OS-polling survivable).

**Presence stays three-way** (lesson 4) — `present | absent | unknown` —
because two things can make it unanswerable: the store read fails, or
reconciliation (§5.1) finds the row and the keychain disagreeing. `unknown` must
**never** read as "no key": that is the 07-25 relay-routing bug, and the router
may reach the Setup Assistant relay only on `absent`, never on `unknown`.

**Snapshot caveat, stated because it is real:** `provider_config` *is*
snapshot-captured (`snapshots/scope.py`), so restoring an old snapshot can
resurrect a stale `connected = 1` for a provider whose key was since deleted.
That resurrects a **flag, never a key** — G3's letter holds — and §5.1
reconciles it away promptlessly at the next launch. The alternative (excluding
the column) would make a restored config claim *fewer* connections than exist,
which is the worse lie.

### 4.2 Self-heal: repair a foreign item by re-creating it

A keychain item's ACL — the list of apps that may read it without asking — is
minted **at creation**, with the creating app on it. That is why creating never
prompts, reading your own item never prompts, and reading someone else's always
does. An item goes *foreign* when a previous build made it, when a keychain is
restored to a new Mac, or when the signing identity rotates.

You cannot repair an ACL by writing to the item. You can by **deleting it and
adding it back**. So: after any successful read of a foreign item, immediately
delete and re-create it with the same bytes — both operations promptless for the
item we now own. "One dialog every session, forever" becomes "one dialog, once."

**Corollary — the trap.** `security-framework`'s `set_password_internal` does:

```rust
let status = SecItemAdd(...);
if status == errSecDuplicateItem { SecItemUpdate(query, update) }   // preserves the item AND its ACL
```

So the convenient write path can *never* heal anything: it rewrites the value
under the old foreign ACL, and a user's instinct ("I'll just save it again")
accomplishes nothing. **Every write of a credential item is delete-then-add,
explicitly.** §5.4 keeps that from causing needless churn.

Applies to provider keys and the device-identity item alike.

**Honest limit:** spike 1 showed that under the self-signed dev cert even an
app-created item prompts after a rebuild, so on dev builds self-heal resets the
clock to the next rebuild rather than "once ever". With an Apple-issued identity
it is once ever.

### 4.3 `Intent` replaces the probe zoo

```rust
pub enum Intent {
    /// The person acted. May perform the promptable read; may retry past an
    /// earlier decline this session.
    UserAction,
    /// A poll, a launch task, anything without a person behind it.
    /// NEVER touches the OS.
    Background,
}
```

One function carries it, so *"will this prompt?"* is readable at every call site
instead of inferable from a docstring. Core-side, nine symbols become three:
`secret_presence(provider) -> present|absent|unknown`,
`get_secret(provider, *, intent)`, and `relay_signing()`. `_primary_key_status`
(the ninth, missed by the first draft) maps: `ready` = present, `missing` =
absent, `unreadable` = unknown **or** a send-time Locked/Unavailable —
`rpc/conversation.py` keeps its refusal branch byte-for-byte.

**Caller inventory** — the reason `Intent` earns a place rather than being a
source-level rule:

| Call site | Needs | Intent |
|---|---|---|
| turn send (`anthropic_provider._resolve_key` et al.) | value | UserAction |
| `provider.connect` validation | value | UserAction |
| relay signing | identity | UserAction |
| `_maybe_load_live_catalog` (`rpc/models.py:40`) | value | **Background** |
| `_maybe_reconnect_saved_providers` (`rpc/models.py:68`) | values | **Background** |
| provider.list / stats.get / availableRoles presence | presence row | (no secret call) |

The two Background consumers were missed by the first draft and are a real
blocker: both **re-arm on the first successful read** (the shell notifies the
core), and the reconnect one-shot latch is set only after a sweep that actually
ran with a readable store. Until then the fallback catalog serves and providers
show as saved-but-idle — never a dialog from a poll.

### 4.4 What stays, deliberately

`keyring` **stays** — but on measured terms, not by default. Lesson 9's missing
knobs were *diagnosis-time* needs and the diagnosis is done; at runtime it does
read/write/delete correctly on four platforms. Two things get added beside it in
`keychain.rs`, because the crate cannot express them:

- an **attributes-only presence probe** (`ItemSearchOptions` with
  `load_attributes(true)`, `load_data(false)`) — verified live to raise **no
  dialog**, and the mechanism §5.1 runs on;
- **delete-then-add writes** (§4.2), bypassing the duplicate→update fallback.

Also unchanged: the three-way outcome on every wire, the OS lock and its
double-checked re-read, per-call key delivery with nothing retained core-side,
the webview's write-only surface, and the trace.

## 5. New — things this thread had not considered

Each is small; each closes something real that the vault plan also missed.

### 5.1 Promptless reconciliation at launch

Attributes-only queries never prompt (verified). So at every launch, compare
`provider_config.connected` against what the keychain actually holds — **zero
dialogs, a few milliseconds** — and surface disagreement *before* the person
sends a message:

- row says connected, item absent → the key was deleted outside Addison
  (Keychain Access, a restored config snapshot, §4.1). Mark `unknown`, show
  "Addison can't find your saved key for X — add it again."
- row says absent, item present → an orphan from a failed delete (lesson 7).
  Offer to clean it up; never read it.

This is the health check the current design has no room for, because today the
only way to ask is a promptable read. It also makes §4.1's snapshot caveat
self-correcting.

### 5.2 A rejected key must change something

**Today a 401 does nothing to stored state.** `ProviderAuthError` exists and the
routing chain handles it, but nothing touches `provider_config.last_check_ok`
and nothing tells the person their key stopped working — so a revoked or expired
key fails every turn, forever, with a per-turn error and no path forward. That
is the most likely real-world failure of the whole subsystem, and no part of
this thread had looked at it.

On a **definitive** auth failure (401/403 — never a 429, never a network error),
mark the provider `needs attention`, surface one plain line ("X rejected
Addison's key — it may have been revoked. Add a new one in Settings."), and let
routing degrade to another connected provider exactly as it does for an
unavailable one. Idempotent: repeated 401s do not re-notify.

### 5.3 Normalise a key at the store boundary

`SettingsPage.tsx` does `key.trim()` — but only there, and `.trim()` is the
frontend's courtesy, not a contract. The Rust store path takes the string as-is,
so any other route (or one frontend regression) can persist a key with a
trailing newline — the classic paste bug that produces an auth failure
indistinguishable from a wrong key.

Normalise **where it is stored**: strip surrounding whitespace, reject embedded
newlines and control/zero-width characters with a plain sentence ("That key has
a line break in it — paste it again as one line"). Cheap, defence in depth, and
it converts a mystifying 401 into a fixable message.

### 5.4 Idempotent writes — don't churn the ACL

Since every write is now delete-then-add (§4.2), a naive "Save" on an unchanged
value would needlessly destroy and re-mint an item. Read-compare-then-write: if
the stored value is byte-identical, do nothing. (The comparison is a read we may
already have cached; it never adds a promptable access on the healthy path.)

### 5.5 Decline backoff — never a dialog the person did not ask for

A decline is remembered for the session (today's `FAILED_READS` behaviour,
preserved) — but the retry signal must be an explicit act, not any incidental
UserAction. A person who declines and then types is not asking again. Retry is
offered by the click-anchored card (§6), never re-raised by a keystroke, a poll,
or a re-render.

### 5.6 Ship the diagnostic that found this bug

`ADDISON_KEYCHAIN_TRACE` is dev-only and it is what cracked the original
mystery. Promote the *count* — not the trace — into Settings → diagnostics:
**"Keychain reads this session: N"**. In the healthy steady state that reads 0
or 1 forever; anything else is the regression, visible without a terminal, from
a user's screenshot. The full trace stays behind the env var with its no-secret
rules (variant words only, never a value, a length, or a prefix).

### 5.7 Two honest gaps, written down rather than fixed

- **G1 stops at the process boundary, by design.** Verified this session: a
  keychain response goes to the core's stdin and is *never* relayed to the
  webview (`agent_process.rs` emits `core-message` only for non-shell-bound
  frames; a dropped keychain response logs a message, not a payload). But once
  the value reaches Python it is a `str`: **Python cannot zeroize**, so a key
  lingers in that process's heap until GC. The shell zeroizes; the core cannot.
  Mitigation is lifetime, not erasure — fetch at the moment of use, hold in a
  local, never log. State it in SAFETY.md rather than implying the whole path is
  scrubbed.
- **A locked login keychain still prompts** — the one case even an app-owned
  item raises a dialog. Yours is `no-timeout` (checked), so it does not arise
  here, but it belongs in the failure matrix instead of being discovered later.

## 6. Dialog policy

- **Launch: zero promptable OS touches.** Presence is a SQLite read;
  reconciliation (§5.1) is attributes-only. The test asserts exactly that —
  *zero promptable* touches, with attrs-only probes permitted and counted.
- **The promptable read is click-anchored.** When a read will plausibly prompt
  (foreign item, prior decline, or the shell detects itself ad-hoc signed), the
  UI shows an explained card and **its button performs the read**. A password
  sheet never lands on top of a person's first message with a 600 s budget
  behind it — the worst timing for personas 54 and 68.
- **Steady state:** zero dialogs with an Apple-issued identity; with the dev
  cert, spike 1's measured floor — silent across relaunches, one sequence per
  rebuild. `sign-and-run.sh` fails open, so the shell **detects its own signing
  state** and the card says plainly *"Always Allow won't stick on this build"*
  rather than letting dialogs recur unexplained.
- **Declined:** remembered for the session; a persistent, non-dismissible row in
  the Settings provider card plus a chat notice, carried to the webview on the
  existing `provider.list`/`stats.get` responses (no new event channel).
  Background callers resolve instantly. Retry only from the card (§5.5).
- **Timeout:** a timed-out read leaves state **unchanged** — nobody declined,
  nothing failed. The parked OS read keeps waiting; a late *Allow* is **banked
  into the session** so the next message just works. Callers get "macOS is still
  asking for your password — answer that window first", never Declined. A retry
  **attaches to the pending read**; a second access is never issued while one is
  parked.

**Copy table** — one user-facing noun throughout: *"your computer's keychain"*
(the shipped strings' wording).

| State | Chat turn | Settings row |
|---|---|---|
| Declined | "Your Mac didn't let Addison read your saved key, so this message wasn't sent. Try again to let it ask once more." | "Can't reach your saved key — try again" |
| Still waiting | "macOS is still asking for your password — answer that window first." | same |
| Unavailable | "Addison couldn't reach your computer's keychain. Locking and unlocking your Mac usually fixes this." (raw OS text → trace only) | same |
| Absent | key card | "No key saved" |
| Unknown (§5.1) | "Addison can't find your saved key for X — add it again in Settings." | needs-attention row |
| Rejected (§5.2) | "X rejected Addison's key — it may have been revoked. Add a new one in Settings." | needs-attention row |

## 7. The device identity stays its own item

**It is the one secret nobody can re-type.** Provider keys came from a website
and can be re-obtained; the device identity is generated by Addison, never
shown, and used to sign Setup Assistant relay requests so the relay recognises
the machine. Lose it and there is nothing to restore from — and the app cannot
even tell: it would mint a fresh one and the relay would see a brand-new device.

Today's code already refuses that (`keychain.rs::from_stored`: *"Errors (rather
than regenerating) on a missing or corrupt blob, so a load never silently
rotates the device's identity"*). It keeps its own item, gets §4.2's self-heal,
and is never folded in with the provider keys — which is also why the vault's
joint-loss trade (§10) would only ever cover re-typeable secrets.

## 8. Failure matrix

| Failure | Behaviour |
|---|---|
| Decline | remembered for the session; copy table; retry only from the card |
| Timeout | state unchanged; late *Allow* banked; retry attaches, never double-reads |
| Foreign item (restored Mac / rotation / dev rebuild) | one explained sequence; **self-healed on success** (§4.2) |
| Key deleted outside Addison | caught promptlessly at launch (§5.1) → `unknown` + needs-attention, never "no key" |
| Orphan item, no config row | surfaced by reconciliation; offered for cleanup; never read |
| Key revoked at the provider | 401 → needs-attention + plain sentence (§5.2); routing degrades |
| Key pasted with a line break | refused at the store boundary with a fixable message (§5.3) |
| Presence unreadable | `unknown` → unreadable-key sentence; **never** the relay |
| Store while declined | fails with the *keychain* sentence, never "that key doesn't work" |
| Login keychain locked | the one case an app-owned item prompts; plain sentence (§5.7) |
| Stale `connected` from a restored snapshot | flag only, never a key; reconciled away at launch (§4.1) |
| Shell-less dev run | `ANTHROPIC_API_KEY` env fallback, unchanged |
| Keychain wholly unavailable | plain sentence; nothing routes off-machine |

## 9. Cross-platform

`keyring` already ships `macos.rs`, `ios.rs`, `windows.rs`, `secret_service.rs`,
`keyutils.rs`, and Cargo.toml already feature-selects per target. So the repair
plan is portable **today** on macOS, Windows, Linux and iOS; the per-platform
work is only the two things keyring cannot express (§4.4), and both degrade
safely.

| Platform | Store | Dialogs | Attrs-only presence | Self-heal |
|---|---|---|---|---|
| **macOS** | login keychain | 0 (Apple ID) / per-rebuild (dev cert) | `SecItem*`, verified | needed, implemented |
| **Windows** | Credential Manager (DPAPI-backed) | **0 — no credential-ACL prompt mechanism exists** | `CredEnumerate` metadata | not needed (no ACL) |
| **Linux** | Secret Service | 0 with a login-unlocked collection; ≤1 collection unlock/session | attribute search without secret | not needed |
| **iOS** | data-protection keychain, app-scoped by entitlement | **0 — the mechanism does not exist** | same crate | not needed |
| **Android** | **gap** — keyring has no backend | n/a | n/a | n/a |

Notes worth carrying: Windows DPAPI is user-scoped, so any process running as
the user can unwrap it (the infostealer class; Chrome's app-bound fix needs a
SYSTEM service, out of scope for a no-service desktop app) — and there is no
seatbelt equivalent yet, so step 5.5's "a sandboxed command gets ciphertext
only" property is macOS-only. **Android is the one platform that needs new work
either way**, and it is the platform where the vault shape genuinely wins (§10):
a Keystore-wrapped, non-exportable master key over an encrypted file, behind a
small Kotlin plugin. iOS and Android both need Tauri mobile targets the product
has not adopted; a path provider replacing the hardcoded `~/.addison` is the
shared prerequisite.

## 10. The vault — kept as a destination, with triggers

The encrypted-file design (one app-created master item; secrets in
`secrets.vault`; a manifest for presence) is the right end state. **Its detailed
specification is NOT preserved** — the draft lived only in this file, which was
renamed and rewritten while untracked, so nothing in git holds it. Rebuilding it
means re-deriving the module layout, envelope format and migration state
machine. What must NOT be re-derived is the scrutiny that hardened it, so the
traps are recorded here (§10.1); everything else in that draft was ordinary
design work.

What it buys that §4 does not:

- **one unlock for a multi-provider turn** (a fallback chain reads two keys);
- **a free slot for every future secret** — step 7's MCP server tokens grow the
  item count, and therefore the *foreign*-item exposure, one per server;
- **one sequence, not N, at Phase-3 identity rotation** — when the dev cert
  becomes a Developer ID every item goes foreign at once (self-heal spreads the
  cost across first-use rather than a burst, but N items is N sequences);
- **Android**, where a wrapped non-exportable master key is the natural shape.

**Build it when a trigger fires**, not before: step 7 lands more than a couple of
stored tokens; Android becomes real; or the Phase-3 rotation burst measurably
hurts. Its cost is unchanged and should be re-read before committing: ~700 new
lines plus crypto, a migration state machine, and four failure modes that do not
exist today (corrupt envelope, torn write, stale backup, joint loss).

### 10.1 The traps a vault build must re-fix

These cost a six-lens review to find, and every one was a *specification* gap
that read as fine on the page. They are not implied by the idea; a fresh draft
will contain them again unless it is checked against this list.

1. **Mint the master keychain item BEFORE writing the vault file.** The reverse
   order leaves — on interruption — a vault sealed under a key that existed only
   in memory, which makes `migration_needed` permanently false while every real
   key still sits in legacy items. Detection must therefore be
   `any_legacy_exists() ∧ migration != Done`, evaluated *regardless* of whether
   a vault file exists (a keychain restored after a vault is created would
   otherwise be invisible forever).
2. **Migration reads must be all-or-nothing.** One declined item mid-batch,
   followed by writing the vault anyway, silently converts "saved but unread"
   into "nothing saved" — lesson 4's collapse, through a new door.
3. **While migration is Pending, deleting a secret must delete its legacy
   counterpart too**, or a deliberately deleted key is resurrected by a later
   re-migration. This is the 07-25 fix; a rewrite drops it by omission.
4. **A one-generation `.bak` resurrects deleted secrets.** A delete is a
   rewrite, so the backup still holds the deleted key; a torn write then
   restores it. Deletes must rewrite both generations, and the envelope needs a
   random **lineage id** so a backup from a pre-re-mint vault reports "older
   vault" rather than "tampered".
5. **Presence read from an unauthenticated manifest must stay three-way.** A
   corrupt or tampered manifest answering "no key" is the relay-misroute again —
   see §4.1's `unknown` rule, which is the same defence in the repair plan.
6. **Every mutation holds one lock across read-decrypt-modify-seal-write.** Two
   rapid stores are otherwise a silent read-modify-write race that loses a key.
7. **Zeroize the whole decrypted map, not just the master key and the returned
   value** — opening the envelope materialises every secret, so reading one key
   leaves plaintext copies of the others in freed heap.
8. **Keep the device identity out of it** (§7) — the reasoning is identical and
   already applies to the repair plan.

## 11. Explored and rejected

- **Move HTTPS into the shell** so a key never crosses into Python at all — the
  strongest possible G1 upgrade (it would close §5.7's zeroization gap
  outright). Rejected: the orchestrator is provider-agnostic by design and owns
  retries, streaming, tool-call translation and routing; moving HTTP to Rust
  means re-implementing four provider translators **in the highest-trust
  process** — a far larger attack surface there, to defend against an attacker
  who already has the core. Revisit only if the core's trust level changes.
- **Touch ID / `LAContext` gating.** Adds a prompt to a project whose goal is
  removing them. Reject for v1; a plausible opt-in "extra protection" setting
  later, never a default, never a floor.
- **A passphrase-derived key.** The login session already gates the keychain; a
  passphrase is a second thing to lose and a second thing to ask for.
- **Encrypted keys in SQLite under a keychain-held key.** That *is* the vault
  (§10), reached by another name.
- **Never storing keys (ask each session).** Hostile to personas 54 and 68.
- **A "forget everything" panic action** (delete all items + clear config).
  Genuinely cheap and arguably a butler-appropriate affordance — not rejected,
  deferred as its own small product decision rather than smuggled in here.

## 12. Tests

New: `launch_makes_zero_promptable_os_touches` (panicking fake, full RPC boot,
100× polls; attrs-only permitted and counted),
`background_intent_never_touches_the_os`,
`a_session_never_makes_a_second_promptable_os_access` (counting fake),
`presence_is_answered_without_touching_the_os`,
`unknown_presence_never_reads_as_no_key`,
`a_foreign_item_is_re_created_after_a_successful_read`,
`a_write_is_never_an_update_in_place`,
`reconciliation_at_launch_raises_no_dialog`,
`a_401_marks_the_provider_and_a_429_does_not`,
`a_key_with_a_line_break_is_refused_at_the_store_boundary`,
`an_unchanged_save_does_not_re_create_the_item`,
`a_declined_read_is_retried_only_from_the_card`,
`a_timed_out_read_neither_declines_nor_double_reads`,
`a_restored_snapshot_never_resurrects_a_key_only_a_flag`.

Kept and re-pointed rather than rewritten — **the repair plan's biggest
advantage over the rewrite**: `tests/test_keychain_read_failures.py` (342 lines
of relay-privacy assertions) and `tests/test_shell_bridge.py` survive with their
properties intact, because the wire methods and the three-way seam do not
change. Only the probe monkeypatches move to `secret_presence`. The ~40
`keychain.rs` characterisation tests shrink with the machinery they characterise
(`FAILED_READS`, presence caching); the OS-lock, migration and G1 leak-sweep
tests stay.

Docs to sweep in the same step (one-owner rule): CLAUDE.md's Multi-provider
paragraph, SAFETY.md (G1 wording + §5.7's Python honesty), flows.md,
architecture.md, data-model.md, VERIFICATION.md (+ a per-release manual
zero-dialog line), TESTING-CHECKLIST.md, CONVENTIONS.md.

## 13. Build order

Each step lands green and is independently useful:

1. **Presence to `provider_config`** + three-way `secret_presence` + the routing
   rule. Deletes the poll, `FAILED_READS`, and most of the probe zoo. *Biggest
   win, smallest risk, no OS-behaviour dependency.*
2. **Self-heal + delete-then-add + idempotent writes** (§4.2, §5.4). *Closes the
   reported bug class permanently.*
3. **`Intent`** + the unlock re-arm for the two Background consumers (§4.3).
4. **Reconciliation, 401 handling, key normalisation, the read counter**
   (§5.1–5.3, §5.6) — product-facing, independent of each other.
5. **Cards + copy table + ad-hoc detection** (§6); docs sweep (§12).

## 14. Owner decisions

1. **Repair vs replace** — this plan recommends repair (§3); the vault stays
   documented with triggers (§10).
2. **The `provider_config` snapshot caveat** (§4.1): reconcile-and-correct as
   planned, or exclude the presence column? Plan says reconcile.
3. **A 401 marking a provider needs-attention** (§5.2) — new behaviour; changes
   what routing does with a revoked key. In or out?
4. **The shipped read counter** (§5.6) — Settings diagnostics line, or keep the
   trace dev-only?
5. **Phase 3**: Apple-issued identity (the only thing that makes R4 absolute),
   data-protection keychain (precondition measured, spike 2), Secure Enclave for
   the device identity (P-256 — a relay-contract change).
6. **A one-click experiment only you can run:** press **Always Allow** once on
   the dev build, rebuild, relaunch. No dialog ⇒ the partition edit is durable
   under the self-signed cert and the dev floor improves from per-rebuild to
   once-ever. Either result belongs in BUILD-LOG.

## 15. Record

**Scrutiny (six lenses, 16 agents, 60 findings: 7 blockers, 29 serious).** Fixed
here: the R1/R3 popup-vs-access overclaim (four lenses independently); three-way
presence and the relay-routing rule; the two missed Background consumers; the
device-identity carve-out (the review's top regret-risk); the self-heal
mechanism and the update-in-place trap; timeout semantics; decline retry;
`_primary_key_status`; the test and doc ledgers; the copy table; the
click-anchored card. Findings that applied only to the vault's envelope,
migration state machine and backup lifecycle went with it to §10 — resolved in
the shelved design, not lost.

**Spikes (07-31).** Spike 1 falsified the rewrite's central claim for
self-signed builds; spike 2 measured the data-protection precondition
(`-34018`). Both in BUILD-LOG with commands and output.

**Verifications this session.** Attributes-only queries raise no dialog;
`security-framework` 3.7.0 supports the whole surface; the step-5.5 seatbelt
denies `mach-lookup`, so a sandboxed command cannot reach `securityd`; keychain
responses never reach the webview; `provider_config` already exists as a
non-secret presence home; keyring already covers four platforms; a 401 currently
changes nothing; the key is trimmed only in the frontend.
