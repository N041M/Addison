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
# Usage:  scripts/gates.sh [python|frontend|rust|all]     (default: all)
#
# Run from anywhere; paths resolve against the repo root, not the caller's cwd.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
JOB=${1:-all}

say() { printf '\n=== %s ===\n' "$1"; }

gates_python() {
    say "python (ruff · pyright · pytest)"
    cd "$ROOT"
    # PYTHONPATH mirrors CI: the editable install covers agent_core.*, the repo
    # root covers tests.*.
    PYTHONPATH="$ROOT" ruff check agent_core/ tests/
    PYTHONPATH="$ROOT" pyright
    PYTHONPATH="$ROOT" pytest tests/ -q
}

gates_frontend() {
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
    say "rust (cargo test, Tauri shell)"
    # A Tauri build needs the frontend bundle to exist; CI stubs it and so do we,
    # so this job never depends on having run the frontend one first.
    mkdir -p "$ROOT/shell/dist"
    cd "$ROOT/shell/src-tauri"
    cargo test
    cargo clippy --all-targets -- -D warnings
}

case "$JOB" in
    python)   gates_python ;;
    frontend) gates_frontend ;;
    rust)     gates_rust ;;
    all)      gates_python; gates_frontend; gates_rust ;;
    *)        echo "usage: scripts/gates.sh [python|frontend|rust|all]" >&2; exit 2 ;;
esac

printf '\nAll requested gates passed.\n'
