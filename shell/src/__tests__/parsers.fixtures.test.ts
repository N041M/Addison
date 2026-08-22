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
  parseAutomations,
  parseAutomationMutation,
  parseCostPlan,
  parseEndpointProposal,
  parseSnapshotList,
  parseStats,
  parseWidgetList,
  parseWorkspaceRoots,
  parseMcpServers,
  parseChannels,
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
import channelListFixture from "./fixtures/channel.list.json";
import automationListFixture from "./fixtures/automation.list.json";
// The same method answered while SIMPLE is active — the only fixture whose name is
// not a method name, because the payload has two shapes and no one call can show
// both (tests/ipc_fixtures.py says why at the generator).
import automationListSimpleFixture from "./fixtures/automation.list.simple.json";
// The orphan path's REFUSAL (2026-08-08) — the success is `{ok:true}` and pins
// nothing; this is the shape that carries a sentence somebody reads.
import automationDisarmOrphanFixture from "./fixtures/automation.disarmOrphan.json";
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

  it("turns the core's refusal slug into a sentence, and an unknown one into nothing", () => {
    // `unavailable` carries a `provider_attempts` outcome — `model_gone` is a row
    // in a table, not English. Forwarded as it stands it reaches the picker and is
    // printed under the model's name, which is the app showing a person the word
    // "model_gone" (CLAUDE.md: plain language, no jargon; the readers are 54 and
    // 68). Translating at this boundary is what keeps either panel from rendering
    // one by forgetting to.
    const withSlug = {
      cloudModels: [
        { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", unavailable: "model_gone" },
        { id: "gemini-3-pro", label: "Gemini 3 Pro", unavailable: "invented_later" },
      ],
    };
    const [gone, unknown] = normalizeCloudModels(withSlug);
    expect(gone.unavailable).toMatch(/^This provider answered/);
    expect(gone.unavailable).not.toMatch(/model_gone|_/);
    // A slug this build has never heard of says nothing rather than saying itself:
    // the row still dims and still reads "unavailable" beside its name, which is
    // the part that is true whatever the reason was.
    expect(unknown.unavailable).toBeUndefined();
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
    // Three rows, one per discovery state (step 7), because the shape is
    // not one shape: an unchecked row carries no `checkedAt`, a checked one carries
    // its tools and counts, a failed one carries a sentence and no tools. A fixture
    // with only the happy row would let the parser drop `error` or invent a
    // `toolCount` and stay green on both sides.
    //
    // THE CHECKED SERVER OFFERED TWO TOOLS AND ONE WAS REFUSED — its namespaced id
    // was already taken — so the row below is the honest asymmetry: `toolCount` is 1,
    // `tools` names only what registered, and `skipped` is 2 (one turned away by the
    // client, one by admission). `toolCount` describing what the SERVER offered
    // rather than what a call could reach is the drift this pins.
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
        toolCount: 1,
        tools: [{ name: "search_docs", description: "Search the team's documentation." }],
        skipped: 2,
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

describe("parseChannels over the real channel.list payload", () => {
  it("reads the connections the core actually sends, camelCase names included", () => {
    // Two renames and one derived field, all of them the class of thing the
    // roots/folders mismatch hid in: `created_at` -> `addedAt`, `token_present` ->
    // `tokenPresent`, and `pairedDevices`, which the core COMPUTES from a second
    // table rather than reading off the row. Pinning them against the generated
    // fixture is what makes a rename on either side a red build.
    //
    // Every row is off, unpaired and "unknown", and that is the payload phase 1 can
    // actually produce: nothing turns a channel on and nothing can ask a transport
    // whether a token works. A fixture claiming otherwise would pin this parser
    // against a shape the handler never emits.
    expect(parseChannels(channelListFixture)).toEqual([
      {
        id: "channel-fixture-0",
        kind: "telegram",
        name: "My phone",
        enabled: false,
        tokenPresent: "unknown",
        pairedDevices: 0,
        addedAt: 4102444800,
      },
      {
        id: "channel-fixture-1",
        kind: "telegram",
        name: "The kitchen tablet",
        enabled: false,
        tokenPresent: "unknown",
        pairedDevices: 0,
        addedAt: 4102444801,
      },
    ]);
  });

  it("carries nothing that could be part of a token", () => {
    // G1 at the artifact. The row's whole account of the credential is one word
    // from a three-value vocabulary; a key, a length, a prefix or a masked form
    // would all be a token reaching the webview. This reads the FIXTURE rather than
    // the parser, because a parser that dropped an extra field would keep this
    // green while the core was already sending it over the wire.
    for (const row of channelListFixture.channels) {
      expect(Object.keys(row as object).sort()).toEqual([
        "addedAt",
        "enabled",
        "id",
        "kind",
        "name",
        "pairedDevices",
        "tokenPresent",
      ]);
      expect(["present", "absent", "unknown"]).toContain(
        (row as { tokenPresent: string }).tokenPresent,
      );
    }
  });
});

describe("parseAutomations over the real automation.list payload", () => {
  it("reads the rows the core actually sends, schedule sentence included", () => {
    // THE SENTENCE IS THE POINT OF THIS ONE. The core renders a schedule into words
    // and this side prints them; a fixture generated from the real handler is the
    // only artifact that can catch the two drifting into two different renderings of
    // one schedule — which would show up as a person reading, on the row, a time
    // that is not when their command runs.
    //
    // Three rows, one per meaningful state: an interval whose sentence collapses
    // ("Every hour", not "Every 60 minutes"), a calendar WITH a weekday and a
    // two-digit minute, and a row whose stored schedule is junk — a real state, made
    // by a hand edit or an older build — which must arrive as {} and the core's own
    // "no schedule" line without taking the other two off the list with it.
    expect(parseAutomations(automationListFixture)).toEqual([
      {
        id: "automation-fixture-0",
        name: "Tidy up downloads",
        label: "com.addison.auto.tidy-downloads",
        command: "/usr/bin/find ~/Downloads -mtime +30 -delete",
        scheduleKind: "interval",
        schedule: { minutes: 60 },
        scheduleSentence: "Every hour",
        createdInMode: "open",
        createdAt: 4102444800,
      },
      {
        id: "automation-fixture-1",
        name: "Back up notes",
        label: "com.addison.auto.backup-notes",
        command: "/usr/local/bin/backup-notes --to ~/Backups",
        scheduleKind: "calendar",
        schedule: { hour: 7, minute: 30, weekday: 1 },
        scheduleSentence: "Every Monday at 7:30",
        createdInMode: "open",
        createdAt: 4102444801,
      },
      {
        id: "automation-fixture-2",
        name: "Something older",
        label: "com.addison.auto.something-older",
        command: "/usr/bin/say hello",
        scheduleKind: "interval",
        schedule: {},
        scheduleSentence: "No schedule saved yet.",
        createdInMode: "open",
        createdAt: 4102444802,
      },
    ]);
  });

  it("carries nothing that could be handed to the operating system", () => {
    // The shell builds the job file itself, from typed fields (plan §5.8) — so no
    // payload may normalise carrying a document, and no row may claim to be armed.
    // The core pins this at its own boundary; this is the same line drawn where the
    // webview would be the one carrying it.
    for (const row of automationListFixture.automations) {
      for (const key of Object.keys(row)) {
        expect(key).not.toMatch(/plist|xml|arm|install|running/i);
      }
      expect(JSON.stringify(row)).not.toContain("<?xml");
    }
  });

  it("keeps the marker the core puts on every row while Simple is active", () => {
    // THE OTHER SHAPE OF THE SAME PAYLOAD (step 8 phase 4). `unavailable` is on every
    // row or on none — an automation's payload is a shell command, so Simple can use
    // none of them — which is why the core generates a second file rather than a
    // second row. What this pins is that the sentence a Simple person reads on their
    // own saved work travelled from the core untouched: the surface renders this
    // string and never writes one of its own, so the row and the refusal cannot drift
    // into telling two stories.
    const rows = parseAutomations(automationListSimpleFixture);
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(row.unavailable).toEqual({
        reason: "developer_abilities",
        message: "That automation runs a command, so it's waiting in Developer profile.",
      });
    }
    // Everything else about a row is unchanged by the profile — the same ids, the
    // same commands, the same sentences. A profile decides what may be DONE with a
    // row, never what the row is.
    expect(rows.map(({ unavailable: _unavailable, ...rest }) => rest)).toEqual(
      parseAutomations(automationListFixture),
    );
  });

  it("carries the core's own refusal when a switch-off is not Addison's to make", () => {
    // `automation.disarmOrphan` is the one way to stop a job a G3 restore orphaned,
    // and it refuses any label Addison did not mint. The SENTENCE is the payload's
    // only content, and the section prints it verbatim in preference to anything it
    // would say itself — so a core that stopped sending one would leave somebody
    // pressing a button that appears to do nothing at all.
    expect(parseAutomationMutation(automationDisarmOrphanFixture)).toEqual({
      ok: false,
      error:
        "Addison can only switch off the automations it set up itself, so it didn't " +
        "switch that one off.",
    });
    // And nothing else rides back: not the label that was sent, not what the OS holds.
    // A refusal is an answer about one request, never a second listing surface.
    expect(Object.keys(automationDisarmOrphanFixture).sort()).toEqual(["error", "ok"]);
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
