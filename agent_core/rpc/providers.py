"""provider.* handlers — the multi-provider connection surface (list / connect /
disconnect) plus the connection-status rollup stats.get renders (engineering-spec
§7, §4.1.1; owner decision 2026-07-18). Carries ONLY non-secret status/metadata —
never any key material (§8.3)."""

from __future__ import annotations

import math
import time
from collections import Counter
from urllib.parse import unquote, urlsplit

from agent_core.models_catalog import PROVIDER_IDS, provider_label
from agent_core.providers.ollama_provider import is_running
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _GENERIC_TURN_ERROR

_BAD_SCHEME = "Enter a web address that starts with http:// or https://."

# Plain, and it names the way out: the key box already exists and the key it
# takes goes straight to the keychain, which is the whole point of refusing here.
_CREDENTIAL_IN_URL = (
    "Enter the server address on its own, without a sign-in name, password or key "
    "in it. Put the key in the key box instead — Addison keeps that in your "
    "computer's keychain."
)

# Said when the address carries a "?" or a "#" part. Named plainly, because the
# person has to know WHICH part of what they typed to remove.
_EXTRA_IN_URL = (
    "Enter the server address on its own. Leave off everything from the first "
    "'?' or '#' onwards — Addison never needs it, and a key hidden in that part "
    "would be saved in plain text. Put the key in the key box instead — Addison "
    "keeps that in your computer's keychain."
)

# Said when part of the address itself looks like a key.
_KEY_IN_PATH = (
    "One part of that address looks like a key. Enter the server address on its "
    "own, without the key in it. Put the key in the key box instead — Addison "
    "keeps that in your computer's keychain."
)

# Openings that start a credential far more often than a route. Matched on a
# whole address segment, lowercased.
#
# "api-" is deliberately NOT here. It reads like a credential opening but it is
# also an ordinary route name — "/api-v1/chat", "/api-gateway/v1" — and refusing
# those told the person to remove a key their address doesn't contain, with no
# way forward. It bought nothing either: a real key beginning "api-" is long and
# mixed enough that the entropy rule below catches it anyway. A prefix earns its
# place here only if it costs no legitimate route.
_KEY_PREFIXES = ("sk-", "sk_", "pk-", "pk_", "ghp_", "gsk_", "xai-", "bearer")

# A segment this long, mixing letters and digits and this unpredictable, is a
# key rather than a route name. Tuned so ordinary segments pass: "v1",
# "chat-completions" (no digit) and "2024-05-01-preview" (entropy ~3.4) are all
# below the bar, while base64/hex-ish key material sits at 4 bits per character
# and above.
_KEYISH_MIN_LENGTH = 16
_KEYISH_MIN_ENTROPY = 3.5


def _entropy_per_character(text: str) -> float:
    """Shannon entropy of ``text`` in bits per character — how unpredictable it
    is. Words repeat letters and score low; random key material scores high."""
    total = len(text)
    if total == 0:
        return 0.0
    return -sum(
        (count / total) * math.log2(count / total) for count in Counter(text).values()
    )


def _segment_looks_like_a_key(segment: str) -> bool:
    """True when one slash-separated piece of the address looks like credential
    material rather than a route name."""
    lowered = segment.lower()
    if lowered.startswith(_KEY_PREFIXES):
        return True
    if len(segment) < _KEYISH_MIN_LENGTH:
        return False
    # Both classes present is what separates a key from a long English-ish word;
    # the entropy bar then separates it from a date or a version string.
    if not (any(c.isdigit() for c in segment) and any(c.isalpha() for c in segment)):
        return False
    return _entropy_per_character(segment) >= _KEYISH_MIN_ENTROPY


