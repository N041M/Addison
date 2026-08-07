"""What a G3 snapshot captures — the declared table set and the declared column
set (amendment §3, spec §4.9).

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
                        "run_count", "last_run_at", "created_in_mode"),
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
}

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
    # Step 6 half A, on the `memory_facts` precedent. `widgets` IS captured — the
    # spec is configuration — but what the person has since DONE with one (a ticked
    # box, an edited note, a paused timer) is their content, not their setup.
    # Restoring a configuration must never un-tick somebody's list. Rows whose
    # widget does not survive a restore are deleted explicitly in
    # Store.apply_config_state (the routine_runs shape), because the FK would
    # otherwise abort the restore at COMMIT.
    "widget_state":     "what the person did with a widget, not configuration",
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
_EXCLUDED_COLUMNS: dict[str, tuple[str, ...]] = {
    "provider_config": ("secret_presence", "key_rejected_at"),
}

# app_settings keys that survive a replace-all restore. One-way latches, not
# reversible config: restoring a payload that predates the flag must not un-set
# it. See Store.apply_config_state.
_PRESERVED_SETTING_KEYS: frozenset[str] = frozenset({"widgets_seeded"})
