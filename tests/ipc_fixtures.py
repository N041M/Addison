"""Shared IPC payload fixtures — the cross-process contract artifact (§9 spirit).

The frontend's defensive parsers (shell/src/lib/parse.ts, shell/src/ipc/client.ts)
are the only thing standing between a shifted core payload and a crashed webview,
and until now their tests used hand-written copies of the core's shapes. This
module generates the REAL payloads by calling the same handler methods the
JSON-RPC dispatch uses, and writes them to shell/src/__tests__/fixtures/*.json —
one artifact both sides share:

- tests/test_ipc_fixture_drift.py regenerates live and fails if a handler's
  shape drifts from the committed files (regenerate: ``python tests/ipc_fixtures.py``
  from the repo root, then re-run the vitest suite);
- the vitest suite (shell/src/__tests__/parsers.fixtures.test.ts) parses the
  same files and pins the parsed output.

So a core change that would break the frontend parsers fails CI on whichever
side runs first — the method-name drift test covers *names*, this covers *shapes*.

Determinism: usage rows use fixed year-2100 epoch timestamps (far inside any
future "this month" window for ``usage_totals_since``), so ``checkedAt`` and the
token totals are byte-stable no matter when the fixtures are regenerated.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.models_catalog import CloudModel, EffortLevel
from agent_core.providers.base import ModelResponse, ModelRole, ProviderCapabilities
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.model import ConfigSnapshot
from agent_core.snapshots.scope import _CAPTURED_TABLES
from agent_core.snapshots.snapshot_manager import _canonical, _fingerprint
from agent_core.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = _REPO_ROOT / "shell" / "src" / "__tests__" / "fixtures"

# Fixed epoch timestamps (2100-01-01 + offsets): always inside the current
# month-window queries, never pruned, byte-stable in the emitted payloads.
_T0 = 4102444800

# Every captured table present and empty — the smallest payload that genuinely
# DECODES, which it has to, because `lastWorkingId` is only filled in when the
# restore walk can actually read the candidate it would target. A blob the
# decoder rejects would silently drop those three fields out of the fixture and
# pin the frontend against a payload the real one never has.
_FIXTURE_TABLES: dict[str, list] = {table: [] for table in _CAPTURED_TABLES}
# Rows holding identical tables share a fingerprint — exactly what two real
# captures of an unchanged config produce, since the fingerprint is over
# `tables` alone (contract §5.5 item 6).
_FIXTURE_FINGERPRINT = _fingerprint(_FIXTURE_TABLES)


def _fixture_payload(
    *,
    snapshot_id: str,
    created_at: int,
    trigger: str,
    reason: str,
    verified: bool,
    undeletable: bool,
    binary_ref: str | None,
) -> str:
    """One snapshot payload in the shape ``SnapshotManager._write_row`` produces.

    Built through the real serialiser and the real fingerprint rather than
    hand-written, and carrying the FULL `meta` block, because `meta` is not
    decoration — it is the row's only backup (contract §5.5 item 7). A fixture
    whose meta is `{}` is not a payload this system can produce, and it cannot
    catch the regression it is here to catch: `rebuild_rows_from_payloads` reads
    `meta["id"]` and skips any payload without one, so a cold rebuild from such
    a fixture writes zero rows — the G4 anchor included — and the fixture stays
    green throughout.

    Timestamps are fixed rather than clock-read so the emitted JSON is
    byte-stable; real captures stamp `time.time_ns()`, which is only there to
    break same-second ties and would make this file flap on every regeneration.
    """
    return _canonical(
        {
            "version": 1,
            "captured_at": created_at,
            "captured_at_ns": created_at * 1_000_000_000,
            "meta": {
                "id": snapshot_id,
                "trigger": trigger,
                "reason": reason,
                "created_in_mode": "safe",
                "state_fingerprint": _FIXTURE_FINGERPRINT,
                "verified_working": int(verified),
                "undeletable": int(undeletable),
                "captures_binary": int(binary_ref is not None),
                "binary_ref": binary_ref,
            },
            "tables": _FIXTURE_TABLES,
        }
    )


class _StubProvider:
    """Satisfies ModelProvider for router registration; never actually called —
    the fixture handlers only *list* the router's configuration."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=200_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None) -> ModelResponse:
        raise AssertionError("fixture stub provider is never invoked")


