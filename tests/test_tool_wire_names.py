"""Tool names on the wire (KNOWN-BUGS 18).

A tool-server tool has the id ``mcp:<server name>:<tool>``. Anthropic and OpenAI
refuse a tool name with a colon or a space in it, and Gemini refuses a space.
Before ``providers/tool_names.py`` existed, every cloud adapter sent the id as the
name, so one checked tool server made every Developer message fail with status
400.

These tests assert the bytes each adapter sends and the id each adapter hands
back. The vendor rules they check against are quoted, with their sources, in the
``tool_names`` module docstring. Nothing here talks to a real vendor. Every HTTP
exchange runs through ``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import json
import random
import re

import httpx
import pytest

from agent_core import mcp_client
from agent_core.main import build_registry
from agent_core.mcp_catalog import McpCatalog, mcp_tool_id
from agent_core.mcp_client import DiscoveredTool
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, TurnSurface
from agent_core.profiles import DEVELOPER, SIMPLE
from agent_core.providers import anthropic_provider, google_provider, openai_provider
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import (
    Message,
    ModelRole,
    ProviderRequestRejected,
    ToolCallRequest,
)
from agent_core.providers.google_provider import GoogleProvider
from agent_core.providers.openai_provider import OpenAIProvider
from agent_core.providers.router import ModelRouter
from agent_core.providers.tool_names import (
    SAME_NAME_REFUSAL,
    WIRE_NAME_MAX_CHARS,
    replayed_tool_ids,
    tool_id_for,
    wire_name,
    wire_names,
)
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.registry import DEV_ONLY_REFUSAL, REMOTE_REFUSAL, ToolRegistry
from tests.test_mcp_dispatch import FakeToolServer

# --- the vendors' published rules, one pattern each ------------------------
# Sources and exact wording are in agent_core/providers/tool_names.py.

ANTHROPIC_TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,128}")
OPENAI_FUNCTION_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")
GEMINI_DECLARATION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}")
GEMINI_CALL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,128}")
VENDOR_RULES = (
    ANTHROPIC_TOOL_NAME,
    OPENAI_FUNCTION_NAME,
    GEMINI_DECLARATION_NAME,
    GEMINI_CALL_NAME,
)
STRICTEST = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")


def follows_every_vendor_rule(name: str) -> bool:
    return all(rule.fullmatch(name) for rule in VENDOR_RULES) and bool(STRICTEST.fullmatch(name))


# --- one tool server with a space and a colon in its name -------------------

SERVER_NAME = "Team: My Files"
SERVER_URL = "https://tools.example/mcp"
MCP_ID = "mcp:Team: My Files:search"
#: Pinned as a literal on purpose. The name must be the same in every process and
#: on every run, because past calls are replayed under it, so a test that computed
#: it the way the code does would pass for a salted or per-process hash too.
MCP_WIRE = "mcp_Team__My_Files_search_b284687477d6"
SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}


def registry_with(*servers: tuple[str, str], call_tool=None, endpoint_for=None) -> ToolRegistry:
    """A registry holding the tools the given ``(server name, tool name)`` pairs
    discover, registered through the real ``McpCatalog`` exactly as a check does."""
    registry = ToolRegistry()
    catalog = McpCatalog(endpoint_for=endpoint_for, call_tool=call_tool)
    for index, (server_name, tool_name) in enumerate(servers):
        catalog.record_success(
            registry,
            server_id=f"s-{index}",
            server_name=server_name,
            tools=(DiscoveredTool(tool_name, "Searches the files.", SCHEMA),),
            skipped=0,
            checked_at=1,
        )
    return registry


def mcp_definition():
    (definition,) = registry_with((SERVER_NAME, "search")).visible_tools(PolicyMode.OPEN)
    assert definition.id == MCP_ID
    return definition


def history() -> list[Message]:
    """A conversation that already used the tool once. The past call is replayed on
    the next request and must go out under the same name the tool list uses."""
    return [
        Message(role="user", content="find the plan"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCallRequest(id="call_1", tool_id=MCP_ID, args={"q": "plan"})],
        ),
        Message(role="tool", content="found: plan.md", tool_call_id="call_1"),
        Message(role="assistant", content="It is plan.md."),
        Message(role="user", content="open it"),
    ]


def capturing_client(*bodies: bytes | dict, seen: list[httpx.Request]) -> httpx.Client:
    """Answers each request with the next body in turn and records every request."""
    queue = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        body = queue.pop(0)
        if isinstance(body, dict):
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def sse(*frames: dict) -> bytes:
    return b"".join(f"data: {json.dumps(frame)}\n\n".encode() for frame in frames)


class _Sink:
    def __init__(self) -> None:
        self.pieces: list[str] = []

    def __call__(self, piece: str) -> None:
        self.pieces.append(piece)


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", [SIMPLE, DEVELOPER], ids=["simple", "developer"])
@pytest.mark.parametrize("mode", [PolicyMode.SAFE, PolicyMode.OPEN], ids=["safe", "open"])
def test_every_built_in_tool_keeps_its_name_byte_for_byte(profile, mode):
    """A valid id goes out unchanged. This walks the real registry, so a built-in
    added later is covered without editing this test.

    Mutation: delete the early ``return tool_id`` in ``wire_name`` and every
    built-in gains a hash suffix, which fails here."""
    definitions = build_registry(profile).visible_tools(mode)
    assert len(definitions) >= 5, "the real registry should hold the built-in tools"
    for definition in definitions:
        assert wire_name(definition.id) == definition.id
        assert follows_every_vendor_rule(definition.id), definition.id


def test_the_reported_ids_become_names_every_vendor_accepts_in_every_adapter():
    """This is the repro from KNOWN-BUGS 18. Two servers, "github" and "My Files",
    go through ``McpCatalog`` and each adapter's ``_translate_tools``. Before the
    fix the names were ``mcp:github:list_issues`` and ``mcp:My Files:search``.

    Mutation: put ``d.id`` back as the name in any one adapter's
    ``_translate_tools`` and that adapter's names fail the vendor rules."""
    registry = registry_with(("github", "list_issues"), ("My Files", "search"))
    tools = registry.visible_tools(PolicyMode.OPEN)
    assert sorted(d.id for d in tools) == ["mcp:My Files:search", "mcp:github:list_issues"]
    expected = sorted(["mcp_My_Files_search_d50bbb32890b", "mcp_github_list_issues_e6c0c66752e2"])

    anthropic_names = [t["name"] for t in anthropic_provider._translate_tools(tools)]
    openai_names = [t["function"]["name"] for t in openai_provider._translate_tools(tools)]
    (google_block,) = google_provider._translate_tools(tools)
    google_names = [d["name"] for d in google_block["functionDeclarations"]]

    for names in (anthropic_names, openai_names, google_names):
        assert sorted(names) == expected
        assert all(follows_every_vendor_rule(name) for name in names)


