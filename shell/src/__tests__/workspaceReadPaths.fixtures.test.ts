// The review surface's read-path payloads, against the REAL core handlers'
// output — the frontend half of the payload-shape drift loop for Phase-3 Build §1.
//
// The screen that renders these is §4; what §1 ships on this side is the TYPES.
// So this suite pins the fixtures against those types rather than against a parser
// that does not exist yet — which is the useful half of the loop right now: the
// `roots`/`folders` incident shipped because each side asserted its own idea of the
// shape and neither ever read the other's. A fixture nobody reads is the same
// failure with an extra file in it.
//
// Regenerate with `python tests/ipc_fixtures.py`; tests/test_ipc_fixture_drift.py
// fails when a handler drifts from the committed files.
import { describe, expect, it } from "vitest";

import type { WorkspaceEntry, WorkspaceFileView, WorkspaceListing } from "../types/protocol";

import listingFixture from "./fixtures/workspace.listDirectory.json";
import readFixture from "./fixtures/workspace.readFile.json";

// The closed set, written out rather than derived: a kind the core starts sending
// that this list does not know about must land as a red test on this side, and a
// list computed from the fixture would agree with whatever the fixture said.
const KINDS: WorkspaceEntry["kind"][] = ["file", "directory", "symlink", "other"];

describe("workspace.listDirectory over the real payload", () => {
  const listing = listingFixture as WorkspaceListing;

  it("names the folder it listed, the root it sits under, and whether it is complete", () => {
    expect(listing.directory).toBe("/fixture/project");
    expect(listing.root).toBe("/fixture/project");
    expect(listing.truncated).toBe(false);
  });

  it("gives every entry all four fields, with a kind from the closed set", () => {
    expect(listing.entries.length).toBeGreaterThan(0);
    for (const entry of listing.entries) {
      expect(Object.keys(entry).sort()).toEqual(["escapes", "kind", "name", "size"]);
      expect(KINDS).toContain(entry.kind);
      expect(typeof entry.size).toBe("number");
      expect(typeof entry.escapes).toBe("boolean");
    }
  });

  it("carries a symlink AS a symlink, and hides nothing", () => {
    // A link rendered as the kind of its target is a folder the person opens before
    // anything refuses — the core reads kinds without following links precisely so
    // this side never has to guess.
    const kinds = new Map(listing.entries.map((entry) => [entry.name, entry.kind]));
    expect(kinds.get("link")).toBe("symlink");
    // `.git` is listed like everything else. Rendering it collapsed is this side's
    // job; leaving it out would be a lie about what is on disk.
    expect(kinds.get(".git")).toBe("directory");
  });
});

describe("workspace.readFile over the real payload", () => {
  const view = readFixture as WorkspaceFileView;

  it("carries the text, the FILE's size, and whether it was cut short", () => {
    expect(Object.keys(view).sort()).toEqual([
      "bytes",
      "content",
      "path",
      "root",
      "truncated",
    ]);
    expect(view.content).toContain("Fixture project");
    // `bytes` is the file's size on disk, not the length of what came back. They are
    // equal here because nothing was truncated; when they differ, the difference is
    // the whole point, and reading it as "length of content" would silently report a
    // 4 MB file as fully shown.
    expect(view.bytes).toBe(new TextEncoder().encode(view.content).length);
    expect(view.truncated).toBe(false);
  });
});
