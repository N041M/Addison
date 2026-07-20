"""snapshot.* handlers — GLOBAL FLOOR G3, guaranteed rollback (amendment §3,
spec §4.9).

A snapshot is a point-in-time copy of Addison's mutable CONFIGURATION —
settings, providers, skills, widgets, routines. It never holds an API key (those
stay in the OS keychain, G1) and never touches the transcript. One is taken
automatically before any risky change and on command from Settings; "Restore to
the last working state" always targets the newest config a turn actually
completed against.

**These are RPC methods and never registry tools, by design.** A tool would sit
behind the PermissionGate, and a floor the gate can deny is not a floor — the
gate must be structurally incapable of refusing a restore. It would also collide
with SAFE invariant 2 (a non-LOW tool needs a real ``undo()``, and the undo of a
restore is incoherent). Nothing here is ever registered.

Store-touching, so these run on the worker thread like every other store handler
(SQLite thread affinity, see JsonRpcServer's docstring) — which also serialises a
restore behind any in-flight turn or routine run, since it replaces the config
tables wholesale.

Key casing, stated once because three layers could drift: ``Store`` returns raw
SQL column names (snake_case), ``SnapshotManager`` returns snake_case plus
``reason_label``, and THIS module is the sole camelCase mapper, at the wire
boundary, exactly like every other namespace.
"""

from __future__ import annotations

from agent_core.models_catalog import provider_label
from agent_core.profiles import resolve_active_profile
from agent_core.rpc.base import ServerContext
from agent_core.snapshots.snapshot_manager import REASONS, select_payload_to_restore

# The sticky warning shown when an automatic snapshot could not be taken. It
# persists until the user takes their own snapshot rather than clearing itself on
# the next successful auto-capture: a degraded floor that clears itself is a
# degraded floor nobody sees.
_CAPTURE_FAILED_WARNING = (
    "Addison couldn't save a restore point just now. Your older "
    "restore points are still there."
)
_CREATE_FAILED = "Addison couldn't save a restore point just now. Try again in a moment."
_NO_TARGET = "There's no saved working setup to go back to yet."


