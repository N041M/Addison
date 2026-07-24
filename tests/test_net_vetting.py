"""The shared SSRF-safe pin (agent_core/net_vetting.py) and its second caller,
openai_provider.list_models — step 4, contract D1/R1.

read_web_page's own SSRF suite (test_read_web_page.py) is the regression gate for
the FACTORING: it must stay byte-for-byte green after the mechanism moved here.
This file pins the NEW surface: the two-policy design (public-web vs. LAN endpoint)
of the ONE shared mechanism, and that list_models' validating GET is now pinned to
the vetted address with the hostname in Host + TLS SNI — the defence that closes
DNS rebinding on the provider.connect validation request.

The HTTP boundary is httpx.MockTransport, which intercepts BELOW name resolution,
so the resolver is injected (verification item 4's "rebinding fake"). A pinned
request carries the vetted IP in request.url.host and the name in Host + SNI.
"""

from __future__ import annotations

import httpx
import pytest

from agent_core import net_vetting
from agent_core.providers.openai_provider import list_models

_PUBLIC = "93.184.216.34"


# --- classify_local_or_lan: disclosure-only LAN detection (D1/D5) ------------


def test_literal_private_and_loopback_are_lan():
    for url in (
        "http://127.0.0.1:11434/v1",
        "http://192.168.1.9:1234/v1",
        "http://10.0.0.5/v1",
        "http://[::1]/v1",
    ):
        assert net_vetting.classify_local_or_lan(url) is True, url


def test_literal_public_is_not_lan():
    assert net_vetting.classify_local_or_lan(f"https://{_PUBLIC}/v1") is False


def test_localhost_and_dotlocal_names_are_lan_without_resolving():
    # These may not resolve offline; they are LAN by name so the disclosure shows.
    boom = lambda host: (_ for _ in ()).throw(OSError("no dns"))  # noqa: E731
    assert net_vetting.classify_local_or_lan("http://localhost:11434", resolve=boom) is True
    assert net_vetting.classify_local_or_lan("http://mybox.local/v1", resolve=boom) is True


def test_hostname_resolving_to_private_is_lan():
    assert (
        net_vetting.classify_local_or_lan(
            "https://home.example.com/v1", resolve=lambda h: ["192.168.1.50"]
        )
        is True
    )


def test_hostname_resolving_to_public_is_not_lan():
    assert (
        net_vetting.classify_local_or_lan(
            "https://api.example.com/v1", resolve=lambda h: [_PUBLIC]
        )
        is False
    )


# --- the two policies over the ONE shared vet (R1) ---------------------------

_SENTENCES = net_vetting.Sentences(
    no_url="no", not_a_web_link="link", not_allowed="blocked", odd_web_address="odd",
    could_not_find_site="find", could_not_open="open", could_not_reach="reach",
    took_too_long="slow", too_many_redirects="loops", dropped_secure_link="downgrade",
)


def test_public_policy_refuses_a_private_address():
    # read_web_page's stance: loopback/LAN is off-limits for the public web.
    verdict = net_vetting.vet_url(
        "http://127.0.0.1/x", lambda h: ["127.0.0.1"], sentences=_SENTENCES,
        allow_private=False, require_default_port=True, max_url_chars=2048,
    )
    assert verdict.problem == "blocked"


def test_endpoint_policy_allows_the_same_private_address():
    # The custom-server stance: the user's own LAN box is the legitimate case.
    verdict = net_vetting.vet_url(
        "http://127.0.0.1:11434/v1", lambda h: ["127.0.0.1"], sentences=_SENTENCES,
        allow_private=True, require_default_port=False, max_url_chars=2048,
    )
    assert verdict.problem is None
    assert verdict.addresses == ("127.0.0.1",)


def test_endpoint_policy_allows_a_nonstandard_port_public_policy_refuses():
    public = net_vetting.vet_url(
        "http://public.example.com:11434/v1", lambda h: [_PUBLIC], sentences=_SENTENCES,
        allow_private=False, require_default_port=True, max_url_chars=2048,
    )
    assert public.problem == "blocked"          # a port scanner otherwise
    endpoint = net_vetting.vet_url(
        "http://public.example.com:11434/v1", lambda h: [_PUBLIC], sentences=_SENTENCES,
        allow_private=True, require_default_port=False, max_url_chars=2048,
    )
    assert endpoint.problem is None             # :11434 is where a model server lives


def test_userinfo_is_refused_under_both_policies():
    # G1 transcript guard — a credential in the URL is refused whatever the policy.
    for allow_private in (True, False):
        verdict = net_vetting.vet_url(
            "https://svc:sk-KEY@api.example.com/v1", lambda h: [_PUBLIC], sentences=_SENTENCES,
            allow_private=allow_private, require_default_port=False, max_url_chars=2048,
        )
        assert verdict.problem == "link"


# --- list_models is pinned to the vetted address (verification item 4) -------


def test_list_models_pins_the_vetted_ip_and_carries_the_name_in_host_and_sni():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url_host"] = request.url.host
        seen["host_header"] = request.headers.get("host")
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, json={"data": [{"id": "m-1"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ids = list_models(
        "https://models.example.com/v1", lambda: "sk-a", client=client,
        resolve=lambda h: [_PUBLIC],
    )
    assert ids == ["m-1"]
    # The socket went to the vetted IP; the name travelled in Host + SNI so the
    # certificate is still checked against the name (never against the IP).
    assert seen["url_host"] == _PUBLIC
    assert seen["host_header"] == "models.example.com"
    assert seen["sni"] == "models.example.com"


def test_list_models_resolves_once_so_a_rebind_cannot_move_the_connection():
    # The rebinding fake (item 4): a resolver that would answer differently the
    # second time. The pin resolves ONCE and connects to that first answer, so the
    # second answer never gets a chance to redirect the socket.
    answers = [[_PUBLIC], ["127.0.0.1"]]
    lookups: list[str] = []

    def resolve(host: str) -> list[str]:
        lookups.append(host)
        return answers[min(len(lookups) - 1, len(answers) - 1)]

    contacted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        contacted.append(request.url.host)
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    list_models("https://rebind.example.com/v1", lambda: "sk-a", client=client, resolve=resolve)
    assert lookups == ["rebind.example.com"]   # asked once, not once per connection
    assert contacted == [_PUBLIC]              # the vetted answer is where it went


def test_list_models_allows_a_loopback_custom_server():
    # The custom-server case: the user's own Ollama on localhost must NOT be refused
    # (the public-web pin would have blocked it — this proves the endpoint policy).
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json={"data": [{"id": "llama"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ids = list_models(
        "http://myollama.local:11434/v1", lambda: "", client=client, require_key=False,
        resolve=lambda h: ["127.0.0.1"],
    )
    assert ids == ["llama"]


def test_list_models_refuses_a_base_url_that_resolves_nowhere():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(RuntimeError):
        list_models(
            "https://nowhere.example.com/v1", lambda: "sk-a", client=client,
            resolve=lambda h: [],
        )
