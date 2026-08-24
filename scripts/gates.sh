#!/bin/sh
# THE gate list. One executable definition, run by CI and by people.
#
# WHY THIS EXISTS. The gates were written down in three places — .github/workflows/
# ci.yml, docs/CONVENTIONS.md, and whatever the person at the keyboard remembered —
# and on 2026-08-06 all three disagreed:
#
#   * a whole session reported "all gates green" having never run pyright or
#     ESLint, because six of eight was what got remembered;
#   * CI ran `npx tsc --noEmit`, which type-checks src only — so the
#     tsconfig.test.json gate that KNOWN-GAPS records as closing the
#     "tsc does not cover the test files" hole had never actually run in CI;
#   * CI ran `npm run lint` with no --max-warnings=0, so warnings passed.
#
# A list of gates in prose is a claim, and this repo's whole documentation
# discipline exists because claims drift from the tree. So the list is a program.
# ci.yml calls it, which is what makes drift impossible rather than merely
# discouraged: there is no second copy left to disagree with.
#
# Usage:  scripts/gates.sh [python|frontend|rust|python-floors|all]   (default: all)
#
# `python-floors` is the ONE job that is not part of `all`, and it exists for the
# Windows port. See its function for what it runs and, more importantly, what it
# deliberately does not.
#
# Run from anywhere; paths resolve against the repo root, not the caller's cwd.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
JOB=${1:-all}

say() { printf '\n=== %s ===\n' "$1"; }

# `$ROOT` in a form the PYTHON on this machine understands.
#
# On Windows CI this script runs under Git Bash, where `$ROOT` is `/d/a/...` — a
# path bash resolves and a native Windows Python does not, so `PYTHONPATH="$ROOT"`
# would silently add a directory that does not exist and every `import tests.*`
# would fail with a message about the import rather than about the path. `cygpath`
# ships with that bash and exists nowhere else, which makes its absence the exact
# test for "this is not that situation".
py_root() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$ROOT"
    else
        printf '%s' "$ROOT"
    fi
}

# A missing TOOL is not a failing gate, and must not read like one. Run cold, this
# script used to stop at a bare `ruff: command not found` — which tells a reader
# nothing about what to do, and invites the worst repair (installing the tool
# globally, so the next run checks a different ruff than CI does).
#
# Exit 2 for "cannot run", distinct from a gate's own non-zero: the difference
# between "your code is wrong" and "your shell is not set up" is the whole message.
need() {
    missing=''
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
    done
    [ -z "$missing" ] && return 0
    printf '\nCannot run this job — not on PATH:%s\n' "$missing" >&2
    case "$1" in
        ruff|pyright|pytest)
            printf 'The Python gates need the agent-core venv:\n\n    source %s/agent_core/.venv/bin/activate\n\n' "$ROOT" >&2
            printf 'If it does not exist yet: python3 -m venv agent_core/.venv && source agent_core/.venv/bin/activate && pip install -e "agent_core[dev]" pyright\n' >&2
            ;;
        npm|node)
            printf 'The frontend gates need node + an install:\n\n    cd %s/shell && npm ci\n\n' "$ROOT" >&2
            ;;
        cargo)
            printf 'The Rust gates need a toolchain: https://rustup.rs\n' >&2
            ;;
    esac
    exit 2
}

gates_python() {
    need ruff pyright pytest
    say "python (ruff · pyright · pytest)"
    cd "$ROOT"
    # PYTHONPATH mirrors CI: the editable install covers agent_core.*, the repo
    # root covers tests.*.
    PYTHONPATH="$(py_root)" ruff check agent_core/ tests/
    PYTHONPATH="$(py_root)" pyright
    PYTHONPATH="$(py_root)" pytest tests/ -q
}

# The Windows job, and the honest statement of its scope.
#
# WHY IT IS NOT `gates.sh python`. The full suite does not pass on Windows and has
# never been asked to: nineteen test files plant symlinks, spawn `/bin/sh`, call
# `mkfifo` or hard-code `/tmp`, and porting those fixtures is its own phase
# (docs/plans/windows-port-plan.md §6). Running the whole suite there today would put a
# permanently red job in front of every pull request, and a gate nobody can make
# green is a gate somebody eventually deletes.
#
# WHY IT EXISTS AT ALL. The Windows half of the G2 fence has assertions that are
# a SKIP everywhere else — `test_the_windows_fence_is_whole_on_windows` is the one
# that says an entry which will not expand is a hole rather than a platform
# difference. A test that only ever skips is a test that has never run, which is
# this repository's most expensive recurring bug. So the floors run on Windows,
# and the list is a program here rather than a sentence in a workflow file, for
# the same reason everything else in this script is.
#
# WIDEN IT AS FIXTURES PORT. Every file that joins this list is one more file that
# cannot regress on Windows; the goal is for this function to disappear into
# `gates_python`.
gates_python_floors() {
    need pytest
    say "python floors (Windows scope)"
    cd "$ROOT"
    PYTHONPATH="$(py_root)" pytest \
        tests/test_step_5_5_containment.py \
        tests/test_g2_no_self_trigger.py \
        tests/test_policy_modes.py \
        -q
}

gates_frontend() {
    need npm node
    say "frontend (eslint · tsc · vitest · build)"
    cd "$ROOT/shell"
    # --max-warnings=0: a warning nobody fails on is a warning nobody fixes.
    npx eslint src --max-warnings=0
    # BOTH configs. `npx tsc --noEmit` alone leaves the test files unchecked,
    # which is the gap tsconfig.test.json was added to close.
    npm run typecheck
    npm test
    npm run build
}

gates_rust() {
    need cargo
    say "rust (cargo test, Tauri shell)"
    # A Tauri build needs the frontend bundle to exist; CI stubs it and so do we,
    # so this job never depends on having run the frontend one first.
    mkdir -p "$ROOT/shell/dist"
    cd "$ROOT/shell/src-tauri"
    cargo test
    # PLATFORM-GATED CODE IS THE ONE THING THIS CANNOT CHECK *LOCALLY*. Everything
    # behind `#[cfg(target_os = "macos")]` compiles here and vanishes on the Linux
    # and Windows runners, taking its imports and constants with it — so
    # `-D warnings` finds dead code there that does not exist here. Cross-checking
    # locally is not practical (a Linux build of the Tauri deps needs a webkit
    # sysroot; a Windows one needs an MSVC toolchain the `ring` build script cannot
    # find on a Mac), so when you gate a symbol, check every import and constant it
    # was the sole user of. CI now runs this same job on all three, which is what
    # turns "check it by hand" into "check it by hand, and then be told".
    #
    # And read the COUNT in a CI failure, not the errors you recognise: "due to 4
    # previous errors" got two of them fixed on 2026-08-06 because the other two
    # were a different error kind and the grep that found them did not match.
    cargo clippy --all-targets -- -D warnings
}

case "$JOB" in
    python)        gates_python ;;
    frontend)      gates_frontend ;;
    rust)          gates_rust ;;
    python-floors) gates_python_floors ;;
    all)           gates_python; gates_frontend; gates_rust ;;
    *)             echo "usage: scripts/gates.sh [python|frontend|rust|python-floors|all]" >&2
                   exit 2 ;;
esac

printf '\nAll requested gates passed.\n'
