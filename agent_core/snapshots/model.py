"""Dataclasses for the G3 snapshot subsystem (amendment §3, spec §4.9).

Mirrors ``config_snapshots`` 1:1. NOT related to ``ActionSnapshot``
(tools/base.py), which is one tool call's undo payload — see the table comment
in schema.sql for the distinction.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfigSnapshot:
    """One app-state snapshot — mirrors the ``config_snapshots`` table 1:1."""

    id: str
    created_at: int
    trigger: str                  # 'auto' | 'on_command'
    reason: str                   # closed slug, see snapshot_manager.REASONS
    payload_version: int
    state_blob: str               # JSON row-image; decoded by the manager, never here
    state_fingerprint: str        # sha256 of the canonical blob, timestamps excluded
    verified_working: bool = False
    undeletable: bool = False     # permanent (G4 anchor OR genesis) — the DB refuses
    # to delete it; see schema.sql's two triggers
    captures_binary: bool = False
    binary_ref: str | None = None  # JSON build reference, never bytes, never a path
    created_in_mode: str = "safe"  # DISPLAY ONLY — never filters a query (G3)


@dataclass
class RestoreResult:
    """What a restore did, in terms the UI can render verbatim."""

    ok: bool
    snapshot_id: str | None = None
    detail: str = ""              # plain-language, user-facing, no jargon
    error: str | None = None      # plain-language reason when ok is False
    binary_mismatch: str | None = None  # plain note when the anchor's build differs
    profile_change: str | None = None   # plain note when the restore moved the user
    # between profiles (and therefore modes)
    # NOTE: the keyless-provider names live nowhere on this dataclass — rpc/snapshots.py
    # (_keyless_provider_note) probes the keychain itself and appends the sentence to
    # `detail`. A field here would be a second, never-written source of truth (dropped
    # 2026-07-24, step 2; the HANDOFF loose end resolved to "drop").
