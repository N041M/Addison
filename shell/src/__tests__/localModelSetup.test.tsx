// "Run a model on this computer" has to follow the setup to its end (KNOWN-BUGS 20).
//
// `model.startLocalSetup` answers `{ok, started}` as soon as the download begins.
// The window used to treat that answer as the end of the setup. It showed the model
// as ready at once, then every progress frame set it back to "setting up…" for
// ever, because the subscriber looked for `done` and `error` keys that the core
// never sends. A failed setup was never shown and every Set up button stayed
// disabled. The finished model did not reach the picker either, because
// `normalizeRoles` dropped the `localModels` list the core sends beside the roles.
//
// The review of the first fix found two more ways to lose track of a setup. The
// frames of one setup could mark a different row ready after a start call timed
// out, and an engine restart during a download left the row running for good.
// Every frame now names its model, and a stopped engine ends a running setup.
//
// The frames here are the REAL ones. tests/ipc_fixtures.py drives the core's own
// `_run_local_setup` once to the end and once into a failed check, and
// tests/test_ipc_fixture_drift.py fails if the core stops producing them.
// `model.availableRoles.json` is the core's answer once "llama3.2:3b" is set up.
//
// The tests come in two parts. Part A drives `useModelSelection` directly. Part B
// renders the real App, presses the real Set up buttons in Settings, and pushes
// frames and engine states through App's own subscribers.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  renderHook,
  screen,
  fireEvent,
  cleanup,
  act,
  within,
} from "@testing-library/react";

import doneFrames from "./fixtures/model.localSetupProgress.json";
import errorFrames from "./fixtures/model.localSetupProgress.error.json";
import rolesAfterSetup from "./fixtures/model.availableRoles.json";

const handlers = vi.hoisted(
  () => new Map<string, (params: Record<string, unknown>) => void>(),
);
const coreStateHandlers = vi.hoisted(() => new Set<(state: string) => void>());

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribeCoreState: (handler: (state: string) => void) => {
      coreStateHandlers.add(handler);
      return () => coreStateHandlers.delete(handler);
    },
    subscribeStatus: () => () => {},
    subscribeDiagnostics: () => () => {},
    subscribe: (method: string, handler: (params: Record<string, unknown>) => void) => {
      handlers.set(method, handler);
      return () => handlers.delete(method);
    },
    ipc: {
      ...actual.ipc,
      startLocalSetup: vi.fn(),
      availableRoles: vi.fn(),
      listProviders: vi.fn(async () => []),
      getProfile: vi.fn(async () => ({ activeProfile: "simple", profiles: [], flags: {} })),
      listWorkspaceRoots: vi.fn(async () => []),
    },
  };
});

// Imported AFTER the mock so these are the mocked functions.
import { ipc } from "../ipc/client";
import { App } from "../App";
import { useModelSelection } from "../hooks/useModelSelection";
import { Method } from "../types/protocol";

// The fixtures set up "Light and quick". "Balanced" is the second curated row.
const MODEL = "llama3.2:3b";
const OTHER = "llama3.1:8b";
const STARTED = { ok: true, started: true };
// Before the setup, only the cloud role is configured and no model is local.
const ROLES_BEFORE = { roles: ["primary"], localModels: [], cloudModels: [] };
// The core's own sentences (rpc/constants.py) and the client's timeout (client.ts).
const OLLAMA_REFUSAL =
  "Ollama isn't running on this computer. Install it from ollama.com (or start it if " +
  "it's already installed), then try again — Addison can't install it for you.";
const BUSY =
  "Addison is already setting up a model. Let that one finish before starting another.";
const TIMED_OUT = "Addison took too long to answer. Please try again.";
// It is written out here so that rewording it has to change this test too.
const INTERRUPTED =
  "Setting up stopped because Addison's engine stopped. Press Set up to try again.";

const startLocalSetup = vi.mocked(ipc.startLocalSetup);
const availableRoles = vi.mocked(ipc.availableRoles);

afterEach(cleanup);

beforeEach(() => {
  handlers.clear();
  coreStateHandlers.clear();
  startLocalSetup.mockReset();
  startLocalSetup.mockResolvedValue(STARTED as never);
  availableRoles.mockReset();
  availableRoles.mockResolvedValue(ROLES_BEFORE as never);
});

/** Let pending promise continuations run. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

/** A promise the test settles by hand. */
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** The same frame, as the core would send it for another model. */
function forModel(frame: Record<string, unknown>, modelName: string) {
  return { ...frame, modelName };
}

// ===========================================================================
// (A) The hook
// ===========================================================================

