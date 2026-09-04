// Your documents — the knowledge base's Settings surface (knowledge retrieval,
// phase 3 of three). Five parts:
//
//   (a) The fail-closed parsers: a row without a usable id or name is DROPPED, an
//       unrecognised status becomes "pending" and an unrecognised on-disk answer
//       becomes "unknown" — both of them the direction that UNDERSTATES what
//       Addison knows. A row with no path is KEPT, which is where this parser
//       parts company with its MCP sibling and why.
//   (b) The panel, rendered for real: the standing line byte-for-byte (its third
//       sentence is the one that says a passage of the document travels to the
//       model), and exactly one status sentence per state — with exactly the
//       actions that state has earned.
//   (c) The controls, driven through the real hook with mocked ipc: Update and Try
//       again both re-read, the two-press Remove says what it costs, and a CLOSED
//       PICKER produces no error and no notice at all — the failure that would
//       answer "never mind" with a sentence about something going wrong.
//   (d) The wait: a document is read and embedded locally with nothing to report
//       while it happens, so the panel says so and disables its controls.
//   (e) The page: NO PROFILE GATE. Simple has the search tool (owner decision 2,
//       2026-08-24) and the tool's own empty-handed sentence sends people to this
//       section by name, so a Simple surface without it would be an instruction to
//       a place that does not exist.
//
// The generated fixture (fixtures/knowledge.list.json, produced by
// tests/ipc_fixtures.py from the real handler) is consumed by
// parsers.fixtures.test.ts — that is where this parser meets a payload nobody on
// this side wrote.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
  renderHook,
  act,
} from "@testing-library/react";
import { parseKnowledgeDocuments, parseKnowledgeMutation } from "../lib/parse";
import { KnowledgePanel } from "../components/KnowledgePanel";
import { SettingsPage } from "../components/SettingsPage";
import { useKnowledge, type KnowledgeCardState } from "../hooks/useKnowledge";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { KnowledgeDocument, ProfileState } from "../types/ui";

afterEach(cleanup);

// --- Frozen copy — byte-for-byte. -------------------------------------------
const STANDING_LINE =
  "Give Addison a document and it can find the right part when you ask, instead of " +
  "reading the whole file. It keeps a searchable copy on this computer. When you ask " +
  "about a document, the parts that match are sent to the model that answers, like " +
  "anything else you say in a chat.";
const ADD_ACTION = "Add a document…";
const BUSY_LINE =
  "Addison is reading the document and building its index. A big file can take a minute.";
const LOADING_LINE = "Looking for your documents…";
const EMPTY_LINE = "No documents yet.";
const SECTION_TITLE = "Your documents";
/** The refusal the core sends when the same file is picked twice. */
const ALREADY_ADDED = "That document is already added. Use Update to read it again.";
/** What the core remembers when there is no local embedding model — the row is
 * WRITTEN with this on it, so the person can press Try again once Ollama runs. */
const NO_LOCAL_MODEL =
  "Addison couldn't do that, because the part that reads documents locally isn't " +
  "available. Install Ollama and the 'nomic-embed-text' model, then try again.";

const NAME = "Tenancy agreement.md";
const PATH = "/Users/mira/Documents/Tenancy agreement.md";

/** A row in the shape the parser produces, so a test can vary one field. */
function doc(over: Partial<KnowledgeDocument> = {}): KnowledgeDocument {
  return {
    id: "d1",
    displayName: NAME,
    path: PATH,
    status: "indexed",
    detail: null,
    chunkCount: 3,
    flaggedChunks: 0,
    byteSize: 1200,
    addedAt: 4102444800,
    indexedAt: 4102444860,
    onDisk: "same",
    ...over,
  };
}

