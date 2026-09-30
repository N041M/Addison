// "Run a model on this computer" — the local-model setup flow (spec §4.1.2,
// design-doc §7.3.2), in the dark direction's row idiom: one hairline row per
// curated choice, the honest "X GB download · needs Y GB memory" line as the
// row's mono value, and an accent "set up" as its action. The person has to ask
// for it. It is off by default and never shown during onboarding.
//
// The person picks one of a small curated list, which hands the Ollama model tag
// to the core via `ipc.startLocalSetup(modelName)`. Progress arrives on
// `model.localSetupProgress` and shows on the row. The core's line is the row's
// mono value, and a 2px `track` bar with an `ink` fill appears only while the
// core reports a percentage, because a bar that moves on its own would claim
// progress nobody measured. The setup ends on the core's "done" or "error" frame.
// After "done" the model list is read again and the model appears in the chat's
// model selector. After "error" the core's sentence shows under the row and every
// Set up button works again.

import { useState } from "react";
import type { LocalSetupState, RoleOption } from "../types/ui";
import { RowAction, SurfaceRow } from "./Surface";

// ---------------------------------------------------------------------------
// The curated choices. ONE obvious constant so the core team can align exact
// tags / sizes / memory floors later (these are placeholders pending the core's
// hardware-gating list). Copy tone follows design-doc §7.3.2: a plain name, an
// honest "X GB download · needs Y GB memory" line — no parameter counts, no
// quantization jargon. `id` is the Ollama tag the core pulls; it is used for the
// call, not shown as the primary label.
// ---------------------------------------------------------------------------
interface LocalModelChoice {
  id: string;
  name: string;
  metaLabel: string;
  note?: string;
}

const LOCAL_MODEL_CHOICES: LocalModelChoice[] = [
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
    <>
      <SurfaceRow
        name={
          <>
            Nothing you say leaves your machine. One-time download, runs through Ollama.
          </>
        }
        action={ollamaOpen ? "hide" : "what's Ollama?"}
        onAction={() => setOllamaOpen((v) => !v)}
      >
        {ollamaOpen && (
          <p className="m-0 mt-2.5 border-l-2 border-rail pl-3.5 text-[12px] leading-[1.55] text-ink-soft">
            Ollama is a small, free program that downloads and runs models on your own
            computer. Addison uses it behind the scenes — if it isn&rsquo;t installed or
            running, Addison will tell you plainly and can&rsquo;t set up a local model until
            it is.
          </p>
        )}
      </SurfaceRow>

      {!connected && (
        <SurfaceRow wrap name="Setting up a local model needs the desktop app. You can look over the choices here, but downloading starts once Addison is connected." />
      )}

      {LOCAL_MODEL_CHOICES.map((choice) => {
        const isInstalled = installed.has(choice.id.toLowerCase());
        const isThis = setup?.modelId === choice.id;
        const running = isThis && setup?.status === "running";
        const done = (isThis && setup?.status === "done") || isInstalled;
        const errored = isThis && setup?.status === "error";
        const percent = typeof setup?.percent === "number" ? clampPercent(setup.percent) : null;

        return (
          <SurfaceRow
            key={choice.id}
            name={choice.name}
            value={
              done
                ? "ready ✓"
                : running
                  ? (setup?.message ?? "getting ready…")
                  : choice.metaLabel
            }
            action={done ? undefined : running ? "setting up…" : "set up"}
            actionAriaLabel={`Set up ${choice.name}`}
            actionDisabled={!connected || anyRunning}
            onAction={done || running ? undefined : () => onStartSetup(choice.id)}
          >
            {/* Live progress — a 2px `track` bar with an `ink` fill, shown only
                when the core reports a real percentage. */}
            {running && percent !== null && (
              <div
                className="mt-2.5 h-[2px] bg-track"
                role="progressbar"
                aria-valuenow={Math.round(percent)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`${choice.name} download progress`}
              >
                <div
                  className="h-[2px] bg-ink transition-[width] duration-500"
                  style={{ width: `${percent}%` }}
                />
              </div>
            )}

            {/* Just-finished confirmation. It keys on this session's setup alone,
                so a model installed on an earlier day shows no note. It used to
                be hidden for installed rows too, which would hide it the moment
                the re-read list names the new model. */}
            {isThis && setup?.status === "done" && (
              <p className="m-0 mt-2 text-[12px] leading-[1.55] text-ink-soft">
                Ready to use. Pick &ldquo;On this computer&rdquo; beside the message box to use
                it.
              </p>
            )}

            {/* Inline, plain-language error — includes the core's own message
                (e.g. Ollama isn't running, or the machine is too small). */}
            {errored && (
              <div className="mt-2">
                <p className="m-0 text-[12px] leading-[1.55] text-ink-soft">
                  {setup?.error ?? "Setting this up didn't work. Please try again."}
                </p>
                {mentionsOllama(setup?.error) && (
                  <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                    Addison needs Ollama installed and running first — see &ldquo;what&rsquo;s
                    Ollama?&rdquo; above.
                  </p>
                )}
                <div className="mt-2">
                  <RowAction
                    disabled={!connected || anyRunning}
                    onClick={() => onStartSetup(choice.id)}
                  >
                    Try again
                  </RowAction>
                </div>
              </div>
            )}
          </SurfaceRow>
        );
      })}
    </>
  );
}

function clampPercent(p: number): number {
  if (Number.isNaN(p)) return 0;
  return Math.min(100, Math.max(0, p));
}

function mentionsOllama(message?: string): boolean {
  return typeof message === "string" && /ollama/i.test(message);
}
