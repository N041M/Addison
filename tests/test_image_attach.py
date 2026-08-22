"""The image path: a picture on a user message, and the gate in front of it.

Phase 1 of [`docs/image-attach-plan.md`](../docs/image-attach-plan.md) §3 — one
subject spread over five modules, so it is tested in one file rather than smeared
across four provider test files where nobody would find the fifth half.

What is pinned here, and why each one is worth a test:

* **The four block shapes.** Every vision API spells "here is a picture"
  differently, and a wrong shape is not a crash — it is a 400 from one provider
  and a silently text-only answer from another. The full structure is asserted,
  never a substring, so a shape that drifts fails rather than still matching.
* **The regression pin.** A message with NO pictures must serialise byte-for-byte
  as it did before this feature existed. Every conversation anybody has ever had
  goes through these functions; if the no-attachment path changed shape at all,
  this feature broke chat for everyone to add a button.
* **The role guard.** Pictures ride user turns only. One that turned up on a tool
  or assistant message is ignored, because half these APIs refuse image parts on
  those roles outright.
* **The history degrade**, in the one adapter that can need it (Ollama, whose
  answer to "can you see" varies per model).
* **The turn gate**, which must refuse rather than let a blind model answer about
  words it did receive as though it had seen the picture it did not.

HTTP is faked with ``httpx.MockTransport`` throughout — the offline technique
every provider test here uses — and the orchestrator half uses the scripted fake
provider from ``test_orchestrator.py``'s house style.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Callable

import httpx
import pytest

from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import (
    ALLOWED_IMAGE_MEDIA_TYPES,
    ImageAttachment,
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.google_provider import GoogleProvider
from agent_core.providers.ollama_provider import OllamaProvider
from agent_core.providers.openai_provider import OpenAIProvider
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.registry import ToolRegistry

# Two tiny stand-ins for real pictures. The bytes are never decoded by anything in
# the core — the adapters pass base64 through verbatim — so a short marker string
# is a truthful fixture and a readable assertion.
PNG = ImageAttachment(media_type="image/png", data_b64="AAAApngbytes")
JPEG = ImageAttachment(media_type="image/jpeg", data_b64="BBBBjpegbytes")

_REFUSAL = (
    "The model answering right now can't look at pictures. "
    "Switch to one that can and send it again."
)


# --- shared HTTP fakes ------------------------------------------------------
def _capture(payload: dict) -> tuple[httpx.Client, dict]:
    """A client whose transport records the request body it was handed."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler)), captured


def _anthropic_body(messages: list[Message]) -> dict:
    client, captured = _capture({"content": [{"type": "text", "text": "ok"}]})
    AnthropicProvider(api_key_getter=lambda: "sk-test", client=client).send(messages, [])
    return captured["body"]


def _openai_body(messages: list[Message]) -> dict:
    client, captured = _capture({"choices": [{"message": {"content": "ok"}}]})
    OpenAIProvider(model="gpt-4.1", api_key_getter=lambda: "sk-test", client=client).send(
        messages, []
    )
    return captured["body"]


def _google_body(messages: list[Message]) -> dict:
    client, captured = _capture({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    GoogleProvider(model="gemini-3-pro", api_key_getter=lambda: "sk-goog", client=client).send(
        messages, []
    )
    return captured["body"]


def _ollama_client(
    routes: dict[str, Callable[[httpx.Request], httpx.Response] | tuple[int, object]],
) -> tuple[httpx.Client, dict]:
    """Path-dispatching client (the ``test_ollama_provider.py`` helper): Ollama
    needs two routes, because ``/api/show`` is what decides whether the model in
    ``/api/chat`` can look at a picture at all."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        captured[path] = json.loads(request.content) if request.content else None
        route = routes.get(path)
        if route is None:
            return httpx.Response(404, json={"error": f"no route for {path}"})
        if callable(route):
            return route(request)
        status, payload = route
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler)), captured


def _ollama_body(messages: list[Message], *, capabilities: list[str]) -> dict:
    client, captured = _ollama_client(
        {
            "/api/show": (200, {"capabilities": capabilities}),
            "/api/chat": (200, {"message": {"content": "ok"}}),
        }
    )
    OllamaProvider("some-model", client=client).send(messages, [])
    return captured["/api/chat"]


# --- the closed media-type set ---------------------------------------------
def test_allowed_media_types_are_the_four_every_vision_api_takes():
    """The set is CLOSED and its membership is a cross-provider fact, not a
    preference: a fifth type is accepted by whichever provider happened to answer
    and refused by the other three, so a turn would fail for some people only.

    Mutation: add "image/heic" (a real phone format, and the tempting addition) —
    this fails, and the failure is the reminder that the shell re-encodes to one
    of these four instead (plan §4)."""
    assert ALLOWED_IMAGE_MEDIA_TYPES == frozenset(
        {"image/png", "image/jpeg", "image/gif", "image/webp"}
    )


def test_an_attachment_cannot_be_edited_after_it_is_made():
    """Frozen is the provenance argument in code: what the person previewed is
    byte-for-byte what is sent, so nothing between the pick and the wire may
    substitute a different picture for the one on screen."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        PNG.data_b64 = "something-else"  # type: ignore[misc]


# --- Anthropic --------------------------------------------------------------
def test_anthropic_user_message_with_pictures_is_image_blocks_then_one_text_block():
    body = _anthropic_body([Message(role="user", content="what is this?", images=(PNG, JPEG))])
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "AAAApngbytes",
                    },
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "BBBBjpegbytes",
                    },
                },
                {"type": "text", "text": "what is this?"},
            ],
        }
    ]