describe("useModelSelection follows the setup's frames", () => {
  it("keeps the row running after startLocalSetup answers", async () => {
    // Kills: the old `.then` that marked the setup done and ready at once.
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(MODEL));
    await settle();

    expect(startLocalSetup).toHaveBeenCalledWith(MODEL);
    expect(result.current.localSetup?.status).toBe("running");
    expect(result.current.localSetup?.message).toBe("Getting ready…");
    expect(availableRoles).not.toHaveBeenCalled();
  });

  it("runs through the download and check, then is done with the model in the list", async () => {
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(MODEL));
    await settle();
    availableRoles.mockResolvedValue(rolesAfterSetup as never);

    const [starting, halfway, downloaded, checking, done] = doneFrames;
    expect(done.stage).toBe("done");
    expect(doneFrames.every((f) => f.modelName === MODEL)).toBe(true);

    act(() => result.current.handleLocalSetupProgress(starting));
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "running",
      message: starting.message,
    });

    act(() => result.current.handleLocalSetupProgress(halfway));
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "running",
      message: halfway.message,
      percent: 45,
    });

    act(() => result.current.handleLocalSetupProgress(downloaded));
    // The check has no measured progress, so its frame takes the bar away.
    act(() => result.current.handleLocalSetupProgress(checking));
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "running",
      message: checking.message,
    });
    expect(availableRoles).not.toHaveBeenCalled();

    // Kills: reading `done` as a key (the old subscriber), and dropping the
    // refresh on "done".
    act(() => result.current.handleLocalSetupProgress(done));
    await settle();
    expect(result.current.localSetup?.status).toBe("done");
    expect(availableRoles).toHaveBeenCalledTimes(1);

    // Kills: `normalizeRoles` leaving the sibling `localModels` off the local role.
    // This is what the chat's model selector reads.
    const local = result.current.roles.find((r) => r.role === "local");
    expect(local?.models).toEqual([{ id: MODEL, label: MODEL }]);
    expect(result.current.effectiveLocalModel("local")).toBe(MODEL);
  });

  it("ends in the core's own sentence when the setup fails, and re-reads nothing", async () => {
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(MODEL));
    await settle();

    // Kills: reading `error` as a key. The core puts the sentence in `message`.
    for (const frame of errorFrames) {
      act(() => result.current.handleLocalSetupProgress(frame));
    }
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "error",
      error: "The local model had a problem. Please try again in a moment.",
    });
    await settle();
    expect(availableRoles).not.toHaveBeenCalled();
  });

  it("keeps the end of the setup when the answer arrives after it", async () => {
    // The setup thread starts before the core writes its answer, so a fast failure
    // can reach the window first. A late answer must not turn it back into a
    // setup that is still running.
    const answer = deferred();
    startLocalSetup.mockReturnValue(answer.promise as never);
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(MODEL));
    for (const frame of errorFrames) {
      act(() => result.current.handleLocalSetupProgress(frame));
    }
    await act(async () => {
      answer.resolve(STARTED);
    });
    expect(result.current.localSetup?.status).toBe("error");
  });

  it("shows a refusal before the download starts, from the rejected call", async () => {
    startLocalSetup.mockRejectedValue(new Error(OLLAMA_REFUSAL));
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(MODEL));
    await settle();
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "error",
      error: OLLAMA_REFUSAL,
    });
  });

  it("never lets one setup's frames mark another model ready (the review's case)", async () => {
    // Light and quick is pressed while a long turn runs. Its call gives up, the
    // core starts the download anyway, and Balanced is then refused as busy. The
    // download's frames used to land on whatever row was on screen, so Balanced
    // read "ready ✓".
    // Kills: applying a frame to the setup on screen whatever model it names, and
    // ignoring every frame once the setup on screen has ended.
    startLocalSetup
      .mockRejectedValueOnce(new Error(TIMED_OUT))
      .mockRejectedValueOnce(new Error(BUSY));
    const { result } = renderHook(() => useModelSelection());

    act(() => result.current.handleStartLocalSetup(MODEL));
    await settle();
    expect(result.current.localSetup).toEqual({ modelId: MODEL, status: "error", error: TIMED_OUT });

    act(() => result.current.handleStartLocalSetup(OTHER));
    await settle();
    expect(result.current.localSetup).toEqual({ modelId: OTHER, status: "error", error: BUSY });

    act(() => result.current.handleLocalSetupProgress(doneFrames[1]));
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "running",
      message: doneFrames[1].message,
      percent: 45,
    });
    for (const frame of doneFrames.slice(2)) {
      act(() => result.current.handleLocalSetupProgress(frame));
    }
    await settle();
    expect(result.current.localSetup).toEqual({ modelId: MODEL, status: "done", percent: 100 });
  });

  it("ignores another model's frames while a setup is still running", () => {
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(OTHER));
    act(() => result.current.handleLocalSetupProgress(doneFrames[4]));
    expect(result.current.localSetup).toEqual({
      modelId: OTHER,
      status: "running",
      message: "Getting ready…",
    });
  });

  it("takes up a setup it lost track of, and ignores a frame that names no model", () => {
    // A window reloaded during the download has no setup in state. The frames
    // name their model, so the row picks the download back up.
    const { result } = renderHook(() => useModelSelection());
    const { modelName: _dropped, ...unnamed } = doneFrames[1];
    act(() => result.current.handleLocalSetupProgress(unnamed));
    expect(result.current.localSetup).toBeNull();

    act(() => result.current.handleLocalSetupProgress(doneFrames[1]));
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "running",
      message: doneFrames[1].message,
      percent: 45,
    });

    // With a setup on screen, a frame that names no model still changes nothing.
    // Kills: putting an unnamed frame on the row that happens to be on screen.
    const { modelName: _alsoDropped, ...unnamedFailure } = errorFrames[4];
    act(() => result.current.handleLocalSetupProgress(unnamedFailure));
    expect(result.current.localSetup?.status).toBe("running");
  });

  it("lands a refusal only on the setup its own call started", async () => {
    // Light and quick's call is still waiting when the engine restarts. The
    // person then starts Balanced, whose download is running when Light and
    // quick's old call finally gives up.
    // Kills: applying a refusal to whatever setup is on screen.
    const lightCall = deferred();
    startLocalSetup.mockReturnValueOnce(lightCall.promise as never);
    const { result } = renderHook(() => useModelSelection());

    act(() => result.current.handleStartLocalSetup(MODEL));
    act(() => result.current.handleCoreState("restarting"));
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "error",
      error: INTERRUPTED,
    });

    act(() => result.current.handleCoreState("ready"));
    act(() => result.current.handleStartLocalSetup(OTHER));
    await settle();
    act(() => result.current.handleLocalSetupProgress(forModel(doneFrames[1], OTHER)));

    await act(async () => {
      lightCall.reject(new Error(TIMED_OUT));
    });
    expect(result.current.localSetup).toEqual({
      modelId: OTHER,
      status: "running",
      message: doneFrames[1].message,
      percent: 45,
    });
  });

  it("leaves a setup its frames have ended alone when its call gives up late", async () => {
    // Kills: dropping the `status === "running"` half of the refusal check.
    const call = deferred();
    startLocalSetup.mockReturnValueOnce(call.promise as never);
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(MODEL));
    for (const frame of doneFrames) {
      act(() => result.current.handleLocalSetupProgress(frame));
    }
    await act(async () => {
      call.reject(new Error(TIMED_OUT));
    });
    expect(result.current.localSetup).toEqual({ modelId: MODEL, status: "done", percent: 100 });
  });

  it("ends a running setup when the engine stops, and only a running one", () => {
    const { result } = renderHook(() => useModelSelection());
    act(() => result.current.handleStartLocalSetup(MODEL));
    act(() => result.current.handleLocalSetupProgress(doneFrames[1]));

    act(() => result.current.handleCoreState("ready"));
    expect(result.current.localSetup?.status).toBe("running");

    act(() => result.current.handleCoreState("restarting"));
    expect(result.current.localSetup).toEqual({
      modelId: MODEL,
      status: "error",
      error: INTERRUPTED,
    });

    // A setup that already finished keeps its result through a later restart.
    act(() => result.current.handleStartLocalSetup(MODEL));
    for (const frame of doneFrames) {
      act(() => result.current.handleLocalSetupProgress(frame));
    }
    act(() => result.current.handleCoreState("stopped"));
    expect(result.current.localSetup?.status).toBe("done");
  });
});

