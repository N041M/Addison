"""Payload-shape drift test — the shape-level companion to test_protocol_drift.py.

protocol drift covers method NAMES; this covers result SHAPES: the committed
JSON fixtures under shell/src/__tests__/fixtures/ (which the vitest parser
suite consumes) must equal what the live handlers produce right now. When a
handler's payload changes shape on purpose, regenerate with
``python tests/ipc_fixtures.py`` and re-run the frontend tests — if the parsers
still pass over the regenerated files, the frontend survived the change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ipc_fixtures.py sits beside this file but tests/ is not a package — put it on
# the path explicitly so the import works under pytest and pyright alike.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ipc_fixtures import FIXTURE_DIR, generate_fixtures  # noqa: E402


def test_committed_fixtures_match_live_handlers(tmp_path):
    generated = generate_fixtures(tmp_path)
    for name, payload in generated.items():
        path = FIXTURE_DIR / f"{name}.json"
        assert path.exists(), (
            f"missing fixture {path.name} — regenerate with: python tests/ipc_fixtures.py"
        )
        committed = json.loads(path.read_text(encoding="utf-8"))
        assert committed == payload, (
            f"{path.name} no longer matches the live {name} handler — the core payload "
            "shape drifted. Regenerate with `python tests/ipc_fixtures.py`, then run "
            "`npm test` in shell/ to confirm the frontend parsers survive the new shape."
        )


def test_fixture_payloads_carry_no_key_material(tmp_path):
    """§8.3 belt: whatever these handlers emit, no field may ever look like a key."""
    blob = json.dumps(generate_fixtures(tmp_path)).lower()
    for needle in ("api_key", "apikey", "sk-ant", "secret"):
        assert needle not in blob