def test_anthropic_pictures_with_no_words_send_no_text_block():
    """The API rejects an empty text block, and sending just a photo is an
    ordinary message — so the block is omitted, not emptied."""
    body = _anthropic_body([Message(role="user", content="", images=(PNG,))])
    assert body["messages"][0]["content"] == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "AAAApngbytes"},
        }
    ]


def test_anthropic_message_without_pictures_is_the_plain_string_it_always_was():
    """THE REGRESSION PIN. Every conversation that predates this feature goes
    through here; ``content`` must still be the bare string, not a one-element
    block list that happens to mean the same thing.

    Mutation: make ``_user_content`` always return blocks — this fails."""
    body = _anthropic_body([Message(role="user", content="hello")])
    assert body["messages"] == [{"role": "user", "content": "hello"}]


def test_anthropic_ignores_pictures_on_tool_and_assistant_messages():
    """Pictures ride user turns only (base.py). One found elsewhere is dropped
    rather than translated: a tool_result block carrying an image is not a shape
    this path has ever verified, and inventing it would break the pairing rule."""
    body = _anthropic_body(
        [
            Message(
                role="assistant",
                content="Reading it.",
                images=(PNG,),
                tool_calls=[ToolCallRequest(id="tu_1", tool_id="read_file", args={})],
            ),
            Message(role="tool", content="file bytes", tool_call_id="tu_1", images=(JPEG,)),
        ]
    )
    assert body["messages"] == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Reading it."},
                {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "file bytes"}
            ],
        },
    ]


# --- OpenAI (and the custom OpenAI-compatible server, same adapter) ---------
def test_openai_user_message_with_pictures_is_image_url_parts_then_text():
    body = _openai_body([Message(role="user", content="what is this?", images=(PNG, JPEG))])
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAApngbytes"},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,BBBBjpegbytes"},
                },
                {"type": "text", "text": "what is this?"},
            ],
        }
    ]


def test_openai_pictures_with_no_words_send_no_text_part():
    body = _openai_body([Message(role="user", content="", images=(PNG,))])
    assert body["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAApngbytes"}}
    ]


def test_openai_message_without_pictures_is_the_plain_string_it_always_was():
    """THE REGRESSION PIN for this adapter — including the ``or ""`` that a
    None-content message has always relied on."""
    body = _openai_body(
        [Message(role="system", content="You are Addison."), Message(role="user", content="hi")]
    )
    assert body["messages"] == [
        {"role": "system", "content": "You are Addison."},
        {"role": "user", "content": "hi"},
    ]


def test_openai_ignores_pictures_on_tool_and_assistant_messages():
    body = _openai_body(
        [
            Message(
                role="assistant",
                content="Reading it.",
                images=(PNG,),
                tool_calls=[ToolCallRequest(id="call_1", tool_id="read_file", args={})],
            ),
            Message(role="tool", content="file bytes", tool_call_id="call_1", images=(JPEG,)),
        ]
    )
    assert body["messages"] == [
        {
            "role": "assistant",
            "content": "Reading it.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file bytes"},
    ]


