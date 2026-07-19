"""Guidance skills — a DECLARATIVE steering primitive (owner-directed 2026-07-20).

================================ SAFETY FRAME ================================
A skill is a named, user-authored plain-TEXT guidance note that steers HOW Addison
approaches tasks. When enabled, its text is appended to the TRANSIENT per-turn
system prompt (see rpc/conversation.py). That is ALL a skill is:

  * NOT executable — not a tool, not a routine, not a widget. There is no code,
    ``eval``, expression, or template field anywhere on it. It is text. This
    respects SAFE-mode invariant 1 (no arbitrary code/shell execution).

  * A skill's text can NEVER widen what Addison is allowed to DO. It can ASK
    Addison to do something, but every side-effecting tool call STILL hits the
    PermissionGate exactly as before — the ToolRegistry + PermissionGate remain
    the sole authority. This mirrors the Routine invariant ("a Routine never gets
    permissions beyond what the user granted live"): steering ≠ permission.

Because a skill only steers and never executes, it is available in BOTH SAFE and
OPEN modes — there is no ``created_in_mode`` gating (unlike routines/widgets).

Skills are user-authored LOCAL content only. Import/sharing of skills is a v2 item
(CLAUDE.md "Do NOT build yet") and is deliberately absent here.

This module is a standalone declarative module (like ``policy.py``): it must NOT
import from ``agent_core.tools`` / ``providers`` / ``routines`` — nothing about a
skill touches execution.
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

# Bounds for a skill's fields (validate_skill enforces them). Kept small and plain:
# a name is a short label, the guidance is a note, not an essay.
MAX_NAME_LEN = 60
MAX_INSTRUCTIONS_LEN = 2000


@dataclass
class Skill:
    """One guidance skill — mirrors the ``skills`` table 1:1 (schema.sql)."""

    id: str
    name: str
    instructions: str
    enabled: bool
    created_at: int


def validate_skill(name: object, instructions: object) -> str | None:
    """Return a plain-language reason the skill is invalid, or None if it's fine.

    Params are typed ``object`` because they arrive straight off the (untrusted) RPC
    params — a non-string is rejected here rather than trusted. Plain, non-technical
    messages (CLAUDE.md): the person sees these verbatim."""
    if not isinstance(name, str) or not name.strip():
        return "Give your skill a name."
    if len(name) > MAX_NAME_LEN:
        return "Keep the name short."
    if not isinstance(instructions, str) or not instructions.strip():
        return "Add some guidance for this skill."
    if len(instructions) > MAX_INSTRUCTIONS_LEN:
        return "Keep the guidance under 2000 characters."
    return None


def compose_skills_prompt(skills: list[Skill]) -> str:
    """The text block appended to the per-turn system prompt for the ENABLED skills.

    Pure function — no I/O, no store, no side effects. Given the already-filtered
    enabled skills, return a leading blank line, a short header, then one bullet per
    skill. An empty list returns "" so the composed prompt is byte-identical to
    today's (the no-skills case must not change a single character)."""
    if not skills:
        return ""
    lines = ["", "The person has turned on these guidance notes — follow them:"]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.instructions}")
    return "\n".join(lines)
