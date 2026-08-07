"""Secret redaction for text on its way to a model — step 5.5, item 4.

WHY THIS EXISTS. G1 guards *Addison's own* keys with four layers: keychain-only
storage, no read-back command, per-call fetch, and a webview that never sees
them. Meanwhile the OPEN harness will `cat ~/.ssh/id_rsa` into an Anthropic
request for the asking. The denylist (step 5.5 item 3) refuses the obvious ask,
and the seatbelt (item 2) stops the write half — but a command that *legitimately*
prints a secret (a build script echoing an env var, a `git remote -v` with a
token in the URL, a stack trace carrying a bearer token) sails through both.
This module is the last thing between that output and someone else's server.

WHAT IT IS NOT. It is a pattern matcher over text, so it is a **backstop, not a
boundary** — the same honesty the denylist carries. A secret in a format nobody
has enumerated passes untouched. It reduces exposure; it does not eliminate it,
and no doc may describe it as elimination.

TWO DECISIONS, MADE DELIBERATELY (the plan's owner decision #2):

  * **Redact toward the MODEL, never into the STORE.** The transcript is the
    user's own record and scrubbing it destroys the evidence that a leak
    happened. So this runs at the orchestrator's send boundary
    (``redacted_for_model``), producing a throwaway view; ``conversation.messages``
    and the SQLite ``messages`` table keep the real bytes. Every provider —
    including one added later — is covered by that single site, which is why the
    seam is the orchestrator and not each provider's ``_translate_history``.

    **TWO NARROWINGS OF THAT RULE, both deliberate.** (1) ``run_command`` scrubs
    its output *before* truncating it, so a TRUNCATED result carries markers into
    the transcript. Redacting after the cut would slice a credential below the
    ``{16,}`` minimum and let the remainder through as an unmatched partial — the
    cut can defeat the boundary, so the boundary moves ahead of the cut. Output
    under the cap is untouched and keeps its real bytes. (2) ``tool_audit.detail``
    is redacted on the way INTO SQLite (``store.insert_tool_audit``), because
    ``run_command``'s detail is the command text and that row is durable, excluded
    from snapshots and never pruned — G1 outranks the evidence argument for a
    column nobody reads as a transcript. Neither narrowing touches
    ``conversation.messages``.
  * **Never redact silently.** A removed secret leaves a visible marker naming
    its kind, so the model knows something was there (and does not "helpfully"
    re-run the command to get it), and the person can see it happened.

Stdlib ``re`` only, no dependencies, no I/O — this module is importable from
anywhere without touching the module-boundary rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

#: The longest body a private-key BLOCK rule will span. A 16384-bit RSA key — the
#: largest anything in ordinary use generates — is under 17 KB once PEM-encoded, so
#: this is far past any real key and still small enough that scanning one is
#: bounded work. It is a ceiling on COST, not a judgement about the key: a body
#: longer than this falls to the truncated-key rule below, which still refuses to
#: let the bytes through.
_MAX_PEM_BODY_CHARS = 32768

# Each rule is (name shown in the marker, compiled pattern). Order matters only
# for overlapping matches, and the patterns are written not to overlap.
#
# EVERY PATTERN HERE IS SCANNED OVER TEXT A STRANGER CHOSE, so the cost of a rule
# on its worst input is part of the rule. `redact` runs on the worker thread — for
# an MCP answer, after that call's deadline has already passed — so a rule that
# backtracks quadratically is everything behind it waiting. Each one below either
# has no way to backtrack or says in its own comment why it cannot.
#
# ANCHORING, and why it is not optional: an unanchored `[A-Za-z0-9]{20,}` would
# eat base64 blobs, git SHAs, minified JS and UUIDs out of ordinary tool output,
# and a redactor that mangles innocent text is one people switch off. Every rule
# below keys off a vendor-assigned prefix or a structural marker, so a false
# positive needs output that genuinely looks like a credential.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Anthropic. `sk-ant-` then the vendor's own alphabet.
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    # OpenAI and the many `sk-`-prefixed compatibles (project/service keys
    # included). Deliberately after the Anthropic rule so the longer prefix wins.
    ("API key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}")),
    # GitHub: personal, OAuth, user-to-server, server-to-server, refresh.
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    # AWS access key id (AKIA/ASIA/AROA/AIDA + 16 uppercase alphanumerics).
    # NOTE THIS IS THE NON-SECRET HALF. An access key ID is an identifier, not a
    # credential; the rule below is the one that matters, and for a long time only
    # this one existed — with a test fixture named `AWS_SECRET={AKIA…}`, which
    # taught the wrong thing to everyone who read it.
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA|AROA|AIDA)[A-Z0-9]{16}\b")),
    # AWS SECRET access key — the 40-character half that actually signs requests.
    #
    # ANCHORED ON ITS NAME, because it has to be: the value is 40 characters of
    # base64 alphabet with no vendor prefix, so an unanchored rule would be
    # indistinguishable from a git SHA (40 hex IS a subset of that alphabet) and
    # would redact half of ordinary `git log` output. `test_ordinary_output_is_
    # left_alone` pins exactly that. Anchoring on the assignment is the whole
    # reason `keep` exists: the variable NAME survives, so the output still reads
    # as an assignment and the model does not re-run the command to "find" it.
    #
    # The cost of anchoring, stated plainly: a bare 40-character secret with no
    # `aws_secret_access_key=` in front of it passes. That is the backstop-not-a-
    # boundary rule doing what it says, not an oversight to be fixed by loosening
    # this into a git-SHA shredder.
    (
        "AWS secret key",
        re.compile(
            r"(?P<keep>aws[_-]?secret[_-]?access[_-]?key\s*[=:]\s*[\"']?)"
            r"[A-Za-z0-9/+=]{40}",
            re.IGNORECASE,
        ),
    ),
    # Stripe and the `_live_`/`_test_` family. UNDERSCORE, which is why the
    # hyphenated `sk-` rule above never caught these.
    ("Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    # GitLab personal/project/group access tokens.
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}")),
    # A JSON Web Token, matched STRUCTURALLY rather than by prefix: three
    # base64url segments, the first beginning `eyJ` because that is what `{"`
    # encodes to. Bearer tokens are usually JWTs but arrive without the header
    # when a command prints one on its own.
    #
    # An UNSIGNED token (`alg: none`, empty third segment) is not matched. It is
    # also not a credential anybody can authenticate with, so the gap is the
    # right way round.
    (
        "JSON web token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    # Slack, all token classes.
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    # Google API keys.
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    # An Authorization header's value, however the header was spelled.
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    # A private key BLOCK, not just its header: matching only the header would
    # leave the key bytes in place, which is worse than not matching at all —
    # it would look redacted while leaking everything. So the rule spans header to
    # footer, and the body is multi-line by definition.
    #
    # THE BODY IS SPELLED OUT RATHER THAN `.*?`, AND THAT IS THE WHOLE POINT OF
    # THESE LINES. A lazy `.*?` under DOTALL has to scan to the end of the input
    # from EVERY header that has no footer after it, which is quadratic in the
    # number of headers — and headers are three dozen characters a server can
    # repeat. 128 KB of them took 2.42 seconds *(measured 2026-08-07 · one
    # ``redact`` call over 127980 characters of repeated BEGIN markers, python
    # 3.12 in agent_core/.venv on the owner's machine)*.
    #
    # A PEM body cannot contain `-----`: that run of five dashes is what opens and
    # closes an armour line, and base64 has no dash in its alphabet. So the body is
    # "anything that does not start a run of five dashes", which stops the scan
    # dead at the next marker instead of at the end of the input. A hyphen INSIDE a
    # line is ordinary body text either way, which is what keeps the `Proc-Type:`
    # and `DEK-Info:` armour headers of an encrypted key inside the match.
    #
    # POSSESSIVE, because a shorter body can never rescue a failed footer — the
    # footer opens with the five dashes the body just refused to cross — so the
    # retries a greedy count would make are all provably useless, and making them
    # is how the cost comes back.
    #
    # THE FOOTER COUNTS FIVE-OR-MORE DASHES, AND THAT IS WHAT MAKES THIS RULE
    # SURVIVE A DIFF. `git diff` prefixes a deleted line with `-`, so a key removed
    # from a file arrives with SIX dashes on its armour lines — and deleting a
    # committed key, then reading the diff, is the likeliest way one ever reaches
    # this function. The body stops dead at that six-dash run, so a footer
    # demanding exactly five could not start where the body ended: the whole match
    # failed and dropped through to the truncated rule below, which emitted a
    # redaction marker followed by the entire key — the "looks redacted while
    # leaking everything" outcome the paragraph above exists to forbid.
    #
    # The HEADER stays a fixed literal, because it is the part tried at every
    # position in the text and must fail in constant time. A run-counting header
    # rescans the whole run from each offset inside it, which turns 128 KB of bare
    # dashes — three keystrokes for a hostile server — into five seconds. The
    # footer is only ever reached after a header has already matched, so counting
    # there costs nothing. A diff's leading dash simply sits outside the match.
    (
        "private key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
            rf"(?:[^-]|-(?!----)){{0,{_MAX_PEM_BODY_CHARS}}}+"
            r"-{5,}+END [A-Z ]*PRIVATE KEY-{5,}+"
        ),
    ),
    # A private key that was truncated before its footer still must not pass — and
    # since the rule above refuses to cross a `-----`, this is also what catches a
    # key whose body ran past :data:`_MAX_PEM_BODY_CHARS`.
    #
    # It spans the same body the block rule does, rather than `[^-]*`, because a
    # single `-` is ordinary inside a key that has no footer: it opens every line
    # of a diff, and it sits inside the `Proc-Type:`/`DEK-Info:` armour of an
    # encrypted one. Stopping at the first hyphen left the marker sitting directly
    # in front of the bytes it claimed to have removed. Possessive and last in the
    # rule list: it is greedy with nothing after it to fail against, so it cannot
    # backtrack, and it stops at the next armour line or at the end of the text.
    (
        "private key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:[^-]|-(?!----))*+"),
    ),
)


def _marker(kind: str) -> str:
    return f"[redacted: {kind}]"


@dataclass(frozen=True)
class RedactionResult:
    text: str
    # What was removed, in order, for the audit trail. Kinds only — never a
    # value, never a length, never a prefix (the trace's rule, for the same
    # reason: a length narrows a brute force and a prefix names the vendor).
    kinds: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.kinds)


def redact(text: str) -> RedactionResult:
    """Replace anything that looks like a credential with a naming marker.

    Idempotent by construction: a marker contains no character sequence any rule
    matches, so redacting twice is redacting once — which matters because the
    send boundary re-walks the whole history every round."""
    if not text:
        return RedactionResult(text=text, kinds=())
    found: list[str] = []
    out = text
    for kind, pattern in _RULES:
        def _sub(match: re.Match[str], _kind: str = kind) -> str:
            found.append(_kind)
            # A rule may declare a `keep` group for text that must survive — the
            # variable name in `aws_secret_access_key=…`, never any part of the
            # secret. It exists for rules anchored on context rather than on a
            # vendor prefix, where swallowing the anchor would delete the sentence
            # around the value and leave output that reads like corruption.
            keep = match.groupdict().get("keep") if match.groupdict() else None
            return f"{keep}{_marker(_kind)}" if keep else _marker(_kind)

        out = pattern.sub(_sub, out)
    return RedactionResult(text=out, kinds=tuple(found))


def redacted_for_model(messages: list) -> tuple[list, tuple[str, ...]]:
    """A view of ``messages`` safe to put on the wire, plus what was removed.

    THE STORE IS NEVER TOUCHED. Returns new ``Message`` objects (via
    ``dataclasses.replace``) and leaves the originals — the ones the transcript
    and SQLite hold — exactly as they were. When nothing matches, the ORIGINAL
    list object is returned unchanged, so the overwhelmingly common case costs
    one scan and allocates nothing.

    Only ``content`` is walked. Tool-call *arguments* are the model's own words
    echoed back, not tool output, and rewriting them would corrupt the
    ``tool_use``/``tool_result`` pairing that providers validate.
    """
    kinds: list[str] = []
    rewritten: list = []
    dirty = False
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            result = redact(content)
            if result.changed:
                kinds.extend(result.kinds)
                rewritten.append(replace(message, content=result.text))
                dirty = True
                continue
        rewritten.append(message)
    if not dirty:
        return messages, ()
    return rewritten, tuple(kinds)
