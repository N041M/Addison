"""What a G3 snapshot captures — the declared table set and the declared column
set (amendment §3, spec §4.9).

It also records which tables and columns joined capture after restore points
shipped, because a payload saved before one joined has no entry for it
(``_PAYLOAD_TABLE_SETS`` and ``_COLUMNS_JOINED_LATER``).

A leaf module on purpose: it imports nothing but ``__future__``, so both
``memory/store.py`` (which builds and applies the row image) and
``snapshot_manager.py`` (which validates a decoded payload against it) can depend
on it without either depending on the other. Every import edge stays one-way,
which is part of the unbreakability argument for the restore path.
"""

from __future__ import annotations

# The config tables a G3 snapshot captures, with the exact columns read and
# written back. Explicit column lists (never SELECT *) so a future column has to
# be added here deliberately — and so no table that could hold key material is
# reachable from this path (G1). tests/test_snapshots.py asserts that every table
# in schema.sql is either in this dict or in _EXCLUDED_TABLES below, so a new
# Phase-2 table (mcp_servers, workspace_trust, ...) cannot be silently
# un-snapshotted.
_CAPTURED_TABLES: dict[str, tuple[str, ...]] = {
    "app_settings":    ("key", "value", "updated_at"),
    "provider_config": ("provider_id", "connected", "added_at", "base_url",
                        "catalog_json", "last_check_ok", "updated_at"),
    "skills":          ("id", "name", "instructions", "enabled", "created_at"),
    "widgets":         ("id", "spec_json", "pinned", "position", "created_at",
                        "created_in_mode"),
    "routines":        ("id", "name", "description", "plan_json",
                        "created_from_conversation_id", "created_at", "updated_at",
                        "run_count", "last_run_at", "created_in_mode",
                        # Routine sharing. CAPTURED because it is part of the row's
                        # own description of itself: a restore that brought an
                        # imported routine back without its provenance would put a
                        # row on screen claiming to have been made here.
                        "imported_at"),
    # Step 7 phase 1. CAPTURED, because spec §4.12 calls an MCP server connection
    # reversible config — snapshotted, revocable, addable by prompting — and it is
    # exactly the `provider_config` shape: a name, an address, and a flag, none of
    # which can hold key material (a credential in the URL is refused at the store
    # boundary, rpc/mcp.py). It is NOT standing consent like `workspace_trust`: a
    # configured server grants Addison nothing on this machine, so a restore that
    # brings one back re-instates a setting, not a permission.
    "mcp_servers":     ("id", "name", "url", "transport", "enabled", "created_at"),
    # Step 8 phase 1. CAPTURED, on the `mcp_servers` terms: the plan's §1 calls an
    # automation reversible config — snapshotted, revocable, addable by prompting —
    # and a saved row grants Addison nothing on this machine, so a restore that
    # brings one back re-instates a DRAFT rather than a permission or a running job.
    #
    # Capturing it is safe in the one direction that matters BECAUSE of what the
    # table does not have: no armed column exists, so nothing a restore writes back
    # can claim the OS is running something (plan §5.6). Restoring cannot arm, and
    # cannot un-arm either — what launchd holds is launchd's, and the surface asks it.
    "automations":     ("id", "name", "label", "command", "schedule_kind",
                        "schedule_json", "created_in_mode", "created_at", "updated_at"),
    # Messaging channels, phase 1. CAPTURED, on the `mcp_servers` terms: a channel
    # row is reversible config — a name, a transport kind and an off switch — and it
    # grants Addison nothing on this machine, so a restore that brings one back
    # re-instates a setting rather than a permission or a running loop. `created_at`
    # joins because every other captured table's tuple carries it; `token_present` is
    # the one column left out, in _EXCLUDED_COLUMNS below, for `secret_presence`'s
    # reason. `channel_pairings` is EXCLUDED — see below; that half is the decision.
    # `on_wake` (phase 3, owner decision 8) joins the captured columns because it is
    # exactly what the decision called it: ordinary configuration, a choice somebody
    # made, restorable without asserting anything about the world outside SQLite.
    "channels":        ("id", "kind", "name", "enabled", "on_wake", "created_at"),
}

