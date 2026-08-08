"""The webview's Content-Security-Policy, pinned (Phase-3 review-surface plan, "The
one thing to understand before starting: the CSP").

The policy in ``shell/src-tauri/tauri.conf.json`` is the webview's lowest-trust
posture written down. It was widened exactly once, deliberately, so that Monaco's
ESM build can position its view lines with inline ``style="…"`` attributes — a
relaxation a nonce cannot substitute for, because nonces do not apply to style
attributes at all. Everything else in the same edit went the other way: ``object-src``
and ``frame-src`` dropped from an inherited ``'self'`` to ``'none'``, and ``base-uri``
/ ``form-action`` — which do NOT fall back to ``default-src``, and were therefore
unrestricted — were locked down.

This test is three assertions, and the second and third are the ones that matter in
a year:

1. **Exact equality** with the reviewed literal. A widening is a decision somebody
   makes on purpose, in a diff a reviewer reads.
2. ``script-src`` never admits ``'unsafe-eval'`` or ``'unsafe-inline'``. This is the
   plan's BRIGHT LINE: the residual risk the widening accepts is visual spoofing, and
   the whole reason that is the *only* residual risk is that script execution stayed
   at ``'self'``. (The "Monaco needs unsafe-eval" folklore comes from the AMD
   ``loader.js``, which the ESM build does not use.)
3. No directive admits ``*``, ``http:``, ``https:`` or ``blob:``, and ``data:`` only
   in ``img-src``. A remote origin anywhere reopens exfiltration in a local-first
   app; ``blob:`` in particular is what a ``?worker&inline`` bundling change or a
   ``MonacoEnvironment.getWorkerUrl`` recipe would need, and both are forbidden.

Equality alone would be a worse test than no test: it teaches the next contributor
to paste the new string. Rules 2 and 3 survive a LEGITIMATE re-pin, so a policy that
is honestly re-reviewed still cannot smuggle those in.

WHAT THIS DOES NOT PROVE. It pins the AUTHORED string. Tauri parses
``app.security.csp`` into a directive map at runtime and injects its own nonces and
IPC sources; today everything lands in ``default-src``, and now that explicit
``script-src``/``connect-src`` exist, Tauri's additions retarget to those. This file
cannot see that augmentation — only a running webview can, which is what
``docs/TESTING-CHECKLIST.md`` §13c is for, on all three platforms rather than only
macOS.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_CONF = Path(__file__).resolve().parents[1] / "shell" / "src-tauri" / "tauri.conf.json"

# The reviewed literal. Changing this line is the decision; the two structural rules
# below are what a changed line still has to survive.
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

# Source expressions no directive may carry. `*` is matched as a whole token so that
# a legitimate `'self'` is never confused for a wildcard, and so `https://*.x` — which
# is caught by the `https:` rule anyway — is not the only reason this fires.
_FORBIDDEN_ANYWHERE = ("*", "http:", "https:", "blob:", "filesystem:", "ws:", "wss:")
# `data:` is admitted in exactly one directive: inline images (an avatar, a favicon,
# a mermaid data URI). Anywhere else it is a script/style/frame source.
_DATA_URI_ALLOWED_IN = frozenset({"img-src"})


def _csp() -> str:
    conf = json.loads(_CONF.read_text(encoding="utf-8"))
    csp = conf["app"]["security"]["csp"]
    assert isinstance(csp, str), "app.security.csp must be a single string policy"
    return csp


def _directives(csp: str) -> dict[str, list[str]]:
    """`{name: [source, …]}`, lowercased names, in authored order."""
    out: dict[str, list[str]] = {}
    for chunk in csp.split(";"):
        parts = chunk.split()
        if not parts:
            continue
        out[parts[0].lower()] = parts[1:]
    return out


def test_the_csp_is_exactly_the_reviewed_literal() -> None:
    """A silent widening. The policy is one string in one file, and this is the diff
    line that makes changing it a decision rather than a side effect of getting some
    library to render."""
    assert _csp() == EXPECTED_CSP, (
        "shell/src-tauri/tauri.conf.json's app.security.csp no longer matches the "
        "reviewed policy. If the change is deliberate, update EXPECTED_CSP in this "
        "file IN THE SAME COMMIT and say why in the PR — the two structural tests "
        "below still have to pass, and they are the ones that hold the line."
    )


def test_script_src_never_admits_unsafe_eval_or_unsafe_inline() -> None:
    """The plan's bright line, as a rule rather than a paragraph.

    Kills: widening `script-src` to make a rendering choice work. Monaco's ESM build
    needs neither; if a future editor/library does, the answer is the bespoke
    `<pre>` + highlight.js viewer, not this string.
    """
    directives = _directives(_csp())
    script = directives.get("script-src")
    assert script is not None, "script-src must be stated explicitly, never inherited"
    for banned in ("'unsafe-eval'", "'unsafe-inline'", "'wasm-unsafe-eval'"):
        assert banned not in script, (
            f"script-src admits {banned}. The widening this app accepted is style-src "
            f"ONLY: its whole residual risk is visual spoofing, and that is true only "
            f"while script execution stays at 'self'."
        )
    # `'strict-dynamic'` would let a single trusted script authorise arbitrary
    # further loads, which is the same hole by another name in an app that has no
    # nonce-managed script pipeline.
    assert "'strict-dynamic'" not in script, "script-src must not admit 'strict-dynamic'"


def test_no_directive_admits_a_remote_or_blob_source() -> None:
    """Kills: a CDN, a `blob:` worker, or a `*` creeping in under a re-pin.

    `blob:` is the specific one this app has a live temptation for — Vite's
    `?worker&inline` produces a blob URL, and every "Monaco needs blob:" answer
    online is the AMD `getWorkerUrl` recipe. The worker is bundled with a plain
    `?worker`, so `worker-src 'self'` holds.
    """
    for name, sources in _directives(_csp()).items():
        for source in sources:
            token = source.lower()
            assert token not in _FORBIDDEN_ANYWHERE, (
                f"{name} admits `{source}` — a remote, wildcard or blob source. "
                f"This app is local-first: nothing the window renders comes from "
                f"anywhere but the app itself."
            )
            # Catches `https://cdn.example`, `http://localhost:1234`, `blob:foo` —
            # anything carrying one of the schemes as a prefix rather than as the
            # whole token.
            assert not re.match(r"^(https?|blob|ws|wss|filesystem):", token), (
                f"{name} admits `{source}`, a remote/blob origin."
            )
            if token == "data:":
                assert name in _DATA_URI_ALLOWED_IN, (
                    f"{name} admits `data:`. A data URI is a script/style/frame the "
                    f"policy cannot see the contents of; it is allowed for inline "
                    f"images and nowhere else."
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


def test_style_src_is_the_only_directive_that_was_loosened() -> None:
    """Kills: `'unsafe-inline'` spreading to a second directive on the reasoning that
    it is 'already in the policy anyway'."""
    for name, sources in _directives(_csp()).items():
        if name == "style-src":
            continue
        assert "'unsafe-inline'" not in sources, (
            f"{name} admits 'unsafe-inline'. Exactly one directive was widened, for "
            f"one named reason (Monaco writes inline style attributes and nonces do "
            f"not apply to those)."
        )