// ---------------------------------------------------------------------------
// (a) the fail-closed parsers
// ---------------------------------------------------------------------------
describe("parseKnowledgeDocuments", () => {
  it("round-trips a realistic knowledge.list payload", () => {
    expect(
      parseKnowledgeDocuments({
        documents: [
          {
            id: "d1",
            displayName: NAME,
            path: PATH,
            status: "indexed",
            detail: null,
            chunkCount: 3,
            flaggedChunks: 1,
            byteSize: 1200,
            addedAt: 4102444800,
            indexedAt: 4102444860,
            onDisk: "changed",
          },
        ],
      }),
    ).toEqual([
      {
        id: "d1",
        displayName: NAME,
        path: PATH,
        status: "indexed",
        detail: null,
        chunkCount: 3,
        flaggedChunks: 1,
        byteSize: 1200,
        addedAt: 4102444800,
        indexedAt: 4102444860,
        onDisk: "changed",
      },
    ]);
  });

  it("never lets a bad field become a claim that a document is searchable", () => {
    // Every one of these fails toward "Addison hasn't got that far". A parser that
    // guessed the other way would print "Ready. NaN passages." under a document
    // nothing can search, on the one page a person opens to see what Addison read.
    const [row] = parseKnowledgeDocuments({
      documents: [
        {
          id: "d1",
          displayName: NAME,
          path: PATH,
          status: "definitely-fine",
          detail: { message: "nope" },
          chunkCount: "lots",
          flaggedChunks: -2,
          byteSize: null,
          addedAt: "yesterday",
          indexedAt: "later",
          onDisk: "probably-there",
        },
      ],
    });
    expect(row.status).toBe("pending");
    expect(row.onDisk).toBe("unknown");
    expect(row.detail).toBeNull();
    expect(row.chunkCount).toBe(0);
    expect(row.flaggedChunks).toBe(0);
    expect(row.byteSize).toBe(0);
    expect(row.addedAt).toBe(0);
    expect(row.indexedAt).toBeNull();
  });

  it("keeps a past failure off a row that is working", () => {
    // An older core, or a `detail` left behind by a re-read: printing it under an
    // indexed document would tell somebody their working document had failed.
    const [row] = parseKnowledgeDocuments({
      documents: [{ id: "d1", displayName: NAME, status: "indexed", detail: "It went wrong." }],
    });
    expect(row.detail).toBeNull();
  });

  it("drops a row it could not act on, and KEEPS one that only lost its path", () => {
    // The id and the name are what Remove and Update need; without them the row is
    // a button that would fail. The PATH is not in that class — dropping the row
    // would hide an indexed document that goes on answering questions while the
    // one page that could remove it pretends it is not there.
    const rows = parseKnowledgeDocuments({
      documents: [
        { id: "keep", displayName: "Keeper.md", path: PATH },
        { id: "no-path", displayName: "Nameless place.md" },
        { id: "", displayName: "No id" },
        { displayName: "No id at all" },
        { id: "no-name", path: PATH },
        "nonsense",
      ],
    });
    expect(rows.map((r) => r.id)).toEqual(["keep", "no-path"]);
    expect(rows[1].path).toBe("");
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", [], {}]) {
      expect(parseKnowledgeDocuments(junk)).toEqual([]);
    }
  });
});

describe("parseKnowledgeMutation", () => {
  it("keeps a closed picker separate from a refusal", () => {
    // The distinction is the whole point: closing the picker is a decision, not a
    // failure, and the panel has nothing to say about it.
    expect(parseKnowledgeMutation({ ok: false, cancelled: true })).toEqual({
      ok: false,
      cancelled: true,
      error: undefined,
    });
    expect(parseKnowledgeMutation({ ok: false, error: ALREADY_ADDED })).toEqual({
      ok: false,
      cancelled: undefined,
      error: ALREADY_ADDED,
    });
  });

  it("carries the row a successful add wrote, and survives one it cannot read", () => {
    const landed = parseKnowledgeMutation({
      ok: true,
      document: { id: "d1", displayName: NAME, path: PATH, status: "indexed", chunkCount: 3 },
    });
    expect(landed.ok).toBe(true);
    expect(landed.document?.id).toBe("d1");
    // An unusable row keeps its `ok`: every caller re-reads the list afterwards, and
    // printing an error about something that worked would be the worse of the two.
    expect(parseKnowledgeMutation({ ok: true, document: { id: "" } })).toEqual({ ok: true });
    for (const junk of [null, undefined, 42, "nope", []]) {
      expect(parseKnowledgeMutation(junk)).toEqual({
        ok: false,
        cancelled: undefined,
        error: undefined,
      });
    }
  });
});

