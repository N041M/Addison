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
}

# Columns of a CAPTURED table that are deliberately not captured. Empty today.
# Its existence is the point: test_capture_scope_covers_every_column_of_every_
# captured_table compares each tuple above against PRAGMA table_info, so a new
# column is either captured or a reviewed line of code here — never a silent
# reset-to-default performed BY the recovery path.
_EXCLUDED_COLUMNS: dict[str, tuple[str, ...]] = {}

# app_settings keys that survive a replace-all restore. One-way latches, not
# reversible config: restoring a payload that predates the flag must not un-set
# it. See Store.apply_config_state.
_PRESERVED_SETTING_KEYS: frozenset[str] = frozenset({"widgets_seeded"})
