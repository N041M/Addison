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

import { parseSnapshotList, parseStats, parseWidgetList } from "../ipc/client";
import { normalizeProfile } from "../lib/parse";
import { normalizeCloudModels, normalizeRoles } from "../hooks/useModelSelection";

import statsFixture from "./fixtures/stats.get.json";
import widgetListFixture from "./fixtures/widget.list.json";
import profileFixture from "./fixtures/profile.get.json";
import rolesFixture from "./fixtures/model.availableRoles.json";
import snapshotListFixture from "./fixtures/snapshot.list.json";

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
  it("keeps all three OPEN-mode widget kinds, drops nothing", () => {
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
      },
      {
        id: "claude-haiku-4-5-20251001",
        label: "Claude Haiku 4.5",
        effortLevels: [],
        default: false,
        provider: "anthropic",
        providerLabel: "Anthropic",
      },
      {
        id: "gpt-fixture",
        label: "Fixture GPT",
        effortLevels: [],
        default: false,
        provider: "openai",
        providerLabel: "OpenAI",
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
