// The review surface's DIFF payloads, against the REAL core handlers' output — the
// frontend half of the payload-shape drift loop for Phase-3 Build §2/§3.
//
// The screen that renders these is §4; what §2/§3 ship on this side is the TYPES, so
// this suite pins the fixtures against those types rather than against a parser that
// does not exist yet — the same shape `workspaceReadPaths.fixtures.test.ts` set for §1,
// and for the same reason: the `roots`/`folders` incident shipped because each side
// asserted its own idea of the payload and neither ever read the other's.
//
// Regenerate with `python tests/ipc_fixtures.py`; tests/test_ipc_fixture_drift.py fails
// when a handler drifts from the committed files.
import { describe, expect, it } from "vitest";

import type {
  WorkspaceEdit,
  WorkspaceEditDiff,
  WorkspaceEditList,
  WorkspaceRevertResult,
} from "../types/protocol";

import diffFixture from "./fixtures/workspace.readEditDiff.json";
import editsFixture from "./fixtures/workspace.listEdits.json";
import revertFixture from "./fixtures/workspace.revertFile.json";

const list = editsFixture as WorkspaceEditList;
const byPath = new Map(list.edits.map((edit) => [edit.path, edit]));

const FIELDS = [
  "created",
  "firstWrittenAt",
  "lastWrittenAt",
  "missing",
  "onDiskChanged",
  "path",
  "relativePath",
  "revertable",
  "root",
  "snapshotIds",
  "writes",
];

describe("workspace.listEdits over the real payload", () => {
  it("gives every edit the same field set, newest first", () => {
    expect(list.edits.length).toBeGreaterThan(1);
    for (const edit of list.edits) {
      expect(Object.keys(edit).sort()).toEqual(FIELDS);
    }
    const written = list.edits.map((edit) => edit.lastWrittenAt);
    expect(written).toEqual([...written].sort((a, b) => b - a));
    expect(list.truncated).toBe(false);
  });

  it("carries NO file text — that is what readEditDiff is for", () => {
    // The whole reason the two methods are separate: twenty files' before and after on
    // one JSON line is megabytes across two process boundaries, for a list whose job is
    // to let somebody choose one of them. A `before`/`after` appearing here would be
    // that mistake, made permanent by a fixture that accepted it.
    for (const edit of list.edits as unknown as Record<string, unknown>[]) {
      expect(edit.before).toBeUndefined();
      expect(edit.after).toBeUndefined();
      expect(edit.content).toBeUndefined();
    }
  });

  it("collapses several writes to one file into ONE edit, chain newest first", () => {
    const app = byPath.get("/fixture/project/src/app.py") as WorkspaceEdit;
    expect(app.writes).toBe(3);
    expect(app.snapshotIds).toEqual(["edit-app-3", "edit-app-2", "edit-app-1"]);
    // The chain spans the three writes: first is the OLDEST, which is where a revert
    // lands and what the diff's left pane shows.
    expect(app.firstWrittenAt).toBeLessThan(app.lastWrittenAt);
  });

  it("says CHANGED when the file on disk is not what Addison wrote", () => {
    expect(byPath.get("/fixture/project/src/app.py")?.onDiskChanged).toBe(true);
    expect(byPath.get("/fixture/project/notes.md")?.onDiskChanged).toBe(false);
  });

  it("says null — not false — when Addison cannot tell", () => {
    // Three-valued on purpose. `false` is the answer that lets a revert proceed with no
    // warning, so an unknown collapsing into it is the one wrong reading that costs
    // somebody their own work. This row predates the digest, and it is also the
    // post-restart row: the shell can no longer put it back.
    const legacy = byPath.get("/fixture/project/legacy.txt") as WorkspaceEdit;
    expect(legacy.onDiskChanged).toBeNull();
    expect(legacy.revertable).toBe(false);
  });

  it("marks a file Addison created, and one that is no longer there", () => {
    const notes = byPath.get("/fixture/project/notes.md") as WorkspaceEdit;
    expect(notes.created).toBe(true);
    const gone = byPath.get("/fixture/project/gone.txt") as WorkspaceEdit;
    expect(gone.missing).toBe(true);
    // A file that is gone cannot be compared against anything.
    expect(gone.onDiskChanged).toBeNull();
  });

  it("still lists an edit whose folder is no longer trusted, with no root", () => {
    // The plan requires it: the question is "what has Addison changed that is still
    // changed", and revoking trust does not un-change a file. `root: null` is why this
    // field is nullable on this side, and `relativePath` falls back to the whole path
    // because there is nothing for it to be relative to.
    const orphan = byPath.get("/fixture/elsewhere/orphan.txt") as WorkspaceEdit;
    expect(orphan.root).toBeNull();
    expect(orphan.relativePath).toBe("/fixture/elsewhere/orphan.txt");
    const inside = byPath.get("/fixture/project/src/app.py") as WorkspaceEdit;
    expect(inside.relativePath).toBe("src/app.py");
  });
});

describe("workspace.readEditDiff over the real payload", () => {
  const diff = diffFixture as WorkspaceEditDiff;

  it("shows the state before the FIRST of the collapsed writes, and disk now", () => {
    expect(Object.keys(diff).sort()).toEqual([
      "after",
      "afterTruncated",
      "before",
      "beforeTruncated",
      "path",
    ]);
    // BEFORE is the oldest prior in the chain — the state a revert produces — and NOT
    // the prior of the most recent write, which would show a diff nobody can act on.
    expect(diff.before).toBe("def main():\n    pass\n");
    // AFTER is the file as it is NOW, edits by the person included. That is the point:
    // it is what Revert would replace.
    expect(diff.after).toContain("my own tweak");
    expect(diff.beforeTruncated).toBe(false);
    expect(diff.afterTruncated).toBe(false);
  });
});

describe("workspace.revertFile over the real payload", () => {
  const reverted = revertFixture as WorkspaceRevertResult;

  it("says which file it put back, in a sentence and by name only", () => {
    expect(reverted.ok).toBe(true);
    expect(reverted.path).toBe("/fixture/project/src/app.py");
    // The NAME, never the full path — the rule every tool label and permission card in
    // the app follows. A sentence with a home directory in it ends up in screenshots.
    expect(reverted.detail).toBe("Put app.py back the way it was before Addison changed it.");
    expect(reverted.detail).not.toContain("/fixture");
    // A success carries no `error` at all, so a renderer keying off its presence
    // cannot show one.
    expect(reverted.error).toBeUndefined();
  });
});
