"""Golden-file drift test (engineering-spec §9): protocol.py and protocol.ts are
hand-synced in v1, so their method-name sets must be identical. Codegen replaces
this at Phase 3; until then this test is what catches a method added on one side
only."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROTOCOL_PY = _REPO_ROOT / "agent_core" / "protocol.py"
_PROTOCOL_TS = _REPO_ROOT / "shell" / "src" / "types" / "protocol.ts"

# Only constant-definition lines count — docstrings/comments may mention methods
# too, and must not be able to mask a missing constant.
# The namespace half is [a-zA-Z]+, not [a-z]+. It was lowercase-only, so the
# camelCase namespaces `costPlan.propose` / `costPlan.apply` matched NEITHER
# pattern and the one guard standing in for codegen silently ignored the two
# newest methods — deleting them from protocol.ts left this test green.
_PY_CONSTANT = re.compile(r'^\s+[A-Z_]+ = "([a-zA-Z]+\.[a-zA-Z]+)"', re.MULTILINE)
_TS_CONSTANT = re.compile(r'^\s+\w+: "([a-zA-Z]+\.[a-zA-Z]+)"', re.MULTILINE)


def _python_methods() -> set[str]:
    return set(_PY_CONSTANT.findall(_PROTOCOL_PY.read_text(encoding="utf-8")))


def _typescript_methods() -> set[str]:
    return set(_TS_CONSTANT.findall(_PROTOCOL_TS.read_text(encoding="utf-8")))


def test_both_files_define_methods():
    # Guards the regexes themselves: an accidental format change that makes a
    # pattern match nothing must fail loudly, not silently pass set() == set().
    assert len(_python_methods()) >= 27
    assert len(_typescript_methods()) >= 27


def test_method_sets_are_in_lockstep():
    python_methods = _python_methods()
    typescript_methods = _typescript_methods()
    only_python = python_methods - typescript_methods
    only_typescript = typescript_methods - python_methods
    assert not only_python and not only_typescript, (
        f"protocol.py and protocol.ts drifted: only in protocol.py={sorted(only_python)}, "
        f"only in protocol.ts={sorted(only_typescript)}"
    )


def test_genesis_label_matches_across_languages():
    """The one user-facing string BOTH sides must hold byte-for-byte (HANDOFF
    step-1 loose end, closed 2026-07-24): the Restore card appends its "this
    clears everything" sentence by comparing the wire label against
    ``GENESIS_LABEL`` in client.ts, and the core writes that label from
    ``REASONS["genesis"]``. If they drift, the genesis warning silently stops
    appearing — a wrong-copy failure no behavioural test on either side alone
    can see, which is exactly the drift-test shape."""
    from agent_core.snapshots.snapshot_manager import REASONS

    client_ts = (_REPO_ROOT / "shell" / "src" / "ipc" / "client.ts").read_text(encoding="utf-8")
    match = re.search(r'export const GENESIS_LABEL = "([^"]+)"', client_ts)
    assert match is not None, "GENESIS_LABEL is gone from client.ts — the genesis sentence broke"
    assert match.group(1) == REASONS["genesis"]
