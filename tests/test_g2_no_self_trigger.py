"""G2 — "Addison never triggers itself" — enforced across the whole Agent Core.

G2 is a GLOBAL floor (CLAUDE.md): no autonomous self-triggering or self-scheduling,
in any mode. Addison may *author* automation that the OS runs; the OS fires it, and
every unit of Addison's own work starts from something a human did — an inbound
JSON-RPC frame, a line typed at the CLI. Until this file existed the only test
pinning G2 (``test_snapshot_subsystem_never_schedules_itself``) AST-scoped
``snapshot_manager.py`` alone, so a ``threading.Timer`` that re-ran a routine every
hour could be added to any other module and the entire suite stayed green.

THE RULE THESE TESTS ENCODE — read this before loosening anything below:

    Nothing in ``agent_core/`` may use a primitive that fires work on a SCHEDULE or
    after a DELAY. This is not a ban on concurrency.

Concurrency the core legitimately needs, and which must keep passing:

  * ``threading.Thread`` for the worker loop and the stdin read loop (``main.py``) —
    a thread runs *now*, and these two only ever consume work someone else handed in.
  * ``threading.Event`` for request/response correlation and permission waits
    (``shell_bridge.py``, ``rpc/routines.py``, ``main.py``) — a *blocking wait* for an
    inbound reply is the opposite of a self-trigger: it cannot proceed without one.
  * ``threading.Lock`` and a blocking ``queue.get()`` in the worker loop — same
    reason: they park until real work arrives.
  * ``time.sleep`` for retry backoff inside an in-flight request (``providers/base.py``)
    — a delay *within* work the user already asked for, which finishes with that
    request and can never outlive it.

What is banned is the shape those cannot express: a callback handed to a clock. A
timer, a scheduler, an alarm, or a thread that exists to wake up and start work is a
second author of Addison's actions, and G2 says there is only one — the user.

If a test here fails, the fix is almost never to widen the allowlist. It is to make
the new work start from an inbound request, or to hand the schedule to the OS (G2's
explicit escape hatch) and gate arming it behind the user-typed keyword prefix.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agent_core

_PACKAGE_ROOT = Path(agent_core.__file__).resolve().parent

# Directories that sit inside agent_core/ but hold code that is not Addison's:
# the virtualenv (third-party packages schedule themselves all day long), caches,
# and build artefacts. Dot-prefixed parts are skipped wholesale, which covers
# .venv and .ruff_cache.
_NOT_ADDISONS_CODE = {"__pycache__", "site-packages", "node_modules", "build", "dist"}

# Modules whose entire reason for existing is to run a callback later.
_SCHEDULER_MODULES = {
    "sched",
    "apscheduler",
    "schedule",
    "croniter",
    "crontab",
    "timeloop",
    "watchdog",
    "celery",
}

# Names that fire a callback on a clock, wherever they are reached from. Matched on
# the bare attribute/imported name rather than a dotted path so an alias
# (``import threading as _t; _t.Timer(...)``) cannot slip past: an alias renames the
# module, never the attribute.
#
# Deliberately absent: ``sleep`` (retry backoff), ``wait`` (blocking on a reply),
# ``Thread``/``Lock``/``Event``. Those run now or park until someone else acts.
_DELAYED_FIRE_NAMES = {
    "Timer",  # threading.Timer
    "scheduler",  # sched.scheduler
    "enterabs",  # sched.scheduler.enterabs
    "setitimer",  # signal.setitimer
    "alarm",  # signal.alarm
    "call_later",  # asyncio loop
    "call_at",  # asyncio loop
    "create_task",  # asyncio — work that outlives the caller's turn
    "ensure_future",
    "run_coroutine_threadsafe",
}

# Every background thread the core starts, by the source text of its ``target=``.
# Each was read and confirmed to consume work rather than originate it:
_REVIEWED_THREAD_TARGETS = {
    # main.py — drains self._queue, which only _dispatch (inbound JSON-RPC) fills.
    "self._worker_loop",
    # main.py — one model pull, started by an inbound model.startLocalSetup and
    # finished; it never starts a turn or a tool call.
    "self._run_local_setup",
}

_ADD_TARGET_HINT = (
    "If this thread is genuinely driven by inbound work rather than by a clock, add "
    "its target to _REVIEWED_THREAD_TARGETS in this file together with the sentence "
    "that says what hands it its work."
)


def _core_modules() -> list[Path]:
    """Every Python module Addison ships in the Agent Core."""
    modules = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        parts = path.relative_to(_PACKAGE_ROOT).parts
        if any(part.startswith(".") or part in _NOT_ADDISONS_CODE for part in parts):
            continue
        modules.append(path)
    return modules


def _where(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(_PACKAGE_ROOT).as_posix()}:{getattr(node, 'lineno', '?')}"


def test_the_g2_scan_reaches_every_module_it_claims_to_cover() -> None:
    """A discovery bug would make every other test in this file pass on nothing.

    The scan walks a directory that also contains .venv; getting the filter slightly
    wrong in either direction is silent — too greedy and third-party timers fail the
    build, too narrow and G2 is unenforced while still reporting green. So pin both
    ends: the modules most able to trigger work are covered, the count is in the
    right order of magnitude, and no scanned path came out of the virtualenv.
    """
    scanned = {p.relative_to(_PACKAGE_ROOT).as_posix() for p in _core_modules()}

    must_cover = {
        "main.py",  # owns the queue, the threads and the read loop
        "orchestrator.py",  # owns run_turn
        "rpc/routines.py",  # the routine.run entry point
        "routines/engine.py",
        "shell_bridge.py",
        "snapshots/snapshot_manager.py",
        "tools/run_command.py",
    }
    assert must_cover <= scanned, f"G2 scan misses {sorted(must_cover - scanned)}"
    assert len(scanned) >= 40, f"G2 scan found only {len(scanned)} modules — filter too narrow"
    assert not [p for p in scanned if ".venv" in p or "site-packages" in p]

    # A count alone is not enough, and this is the hole it leaves: adding one name
    # to the exclusion set drops a WHOLE SUBPACKAGE (providers/ is ~10 modules)
    # while the total stays over the floor and every must_cover name still matches,
    # so a real Timer in providers/base.py would sail through a green suite. Pin the
    # subpackages themselves — losing one is then loud, whatever the count says.
    covered_packages = {p.split("/")[0] for p in scanned if "/" in p}
    expected_packages = {
        "memory", "permissions", "providers", "routines", "rpc", "snapshots", "tools",
    }
    assert expected_packages <= covered_packages, (
        f"G2 scan lost whole subpackages: {sorted(expected_packages - covered_packages)}"
    )


def test_no_core_module_imports_a_scheduler() -> None:
    """A scheduling library in the dependency graph is a self-trigger waiting for a
    caller — and unlike a bare ``threading.Timer`` it looks deliberate in review, so
    the ban has to be stated rather than assumed."""
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            for name in imported:
                root = name.split(".")[0]
                assert root not in _SCHEDULER_MODULES, (
                    f"{_where(path, node)} imports {name} — Addison never schedules "
                    f"itself (G2). Hand the schedule to the OS instead; the OS fires "
                    f"it, Addison authors it."
                )


def test_no_core_module_hands_a_callback_to_a_clock() -> None:
    """The floor itself: no timer, alarm or deferred-callback primitive anywhere in
    agent_core. Catches both ``from threading import Timer`` and any attribute spelling
    of it, including through an aliased import."""
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name not in _DELAYED_FIRE_NAMES, (
                        f"{_where(path, node)} imports {alias.name} — that fires work "
                        f"on a clock, and Addison never triggers itself (G2)."
                    )
            elif isinstance(node, ast.Attribute):
                assert node.attr not in _DELAYED_FIRE_NAMES, (
                    f"{_where(path, node)} uses .{node.attr} — that fires work on a "
                    f"clock, and Addison never triggers itself (G2). Work starts from "
                    f"an inbound request, never from a timer."
                )


def test_every_background_thread_runs_work_someone_else_handed_in() -> None:
    """A thread is allowed; a thread that wakes itself up is the same breach a Timer
    is, spelled with sleep(). Banning the timer primitives alone would leave that door
    open, so each thread's target is named here and a new one has to be argued for."""
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {
                    base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
                    for base in node.bases
                }
                assert "Thread" not in bases, (
                    f"{_where(path, node)} subclasses Thread, which hides the entry "
                    f"point in run(). Pass an explicit target instead. {_ADD_TARGET_HINT}"
                )
                continue
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called != "Thread":
                continue
            targets = [kw.value for kw in node.keywords if kw.arg == "target"]
            assert targets, (
                f"{_where(path, node)} starts a thread without an explicit target=. "
                f"G2 is only checkable when the entry point is visible. {_ADD_TARGET_HINT}"
            )
            target = ast.unparse(targets[0])
            assert target in _REVIEWED_THREAD_TARGETS, (
                f"{_where(path, node)} starts a thread on {target}, which no one has "
                f"confirmed is driven by inbound work rather than by a clock (G2). "
                f"{_ADD_TARGET_HINT}"
            )
