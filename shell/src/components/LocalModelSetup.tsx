// "Run a model on this computer" — the local-model setup flow (spec §4.1.2,
// design-doc §7.3.2). Lives inside the Settings drawer, under "Where Addison
// thinks". Distinct, explicit, opt-in: NOT enabled by default and never shown
// during onboarding.
//
// The user picks one of a small curated list; that hands the Ollama model tag
// to the core via `ipc.startLocalSetup(modelName)`. Live progress streams back
// on `model.localSetupProgress` and renders inline here (a plain stage label +
// a simple linear percent — no spinner theatrics, no shimmer). On success the
// roles refresh and the model shows up in the chat's model selector.
//
// Visual direction is binding (CLAUDE.md): dark terminal-adjacent surfaces, sharp
// corners, one restrained steel-blue accent, plain language for readers who are 54
// and 68.

import { useState } from "react";
import type { LocalSetupState, RoleOption } from "../types/ui";

// ---------------------------------------------------------------------------
// The curated choices. ONE obvious constant so the core team can align exact
// tags / sizes / memory floors later (these are placeholders pending the core's
// hardware-gating list). Copy tone follows design-doc §7.3.2: a plain name, an
// honest download size, and a plain "needs at least X memory" line — no
// parameter counts, no quantization jargon. `id` is the Ollama tag the core
// pulls; it is used for the call, not shown as the primary label.
// ---------------------------------------------------------------------------
export interface LocalModelChoice {
  id: string;
  name: string;
  downloadLabel: string;
  memoryLabel: string;
  note?: string;
}

export const LOCAL_MODEL_CHOICES: LocalModelChoice[] = [
  {
    id: "llama3.2:3b",
    name: "Light and quick",
    downloadLabel: "About 2 GB to download",
    memoryLabel: "Needs a computer with at least 8 GB of memory",
    note: "Fast, good for everyday questions. Basic tool support.",
  },
  {
    id: "llama3.1:8b",
    name: "Balanced",
    downloadLabel: "About 4.7 GB to download",
    memoryLabel: "Needs a computer with at least 16 GB of memory",
    note: "A capable all-rounder for most everyday tasks.",
  },
  {
    id: "qwen2.5:14b",
    name: "Most capable",
    downloadLabel: "About 9 GB to download",
    memoryLabel: "Needs a computer with at least 32 GB of memory",
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
    <section>
      <h3 className="text-base font-semibold text-ink">Run a model on this computer</h3>
      <p className="mt-1 text-sm text-muted">
        Addison can also use a model that runs entirely on this computer —
        nothing you say leaves your machine. It needs a one-time download.
      </p>

      {/* Requires Ollama — honest up front, with a plain explainer on request. */}
      <p className="mt-2 text-sm text-muted">
        This runs through Ollama, a free helper program.{" "}
        <button
          type="button"
          onClick={() => setOllamaOpen((v) => !v)}
          aria-expanded={ollamaOpen}
          className="font-medium text-accent-dark underline underline-offset-2 hover:text-accent"
        >
          What's Ollama?
        </button>
      </p>
      {ollamaOpen && (
        <p className="mt-2 border-l-2 border-line pl-3 text-sm text-ink-soft">
          Ollama is a small, free program that downloads and runs models on your
          own computer. Addison uses it behind the scenes — if it isn't installed
          or running, Addison will tell you plainly and can't set up a local
          model until it is.
        </p>
      )}

      {!connected && (
        <p className="mt-3 text-sm text-muted">
          Setting up a local model needs the desktop app. You can look over the
          choices here, but downloading starts once Addison is connected.
        </p>
      )}

      <ul className="mt-4 flex flex-col gap-3">
        {LOCAL_MODEL_CHOICES.map((choice) => {
          const isInstalled = installed.has(choice.id.toLowerCase());
          const isThis = setup?.modelId === choice.id;
          const running = isThis && setup?.status === "running";
          const done = (isThis && setup?.status === "done") || isInstalled;
          const errored = isThis && setup?.status === "error";

          return (
            <li key={choice.id} className="border border-line bg-surface px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-base font-medium text-ink">{choice.name}</p>
                  <p className="mt-0.5 text-sm text-muted">{choice.downloadLabel}</p>
                  <p className="text-sm text-muted">{choice.memoryLabel}</p>
                  {choice.note && (
                    <p className="mt-1 text-sm text-ink-soft">{choice.note}</p>
                  )}
                </div>

                <div className="shrink-0 text-right">
                  {done ? (
                    <span className="inline-flex items-center gap-1 text-sm font-medium text-accent-dark">
                      <span aria-hidden="true">✓</span> On this computer
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onStartSetup(choice.id)}
                      disabled={!connected || anyRunning}
                      className="bg-accent px-4 py-2 text-sm font-semibold text-accent-fg hover:bg-accent-dark disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {running ? "Setting up…" : "Download and set up"}
                    </button>
                  )}
                </div>
              </div>

              {/* Live progress — plain stage line + simple linear percent. */}
              {running && (
                <div className="mt-3">
                  <p className="text-sm text-ink-soft">
                    {setup?.message ?? setup?.stage ?? "Getting ready…"}
                  </p>
                  {typeof setup?.percent === "number" && (
                    <div className="mt-2 flex items-center gap-2">
                      <div
                        className="h-2 flex-1 border border-line bg-paper"
                        role="progressbar"
                        aria-valuenow={Math.round(setup.percent)}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label={`${choice.name} download progress`}
                      >
                        <div
                          className="h-full bg-accent"
                          style={{ width: `${clampPercent(setup.percent)}%` }}
                        />
                      </div>
                      <span className="w-10 text-right text-sm tabular-nums text-muted">
                        {Math.round(clampPercent(setup.percent))}%
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* Just-finished confirmation (installed rows already read as done). */}
              {isThis && setup?.status === "done" && !isInstalled && (
                <p className="mt-3 text-sm text-accent-dark">
                  Ready to use. Pick "On this computer" beside the message box to
                  use it.
                </p>
              )}

              {/* Inline, plain-language error — includes the core's own message
                  (e.g. Ollama isn't running, or the machine is too small). */}
              {errored && (
                <div className="mt-3">
                  <p className="text-sm text-danger">
                    {setup?.error ?? "Setting this up didn't work. Please try again."}
                  </p>
                  {mentionsOllama(setup?.error) && (
                    <p className="mt-1 text-sm text-muted">
                      Addison needs Ollama installed and running first — see
                      "What's Ollama?" above.
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => onStartSetup(choice.id)}
                    disabled={!connected || anyRunning}
                    className="mt-2 text-sm font-medium text-accent-dark hover:text-accent disabled:opacity-50"
                  >
                    Try again
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function clampPercent(p: number): number {
  if (Number.isNaN(p)) return 0;
  return Math.min(100, Math.max(0, p));
}

function mentionsOllama(message?: string): boolean {
  return typeof message === "string" && /ollama/i.test(message);
}
