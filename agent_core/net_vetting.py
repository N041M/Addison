"""SSRF-safe pinned HTTP execution, shared by every caller whose destination is
chosen by untrusted or model-influenced input (step 4, contract D1/R1).

WHY THIS MODULE EXISTS. ``read_web_page`` grew the only correct implementation of
"fetch a URL without being tricked into reaching inside the trust boundary": it
resolves the host, VETS the resolved addresses, then PINS the connection to a
vetted address (connects by IP literal) while carrying the hostname in the
``Host`` header and the TLS SNI, so the certificate is still verified against the
NAME. Redirects are followed by hand (``follow_redirects=False``) so every hop is
re-vetted and re-pinned. Step 4 makes a SECOND kind of request reachable through a
model/user-influenced address — the ``provider.connect`` validation GET to a
custom OpenAI-compatible server — and it must adopt the same defence rather than
grow a weaker copy of it. So the WHOLE pinned-request execution lives here now
(not just ``pinned_url``): reusing only the URL rewrite would make httpx verify the
cert against the IP and refuse every legitimate HTTPS server, or tempt someone to
weaken cert verification, which is a worse hole (contract R1).

This is a top-level ``agent_core`` module, not under ``tools/``/``providers/``/
``routines/``, so both ``tools.read_web_page`` and ``providers.openai_provider``
may import it without crossing the module-boundary rule (spec §2).

TWO POLICIES, ONE MECHANISM. The pinning + redirect re-vet loop is identical for
both callers; only the vetting DECISION differs, and it is a parameter:

  * ``read_web_page`` (the public web): ``allow_private=False`` +
    ``require_default_port=True`` — a page lives on a public host on the standard
    port; loopback/LAN/metadata and odd ports are refused.
  * ``provider.connect`` custom endpoint (the user's OWN LAN model host):
    ``allow_private=True`` + ``require_default_port=False`` — loopback/private
    ranges and non-standard ports are the LEGITIMATE case (``http://localhost:11434``
    is their Ollama). The pin still closes rebinding (a public-looking hostname
    cannot swap to a different address between vet and connect), disabling
    redirects closes the redirect re-vet gap, and the endpoint card DISCLOSES a
    LAN target to the user (rpc/providers.py, D5) — the three together cover the
    LAN case without blocking it.

The plain-language sentences a caller shows on each failure are ALSO a parameter
(``Sentences``): the page tool says "public web pages only", the connect path says
"couldn't reach that server". The mechanism is shared; the words are the caller's.

The ``resolve`` seam is injectable so tests can stub DNS — ``httpx.MockTransport``
intercepts BELOW name resolution, so a resolve-then-check design is untestable
offline unless the resolution itself is injectable. The client is NEVER created
here: each caller owns its client lifecycle (``read_web_page`` builds one with
``trust_env=False`` — the one client in the repo whose destination is untrusted).
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

# The scheme's standard port — the only port ``require_default_port`` accepts.
_DEFAULT_PORTS = {"http": 80, "https": 443}
# How many of a name's vetted addresses to try before giving up. A name commonly
# answers with both an A and an AAAA record and only one is reachable from this
# machine; pinning to the first alone would turn that into "I couldn't reach it".
# Every address tried has already passed the vet.
MAX_ADDRESS_ATTEMPTS = 3


@dataclass(frozen=True)
class Sentences:
    """The plain-language failure strings a caller hands back, one per outcome.

    Kept as data rather than hard-coded here because the two callers speak to
    different audiences about different things — a page vs. a model server — and
    the mechanism must not decide their words."""

    no_url: str
    not_a_web_link: str
    not_allowed: str          # off-limits address, or a refused port
    odd_web_address: str
    could_not_find_site: str
    could_not_open: str
    could_not_reach: str
    took_too_long: str
    too_many_redirects: str
    dropped_secure_link: str


@dataclass
class Verdict:
    """The answer from ``vet_url``: a refusal sentence, or the addresses cleared."""

    problem: str | None
    addresses: tuple[str, ...] = ()


@dataclass
class _Redirect:
    """A hop the server asked for. Raw and unvetted — the loop re-vets it."""

    location: str


class VettingError(Exception):
    """Carries the plain sentence the caller hands back — never a stack trace.

    ``retryable`` marks a TRANSIENT network failure (a connect/read timeout, a
    connection reset) as opposed to a settled refusal (a blocked address, a
    redirect loop, a malformed URL). A caller that retries idempotent GETs (the
    ``provider.connect`` model-listing validation) inspects it to decide whether a
    second attempt could plausibly succeed; ``read_web_page`` ignores it. It is a
    hint, never a policy — the flag never widens what is fetched."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def resolve_host(hostname: str) -> list[str]:
    """Every address a hostname currently answers with, both families.

    Split out so tests can inject a resolver (``httpx.MockTransport`` intercepts
    below DNS, so resolve-then-check is untestable offline otherwise)."""
    infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def address_is_public(raw: str) -> bool:
    """True only for an address genuinely out on the public internet."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    # Unwrap the IPv6 forms that carry an IPv4 address inside them, so
    # ::ffff:127.0.0.1 and 2002:7f00:1::1 are judged as the 127.0.0.1 they are.
    # Recent CPython classifies both on its own; doing it here too keeps the guard
    # from depending on that staying true.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is None:
        mapped = getattr(address, "sixtofour", None)
    if mapped is not None:
        address = mapped
    if (
        address.is_loopback       # 127.0.0.0/8, ::1 — the user's own machine
        or address.is_private     # RFC1918 LAN, IPv6 unique-local fc00::/7
        or address.is_link_local  # 169.254.0.0/16 — includes cloud metadata
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return False
    # Belt to those braces: anything the stdlib does not consider globally
    # routable is not a public web page either. Deciding by an allow-rule rather
    # than a blocklist is the point: a blocklist is always one address short,
    # because the attacker picks the address.
    return bool(address.is_global)


def _explicit_port(parts) -> int | None:
    """The URL's port when it is NOT the scheme's default, else None.

    A browser omits the default port from ``Host`` and from the connection it
    describes, so omitting it keeps the public-web caller byte-identical to what it
    sent before this function existed. Everything else MUST carry it — see
    ``pinned_url``."""
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        return None
    return None if port == _DEFAULT_PORTS.get(parts.scheme.lower()) else port


def host_header(parts) -> str:
    """What a browser would put in ``Host`` — the name, re-bracketed if IPv6, and
    the port when it is not the scheme's default (a vhosted server on :11434 must
    be addressed as ``host:11434``, exactly as httpx would)."""
    hostname = parts.hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = _explicit_port(parts)
    return f"{host}:{port}" if port is not None else host


def pinned_url(parts, address: str) -> str:
    """The same request, addressed to the ONE vetted address — PORT INCLUDED.

    The name is not in this URL at all, so nothing resolves it a second time. The
    name still travels — in ``Host`` and, for https, in the TLS SNI — so virtual
    hosting works and the certificate is still checked against the HOSTNAME. Cert
    verification is not weakened; it is simply pointed at the name it was always
    meant to be pointed at.

    THE PORT IS PART OF THE DESTINATION. Dropping it was harmless while the only
    caller required the default port, and became a live defect the moment step 4
    allowed any port: ``http://localhost:11434/v1`` silently became a request to
    ``127.0.0.1:80`` — a DIFFERENT service on the same machine, carrying the
    caller's ``Authorization`` header to it. So every non-default port is carried
    through, and the default port is omitted (what a browser does) so the
    public-web caller's requests are byte-identical to before."""
    host = f"[{address}]" if ":" in address else address
    port = _explicit_port(parts)
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """(scheme, host, effective port) — the identity a credential is scoped to.
    The same triple httpx compares when it decides whether to keep ``Authorization``
    across a redirect.

    Returns None for a URL that cannot even be parsed (a malformed IPv6 literal
    makes ``urlsplit`` raise). None never compares equal to another origin, INCLUDING
    another None — the caller treats "I can't tell where this is going" as "not the
    origin the credential was issued for", so an unparseable hop drops the key.
    ``vet_url`` refuses such a URL a moment later anyway; failing closed here means
    the ordering of those two checks can never matter."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        return None
    return (scheme, (hostname or "").lower(), port or _DEFAULT_PORTS.get(scheme))


def vet_url(
    url: object,
    resolve: Callable[[str], list[str]],
    *,
    sentences: Sentences,
    allow_private: bool,
    require_default_port: bool,
    max_url_chars: int,
) -> Verdict:
    """Judge a URL, and hand back the exact addresses the caller may connect to.

    WHY BY RESOLVED IP, NOT HOSTNAME STRING: whoever chose the URL also owns its
    DNS record. ``pages.example.com`` is on nobody's blocklist and can answer
    127.0.0.1 whenever its owner likes, so refusing the literal strings
    "localhost"/"127.0.0.1" stops nothing. Resolving first and judging the
    ADDRESSES is the only check that holds. Under ``allow_private`` the address
    class is NOT a refusal — a custom server is meant to be on the LAN — but the
    addresses still come back so the connection is PINNED to them (rebinding
    closed regardless of policy).

    EVERY address must pass when public-only, not just the first — a name that
    answers with one public and one loopback address would otherwise get through
    on a lucky ordering."""
    if not isinstance(url, str) or not url.strip():
        return Verdict(sentences.no_url)
    candidate = url.strip()
    if len(candidate) > max_url_chars:
        return Verdict(sentences.odd_web_address)
    lowered = candidate.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return Verdict(sentences.not_a_web_link)
    try:
        parts = urlsplit(candidate)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return Verdict(sentences.not_a_web_link)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return Verdict(sentences.not_a_web_link)
    # Userinfo refused. The disguise case (http://example.com@127.0.0.1/) is
    # already dead — the address checks judge 127.0.0.1, what ``hostname`` parses
    # to. This guards the TRANSCRIPT: a fetched URL can be echoed back to the
    # model, so a live credential in it would copy out of the machine.
    if parts.username or parts.password:
        return Verdict(sentences.not_a_web_link)
    if not hostname:
        return Verdict(sentences.not_a_web_link)
    if require_default_port and port is not None and port != _DEFAULT_PORTS[scheme]:
        # Public web pages live on the standard port; allowing any would make this
        # a port scanner pointed at public hosts. (Off under the endpoint policy —
        # a LAN model server runs on :11434, :1234, ...)
        return Verdict(sentences.not_allowed)

    # A literal address needs no lookup — judge it directly, and never resolve.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not allow_private and not address_is_public(str(literal)):
            return Verdict(sentences.not_allowed)
        return Verdict(None, (str(literal),))

    try:
        addresses = resolve(hostname)
    except (OSError, UnicodeError):
        # UnicodeError too: getaddrinfo runs the name through IDNA first, and a
        # name built to fail that raises UnicodeEncodeError instead of OSError.
        return Verdict(sentences.could_not_find_site)
    if not addresses:
        return Verdict(sentences.could_not_find_site)
    if not allow_private and not all(address_is_public(a) for a in addresses):
        return Verdict(sentences.not_allowed)
    return Verdict(None, tuple(addresses))


def _request_once(
    client: httpx.Client,
    logical: str,
    address: str,
    *,
    base_headers: dict,
    on_final: Callable[[httpx.Response, str], Any],
    sentences: Sentences,
    timeout: float,
    max_url_chars: int,
) -> Any:
    """One GET, pinned to ``address``, with ``logical``'s name in Host + SNI.

    A 3xx returns a ``_Redirect`` for the driver to re-vet; any other status is
    handed to ``on_final`` INSIDE the stream context (so a streaming reader can
    still pull the body). ``on_final`` owns everything response-specific — content
    type, byte caps, status-to-message mapping — because that differs per caller;
    the pin, the Host header, the SNI extension and ``follow_redirects=False`` do
    not, and they live here."""
    parts = urlsplit(logical)
    headers = dict(base_headers)
    headers["Host"] = host_header(parts)
    with client.stream(
        "GET",
        pinned_url(parts, address),
        headers=headers,
        # Read by httpcore as the TLS server_hostname, so the certificate is
        # checked against the site's name even though the connection is by IP.
        extensions={"sni_hostname": parts.hostname or ""},
        # timeout on the call, not the client, so an injected client carries it too.
        timeout=timeout,
        follow_redirects=False,
    ) as response:
        if 300 <= response.status_code < 400:
            location = response.headers.get("location", "")
            if not location:
                raise VettingError(sentences.could_not_open)
            if len(location) > max_url_chars:
                raise VettingError(sentences.odd_web_address)
            return _Redirect(location)
        return on_final(response, logical)


def _one_hop(
    client: httpx.Client,
    logical: str,
    addresses: tuple[str, ...],
    *,
    base_headers: dict,
    on_final: Callable[[httpx.Response, str], Any],
    sentences: Sentences,
    timeout: float,
    max_url_chars: int,
    deadline: float | None = None,
) -> Any:
    """One hop: try the vetted addresses in turn, translate httpx failures to plain
    words. ``on_final``'s own exceptions (a caller's refusal) are NOT httpx errors,
    so they propagate untouched to the caller."""
    if not addresses:  # vet never returns a clean verdict with none; belt anyway
        raise VettingError(sentences.could_not_find_site)
    attempts = addresses[:MAX_ADDRESS_ATTEMPTS]
    last = len(attempts) - 1
    for index, address in enumerate(attempts):
        # The budget bounds THIS loop as well as the hop loop above it. Bounding
        # only hops leaves MAX_ADDRESS_ATTEMPTS x timeout inside each one, which is
        # most of the wait: three addresses at 10s is half a minute before the hop
        # loop gets a chance to look at the clock.
        attempt_timeout = timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VettingError(sentences.took_too_long, retryable=True)
            attempt_timeout = min(timeout, remaining)
        try:
            return _request_once(
                client,
                logical,
                address,
                base_headers=base_headers,
                on_final=on_final,
                sentences=sentences,
                timeout=attempt_timeout,
                max_url_chars=max_url_chars,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # This address didn't answer; a name commonly has one reachable and
            # one not. Both were vetted, so trying the next widens nothing. Listed
            # before TimeoutException because ConnectTimeout is one. Transient, so
            # a retrying caller may try again (retryable=True).
            if index == last:
                raise VettingError(sentences.could_not_reach, retryable=True) from None
        except httpx.TimeoutException:
            raise VettingError(sentences.took_too_long, retryable=True) from None
        except httpx.HTTPError:
            raise VettingError(sentences.could_not_reach, retryable=True) from None
        except (httpx.InvalidURL, UnicodeError):
            # Neither is an httpx.HTTPError. Reachable from a hostile hop: httpx
            # builds the redirect request eagerly even with follow_redirects=False,
            # so a "Location: javascript:..." raises InvalidURL; a hostname httpx
            # cannot IDNA-encode raises idna.IDNAError (a UnicodeError).
            raise VettingError(sentences.odd_web_address) from None
    raise VettingError(sentences.could_not_reach)


def open_vetted(
    client: httpx.Client,
    url: str,
    *,
    resolve: Callable[[str], list[str]],
    on_final: Callable[[httpx.Response, str], Any],
    sentences: Sentences,
    base_headers: dict | None = None,
    credential_headers: dict | None = None,
    allow_private: bool,
    require_default_port: bool,
    max_url_chars: int,
    max_redirects: int,
    timeout: float,
    total_timeout: float | None = None,
) -> Any:
    """Vet, fetch, and follow redirects by hand, pinning every hop. Returns
    whatever ``on_final`` returns; raises ``VettingError`` (plain sentence) on any
    refusal or network failure.

    Redirects are followed manually so every hop goes back through ``vet_url`` and
    is then pinned to the vetted address. A public page answering 302 to
    http://localhost is the obvious way around a check that only looked at the URL
    the caller supplied; handing the hop list to httpx would reopen exactly that.
    ``client`` is never created or closed here — the caller owns its lifecycle.

    TWO HEADER BAGS, AND THE SPLIT IS LOAD-BEARING. ``base_headers`` (a User-Agent,
    an Accept) travel to every hop. ``credential_headers`` — an API key — travel
    ONLY while the request is still going to the origin the caller aimed it at, and
    are dropped the moment a redirect crosses to a different scheme/host/port.

    This is not caution, it is a defect that shipped and was caught: following
    redirects by hand replaced ``httpx``'s follower, and ``httpx`` strips
    ``Authorization`` cross-origin (``Client._redirect_headers``). The hand-rolled
    loop did not, so a custom model server — or anything able to answer 302 for it —
    could harvest the user's API key verbatim by redirecting the validation GET at
    itself. A SEPARATE PARAMETER rather than a "strip anything called authorization"
    rule, because the next caller to put a secret in a header must inherit the
    protection by construction, not by naming their header correctly.

    ``timeout`` bounds ONE socket; ``total_timeout`` bounds the WHOLE walk. They
    are different numbers because this loop multiplies: up to
    ``MAX_ADDRESS_ATTEMPTS`` addresses per hop, times ``max_redirects + 1`` hops,
    each on its own ``timeout``. With a 10-second socket timeout that is two
    minutes before anything gives up — and four if the caller then retries. A
    caller a person is actively waiting on (a connect card) passes
    ``total_timeout``; each socket then gets ``min(timeout, what is left)``, so the
    product is bounded by one number the caller chose. Same reasoning as the
    step-3 fallback budget: a per-attempt timeout is not a budget."""
    headers = dict(base_headers or {})
    secrets = dict(credential_headers or {})
    current = url.strip()
    origin = _origin(current)
    started_secure = current.lower().startswith("https://")
    deadline = None if total_timeout is None else time.monotonic() + total_timeout
    for _ in range(max_redirects + 1):
        hop_timeout = timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VettingError(sentences.took_too_long, retryable=True)
            hop_timeout = min(timeout, remaining)
        verdict = vet_url(
            current,
            resolve,
            sentences=sentences,
            allow_private=allow_private,
            require_default_port=require_default_port,
            max_url_chars=max_url_chars,
        )
        if verdict.problem is not None:
            raise VettingError(verdict.problem)
        outcome = _one_hop(
            client,
            current,
            verdict.addresses,
            base_headers={**headers, **secrets},
            on_final=on_final,
            sentences=sentences,
            timeout=hop_timeout,
            max_url_chars=max_url_chars,
            deadline=deadline,
        )
        if not isinstance(outcome, _Redirect):
            return outcome
        next_url = urljoin(current, outcome.location)
        if started_secure and next_url.lower().startswith("http://"):
            # A chain that began on https must not quietly finish on http — the
            # body of that last hop can be rewritten by anyone on the path.
            raise VettingError(sentences.dropped_secure_link)
        next_origin = _origin(next_url)
        if secrets and (next_origin is None or origin is None or next_origin != origin):
            # Leaving the origin the credential was issued for: drop it, and never
            # pick it up again even if a later hop returns to that origin (a chain
            # that has passed through a foreign host is not one to re-trust).
            secrets = {}
        current = next_url  # re-vetted at the top of the next pass
    raise VettingError(sentences.too_many_redirects)


def classify_local_or_lan(
    url: str, resolve: Callable[[str], list[str]] | None = None
) -> bool:
    """True when a base URL points at this computer or the local network (D1/D5).

    Best-effort and side-effect-light: a literal address is judged directly; a
    hostname is resolved (via the injectable seam) and counts as LAN if ANY
    resolved address is off the public internet. It is used ONLY to DISCLOSE a LAN
    target on the endpoint card — never to allow or block a connection, so a wrong
    answer costs at most one disclosure sentence, never safety. Common local names
    that may not resolve offline (``localhost``, ``*.local``) are treated as LAN so
    the disclosure still shows."""
    resolver = resolve if resolve is not None else resolve_host
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return False
    if not hostname:
        return False
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return not address_is_public(str(literal))
    lowered = hostname.lower()
    if lowered == "localhost" or lowered.endswith((".local", ".lan", ".internal", ".home")):
        return True
    try:
        addresses = resolver(hostname)
    except (OSError, UnicodeError):
        return False
    return bool(addresses) and any(not address_is_public(a) for a in addresses)