# WHICH TABLES A PAYLOAD MAY LACK (KNOWN-BUGS 16, fixed 2026-09-30).
#
# Restore points shipped on 2026-07-20 capturing the five tables in
# _FIRST_CAPTURED_TABLES. Three more joined capture later, and a payload saved before
# a table joined has no entry for it. The decoder used to require every table in
# _CAPTURED_TABLES, so each table that joined made every older restore point
# unreadable, including the permanent first one.
#
# A payload that lacks a later table restores that table as empty
# (Store.apply_config_state). The owner confirmed that rule on 2026-09-30.
# docs/SAFETY.md ("What is captured") owns it, including what it does to each of the
# three tables.
#
# The decoder accepts a payload only when its tables are exactly one of the sets a
# build has written (_PAYLOAD_TABLE_SETS). It refuses a payload that lacks one of the
# first five, one that lacks a later table while holding a table that joined after
# it, and one that holds a table this build does not know. Damage is the only thing
# that produces the first two. The third can only come from a newer build or from
# damage. The decoder before 2026-09-30 ignored such a table and applied the rest.
# Refusing it is a deliberate change, made because applying the known tables would
# put back part of a newer build's setup. It matches the refusal of a column this
# build does not know, which the decoder has always made. PAYLOAD_VERSION has been 1
# since restore points shipped, so the version check does not catch this case.
#
# To add a captured table, add it to _CAPTURED_TABLES and append it to
# _JOINED_CAPTURE_LATER in the same change. tests/test_snapshots.py fails until the
# two agree, and it restores a payload of every older shape.
_FIRST_CAPTURED_TABLES: tuple[str, ...] = (
    "app_settings", "provider_config", "skills", "widgets", "routines",
)
_JOINED_CAPTURE_LATER: tuple[str, ...] = (
    "mcp_servers",    # 2026-08-06, step 7 phase 1
    "automations",    # 2026-08-07, step 8 phase 1
    "channels",       # 2026-08-22, messaging channels phase 1
)

# Every table set a build has written into a payload, oldest first. The last is the
# set this build writes.
_PAYLOAD_TABLE_SETS: tuple[frozenset[str], ...] = tuple(
    frozenset(_FIRST_CAPTURED_TABLES + _JOINED_CAPTURE_LATER[:joined])
    for joined in range(len(_JOINED_CAPTURE_LATER) + 1)
)

# COLUMNS THAT JOINED A CAPTURED TABLE LATER, oldest first. Each entry is the table,
# the column, and the value a restore fills in when an older payload's row has no
# such key. That value is the column's schema default, because the restore inserts
# the row without the column and SQLite supplies the default.
#
# snapshot_manager._fingerprints needs this record. A restore point saved before a
# column joined was fingerprinted without it. After the walk lands on that restore
# point, the setup read back has the column in every row, so without this record the
# two never match, the walk forgets where it landed, and the next press restores the
# newest working setup, which can be the broken one (the review of KNOWN-BUGS 16).
#
# To capture a new column of a table that is already captured, add it to
# _CAPTURED_TABLES and append it here in the same change. tests/test_snapshots.py
# fails until the two agree.
_COLUMNS_JOINED_LATER: tuple[tuple[str, str, int | str | None], ...] = (
    ("routines", "imported_at", None),     # 2026-08-15, routine sharing
    ("channels", "on_wake", "decline"),    # 2026-08-22, messaging channels phase 3
)

