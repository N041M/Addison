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
import os
import re
from pathlib import Path

import agent_core
from agent_core.policy import (
    DENIED_ARMING,
    OS_AUTOMATION_DIRS,
    command_denied_path,
    workspace_trust_allows,
)
from agent_core.tools.base import FORBIDDEN_CALL_ARMING, call_is_forbidden

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
    # main.py — one folder picker, started by an inbound workspace.pickDirectory
    # (a person clicking "Choose a folder") and finished when they answer the
    # dialog. It relays one shell call and responds; it never starts a turn, runs
    # a tool, or schedules anything.
    "self._run_workspace_pick_directory",
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


# ===========================================================================
# THE OTHER HALF OF G2 — Addison may not ARM the OS's clock either
# ===========================================================================
# Everything above pins the first half of the floor: no timer, no scheduler, no
# thread that wakes itself. That is Addison triggering ITSELF. The floor's second
# half is the escape hatch's own boundary — "Addison may AUTHOR automation; only
# the OS runs it" is a promise about a gate that does not exist yet, and until step
# 8 phase 3 builds it the honest position is that NOTHING in the tree may hand the
# OS a job at all.
#
# Two fences make that true, and they close two different doors (step-8 plan §5.5):
#
#   1. the TRUST-GRANT refusal — ~/Library/LaunchAgents and friends can never be a
#      trusted workspace, so `write_project_file` can never drop a plist in one
#      behind an ordinary card. This is the door that needs no shell, and it was
#      OPEN until 2026-08-07 (KNOWN-GAPS, "OS-automation directories can be trusted
#      and written today").
#   2. the ARMING-BINARY refusal — a command whose program is `crontab`/`launchctl`/
#      `at`/`batch` is refused before the gate, at every dispatch site.
#
# Remove EITHER and "nothing in the tree can arm automation" becomes false again,
# which is why both are asserted here, next to the floor they belong to, rather
# than only in the containment file where the mechanism lives.


def test_g2_the_os_automation_directories_can_never_be_trusted_for_arming() -> None:
    """Fence 1. A trusted workspace is the card-free zone; an automation directory
    inside it would make writing a launchd job a card-free action. Refused in both
    directions, so trusting the PARENT is not a way round it."""
    for entry in OS_AUTOMATION_DIRS:
        expanded = os.path.expanduser(entry)
        assert workspace_trust_allows(expanded) is False, entry
        assert workspace_trust_allows(os.path.join(expanded, "job.plist")) is False, entry
    # The parent, which is how the fence would otherwise be walked around.
    assert workspace_trust_allows(os.path.expanduser("~/Library")) is False
    # ...and not vacuous: an unrelated folder is still trustable, so this test
    # cannot pass by refusing everything.
    assert workspace_trust_allows(os.path.expanduser("~/addison-g2-not-a-real-folder")) is True


def test_g2_arming_the_os_clock_from_a_command_is_refused_before_the_gate() -> None:
    """Fence 2. `command_denied_path` answers ARMING before it looks at any path, so
    every dispatch site inherits it through the check it already makes; the
    per-site proof is in test_step_5_5_containment.py.

    The refusal is not platform-conditional: the seatbelt blocks `launchctl`'s Mach
    traffic on macOS and nothing blocks `crontab` anywhere else, so a fence that
    followed `kernel_confines_writes` would be absent exactly where it is needed."""

    class _Command:
        """run_command's shape, reduced to what the denylist reads."""

        def command_text(self, args: dict) -> str | None:
            return str(args.get("command", "")) or None

    tool = _Command()
    data_dir = os.path.expanduser("~/.addison")
    for command in ("crontab -", "launchctl load ~/x.plist", "at now", "batch",
                    "cd /tmp && crontab -"):
        denial = command_denied_path(command, data_dir)
        assert denial is not None and denial[1] == DENIED_ARMING, command
        assert call_is_forbidden(tool, {"command": command}, data_dir) == FORBIDDEN_CALL_ARMING
    # Not vacuous, and not a ban on the subject: talking about the program still
    # works, which is what keeps the fence from being routed around.
    assert call_is_forbidden(tool, {"command": "man crontab"}, data_dir) is None


def test_g2_the_fence_list_is_in_lockstep_with_the_shell() -> None:
    """The fence is ONE list with THREE consumers (step-8 plan §5.5), and the third
    lives in another language: ``exec.rs`` carries its own ``OS_AUTOMATION_DIRS``,
    derived shell-side on purpose (the profile's floor must not depend on the
    core's honesty), which leaves hand-sync as the contract — the same deal
    ``protocol.py`` / ``protocol.ts`` have. A hand-synced contract asserted on one
    side only is asserted on neither, so this reads the Rust constant out of the
    source and compares ENTRY FOR ENTRY, order included: the shell file's comment
    promises readability "against the core's entry-for-entry", and order is what
    makes that check a glance rather than a diff."""
    exec_rs = (
        Path(__file__).resolve().parent.parent / "shell" / "src-tauri" / "src" / "exec.rs"
    ).read_text(encoding="utf-8")
    block = re.search(
        r"const OS_AUTOMATION_DIRS: &\[&str\] = &\[\n(?P<body>(?:\s*\"[^\"]+\",\n)+)\s*\];",
        exec_rs,
    )
    assert block is not None, "exec.rs no longer declares OS_AUTOMATION_DIRS as a &[&str] literal"
    rust_entries = re.findall(r"\"([^\"]+)\"", block.group("body"))
    assert rust_entries == list(OS_AUTOMATION_DIRS)