def _base_url_problem(url) -> str | None:
    """Why this base URL cannot be used, as one plain sentence — or ``None`` when
    it is fine.

    Two things are checked, for two different reasons.

    SCHEME. Accepted only as an ``http://`` or ``https://`` URL with a host after
    the scheme. ``http://`` is deliberately permitted — a custom server is the ONE
    allowed plain-HTTP case (localhost/LAN model hosts). No other scheme
    (``file:``, ``ftp:``, …) is ever accepted.

    CREDENTIALS (GLOBAL FLOOR G1). A base URL is stored in ``provider_config`` and
    ``provider_config`` is captured by every G3 snapshot — so it lands in
    ``config_snapshots.state_blob`` (plain text in SQLite), in the plaintext
    sidecar file beside the database, and in any permanent anchor, forever. A key
    smuggled into a URL is therefore refused HERE, at the moment the person types
    it, rather than stripped later on capture: stripping on capture would make a
    restore write back a *different* address than the one that was configured and
    silently break their server. Refusing at the door is the only version of this
    that keeps restore honest.

    The refusal is STRUCTURAL, not a list of forbidden parameter names. An earlier
    version blocked eight credential-ish names and was beaten by ``?sk=`` and
    ``?t=`` — which is what a blocklist always does, because the attacker picks the
    name. What a provider base URL legitimately needs is bounded and small:
    ``scheme://host[:port][/path]``. So:
      * userinfo (``https://user:sk-live-…@host/v1``) — refused.
      * ANY query string or fragment — refused outright, whatever it contains.
        Addison appends its own paths to this address; it has no use for either
        part, so there is nothing to weigh against closing the hole for good.
      * a key-shaped PATH segment (``…/v1/sk-live-…``) — refused. A blanket rule
        is not available here because ``/v1`` is legitimate, so this one is a
        judgement: known key openings plus long, high-entropy segments. It is
        biased toward refusing, because a wrong refusal costs one clear sentence
        while a wrong acceptance writes the person's key into two plain-text files
        and every snapshot taken from then on.
    """
    if not isinstance(url, str) or not url.strip():
        return _BAD_SCHEME
    scheme_ok = any(
        url.startswith(scheme) and len(url) > len(scheme) for scheme in ("http://", "https://")
    )
    if not scheme_ok:
        return _BAD_SCHEME
    try:
        parts = urlsplit(url)
    except ValueError:
        # A malformed address (a bad bracketed host, say) is simply not usable,
        # and the person's next step is the same as for a wrong scheme.
        return _BAD_SCHEME
    if not parts.hostname:
        return _BAD_SCHEME
    # "@" anywhere in the authority is userinfo, whether or not it parsed into a
    # username — checked on the raw authority so nothing slips past on a parse
    # quirk.
    if "@" in parts.netloc or parts.username or parts.password:
        return _CREDENTIAL_IN_URL
    # Read off the RAW text after the scheme rather than the parsed fields, so a
    # "?" or "#" can never reach the store on a parsing quirk.
    after_scheme = url.split("://", 1)[1]
    if "?" in after_scheme or "#" in after_scheme:
        return _EXTRA_IN_URL
    # Percent-decoded first: %73k-live-… is the same key wearing a disguise.
    for segment in unquote(parts.path).split("/"):
        if segment and _segment_looks_like_a_key(segment):
            return _KEY_IN_PATH
    return None


def _valid_http_url(url) -> bool:
    """True when this base URL is usable — see ``_base_url_problem`` for what that
    means and why a URL carrying a credential is not."""
    return _base_url_problem(url) is None


