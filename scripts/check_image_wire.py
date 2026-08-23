#!/usr/bin/env python3
"""Send one real picture to each configured provider, through Addison's own adapters.

WHAT THIS IS FOR. Every image block shape in this repo is asserted by tests that
were written from the same belief that produced the code — a suite that cannot
disagree with its author. The vendors' documentation was checked afterwards and
agreed, but "documentation-correct" and "the API accepted it" are different
claims, and only one of them is evidence. This script produces the second one.

WHAT IT DOES NOT DO. It never prints, logs, stores or transmits your key anywhere
but to the provider whose key it is. It reads keys from the ENVIRONMENT only —
nothing here touches the OS keychain, and no key is ever passed as a command-line
argument (those are visible to every process on the machine via `ps`).

It calls the REAL adapters (`agent_core/providers/*`), not a hand-written request,
which is the whole point: what is under test is Addison's translation of a
`Message` carrying an `ImageAttachment`, exactly as a turn would build it.

THE PICTURE IS THE ASSERTION. A 200 response proves only that the request parsed;
it does not prove the model received PIXELS. So the image is a solid, unusual
colour and the question asks for that colour in one word. A model that answers
correctly saw the image; a model that is guessing from the words alone cannot,
because the words never name a colour.

    Usage:
        python3 scripts/check_image_wire.py

    Keys (set only the ones you want to test):
        ANTHROPIC_API_KEY   OPENAI_API_KEY   GOOGLE_API_KEY
        CUSTOM_API_KEY + CUSTOM_BASE_URL + CUSTOM_MODEL   (an OpenAI-compatible server)
        OLLAMA_MODEL        (a local vision model, e.g. "llama3.2-vision")

    A provider with no key is skipped, not failed.
"""

from __future__ import annotations

import base64
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_core.providers.base import ImageAttachment, Message  # noqa: E402

# The colour the model has to name back, and the word that counts as right. Chosen
# to be unambiguous in one word and not nameable from the prompt: the question says
# "what colour", never "is it purple".
_RGB = (128, 0, 255)
_EXPECT = ("purple", "violet", "magenta", "blue")
_QUESTION = "What is the single dominant colour of this image? Answer with one word."


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A valid PNG of one flat colour, built with the standard library alone.

    No Pillow: this repo is stdlib-first, and a check that needs a new dependency
    installed before it can run is a check people skip.
    """
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def picture_message() -> Message:
    """The turn a person would send: one attached picture and one question."""
    png = solid_png(64, 64, _RGB)
    return Message(
        role="user",
        content=_QUESTION,
        images=(
            ImageAttachment(
                media_type="image/png",
                data_b64=base64.standard_b64encode(png).decode("ascii"),
            ),
        ),
    )


def verdict(answer: str) -> tuple[bool, str]:
    """Did the model actually look at the pixels?

    Accepts a small family of names for the same colour, because "what colour is
    this" has more than one right answer and this script is testing the WIRE, not
    the model's vocabulary.
    """
    lowered = (answer or "").strip().lower()
    if not lowered:
        return False, "answered nothing"
    if any(word in lowered for word in _EXPECT):
        return True, lowered.splitlines()[0][:60]
    return False, f"said {lowered.splitlines()[0][:60]!r}, which is not the colour sent"


def providers():
    """Every provider this machine is set up to test, as (name, built provider)."""
    from agent_core.providers.anthropic_provider import AnthropicProvider
    from agent_core.providers.google_provider import GoogleProvider
    from agent_core.providers.ollama_provider import OllamaProvider
    from agent_core.providers.openai_provider import OpenAIProvider

    out = []
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        out.append(
            (
                "anthropic",
                AnthropicProvider(
                    model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
                    api_key_getter=lambda k=key: k,
                ),
            )
        )
    if key := os.environ.get("OPENAI_API_KEY"):
        out.append(
            (
                "openai",
                OpenAIProvider(
                    model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
                    api_key_getter=lambda k=key: k,
                ),
            )
        )
    if key := os.environ.get("GOOGLE_API_KEY"):
        out.append(
            (
                "google",
                GoogleProvider(
                    model=os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash"),
                    api_key_getter=lambda k=key: k,
                ),
            )
        )
    # The custom server is the one whose eyes Addison deliberately does not claim to
    # know (KNOWN-GAPS), so testing it is the only way to learn whether yours can see.
    if (key := os.environ.get("CUSTOM_API_KEY")) and (base := os.environ.get("CUSTOM_BASE_URL")):
        out.append(
            (
                "custom",
                OpenAIProvider(
                    model=os.environ.get("CUSTOM_MODEL", "local-model"),
                    api_key_getter=lambda k=key: k,
                    base_url=base,
                    service_label="custom server",
                ),
            )
        )
    if model := os.environ.get("OLLAMA_MODEL"):
        out.append(("ollama", OllamaProvider(model=model)))
    return out


def main() -> int:
    message = picture_message()
    configured = providers()
    if not configured:
        print("Nothing to test — set at least one key. See this file's docstring.")
        return 2

    failures = 0
    for name, provider in configured:
        print(f"\n=== {name} ===")
        try:
            can_see = provider.capabilities().vision
            print(f"  claims vision: {can_see}")
            response = provider.send([message], [], timeout=60.0)
            ok, detail = verdict(getattr(response, "text", ""))
            print(f"  {'PASS' if ok else 'FAIL'}: {detail}")
            if not ok:
                failures += 1
        except Exception as exc:  # noqa: BLE001 — a check reports, it does not raise
            # The adapters raise plain sentences by design and never chain the
            # original (an httpx error string can carry the URL, and some APIs put
            # credentials in a URL). Printing the type alone keeps that true.
            print(f"  FAIL: {type(exc).__name__}: {exc}")
            failures += 1

    print(f"\n{len(configured) - failures}/{len(configured)} providers accepted the picture.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