def test_a_name_is_the_same_in_every_process():
    """A name must be the same in every run and every process. Python's own
    ``hash()`` of a string is salted per process, so a name built from it would
    change between two launches and break every replayed call.

    Mutation: build the suffix from ``hash(tool_id)`` instead of SHA-256 and the
    literal no longer matches."""
    assert wire_name(MCP_ID) == MCP_WIRE
    assert wire_name("mcp:github:list_issues") == "mcp_github_list_issues_e6c0c66752e2"
    long_id = (
        "mcp:Quarterly planning and budget review documents shared drive"
        ":search_every_document_in_the_folder_by_title"
    )
    assert wire_name(long_id) == "mcp_Quarterly_planning_and_budget_review_documents__5e3b04676bc6"
    assert len(wire_name(long_id)) == WIRE_NAME_MAX_CHARS
    # The suffix is the SHA-256 of the whole original id, not of what is left of it.
    assert MCP_WIRE.endswith(hashlib.sha256(MCP_ID.encode()).hexdigest()[:12])


_ALPHABET = (
    "abcXYZ019_-"  # characters the rule allows
    ": ./\\\t\n@#"  # characters it does not
    "éČ漢🙂"  # characters outside ASCII
)


def _random_ids(seed: int, count: int) -> list[str]:
    """These ids are built to stress the rule. Many share long prefixes, many are longer than
    64 characters, some are empty, and some start with a digit or a dash."""
    rng = random.Random(seed)
    prefixes = ["", "mcp:", "mcp:My Files:", "9", "-", "mcp:" + "x" * 70]
    ids = []
    for _ in range(count):
        body = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 140)))
        ids.append(rng.choice(prefixes) + body)
    return ids


