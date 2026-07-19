// "Run a model on this computer" — the local-model setup flow (spec §4.1.2,
// design-doc §7.3.2; Fern direction: design-brief-fern README §4). Lives inside
// the in-window Settings page, as the inner content of the "Run a model on this
// computer" card (App/SettingsPage provides the card shell + heading). Distinct,
// explicit, opt-in: NOT enabled by default and never shown during onboarding.
//
// The user picks one of a small curated list; that hands the Ollama model tag to
// the core via `ipc.startLocalSetup(modelName)`. Live progress streams back on
// `model.localSetupProgress` and renders inline here (a plain stage label + a 5px
// fern progress bar on `hair` — no spinner theatrics). On success the roles
// refresh and the model shows up in the chat's model selector.

import { useState } from "react";
import type { LocalSetupState, RoleOption } from "../types/ui";

// ---------------------------------------------------------------------------
// The curated choices. ONE obvious constant so the core team can align exact
// tags / sizes / memory floors later (these are placeholders pending the core's
// hardware-gating list). Copy tone follows design-doc §7.3.2: a plain name, an
// honest "X GB download · needs Y GB memory" line — no parameter counts, no
// quantization jargon. `id` is the Ollama tag the core pulls; it is used for the
// call, not shown as the primary label.
// ---------------------------------------------------------------------------
export interface LocalModelChoice {
  id: string;
  name: string;
  metaLabel: string;
  note?: string;
}

export const LOCAL_MODEL_CHOICES: LocalModelChoice[] = [
  {
    id: "llama3.2:3b",
    name: "Light and quick",
    metaLabel: "2 GB download · needs 8 GB memory",
    note: "Fast, good for everyday questions. Basic tool support.",
  },
  {
    id: "llama3.1:8b",
    name: "Balanced",
    metaLabel: "4.7 GB download · needs 16 GB memory",
    note: "A capable all-rounder for most everyday tasks.",
  },
  {
    id: "qwen2.5:14b",
    name: "Most capable",
    metaLabel: "9 GB download · needs 32 GB memory",
    note: "Slower, but handles longer, more involved tasks.",
  },
];

interface Props {
  connected: boolean;
  roles: RoleOption[];
  setup: LocalSetupState | null;
  onStartSetup: (modelId: string) => void;
}

export function LocalModelSetup({ connected, roles, setup, onStartSetup }: Props) {
  const [ollamaOpen, setOllamaOpen] = useState(false);

  // Models already on this computer come back from the core inside the local
  // role, so "installed" survives even after a fresh setup's transient state
  // clears. Match case-insensitively on the Ollama tag.
  const installed = new Set(
    (roles.find((r) => r.role === "local")?.models ?? []).map((m) => m.id.toLowerCase()),
  );

  const anyRunning = setup?.status === "running";

  return (
    <div>
      <p className="text-meta text-muted">
        Nothing you say leaves your machine. One-time download, runs through Ollama —{" "}
        <button
          type="button"
          onClick={() => setOllamaOpen((v) => !v)}
          aria-expanded={ollamaOpen}
          className="font-medium text-fern-deep underline underline-offset-2 hover:text-fern"
        >
          what's Ollama?
        </button>
      </p>
      {ollamaOpen && (
        <p className="mt-2 border-l-2 border-line pl-3 text-hint text-ink-soft">
          Ollama is a small, free program that downloads and runs models on your
          own computer. Addison uses it behind the scenes — if it isn't installed
          or running, Addison will tell you plainly and can't set up a local model
          until it is.
        </p>
      )}

      {!connected && (
        <p className="mt-2 text-hint text-muted">
          Setting up a local model needs the desktop app. You can look over the
          choices here, but downloading starts once Addison is connected.
        </p>
      )}

      <ul className="mt-3.5 flex flex-col gap-2">
        {LOCAL_MODEL_CHOICES.map((choice) => {
          const isInstalled = installed.has(choice.id.toLowerCase());
          const isThis = setup?.modelId === choice.id;
          const running = isThis && setup?.status === "running";
          const done = (isThis && setup?.status === "done") || isInstalled;
          const errored = isThis && setup?.status === "error";

          return (
            <li key={choice.id} className="rounded border border-line bg-paper px-[14px] py-2.5">
              <div className="flex items-center justify-between gap-2.5">
                <div className="min-w-0">
                  <p className="text-action font-semibold text-ink">{choice.name}</p>
                  <p className="mt-px text-fine text-faint">{choice.metaLabel}</p>
                </div>

                <div className="shrink-0 text-right">
                  {done ? (
                    <span className="text-xs font-medium text-fern-deep">
                      ✓ On this computer
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onStartSetup(choice.id)}
                      disabled={!connected || anyRunning}
                      className="rounded-sm border border-line bg-transparent px-[14px] py-1.5 text-xs font-semibold text-fern-deep hover:border-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {running ? "Setting up…" : "Set up"}
                    </button>
                  )}
                </div>
              </div>

              {/* Live progress — plain stage line + a 5px fern bar on `hair`. */}
              {running && (
                <div className="mt-2.5">
                  <p className="text-hint text-ink-soft">
                    {setup?.message ?? setup?.stage ?? "Getting ready…"}
                  </p>
                  {typeof setup?.percent === "number" && (
                    <div className="mt-2 flex items-center gap-2">
                      <div
                        className="h-[5px] flex-1 overflow-hidden rounded-pill bg-hair"
                        role="progressbar"
                        aria-valuenow={Math.round(setup.percent)}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label={`${choice.name} download progress`}
                      >
                        <div
                          className="h-full rounded-pill bg-fern"
                          style={{ width: `${clampPercent(setup.percent)}%` }}
                        />
                      </div>
                      <span className="w-9 text-right text-fact tabular-nums text-muted">
                        {Math.round(clampPercent(setup.percent))}%
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* Just-finished confirmation (installed rows already read as done). */}
              {isThis && setup?.status === "done" && !isInstalled && (
                <p className="mt-2.5 text-hint text-fern-deep">
                  Ready to use. Pick "On this computer" beside the message box to use it.
                </p>
              )}

              {/* Inline, plain-language error — includes the core's own message
                  (e.g. Ollama isn't running, or the machine is too small). */}
              {errored && (
                <div className="mt-2.5">
                  <p className="text-hint text-danger">
                    {setup?.error ?? "Setting this up didn't work. Please try again."}
                  </p>
                  {mentionsOllama(setup?.error) && (
                    <p className="mt-1 text-hint text-muted">
                      Addison needs Ollama installed and running first — see
                      "what's Ollama?" above.
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => onStartSetup(choice.id)}
                    disabled={!connected || anyRunning}
                    className="mt-2 text-hint font-medium text-fern-deep hover:text-fern disabled:opacity-50"
                  >
                    Try again
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function clampPercent(p: number): number {
  if (Number.isNaN(p)) return 0;
  return Math.min(100, Math.max(0, p));
}

function mentionsOllama(message?: string): boolean {
  return typeof message === "string" && /ollama/i.test(message);
}
