// Generated-fixture round trips — the frontend half of the payload-shape drift
// loop. The JSON files under ./fixtures/ are produced by the Python side
// (tests/ipc_fixtures.py) from the REAL core handlers; tests/test_ipc_fixture_drift.py
// fails when a handler drifts from the committed files, and this suite fails when
// the parsers stop surviving those exact shapes. Between the two, a core payload
// change that would break the webview cannot land green.
//
// These complement parsers.test.ts: that file owns the junk/fallback paths; this
// one pins the full parsed output for the genuine article. If a fixture changes,
// regenerate (python tests/ipc_fixtures.py) and update the expectations here
// deliberately — a diff in this file IS the frontend-visible impact of the change.
import { describe, expect, it } from "vitest";

import {
  parseCostPlan,
  parseEndpointProposal,
  parseSnapshotList,
  parseStats,
  parseWidgetList,
  parseWorkspaceRoots,
  parseMcpServers,
} from "../ipc/client";
import { normalizeProfile } from "../lib/parse";
import { normalizeCloudModels, normalizeRoles } from "../hooks/useModelSelection";

import statsFixture from "./fixtures/stats.get.json";
import widgetListFixture from "./fixtures/widget.list.json";
import profileFixture from "./fixtures/profile.get.json";
import rolesFixture from "./fixtures/model.availableRoles.json";
import snapshotListFixture from "./fixtures/snapshot.list.json";
import workspaceListFixture from "./fixtures/workspace.list.json";
import mcpListFixture from "./fixtures/mcp.list.json";
import costPlanFixture from "./fixtures/costPlan.propose.json";
import endpointProposeFixture from "./fixtures/endpoint.proposeFromConversation.json";

describe("parseStats over the real stats.get payload", () => {
  it("pins the full parsed output", () => {
    expect(parseStats(statsFixture)).toEqual({
      tokensMonth: { total: 2610, limit: null },
      providerLatency: [
        { provider: "openai", ms: 720 },
        { provider: "anthropic", ms: 640 },
      ],
      connections: [
        { id: "ollama", label: "Ollama · this computer", status: "idle", detail: "not running" },
        { id: "anthropic", label: "Anthropic API", status: "reachable", detail: "640 ms" },
      ],
    });
  });
});

describe("parseWidgetList over the real widget.list payload", () => {
  it("keeps every widget kind the core sends, with the checklist's ticks, drops nothing", () => {
    expect(parseWidgetList(widgetListFixture)).toEqual([
      {
        id: "widget-fixture-0",
        spec: { kind: "routine", routineId: "routine-morning-brief", title: "Morning brief" },
        pinned: true,
        createdInMode: "safe",
      },
      {
        id: "widget-fixture-1",
        spec: { kind: "stat", source: "tokens_month", title: "Tokens this month" },
        pinned: false,
        createdInMode: "safe",
      },
      {
        id: "widget-fixture-2",
        spec: { kind: "command", command: "git status", title: "Repo status" },
        pinned: false,
        createdInMode: "open",
      },
      {
        id: "widget-fixture-3",
        spec: { kind: "checklist", items: ["Buy milk", "Call Ana"], title: "Saturday" },
        pinned: false,
        createdInMode: "safe",
        // The stored ticks ride on the row, half-set — an all-false array here
        // would mean the parser had dropped the state and defaulted it.
        state: { checked: [true, false] },
      },
    ]);
  });
});