def test_any_id_becomes_a_name_that_follows_the_strictest_rule():
    """This is a property test over random ids. The rewritten name always starts
    with a letter or an underscore, holds only allowed characters and is at most 64
    characters long.

    Mutations: remove the leading-underscore fix and the ids that start with "9"
    or "-" fail. Remove the cut and the long ids fail. Widen ``_OUTSIDE_RULE`` to
    ``\\W`` and the non-ASCII letters fail."""
    for tool_id in _random_ids(seed=18, count=4000) + ["", "9lives", "-dash", "a" * 64, "a" * 65]:
        name = wire_name(tool_id)
        assert follows_every_vendor_rule(name), (tool_id, name)
        assert len(name) <= WIRE_NAME_MAX_CHARS


def test_distinct_ids_never_share_a_name_including_after_the_cut():
    """Two ids that differ only in a replaced character, or only after character
    51, still get different names, because the hash is taken over the whole
    original id.

    Mutation: hash the cleaned or the cut string instead of the original id and
    the first family collapses to one name."""
    replaced_only = [
        "mcp:a b:c",
        "mcp:a:b:c",
        "mcp:a.b:c",
        "mcp:a/b:c",
        "mcp:aéb:c",
        "mcp:a b:c ",
        "mcp_a_b_c ",
    ]
    shared_start = "mcp:" + "Shared drive with a very long name indeed:" + "t" * 40
    after_the_cut = [shared_start + suffix for suffix in ("a", "b", "c", "aa", "")]
    families = replaced_only + after_the_cut
    names = [wire_name(i) for i in families]
    assert len(set(names)) == len(families)
    # The cut really did make the readable parts identical in the second family,
    # so the hash is the only thing keeping those names apart.
    assert len({n[: WIRE_NAME_MAX_CHARS - 13] for n in names[len(replaced_only) :]}) == 1

    ids = set(_random_ids(seed=1818, count=6000))
    table = wire_names(ids)
    assert len(table) == len(ids)


def test_every_name_maps_back_to_its_id():
    """The adapters rely on this round trip. The table built from the offered ids
    turns every wire name back into its id. A valid id maps to itself. The same id
    offered twice is not a collision.

    Mutation: drop ``earlier != tool_id`` from the collision check and the repeated
    id is refused, which fails here."""
    ids = ["calculator", "read_web_page", MCP_ID, "mcp:github:list_issues", MCP_ID]
    table = wire_names(ids)
    for tool_id in ids:
        assert tool_id_for(wire_name(tool_id), table) == tool_id
    assert table["calculator"] == "calculator"


def test_a_name_the_table_does_not_hold_comes_back_as_it_arrived():
    """A tool that was offered earlier and is gone now, or a name the model made
    up, is handed to the orchestrator unchanged. The orchestrator then refuses it
    as an unknown tool (``UNKNOWN_TOOL_REFUSAL``) exactly as before."""
    table = wire_names([MCP_ID])
    assert tool_id_for("mcp_Gone_search_0123456789ab", table) == "mcp_Gone_search_0123456789ab"
    assert tool_id_for("calculator", table) == "calculator"
    assert tool_id_for(MCP_WIRE, None) == MCP_WIRE


def test_two_ids_that_would_share_a_name_are_refused():
    """A valid id that equals another id's rewritten name is the one collision a
    test can build on purpose. Sending both would give the vendor two tools with
    one name, which it refuses, and a reply naming it could not be mapped back.
    The sentence names the control that exists, which is Remove in the Tool
    servers section of Settings.

    Mutation: remove the check in ``wire_names`` and no exception is raised."""
    with pytest.raises(ProviderRequestRejected) as raised:
        wire_names([MCP_WIRE, MCP_ID])
    assert str(raised.value) == SAME_NAME_REFUSAL
    assert "Remove one of your tool servers in Settings" in SAME_NAME_REFUSAL


def make_provider(kind: str, client: httpx.Client):
    if kind == "anthropic":
        return AnthropicProvider(api_key_getter=lambda: "sk-test", client=client)
    if kind == "openai":
        return OpenAIProvider(model="gpt-4.1", api_key_getter=lambda: "sk-test", client=client)
    return GoogleProvider(model="gemini-2.5-pro", api_key_getter=lambda: "sk-goog", client=client)