// ===========================================================================
// (B) The real App, the real buttons, and App's own subscribers
// ===========================================================================

/** jsdom ships no matchMedia and the layout keys off one. Wide by default. */
function stubMatchMedia() {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function emit(frame: Record<string, unknown>) {
  const handler = handlers.get(Method.ModelLocalSetupProgress);
  expect(handler, "App never subscribed to model.localSetupProgress").toBeDefined();
  act(() => handler?.(frame));
}

function emitCoreState(state: string) {
  expect(coreStateHandlers.size, "App never subscribed to the engine state").toBeGreaterThan(0);
  act(() => coreStateHandlers.forEach((handler) => handler(state)));
}

/** The "Run a model on this computer" section of Settings. */
function localSection(): HTMLElement {
  // The section label's parent is the whole section (Surface.tsx, SurfaceSection).
  const section = screen.getByText("Run a model on this computer").parentElement;
  if (!section) throw new Error("the local model section is not on screen");
  return section;
}

/** One curated row in that section, found by its name. */
function row(name: string) {
  const nameCell = within(localSection()).getByText(name, { selector: "span" });
  const element = nameCell.parentElement?.parentElement;
  if (!element) throw new Error(`no row named ${name}`);
  return within(element);
}

async function openSettings() {
  render(<App />);
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  await screen.findByRole("button", { name: "Set up Light and quick" });
}

async function pressSetUp(name: string) {
  fireEvent.click(screen.getByRole("button", { name: `Set up ${name}` }));
  await settle();
}

function setUpButtons(): HTMLButtonElement[] {
  return within(localSection()).getAllByRole("button", {
    name: /^Set up /,
  }) as HTMLButtonElement[];
}

describe("the Settings rows, driven through App's subscribers", () => {
  beforeEach(() => {
    stubMatchMedia();
    localStorage.clear();
    Element.prototype.scrollIntoView = () => {};
  });

  it("stays running through the download, then says ready and names the model", async () => {
    await openSettings();
    await pressSetUp("Light and quick");
    expect(startLocalSetup).toHaveBeenCalledWith(MODEL);
    const section = () => within(localSection());

    // The row is still setting up after the answer. The old window said "ready ✓"
    // here.
    expect(section().queryByText("ready ✓")).toBeNull();
    expect(section().getByText("setting up…")).toBeTruthy();
    expect(setUpButtons().every((b) => b.disabled)).toBe(true);

    availableRoles.mockResolvedValue(rolesAfterSetup as never);
    emit(doneFrames[1]);
    expect(section().getByText("Downloading the model — 45%")).toBeTruthy();
    expect(section().getByRole("progressbar").getAttribute("aria-valuenow")).toBe("45");

    for (const frame of doneFrames.slice(2)) emit(frame);
    await settle();

    expect(section().getByText("ready ✓")).toBeTruthy();
    expect(
      section().getByText(/Ready to use\. Pick .On this computer. beside the message box/),
    ).toBeTruthy();
    expect(section().queryByText("setting up…")).toBeNull();
    // The re-read list reached the page: "Where Addison thinks" names the model.
    expect(screen.getByText(`On this computer — ${MODEL}`)).toBeTruthy();
  });

  it("shows the core's sentence when the check fails, and every Set up works again", async () => {
    await openSettings();
    await pressSetUp("Light and quick");
    for (const frame of errorFrames) emit(frame);
    await settle();

    const section = within(localSection());
    expect(
      section.getByText("The local model had a problem. Please try again in a moment."),
    ).toBeTruthy();
    expect(section.queryByText("setting up…")).toBeNull();
    expect(section.queryByText("ready ✓")).toBeNull();
    expect(setUpButtons().some((b) => b.disabled)).toBe(false);
    const tryAgain = section.getByRole("button", { name: "Try again" }) as HTMLButtonElement;
    expect(tryAgain.disabled).toBe(false);
  });

  it("marks the model whose download finished as ready (the review's case)", async () => {
    startLocalSetup
      .mockRejectedValueOnce(new Error(TIMED_OUT))
      .mockRejectedValueOnce(new Error(BUSY));
    await openSettings();

    await pressSetUp("Light and quick");
    expect(row("Light and quick").getByText(TIMED_OUT)).toBeTruthy();
    await pressSetUp("Balanced");
    expect(row("Balanced").getByText(BUSY)).toBeTruthy();

    availableRoles.mockResolvedValue(rolesAfterSetup as never);
    for (const frame of doneFrames) emit(frame);
    await settle();

    expect(row("Light and quick").getByText("ready ✓")).toBeTruthy();
    expect(row("Balanced").queryByText("ready ✓")).toBeNull();
    expect(row("Balanced").queryByText(/Ready to use/)).toBeNull();
  });

  it("ends the setup in a plain sentence when the engine restarts mid-download", async () => {
    // No final frame comes from an engine that has stopped, so before this the row
    // said "setting up…" and every Set up button stayed disabled for good.
    // Kills: App not handing the engine state to the hook.
    await openSettings();
    await pressSetUp("Light and quick");
    emit(doneFrames[1]);
    expect(setUpButtons().every((b) => b.disabled)).toBe(true);

    emitCoreState("restarting");

    expect(row("Light and quick").getByText(INTERRUPTED)).toBeTruthy();
    expect(within(localSection()).queryByText("setting up…")).toBeNull();
    expect(setUpButtons().some((b) => b.disabled)).toBe(false);
    const tryAgain = row("Light and quick").getByRole("button", {
      name: "Try again",
    }) as HTMLButtonElement;
    expect(tryAgain.disabled).toBe(false);
  });
});
