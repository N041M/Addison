"""web_search against canned DuckDuckGo HTML (engineering-spec §11 step 5).

The HTTP boundary is faked with ``httpx.MockTransport`` — the same offline
technique the provider tests use — so there is NO live network here. Covers the
DDG HTML parse (titles/urls/snippets), ``uddg`` redirect decoding, the 5-result
cap, the untrusted-data wrapper (design-doc §9), and that every failure path
(network, non-2xx, zero results, blank query) returns ``success=False`` with a
plain-language message rather than raising.
"""

from urllib.parse import quote

import httpx

from agent_core.tools.base import ExecutionContext
from agent_core.tools.web_search import WebSearchTool


def _ctx() -> ExecutionContext:
    return ExecutionContext(conversation_id="t")  # web_search needs no bridge


def _tool_for(handler) -> WebSearchTool:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return WebSearchTool(client=client)


def _canned_html(n: int) -> str:
    """n DDG results, each a result__a (redirect href) + result__snippet anchor."""
    parts = ["<html><body>"]
    for i in range(n):
        real = f"https://example.com/{i}"
        href = f"//duckduckgo.com/l/?uddg={quote(real, safe='')}&rut=abc{i}"
        parts.append('<div class="result results_links">')
        parts.append(f'<a class="result__a" href="{href}">Title {i}</a>')
        parts.append(f'<a class="result__snippet" href="{href}">Snippet number {i}.</a>')
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


def test_parses_titles_urls_snippets_and_decodes_uddg():
    tool = _tool_for(lambda req: httpx.Response(200, text=_canned_html(3)))
    result = tool.execute({"query": "cats"}, _ctx())

    assert result.success is True
    results = result.content["results"]
    assert len(results) == 3
    assert results[0] == {
        "title": "Title 0",
        "url": "https://example.com/0",  # decoded out of the uddg redirect
        "snippet": "Snippet number 0.",
    }
    assert results[2]["url"] == "https://example.com/2"


def test_caps_at_five_results():
    tool = _tool_for(lambda req: httpx.Response(200, text=_canned_html(7)))
    result = tool.execute({"query": "many"}, _ctx())
    assert result.success is True
    assert len(result.content["results"]) == 5
    assert result.content["results"][4]["url"] == "https://example.com/4"


def test_untrusted_note_present_and_wraps_results():
    tool = _tool_for(lambda req: httpx.Response(200, text=_canned_html(2)))
    result = tool.execute({"query": "x"}, _ctx())
    assert set(result.content) == {"untrusted_note", "results"}
    assert "not from the user" in result.content["untrusted_note"]


def test_query_and_desktop_user_agent_are_sent():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["ua"] = req.headers.get("user-agent", "")
        return httpx.Response(200, text=_canned_html(1))

    _tool_for(handler).execute({"query": "black cats"}, _ctx())
    assert "q=" in captured["url"] and "cats" in captured["url"]
    assert "Mozilla" in captured["ua"]


def test_network_error_returns_failure_not_exception():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = _tool_for(handler).execute({"query": "x"}, _ctx())
    assert result.success is False
    assert "internet" in result.content


def test_non_2xx_returns_failure():
    tool = _tool_for(lambda req: httpx.Response(503, text="service unavailable"))
    result = tool.execute({"query": "x"}, _ctx())
    assert result.success is False
    assert isinstance(result.content, str) and result.content


def test_zero_parsed_results_returns_failure():
    tool = _tool_for(lambda req: httpx.Response(200, text="<html><body>nothing here</body></html>"))
    result = tool.execute({"query": "x"}, _ctx())
    assert result.success is False
    assert isinstance(result.content, str) and result.content


def test_blank_query_short_circuits_without_network():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=_canned_html(1))

    result = _tool_for(handler).execute({"query": "   "}, _ctx())
    assert result.success is False
    assert calls["n"] == 0  # never hit the network for an empty query
