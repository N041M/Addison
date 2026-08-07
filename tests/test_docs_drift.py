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

**Every gate here has two halves**, and the second is not optional: it fires on the
drift it was written for, *and* it is silent on prose a future document would
legitimately contain. The silence half is asserted with `assert_silent`, next to the
gate it belongs to; [`gate_precision.py`](gate_precision.py) owns why a false
positive costs more than a missed one here. That is also why each check below is a
module-level **scanner** — a function from text to findings — rather than a loop in a
test body: a precision test of a copy of the logic tests the copy.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from tests.doc_claims import (
    CLAIMS,
    REPO,
    Claim,
    Offender,
    Wrong,
    findings_in_text,
    markdown_files,
    offenders_for,
)
from tests.gate_precision import assert_silent


def _broken_links(text: str, parent: Path) -> list[str]:
    """Relative link targets in ``text`` that do not exist, resolved from ``parent``."""
    broken: list[str] = []
    for match in re.finditer(r"\]\(([^)#][^)]*?)\)", text):
        target = match.group(1).split("#")[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (parent / target).exists():
            broken.append(target)
    return broken


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
        broken += [
            f"{md.relative_to(REPO)} -> {target}"
            for target in _broken_links(md.read_text(), md.parent)
        ]
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


def test_the_link_check_is_silent_on_links_that_resolve():
    """The four shapes the doc set actually writes, all legitimate, none flagged.

    Known and deliberate non-exclusion: a link inside a fenced block is checked like
    any other. Nothing in the tree documents markdown syntax by example, so excluding
    fences would buy nothing and cost the ability to catch a broken link in a copyable
    snippet — but if a document ever does, that is the tightening to make.
    """
    docs = REPO / "docs"
    assert_silent(
        "test_every_markdown_link_resolves",
        lambda text: _broken_links(text, docs),
        {
            "a sibling document that exists": "See [`SAFETY.md`](SAFETY.md) for the floors.",
            "a link out of docs/ into the repo root": "Status lives in [ROADMAP](../ROADMAP.md).",
            "an anchor into a real file": "See [G3](SAFETY.md#the-four-global-floors).",
            "a bare in-page anchor, which names no file": "Jump to [the floors](#floors).",
            "an external URL": "The [Register coverage](https://example.com/a.md) is history.",
            "a directory link, which the map writes for the design briefs": (
                "The v4 direction is [`design-brief-dark/`](design-brief-dark/)."
            ),
            "a mailto: link": "Ask [the owner](mailto:someone@example.com).",
        },
    )


def _er_entities(doc: str) -> set[str]:
    """Entity names drawn in the ``erDiagram`` blocks of ``doc``.

    Two forms, because the document uses both: a declaration block
    (`conversations {`, four-space indented) and the left side of a relationship
    (`conversations ||--o{ messages`). Attribute lines sit deeper and carry no brace,
    so they never enter the set.
    """
    named: set[str] = set()
    for block in re.findall(r"```mermaid\s*\nerDiagram(.*?)```", doc, re.S):
        named |= set(re.findall(r"^\s{4}([a-z_]+)\s*\{", block, re.M))
        named |= set(re.findall(r"\b([a-z_]+)\s+\|\|", block))
    return named


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
    assert re.findall(r"```mermaid\s*\nerDiagram(.*?)```", doc, re.S), (
        "no erDiagram blocks found in data-model.md"
    )
    named = _er_entities(doc)

    missing = sorted(tables - named)
    assert not missing, (
        "these tables exist in schema.sql but appear in no ER diagram: "
        f"{missing}. Add them to docs/data-model.md."
    )
    stale = sorted(named - tables)
    assert not stale, (
        f"these entities are drawn in docs/data-model.md but no longer exist: {stale}"
    )


def test_the_er_extraction_reads_entities_and_not_the_rest_of_the_diagram():
    """The `stale` half of the coverage check is a set difference, so anything the
    extraction wrongly *calls* an entity becomes a phantom table nobody can delete.

    A mermaid ER block is mostly not entity names: attribute rows, type keywords,
    relationship labels, PK/FK markers, quoted comments, `%%` notes. Feeding a block
    made of exactly those and asking for names outside the two it declares is the
    precision half — the `missing` direction is proved silent by the gate above
    passing on the real document.
    """
    sample = """
Prose above the block mentions the `usage_log` table, which is not drawn here.

```mermaid
erDiagram
    conversations ||--o{ messages : contains
    conversations ||--o{ memory_facts : sources
    %% grouped by concern, not dumped from the schema
    conversations {
        TEXT id PK
        TEXT title
        TEXT summary "v2"
        TEXT continued_from_conversation_id FK "v2, self-FK"
    }
    messages {
        TEXT id PK
        TEXT conversation_id FK
        TEXT role "user|assistant|tool"
        INTEGER created_at
    }
    memory_facts {
        TEXT id PK
        INTEGER confirmed_by_user
    }
```
"""
    drawn = {"conversations", "messages", "memory_facts"}
    assert_silent(
        "test_data_model_er_diagrams_cover_every_schema_table",
        lambda text: sorted(_er_entities(text) - drawn),
        {
            "attributes, types, labels and comments are not entities": sample,
            "a document with no ER block at all names nothing": (
                "The schema is in `agent_core/memory/schema.sql`; `usage_log` is pruned."
            ),
        },
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


def _unresolved_paths(text: str, parent: Path) -> list[str]:
    """`line_no: \\`token\\`` for every backticked path-shaped token in ``text`` that
    resolves to nothing, from a document sitting in ``parent``."""
    by_last: dict[str, list[str]] = {}
    for path in _repo_paths():
        by_last.setdefault(path.rsplit("/", 1)[-1], []).append(path)

    def resolves(token: str) -> bool:
        token = token.rstrip("/")
        last = token.rsplit("/", 1)[-1]
        return any(c == token or c.endswith("/" + token) for c in by_last.get(last, []))

    broken: list[str] = []
    for raw, line_no in _inline_code_spans(text):
        token = raw.split("::")[0].strip()              # `mod.py::symbol` -> `mod.py`
        if not _PATH_SHAPE.match(token):
            continue
        if not (token.endswith("/") or token.endswith(_SOURCE_SUFFIXES)):
            continue
        if token.rstrip("/").rsplit("/", 1)[-1].startswith("."):
            continue                                     # `.json` names a format
        if (parent / token).exists() or resolves(token):
            continue
        broken.append(f"{line_no}: `{raw}`")
    return broken


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
    broken: list[str] = []
    for view in REFERENCE_VIEWS:
        md = REPO / view
        broken += [f"{view}:{f}" for f in _unresolved_paths(md.read_text(), md.parent)]

    assert not broken, (
        "these reference views name a path that is not in the tree. Point the sentence "
        "at the file that replaced it, or delete the reference — a path that resolves "
        "to nothing sends the next reader looking for code that does not exist:\n  "
        + "\n  ".join(broken)
    )


def test_the_path_check_is_silent_on_every_shape_that_is_not_a_broken_path():
    """The anchor most likely to go noisy, because backticks hold far more than paths.

    Every sample here is a token the reference views already write, or one the next
    honest edit would: the three legitimate ways a path is spelled, and the six shapes
    that merely look like one. A single false positive would make this gate the first
    thing the next agent deletes, and it would take the stale-path coverage with it.
    """
    docs = REPO / "docs"
    assert_silent(
        "test_reference_views_name_no_path_that_does_not_exist",
        lambda text: _unresolved_paths(text, docs),
        {
            "a fully qualified path that exists": (
                "The mode is derived in `agent_core/policy.py`, never persisted."
            ),
            "a package-relative path, which the views write constantly": (
                "`ROUTING_STRATEGIES` in `providers/router.py` is the authority."
            ),
            "a bare filename": "The seatbelt profile is built in `exec.rs`.",
            "a path relative to the document": "Status lives in `../ROADMAP.md`.",
            "a path with a symbol suffix": "See `snapshot_manager.py::capture`.",
            "a bare extension, which names a format and not a file": (
                "Every payload is written twice, the second time as plain `.json`."
            ),
            "a dotted symbol reference, which is not a path": (
                "Destructiveness is per-call (`tools/base.call_is_destructive`)."
            ),
            "a token with a space in it is not path-shaped": (
                "`provider.connect` validates with `GET /v1/models` and nothing more."
            ),
            "an id shape with colons in it": "Ids are `mcp:<server>:<tool>`, never bare.",
            "a hex colour and a duration": (
                "Near-black paper (`#0C0C0D`), and the scramble settles in `180ms`."
            ),
            "a dot-prefixed directory": "The venv lives at `agent_core/.venv/`.",
            "a prose sentence with no backticks at all": (
                "The orchestrator resolves a model every turn and holds no provider."
            ),
        },
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


def _class_blocks_in(doc: str) -> list[str]:
    """The body of every ```` ```mermaid classDiagram ```` block in ``doc``."""
    return re.findall(r"```mermaid\s*\nclassDiagram(.*?)```", doc, re.S)


def _class_diagram_blocks() -> list[str]:
    blocks = _class_blocks_in((REPO / "docs/classes.md").read_text())
    assert blocks, "no classDiagram blocks found in docs/classes.md — did they move?"
    return blocks


def _classes_the_code_lacks(blocks: list[str], known: set[str]) -> list[str]:
    """Boxes drawn in ``blocks`` that name no class in ``known`` and carry no marker."""
    problems: list[str] = []
    for block in blocks:
        excused = {m.group(1) for m in _NOT_IN_CODE.finditer(block)}
        for name in re.findall(r"^\s*class\s+(\w+)", block, re.M):
            if name not in known and name not in excused:
                problems.append(name)
    return problems


def _marker_problems(blocks: list[str], known: set[str]) -> list[str]:
    problems: list[str] = []
    for block in blocks:
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
    return problems


def _member_problems(blocks: list[str], classes: dict[str, set[str]]) -> list[str]:
    problems: list[str] = []
    for block in blocks:
        for match in re.finditer(r"^\s*class\s+(\w+)\s*\{(.*?)^\s*\}", block, re.S | re.M):
            name, body = match.group(1), match.group(2)
            if name not in classes:
                continue
            for member in re.finditer(r"^\s*[+\-#~](\w+)", body, re.M):
                if member.group(1) not in classes[name]:
                    problems.append(f"{name}.{member.group(1)}")
    return problems


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
    problems = _classes_the_code_lacks(_class_diagram_blocks(), set(_python_classes()))
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
    problems = _marker_problems(_class_diagram_blocks(), set(_python_classes()))
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
    problems = _member_problems(_class_diagram_blocks(), _python_classes())
    assert not problems, (
        "docs/classes.md draws these members and the real class has no such attribute "
        f"or method: {sorted(problems)}. Correct the name, or drop the row — a "
        "signature in a diagram is what the next agent writes a call against."
    )


# A document shaped like `classes.md`: prose that names classes, one mermaid block that
# draws real ones with real members, relationship arrows, and one deliberately-ahead box
# carrying its marker. Every line here is legitimate; none of the three class gates may
# say a word about any of it.
_LEGITIMATE_CLASS_DOC = """
## Tools and permissions

`ToolRegistry` is the one registry; `visible_tools(mode)` is a filtered view over it,
never a second registry. The retired `CapabilityTier` idea would have let a spec
declare its own powers — the closed kind list replaced it, and `McpToolAdapter` is a
target shape rather than code.

```mermaid
classDiagram
    %% not-in-code: McpToolAdapter — Phase-2 step 7, not built; nothing registers an MCP tool yet
    class ToolRegistry {
        +register(tool, dev_only, open_only, allow_missing_undo)
        +visible_tools(mode)
        +is_dev_only(tool_id) bool
    }
    class McpToolAdapter {
        +to_definition() ToolDefinition
    }
    class PermissionGate {
        +authorize(tool_id, mode, destructive, detail, guards, trusted) PermissionStatus
        +revoke_all()
    }
    ToolRegistry --> PermissionGate : consulted by
    ToolRegistry o-- ToolDefinition
```
"""


def test_the_class_gates_are_silent_on_a_legitimately_written_diagram():
    """All three class anchors at once, on the document shape they are meant to allow.

    The four things that must NOT be read as a stale class box: a class named in
    **prose** rather than drawn (`CapabilityTier` — the whole reason the anchor is
    scoped to the mermaid block), a relationship arrow, a box excused by a
    `not-in-code` marker, and that marker itself once the box beside it exists.
    """
    known = set(_python_classes())
    classes = _python_classes()
    for name in ("ToolRegistry", "PermissionGate"):
        assert name in known, f"{name} was renamed — re-point this precision sample"
    assert "CapabilityTier" not in known, (
        "agent_core/ now defines CapabilityTier, so it is no longer an example of a "
        "class the code deliberately lacks — pick another for this sample"
    )
    samples = {"a diagram drawn the way docs/classes.md is written": _LEGITIMATE_CLASS_DOC}
    assert_silent(
        "test_class_diagrams_name_no_class_the_code_does_not_have",
        lambda text: _classes_the_code_lacks(_class_blocks_in(text), known),
        samples,
    )
    assert_silent(
        "test_a_not_in_code_marker_is_neither_orphaned_nor_overtaken",
        lambda text: _marker_problems(_class_blocks_in(text), known),
        samples,
    )
    assert_silent(
        "test_class_diagram_members_exist_on_the_real_class",
        lambda text: _member_problems(_class_blocks_in(text), classes),
        samples,
    )


# --- flows.md and the rest: the wire methods --------------------------------


def _rpc_methods() -> set[str]:
    """Every `namespace.method` string in `agent_core/protocol.py`, the hand-synced
    owner of the wire contract."""
    proto = (REPO / "agent_core/protocol.py").read_text()
    methods = set(re.findall(r'"([a-zA-Z]+\.[a-zA-Z]+)"', proto))
    assert methods, "no method strings found in agent_core/protocol.py — did it move?"
    return methods


# A dotted token, not preceded or followed by path/identifier characters — so
# `data-model.md` and `rpc/workspace.is_trusted` are never mistaken for one.
_DOTTED_TOKEN = re.compile(r"(?<![\w./-])([a-z][a-zA-Z]*)\.([a-z][a-zA-Z]*)(?![\w./-])")


def _rpc_problems_in(text: str, methods: set[str], *, sequence_blocks: bool) -> list[str]:
    """`line_no: token` for every `namespace.method` in ``text`` that ``methods`` lacks.

    Two collection sites with different precision rules — inline code spans anywhere,
    and (when ``sequence_blocks``) unquoted text inside ``sequenceDiagram`` blocks,
    where camelCase is the discriminator between a wire method and a Python attribute
    access. The test's docstring argues both.
    """
    namespaces = {m.split(".")[0] for m in methods}
    problems: list[str] = []

    def check(fragment: str, line_no: int, require_camel: bool) -> None:
        for match in _DOTTED_TOKEN.finditer(fragment):
            ns, method = match.group(1), match.group(2)
            if ns not in namespaces:
                continue
            if match.group(0).endswith(_SOURCE_SUFFIXES):    # `keychain.rs` is a path
                continue
            if require_camel and method.islower():
                continue
            if match.group(0) not in methods:
                problems.append(f"{line_no}: {match.group(0)}")

    for raw, line_no in _inline_code_spans(text):
        check(raw, line_no, require_camel=False)

    if sequence_blocks:
        for match in re.finditer(r"```mermaid\s*\nsequenceDiagram(.*?)```", text, re.S):
            base = text.count("\n", 0, match.start(1)) + 1
            for offset, line in enumerate(match.group(1).splitlines()):
                check(line, base + offset, require_camel=True)
    return problems


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
    problems: list[str] = []
    for view in REFERENCE_VIEWS:
        text = (REPO / view).read_text()
        problems += [
            f"{view}:{finding}"
            for finding in _rpc_problems_in(text, methods, sequence_blocks=True)
        ]

    assert not problems, (
        "these passages name an RPC method that agent_core/protocol.py does not "
        "define. Use the method that replaced it, or drop the arrow — a diagram naming "
        "a frame the server would reject is worse than no diagram. (If the token is a "
        "Python call rather than a wire method, do not write it in the "
        "`namespace.method` shape.):\n  " + "\n  ".join(sorted(set(problems)))
    )


def test_the_camelcase_rule_holds_on_a_python_call_inside_a_sequence_diagram():
    """The false positive this gate was built around, asserted rather than argued.

    Flow 1 draws `provider.send with the tool_result appended` — a Python attribute
    access on a provider object, structurally identical to the wire method
    `provider.list`, in a namespace that really exists. One such finding is all it
    takes for the whole gate to be deleted, so the camelCase discriminator gets a test
    of its own alongside the other shapes that are not wire methods.
    """
    methods = _rpc_methods()
    assert "provider.list" in methods, (
        "`provider.list` is gone from protocol.py — this sample depended on `provider` "
        "being a real RPC namespace, which is what makes the false positive possible"
    )
    assert_silent(
        "test_reference_views_name_no_rpc_method_that_does_not_exist",
        lambda text: _rpc_problems_in(text, methods, sequence_blocks=True),
        {
            "a snake_case Python call inside a sequence diagram is not a wire method": (
                "```mermaid\nsequenceDiagram\n"
                "    ORC->>PR: provider.send(messages, tools, effort, timeout)\n"
                "    PR-->>ORC: response with tool_calls\n"
                "    ORC->>PR: provider.send with the tool_result appended\n"
                "    Note over ORC: model_router.resolve() runs every turn\n"
                "```\n"
            ),
            "a real camelCase method in a sequence diagram": (
                "```mermaid\nsequenceDiagram\n"
                "    WV->>SH: invoke send_to_core, conversation.sendMessage\n"
                "```\n"
            ),
            "a real method in backticks": "`provider.list` carries status only, never a key.",
            "a filename whose stem is a real namespace": (
                "The shell's `keychain.rs` is the only reader; see `agent_core/rpc/mcp.py`."
            ),
            "a module-qualified symbol, refused by the path lookbehind": (
                "`rpc/workspace.is_trusted` answers the question the gate asks."
            ),
            "a hyphenated filename": "The tables are drawn in `data-model.md`.",
            "prose outside backticks is never scanned — the documented trade": (
                "A future provider.whatever written in plain prose is not a wire method."
            ),
        },
    )


# --- the two closed sets architecture.md claims to enumerate -----------------


def _missing_mixins(modules: list[str], doc: str) -> list[str]:
    """RPC namespace modules the document does not name as a BARE backticked word."""
    named = {raw for raw, _ in _inline_code_spans(doc)}
    return [m for m in modules if m not in named]


def _missing_rust_modules(modules: list[str], doc: str) -> list[str]:
    """Shell modules the document names in none of the three accepted spellings."""
    named = {raw for raw, _ in _inline_code_spans(doc)}
    return [
        m for m in modules
        if not any(t in named for t in (m, f"{m}.rs", f"shell/src-tauri/src/{m}.rs"))
    ]


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

    missing = _missing_mixins(modules, (REPO / "docs/architecture.md").read_text())
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

    missing = _missing_rust_modules(modules, (REPO / "docs/architecture.md").read_text())
    assert not missing, (
        f"docs/architecture.md enumerates the Tauri shell's Rust modules and omits "
        f"{missing}. Say in backticks what each one does — this document is the only "
        "map of what the highest-trust process contains."
    )


def test_the_enumeration_checks_accept_every_way_a_module_is_legitimately_named():
    """These two are the only `missing`-direction gates in the file, so their failure
    mode is the opposite of the others: they go noisy by refusing a name the document
    *did* write, and the fix a reader would then reach for is to write it twice.

    The rpc check is deliberately strict — the BARE namespace word, because accepting
    `agent_core/rpc/mcp.py` would have kept it green through the exact omission it was
    written for. The Rust check is deliberately generous, accepting three spellings.
    Both are pinned here so neither drifts into the other's rule by accident.
    """
    assert_silent(
        "test_architecture_names_every_rpc_namespace_module",
        lambda text: _missing_mixins(["mcp", "guards"], text),
        {
            "the bare namespace word in backticks, in a sentence": (
                "One module per namespace: `guards` holds the two Custom dials, and "
                "`mcp` answers list/add/remove while nothing is callable."
            ),
            "named in a table cell": "| `mcp` | tool servers |\n| `guards` | the dials |",
        },
    )
    assert_silent(
        "test_architecture_names_every_shell_rust_module",
        lambda text: _missing_rust_modules(["exec", "keychain"], text),
        {
            "the bare module stem": "`exec` generates the seatbelt; `keychain` reads keys.",
            "the file name": "`exec.rs` builds the profile, `keychain.rs` holds G1.",
            "the full repo path": (
                "`shell/src-tauri/src/exec.rs` and `shell/src-tauri/src/keychain.rs`."
            ),
        },
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


# Prose each row must stay SILENT on: sentences the tree already carries, and the ones
# the next honest edit would produce. A row is only as good as its quiet half — the
# module docstring in doc_claims.py says a gate that cries wolf gets deleted by the next
# agent and takes its real coverage with it, and this is where that is checked rather
# than promised. Two rows were TIGHTENED when these samples were first run; both are
# named in the comments beside their patterns.
_LEGITIMATE_PROSE: dict[str, dict[str, str]] = {
    "automation-authoring-built-arming-not": {
        "recounting the claim that WAS true before phase 2, in the past tense": (
            "Until authoring shipped, nothing in the tree can author automation was "
            "simply true, and G2 held trivially."
        ),
        "BUILD-LOG quoting the retired standing claim to call it false": (
            "The tree's standing claim was \"nothing can author or arm automation, so "
            "G2 holds trivially\" — correct about the machinery, false about the "
            "trusted-folder path."
        ),
        "the fact stated at today's polarity, which is the point of the row": (
            "Addison can author an automation as an inert draft; nothing in the tree "
            "can ARM one, because the arming surface is phase 3."
        ),
        "prose about ARMING being absent, which must never be mistaken for authoring": (
            "There is no arming surface anywhere in the tree, so a saved automation "
            "has never been handed to launchd."
        ),
    },
    "g3-open-mode-polarity": {
        "recounting the five days it WAS true, in the past tense": (
            "For five days this floor was **overclaimed in OPEN**, and the record of "
            "why is worth keeping."
        ),
        "prose that describes the retired rule as retired": (
            "G3 used to be overclaimed in OPEN; step 5.5 closed it on 2026-07-31."
        ),
        "the spec's row, which quotes the old wording to call it spent": (
            "The 2026-07-26 scope correction that made this row read *\"overclaimed in "
            "OPEN\"* is spent; SAFETY.md owns the floor's scope."
        ),
        "today's polarity stated plainly": (
            "The seatbelt profile and the pre-gate denylist mean the floor holds in "
            "OPEN too, since 2026-07-31."
        ),
    },
    "g3-guarantee-defers-to-owner": {
        "a restatement with its owner on the same line": (
            "A gate that could deny a restore would make \"the restore path is itself "
            "unbreakable\" false — [SAFETY.md](SAFETY.md) owns the floor's scope."
        ),
        "a restatement whose pointer sits two paragraphs down": (
            "Neither the user nor the model can drive Addison into an unrecoverable "
            "configuration.\n\nThe machinery is described in flows 9 and 10; the two "
            "edges the floor does not reach are named in docs/SAFETY.md, which owns "
            "this and states each in full."
        ),
        "prose about G3 that never restates the guarantee": (
            "G3 is the operative meaning of safety here: snapshots are automatic "
            "before a risky change and available on command."
        ),
    },
    "artifact-disabling-polarity": {
        "the owner's own past-tense account of the old behaviour": (
            "*They used to be hidden: filtered out of `routine.list` and "
            "`widget.list` entirely.* The refusal was never the problem."
        ),
        "a subordinate clause recounting the old rule": (
            "The card read oddly, as routines made in OPEN were then hidden in SAFE."
        ),
        "a TOOL being hidden from the SAFE view, which is true and unrelated": (
            "The two `open_only` file tools are hidden from the SAFE view and refused "
            "at dispatch outside OPEN."
        ),
        "today's rule stated plainly": (
            "Routines and widgets created in OPEN are listed but disabled under SAFE, "
            "and return untouched when Developer is active again."
        ),
    },
    "mcp-transport-http-only": {
        "the decision stated in the owner's own words": (
            "So a server row stores a **URL and never a command**, and nothing in "
            "phase 1 can spawn a process."
        ),
        "the DENIAL of the field, which reads like the field": (
            "There is no launch command column in `mcp_servers`, because stdio would "
            "mean the core starting an executable outside the seatbelt."
        ),
        "the history of the decision, in the past tense": (
            "Transport was a genuinely open question until 2026-08-06, when the owner "
            "answered it: HTTP only for v1."
        ),
        "stdio described as scheduled rather than supported": (
            "stdio is not rejected, it is scheduled — phase 5, behind containment."
        ),
    },
    "mcp-is-dev-only-in-v1": {
        "the WIDGET vocabulary sentence, which is true and about something else": (
            "SAFE admits only the non-destructive set of widget kinds, and that set is "
            "hard-coded rather than declared."
        ),
        "invariant 2 keeping a no-undo tool OUT of the SAFE view": (
            "A mutating tool with no `undo()` simply cannot be LOW, so it can never "
            "reach SAFE whatever a server claims about it."
        ),
        "the pre-decision constraint quoted while saying it was deferred": (
            "The sketch said in SAFE only read-only or genuinely undo-able MCP tools "
            "are admitted. That question is deferred: MCP is dev-only for v1."
        ),
    },
    # Samples for the FALSE side, which is the one active since phase 3 shipped
    # (2026-08-07). The rule now hunts documents still promising that nothing a tool
    # server offers can run; what it must not flag is prose recounting the phase
    # that was true of, or describing the gate that stands in front of a call.
    "mcp-tools-are-not-callable": {
        "a recounting that names the phase it is describing": (
            "Phase 2 shipped a row that read: Addison can see this tool but can't use "
            "it yet."
        ),
        "the same recounting, wrapped between the phase and the quote": (
            "Every row in a section says what protects the person. Until\nphase 3 that "
            "was \"Addison can see this tool but can't use it yet\" — the sentence the "
            "core answered with."
        ),
        "the gate described honestly, which is a limit and not an absence": (
            "You can't run any of them without approving the card Addison shows first."
        ),
        "a sentence about the PERSON using a tool server, not about Addison": (
            "You can use a tool server to give Addison abilities it doesn't ship with."
        ),
        "the shipped claim, which pairs the ability with the safeguard": (
            "Addison can now use a tool server's tools in Developer, and it asks you "
            "before each one."
        ),
        "prose about discovery that never mentions running anything": (
            "A discovered tool registers namespaced `mcp:<server>:<tool>`, dev-only, "
            "HIGH and destructive unconditionally."
        ),
    },
    "redaction-is-a-backstop": {
        "the honest form the tree already writes, with the limit in the same breath": (
            "Redaction strips the credential shapes it knows on the way to the model "
            "and the audit trail records that it happened — but an unrecognised or "
            "deliberately-encoded secret still passes."
        ),
        "the plain-language form, which quantifies over what Addison RECOGNISES": (
            "Anything a server sends back has the passwords and keys Addison "
            "recognises stripped out of it before a model sees it."
        ),
        "G1, which is a floor about Addison's OWN keys and not about this at all": (
            "API keys never reach the frontend/webview or SQLite. Keychain only, read "
            "at the moment of use."
        ),
        "the rule stated as a rule, which must not trip the rule": (
            "It reduces exposure; it does not eliminate it, and no doc may describe "
            "it as elimination."
        ),
        "an ordinary sentence about where redaction runs": (
            "Every result's text is redacted, and the kinds ride back on the "
            "ToolResult so both dispatch paths can record them."
        ),
        "the audit column, which lists kinds rather than promising completeness": (
            "`redacted` lists the KINDS the redactor stripped on the way to the "
            "model, never the values."
        ),
    },
    "retired-amendment-has-no-precedence": {
        "the retired rule quoted as a quotation": (
            "It kept a \"where we differ, the amendment wins\" rule long after its "
            "content had been merged."
        ),
        "prose describing the rule in order to kill it": (
            "The rule that the amendment wins was retired on 2026-07-27; it is minutes, "
            "not law."
        ),
        "the banner every reference view carries": (
            "Do not consult the amendment to settle a question — it is a historical "
            "record, folded into the authoritative docs and retired 2026-07-27."
        ),
    },
    "live-dev-signing-script": {
        "the superseded script named as superseded": (
            "`scripts/sign-dev-binary.sh` is the superseded manual predecessor; "
            "`sign-and-run.sh` is what signs each dev build now."
        ),
        "the one-time certificate step, which is what that script is still for": (
            "The one-time certificate creation — including the TRUST step people get "
            "stuck on — is in `scripts/sign-dev-binary.sh`'s header."
        ),
        "prose about signing that names neither script": (
            "An unsigned `cargo build` is ad-hoc signed, so each rebuild looks like a "
            "new app to the keychain."
        ),
    },
    "gate-list-owner": {
        "a fenced block that points at the script": (
            "Run every gate exactly as CI runs them:\n\n"
            "```bash\n./scripts/gates.sh            # or: python | frontend | rust\n```\n"
        ),
        "gate tools named in PROSE while describing a closed gap": (
            "CI ran `npx tsc --noEmit`, which checks src only, and `npm run lint` with "
            "no --max-warnings=0, so warnings passed. Both are closed."
        ),
        "a fenced block that runs something other than the gates": (
            "```bash\ncd shell && npm run tauri dev\n```\n"
        ),
    },
}


def test_the_claims_registry_is_silent_on_legitimate_prose():
    """The other half of `test_no_document_contradicts_a_registered_claim`.

    That test proves a row FIRES; flipping an owner constant is how you watch it. What
    nothing proved until now is that a row stays QUIET on the sentence a careful writer
    would produce — and quiet is the property that decides whether the row survives.
    Every row here recounts history, quotes the wording it replaced, or states the
    decision plainly: all three are shapes the doc set writes constantly.

    A new row must bring its samples, which is what the coverage assertion below is
    for. Writing them is also the cheapest review a row gets: two rows were tightened
    the day this test was written, both because a *correct* sentence tripped them.
    """
    missing = [claim.id for claim in CLAIMS if claim.id not in _LEGITIMATE_PROSE]
    assert not missing, (
        f"these claim rows have no precision samples: {missing}. A row needs both "
        "halves — prove it fires by flipping its owner constant, and prove it is "
        "silent by adding legitimate prose to _LEGITIMATE_PROSE that it must not flag."
    )
    stale = [key for key in _LEGITIMATE_PROSE if key not in {c.id for c in CLAIMS}]
    assert not stale, f"samples for rows that no longer exist: {stale}"

    for claim in CLAIMS:
        assert_silent(
            f"claim `{claim.id}`",
            lambda text, claim=claim: findings_in_text(claim, text),
            _LEGITIMATE_PROSE[claim.id],
        )


def test_a_work_order_admits_that_the_row_itself_could_be_wrong():
    """Nothing in `doc_claims.py` verifies that a row's `true_state` is what its owner
    document actually says — it is a hand-written constant, checked by a human reading
    both. So a subtly wrong row does not merely fail to help: it fails the build on
    correct prose and hands the next agent a file:line instructing them to write the
    wrong sentence, which is strictly worse than having no row for that fact.

    One line in the shared formatter is the difference between a gate that propagates
    an error and one that invites a check, and it belongs in the formatter rather than
    in eight `fix` strings so no row can be added without it.
    """
    claim = CLAIMS[0]
    order = Offender(claim, "docs/example.md", 12, "some offending line").work_order()
    assert "this row is wrong" in order, (
        "the work order no longer admits the row could be wrong. Restore that line in "
        "doc_claims.THIS_ROW_MAY_BE_WRONG — a work order that reads as certain is how "
        "one bad row becomes a doc-set-wide error."
    )
    assert claim.owner in order, "the work order must name the owner document to check"
    assert "{owner}" not in order, "THIS_ROW_MAY_BE_WRONG was not formatted"


def test_every_claim_row_is_well_formed():
    """Structural sanity for the registry itself, so a malformed row fails loudly
    rather than silently guarding nothing.

    The failure mode this exists for: a row whose `holds` is False and whose
    `while_false` is None is *inert* — it looks like coverage in a diff and enforces
    nothing. That is worse than no row, because the next agent trusts it.
    """
    problems = _row_problems(CLAIMS)
    assert not problems, "malformed claim rows:\n  " + "\n  ".join(problems)


def test_the_row_checker_accepts_the_two_legitimate_row_shapes():
    """Both shapes `doc_claims.Claim` documents: a two-sided polarity row, and the
    one-sided row for a fact with no meaningful opposite (`while_false` None with
    `holds` True). The second is the one a structural check is most likely to reject by
    accident, and rejecting it would push the next author into writing a fake opposite
    polarity — a pattern that guards nothing, which is the exact defect this checker
    exists to catch.
    """
    two_sided = Claim(
        id="sample-two-sided",
        owner="docs/SAFETY.md",
        true_state="The floor holds in both modes.",
        while_true=Wrong(
            pattern=r"holds in neither",
            fix="Amend the sentence, or delete it and link to docs/SAFETY.md.",
        ),
        false_state="The floor is limited in OPEN.",
        while_false=Wrong(
            pattern=r"holds in both",
            fix="The floor is limited again — amend it, or link to docs/SAFETY.md.",
        ),
    )
    one_sided = Claim(
        id="sample-one-sided",
        owner="docs/CONVENTIONS.md",
        true_state="`sign-and-run.sh` is the live mechanism.",
        while_true=Wrong(
            pattern=r"sign-dev-binary",
            fix="Name `sign-and-run.sh` beside it, or mark the mention as history.",
            excused_by=r"supersed|predecessor",
            window=400,
        ),
    )
    assert not _row_problems((two_sided, one_sided)), (
        "the well-formedness checker rejects a legitimately shaped row; a one-sided "
        "row is explicitly allowed by doc_claims.Claim's docstring"
    )


def _row_problems(claims) -> list[str]:
    problems: list[str] = []
    ids: set[str] = set()
    for claim in claims:
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
    return problems


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
    entries = [
        (path.name, path.is_dir())
        for path in sorted((REPO / "docs").iterdir())
        if not path.name.startswith(".") and (path.is_dir() or path.suffix == ".md")
    ]
    missing = _unmapped_documents((REPO / "docs/README.md").read_text(), entries)

    assert not missing, (
        "docs/README.md claims to map every document and does not list these. Add a "
        "row saying what each one OWNS (or, for history, that it settles nothing) — "
        "an unlisted document is invisible to anyone who reads the map:\n  "
        + "\n  ".join(missing)
    )


def test_the_map_check_accepts_every_way_the_map_actually_links():
    """The map's own table writes links four ways, and this gate reads all four as
    "listed". Getting that wrong would demand a second, redundant link for a document
    the map already covers — noise on a document set whose entire discipline is *one*
    mention per topic.
    """
    readme = (
        "| [`SAFETY.md`](SAFETY.md) | The four floors. |\n"
        "| [`ROADMAP`](../ROADMAP.md) | Status, and only status. |\n"
        "| [`step-7-mcp-plan.md`](step-7-mcp-plan.md#5-decisions) | Transport. |\n"
        "| [`design-brief-dark/`](design-brief-dark/IMPLEMENTATION.md) | The v4 look. |\n"
        "| [`screenshots/`](screenshots/) | Generated, owns no prose. |\n"
    )
    entries = [
        ("SAFETY.md", False),
        ("step-7-mcp-plan.md", False),
        ("design-brief-dark", True),
        ("screenshots", True),
        ("README.md", False),          # the map does not list itself
    ]
    assert_silent(
        "test_docs_readme_maps_every_document",
        lambda text: _unmapped_documents(text, entries),
        {
            "a plain link, an anchored link, a link INTO a directory, and the map itself": (
                readme
            ),
        },
    )


def _unmapped_documents(readme: str, entries: list[tuple[str, bool]]) -> list[str]:
    """Names in ``entries`` (`(name, is_dir)`) that ``readme`` links nowhere."""
    linked = {m.group(1).split("#")[0] for m in re.finditer(r"\]\(([^)]+)\)", readme)}
    linked = {t.rstrip("/") for t in linked}

    missing: list[str] = []
    for name, is_dir in entries:
        rel = name.rstrip("/")
        if is_dir:
            if rel not in linked and not any(t.startswith(rel + "/") for t in linked):
                missing.append(f"docs/{rel}/")
        elif name != "README.md" and rel not in linked:
            missing.append(f"docs/{rel}")
    return missing


def _gate_jobs(script: str, workflow: str) -> tuple[set[str], set[str], set[str]]:
    """`(jobs the script knows, jobs the workflow calls it for, jobs the workflow
    defines)`."""
    case_body = re.search(r"case \"\$JOB\" in(.*?)esac", script, re.S)
    assert case_body, "no `case \"$JOB\"` found in scripts/gates.sh — did it move?"
    known = set(re.findall(r"^\s*(\w+)\)", case_body.group(1), re.M)) - {"all"}
    assert known, "no jobs found in scripts/gates.sh — did the case statement move?"

    called = set(re.findall(r"\./scripts/gates\.sh\s+(\w+)", workflow))
    jobs_block = re.search(r"^jobs:\n(.*)\Z", workflow, re.S | re.M)
    assert jobs_block, "no `jobs:` block in .github/workflows/ci.yml"
    jobs = set(re.findall(r"^  (\w+):$", jobs_block.group(1), re.M))
    return known, called, jobs


def test_the_gate_job_extraction_reads_a_workflow_and_not_its_scenery():
    """The three sets have to be read out of two files that are mostly *not* job names:
    a shell script full of function definitions and here-doc text, and a workflow whose
    steps, `name:` strings and nested keys outnumber its jobs several times over.

    Any of that scenery read as a job name makes the two sets disagree and the gate
    fails on a green tree — the noisiest possible failure, on the one check whose whole
    job is that CI and the script cannot diverge.
    """
    script = """
say() { printf '\\n=== %s ===\\n' "$1"; }
gates_python() { ruff check .; }
case "$JOB" in
    python)   gates_python ;;
    frontend) gates_frontend ;;
    all)      gates_python; gates_frontend ;;
    *)        printf 'unknown job\\n' >&2; exit 2 ;;
esac
"""
    workflow = """
name: ci
concurrency:
  group: ci-${{ github.ref }}
jobs:
  python:
    name: python (ruff · pyright · pytest)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Gates
        run: ./scripts/gates.sh python
  frontend:
    name: frontend (eslint · tsc · vitest · build)
    defaults:
      run:
        working-directory: shell
    steps:
      - name: Gates
        run: ./scripts/gates.sh frontend
"""
    known, called, jobs = _gate_jobs(script, workflow)
    assert known == called == jobs == {"python", "frontend"}, (
        "the extraction disagrees with a workflow and script that AGREE: "
        f"script={sorted(known)} called={sorted(called)} defined={sorted(jobs)}. "
        "`*)` is not a job, `concurrency:` is not a job, and a `name:` inside a step "
        "is not a call."
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
    known, called, jobs = _gate_jobs(
        (REPO / "scripts/gates.sh").read_text(),
        (REPO / ".github/workflows/ci.yml").read_text(),
    )
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
        problems += _measurement_problems(md.read_text(), md.relative_to(REPO).as_posix())
    assert not problems, "malformed measurement markers:\n  " + "\n  ".join(problems)


def test_the_measurement_gate_costs_nothing_until_someone_uses_the_form_badly():
    """A convention gate has to be free for everyone who is not using the convention,
    or it becomes a tax on writing at all — and it must not fire on the convention
    being *documented*, which would make it impossible to explain itself.

    `CONVENTIONS.md` owns the form and shows it in a fenced block; `_without_code`
    exists for exactly that, so a shown marker and a used marker are different things.
    """
    assert_silent(
        "test_every_measurement_marker_is_well_formed",
        lambda text: _measurement_problems(text, "sample.md"),
        {
            "a well-formed marker with its conditions": (
                "A warm read costs 29 ms *(measured 2026-07-31 · a warm read of an "
                "app-owned keychain item from the signing binary itself)*."
            ),
            "the template being SHOWN in a fenced block, as CONVENTIONS.md shows it": (
                "The exact form is:\n\n```\n29 ms *(measured 2026-07-31 · what it was "
                "measured under)*\n```\n"
            ),
            "the template shown in an inline code span": (
                "Write it as `*(measured YYYY-MM-DD · what it was measured under)*`."
            ),
            "prose with a number and no marker at all": (
                "Retention is 50 snapshots / 30 days, whichever keeps more."
            ),
            "the word measured used as an ordinary verb": (
                "Which install this is is measured, not inferred."
            ),
        },
    )


def _measurement_problems(text: str, rel: str) -> list[str]:
    text = _without_code(text)
    problems: list[str] = []
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
    return problems


def test_a_spike_result_is_marked_perishable():
    """A spike is an experiment, and its result expires when the thing it was run
    against changes. `docs/secrets-and-keychain-plan.md` quoted spike 1's conclusion
    as a permanent property for six days after `sign-and-run.sh` had voided it.

    Narrow on purpose: only a spike reference whose neighbourhood carries a NUMBER
    WITH A UNIT — the perishable part. A spike discussed without a figure is
    narrative and needs no marker, and the design briefs' `ms` durations are design
    specifications rather than measurements, so they never come near this.
    """
    problems: list[str] = []
    for md in markdown_files():
        problems += _spike_problems(md.read_text(), md.relative_to(REPO).as_posix())
    assert not problems, (
        "these spike figures read as permanent properties. Mark each perishable with "
        "`*(measured YYYY-MM-DD · the conditions it was measured under)*` — the "
        "condition is the half that goes void — or say plainly that the result has "
        "been superseded. docs/CONVENTIONS.md owns the convention:\n  "
        + "\n  ".join(problems)
    )


def _spike_problems(text: str, rel: str) -> list[str]:
    spike = re.compile(r"\bspikes?\s*\d", re.I)
    measurement = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:ms|s|%|MB|KB|GB)\b")
    # A result explicitly retired is already marked perishable, in the strongest way.
    retired = re.compile(r"SUPERSEDED|VOID|no longer true|is (?:now )?history", re.I)
    WINDOW = 260

    problems: list[str] = []
    for match in spike.finditer(text):
        scope = text[max(0, match.start() - WINDOW) : match.end() + WINDOW]
        if not measurement.search(scope):
            continue
        if MEASURED_OPENING.search(scope) or retired.search(scope):
            continue
        line_no = text.count("\n", 0, match.start()) + 1
        problems.append(f"{rel}:{line_no}: {text.split(chr(10))[line_no - 1].strip()[:110]}")
    return problems


def test_the_spike_gate_is_narrow_enough_to_leave_ordinary_prose_alone():
    """This is the widest net in the file — a 520-character window around any "spike N"
    — so it is the one most likely to catch something innocent and get itself deleted.

    Three ways a spike figure is already honest (a marker, an explicit supersession,
    no figure at all) and two ways a number near the word "spike" is not a spike result
    at all. The design briefs' `ms` durations are the standing example: they are
    specifications for how long an animation should take, not measurements of anything.
    """
    assert_silent(
        "test_a_spike_result_is_marked_perishable",
        lambda text: _spike_problems(text, "sample.md"),
        {
            "a spike result carrying its marker": (
                "Spike 1 read the item in 29 ms *(measured 2026-07-31 · a warm read "
                "from the signing binary on the owner's machine)*."
            ),
            "a spike result said plainly to be superseded": (
                "Spike 1's 12 ms conclusion is SUPERSEDED — `sign-and-run.sh` voided "
                "the condition it was taken under."
            ),
            "a spike discussed with no figure, which is narrative": (
                "Spike 2 answered the shape question: the vault is a destination, not "
                "a step, and it stays behind its named triggers."
            ),
            "a design specification that happens to be in ms": (
                "The character-scramble settles over 180 ms and fadeRise over 120 ms; "
                "both are no-ops under prefers-reduced-motion."
            ),
            "a numbered item that is not a spike": (
                "Step 5.5 item 3 closed the last edge, and coverage sat at 90% after."
            ),
        },
    )
