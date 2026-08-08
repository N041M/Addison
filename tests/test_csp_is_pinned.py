"""The webview's Content-Security-Policy, pinned (Phase-3 review-surface plan, "The
one thing to understand before starting: the CSP").

The policy in ``shell/src-tauri/tauri.conf.json`` is the webview's lowest-trust
posture written down. Relative to what the app shipped with — which was literally
``default-src 'self'`` and nothing else — it loosens exactly **two** things, and both
are named here:

* ``style-src 'self' 'unsafe-inline'``, so that Monaco's ESM build can position its
  view lines with inline ``style="…"`` attributes — a relaxation a nonce cannot
  substitute for, because nonces do not apply to style attributes at all;
* ``img-src 'self' data:``, so an inline data-URI image (an avatar, a favicon, a
  mermaid diagram) renders at all. Under ``default-src 'self'`` those were blocked.

Everything else in the same edit went the other way: ``object-src`` and ``frame-src``
dropped from an inherited ``'self'`` to ``'none'``, and ``base-uri`` / ``form-action``
— which do NOT fall back to ``default-src``, and were therefore unrestricted — were
locked down.

("Widened exactly once" is what this file used to say, and it was false about the very
string it pins: ``img-src``'s ``data:`` is a second loosening, and a test named for the
claim checked only that ``'unsafe-inline'`` had not spread. Both are corrected here.)

This test is three assertions, and the second and third are the ones that matter in
a year:

1. **Exact equality** with the reviewed literal. A widening is a decision somebody
   makes on purpose, in a diff a reviewer reads.
2. ``script-src`` — and its ``-elem``/``-attr`` refinements, which OVERRIDE it — never
   admits ``'unsafe-eval'`` or ``'unsafe-inline'``. This is the plan's BRIGHT LINE: the
   residual risk the widening accepts is visual spoofing, and the whole reason that is
   the *only* residual risk is that script execution stayed at ``'self'``. (The "Monaco
   needs unsafe-eval" folklore comes from the AMD ``loader.js``, which the ESM build
   does not use.)
3. **Every source in the policy is drawn from a tiny fixed vocabulary** — ``'self'``,
   ``'none'``, and the two sanctioned looseners in the two directives that carry them.
   An ALLOWLIST, deliberately, because the blocklist this rule used to be did not hold:
   it caught ``https://cdn.example``, ``blob:`` and a bare ``*``, and let
   ``connect-src 'self' telemetry.example.com`` straight through — a host source with
   no scheme, which is the ORDINARY way a CDN or a telemetry endpoint is written. In a
   local-first app a remote ``connect-src`` is precisely the exfiltration path this
   file claims to hold the line on.

Equality alone would be a worse test than no test: it teaches the next contributor
to paste the new string. Rules 2 and 3 survive a LEGITIMATE re-pin, so a policy that
is honestly re-reviewed still cannot smuggle those in.

**DIRECTIVES ARE PARSED FIRST-WINS, AND A REPEATED ONE IS AN OUTRIGHT FAILURE.** CSP
enforces the FIRST occurrence of a directive and ignores every later one. This file
used to build its map last-wins, so ``script-src 'unsafe-eval'; …; script-src 'self'``
passed rules 2 and 3 while the browser enforced ``'unsafe-eval'`` — the rules read the
decorative copy and the webview obeyed the real one. First-wins is what the browser
does; refusing the repeat as well is because a policy that states a directive twice is
one no reader can check by eye, whichever half is enforced.

WHAT THIS DOES NOT PROVE, and one thing it used to claim falsely. It pins the AUTHORED
string. Tauri parses ``app.security.csp`` into a directive map at runtime and injects
its own nonces and hashes — into ``script-src`` and ``style-src`` and NOWHERE ELSE
(``tauri::manager::set_csp``, verified against tauri 2.11.5, the version in
``Cargo.lock``). It does **not** inject IPC sources: Tauri's own documentation has the
app author ``connect-src ipc: http://ipc.localhost`` by hand, and this policy
deliberately does not (see docs/KNOWN-GAPS.md — the consequence is that Tauri's
custom-protocol IPC is blocked and it falls back to ``window.ipc.postMessage``, which
is how this app has always run). What a running webview does with the delivered header
is what ``docs/TESTING-CHECKLIST.md`` §13c is for, on all three platforms rather than
only macOS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_CONF = Path(__file__).resolve().parents[1] / "shell" / "src-tauri" / "tauri.conf.json"

# The reviewed literal. Changing this line is the decision; the structural rules below
# are what a changed line still has to survive.
EXPECTED_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "worker-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-src 'none'"
)

# The ENTIRE vocabulary this policy may draw on, anywhere.
#
# AN ALLOWLIST, and that is the whole point of it. The rule underneath used to be a
# blocklist of shapes — `*`, `http:`, `https:`, `blob:` and a scheme-prefix regex — and
# a CSP source expression has shapes none of those match: a bare host
# (`telemetry.example.com`), a wildcard host (`*.example.com`), a scheme-relative
# authority (`//example.com`), a bare address (`10.0.0.1:9000`). Every one of them is a
# remote origin, and every one of them passed. A blocklist of shapes can only ever
# refuse the shapes somebody thought of.
_ALLOWED_EVERYWHERE = frozenset({"'self'", "'none'"})

# The two sanctioned looseners, each tied to the ONE directive it was argued for. This
# pair is also the answer to "which directives loosen what `default-src 'self'` already
# allowed?" — there are exactly two, and these are they.
_SANCTIONED_LOOSENINGS: dict[str, frozenset[str]] = {
    # Monaco writes inline style attributes; nonces do not apply to style attributes.
    "style-src": frozenset({"'unsafe-inline'"}),
    # An inline image: an avatar, a favicon, a mermaid data URI. Anywhere else a
    # `data:` is a script/style/frame whose contents the policy cannot see.
    "img-src": frozenset({"data:"}),
}

# `script-src` alone is not the bright line: `script-src-elem` and `script-src-attr`
# OVERRIDE it for the loads they cover, so a policy that keeps `script-src 'self'` and
# adds `script-src-elem 'unsafe-inline'` has moved the line while leaving the sentence
# everybody reads untouched.
_SCRIPT_DIRECTIVES = ("script-src", "script-src-elem", "script-src-attr")


def _csp() -> str:
    conf = json.loads(_CONF.read_text(encoding="utf-8"))
    csp = conf["app"]["security"]["csp"]
    assert isinstance(csp, str), "app.security.csp must be a single string policy"
    return csp


def _parse(csp: str) -> tuple[dict[str, list[str]], list[str]]:
    """``({name: [source, …]}, [repeated name, …])``, lowercased names.

    FIRST-WINS, because that is what a browser does with a repeated directive: the
    first occurrence is enforced and every later one is ignored. Building this map
    last-wins — as it was built — meant the rules below inspected a directive the
    webview was not obeying.
    """
    out: dict[str, list[str]] = {}
    repeated: list[str] = []
    for chunk in csp.split(";"):
        parts = chunk.split()
        if not parts:
            continue
        name = parts[0].lower()
        if name in out:
            repeated.append(name)
            continue  # first wins, exactly as the browser decides it
        out[name] = parts[1:]
    return out, repeated


def _directives(csp: str) -> dict[str, list[str]]:
    return _parse(csp)[0]


def _outside_the_vocabulary(directives: dict[str, list[str]]) -> list[str]:
    """Every ``directive source`` pair the vocabulary does not admit."""
    offending: list[str] = []
    for name, sources in directives.items():
        allowed = _ALLOWED_EVERYWHERE | _SANCTIONED_LOOSENINGS.get(name, frozenset())
        offending.extend(f"{name} {source}" for source in sources if source.lower() not in allowed)
    return offending


def test_the_csp_is_exactly_the_reviewed_literal() -> None:
    """A silent widening. The policy is one string in one file, and this is the diff
    line that makes changing it a decision rather than a side effect of getting some
    library to render."""
    assert _csp() == EXPECTED_CSP, (
        "shell/src-tauri/tauri.conf.json's app.security.csp no longer matches the "
        "reviewed policy. A WIDENING IS AN OWNER-GRADE DECISION, not a re-pin: this "
        "message is not permission to paste the new string in above. If the policy "
        "genuinely changed, say why in the PR, and expect the structural rules below "
        "to refuse anything that is not 'self', 'none', or one of the two looseners "
        "they already name."
    )


def test_no_directive_is_stated_twice() -> None:
    """Kills: `script-src 'unsafe-eval'; …; script-src 'self'`.

    A browser enforces the FIRST occurrence. A policy that states a directive twice
    therefore has a copy that is enforced and a copy that is decorative, and no reader
    can tell by eye which they are looking at — so the answer is neither "check the
    first" nor "check the last" but "refuse the shape".
    """
    _, repeated = _parse(_csp())
    assert repeated == [], (
        f"these directives are stated more than once: {repeated}. The browser obeys "
        f"the FIRST of each and ignores the rest, so the later ones are a claim about "
        f"the policy that the policy does not keep."
    )


def test_script_src_never_admits_unsafe_eval_or_unsafe_inline() -> None:
    """The plan's bright line, as a rule rather than a paragraph.

    Kills: widening `script-src` to make a rendering choice work, AND doing the same
    thing one directive over — `script-src-elem` / `script-src-attr` override
    `script-src` for the loads they cover, so relaxing one of those moves the line
    while leaving the sentence everybody reads untouched. Monaco's ESM build needs
    none of it; if a future editor/library does, the answer is the bespoke `<pre>` +
    highlight.js viewer, not this string.
    """
    directives = _directives(_csp())
    assert directives.get("script-src") is not None, (
        "script-src must be stated explicitly, never inherited"
    )
    for name in _SCRIPT_DIRECTIVES:
        sources = directives.get(name)
        if sources is None:
            continue  # absent is the strictest answer: it falls back to script-src
        for banned in ("'unsafe-eval'", "'unsafe-inline'", "'wasm-unsafe-eval'"):
            assert banned not in sources, (
                f"{name} admits {banned}. The widening this app accepted is style-src "
                f"ONLY: its whole residual risk is visual spoofing, and that is true "
                f"only while script execution stays at 'self'."
            )
        # `'strict-dynamic'` would let a single trusted script authorise arbitrary
        # further loads, which is the same hole by another name in an app that has no
        # nonce-managed script pipeline.
        assert "'strict-dynamic'" not in sources, f"{name} must not admit 'strict-dynamic'"


def test_every_source_is_drawn_from_the_policys_own_vocabulary() -> None:
    """Kills: a CDN, a telemetry endpoint, a `blob:` worker, a `*`, an injected nonce
    — and, the case the blocklist this replaced could not see, a BARE HOST.

    `blob:` is the specific one this app has a live temptation for — Vite's
    `?worker&inline` produces a blob URL, and every "Monaco needs blob:" answer online
    is the AMD `getWorkerUrl` recipe. The worker is bundled with a plain `?worker`, so
    `worker-src 'self'` holds.

    `ipc:` and `http://ipc.localhost` are the OTHER live temptation, and they are
    refused here on purpose. Tauri's custom-protocol IPC is blocked by this policy and
    falls back to `window.ipc.postMessage`; the tempting fix is to paste Tauri's
    documented `connect-src ipc: http://ipc.localhost` in and be done. That is a
    widening of the one directive that governs where this app may talk to, in an app
    whose whole posture is that it talks nowhere — an owner decision, recorded in
    docs/KNOWN-GAPS.md, not a test somebody edits to make a diagnostic quieter.
    """
    offending = _outside_the_vocabulary(_directives(_csp()))
    assert offending == [], (
        f"these sources are outside the policy's vocabulary: {offending}. This app is "
        f"local-first: nothing the window renders or reaches comes from anywhere but "
        f"the app itself, so the only sources are 'self', 'none', and the two "
        f"looseners named in _SANCTIONED_LOOSENINGS."
    )


@pytest.mark.parametrize(
    "source,why",
    [
        ("telemetry.example.com", "a bare host — the ordinary way an endpoint is written"),
        ("*.example.com", "a wildcard host"),
        ("//example.com", "a scheme-relative authority"),
        ("10.0.0.1:9000", "a bare address and port"),
        ("https://cdn.example", "a scheme-prefixed remote origin"),
        ("blob:", "the `?worker&inline` / getWorkerUrl temptation"),
        ("*", "the wildcard"),
        ("ipc:", "Tauri's IPC scheme — an owner decision, not a test edit"),
        ("http://ipc.localhost", "Tauri's IPC origin on Windows"),
        ("asset:", "Tauri's asset protocol"),
        ("'unsafe-eval'", "a script relaxation, caught twice on purpose"),
        ("'nonce-abcdef'", "a nonce Tauri injects at runtime and nobody authors"),
        ("data:", "admitted in img-src and refused everywhere else"),
    ],
)
def test_the_vocabulary_rule_catches_the_shapes_a_blocklist_missed(
    source: str, why: str
) -> None:
    """The rule above, driven against sources that are NOT in the policy.

    Without this, `test_every_source_is_drawn_from_the_policys_own_vocabulary` passes
    on an empty vocabulary check — it asserts a list is empty, and a checker that
    always returns an empty list satisfies it. The four host shapes at the top of this
    list are the measured failure: with the previous blocklist, `connect-src 'self'
    telemetry.example.com` re-pinned honestly gave 9 passed and a green suite.
    """
    doctored = EXPECTED_CSP.replace("connect-src 'self'", f"connect-src 'self' {source}")
    assert doctored != EXPECTED_CSP, "the fixture must actually alter the policy"
    offending = _outside_the_vocabulary(_directives(doctored))
    assert f"connect-src {source}" in offending, (
        f"the vocabulary rule let `{source}` through ({why})"
    )


@pytest.mark.parametrize(
    "directive,expected",
    [
        # The four that are net-TIGHTER than what the app shipped with. Two of them
        # (`base-uri`, `form-action`) do not fall back to `default-src` at all, so
        # before this policy they were unrestricted — `base-uri` matters more once
        # `style-src` is loose, because a rewritten <base> retargets every relative
        # URL on the page.
        ("object-src", ["'none'"]),
        ("frame-src", ["'none'"]),
        ("base-uri", ["'none'"]),
        ("form-action", ["'none'"]),
        # Stated explicitly even though it falls back through child-src → script-src
        # → default-src, so a reviewer sees `'self'` — not `blob:` — as a decision.
        ("worker-src", ["'self'"]),
    ],
)
def test_the_tightenings_that_came_with_the_widening(
    directive: str, expected: list[str]
) -> None:
    """Kills: quietly dropping the half of this edit that made the policy stricter,
    while keeping the half that made it looser."""
    assert _directives(_csp()).get(directive) == expected


def test_exactly_two_directives_loosen_the_inherited_default() -> None:
    """Kills: a THIRD loosening, and the false claim this test used to make.

    Its name said "style-src is the only directive that was loosened" and its body
    checked only that `'unsafe-inline'` had not spread to a second directive — which
    is a different, much smaller statement. Measured against the policy this file
    actually pins, the name was false: the app shipped with `default-src 'self'` and
    nothing else, under which a `data:` image URI was blocked, so `img-src 'self'
    data:` is a second loosening and always was.

    So the test now asserts what a reader of the name would expect: the COMPLETE set
    of places this policy admits anything beyond `'self'`/`'none'`, named, with
    nothing else in it.
    """
    found = {
        (name, source)
        for name, sources in _directives(_csp()).items()
        for source in sources
        if source.lower() not in _ALLOWED_EVERYWHERE
    }
    sanctioned = {
        (name, source)
        for name, sources in _SANCTIONED_LOOSENINGS.items()
        for source in sources
    }
    assert found == sanctioned, (
        f"the set of loosenings changed. Expected exactly {sorted(sanctioned)} — the "
        f"inline style attributes Monaco writes, and inline data-URI images — and "
        f"found {sorted(found)}. Each one is a named argument in this file's "
        f"docstring; a third needs its own."
    )