ADAPTERS = ["anthropic", "openai", "google"]


@pytest.mark.parametrize("kind", ADAPTERS)
def test_every_adapter_refuses_a_shared_name_before_anything_is_sent(kind):
    """Each adapter builds its table through ``wire_names`` and so inherits the
    check.

    Mutation: replace ``wire_names(...)`` in any one adapter's ``send`` with a
    plain dict comprehension and that adapter's case fails."""
    seen: list[httpx.Request] = []
    provider = make_provider(kind, capturing_client({}, seen=seen))

    class Impostor:
        id = MCP_WIRE
        description = "Looks like another tool's name."
        parameters_schema = SCHEMA

    with pytest.raises(ProviderRequestRejected) as raised:
        provider.send([Message(role="user", content="hi")], [Impostor(), mcp_definition()])
    assert str(raised.value) == SAME_NAME_REFUSAL
    assert seen == [], "the refusal happens before any request leaves"


# ---------------------------------------------------------------------------
# Past calls: a tool that is registered and not offered
# ---------------------------------------------------------------------------


def test_the_table_holds_replayed_calls_and_offered_tools_keep_their_names():
    """A replayed call's id maps back even when its tool is not offered. An offered
    tool keeps its name when a past id would claim the same one, and a past id
    never causes a refusal, because removing a tool server cannot remove history.

    Mutations: drop the ``past_ids`` loop and the first assertion fails. Let a past
    id overwrite an offered one and the second fails. Run past ids through the
    collision check and the third raises."""
    assert wire_names([], past_ids=[MCP_ID]) == {MCP_WIRE: MCP_ID}
    assert wire_names([MCP_WIRE], past_ids=[MCP_ID]) == {MCP_WIRE: MCP_WIRE}
    assert wire_names([], past_ids=[MCP_WIRE, MCP_ID]) == {MCP_WIRE: MCP_WIRE}


def test_the_replayed_ids_come_from_tool_calls_only():
    """Only ``Message.tool_calls`` goes out on the wire. ``past_tool_calls`` is the
    reloaded record that no adapter may read, so its ids stay out of the table.

    Mutation: read ``past_tool_calls`` as well and this fails."""
    reloaded = Message(
        role="assistant",
        content="",
        past_tool_calls=[ToolCallRequest(id="old", tool_id="mcp:Old:gone", args={})],
    )
    assert replayed_tool_ids(history() + [reloaded]) == [MCP_ID]


_REPLY_NAMING_THE_PAST_TOOL = {
    "anthropic": {
        "content": [{"type": "tool_use", "id": "toolu_5", "name": MCP_WIRE, "input": {}}],
        "stop_reason": "tool_use",
    },
    "openai": {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_5",
                            "type": "function",
                            "function": {"name": MCP_WIRE, "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    },
    "google": {
        "candidates": [
            {
                "content": {"parts": [{"functionCall": {"name": MCP_WIRE, "args": {}}}]},
                "finishReason": "STOP",
            }
        ]
    },
}


@pytest.mark.parametrize("kind", ADAPTERS)
def test_every_adapter_maps_a_call_to_a_tool_it_no_longer_offers_back_to_the_real_id(kind):
    """The request offers no tools, as after a switch to Simple, and the history
    holds a past call to the tool-server tool. The model names that tool by the
    wire name the history showed it, and the adapter hands back the real id.

    Mutation: drop ``replayed_tool_ids(messages)`` from any one adapter's ``send``
    and that adapter's case returns the wire name."""
    seen: list[httpx.Request] = []
    provider = make_provider(kind, capturing_client(_REPLY_NAMING_THE_PAST_TOOL[kind], seen=seen))

    response = provider.send(history(), [])

    assert "tools" not in json.loads(seen[0].content)
    (call,) = response.tool_calls
    assert call.tool_id == MCP_ID


# ---------------------------------------------------------------------------
# Each adapter: the exact request, and the id that comes back
# ---------------------------------------------------------------------------


def test_anthropic_sends_the_wire_name_everywhere_and_returns_the_real_id():
    """The tool list and the replayed ``tool_use`` block carry the same wire name.
    A ``tool_result`` carries only the ``tool_use_id``. The reply's ``tool_use``
    comes back as the real id.

    Mutations: put ``c.tool_id`` back in ``_translate_history`` and the replayed
    block fails. Drop ``tool_id_for`` from ``_translate_response`` and the returned
    id fails."""
    definition = mcp_definition()
    seen: list[httpx.Request] = []
    reply = {
        "content": [
            {"type": "tool_use", "id": "toolu_2", "name": MCP_WIRE, "input": {"q": "plan.md"}}
        ],
        "stop_reason": "tool_use",
    }
    provider = AnthropicProvider(
        api_key_getter=lambda: "sk-test", client=capturing_client(reply, seen=seen)
    )

    response = provider.send(history(), [definition])

    (request,) = seen
    body = json.loads(request.content)
    assert body["tools"] == [
        {
            "name": MCP_WIRE,
            "description": definition.description,
            "input_schema": SCHEMA,
        }
    ]
    assert body["messages"] == [
        {"role": "user", "content": "find the plan"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_1", "name": MCP_WIRE, "input": {"q": "plan"}}
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "found: plan.md"}
            ],
        },
        {"role": "assistant", "content": "It is plan.md."},
        {"role": "user", "content": "open it"},
    ]
    assert MCP_ID.encode() not in request.content
    (call,) = response.tool_calls
    assert (call.id, call.tool_id, call.args) == ("toolu_2", MCP_ID, {"q": "plan.md"})