describe("normalizeProfile over the real profile.get payload", () => {
  it("carries the Developer profile, OPEN mode, every flag, and the Custom entry's advanced marker", () => {
    expect(normalizeProfile(profileFixture)).toEqual({
      activeProfile: "developer",
      mode: "open",
      profiles: [
        // The two basic profiles keep their exact serialized shape — no `advanced`
        // key (contract D4/R6). Only Custom carries it, and only because the real
        // payload marks it so; this is where the frontend proves it never invents
        // the disclosure and never leaks it onto Simple/Developer.
        { id: "simple", label: "Simple", description: "Simple — the everyday Addison." },
        {
          id: "developer",
          label: "Developer",
          description: "Developer — extra visibility for technical users. Same safety rules.",
        },
        {
          id: "custom",
          label: "Custom",
          description:
            "Custom — for advanced users. Addison can do everything the Developer profile " +
            "allows, and you choose how often it asks you first. Going back to a working " +
            "setup always stays possible.",
          advanced: true,
        },
      ],
      flags: {
        exposeRoutinePlan: true,
        rawDiagnostics: true,
        headlessCli: true,
        byokFirstOnboarding: true,
      },
    });
  });
});

describe("normalizeRoles / normalizeCloudModels over the real availableRoles payload", () => {
  it("surfaces primary + local with plain labels", () => {
    expect(normalizeRoles(rolesFixture)).toEqual([
      { role: "primary", label: "Cloud", configured: true },
      { role: "local", label: "On this computer", configured: true },
    ]);
  });

  it("carries the full cloud catalog with effort levels", () => {
    expect(normalizeCloudModels(rolesFixture)).toEqual([
      {
        id: "claude-opus-4-8",
        label: "Claude Opus 4.8",
        effortLevels: [
          { id: "low", label: "low" },
          { id: "high", label: "high" },
          { id: "xhigh", label: "xhigh" },
        ],
        default: true,
        provider: "anthropic",
        providerLabel: "Anthropic",
        // The core's to_wire does NOT send `free` for cloud models — every
        // curated cloud model is paid, and the free chip stays Ollama-only. The
        // parser therefore lands on false for all three, which is what makes the
        // composer menu's note read "quality" rather than a guess.
        free: false,
      },
      {
        id: "claude-haiku-4-5-20251001",
        label: "Claude Haiku 4.5",
        effortLevels: [],
        default: false,
        provider: "anthropic",
        providerLabel: "Anthropic",
        free: false,
      },
      {
        id: "gpt-fixture",
        label: "Fixture GPT",
        effortLevels: [],
        default: false,
        provider: "openai",
        providerLabel: "OpenAI",
        free: false,
      },
    ]);
  });
});

describe("parseSnapshotList over the real snapshot.list payload", () => {
  it("pins the full parsed output, permanent row included", () => {
    // Note what is NOT here: no copy of the config, no fingerprint, no build
    // reference. `capturesBinary` is a boolean and that is all the card needs
    // (contract §7.3) — this expectation is where that stays true.
    expect(parseSnapshotList(snapshotListFixture)).toEqual({
      snapshots: [
        {
          id: "snapshot-fixture-2",
          createdAt: 4102444802,
          trigger: "auto",
          reason: "guard_weakened",
          reasonLabel: "Before turning a guard off",
          verifiedWorking: true,
          undeletable: true,
          capturesBinary: true,
          createdInMode: "safe",
        },
        {
          id: "snapshot-fixture-1",
          createdAt: 4102444801,
          trigger: "on_command",
          reason: "user_request",
          reasonLabel: "You saved this",
          verifiedWorking: true,
          undeletable: false,
          capturesBinary: false,
          createdInMode: "safe",
        },
        {
          id: "snapshot-fixture-0",
          createdAt: 4102444800,
          trigger: "auto",
          reason: "mode_switch",
          reasonLabel: "Before switching profile",
          verifiedWorking: false,
          undeletable: false,
          capturesBinary: false,
          createdInMode: "safe",
        },
      ],
      lastWorkingId: "snapshot-fixture-2",
      lastWorkingLabel: "Before turning a guard off",
      // null on the wire means "no profile change", not a sentence to render.
      lastWorkingProfileChange: undefined,
      warning: undefined,
    });
  });
});

