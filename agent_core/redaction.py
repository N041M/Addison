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

# Each rule is (name shown in the marker, compiled pattern). Order matters only
# for overlapping matches, and the patterns are written not to overlap.
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
    # it would look redacted while leaking everything. Non-greedy to the footer,
    # and DOTALL because the body is multi-line by definition.
    (
        "private key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # A private key that was truncated before its footer still must not pass.
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[^-]*")),
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
