"""Refuse to open the owner's live database from anything except the running app.

Why this exists
---------------
A build agent once constructed a real ``Store`` against the default path and wrote
an *undeletable* snapshot row into the owner's live database — permanent by design
(schema.sql's ``RAISE(ABORT)`` triggers), so the recovery machinery made its own
accident unremovable through the app. The first fix was an autouse pytest fixture,
and a later audit found the hole that mattered: a 151 KB Addison-schema database
sitting in ``~/.addison`` written by an **ad-hoc probe script** an hour before that
fixture was committed. Probe scripts are how this project is developed. A guard
that only covers ``pytest`` does not cover the route that actually fired.

So the guard moved here, and it changed on two axes:

- **Delivery**: it is armed by ``import agent_core`` (see ``agent_core/__init__``),
  not by collecting a conftest. Any process that touches this codebase at all is
  covered — probes, ``python -c``, a REPL, a one-off migration script, pytest.
- **Depth**: it wraps ``sqlite3.connect``, not ``Store.__init__``. That is the
  single choke point every route to the file shares, so it also catches the two
  bypasses a ``Store``-level check misses — a bare ``sqlite3.connect`` that never
  imports ``Store``, and a ``Store`` subclass whose ``__init__`` skips ``super()``.

How the app gets through
------------------------
The hard part is not blocking; it is telling "the app, running normally" — which
legitimately opens exactly this path — from "a test or probe that should be using
a temp directory". Every ambient signal I considered fails that test in the
dangerous direction: cwd, ``sys.argv``, the interpreter (probes are run with the
repo venv), an env var (inherited by children), even ``__main__.__spec__`` being
``agent_core.main`` (which is also false for the Phase-3 bundled binary, so it
would block the real app in production). Each answers "does this *look* like the
app?", and a probe can satisfy any of them by accident — silently, which is the
failure mode this whole module exists to prevent.

So the signal is not a signal at all: **default-deny plus one explicit grant**.
``main()`` — the one function all three launch routes end in (env override,
bundled binary, ``-m agent_core.main``; see ``shell/src-tauri/src/agent_process.rs``)
— calls :func:`allow_live_database` on itself. Nothing else does. It is a line of
code that runs when, and only when, the application actually starts; a probe does
not execute it by resembling anything. It also fails in the safe direction: a new
route into this codebase that forgets to opt in gets blocked, loudly, instead of
quietly inheriting permission.

What this does NOT cover
------------------------
A Python process that imports no Addison code whatsoever — a bare
``sqlite3.connect`` in a script that never touches this package. Closing that
needs a ``.pth`` or ``sitecustomize`` in the venv's ``site-packages``, and that is
deliberately rejected: it lives outside version control, so it silently vanishes
the next time someone rebuilds the venv (protection that reads as present while
being absent is worse than none), and it would fire inside pip, ruff and every
other tool sharing that interpreter. The residual gap is also not the risk: an
Addison-schema database is written by Addison's code, and importing that code is
what arms this guard.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# The owner's real data directory. Module-level rather than baked into a closure so
# a test can point it somewhere harmless and exercise the allow/deny logic without
# going anywhere near the real one.
_LIVE_DATA_DIR = Path.home() / ".addison"

_app_has_declared_itself = False
_installed = False


class LiveDatabaseBlocked(AssertionError):
    """Raised when code that is not the application tries to open ``~/.addison``.

    Subclasses ``AssertionError`` on purpose: under pytest it reads as a plain test
    failure rather than an error someone might reach for an ``except`` clause to
    quieten.

    It IS still caught by a broad ``except Exception``, and the recovery paths are
    full of them — ``JsonRpcServer._rebuild_into`` against a guarded path reports
    "rebuild failed" rather than naming the guard. The block itself holds either
    way (nothing is written); what is lost is the loud message, in the one place
    a loud message is the whole point. Making this a ``BaseException`` would fix
    that and is the obvious next move, but it changes how every existing handler
    behaves, so it is a deliberate follow-up rather than a line to slip into a
    docstring correction. Recorded in ``docs/HANDOFF.md``.
    """


def allow_live_database() -> None:
    """Declare that this process IS the Addison application.

    The only caller is ``agent_core.main.main()``. Do not call it from a test, a
    probe, or a helper — the whole value of the guard is that this line is hard to
    reach by accident.
    """
    global _app_has_declared_itself
    _app_has_declared_itself = True


def is_installed() -> bool:
    """Whether :func:`install` has wrapped ``sqlite3.connect`` in this process."""
    return _installed


def install() -> None:
    """Wrap ``sqlite3.connect`` so live-directory paths are refused. Idempotent."""
    global _installed
    if _installed:
        return

    original_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        if not _app_has_declared_itself:
            candidate = _database_path(database)
            if candidate is not None:
                blocked = _resolve_inside_live_data_dir(candidate)
                if blocked is not None:
                    raise LiveDatabaseBlocked(
                        f"Something other than the Addison app tried to open the live "
                        f"database at {blocked}. Point this at a temporary directory "
                        f"instead — see agent_core/live_db_guard.py."
                    )
        return original_connect(database, *args, **kwargs)

    sqlite3.connect = guarded_connect  # type: ignore[assignment]
    _installed = True


def _database_path(database: object) -> Path | None:
    """The filesystem path ``sqlite3.connect`` was pointed at, or None if there isn't
    one (``:memory:``, an anonymous temporary database, or an argument we can't read
    — none of which can touch the live directory)."""
    raw: object = database
    if not isinstance(raw, (str, bytes)):
        try:
            raw = os.fspath(raw)  # type: ignore[arg-type]
        except TypeError:
            return None
    if isinstance(raw, bytes):
        raw = raw.decode(errors="replace")
    if not isinstance(raw, str):
        return None
    text = raw
    if text in ("", ":memory:"):
        return None
    if text.startswith("file:"):
        # URI form (connect(..., uri=True)). The path is everything between the
        # scheme and the query string; "file::memory:" has no path at all.
        text = text[len("file:") :].split("?", 1)[0]
        if text in ("", ":memory:"):
            return None
    return Path(text)


def _resolve_inside_live_data_dir(candidate: Path) -> Path | None:
    """The resolved path if it lands inside the live data directory, else None.

    ``resolve()``, not just ``expanduser()``: ``Path.parents`` walks the LITERAL
    components, so ``~/Desktop/../.addison/x`` has no ``~/.addison`` component to
    match, and a symlink pointing into the live directory has none either. Both
    walk straight past a naive parents check and end up writing the real file.
    """
    try:
        live = _LIVE_DATA_DIR.expanduser().resolve()
        resolved = candidate.expanduser().resolve()
    except (OSError, RuntimeError):
        # An unresolvable path is not a path into the live directory.
        return None
    if resolved == live or live in resolved.parents:
        return resolved
    return None