# --- Google -----------------------------------------------------------------
def test_google_user_message_with_pictures_is_inline_data_parts_then_text():
    body = _google_body([Message(role="user", content="what is this?", images=(PNG, JPEG))])
    assert body["contents"] == [
        {
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/png", "data": "AAAApngbytes"}},
                {"inline_data": {"mime_type": "image/jpeg", "data": "BBBBjpegbytes"}},
                {"text": "what is this?"},
            ],
        }
    ]


def test_google_pictures_with_no_words_send_no_text_part():
    body = _google_body([Message(role="user", content="", images=(PNG,))])
    assert body["contents"][0]["parts"] == [
        {"inline_data": {"mime_type": "image/png", "data": "AAAApngbytes"}}
    ]


def test_google_message_without_pictures_keeps_its_single_empty_text_part():
    """THE REGRESSION PIN, and the one place the empty text part SURVIVES: with
    no pictures it is the only part there is, and Gemini wants a part."""
    assert _google_body([Message(role="user", content="hi")])["contents"] == [
        {"role": "user", "parts": [{"text": "hi"}]}
    ]
    assert _google_body([Message(role="user", content="")])["contents"] == [
        {"role": "user", "parts": [{"text": ""}]}
    ]


def test_google_ignores_pictures_on_tool_and_assistant_messages():
    body = _google_body(
        [
            Message(
                role="assistant",
                content="Reading it.",
                images=(PNG,),
                tool_calls=[ToolCallRequest(id="tc1", tool_id="read_file", args={})],
            ),
            Message(role="tool", content="file bytes", tool_call_id="tc1", images=(JPEG,)),
        ]
    )
    assert body["contents"] == [
        {
            "role": "model",
            "parts": [
                {"text": "Reading it."},
                {"functionCall": {"name": "read_file", "args": {}}},
            ],
        },
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": "read_file", "response": {"result": "file bytes"}}}
            ],
        },
    ]


# --- Ollama: the one adapter whose answer to "can you see" varies -----------
def test_ollama_vision_model_gets_the_pictures_on_the_message():
    body = _ollama_body(
        [Message(role="user", content="what is this?", images=(PNG, JPEG))],
        capabilities=["completion", "vision"],
    )
    assert body["messages"] == [
        {
            "role": "user",
            "content": "what is this?",
            "images": ["AAAApngbytes", "BBBBjpegbytes"],
        }
    ]


def test_ollama_text_only_model_gets_picture_markers_and_no_pixels():
    """THE HISTORY DEGRADE (plan §3, §9). A model with no ``vision`` capability
    never receives the base64 — it could not use it, and Ollama would carry it as
    prompt weight for nothing — and gets one ``[picture]`` per image in front of
    the words, so the sentence that referred to them is not a non-sequitur.

    Mutation: drop the ``if not vision`` branch — ``images`` reappears on a
    message no model can read and this fails on both assertions."""
    body = _ollama_body(
        [Message(role="user", content="what is this?", images=(PNG, JPEG))],
        capabilities=["completion"],
    )
    assert body["messages"] == [
        {"role": "user", "content": "[picture] [picture]\n\nwhat is this?"}
    ]
    assert "images" not in body["messages"][0]


def test_ollama_text_only_model_with_a_wordless_picture_gets_the_marker_alone():
    body = _ollama_body(
        [Message(role="user", content="", images=(PNG,))], capabilities=["completion"]
    )
    assert body["messages"] == [{"role": "user", "content": "[picture]"}]


def test_ollama_message_without_pictures_is_the_plain_entry_it_always_was():
    """THE REGRESSION PIN — asserted for BOTH models, because the degrade must be
    invisible to a conversation that never attached anything."""
    plain = [Message(role="system", content="You are Addison."), Message(role="user", content="hi")]
    expected = [
        {"role": "system", "content": "You are Addison."},
        {"role": "user", "content": "hi"},
    ]
    assert _ollama_body(plain, capabilities=["vision"])["messages"] == expected
    assert _ollama_body(plain, capabilities=["completion"])["messages"] == expected


def test_ollama_ignores_pictures_on_tool_and_assistant_messages():
    body = _ollama_body(
        [
            Message(role="assistant", content="Reading it.", images=(PNG,)),
            Message(role="tool", content="file bytes", tool_call_id="x", images=(JPEG,)),
        ],
        capabilities=["vision"],
    )
    assert body["messages"] == [
        {"role": "assistant", "content": "Reading it."},
        {"role": "tool", "content": "file bytes"},
    ]


