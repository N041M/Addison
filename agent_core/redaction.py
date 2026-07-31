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
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA|AROA|AIDA)[A-Z0-9]{16}\b")),
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
            return _marker(_kind)

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