class ProvidersMixin(ServerContext):
    def _connections(self, latency: list[dict]) -> list[dict]:
        """Ollama (probed live) + each connected cloud provider. Status/detail are
        plain strings; there is NEVER any key material in this payload (§8.3)."""
        conns: list[dict] = []
        try:
            ollama_up = is_running(self._ollama_base_url, self._ollama_client)
        except Exception:
            ollama_up = False
        conns.append(
            {
                "id": "ollama",
                "label": "Ollama · this computer",
                "status": "running" if ollama_up else "idle",
                "detail": "running" if ollama_up else "not running",
            }
        )
        latency_by_provider = {row["provider"]: row["ms"] for row in latency}
        stored = {c["provider_id"]: c for c in self.store.list_provider_configs()}
        for provider_id in PROVIDER_IDS:
            cfg = stored.get(provider_id)
            if cfg is not None:
                connected = cfg["connected"]
            else:
                connected = provider_id != "custom" and self._provider_key_present(provider_id)
            if not connected:
                continue
            ms = latency_by_provider.get(provider_id)
            label = provider_label(provider_id)
            conns.append(
                {
                    "id": provider_id,
                    "label": f"{label} API" if provider_id != "custom" else label,
                    "status": "reachable",
                    "detail": f"{ms} ms" if ms is not None else "connected",
                }
            )
        return conns

    # --- provider connections (multi-provider, §4.1.1) --------------------
    def _provider_key_present(self, provider_id: str) -> bool:
        probe = self._provider_key_probe
        if probe is None:
            return False
        try:
            return bool(probe(provider_id))
        except Exception:
            return False

    def _provider_list(self) -> dict:
        """provider.list -> {providers: [...]}. Carries ONLY non-secret status and
        metadata — NEVER any key material (invariant §8.3): id, plain label, whether
        it is connected, and (when known) the added date, custom base URL, and the
        last connect-check result.

        ``connected`` trusts a stored connection row exactly; only when there is NO
        row does it fall back to 'a key is already in the keychain' — that fallback
        exists so a legacy/migrated Anthropic key shows connected without a re-connect."""
        self._ensure_built()
        stored = {c["provider_id"]: c for c in self.store.list_provider_configs()}
        rows: list[dict] = []
        for provider_id in PROVIDER_IDS:
            cfg = stored.get(provider_id)
            if cfg is not None:
                connected = cfg["connected"]
            else:
                connected = provider_id != "custom" and self._provider_key_present(provider_id)
            row: dict = {
                "id": provider_id,
                "label": provider_label(provider_id),
                "connected": connected,
            }
            if cfg is not None:
                if cfg["added_at"] is not None:
                    row["addedAt"] = cfg["added_at"]
                if provider_id == "custom" and cfg["base_url"]:
                    row["baseUrl"] = cfg["base_url"]
                if cfg["last_check_ok"] is not None:
                    row["lastCheckOk"] = cfg["last_check_ok"]
            rows.append(row)
        return {"providers": rows}

    def _provider_connect(self, params: dict) -> dict:
        """provider.connect {provider, baseUrl?} -> {ok, error?}. The key was already
        stored by the Rust command; here the core pulls it from the keychain, makes ONE
        tiny validating request, and — on success — records metadata and folds the
        provider's models into the picker union. On failure it does NOT mark the provider
        connected (the card offers Remove to clear the stored key)."""
        self._ensure_built()
        provider_id = params.get("provider")
        base_url = (params.get("baseUrl") or "").strip() or None
        if provider_id not in PROVIDER_IDS:
            return {"ok": False, "error": "That provider isn't available."}
        # Checked for EVERY provider, not just custom: whatever arrives here is
        # written to provider_config, and provider_config is captured by every
        # snapshot (G1 — see _base_url_problem). Refused before the connect is
        # attempted and before the H2 snapshot below, so a rejected address never
        # reaches the store or a payload.
        if base_url is not None:
            problem = _base_url_problem(base_url)
            if problem is not None:
                return {"ok": False, "error": problem}
        elif provider_id == "custom":
            return {"ok": False, "error": _BAD_SCHEME}
        if self._connect_provider is None:
            return {"ok": False, "error": "Connecting a provider needs the desktop app."}
        # Hook H2 (G3): one restore point per connect ATTEMPT, before it — every
        # branch below writes provider_config, success and failure alike, so
        # snapshotting per branch would only churn a row on each offline retry.
        # Recoverable if the capture fails (the person can reconnect), so this
        # proceeds with the sticky warning.
        self._snapshot_auto("provider_connect")
        try:
            models = self._connect_provider(provider_id, base_url)
        except RuntimeError as exc:
            # Provider errors already carry a plain, user-ready sentence. Record the
            # failed check WITHOUT marking connected, so provider.list shows it off.
            self.store.upsert_provider_config(
                provider_id, connected=False, base_url=base_url, last_check_ok=False
            )
            return {"ok": False, "error": str(exc)}
        except Exception:
            self.store.upsert_provider_config(
                provider_id, connected=False, base_url=base_url, last_check_ok=False
            )
            return {"ok": False, "error": _GENERIC_TURN_ERROR}
        self.store.upsert_provider_config(
            provider_id,
            connected=True,
            added_at=int(time.time()),
            base_url=base_url,
            last_check_ok=True,
        )
        self._set_provider_models(provider_id, models)
        return {"ok": True}

    def _provider_disconnect(self, params: dict) -> dict:
        """provider.disconnect {provider} -> {ok}. Forget the connection metadata and
        drop that provider's models from the picker union and the router pool. The key
        itself is removed separately by the Rust keychain command (the webview calls it)."""
        self._ensure_built()
        provider_id = params.get("provider")
        if provider_id not in PROVIDER_IDS:
            return {"ok": False, "error": "That provider isn't available."}
        # Hook H3 (G3): disconnecting also unregisters every one of that provider's
        # router models below, so it is the widest-reaching of the recoverable
        # hooks. No snapshot for a no-op disconnect — a provider that was never
        # connected has nothing to roll back to.
        if self.store.get_provider_config(provider_id) is not None:
            self._snapshot_auto("provider_disconnect")
        self.store.delete_provider_config(provider_id)
        for model in [m for m in self._cloud_catalog if m.provider == provider_id]:
            self.model_router.unregister_primary_model(model.id)
        self._cloud_catalog = [m for m in self._cloud_catalog if m.provider != provider_id]
        return {"ok": True}
