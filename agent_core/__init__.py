"""Addison Agent Core (engineering-spec §1.2).

Importing this package arms the live-database guard: from here on the process may
not open ``~/.addison`` unless it declares itself the application
(``live_db_guard.allow_live_database()``, called only by ``main.main()``).

It is installed *here*, at the package root, because this is the one line every
route into this codebase passes through — a test, an ad-hoc probe script, a REPL,
the app itself. The guard it replaces was an autouse pytest fixture, which left
every probe script unprotected; a probe is what wrote a real database into the
owner's live directory. See ``agent_core/live_db_guard.py`` for the reasoning.
"""

from . import live_db_guard

live_db_guard.install()