def _seeded_store(db_path: Path) -> Store:
    """A Store holding one deterministic row of everything the handlers read."""
    store = Store(db_path)
    # Suppress first-run widget seeding (_seed_default_widgets) so this fixture keeps
    # exactly its three explicit widgets and the committed widget.list.json stays at 3.
    store.set_setting("widgets_seeded", "1")
    # Developer profile → OPEN mode: the richest payloads (command widget visible,
    # profile.get shows the relaxed mode). The parsers' SAFE fallbacks are covered
    # by the junk-input tests; the fixtures pin the fullest real shape.
    store.set_setting("active_profile", "developer")
    for i, (provider, inp, out, ms) in enumerate(
        [("anthropic", 1200, 400, 850), ("anthropic", 300, 90, 640), ("openai", 500, 120, 720)]
    ):
        store.insert_usage(
            id=f"usage-fixture-{i}",
            conversation_id="conv-fixture",
            provider=provider,
            model="model-fixture",
            input_tokens=inp,
            output_tokens=out,
            latency_ms=ms,
            created_at=_T0 + i,
        )
    for i, (spec, mode) in enumerate(
        [
            ({"kind": "routine", "routineId": "routine-morning-brief", "title": "Morning brief"},
             "safe"),
            ({"kind": "stat", "source": "tokens_month", "title": "Tokens this month"}, "safe"),
            ({"kind": "command", "command": "git status", "title": "Repo status"}, "open"),
        ]
    ):
        store.insert_widget(
            id=f"widget-fixture-{i}",
            spec_json=json.dumps(spec),
            pinned=i == 0,
            position=i,
            created_at=_T0 + i,
            created_in_mode=mode,
        )
    # G3 snapshots. Seeded HERE, before the server builds, because the genesis
    # snapshot is only written when the table is empty — so seeding first is what
    # keeps this fixture at exactly these three rows. One ordinary auto row, one
    # on-command verified row (the restore target), one permanent G4 anchor.
    for i, (trigger, reason, verified, undeletable, binary_ref) in enumerate(
        [
            ("auto", "mode_switch", False, False, None),
            ("on_command", "user_request", True, False, None),
            ("auto", "guard_weakened", True, True, '{"version": "0.1.0"}'),
        ]
    ):
        snapshot_id = f"snapshot-fixture-{i}"
        store.insert_config_snapshot(
            ConfigSnapshot(
                id=snapshot_id,
                created_at=_T0 + i,
                trigger=trigger,
                reason=reason,
                payload_version=1,
                state_blob=_fixture_payload(
                    snapshot_id=snapshot_id,
                    created_at=_T0 + i,
                    trigger=trigger,
                    reason=reason,
                    verified=verified,
                    undeletable=undeletable,
                    binary_ref=binary_ref,
                ),
                # The real fingerprint of the real tables, matching the payload's
                # own `meta`. It is a fixed value in practice because the tables
                # are, so the emitted fixture stays byte-stable.
                state_fingerprint=_FIXTURE_FINGERPRINT,
                verified_working=verified,
                undeletable=undeletable,
                captures_binary=binary_ref is not None,
                binary_ref=binary_ref,
                created_in_mode="safe",
            )
        )
    return store


def _catalog() -> list[CloudModel]:
    effort = (
        EffortLevel("low", "low"),
        EffortLevel("high", "high", default=True),
        EffortLevel("xhigh", "xhigh"),
    )
    return [
        CloudModel(
            id="claude-opus-4-8",
            label="Claude Opus 4.8",
            description="",
            adaptive_thinking=True,
            effort_levels=effort,
            default=True,
        ),
        CloudModel(
            id="claude-haiku-4-5-20251001",
            label="Claude Haiku 4.5",
            description="",
        ),
        CloudModel(
            id="gpt-fixture",
            label="Fixture GPT",
            description="",
            provider="openai",
        ),
    ]


def generate_fixtures(tmp_dir: Path) -> dict[str, dict]:
    """Method name -> the exact result payload its handler returns today."""
    router = ModelRouter(configured={ModelRole.PRIMARY: _StubProvider()})
    router.register_local_model("llama3.2:3b", _StubProvider())

    def _down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    server = JsonRpcServer(
        reader=None,
        writer=None,
        tool_registry=ToolRegistry(),
        store_factory=lambda: _seeded_store(tmp_dir / "fixtures.sqlite3"),
        model_router=router,
        cloud_catalog=_catalog(),
        # Ollama probe fails fast → the deterministic "idle / not running" row.
        ollama_base_url="http://127.0.0.1:11434",
        ollama_client=httpx.Client(transport=httpx.MockTransport(_down)),
        # Exactly one connected cloud provider → one "reachable" connection row.
        provider_key_probe=lambda provider_id: provider_id == "anthropic",
    )
    server._ensure_built()
    return {
        "stats.get": server._stats_get(),
        "widget.list": server._widget_list(),
        "profile.get": server._profile_get(),
        "model.availableRoles": server._available_roles(),
        "snapshot.list": server._snapshot_list(),
    }


def write_fixtures(tmp_dir: Path) -> list[Path]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in generate_fixtures(tmp_dir).items():
        path = FIXTURE_DIR / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for p in write_fixtures(Path(td)):
            print(f"wrote {p.relative_to(_REPO_ROOT)}")
