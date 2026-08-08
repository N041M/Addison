// The review surface's read-path payloads, against the REAL core handlers'
// output — the frontend half of the payload-shape drift loop for Phase-3 Build §1.
//
// §1 shipped the types and this suite pinned the fixtures against them, because
// the screen that renders them was still §4 and a parser did not exist yet. §4 has
// landed, so the CONSUMERS now exist and the last section of this file runs them
// over these same payloads — which is the whole loop closed: the `roots`/`folders`
// incident shipped because each side asserted its own idea of the shape and neither
// ever read the other's. A fixture nobody parses is that failure with an extra file
// in it.
//
// Regenerate with `python tests/ipc_fixtures.py`; tests/test_ipc_fixture_drift.py
// fails when a handler drifts from the committed files.
import { describe, expect, it } from "vitest";

import { parseWorkspaceFileView, parseWorkspaceListing } from "../ipc/client";
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

// ---------------------------------------------------------------------------
// The parsers, over the same payloads (§4 — the consumers now exist)
// ---------------------------------------------------------------------------

describe("the read-path parsers against the real payloads", () => {
  it("parses the listing the core actually sends, entry for entry", () => {
    // The other half of the drift loop: not "does this fixture match a type" but
    // "does the code that renders it read this shape". Kills a parser keyed on any
    // other field names — the `roots`/`folders` failure mode, where both suites
    // were green and the surface was permanently empty.
    const answer = parseWorkspaceListing(listingFixture);
    expect(answer.error).toBeUndefined();
    expect(answer.value!.directory).toBe("/fixture/project");
    expect(answer.value!.entries.map((e) => e.name)).toEqual([
      ".git",
      "README.md",
      "link",
      "src",
    ]);
    expect(answer.value!.entries.map((e) => e.kind)).toEqual([
      "directory",
      "file",
      "symlink",
      "directory",
    ]);
    expect(answer.value!.entries.every((e) => e.escapes === false)).toBe(true);
  });

  it("parses the viewer's answer, keeping the FILE's size", () => {
    const answer = parseWorkspaceFileView(readFixture);
    expect(answer.error).toBeUndefined();
    expect(answer.value!.content).toContain("Fixture project");
    expect(answer.value!.bytes).toBe(18);
    expect(answer.value!.truncated).toBe(false);
  });
});

describe("the read-path parsers fail CLOSED", () => {
  it("forwards a refusal as the core's own sentence, never as an empty listing", () => {
    // A folder that refuses and a folder that is empty must not look the same. The
    // refusals are already plain, already-user-ready sentences, so they are shown
    // whole rather than replaced.
    const refused = parseWorkspaceListing({
      ok: false,
      error: "That folder isn't one you've told Addison it may work in.",
    });
    expect(refused.value).toBeUndefined();
    expect(refused.error).toBe("That folder isn't one you've told Addison it may work in.");
    // And a shape nobody can read is an error too, not a blank folder.
    expect(parseWorkspaceListing(null).error).toBe("Addison couldn't read that just now.");
    expect(parseWorkspaceListing({ entries: [] }).error).toBeTruthy();
  });

  it("treats an unreadable `escapes` as ESCAPING", () => {
    // The direction that DIMS a row rather than the one that invites a click. The
    // boundary is the core refusing the follow-up call; this is what stops somebody
    // reaching it. Kills `escapes: row.escapes === true`, which is the same line
    // written the wrong way round.
    const answer = parseWorkspaceListing({
      directory: "/p",
      entries: [
        { name: "a", kind: "file", size: 1 },
        { name: "b", kind: "file", size: 1, escapes: false },
      ],
    });
    expect(answer.value!.entries.map((e) => e.escapes)).toEqual([true, false]);
  });

  it("never turns an unknown kind into a folder", () => {
    // Kills a permissive kind coercion. "directory" is the one value that makes a
    // row EXPANDABLE, so a garbled kind must never land on it.
    const answer = parseWorkspaceListing({
      directory: "/p",
      entries: [
        { name: "a", kind: "fifo", size: 0, escapes: false },
        { name: "b", size: 0, escapes: false },
      ],
    });
    expect(answer.value!.entries.map((e) => e.kind)).toEqual(["other", "other"]);
  });

  it("shows an EMPTY file rather than calling it unreadable", () => {
    // `""` is a real answer and a truthiness check would reject it — a file that is
    // genuinely empty would report a refusal that never happened.
    const answer = parseWorkspaceFileView({ path: "/p/empty", content: "", bytes: 0 });
    expect(answer.error).toBeUndefined();
    expect(answer.value!.content).toBe("");
  });
});