def test_anthropic_streaming_returns_the_real_id():
    """The streamed reply names the tool in ``content_block_start``.

    Mutation: drop ``tool_id_for`` from ``_translate_stream`` and this fails."""
    seen: list[httpx.Request] = []
    body = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_3", "name": MCP_WIRE},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"q": "x"}'},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 3},
        },
    )
    provider = AnthropicProvider(
        api_key_getter=lambda: "sk-test", client=capturing_client(body, seen=seen)
    )

    response = provider.send(history(), [mcp_definition()], on_delta=_Sink())

    assert json.loads(seen[0].content)["tools"][0]["name"] == MCP_WIRE
    (call,) = response.tool_calls
    assert (call.id, call.tool_id, call.args) == ("toolu_3", MCP_ID, {"q": "x"})


def test_openai_sends_the_wire_name_everywhere_and_returns_the_real_id():
    """The function list and the replayed ``tool_calls`` entry carry the same wire
    name. A ``tool`` message carries only the ``tool_call_id``.

    Mutations: put ``c.tool_id`` back in ``_translate_history`` and the replayed
    entry fails. Drop ``tool_id_for`` from ``_translate_response`` and the returned
    id fails."""
    definition = mcp_definition()
    seen: list[httpx.Request] = []
    reply = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": MCP_WIRE, "arguments": '{"q": "plan.md"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    provider = OpenAIProvider(
        model="gpt-4.1", api_key_getter=lambda: "sk-test", client=capturing_client(reply, seen=seen)
    )

    response = provider.send(history(), [definition])

    (request,) = seen
    body = json.loads(request.content)
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": MCP_WIRE,
                "description": definition.description,
                "parameters": SCHEMA,
            },
        }
    ]
    assert body["messages"] == [
        {"role": "user", "content": "find the plan"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": MCP_WIRE, "arguments": '{"q": "plan"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "found: plan.md"},
        {"role": "assistant", "content": "It is plan.md."},
        {"role": "user", "content": "open it"},
    ]
    assert MCP_ID.encode() not in request.content
    (call,) = response.tool_calls
    assert (call.id, call.tool_id, call.args) == ("call_2", MCP_ID, {"q": "plan.md"})


def test_openai_streaming_returns_the_real_id():
    """The streamed reply names the function on the first fragment only.

    Mutation: drop ``tool_id_for`` from ``_translate_stream`` and this fails."""
    seen: list[httpx.Request] = []
    body = sse(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_3",
                                "function": {"name": MCP_WIRE, "arguments": '{"q"'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "x"}'}}]}}
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )
    provider = OpenAIProvider(
        model="gpt-4.1", api_key_getter=lambda: "sk-test", client=capturing_client(body, seen=seen)
    )

    response = provider.send(history(), [mcp_definition()], on_delta=_Sink())

    assert json.loads(seen[0].content)["tools"][0]["function"]["name"] == MCP_WIRE
    (call,) = response.tool_calls
    assert (call.id, call.tool_id, call.args) == ("call_3", MCP_ID, {"q": "x"})


def test_google_sends_the_wire_name_everywhere_and_returns_the_real_id():
    """The declaration, the replayed ``functionCall`` and the ``functionResponse``
    all carry the same wire name. Gemini pairs a response with its call by name, so
    the two must agree.

    Mutations: put ``call.tool_id`` back in ``_function_call_part`` and the
    replayed call fails. Store ``c.tool_id`` in ``call_names`` and the response
    fails. Drop ``tool_id_for`` from ``_tool_call_from_part`` and the returned id
    fails."""
    definition = mcp_definition()
    seen: list[httpx.Request] = []
    reply = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": MCP_WIRE, "args": {"q": "plan.md"}}}],
                },
                "finishReason": "STOP",
            }
        ]
    }
    provider = GoogleProvider(
        model="gemini-2.5-pro",
        api_key_getter=lambda: "sk-goog",
        client=capturing_client(reply, seen=seen),
    )

    response = provider.send(history(), [definition])

    (request,) = seen
    body = json.loads(request.content)
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {"name": MCP_WIRE, "description": definition.description, "parameters": SCHEMA}
            ]
        }
    ]
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "find the plan"}]},
        {"role": "model", "parts": [{"functionCall": {"name": MCP_WIRE, "args": {"q": "plan"}}}]},
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": MCP_WIRE, "response": {"result": "found: plan.md"}}}
            ],
        },
        {"role": "model", "parts": [{"text": "It is plan.md."}]},
        {"role": "user", "parts": [{"text": "open it"}]},
    ]
    assert MCP_ID.encode() not in request.content
    (call,) = response.tool_calls
    assert (call.tool_id, call.args) == (MCP_ID, {"q": "plan.md"})


