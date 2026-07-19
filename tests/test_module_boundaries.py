"""Composition-root boundary enforcement (engineering-spec §2, CLAUDE.md
"Module boundary rule"). In the spirit of test_tool_registry.py's "single most
important test", this locks a structural invariant mechanically rather than by
convention: ``agent_core/tools/``, ``agent_core/providers/`` and
``agent_core/routines/`` must not import from ONE ANOTHER. They are wired
together only by the composition root (``orchestrator.py``), which is what lets
the Routine engine replay tool calls through the exact same registry + gate as
the live loop instead of building a parallel one. If a tool ever imported a
provider, or a provider a routine, the no-escalation and provider-agnostic
properties would erode silently — this test makes that a red build.

Enforced with the stdlib ``ast`` module (no import side effects): every .py under
the three packages is parsed and its import targets — both ``import X`` and
``from X import ...``, absolute and relative — are resolved to a package and
checked pairwise. One edge is deliberately allow-listed: ``routines/engine.py``
imports tool TYPES + the ToolRegistry (``agent_core.tools.base`` /
``.registry``) because replaying a tool call requires constructing an
ExecutionContext and holding the shared registry — this is the sanctioned
mechanism CLAUDE.md's own rationale describes, not a leak. Everything else among
the three packages stays forbidden.

Also locks the one-way policy dependency noted in policy.py's module docstring:
``agent_core.policy`` must not import ``agent_core.tools`` (tools/base.py imports
PolicyMode, so the arrow runs one way only — a cycle here would be a real bug).
"""

from __future__ import annotations

import ast
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1] / "agent_core"
_PACKAGES = ("tools", "providers", "routines")

# The single sanctioned cross-package edge (CLAUDE.md rationale): the routine
# engine replays tool calls through the shared registry, so it imports tool types.
_ALLOWED_EDGES = {
    ("agent_core.routines.engine", "agent_core.tools"),
}


def _module_name(path: Path) -> str:
    """Dotted module name for a file under agent_core (e.g. routines/engine.py ->
    agent_core.routines.engine; a package __init__.py -> agent_core.routines)."""
    rel = path.relative_to(_AGENT_CORE.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(importer_module: str, node: ast.ImportFrom) -> str | None:
    """Absolute target of a relative ``from . import x`` — None if it can't be
    resolved. ``level`` counts leading dots; level 1 is the importer's own package."""
    package_parts = importer_module.split(".")[:-1]  # drop the module's own name
    if node.level - 1 > len(package_parts):
        return None
    base = package_parts[: len(package_parts) - (node.level - 1)]
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base) if base else None


def _imported_targets(tree: ast.AST, importer_module: str) -> list[tuple[str, int]]:
    """Every absolute module string this file imports, with the source line."""
    targets: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative
                resolved = _resolve_relative(importer_module, node)
                if resolved is not None:
                    targets.append((resolved, node.lineno))
            elif node.module:
                targets.append((node.module, node.lineno))
    return targets


def _package_of(module: str) -> str | None:
    """Which of the three boundary packages ``module`` belongs to, or None."""
    for pkg in _PACKAGES:
        prefix = f"agent_core.{pkg}"
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _iter_py_files():
    for pkg in _PACKAGES:
        yield from sorted((_AGENT_CORE / pkg).glob("*.py"))


def test_boundary_packages_do_not_import_each_other():
    """No module in tools/providers/routines imports from another of the three
    (the one allow-listed engine->tools edge aside). A failure names the exact
    file and import line so the leak is a one-line fix."""
    violations: list[str] = []
    for path in _iter_py_files():
        importer_module = _module_name(path)
        own_package = _package_of(importer_module) or f"agent_core.{path.parent.name}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target, lineno in _imported_targets(tree, importer_module):
            target_package = _package_of(target)
            if target_package is None or target_package == own_package:
                continue
            if (importer_module, target_package) in _ALLOWED_EDGES:
                continue
            violations.append(
                f"{path}:{lineno} — {importer_module} imports {target} "
                f"(crosses {own_package} -> {target_package}, forbidden)"
            )
    assert not violations, "Module-boundary rule violated:\n" + "\n".join(violations)


def test_policy_does_not_import_tools():
    """policy.py's one-way dependency (its own docstring): tools/base.py imports
    PolicyMode, so agent_core.policy must never import agent_core.tools back —
    that would be an import cycle and a real bug."""
    path = _AGENT_CORE / "policy.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = [
        f"{path}:{lineno} — imports {target}"
        for target, lineno in _imported_targets(tree, "agent_core.policy")
        if target == "agent_core.tools" or target.startswith("agent_core.tools.")
    ]
    assert not offenders, "policy.py must not import agent_core.tools:\n" + "\n".join(offenders)