# Deliberately NOT captured, each for a stated reason. A restore leaves all of
# these byte-identical.
_EXCLUDED_TABLES: dict[str, str] = {
    "conversations":    "transcript — append-only history, orthogonal to config (§3.1)",
    "messages":         "transcript — rollback restores config, never erases chats",
    "memory_facts":     "user-confirmed memory, not configuration",
    "usage_log":        "telemetry substrate (§4.8); rewinding it would falsify the meter",
    "action_snapshots": "the per-tool-call undo window (§4.5) — an independent mechanism",
    "routine_runs":     "run history; FK-cleaned on restore, never rewritten",
    "device_identity":  "device id; its private half lives in the keychain (G1)",
    "config_snapshots": "this table — a restore must never rewrite the way back",
    # C14, reversed during review. Live consent state, not config. Restoring it
    # could REINSTATE a grant the user had revoked since the snapshot — a
    # permission grant delivered by an ungated one-action button. Inert today
    # (nothing reads or writes this table; PermissionGate keeps grants in memory).
    # If grants ever persist, restore must INTERSECT, never replace.
    "tool_grants":      "live consent state; restoring it could re-widen permissions",
    # Step 5, D2 (inverts the v1 lean, per the tool_grants precedent above). Trust
    # is standing consent that suppresses cards inside a directory — functionally a
    # grant. Restoring a snapshot taken while a folder was trusted would RE-INSTATE
    # a trust the user has since revoked, delivered by the ungated one-action restore
    # button. So a restore never resurrects trust, and the round-1 D6 disclosure is
    # unnecessary: there is nothing to disclose.
    "workspace_trust":  "standing consent (like tool_grants); restoring it could re-trust a revoked folder",
    # Step 5.5, item 4. History, on the tool_grants precedent: a restore that
    # rewrote the record of what Addison did — or was refused — would be worse
    # than having no record. The audit trail must survive every rollback intact,
    # including a rollback performed to undo whatever the log recorded.
    "tool_audit":       "audit history; a restore must never rewrite what happened",
    # 2026-08-07, on the tool_audit precedent and for the same reason: this is the
    # record of what a provider DID, and a restore that rewrote it would erase the
    # evidence somebody is rolling back BECAUSE of. The likeliest reason to restore
    # after a provider goes wrong is the provider going wrong.
    "provider_attempts": "failure history; a restore must never rewrite what happened",
    # KNOWN-BUGS 94. On 2026-09-30 the owner chose to keep key answers for a provider
    # with no row outside captured state, for the same reason as `secret_presence`.
    # A live key read and a key rejection are observations, and a turn writes them.
    # Kept here, a turn never changes captured state, so the restore walk still
    # recognises the restore point it landed on. A restore leaves them alone, so a
    # key that is still saved keeps its provider showing as connected after a
    # restore takes the provider's row away.
    "provider_observations": "what a key read or a rejected key proved, written by turns",
    # Step 6 half A, on the `memory_facts` precedent. `widgets` IS captured — the
    # spec is configuration — but what the person has since DONE with one (a ticked
    # box, an edited note, a paused timer) is their content, not their setup.
    # Restoring a configuration must never un-tick somebody's list. Rows whose
    # widget does not survive a restore are deleted explicitly in
    # Store.apply_config_state (the routine_runs shape), because the FK would
    # otherwise abort the restore at COMMIT.
    "widget_state":     "what the person did with a widget, not configuration",
    # Messaging channels, phase 1, on the tool_grants / workspace_trust precedent and
    # for the same reason at full strength: a pairing is an AUTHORIZATION, not
    # configuration. Nothing outside SQLite holds that truth — unlike an armed
    # automation, which the OS holds and is asked for — so the row IS the
    # authorization, and a one-action restore that put one back would re-instate an
    # authorization somebody deliberately revoked. After a restore no phone is paired;
    # pairing again costs one code and one message.
    "channel_pairings": "an authorization, not config; a restore must never re-pair a revoked phone",
    # Knowledge, phase 1 (owner decision 4, 2026-08-24), on the `tool_grants`
    # precedent and for the same reason: removing a document is an act somebody
    # performed, and a one-action restore that put it back would undo that act
    # without a card — and would re-open the standing channel the plan's §4 is
    # about. The cost is stated and small: after a restore the knowledge base is
    # whatever it is now, and a document that went missing is re-added by picking
    # it again. The chunks and vectors follow the document by FK, so all three
    # tables are excluded together or the exclusion means nothing.
    "knowledge_documents":  "an attached document; a restore must never un-remove one",
    "knowledge_chunks":     "derived from a document; excluded with it",
    "knowledge_embeddings": "derived from a chunk; excluded with it",
}

# Columns of a CAPTURED table that are deliberately not captured.
# test_capture_scope_covers_every_column_of_every_captured_table compares each tuple
# above against PRAGMA table_info, so a new column is either captured or a reviewed
# line of code here — never a silent reset-to-default performed BY the recovery path.
#
# provider_config.secret_presence (plan §4.1, first entry here). It is an OBSERVATION
# with a timestamp attached to it, not configuration: it records what a keychain read
# proved at some past moment. Restoring one would assert a fortnight-old answer about
# a store the person has been editing since — the plan's own snapshot caveat, but for
# a field where the stale value is the claim itself rather than a flag beside it.
# Leaving it out means a restore resets it to the schema default, 'unknown', which is
# both the honest post-restore answer (Addison genuinely does not know any more) and
# the safe one: 'unknown' can never read as "no key saved", so no restore can route a
# turn to the external relay. The next person-driven read corrects it for free.
#
# provider_config.key_rejected_at (plan §5.2) joins it, for the same reason and one
# more. It records that a provider refused the saved key at a moment in time —
# an observation, not configuration — and it doubles as the "the person has been
# told" latch. Capturing it would make a restore able to do two wrong things: assert
# a fortnight-old rejection about a key that has been replaced since (a
# needs-attention state nothing can clear except another connect), or silence the
# notice for a key that IS revoked, because the restored row already says "told".
# Left out, a restore resets it to NULL, which is the honest post-restore answer —
# Addison no longer knows — and the next definitive rejection says so once.
#
# channels.token_present (messaging channels, phase 1) joins them, on
# `secret_presence`'s reasoning verbatim: it is an OBSERVATION about the OS keychain,
# which no snapshot touches, and a restored row asserting 'present' would claim a
# token that may have been removed since. Left out, a restore resets it to the schema
# default 'unknown' — the honest post-restore answer, and the safe one, because
# 'unknown' can never read as "a token is saved".
_EXCLUDED_COLUMNS: dict[str, tuple[str, ...]] = {
    "provider_config": ("secret_presence", "key_rejected_at"),
    "channels": ("token_present",),
}

# app_settings keys that survive a replace-all restore. One-way latches, not
# reversible config: restoring a payload that predates the flag must not un-set
# it. See Store.apply_config_state.
_PRESERVED_SETTING_KEYS: frozenset[str] = frozenset({"widgets_seeded"})