# --- the turn gate ----------------------------------------------------------
class _SeeingOrBlindProvider:
    """A scripted provider (house style, ``test_orchestrator.py``) that answers
    the ONE capability question this gate asks, and records whether it was ever
    asked to send — the assertion that matters, since a gate that streams the
    right sentence and calls the model anyway has refused nothing."""

    def __init__(self, vision: bool) -> None:
        self._vision = vision
        self.sends: list[list[Message]] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=200_000,
            supports_streaming=False,
            runs_off_device=False,
            vision=self._vision,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.sends.append(list(messages))
        return ModelResponse(text="I can see it.", tool_calls=[])


def _run(provider, message: Message) -> tuple[Conversation, list[str]]:
    registry = ToolRegistry()
    streamed: list[str] = []
    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(),
        undo_manager=UndoManager(store=None, tool_registry=registry),
        stream_to_frontend=streamed.append,
    )
    conversation = Conversation(id="c")
    conversation.messages.append(message)
    orchestrator.run_turn(conversation)
    return conversation, streamed


def test_a_message_with_pictures_is_refused_when_the_model_cannot_look_at_them():
    """The gate: nothing is sent, the plain sentence is said, and the turn ends.

    Mutation: delete the ``if provider.capabilities().vision: return False`` check
    (or the call to ``_refuse_if_blind_to_pictures``) — the provider is sent a
    picture it cannot read and this fails on the very first assertion."""
    provider = _SeeingOrBlindProvider(vision=False)
    conversation, streamed = _run(
        provider, Message(role="user", content="what is this?", images=(PNG,))
    )
    assert provider.sends == [], "no picture may reach a model that cannot look at one"
    assert streamed == [_REFUSAL]
    # And the transcript ends on the refusal rather than on an unanswered question,
    # so a reopened conversation shows what happened (``_finish_over_budget``'s rule).
    assert [(m.role, m.content) for m in conversation.messages] == [
        ("user", "what is this?"),
        ("assistant", _REFUSAL),
    ]


def test_a_message_with_pictures_reaches_a_model_that_can_look_at_them_intact():
    """The other half, and what keeps the test above from passing for the wrong
    reason: a vision model is sent the turn, with the pictures still on it."""
    provider = _SeeingOrBlindProvider(vision=True)
    conversation, streamed = _run(
        provider, Message(role="user", content="what is this?", images=(PNG, JPEG))
    )
    assert len(provider.sends) == 1
    assert provider.sends[0][0].images == (PNG, JPEG)
    assert _REFUSAL not in streamed
    assert conversation.messages[-1].content == "I can see it."


def test_a_message_with_no_pictures_never_asks_a_blind_model_anything():
    """THE FREEZE. A turn with no attachments is what every turn is today, and it
    must be byte-identical: the gate does not resolve a provider, does not read a
    capability, and refuses nothing — a blind model answers an ordinary message
    exactly as it always has.

    Mutation: gate on the message's presence rather than on its pictures — this
    fails, because every ordinary turn on a text-only model starts being refused."""
    provider = _SeeingOrBlindProvider(vision=False)
    conversation, streamed = _run(provider, Message(role="user", content="just words"))
    assert len(provider.sends) == 1
    assert streamed == ["I can see it."]
    assert conversation.messages[-1].content == "I can see it."


def test_only_the_new_message_is_gated_not_an_older_picture_in_history():
    """An OLDER attachment reaching a blind model is the adapters' degrade, never
    a refusal (plan §9): the person did nothing to cause it, and a dead turn would
    punish them for a picture they attached ten minutes ago.

    Mutation: make the gate scan the whole history for pictures instead of the
    last user message — this fails, because the follow-up question is refused."""
    provider = _SeeingOrBlindProvider(vision=False)
    registry = ToolRegistry()
    streamed: list[str] = []
    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(),
        undo_manager=UndoManager(store=None, tool_registry=registry),
        stream_to_frontend=streamed.append,
    )
    conversation = Conversation(id="c")
    # A picture attached earlier in this conversation, already answered — and then
    # the message this turn is actually about, which carries none.
    conversation.messages.append(Message(role="user", content="look at this", images=(PNG,)))
    conversation.messages.append(Message(role="assistant", content="Earlier answer."))
    conversation.messages.append(Message(role="user", content="and now?"))
    orchestrator.run_turn(conversation)
    assert len(provider.sends) == 1, "the follow-up question is answered, not refused"
    assert streamed == ["I can see it."]
    # The old picture DID go out with the history — dropping it is the adapter's
    # call (Ollama's degrade), never the orchestrator's.
    assert provider.sends[0][0].images == (PNG,)
