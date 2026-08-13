"""The PORTABLE routine format: what a routine looks like when it leaves this
machine, and the strict reader that lets one back in.

This module is PURE. It builds a dict and it parses a dict. It touches no store,
no registry, no gate, no screening and no RPC method: a caller decides what to do
with what it gets back. That is deliberate, because every question about who may
export, who may import, what card the person sees and what screening runs over a
file a stranger wrote is a separate decision, and none of them should be settled
by accident inside a serializer.

WHY A SECOND FORMAT AT ALL. ``routines.model.routine_to_json`` already turns a
Routine into a dict, and it is the wrong dict for this job in both directions. It
carries the row's identity and every field the plan happens to have, and its
reader (``routine_from_json``) parses TRUSTED DATABASE ROWS: it indexes keys it
expects to be there and believes what it finds, which is correct for bytes this
app wrote and fatal for bytes an unknown person sent. **Do not merge the two.**
The trusting reader stays trusting and fast for the DB path; the reader below
assumes the file is hostile and says so in a sentence a person can act on.

WHITELIST, NEVER BLACKLIST. :func:`to_portable` names the fields it copies, one
by one. A field added to ``Routine`` or ``RoutineStep`` tomorrow therefore leaves
this machine only when somebody writes its name here and thinks about it, which
is the property a blacklist cannot have. ``tests/test_routine_portable.py`` pins
the exact key set so the decision cannot be made by omission.

TWO NARROWINGS, and both are refusals rather than repairs:

  1. **A command step is refused in BOTH directions.** Not exported, and a file
     carrying one is refused before anything else about it is read. A shell line
     the person typed into their own Developer session is one object; the same
     shell line arriving as a one-click artifact from somebody else, to be run by
     pressing a button in a library list, is a different object with a different
     risk, and the profile check that governs the first one was never asked about
     the second. Until an owner decision says otherwise, the format simply cannot
     express a command. (The "does this plan carry a developer ability" logic in
     ``rpc/routines.py::_routine_needs_dev`` is the REFERENCE for the shape of
     this question, never an import: it lives one layer up because it also asks
     the registry, and ``routines/`` may not import ``tools/``.)

  2. **An absolute path is refused, never scrubbed.** A variable default or an
     args_template leaf that looks like an absolute path, or that carries a home
     directory, stops the export with a sentence naming the field. Scrubbing it
     would hand the author a file that quietly does something other than what
     their routine does, and they would find out on somebody else's machine. The
     honest move is to tell the author which field to turn into a variable.

WHAT THE FORMAT CARRIES: the version, the name, the description, the variables
(name / prompt / default) and the steps (step_id, tool_id, args_template,
depends_on, on_failure, model_role). What it does NOT carry is listed at
:data:`_EXCLUDED_FIELDS`, each with the reason it is left behind.

Imports: the standard library and ``routines/model.py``. Nothing else, ever -
this file must stay importable from any layer that has a Routine in its hands.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from agent_core.routines.model import Routine, RoutineStep, RoutineVariable

#: The format version. It is the FIRST key of the file (``addison_routine``) so a
#: reader can decide whether it understands the bytes before it looks at them.
PORTABLE_VERSION = 1

#: Older versions and how to bring them forward. Empty at version 1, because
#: there is no version 0 in the world. The default arm of the lookup is REFUSAL,
#: never a best-effort read: a file from a version this build has no migration
#: for is a file this build cannot claim to understand.
_MIGRATIONS: dict[int, Any] = {}

# Fields that exist on Routine / RoutineStep and deliberately do not travel. This
# tuple is documentation, not machinery (the whitelist above is the machinery),
# and the test suite reads it as the list of names that must be absent.
_EXCLUDED_FIELDS = (
    # ``id``: import mints a fresh uuid4. A shared file must not collide with a
    # local row, and must not be able to masquerade as one somebody already has.
    "id",
    # ``created_in_mode``: display-only provenance about the SENDER's machine.
    # Carrying it would recreate, one machine removed, exactly the mistake owner
    # decision 2026-08-08 closed: deciding what a profile may do from a stamp
    # instead of from what the artifact IS.
    "created_in_mode",
    # ``created_from_conversation_id``: a dangling foreign key on the receiver's
    # database, and a leak of the sender's chat ids.
    "created_from_conversation_id",
    # run_count / last_run_at / anything from routine_runs: the sender's history
    # of their own machine. It says nothing about the plan and is not the
    # receiver's to hold.
    "run_count",
    "last_run_at",
    # ``model_id``: provider trivia the receiver may not have configured, and a
    # pinned id from a build or an account that is not theirs. ``model_role`` DOES
    # travel: "run this step on the local model" is a claim about privacy, and it
    # means the same thing on every machine.
    "model_id",
    # ``command``: see narrowing 1 in the module docstring.
    "command",
)

# --- ceilings ---------------------------------------------------------------
# Each is a bound on COST, not a judgement about the routine. A file arriving
# from anywhere gets read by a machine belonging to somebody who did not write
# it, so every walk below has to terminate on the worst input somebody can
# author. Generous enough that no honest routine meets them.

#: Serialized size of the whole file. A real routine is a few kilobytes.
_MAX_BYTES = 256 * 1024
#: Steps in one routine. The conversational builder produces a handful.
_MAX_STEPS = 200
#: Variables in one routine.
_MAX_VARIABLES = 100
#: How deeply an args_template may nest. Templated arguments are shallow data;
#: anything past this is a structure aimed at whatever walks it, not at a tool.
_MAX_DEPTH = 12
#: Length of any single string in the file.
_MAX_STRING = 8 * 1024

_ON_FAILURE_VALUES = ("abort", "skip", "ask_user")
_MODEL_ROLES = ("primary", "local")

#: Tool ids from an outside tool server. They name SOMEBODY ELSE'S server, which
#: the receiver either does not have or, worse, has under the same name pointing
#: somewhere else. Refused on the way in.
_MCP_PREFIX = "mcp:"

#: Looks-like-an-absolute-path. Windows drive letters are included even though v1
#: ships on macOS: the file travels, so the check should recognise a path the
#: SENDER's machine produced whatever that machine was.
_ABSOLUTE_PATH = re.compile(r"^(?:/|~|[A-Za-z]:[\\/]|\\\\)")
#: Home directories anywhere inside a longer string.
_HOME_DIR = re.compile(r"(?:/Users/|/home/|\\Users\\|~/)")


def _looks_like_a_path(value: str) -> bool:
    return bool(_ABSOLUTE_PATH.match(value.strip()) or _HOME_DIR.search(value))


def _path_leaf(value: Any, trail: str) -> str | None:
    """First leaf under ``value`` that looks like an absolute path, described by
    where it sits, or None. Depth-bounded like everything else here."""
    stack: list[tuple[Any, str, int]] = [(value, trail, 0)]
    while stack:
        node, where, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        if isinstance(node, str):
            if _looks_like_a_path(node):
                return where
        elif isinstance(node, dict):
            for key, sub in node.items():
                stack.append((sub, f"{where}.{key}", depth + 1))
        elif isinstance(node, list):
            for index, sub in enumerate(node):
                stack.append((sub, f"{where}[{index}]", depth + 1))
    return None


# --- export -----------------------------------------------------------------


def to_portable(routine: Routine) -> dict | str:
    """The shareable form of ``routine``, or a plain sentence saying why it cannot
    be shared. Every field is named here on purpose (see the module docstring):
    this is a whitelist, so a new field on Routine or RoutineStep does not travel
    until somebody adds it below."""
    for step in routine.steps:
        if step.command is not None:
            return (
                f"This routine cannot be shared because the step \"{step.step_id}\" runs a "
                "command on your computer. Shared routines can only use Addison's own "
                "actions, never commands."
            )

    for variable in routine.variables:
        if isinstance(variable.default, str) and _looks_like_a_path(variable.default):
            return (
                f"This routine cannot be shared because the setting \"{variable.name}\" has a "
                "default that points at a folder on your computer. Leave that default empty "
                "so the person you share it with can choose their own, then try again."
            )

    for step in routine.steps:
        where = _path_leaf(step.args_template, "")
        if where is not None:
            field = where.lstrip(".") or "an argument"
            return (
                f"This routine cannot be shared because the step \"{step.step_id}\" points at a "
                f"folder on your computer, in \"{field}\". Turn that into a question the "
                "routine asks when it runs, then try again."
            )

    return {
        # First key, so a reader meets the version before anything else.
        "addison_routine": {"version": PORTABLE_VERSION},
        "name": routine.name,
        "description": routine.description,
        "variables": [
            {"name": v.name, "prompt": v.prompt, "default": v.default}
            for v in routine.variables
        ],
        "steps": [
            {
                "step_id": s.step_id,
                "tool_id": s.tool_id,
                "args_template": s.args_template,
                "depends_on": list(s.depends_on),
                "on_failure": s.on_failure,
                "model_role": s.model_role,
            }
            for s in routine.steps
        ],
    }


# --- import -----------------------------------------------------------------


def parse_portable(data: Any) -> Routine | str:
    """A Routine built from ``data``, or one plain sentence saying why not.

    NEVER raises, whatever ``data`` is, and never returns a half-built routine:
    every check below runs before the first object is constructed. The id is
    minted here: the file does not carry one.

    Order of the checks, and it is deliberate: shape, then size, then version,
    then command steps, then everything else. Version comes before the fields
    because a file from a version this build does not know is not a file whose
    fields mean anything; commands come before the rest because if the answer is
    "no" it should not depend on the file being otherwise well-formed.
    """
    try:
        return _parse(data)
    except Exception:  # noqa: BLE001 - a reader of hostile bytes owes a sentence, never a traceback
        return (
            "This file is not a routine Addison can read. Ask the person who sent it to "
            "share it again."
        )


def _parse(data: Any) -> Routine | str:
    if not isinstance(data, dict):
        return "This file is not a routine. A shared routine is a single block of settings."

    size = _size_of(data)
    if size is None:
        return "This file is not a routine Addison can read. Some of it is not plain text."
    if size > _MAX_BYTES:
        return (
            "This file is too big to be a shared routine. Ask the person who sent it to "
            "share a smaller one."
        )

    version_refusal = _check_version(data)
    if version_refusal is not None:
        return version_refusal

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        return "This file is missing its list of steps, so there is nothing to run."
    for raw in raw_steps:
        if isinstance(raw, dict) and raw.get("command") is not None:
            return (
                "This shared routine runs a command on your computer, so Addison will not "
                "add it. Shared routines can only use Addison's own actions."
            )

    if len(raw_steps) > _MAX_STEPS:
        return (
            f"This shared routine has too many steps. Addison can read up to {_MAX_STEPS}."
        )
    if not raw_steps:
        return "This shared routine has no steps in it, so there is nothing to run."

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return "This shared routine has no name."
    if len(name) > _MAX_STRING:
        return "The name of this shared routine is too long."

    description = data.get("description", "")
    if not isinstance(description, str) or len(description) > _MAX_STRING:
        return "The description of this shared routine is not written in a way Addison can read."

    variables = _parse_variables(data.get("variables", []))
    if isinstance(variables, str):
        return variables

    steps = _parse_steps(raw_steps)
    if isinstance(steps, str):
        return steps

    dependency_refusal = _check_dependencies(steps)
    if dependency_refusal is not None:
        return dependency_refusal

    return Routine(
        # A FRESH id. The file carries none, and a shared routine must never be
        # able to take the place of a routine already on this machine.
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        variables=variables,
        steps=steps,
    )


def _size_of(data: dict) -> int | None:
    try:
        return len(json.dumps(data).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return None


def _check_version(data: dict) -> str | None:
    header = data.get("addison_routine")
    if not isinstance(header, dict):
        return (
            "This file does not look like a routine shared from Addison, so Addison will "
            "not open it."
        )
    version = header.get("version")
    if version is None:
        return "This shared routine does not say which version of Addison made it."
    # bool is an int in Python, and True is not a version.
    if not isinstance(version, int) or isinstance(version, bool):
        return "This shared routine's version is not a whole number, so Addison cannot read it."
    if version > PORTABLE_VERSION:
        return (
            "This shared routine was made by a newer version of Addison. Update Addison, "
            "then try again."
        )
    if version < PORTABLE_VERSION:
        migration = _MIGRATIONS.get(version)
        if migration is None:
            # The default arm is refusal, not a hopeful read.
            return (
                "This shared routine was made by an older version of Addison that this one "
                "cannot read. Ask the person who sent it to share it again from an updated "
                "Addison."
            )
    return None


def _parse_variables(raw: Any) -> list[RoutineVariable] | str:
    if not isinstance(raw, list):
        return "The questions this shared routine asks are not in a form Addison can read."
    if len(raw) > _MAX_VARIABLES:
        return (
            f"This shared routine asks for too many settings. Addison can read up to "
            f"{_MAX_VARIABLES}."
        )
    variables: list[RoutineVariable] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            return "One of the settings in this shared routine is not written correctly."
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > _MAX_STRING:
            return "One of the settings in this shared routine has no usable name."
        if name in seen:
            return f"This shared routine asks for \"{name}\" twice, so Addison cannot read it."
        seen.add(name)
        prompt = entry.get("prompt", "")
        if not isinstance(prompt, str) or len(prompt) > _MAX_STRING:
            return f"The question for \"{name}\" in this shared routine is not readable text."
        default = entry.get("default")
        if default is not None and (not isinstance(default, str) or len(default) > _MAX_STRING):
            return f"The suggested answer for \"{name}\" in this shared routine is not text."
        variables.append(RoutineVariable(name=name, prompt=prompt, default=default))
    return variables


def _parse_steps(raw_steps: list) -> list[RoutineStep] | str:
    steps: list[RoutineStep] = []
    seen: set[str] = set()
    for raw in raw_steps:
        if not isinstance(raw, dict):
            return "One of the steps in this shared routine is not written correctly."

        step_id = raw.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip() or len(step_id) > _MAX_STRING:
            return "One of the steps in this shared routine has no usable name."
        if step_id in seen:
            return f"This shared routine has two steps called \"{step_id}\"."
        seen.add(step_id)

        tool_id = raw.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id.strip() or len(tool_id) > _MAX_STRING:
            return f"The step \"{step_id}\" does not say which action it uses."
        if tool_id.startswith(_MCP_PREFIX):
            return (
                f"The step \"{step_id}\" uses a tool from somebody else's server, which is "
                "not something Addison can add from a shared file."
            )

        args = raw.get("args_template", {})
        if not isinstance(args, dict):
            return f"The settings for the step \"{step_id}\" are not written correctly."
        depth_problem = _too_deep_or_untyped(args)
        if depth_problem is not None:
            return f"The settings for the step \"{step_id}\" {depth_problem}"

        depends_on = raw.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
            return f"The step \"{step_id}\" does not say correctly which steps come before it."
        if len(depends_on) > _MAX_STEPS:
            return f"The step \"{step_id}\" lists too many steps before it."

        on_failure = raw.get("on_failure", "abort")
        if on_failure not in _ON_FAILURE_VALUES:
            return (
                f"The step \"{step_id}\" does not say what to do if it goes wrong, in a way "
                "Addison understands."
            )

        model_role = raw.get("model_role")
        if model_role is not None and model_role not in _MODEL_ROLES:
            return f"The step \"{step_id}\" asks for a kind of model Addison does not have."

        steps.append(
            RoutineStep(
                step_id=step_id,
                tool_id=tool_id,
                args_template=args,
                depends_on=list(depends_on),
                on_failure=on_failure,
                # Never from the file: refused above, and left unset here so the
                # two facts cannot drift apart.
                command=None,
                model_role=model_role,
                # Not carried by the format (see _EXCLUDED_FIELDS).
                model_id=None,
            )
        )
    return steps


def _too_deep_or_untyped(args: dict) -> str | None:
    """None when ``args`` is shallow, plain JSON data. Otherwise the tail of a
    sentence saying what is wrong with it."""
    stack: list[tuple[Any, int]] = [(args, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_DEPTH:
            return "are packed too deeply for Addison to read."
        if isinstance(node, str):
            if len(node) > _MAX_STRING:
                return "contain a piece of text that is too long."
        elif isinstance(node, dict):
            for key, sub in node.items():
                if not isinstance(key, str):
                    return "are not written correctly."
                stack.append((sub, depth + 1))
        elif isinstance(node, list):
            for sub in node:
                stack.append((sub, depth + 1))
        elif not isinstance(node, (int, float, bool, type(None))):
            return "are not written correctly."
    return None


def _check_dependencies(steps: list[RoutineStep]) -> str | None:
    """Every ``depends_on`` names a step in this file, and the graph has no loop.
    A loop would be a routine that can never start; a missing name would be a
    step waiting on nothing."""
    known = {step.step_id for step in steps}
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in known:
                return (
                    f"The step \"{step.step_id}\" waits for a step called \"{dependency}\", "
                    "which is not in this file."
                )
            if dependency == step.step_id:
                return f"The step \"{step.step_id}\" waits for itself, so it could never start."

    # Kahn's algorithm: whatever is left when nothing more can be started is a loop.
    incoming = {step.step_id: set(step.depends_on) for step in steps}
    ready = [name for name, deps in incoming.items() if not deps]
    done: set[str] = set()
    while ready:
        name = ready.pop()
        done.add(name)
        for other, deps in incoming.items():
            if other in done or name not in deps:
                continue
            deps.discard(name)
            if not deps:
                ready.append(other)
    if len(done) != len(steps):
        stuck = sorted(set(incoming) - done)
        return (
            f"The steps {', '.join(chr(34) + s + chr(34) for s in stuck)} wait for each "
            "other in a circle, so this routine could never start."
        )
    return None