class SnapshotsMixin(ServerContext):
    # --- read -------------------------------------------------------------
    def _snapshot_list(self) -> dict:
        """snapshot.list -> every snapshot newest-first, plus what "Restore to the
        last working state" would actually do right now.

        ``lastWorking*`` are not cosmetic: the confirm step has to name the target
        BEFORE the click ("Addison as first installed" is a very different click
        from "Working setup") and has to say when the restore will move the user
        between profiles, and therefore between policy modes.

        No ``stateBlob``, no ``stateFingerprint`` and no ``binaryRef`` contents
        ever cross this boundary — ``capturesBinary`` is a boolean and that is all
        the UI needs."""
        self._ensure_built()
        payload: dict = {"snapshots": [_row_to_wire(row) for row in self.snapshot_manager.list()]}
        target = self.snapshot_manager.last_working_target()
        if target is not None:
            payload["lastWorkingId"] = target.get("id")
            payload["lastWorkingLabel"] = target.get("reason_label")
            payload["lastWorkingProfileChange"] = target.get("profile_change")
        if self._snapshot_warning is not None:
            payload["warning"] = self._snapshot_warning
        return payload

    # --- write ------------------------------------------------------------
    def _snapshot_create(self) -> dict:
        """snapshot.create -> {ok, snapshotId} | {ok:false, error}.

        The Settings "Save a restore point now" control. A successful save also clears
        the sticky warning: the user has just proved for themselves that restore
        points are being written again, so keeping the notice up would be noise."""
        self._ensure_built()
        try:
            snapshot = self.snapshot_manager.capture(trigger="on_command", reason="user_request")
        except Exception:
            return {"ok": False, "error": _CREATE_FAILED}
        self._snapshot_warning = None
        return {"ok": True, "snapshotId": snapshot.id}

    def _snapshot_restore(self, params: dict) -> dict:
        """snapshot.restore {id} -> {ok, detail?, binaryMismatch?} | {ok:false, error}."""
        self._ensure_built()
        snapshot_id = params.get("id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return {"ok": False, "error": "That restore point isn't here any more."}
        return self._finish_restore(self.snapshot_manager.restore(snapshot_id))

    def _snapshot_restore_last_working(self) -> dict:
        """snapshot.restoreLastWorking -> the one-action G3 floor. No arguments,
        by design: the floor cannot require the user to know an id."""
        self._ensure_built()
        return self._finish_restore(self.snapshot_manager.restore_last_working())

    def _snapshot_delete(self, params: dict) -> dict:
        """snapshot.delete {id} -> {ok} | {ok:false, error}. A permanent row (a G4
        anchor or genesis) is refused with a plain sentence in a SUCCESSFUL
        result, never an exception."""
        self._ensure_built()
        snapshot_id = params.get("id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return {"ok": False, "error": "That restore point isn't here any more."}
        ok, error = self.snapshot_manager.delete(snapshot_id)
        if ok:
            return {"ok": True}
        return {"ok": False, "error": error}

    # --- post-restore resync ----------------------------------------------
    def _finish_restore(self, result) -> dict:
        """Bring the LIVE session back in line with the config that was just
        restored, then render the result for the wire.

        The store is only half the session: the active profile, the router's model
        pool, the reconnect latch and the permission grants all live in memory and
        would otherwise still describe the config the user just rolled away from.
        Every step is wrapped — a resync problem must never turn a successful
        recovery into a failure."""
        if not result.ok:
            return {"ok": False, "error": result.error or _NO_TARGET}

        # (a) The profile is a restored, user-and-model-writable setting, so it can
        #     hold garbage. resolve_active_profile degrades an unknown value to
        #     SIMPLE — i.e. to SAFE mode, never to OPEN.
        try:
            self._active_profile = resolve_active_profile(self.store)
        except Exception:
            pass
        # (b) Drop the router models of providers the restored config no longer
        #     knows about (the same loop provider.disconnect runs).
        # (c) and re-arm the one-shot reconnect so the next availableRoles rebuilds
        #     the pool from the restored provider rows.
        try:
            self._resync_providers()
        except Exception:
            pass
        # (d) Grants are in-memory and a restore does not touch them, so without
        #     this the session would stay MORE permissive than the config it just
        #     rolled back to.
        try:
            self.permission_gate.revoke_all()
        except Exception:
            pass
        # (e) A restored provider row comes back saying connected, but keys are
        #     excluded from snapshots by design (G1) — so one whose keychain entry
        #     was removed since would look fine and fail every turn. Report it;
        #     do NOT rewrite `connected`, which would be permanent for providers
        #     whose keys are perfectly fine.
        detail = result.detail
        for sentence in (result.profile_change, self._keyless_provider_note()):
            if sentence:
                detail = f"{detail} {sentence}".strip()

        payload: dict = {"ok": True, "detail": detail}
        if result.snapshot_id:
            payload["snapshotId"] = result.snapshot_id
        if result.binary_mismatch:
            payload["binaryMismatch"] = result.binary_mismatch
        return payload

    def _resync_providers(self) -> None:
        """Forget the router models of providers the restored config dropped, and
        re-arm the reconnect latch for the ones it kept."""
        known = {cfg["provider_id"] for cfg in self.store.list_provider_configs()}
        for model in [m for m in self._cloud_catalog if m.provider not in known]:
            self.model_router.unregister_primary_model(model.id)
        self._cloud_catalog = [m for m in self._cloud_catalog if m.provider in known]
        self._providers_reconnected = False

    def _keyless_provider_note(self) -> str | None:
        """Name the restored providers whose stored key is gone, in plain words.
        Silent when there is no key probe wired (CLI/tests) — an unknowable state
        is not something to warn about."""
        probe = self._provider_key_probe
        if probe is None:
            return None
        missing: list[str] = []
        try:
            for cfg in self.store.list_provider_configs():
                if not cfg["connected"]:
                    continue
                if not probe(cfg["provider_id"]):
                    missing.append(provider_label(cfg["provider_id"]))
        except Exception:
            return None
        if not missing:
            return None
        names = " and ".join(missing) if len(missing) < 3 else ", ".join(missing)
        verb = "its key was" if len(missing) == 1 else "their keys were"
        return f"{names} is set up again but {verb} removed — add it in Settings to use it."

    # --- G3 hooks called from the other namespace mixins -------------------
    def _snapshot_auto(self, reason: str) -> bool:
        """G3: take the automatic snapshot before a risky or sweeping config
        change (amendment §3.2). Called from the hook sites in contract §8.
        Returns True when a restore point now exists for this change.

        NEVER RAISES — a snapshot problem must not turn a legitimate config change
        into a stack trace. But it does not swallow the outcome either: it returns
        it, because the right response depends on the change it precedes. A hook
        whose old content exists nowhere else (a delete, an in-place note
        overwrite) REFUSES on False; a recoverable one (a profile switch, a
        provider connect) proceeds with the sticky warning."""
        try:
            self.snapshot_manager.capture(trigger="auto", reason=reason)
            return True
        except Exception:
            self._snapshot_warning = _CAPTURE_FAILED_WARNING
            return False

    def _mark_verified_working(self) -> None:
        """G3: this configuration just answered a message, so it is provably
        working (amendment §3.2, "known-working marking"). Cheap and idempotent —
        the manager writes nothing when the config fingerprint is unchanged.
        Swallows everything: a failure here must never convert a successful turn
        into an error."""
        try:
            self.snapshot_manager.mark_verified_working()
        except Exception:
            pass


def snapshot_list_from_payloads(payloads: list[dict]) -> dict:
    """A ``snapshot.list`` payload built from sidecar files alone, with no Store.

    The cold-start read (main.py's store-free job path): when the database will
    not open, the user must still be able to SEE what they can go back to before
    they commit to going back to it. Every field comes out of each payload's
    ``meta`` block, which is exactly why ``meta`` carries the row's flags.

    ``lastWorking*`` is chosen by ``select_payload_to_restore`` — the SAME
    function the cold-start rebuild in ``main.py`` uses to pick what it applies,
    and the same one the manager's sidecar arm uses. That is the whole point of
    there being one function: this payload NAMES a restore point in the confirm
    step, and if the button then applied a different one, the naming would be
    worse than useless. It used to: this picked the newest VERIFIED payload while
    the rebuild took the newest payload of any kind, so in the one degraded path
    the floor exists for, the card said "Working setup" and the click handed back
    the configuration the user was trying to escape.

    Deliberately read-only — nothing here renames, rebuilds or writes anything. A
    list is a look, and a look must never cost the user their database file."""
    rows: list[dict] = []
    for payload in payloads:
        meta = payload.get("meta")
        if not isinstance(meta, dict) or not isinstance(meta.get("id"), str):
            continue
        reason = meta.get("reason")
        row = _row_to_wire(
            {
                "id": meta.get("id"),
                "created_at": payload.get("captured_at"),
                "trigger": meta.get("trigger"),
                "reason": reason,
                "reason_label": _reason_label(reason),
                "verified_working": meta.get("verified_working"),
                "undeletable": meta.get("undeletable"),
                "captures_binary": meta.get("captures_binary"),
                "created_in_mode": meta.get("created_in_mode"),
            }
        )
        rows.append(row)
    out: dict = {"snapshots": rows}
    target, _is_verified = select_payload_to_restore(payloads)
    if target is not None:
        meta = target.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        out["lastWorkingLabel"] = _reason_label(meta.get("reason"))
        target_id = meta.get("id")
        if isinstance(target_id, str) and target_id:
            out["lastWorkingId"] = target_id
        # The label is set even when the id is not, because the label is what the
        # confirm step shows and the restore would go ahead with this payload
        # either way. An id that is missing means the row is not in the list
        # above either, so there is nothing for the card to point at — the two
        # sides still describe the same payload, which is the property that
        # matters.
        #
        # The profile comparison needs the LIVE config, and there is no readable
        # config in this state — so say nothing rather than guess.
        out["lastWorkingProfileChange"] = None
    return out


def _reason_label(reason) -> str:
    """The plain-language name for a reason slug, tolerating anything at all —
    these come off disk in the one situation where nothing else is trustworthy."""
    if isinstance(reason, str) and reason in REASONS:
        return REASONS[reason]
    return REASONS["other"]


def _row_to_wire(row: dict) -> dict:
    """One manager row -> its camelCase wire shape. The omissions are deliberate:
    the fingerprint, the payload version and the binary reference are internal
    machinery, and the blob never leaves the database at all."""
    return {
        "id": row.get("id"),
        "createdAt": row.get("created_at"),
        "trigger": row.get("trigger"),
        "reason": row.get("reason"),
        "reasonLabel": row.get("reason_label"),
        "verifiedWorking": bool(row.get("verified_working")),
        "undeletable": bool(row.get("undeletable")),
        "capturesBinary": bool(row.get("captures_binary")),
        "createdInMode": row.get("created_in_mode"),
    }
