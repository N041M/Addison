"""Documentation drift — the failures that prose alone cannot catch.

This repo's recorded failure mode is not bad code, it is **prose that stopped being
true**: `CLAUDE.md` has twice asserted the opposite of what shipped, and a
branch-state paragraph was falsified by a merge that never touched the file
containing it. Every check here exists because that class of drift has actually
happened, and none of it is caught by the other gates.

It matters more here than in a human-run repo. A person senses a stale doc — the
tone is off, the date is old. An agent reads it as true and acts on it.

**The load-bearing facts and their contradiction patterns live in
[`doc_claims.py`](doc_claims.py), one row each.** Adding a rule is adding a row;
`test_no_document_contradicts_a_registered_claim` below is the only test that
iterates them, and it is the only one you should need to touch.

Same idiom as `test_capture_scope_covers_every_schema_table`: the build fails when a
document and the tree disagree, so the decision is forced at the moment of the
change rather than discovered a fortnight later.
"""

from __future__ import annotations

import re
from datetime import date

from tests.doc_claims import CLAIMS, REPO, markdown_files, offenders_for


def test_every_markdown_link_resolves():
    """A relative link — or an embedded image — that points at nothing.

    Covers `![...](...)` as well as `[...](...)`, because the README now embeds
    generated screenshots and a deleted or renamed PNG would otherwise break the
    front page silently.

    Cheap, mechanical, and it has already caught a real break: the safety model was
    split into its own file and one link kept a path relative to the repo root
    instead of to `docs/`.
    """
    broken: list[str] = []
    for md in markdown_files():
        for match in re.finditer(r"\]\(([^)#][^)]*?)\)", md.read_text()):
            target = match.group(1).split("#")[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).exists():
                broken.append(f"{md.relative_to(REPO)} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


def test_data_model_er_diagrams_cover_every_schema_table():
    """`docs/data-model.md` is a hand-maintained mirror of `schema.sql`, so it drifts
    by construction — a new table simply does not appear, and nothing complains.

    The diagrams are deliberately NOT generated: they are split into three blocks
    grouped by concern, and a machine dump of fifteen tables would be less use than
    the curated grouping. So the *grouping* stays human and the *coverage* is
    enforced here. A new table means adding it to whichever block it belongs in.
    """
    schema = (REPO / "agent_core/memory/schema.sql").read_text()
    tables = set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? ([a-z_]+)", schema))
    assert tables, "no CREATE TABLE found — did schema.sql move?"

    doc = (REPO / "docs/data-model.md").read_text()
    blocks = re.findall(r"```mermaid\s*\nerDiagram(.*?)```", doc, re.S)
    assert blocks, "no erDiagram blocks found in data-model.md"

    named: set[str] = set()
    for block in blocks:
        named |= set(re.findall(r"^\s{4}([a-z_]+)\s*\{", block, re.M))
        named |= set(re.findall(r"\b([a-z_]+)\s+\|\|", block))

    missing = sorted(tables - named)
    assert not missing, (
        "these tables exist in schema.sql but appear in no ER diagram: "
        f"{missing}. Add them to docs/data-model.md."
    )
    stale = sorted(named - tables)
    assert not stale, (
        f"these entities are drawn in docs/data-model.md but no longer exist: {stale}"
    )


# ---------------------------------------------------------------------------
# The reference views — architecture.md, classes.md, flows.md, data-model.md
#
# `docs/README.md` groups these four as *"hand-maintained and mirror real code, so
# they drift by construction"*, and until 2026-08-06 only data-model.md had a
# mechanical defence. A docs audit that day found stale content in ALL FOUR, and the
# three unguarded ones generated more drift work in a single day than every plan
# document combined. A reference view naming a module that no longer exists, or a
# class that was renamed, is not untidy — for a reader with no instinct that a
# sentence might be void, it is a latent wrong action.
#
# Everything below anchors on a STRUCTURED form: a path in backticks, a name inside a
# mermaid block, a `namespace.method` token. Never free prose — a sentence mentioning
# "the orchestrator" must not be read as a symbol. That is not fastidiousness: a noisy
# gate is worse than no gate here, because the next agent deletes it and takes its
# real coverage with it. Where an anchor could not be made silent on the current tree
# it was DROPPED rather than loosened; the drops are named in each docstring.
#
# These are set comparisons, not claims, so they are named tests rather than rows in
# `doc_claims.py` — see that module's "What is NOT a claim row".
# ---------------------------------------------------------------------------

REFERENCE_VIEWS = (
    "docs/architecture.md",
    "docs/classes.md",
    "docs/flows.md",
    "docs/data-model.md",
)

_SOURCE_SUFFIXES = (
    ".py", ".ts", ".tsx", ".rs", ".sql", ".sh", ".md",
    ".html", ".css", ".js", ".json", ".yml", ".yaml", ".toml",
)
# A repo-path shape: segments of word characters, dots and dashes. Deliberately
# refuses anything with a space, a glob, a colon or a bracket in it.
_PATH_SHAPE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?$")
_SKIP_TREE = {
    "node_modules", ".git", ".venv", ".claude", ".pytest_cache", ".ruff_cache",
    "__pycache__", "target", "dist",
}


def _repo_paths() -> set[str]:
    """Every file and directory in the repo, repo-relative posix, minus vendored and
    generated trees."""
    out: set[str] = set()
    for path in REPO.rglob("*"):
        rel = path.relative_to(REPO)
        if _SKIP_TREE & set(rel.parts):
            continue
        out.add(rel.as_posix())
    return out


def _inline_code_spans(text: str):
    """`(token, line_number)` for every single-backtick span. Fenced blocks are not
    scanned: a fence is a transcript or an example, and a path inside one is often
    deliberately hypothetical."""
    for match in re.finditer(r"(?<!`)`([^`\n]+)`(?!`)", text):
        yield match.group(1), text.count("\n", 0, match.start()) + 1


def test_reference_views_name_no_path_that_does_not_exist():
    """A backticked repo path is the strongest anchor these documents offer: it is
    unambiguous, it is what an agent greps for, and a stale one sends the next reader
    to a file that is not there.

    **Only the `stale` direction is enforced**, and deliberately. The `missing`
    direction — "every file in the tree is named in the reference views" — is right
    for data-model's tables, which are a closed set, and absurd here: these documents
    are curated maps, and demanding a mention of every file would be exactly the noise
    that gets a gate deleted. (Two genuinely closed sets DO get the `missing`
    treatment; see the two tests below.)

    Resolution is deliberately generous, because the documents write paths three ways
    and all three are legitimate: fully qualified (`agent_core/policy.py`), relative to
    the document (`../ROADMAP.md`), and package-relative or bare shorthand
    (`providers/router.py`, `keychain.rs`). A token resolves if it is a segment-aligned
    suffix of any real path. Generosity costs coverage of *misplacement* and buys
    silence, which is the trade this gate has to win.

    Dropped anchors, both because they could not be made silent without guesswork:
    dotted symbol references (`tools/base.call_is_destructive`), which are not paths,
    and bare extension tokens (`.json`), which name a format.
    """
    paths = _repo_paths()
    by_last: dict[str, list[str]] = {}
    for path in paths:
        by_last.setdefault(path.rsplit("/", 1)[-1], []).append(path)

    def resolves(token: str) -> bool:
        token = token.rstrip("/")
        last = token.rsplit("/", 1)[-1]
        return any(c == token or c.endswith("/" + token) for c in by_last.get(last, []))

    broken: list[str] = []
    for view in REFERENCE_VIEWS:
        md = REPO / view
        text = md.read_text()
        for raw, line_no in _inline_code_spans(text):
            token = raw.split("::")[0].strip()          # `mod.py::symbol` -> `mod.py`
            if not _PATH_SHAPE.match(token):
                continue
            if not (token.endswith("/") or token.endswith(_SOURCE_SUFFIXES)):
                continue
            if token.rstrip("/").rsplit("/", 1)[-1].startswith("."):
                continue                                 # `.json` names a format
            if (md.parent / token).exists() or resolves(token):
                continue
            broken.append(f"{view}:{line_no}: `{raw}`")

    assert not broken, (
        "these reference views name a path that is not in the tree. Point the sentence "
        "at the file that replaced it, or delete the reference — a path that resolves "
        "to nothing sends the next reader looking for code that does not exist:\n  "
        + "\n  ".join(broken)
    )


# --- classes.md ------------------------------------------------------------

# A box a diagram draws on purpose that the code does not have. Written inside the
# mermaid block, beside the box it excuses, in the form
#
#     %% not-in-code: CapabilityTier — retired by owner decision 2026-08-06
#
# This is the `docs/README.md` rule "state what is not true yet" made checkable. It is
# the only escape hatch from the class-name check, and it is not a mute button: the
# reason is mandatory, an orphaned marker fails, and a marker whose class LATER shows
# up in the code fails too.
_NOT_IN_CODE = re.compile(r"^\s*%%\s*not-in-code:\s*(\w+)\s*—\s*(\S.*?)\s*$", re.M)


def _python_classes() -> dict[str, set[str]]:
    """`{class name: every member name reachable on it}` across `agent_core/`.

    The member set is deliberately a generous superset — class-body `def`s and
    assignments, `self.x = ...` anywhere in the body, and members inherited from a
    base class scanned here. A superset only ever *forgives*, so the member check
    stays silent on shape it cannot see while still catching a name that was deleted
    or renamed.
    """
    import ast

    bodies: dict[str, ast.ClassDef] = {}
    for path in (REPO / "agent_core").rglob("*.py"):
        if _SKIP_TREE & set(path.relative_to(REPO).parts):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:                              # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bodies.setdefault(node.name, node)

    def own(node: ast.ClassDef) -> set[str]:
        members: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members.add(stmt.name)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                members.add(stmt.target.id)
            elif isinstance(stmt, ast.Assign):
                members |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                if sub.value.id == "self" and isinstance(sub.ctx, ast.Store):
                    members.add(sub.attr)
        return members

    resolved: dict[str, set[str]] = {}

    def members_of(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if name in resolved:
            return resolved[name]
        node = bodies[name]
        out = own(node)
        for base in node.bases:
            base_name = base.id if isinstance(base, ast.Name) else None
            if base_name and base_name in bodies and base_name not in seen:
                out |= members_of(base_name, seen | {name})
        resolved[name] = out
        return out

    return {name: members_of(name) for name in bodies}


def _class_diagram_blocks() -> list[str]:
    doc = (REPO / "docs/classes.md").read_text()
    blocks = re.findall(r"```mermaid\s*\nclassDiagram(.*?)```", doc, re.S)
    assert blocks, "no classDiagram blocks found in docs/classes.md — did they move?"
    return blocks


def test_class_diagrams_name_no_class_the_code_does_not_have():
    """`docs/classes.md` promises *"the real ones from the code"*. A renamed class is
    the drift that costs most: an agent reads the diagram, greps for the old name,
    finds nothing, and either re-invents it or edits the wrong module.

    `stale` only — the `missing` direction ("every class in `agent_core/` appears in a
    diagram") is exactly the noisy demand this file's docstring warns against; the
    three views are curated and say so.

    A box the code deliberately does not have is legal, but must SAY so, in the
    mermaid block beside it: `%% not-in-code: Name — why`. Six boxes need one today
    and all six are already explained in the surrounding prose — the marker just moves
    the fact somewhere a machine can read it.
    """
    known = set(_python_classes())
    problems: list[str] = []
    for block in _class_diagram_blocks():
        excused = {m.group(1) for m in _NOT_IN_CODE.finditer(block)}
        for name in re.findall(r"^\s*class\s+(\w+)", block, re.M):
            if name not in known and name not in excused:
                problems.append(name)

    assert not problems, (
        f"docs/classes.md draws these classes and agent_core/ has no such class: "
        f"{sorted(problems)}. Rename the box to the class that replaced it, or — if "
        "the box is deliberately ahead of the code — add `%% not-in-code: Name — why` "
        "inside the same mermaid block."
    )


def test_a_not_in_code_marker_is_neither_orphaned_nor_overtaken():
    """The escape hatch needs its own guard, or it becomes the hole.

    Two failure modes, both real: a marker left behind after its box was deleted
    (which quietly excuses a future box of the same name), and a marker still standing
    after the code CAUGHT UP — the class now exists, the diagram is describing it as
    hypothetical, and every reader is told the thing is unbuilt when it shipped. That
    second one is the sentence shape this repo has shipped three times.
    """
    known = set(_python_classes())
    problems: list[str] = []
    for block in _class_diagram_blocks():
        drawn = set(re.findall(r"^\s*class\s+(\w+)", block, re.M))
        for match in _NOT_IN_CODE.finditer(block):
            name = match.group(1)
            if name not in drawn:
                problems.append(
                    f"{name}: marked not-in-code but no box of that name is drawn in "
                    "the same block — delete the marker."
                )
            if name in known:
                problems.append(
                    f"{name}: marked not-in-code, but agent_core/ now defines it. The "
                    "code caught up: delete the marker and amend the prose that calls "
                    "it unbuilt."
                )
    assert not problems, "not-in-code markers in docs/classes.md:\n  " + "\n  ".join(problems)


def test_class_diagram_members_exist_on_the_real_class():
    """The members are the half a reader actually copies — a signature in a diagram is
    what an agent writes a call against.

    Scoped to boxes that map to a real class, so the `not-in-code` boxes never reach
    here. `stale` only, and emphatically so: the file states its members are *"trimmed
    to the load-bearing members"*, so demanding completeness would fight the document's
    own design. Members are matched by NAME, not by signature — argument lists in a
    diagram are illustrative, and parsing them would be the noise that kills the gate.

    This caught a real one on the day it was written: `Tool` was drawn with
    `+undo(snapshot)`, which `tools/base.py` deliberately does not have — the Protocol
    excludes `undo` so that LOW read-only tools are not misdescribed, and the mandatory
    -undo invariant is enforced at registration instead. The diagram was asserting the
    exact misreading that docstring exists to prevent.
    """
    classes = _python_classes()
    problems: list[str] = []
    for block in _class_diagram_blocks():
        for match in re.finditer(r"^\s*class\s+(\w+)\s*\{(.*?)^\s*\}", block, re.S | re.M):
            name, body = match.group(1), match.group(2)
            if name not in classes:
                continue
            for member in re.finditer(r"^\s*[+\-#~](\w+)", body, re.M):
                if member.group(1) not in classes[name]:
                    problems.append(f"{name}.{member.group(1)}")

    assert not problems, (
        "docs/classes.md draws these members and the real class has no such attribute "
        f"or method: {sorted(problems)}. Correct the name, or drop the row — a "
        "signature in a diagram is what the next agent writes a call against."
    )


# --- flows.md and the rest: the wire methods --------------------------------


def _rpc_methods() -> set[str]:
    """Every `namespace.method` string in `agent_core/protocol.py`, the hand-synced
    owner of the wire contract."""
    proto = (REPO / "agent_core/protocol.py").read_text()
    methods = set(re.findall(r'"([a-zA-Z]+\.[a-zA-Z]+)"', proto))
    assert methods, "no method strings found in agent_core/protocol.py — did it move?"
    return methods


def test_reference_views_name_no_rpc_method_that_does_not_exist():
    """`docs/flows.md` opens with *"Method and function names match the code"*, and the
    sequence diagrams are read as a call list. A renamed RPC method leaves a diagram
    describing a frame the server would reject.

    Two collection sites, with different precision rules, because the surrounding
    structure differs:

    * **Inline code spans** anywhere in the four views. The backticks are the
      structure, so any `namespace.method` whose namespace is a real RPC namespace is
      checked.
    * **Unquoted text inside `sequenceDiagram` blocks**, where camelCase is required.
      That is not a stylistic preference, it is the discriminator: the wire boundary is
      camelCase and Python is snake_case, so a camelCase dotted token cannot be an
      attribute access. Without it, flow 1's `provider.send with the tool_result
      appended` — a Python call on a provider object — is indistinguishable from a
      wire method, and one false positive is all it takes.

    Cost of that rule: single-word wire methods (`snapshot.list`, `guards.set`) are
    only covered where they appear in backticks, which is most places. Accepted —
    partial coverage that people trust beats full coverage that gets disabled.

    `stale` only. The `missing` direction would demand that all 77 protocol methods be
    drawn in a flow, which is not what a curated set of fifteen flows is for.
    """
    methods = _rpc_methods()
    namespaces = {m.split(".")[0] for m in methods}
    # A dotted token, not preceded or followed by path/identifier characters — so
    # `data-model.md` and `rpc/workspace.is_trusted` are never mistaken for one.
    token = re.compile(r"(?<![\w./-])([a-z][a-zA-Z]*)\.([a-z][a-zA-Z]*)(?![\w./-])")

    problems: list[str] = []

    def check(name: str, text: str, line_no: int, require_camel: bool) -> None:
        for match in token.finditer(text):
            ns, method = match.group(1), match.group(2)
            if ns not in namespaces:
                continue
            if match.group(0).endswith(_SOURCE_SUFFIXES):    # `keychain.rs` is a path
                continue
            if require_camel and method.islower():
                continue
            if match.group(0) not in methods:
                problems.append(f"{name}:{line_no}: {match.group(0)}")

    for view in REFERENCE_VIEWS:
        text = (REPO / view).read_text()
        for raw, line_no in _inline_code_spans(text):
            check(view, raw, line_no, require_camel=False)

    flows = (REPO / "docs/flows.md").read_text()
    for match in re.finditer(r"```mermaid\s*\nsequenceDiagram(.*?)```", flows, re.S):
        base = flows.count("\n", 0, match.start(1)) + 1
        for offset, line in enumerate(match.group(1).splitlines()):
            check("docs/flows.md", line, base + offset, require_camel=True)

    assert not problems, (
        "these passages name an RPC method that agent_core/protocol.py does not "
        "define. Use the method that replaced it, or drop the arrow — a diagram naming "
        "a frame the server would reject is worse than no diagram. (If the token is a "
        "Python call rather than a wire method, do not write it in the "
        "`namespace.method` shape.):\n  " + "\n  ".join(sorted(set(problems)))
    )


# --- the two closed sets architecture.md claims to enumerate -----------------


def test_architecture_names_every_rpc_namespace_module():
    """`docs/architecture.md` states that `JsonRpcServer` *"is composed from the mixins
    in `agent_core/rpc/` — one module per method namespace"* and then lists them. That
    is a promise about a CLOSED set, which is what makes the `missing` direction fair
    here and unfair for paths or classes.

    It was already broken when this was written: `agent_core/rpc/mcp.py` shipped with
    step 7's phase 1 on 2026-08-06 and the list still had thirteen names.

    The set is derived from the code the same way the server composes it — a module
    defining a `*Mixin(ServerContext)` class — so `base.py` and `constants.py` are
    excluded by construction rather than by a hand-kept ignore list.

    What counts is the BARE namespace name in backticks, not the file path: the doc
    already carried `agent_core/rpc/mcp.py` elsewhere on the day the enumeration was
    missing `mcp`, and accepting that would have kept this green through the exact
    defect it was written for. One word per new namespace is the whole cost.
    """
    modules = sorted(
        path.stem
        for path in (REPO / "agent_core/rpc").glob("*.py")
        if re.search(r"^class \w+Mixin\(ServerContext\)", path.read_text(), re.M)
    )
    assert modules, "no *Mixin(ServerContext) classes found under agent_core/rpc/"

    doc = (REPO / "docs/architecture.md").read_text()
    named = {raw for raw, _ in _inline_code_spans(doc)}
    missing = [m for m in modules if m not in named]
    assert not missing, (
        f"docs/architecture.md enumerates the agent_core/rpc/ namespace mixins and "
        f"omits {missing}. Add each to that list in backticks — an RPC namespace the "
        "architecture never mentions is invisible to anyone reading the map."
    )


def test_architecture_names_every_shell_rust_module():
    """The same closed-set argument, for the highest-trust process. `architecture.md`
    enumerates the Rust modules beside `main.rs`, and its whole subject is what that
    process may do — so a shell module it never mentions is the most expensive kind of
    omission in the doc set.

    It was already broken when this was written: `shell/src-tauri/src/exec.rs` shipped
    with step 5.5's containment work and appeared in no enumeration here.
    """
    modules = sorted(
        path.stem
        for path in (REPO / "shell/src-tauri/src").glob("*.rs")
        if path.stem != "main"
    )
    assert modules, "no Rust modules found under shell/src-tauri/src/"

    doc = (REPO / "docs/architecture.md").read_text()
    named = {raw for raw, _ in _inline_code_spans(doc)}
    missing = [
        m for m in modules
        if not any(t in named for t in (m, f"{m}.rs", f"shell/src-tauri/src/{m}.rs"))
    ]
    assert not missing, (
        f"docs/architecture.md enumerates the Tauri shell's Rust modules and omits "
        f"{missing}. Say in backticks what each one does — this document is the only "
        "map of what the highest-trust process contains."
    )


# ---------------------------------------------------------------------------
# The claims registry — one test, many rows
# ---------------------------------------------------------------------------


def test_no_document_contradicts_a_registered_claim():
    """The generalisation of `G3_RESOLVED_IN_OPEN`, which is the only mechanism that
    has ever caught this repo's drift automatically — flipping it named eight
    offending lines across seven files in one run.

    Each row in `doc_claims.CLAIMS` carries a load-bearing fact, its owning
    document, and the shape a contradiction takes. This iterates them and prints a
    **work order** per offender: file, line, the offending text, what is actually
    true, and the edit to make. *"Docs are inconsistent"* costs a turn of
    investigation; a file and a line gets fixed on the spot.

    Two properties the rows are designed around, both learned the hard way:

    * **Polarity is two-sided.** A check that enforces the PRESENCE of a caveat is
      symmetric under the caveat becoming false, and stays green through exactly the
      drift it was written for. Rows carry `while_true` AND `while_false`, so the
      day an owner constant flips, every document still asserting the old fact fails.
    * **Deference beats copying.** Where a rule cannot tell "asserting" from
      "discussing", the checkable rule is that a restatement must have its owner
      within reach. A copy of a caveat outlives whatever is true today; a pointer at
      the owner does not.

    To add a rule, add a row — see `doc_claims.py`'s module docstring. Do not add a
    test here.
    """
    reports: list[str] = []
    for claim in CLAIMS:
        reports += [offender.work_order() for offender in offenders_for(claim)]
    assert not reports, (
        f"{len(reports)} passage(s) contradict a claim registered in "
        "tests/doc_claims.py. Fix the document, or — if the FACT changed — flip the "
        "claim's owner constant and amend the named lines in the SAME commit:\n\n"
        + "\n\n".join(reports)
    )


def test_every_claim_row_is_well_formed():
    """Structural sanity for the registry itself, so a malformed row fails loudly
    rather than silently guarding nothing.

    The failure mode this exists for: a row whose `holds` is False and whose
    `while_false` is None is *inert* — it looks like coverage in a diff and enforces
    nothing. That is worse than no row, because the next agent trusts it.
    """
    problems: list[str] = []
    ids: set[str] = set()
    for claim in CLAIMS:
        if claim.id in ids:
            problems.append(f"{claim.id}: duplicate id")
        ids.add(claim.id)
        if not (REPO / claim.owner).exists():
            problems.append(f"{claim.id}: owner {claim.owner} does not exist")
        if claim.while_false is not None and not claim.false_state:
            problems.append(
                f"{claim.id}: has a while_false pattern but no false_state sentence, "
                "so flipping `holds` would print a blank work order"
            )
        if not claim.holds and claim.while_false is None:
            problems.append(
                f"{claim.id}: holds=False with no while_false pattern — this row is "
                "INERT and guards nothing. Write the other polarity or delete the row."
            )
        for wrong in (claim.while_true, claim.while_false):
            if wrong is None:
                continue
            re.compile(wrong.pattern)          # raises on a bad pattern
            if wrong.excused_by:
                re.compile(wrong.excused_by)
            if len(wrong.fix) < 40:
                problems.append(
                    f"{claim.id}: `fix` is too short to be a work order — say which "
                    "file to edit and what to write."
                )
    assert not problems, "malformed claim rows:\n  " + "\n  ".join(problems)


# ---------------------------------------------------------------------------
# Documents whose self-description is checkable
# ---------------------------------------------------------------------------


def test_docs_readme_maps_every_document():
    """`docs/README.md` calls itself *"the full map — every file, what it owns"*, and
    `CLAUDE.md` repeats that promise. A claim that specific is checkable, so it is
    checked: a new document that nobody linked is invisible to an agent that reads
    the map instead of listing the directory.

    Scope is deliberately the depth-1 document set plus the immediate
    subdirectories — the map lists design briefs as directories on purpose, and
    enumerating every file inside them would be the kind of noise that gets a gate
    deleted.
    """
    readme = REPO / "docs/README.md"
    linked = {m.group(1).split("#")[0] for m in re.finditer(r"\]\(([^)]+)\)", readme.read_text())}
    linked = {t.rstrip("/") for t in linked}

    missing: list[str] = []
    for path in sorted((REPO / "docs").iterdir()):
        if path.name.startswith("."):
            continue
        rel = path.name.rstrip("/")
        if path.is_dir():
            if rel not in linked and not any(t.startswith(rel + "/") for t in linked):
                missing.append(f"docs/{rel}/")
        elif path.suffix == ".md" and path.name != "README.md" and rel not in linked:
            missing.append(f"docs/{rel}")

    assert not missing, (
        "docs/README.md claims to map every document and does not list these. Add a "
        "row saying what each one OWNS (or, for history, that it settles nothing) — "
        "an unlisted document is invisible to anyone who reads the map:\n  "
        + "\n  ".join(missing)
    )


def test_ci_runs_the_gate_script_for_every_job_it_defines():
    """`scripts/gates.sh` exists because the gate list lived in three places and all
    three disagreed on 2026-08-06 — CI ran `npx tsc --noEmit` (src only), so the
    `tsconfig.test.json` gate that `KNOWN-GAPS.md` recorded as CLOSED had never
    actually run there.

    The script's header states that `ci.yml` calls it. That is the claim, so this is
    the check: every job the workflow defines runs `scripts/gates.sh <job>`, and
    every job the script knows about has a workflow job. A second copy of the list
    cannot reappear without failing here.
    """
    script = (REPO / "scripts/gates.sh").read_text()
    workflow = (REPO / ".github/workflows/ci.yml").read_text()

    case_body = re.search(r"case \"\$JOB\" in(.*?)esac", script, re.S)
    assert case_body, "no `case \"$JOB\"` found in scripts/gates.sh — did it move?"
    known = set(re.findall(r"^\s*(\w+)\)", case_body.group(1), re.M)) - {"all"}
    assert known, "no jobs found in scripts/gates.sh — did the case statement move?"

    called = set(re.findall(r"\./scripts/gates\.sh\s+(\w+)", workflow))
    jobs_block = re.search(r"^jobs:\n(.*)\Z", workflow, re.S | re.M)
    assert jobs_block, "no `jobs:` block in .github/workflows/ci.yml"
    jobs = set(re.findall(r"^  (\w+):$", jobs_block.group(1), re.M))

    assert known == called, (
        f"scripts/gates.sh defines jobs {sorted(known)} but .github/workflows/ci.yml "
        f"runs {sorted(called)}. CI must run the script, never its own copy of the "
        "commands — that divergence is why the script exists."
    )
    assert jobs == called, (
        f"ci.yml defines jobs {sorted(jobs)} but only {sorted(called)} call "
        "scripts/gates.sh. Every job runs the script; a job with inline commands is a "
        "second gate list."
    )


# ---------------------------------------------------------------------------
# Perishable claims
# ---------------------------------------------------------------------------

# The convention (docs/CONVENTIONS.md owns it): an empirical number is written with
# the date it was taken AND the conditions it was taken under, like
#
#     29 ms *(measured 2026-07-31 · app-owned keychain item, self-signed dev build)*
#
# A measurement written as a permanent property is a trap for an agent, which has no
# instinct that a spike result might have been voided by a change elsewhere. This
# repo has already quoted one as a permanent property months after the condition it
# was measured under had changed.
MEASURED_MARKER = re.compile(r"\*\(measured (\d{4}-\d{2}-\d{2})\s*·\s*([^)]*)\)\*")
# Just the opening. Used where a window is too small to contain a long condition —
# well-formedness is the other test's job, not every caller's.
MEASURED_OPENING = re.compile(r"\*\(measured \d{4}-\d{2}-\d{2}\b")


def _without_code(text: str) -> str:
    """Blank out fenced blocks and inline code spans, preserving offsets.

    A marker being *shown* — the template in `CONVENTIONS.md`, an example in a
    fence — is not a marker being *used*, and a gate that cannot tell the two apart
    makes it impossible to document its own convention.
    """
    out = list(text)
    for match in re.finditer(r"```.*?```|`[^`\n]*`", text, re.S):
        for i in range(match.start(), match.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def test_every_measurement_marker_is_well_formed():
    """A marker that does not parse is a marker nobody can trust, and a convention
    nobody can grep for is not a convention.

    Silent by construction on prose that carries no marker — this checks the shape of
    the ones that do, so the cost of the convention is zero until someone uses it
    badly.
    """
    problems: list[str] = []
    for md in markdown_files():
        text = _without_code(md.read_text())
        rel = md.relative_to(REPO).as_posix()
        for match in re.finditer(r"\*\(measured\b[^)]*\)\*", text):
            line_no = text.count("\n", 0, match.start()) + 1
            full = MEASURED_MARKER.match(match.group(0))
            if full is None:
                problems.append(
                    f"{rel}:{line_no}: {match.group(0)[:80]} — expected "
                    "`*(measured YYYY-MM-DD · what it was measured under)*`"
                )
                continue
            try:
                date.fromisoformat(full.group(1))
            except ValueError:
                problems.append(f"{rel}:{line_no}: {full.group(1)} is not a real date")
            if len(full.group(2).strip()) < 8:
                problems.append(
                    f"{rel}:{line_no}: the marker names a date but not the CONDITIONS. "
                    "The condition is the perishable half — a number is only void "
                    "because the thing it was measured under changed."
                )
    assert not problems, "malformed measurement markers:\n  " + "\n  ".join(problems)


def test_a_spike_result_is_marked_perishable():
    """A spike is an experiment, and its result expires when the thing it was run
    against changes. `docs/secrets-and-keychain-plan.md` quoted spike 1's conclusion
    as a permanent property for six days after `sign-and-run.sh` had voided it.

    Narrow on purpose: only a spike reference whose neighbourhood carries a NUMBER
    WITH A UNIT — the perishable part. A spike discussed without a figure is
    narrative and needs no marker, and the design briefs' `ms` durations are design
    specifications rather than measurements, so they never come near this.
    """
    spike = re.compile(r"\bspikes?\s*\d", re.I)
    measurement = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:ms|s|%|MB|KB|GB)\b")
    # A result explicitly retired is already marked perishable, in the strongest way.
    retired = re.compile(r"SUPERSEDED|VOID|no longer true|is (?:now )?history", re.I)
    WINDOW = 260

    problems: list[str] = []
    for md in markdown_files():
        text = md.read_text()
        rel = md.relative_to(REPO).as_posix()
        for match in spike.finditer(text):
            scope = text[max(0, match.start() - WINDOW) : match.end() + WINDOW]
            if not measurement.search(scope):
                continue
            if MEASURED_OPENING.search(scope) or retired.search(scope):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            problems.append(
                f"{rel}:{line_no}: {text.split(chr(10))[line_no - 1].strip()[:110]}"
            )
    assert not problems, (
        "these spike figures read as permanent properties. Mark each perishable with "
        "`*(measured YYYY-MM-DD · the conditions it was measured under)*` — the "
        "condition is the half that goes void — or say plainly that the result has "
        "been superseded. docs/CONVENTIONS.md owns the convention:\n  "
        + "\n  ".join(problems)
    )
