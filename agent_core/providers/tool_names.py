"""Tool names in the form the cloud model APIs accept.

A tool id is Addison's own name for a tool. It is stored in ``tool_audit``, in
grants and in routines, and it must not change. Most ids are plain words such as
``calculator``. A tool discovered from a tool server has the id
``mcp:<server name>:<tool>`` (``mcp_catalog.mcp_tool_id``), and the server name is
whatever the person typed, spaces included.

The vendors restrict the characters and the length of a tool name. Each rule below
was read from the vendor's published reference on 2026-09-30:

- Anthropic, a tool's ``name``: "Must match the regex ``^[a-zA-Z0-9_-]{1,128}$``."
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- OpenAI Chat Completions, ``FunctionObject.name``: "Must be a-z, A-Z, 0-9, or
  contain underscores and dashes, with a maximum length of 64."
  https://platform.openai.com/docs/api-reference/chat/create (read from the
  published OpenAPI file, https://github.com/openai/openai-openapi, ``openapi.yaml``)
- Gemini API, ``FunctionDeclaration.name`` allows "underscores, colons, dots, and
  dashes, with a maximum length of 128". ``FunctionCall.name`` and
  ``FunctionResponse.name`` allow only "underscores and dashes, with a maximum
  length of 128". https://ai.google.dev/api/generate-content
- Google Cloud's reference for the same ``FunctionDeclaration`` adds "Must start
  with a letter or an underscore."
  https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/Shared.Types/FunctionDeclaration

The rule used here is all of those at once. A name starts with an ASCII letter or
an underscore, continues with ASCII letters, digits, underscores and dashes, and is
at most 64 characters long. A colon and a space both break it, which is why every
tool-server tool was refused before this module existed (KNOWN-BUGS 18).

:func:`wire_name` maps a tool id to a name that follows the rule. An id that
already follows it is returned unchanged, so every built-in tool keeps its name on
the wire byte for byte. Any other id has each character outside the rule replaced
by an underscore and is cut short enough to leave room for a suffix. The suffix is
an underscore and the first 12 hex digits of the SHA-256 of the whole original id.
The hash is taken before anything is replaced or cut, so two ids that differ only
in a replaced character, or only after the cut, still get different names. The
function is pure, so an id gets the same name on every request. That matters
because the adapters replay past tool calls from the conversation under their
names, and a name that changed between two requests would no longer match.

A 48-bit hash makes two ids sharing a name very unlikely, and :func:`wire_names`
makes it impossible to send. It builds the per-request table from name back to id
and refuses the request if two different ids would go out under one name.

:func:`tool_id_for` maps a name the model returned back to its tool id. A name that
is not in the table comes back unchanged, which sends a tool that is no longer
offered to the orchestrator's unknown-tool refusal exactly as before.

The Anthropic, OpenAI and Google adapters use this module. The OpenAI adapter also
serves the OpenAI-compatible custom server, so that server gets the same names. The
module-boundary rule applies, and nothing here imports from ``tools/`` or
``routines/``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping

from agent_core.providers.base import ProviderRequestRejected

#: The longest name every vendor above accepts (OpenAI's limit).
WIRE_NAME_MAX_CHARS = 64

#: The strictest common rule. Used with ``fullmatch``, so a trailing newline cannot
#: slip past the way it can with ``$``.
_WIRE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")

#: One character outside the rule. The classes are spelled out in ASCII on purpose,
#: because ``\w`` would let a non-ASCII letter through.
_OUTSIDE_RULE = re.compile(r"[^A-Za-z0-9_-]")

#: How many hex digits of the SHA-256 go on the end of a rewritten name.
_HASH_HEX_DIGITS = 12

#: Said when two tools would reach the model under one name. Every such collision
#: involves a rewritten id. In practice every rewritten id comes from a tool server,
#: so the next step names tool servers.
SAME_NAME_REFUSAL = (
    "Two of your tools ended up with the same name, so Addison didn't send this. "
    "Turn off one of your tool servers and try again."
)


def wire_name(tool_id: str) -> str:
    """The name ``tool_id`` goes out under. Pure, so the same id always gives the
    same name. See the module docstring for the rule and the reasoning."""
    if _WIRE_NAME.fullmatch(tool_id):
        return tool_id
    digest = hashlib.sha256(tool_id.encode("utf-8", "surrogatepass")).hexdigest()
    readable = _OUTSIDE_RULE.sub("_", tool_id)
    if not readable or not (readable[0].isalpha() or readable[0] == "_"):
        # An empty id, or one that starts with a digit or a dash, gets a leading
        # underscore so that the first character follows the rule.
        readable = "_" + readable
    readable = readable[: WIRE_NAME_MAX_CHARS - _HASH_HEX_DIGITS - 1]
    return f"{readable}_{digest[:_HASH_HEX_DIGITS]}"


def wire_names(tool_ids: Iterable[str]) -> dict[str, str]:
    """The table from wire name back to tool id, for the tools one request offers.

    Raises ``ProviderRequestRejected`` with :data:`SAME_NAME_REFUSAL` before
    anything is sent when two different ids would share a name. The vendors refuse
    duplicate tool names, and a reply naming one of them could not be mapped back
    to the tool the model meant."""
    table: dict[str, str] = {}
    for tool_id in tool_ids:
        name = wire_name(tool_id)
        earlier = table.get(name)
        if earlier is not None and earlier != tool_id:
            raise ProviderRequestRejected(SAME_NAME_REFUSAL)
        table[name] = tool_id
    return table


def tool_id_for(name: str, table: Mapping[str, str] | None) -> str:
    """The tool id behind a name the model returned. A name the table does not hold
    is returned as it arrived, and the orchestrator refuses it as an unknown tool
    if nothing is registered under it."""
    if table is None:
        return name
    return table.get(name, name)