// ---------------------------------------------------------------------------
// (b) the panel, with an injected state
// ---------------------------------------------------------------------------
function stateWith(over: Partial<KnowledgeCardState> = {}): KnowledgeCardState {
  return {
    documents: [],
    documentsLoaded: true,
    busy: false,
    error: null,
    notice: null,
    refreshDocuments: vi.fn(),
    handleAdd: vi.fn(async () => {}),
    handleReindex: vi.fn(async () => {}),
    handleRemove: vi.fn(async () => {}),
    ...over,
  };
}

function renderPanel(state: KnowledgeCardState) {
  render(<KnowledgePanel connected={true} knowledge={state} />);
}

describe("the documents panel", () => {
  it("shows the honest standing line byte-for-byte", () => {
    // Its third sentence is the load-bearing one: a passage of the person's own
    // document is sent to whichever model answers. Somebody deciding whether to add
    // their tenancy agreement is entitled to read that before they do.
    renderPanel(stateWith());
    expect(screen.getByText(STANDING_LINE)).toBeTruthy();
  });

  it("shows a quiet line before the documents have loaded, and no empty claim", () => {
    renderPanel(stateWith({ documentsLoaded: false }));
    expect(screen.getByText(LOADING_LINE)).toBeTruthy();
    // "No documents yet." is a claim about somebody's own library, and until the
    // core has answered, nobody has asked.
    expect(screen.queryByText(EMPTY_LINE)).toBeNull();
  });

  it("says the list is empty only once the core has answered", () => {
    renderPanel(stateWith());
    expect(screen.getByText(EMPTY_LINE)).toBeTruthy();
    expect(screen.queryByText(LOADING_LINE)).toBeNull();
    // The control reads with the ellipsis that means "this opens something", and
    // carries a spoken name of its own — a page of "add" buttons is unusable with
    // a screen reader.
    const add = screen.getByRole("button", { name: "Add a document" });
    expect(add.textContent).toBe(ADD_ACTION);
  });

  it("shows a ready document as one sentence, its path, and nothing to fix", () => {
    renderPanel(stateWith({ documents: [doc()] }));
    expect(screen.getByText("Ready. 3 passages.")).toBeTruthy();
    // The path in the machine-fact mono: two documents called "Notes.txt" are told
    // apart by nothing else.
    expect(screen.getByText(PATH)).toBeTruthy();
    expect(screen.queryByRole("button", { name: `Update ${NAME}` })).toBeNull();
    expect(screen.queryByRole("button", { name: `Try again ${NAME}` })).toBeNull();
    expect(screen.getByRole("button", { name: `Remove ${NAME}` })).toBeTruthy();
  });

  it("counts one passage as one passage", () => {
    renderPanel(stateWith({ documents: [doc({ chunkCount: 1 })] }));
    expect(screen.getByText("Ready. 1 passage.")).toBeTruthy();
  });

  it("says a document could not be digested without saying it is gone", () => {
    // "unknown" is what the core sends when nothing could be compared — no shell
    // bridge, or no digest. It must read exactly like "same": the document IS
    // indexed and IS searchable, and Addison simply has not looked at the file.
    renderPanel(stateWith({ documents: [doc({ onDisk: "unknown" })] }));
    expect(screen.getByText("Ready. 3 passages.")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/can't find|has changed/i);
  });

  it("admits when a document contains writing shaped like an instruction", () => {
    // The screening verdict, stored at index time, told to the person on the page
    // where they could still remove the document.
    renderPanel(stateWith({ documents: [doc({ flaggedChunks: 1 })] }));
    expect(
      screen.getByText(
        "Ready. 3 passages. 1 of them contain writing shaped like an instruction; " +
          "Addison treats those as information.",
      ),
    ).toBeTruthy();
  });

  it("says a file has changed, and offers to read it again", () => {
    renderPanel(stateWith({ documents: [doc({ onDisk: "changed" })] }));
    expect(screen.getByText("This file has changed since Addison read it.")).toBeTruthy();
    // Not "Ready": what Addison would answer from is the text it read, and saying
    // so is the whole reason the digest is taken.
    expect(screen.queryByText("Ready. 3 passages.")).toBeNull();
    expect(screen.getByRole("button", { name: `Update ${NAME}` })).toBeTruthy();
    expect(screen.getByRole("button", { name: `Remove ${NAME}` })).toBeTruthy();
  });

  it("says a file is gone, and offers no errand that could not finish", () => {
    // No Update: the picker would open on a file that is not there, and the person
    // would be sent to look for something Addison already knows is missing.
    renderPanel(stateWith({ documents: [doc({ onDisk: "missing" })] }));
    expect(screen.getByText("Addison can't find this file any more.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: `Update ${NAME}` })).toBeNull();
    expect(screen.getByRole("button", { name: `Remove ${NAME}` })).toBeTruthy();
  });

  it("shows a failed document as the core's own sentence, with the way back", () => {
    renderPanel(
      stateWith({
        documents: [doc({ status: "failed", detail: NO_LOCAL_MODEL, chunkCount: 0 })],
      }),
    );
    // Verbatim: the sentence names the missing piece and the next step, and nothing
    // this side could write would be better than the core's own words.
    expect(screen.getByText(NO_LOCAL_MODEL)).toBeTruthy();
    expect(screen.getByRole("button", { name: `Try again ${NAME}` })).toBeTruthy();
    expect(screen.getByRole("button", { name: `Remove ${NAME}` })).toBeTruthy();
  });

  it("says plainly when a document was never finished", () => {
    renderPanel(stateWith({ documents: [doc({ status: "pending", chunkCount: 0 })] }));
    expect(screen.getByText("Addison hasn't finished reading this.")).toBeTruthy();
    expect(screen.getByRole("button", { name: `Try again ${NAME}` })).toBeTruthy();
  });

  it("re-reads exactly the document whose control was pressed", () => {
    const state = stateWith({
      documents: [
        doc({ onDisk: "changed" }),
        doc({ id: "d2", displayName: "Notes.txt", path: "/Users/mira/Notes.txt", status: "failed", detail: NO_LOCAL_MODEL }),
      ],
    });
    renderPanel(state);
    // A column of identical "Update" buttons is the shape in which somebody updates
    // the wrong document, so each is named.
    fireEvent.click(screen.getByRole("button", { name: `Update ${NAME}` }));
    expect(state.handleReindex).toHaveBeenCalledWith("d1");
    fireEvent.click(screen.getByRole("button", { name: "Try again Notes.txt" }));
    expect(state.handleReindex).toHaveBeenCalledWith("d2");
    expect(state.handleReindex).toHaveBeenCalledTimes(2);
  });

  it("takes two presses to remove, names the document, and says what it costs", () => {
    const state = stateWith({
      documents: [doc(), doc({ id: "d2", displayName: "Notes.txt", path: "/Users/mira/Notes.txt" })],
    });
    renderPanel(state);
    expect(screen.getAllByRole("button", { name: /^Remove / })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Remove Notes.txt" }));
    expect(state.handleRemove).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Remove Notes.txt" }).textContent).toBe(
      "Really remove?",
    );
    // The consequence this two-press has that no other one in Settings does: the
    // knowledge tables are excluded from restore points, so the usual way back is
    // not there. It is said at the moment of the second press.
    expect(
      screen.getByText(
        "Removing a document is permanent — a restore point won't bring it back. The file on " +
          "your computer is left alone.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove Notes.txt" }));
    expect(state.handleRemove).toHaveBeenCalledWith("d2", "Notes.txt");
  });

  it("says what a finished removal did, and to which document", () => {
    renderPanel(
      stateWith({ notice: "Addison has forgotten Notes.txt. The file itself is untouched." }),
    );
    expect(
      screen.getByText("Addison has forgotten Notes.txt. The file itself is untouched."),
    ).toBeTruthy();
  });

  it("renders a refusal as one plain sentence, not a stack trace", () => {
    renderPanel(stateWith({ error: ALREADY_ADDED }));
    const text = document.body.textContent ?? "";
    expect(text).toContain(ALREADY_ADDED);
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("Error:");
  });

  // -------------------------------------------------------------------------
  // (d) the wait
  // -------------------------------------------------------------------------
  it("names the wait while a document is being read, and disables the controls", () => {
    // A local embedding pass over a couple of megabytes has nothing to report while
    // it happens. Silence there reads as a broken page, and a second press during
    // it would put a second modal picker behind the first.
    renderPanel(stateWith({ busy: true, documents: [doc({ onDisk: "changed" })] }));
    expect(screen.getByText(BUSY_LINE)).toBeTruthy();
    expect(screen.getByRole("button", { name: `Update ${NAME}` }).hasAttribute("disabled")).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: `Remove ${NAME}` }).hasAttribute("disabled")).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: "Add a document" }).hasAttribute("disabled")).toBe(
      true,
    );
  });

  it("says nothing about a wait when nothing is happening", () => {
    renderPanel(stateWith({ documents: [doc()] }));
    expect(screen.queryByText(BUSY_LINE)).toBeNull();
  });

  it("shows a quiet placeholder when the engine isn't connected", () => {
    render(<KnowledgePanel connected={false} knowledge={stateWith()} />);
    expect(screen.queryByRole("button", { name: "Add a document" })).toBeNull();
    expect(screen.getByText(/once Addison.s engine is connected/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (c) the hook, with mocked ipc
// ---------------------------------------------------------------------------
vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribeCoreState: () => () => {},
    ipc: {
      ...actual.ipc,
      listKnowledgeDocuments: vi.fn(async () => []),
      addKnowledgeDocument: vi.fn(async () => ({ ok: true })),
      reindexKnowledgeDocument: vi.fn(async () => ({ ok: true })),
      removeKnowledgeDocument: vi.fn(async () => ({ ok: true })),
    },
  };
});

describe("useKnowledge (real hook, mocked ipc)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reads the list on mount and picks no file by itself", async () => {
    const { ipc } = await import("../ipc/client");
    renderHook(() => useKnowledge({ connected: true }));
    await waitFor(() => expect(ipc.listKnowledgeDocuments).toHaveBeenCalled());
    // Listing is not reading. Nothing here opens a picker or touches a file the
    // person did not just choose.
    expect(ipc.addKnowledgeDocument).not.toHaveBeenCalled();
    expect(ipc.reindexKnowledgeDocument).not.toHaveBeenCalled();
  });

  it("says NOTHING when the picker is closed", async () => {
    // The failure this guards: answering a deliberate "never mind" with a line
    // explaining what went wrong. A cancelled add is not an error and not an
    // outcome — it is a decision, and the panel stays silent about it.
    const { ipc } = await import("../ipc/client");
    (ipc.addKnowledgeDocument as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      cancelled: true,
    });
    const { result } = renderHook(() => useKnowledge({ connected: true }));
    await act(async () => {
      await result.current.handleAdd();
    });
    expect(result.current.error).toBeNull();
    expect(result.current.notice).toBeNull();
  });

  it("says nothing when a re-read's picker is closed either", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.reindexKnowledgeDocument as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      cancelled: true,
    });
    const { result } = renderHook(() => useKnowledge({ connected: true }));
    await act(async () => {
      await result.current.handleReindex("d1");
    });
    expect(result.current.error).toBeNull();
    expect(result.current.notice).toBeNull();
  });

  it("surfaces the core's own refusal sentence, untouched", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.addKnowledgeDocument as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: ALREADY_ADDED,
    });
    const { result } = renderHook(() => useKnowledge({ connected: true }));
    await act(async () => {
      await result.current.handleAdd();
    });
    expect(result.current.error).toBe(ALREADY_ADDED);
  });

  it("sends the id it was given when a document is re-read", async () => {
    const { ipc } = await import("../ipc/client");
    const { result } = renderHook(() => useKnowledge({ connected: true }));
    await act(async () => {
      await result.current.handleReindex("d2");
    });
    expect(ipc.reindexKnowledgeDocument).toHaveBeenCalledWith("d2");
  });

  it("says plainly what a successful removal did, and to what", async () => {
    const { ipc } = await import("../ipc/client");
    const { result } = renderHook(() => useKnowledge({ connected: true }));
    await act(async () => {
      await result.current.handleRemove("d1", "Notes.txt");
    });
    expect(ipc.removeKnowledgeDocument).toHaveBeenCalledWith("d1");
    expect(result.current.notice).toBe(
      "Addison has forgotten Notes.txt. The file itself is untouched.",
    );
    expect(result.current.error).toBeNull();
  });

  it("is busy only while the read is actually out", async () => {
    const { ipc } = await import("../ipc/client");
    let release: (value: { ok: boolean }) => void = () => {};
    (ipc.addKnowledgeDocument as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const { result } = renderHook(() => useKnowledge({ connected: true }));
    let pending: Promise<void> = Promise.resolve();
    await act(async () => {
      pending = result.current.handleAdd();
    });
    expect(result.current.busy).toBe(true);
    await act(async () => {
      release({ ok: true });
      await pending;
    });
    expect(result.current.busy).toBe(false);
    // And the list is re-read afterwards, because what the add wrote is the core's
    // answer and not this side's guess.
    expect(ipc.listKnowledgeDocuments).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// (e) the page — no profile gate
// ---------------------------------------------------------------------------
const PROFILE: ProfileState = {
  activeProfile: "developer",
  mode: "open",
  profiles: [
    { id: "simple", label: "Simple", description: "Approachable by default." },
    { id: "developer", label: "Developer", description: "Power on request." },
    { id: "custom", label: "Custom", description: "Advanced.", advanced: true },
  ],
  flags: {
    exposeRoutinePlan: false,
    rawDiagnostics: false,
    headlessCli: false,
    byokFirstOnboarding: false,
  },
};

function renderSettings(profile: ProfileState, knowledge: KnowledgeCardState | undefined) {
  const noop = vi.fn();
  const models = {
    roles: [],
    rolesLoaded: true,
    cloudModels: [],
    providers: [],
    selectedRole: "primary",
    refreshRoles: noop,
    refreshProviders: noop,
    handleSelectModel: noop,
    handleSelectEffort: noop,
    handleChangeDefaultCloudModel: noop,
    handleChangeDefaultRole: noop,
    handleStartLocalSetup: noop,
    handleConnectProvider: noop,
    handleRemoveProvider: noop,
    localSetup: null,
    setLocalSetup: noop,
  };
  const skills = {
    skills: [],
    skillsLoaded: true,
    refreshSkills: noop,
    handleCreateSkill: vi.fn(async () => {}),
    handleUpdateSkill: vi.fn(async () => {}),
    handleToggleSkill: vi.fn(async () => {}),
    handleDeleteSkill: vi.fn(async () => {}),
  };
  const guards: GuardsCardState = {
    guards: null,
    guardsLoaded: false,
    busy: false,
    error: null,
    refreshGuards: noop,
    handleSave: vi.fn(async () => {}),
  };
  const snapshots = {
    snapshots: [],
    snapshotsLoaded: true,
    busy: false,
    notice: null,
    refreshSnapshots: noop,
    handleCreateSnapshot: vi.fn(async () => {}),
    handleRestoreLastWorking: vi.fn(async () => {}),
    handleRestoreSnapshot: vi.fn(async () => {}),
    handleDeleteSnapshot: vi.fn(async () => {}),
  };
  render(
    <SettingsPage
      connected={true}
      models={models as unknown as ModelSelection}
      skills={skills as unknown as SkillsState}
      knowledge={knowledge}
      snapshots={snapshots as unknown as SnapshotsState}
      guards={guards}
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
    />,
  );
}

describe("the Your-documents section on every surface", () => {
  it("renders on the Developer surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" }, stateWith());
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
  });

  it("renders on the Simple surface too, with the same controls", () => {
    // NOT the Tool servers treatment. `search_knowledge` is LOW and read-only and
    // registers in both profiles (owner decision 2, 2026-08-24), and its
    // empty-handed sentence tells people to "Add one in Settings, under Your
    // documents" — a Simple surface without this section would be an instruction to
    // a place that does not exist.
    renderSettings(
      { ...PROFILE, activeProfile: "simple", mode: "safe" },
      stateWith({ documents: [doc()] }),
    );
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
    expect(screen.getByText(NAME)).toBeTruthy();
    expect(screen.getByRole("button", { name: `Remove ${NAME}` })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Add a document" }).length).toBeGreaterThan(0);
  });

  it("sits between Skills and Profile, where the standing line is read on the way past", () => {
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" }, stateWith());
    const text = document.body.textContent ?? "";
    expect(text.indexOf("Skills")).toBeLessThan(text.indexOf(SECTION_TITLE));
    expect(text.indexOf(SECTION_TITLE)).toBeLessThan(text.indexOf("Profile"));
  });

  it("is omitted when no knowledge bundle is supplied (older callers)", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" }, undefined);
    expect(screen.queryByText(SECTION_TITLE)).toBeNull();
  });
});