def test_google_streaming_returns_the_real_id():
    """Mutation: pass no table to ``_tool_call_from_part`` in ``_translate_stream``
    and this fails."""
    seen: list[httpx.Request] = []
    body = sse(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": MCP_WIRE, "args": {"q": "x"}}}]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    )
    provider = GoogleProvider(
        model="gemini-2.5-pro",
        api_key_getter=lambda: "sk-goog",
        client=capturing_client(body, seen=seen),
    )

    response = provider.send(history(), [mcp_definition()], on_delta=_Sink())

    (declaration,) = json.loads(seen[0].content)["tools"][0]["functionDeclarations"]
    assert declaration["name"] == MCP_WIRE
    (call,) = response.tool_calls
    assert (call.tool_id, call.args) == (MCP_ID, {"q": "x"})


def test_google_names_a_result_whose_call_is_missing_with_a_legal_name():
    """A tool result whose call is not in the history is named after its call id.
    That fallback goes through ``wire_name`` too, so an id with a colon in it does
    not put a colon where Gemini refuses one."""
    contents = google_provider._translate_history(
        [Message(role="tool", content="late", tool_call_id="setup:1")]
    )
    (part,) = contents[0]["parts"]
    assert GEMINI_CALL_NAME.fullmatch(part["functionResponse"]["name"])


# ---------------------------------------------------------------------------
# End to end: a real turn, a real adapter, a real tool-server call
# ---------------------------------------------------------------------------


def test_a_turn_on_anthropic_runs_the_tool_and_replays_it_under_the_same_name(tmp_path):
    """This test covers the whole path. The orchestrator offers the tool, and the
    fake Messages API asks for it by its wire name. The gate is answered Allow. The
    fake tool server is called with its own tool name. The conversation records the
    real id. The second request replays the call under the wire name it went out
    with.

    The orchestrator always passes ``on_delta``, so this runs the adapter's
    streaming path."""
    server = FakeToolServer(text="found: plan.md")
    tool_client = httpx.Client(transport=httpx.MockTransport(server.handler))

    def call_tool(url, name, args, budget):
        return mcp_client.call_tool(
            url, name, args, budget, client=tool_client, resolve=lambda host: ["127.0.0.1"]
        )

    registry = registry_with(
        (SERVER_NAME, "search"),
        call_tool=call_tool,
        endpoint_for=lambda server_id, name: SERVER_URL if name == SERVER_NAME else None,
    )
    assert mcp_tool_id(SERVER_NAME, "search") == MCP_ID

    seen: list[httpx.Request] = []
    first = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_9", "name": MCP_WIRE},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"q": "plan"}'},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 3},
        },
    )
    second = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 9, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Done."},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
    )
    provider = AnthropicProvider(
        api_key_getter=lambda: "sk-test", client=capturing_client(first, second, seen=seen)
    )
    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED),
        undo_manager=UndoManager(store=None, tool_registry=registry),
    )
    conversation = Conversation(id="conv-18")
    conversation.messages.append(Message(role="user", content="find the plan"))

    try:
        orchestrator.run_turn(conversation, requested_role=ModelRole.PRIMARY, mode=PolicyMode.OPEN)
    finally:
        tool_client.close()

    (tool_call,) = server.calls_made()
    assert tool_call["params"]["name"] == "search"
    assert tool_call["params"]["arguments"] == {"q": "plan"}

    recorded = [c for m in conversation.messages for c in (m.tool_calls or [])]
    assert [c.tool_id for c in recorded] == [MCP_ID]
    assert conversation.messages[-1].content == "Done."

    assert len(seen) == 2
    replay = json.loads(seen[1].content)
    assert replay["tools"][0]["name"] == MCP_WIRE
    assert replay["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "toolu_9", "name": MCP_WIRE, "input": {"q": "plan"}}
        ],
    }
    assert replay["messages"][2]["content"][0]["tool_use_id"] == "toolu_9"
    assert "found: plan.md" in replay["messages"][2]["content"][0]["content"]


