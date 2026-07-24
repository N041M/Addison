// Add-a-model-server confirmation card (Phase-2 step 4) — mirrors the routine /
// widget proposal cards' calm inline look and gating.
//
// Addison drafts the proposal CORE-side (endpoint.proposeFromConversation) from a
// short, add-endpoint-shaped user utterance and holds it; nothing connects until
// the person presses "Add server". The provider is ALWAYS shown as "Your own
// server" — there is no model-authored label (contract F5). The full base URL is
// shown verbatim so the person sees exactly the address they are trusting.
//
// G1 — the hard requirement of this card: the API key goes STRAIGHT to the OS
// keychain via the Rust `storeProviderKey` command, and is NEVER part of an
// `endpoint.*` frame (no chat/core frame ever carries it). On "Add server" we
// store the key first (keychain), then confirmAdd with the base URL only — the
// key is never a parameter of confirmAddEndpoint.

import { useState } from "react";
import { ipc, storeProviderKey } from "../ipc/client";
import type { EndpointProposal } from "../types/ui";

// The custom "your own server" provider id — the ONE OpenAI-compatible endpoint
// slot, keyed under this id in the keychain and in provider_config.
const ENDPOINT_PROVIDER = "custom";

const ADD_FAILED = "Addison couldn't add that server. Try again in a moment.";

interface Props {
  proposal: EndpointProposal;
  onDismiss: () => void;
  /** Called after the server is successfully added, so callers can refresh. */
  onAdded?: () => void;
}

export function EndpointProposalCard({ proposal, onDismiss, onAdded }: Props) {
  const [key, setKey] = useState("");
  const [status, setStatus] = useState<"idle" | "working" | "error">("idle");
  const [error, setError] = useState("");
  const working = status === "working";

  async function add() {
    setStatus("working");
    setError("");
    try {
      const trimmed = key.trim();
      // G1: the key goes to the keychain via the Rust command, never a core frame.
      if (trimmed) await storeProviderKey(ENDPOINT_PROVIDER, trimmed);
      // The key is NOT a parameter here — only the base URL + the decision cross.
      const res = await ipc.confirmAddEndpoint(proposal.baseUrl, true);
      if (!res.ok) {
        setStatus("error");
        setError(res.error || ADD_FAILED);
        return;
      }
      setStatus("idle");
      onAdded?.();
      onDismiss();
    } catch {
      setStatus("error");
      setError(ADD_FAILED);
    }
  }

  function decline() {
    // Tell the core to drop the held draft; best-effort, then close the card.
    ipc.confirmAddEndpoint(proposal.baseUrl, false).catch(() => {});
    onDismiss();
  }

  return (
    <section
      aria-label="Add a model server?"
      className="animate-[fade-rise_160ms_ease-out] border-t border-line bg-surface px-6 py-4"
    >
      <h3 className="text-base font-semibold text-ink">Add a model server?</h3>

      <p className="mt-2 font-mono text-fact text-ink-soft break-all">{proposal.baseUrl}</p>
      <p className="mt-1 text-sm text-muted">Your own server</p>

      <p className="mt-2 text-sm text-muted">You asked Addison to add this address.</p>
      {proposal.isLocalOrLan && (
        <p className="mt-1 text-sm text-muted">
          This points to your own computer or a device on your network.
        </p>
      )}

      <label className="mt-4 block text-sm font-medium text-ink-soft">
        Paste the server's API key (it stays in your keychain)
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
            if (status === "error") setStatus("idle");
          }}
          disabled={working}
          className="mt-1 w-full max-w-md rounded border border-line bg-paper px-3 py-2 text-base text-ink placeholder:text-faint disabled:opacity-60"
        />
      </label>

      {status === "error" && <p className="mt-2 text-fine text-danger">{error}</p>}

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={() => void add()}
          disabled={working}
          className="rounded-pill bg-fern px-5 py-2 text-sm font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50"
        >
          {working ? "Adding…" : "Add server"}
        </button>
        <button
          type="button"
          onClick={decline}
          disabled={working}
          className="rounded-sm px-2 py-2 text-sm font-medium text-muted hover:text-ink-soft disabled:opacity-50"
        >
          Not now
        </button>
      </div>
    </section>
  );
}