// --- Step-4 / step-5 payloads ----------------------------------------------
// These exist because their ABSENCE had a cost. `parseWorkspaceRoots` read
// `{roots}` while the core sent `{folders}`, so the trusted-folder list rendered
// permanently empty in the shipped app — the "Stop trusting" button never
// appeared, and standing consent that suppresses permission cards could not be
// revoked from the UI. Both suites were green: the Python one asserted `folders`,
// the vitest one parsed a hand-built `{roots: […]}` literal, and neither could
// see the other. A fixture generated from the real handler is the one artifact
// both sides share, so every new payload a parser consumes gets one.
describe("parseWorkspaceRoots over the real workspace.list payload", () => {
  it("reads the folders the core actually sends", () => {
    expect(parseWorkspaceRoots(workspaceListFixture)).toEqual([
      { directory: "/fixture/project", grantedAt: 4102444800 },
    ]);
  });
});

describe("parseMcpServers over the real mcp.list payload", () => {
  it("reads the servers the core actually sends, camelCase timestamp included", () => {
    // `addedAt` is the core's `created_at` renamed at the wire boundary — the same
    // class of mapping the roots/folders mismatch hid in. Pinning it against the
    // generated fixture is what makes a rename on either side a red build.
    //
    // Three rows, one per discovery state (step 7 phase 2), because the shape is
    // not one shape: an unchecked row carries no `checkedAt`, a checked one carries
    // its tools and counts, a failed one carries a sentence and no tools. A fixture
    // with only the happy row would let the parser drop `error` or invent a
    // `toolCount` and stay green on both sides.
    expect(parseMcpServers(mcpListFixture)).toEqual([
      {
        id: "mcp-fixture-0",
        name: "Fixture tool server",
        url: "https://tools.example/mcp",
        enabled: true,
        addedAt: 4102444800,
        status: "never",
        checkedAt: undefined,
        toolCount: undefined,
        tools: [],
        skipped: undefined,
        error: undefined,
      },
      {
        id: "mcp-fixture-1",
        name: "Checked server",
        url: "https://checked.example/mcp",
        enabled: true,
        addedAt: 4102444801,
        status: "ok",
        checkedAt: 4102444800,
        toolCount: 2,
        tools: [
          { name: "search_docs", description: "Search the team's documentation." },
          { name: "open_ticket", description: "Open a support ticket." },
        ],
        skipped: 1,
        error: undefined,
      },
      {
        id: "mcp-fixture-2",
        name: "Unreachable server",
        url: "https://offline.example/mcp",
        enabled: true,
        addedAt: 4102444802,
        status: "failed",
        checkedAt: 4102444800,
        toolCount: undefined,
        tools: [],
        skipped: undefined,
        error:
          "Addison couldn't reach that server. Check the address, and that the server is running.",
      },
    ]);
  });

  it("carries no server-authored field the surfaces would have to trust", () => {
    // The core sends names and descriptions and NOTHING else a server wrote — no
    // input schema, no annotations, no URL of its own. This is what keeps "a
    // stranger's text" a bounded problem on the two pages that render it.
    for (const row of mcpListFixture.servers) {
      for (const tool of (row as { tools?: unknown[] }).tools ?? []) {
        expect(Object.keys(tool as object).sort()).toEqual(["description", "name"]);
      }
    }
  });
});

describe("parseCostPlan over the real costPlan.propose payload", () => {
  it("carries the canned name and the FULL instructions the card must show", () => {
    const plan = parseCostPlan(costPlanFixture);
    expect(plan?.skillName).toBe("Addison: keep it cheap");
    // The card renders the whole text — a truncated parse would hide what the
    // person is agreeing to.
    expect(plan?.skillInstructions).toBe(costPlanFixture.skillInstructions);
  });
});

describe("parseEndpointProposal over the real endpoint payload", () => {
  it("fails closed on the core's genuine no-proposal answer", () => {
    // The fixture is generated from an empty conversation, so it is the {none:true}
    // shape — which must produce no card at all.
    expect(parseEndpointProposal(endpointProposeFixture)).toBeNull();
  });
});