@pytest.mark.parametrize(
    "mode, surface, refusal, outcome",
    [
        pytest.param(PolicyMode.SAFE, TurnSurface.DESK, DEV_ONLY_REFUSAL, "dev_only", id="simple"),
        pytest.param(
            PolicyMode.OPEN, TurnSurface.REMOTE, REMOTE_REFUSAL, "not_callable", id="phone"
        ),
    ],
)
def test_a_hidden_tool_named_from_history_is_refused_and_audited_under_its_real_id(
    mode, surface, refusal, outcome
):
    """The chat used the tool-server tool in Developer, and the next turn runs in
    Simple, or arrives from the phone. The tool is registered and not offered. The
    fake Messages API names it by the wire name the replayed history shows. The
    adapter maps that name back to the real id, so the orchestrator gives the
    dev-only or the phone refusal, and the ``tool_audit`` row carries the real id.
    The tool server is never called. Before the table held past calls, the wire
    name reached dispatch, the refusal was the unknown-tool sentence, and the row
    recorded the wire name as ``not_callable``.

    Mutation: drop ``replayed_tool_ids(messages)`` from the Anthropic adapter's
    ``send`` and both cases fail."""
    called: list[tuple] = []
    registry = registry_with(
        (SERVER_NAME, "search"),
        call_tool=lambda *call: called.append(call),
        endpoint_for=lambda server_id, name: SERVER_URL,
    )
    seen: list[httpx.Request] = []
    first = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_7", "name": MCP_WIRE},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"q": "plan"}'},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 3},
        },
    )
    second = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 9, "output_tokens": 0}}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "I can't use that here."},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
    )
    provider = AnthropicProvider(
        api_key_getter=lambda: "sk-test", client=capturing_client(first, second, seen=seen)
    )
    rows: list[dict] = []
    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED),
        undo_manager=UndoManager(store=None, tool_registry=registry),
        on_tool_audit=rows.append,
    )
    conversation = Conversation(id="conv-18b")
    conversation.messages.extend(history())

    orchestrator.run_turn(
        conversation, requested_role=ModelRole.PRIMARY, mode=mode, surface=surface
    )

    assert "tools" not in json.loads(seen[0].content), "the tool is not offered"
    assert called == [], "the tool server is never called"
    results = [m for m in conversation.messages if m.role == "tool"]
    assert results[-1].content == refusal
    assert [(row["tool_id"], row["outcome"]) for row in rows] == [(MCP_ID, outcome)]
