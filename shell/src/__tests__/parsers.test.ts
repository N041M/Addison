// Contract-drift guard for the frontend's defensive parsers (maintainability
// review 2026-07-19, item 4). Each parser is exercised BOTH ways:
//   (a) round-trip with a realistic payload shaped like what agent_core actually
//       sends (field names taken from agent_core/main.py's stats.get,
//       widget.list, profile.get, and model.availableRoles handlers), and
//   (b) the documented fallback paths — null / missing / wrong-typed / unknown
//       enum / junk — which must degrade, never throw.
//
// These parsers are the only thing standing between a shifted core payload and a
// crashed webview, so the assertions pin the FULL parsed output, not just a spot
// check.

import { describe, it, expect } from "vitest";
import { parseStats, parseWidgetList } from "../ipc/client";
import { normalizeProfile } from "../lib/parse";
import { normalizeRoles, normalizeCloudModels } from "../hooks/useModelSelection";

// ---------------------------------------------------------------------------
// parseStats — mirrors main.py `_stats_get` + `_connections`.
// ---------------------------------------------------------------------------
describe("parseStats", () => {
  it("round-trips a realistic stats.get payload", () => {
    const wire = {
      tokensMonth: { total: 12345, limit: null },
      providerLatency: [
        { provider: "anthropic", ms: 812 },
        { provider: "openai", ms: 430 },
      ],
      connections: [
        { id: "ollama", label: "Ollama · this computer", status: "idle", detail: "not running" },
        { id: "anthropic", label: "Anthropic API", status: "reachable", detail: "812 ms" },
      ],
    };
    expect(parseStats(wire)).toEqual({
      tokensMonth: { total: 12345, limit: null },
      providerLatency: [
        { provider: "anthropic", ms: 812 },
        { provider: "openai", ms: 430 },
      ],
      connections: [
        { id: "ollama", label: "Ollama · this computer", status: "idle", detail: "not running" },
        { id: "anthropic", label: "Anthropic API", status: "reachable", detail: "812 ms" },
      ],
    });
  });

  it("keeps a numeric monthly limit when the core sends one", () => {
    expect(parseStats({ tokensMonth: { total: 10, limit: 1000 } })).toEqual({
      tokensMonth: { total: 10, limit: 1000 },
      providerLatency: [],
      connections: [],
    });
  });

  it("falls back to an empty, zeroed picture for null/junk input", () => {
    const empty = { tokensMonth: { total: 0, limit: null }, providerLatency: [], connections: [] };
    expect(parseStats(null)).toEqual(empty);
    expect(parseStats(undefined)).toEqual(empty);
    expect(parseStats("nonsense")).toEqual(empty);
    expect(parseStats(42)).toEqual(empty);
    expect(parseStats({})).toEqual(empty);
  });

  it("zeroes missing/wrong-typed token totals", () => {
    expect(parseStats({ tokensMonth: { total: "lots", limit: "none" } }).tokensMonth).toEqual({
      total: 0,
      limit: null,
    });
    expect(parseStats({ tokensMonth: null }).tokensMonth).toEqual({ total: 0, limit: null });
  });

  it("drops latency rows missing a string provider or numeric ms", () => {
    const parsed = parseStats({
      providerLatency: [
        { provider: "anthropic", ms: 100 }, // kept
        { provider: "openai" }, // no ms -> dropped
        { ms: 50 }, // no provider -> dropped
        { provider: "x", ms: "slow" }, // ms not a number -> dropped
        "garbage", // not an object -> dropped
      ],
    });
    expect(parsed.providerLatency).toEqual([{ provider: "anthropic", ms: 100 }]);
  });

  it("coerces unknown connection status to idle and fills label/detail fallbacks", () => {
    const parsed = parseStats({
      connections: [
        { id: "custom", status: "on-fire", detail: 7 }, // unknown status, non-string detail, no label
        { label: "no id here", status: "running" }, // no id -> dropped
        { id: "ok", label: "Fine", status: "running", detail: "running" }, // fully valid
      ],
    });
    expect(parsed.connections).toEqual([
      { id: "custom", label: "custom", status: "idle", detail: "" },
      { id: "ok", label: "Fine", status: "running", detail: "running" },
    ]);
  });
});

// ---------------------------------------------------------------------------
// parseWidgetList — mirrors main.py `_widget_list` (the `position` field is
// intentionally dropped by the parser; `createdInMode` drives the DEV tag).
// ---------------------------------------------------------------------------
describe("parseWidgetList", () => {
  it("round-trips a realistic widget.list payload (all three kinds)", () => {
    const wire = {
      widgets: [
        {
          id: "w1",
          spec: { kind: "routine", routineId: "r1", title: "Morning digest" },
          pinned: true,
          position: 0,
          createdInMode: "safe",
        },
        {
          id: "w2",
          spec: { kind: "stat", source: "tokens_month", title: "Tokens this month" },
          pinned: false,
          position: 1,
          createdInMode: "safe",
        },
        {
          id: "w3",
          spec: { kind: "command", command: "ls -la", title: "List files" },
          pinned: true,
          position: 2,
          createdInMode: "open",
        },
      ],
    };
    expect(parseWidgetList(wire)).toEqual([
      {
        id: "w1",
        spec: { kind: "routine", routineId: "r1", title: "Morning digest" },
        pinned: true,
        createdInMode: "safe",
      },
      {
        id: "w2",
        spec: { kind: "stat", source: "tokens_month", title: "Tokens this month" },
        pinned: false,
        createdInMode: "safe",
      },
      {
        id: "w3",
        spec: { kind: "command", command: "ls -la", title: "List files" },
        pinned: true,
        createdInMode: "open",
      },
    ]);
  });

  it("defaults pinned to true and accepts snake_case created_in_mode", () => {
    const parsed = parseWidgetList({
      widgets: [
        {
          id: "w1",
          spec: { kind: "stat", source: "connections", title: "Connections" },
          created_in_mode: "open",
        },
      ],
    });
    expect(parsed).toEqual([
      {
        id: "w1",
        spec: { kind: "stat", source: "connections", title: "Connections" },
        pinned: true,
        createdInMode: "open",
      },
    ]);
  });

  it("falls back to an empty list for null/junk input", () => {
    expect(parseWidgetList(null)).toEqual([]);
    expect(parseWidgetList("nope")).toEqual([]);
    expect(parseWidgetList({})).toEqual([]);
    expect(parseWidgetList({ widgets: "not-an-array" })).toEqual([]);
  });

  it("drops rows with no string id or an unrenderable spec", () => {
    const parsed = parseWidgetList({
      widgets: [
        { spec: { kind: "stat", source: "connections", title: "x" } }, // no id
        { id: "a", spec: { kind: "routine", title: "no routineId" } }, // missing routineId
        { id: "b", spec: { kind: "stat", source: "made_up_source", title: "x" } }, // source off whitelist
        { id: "c", spec: { kind: "command", title: "no command" } }, // missing command
        { id: "d", spec: { kind: "mystery", title: "x" } }, // unknown kind
        { id: "e", spec: { kind: "stat", source: "tokens_month" } }, // missing title
        { id: "f", spec: "not-an-object" }, // spec not a record
      ],
    });
    expect(parsed).toEqual([]);
  });

  it("marks createdInMode undefined for an unknown mode value", () => {
    const parsed = parseWidgetList({
      widgets: [
        {
          id: "w1",
          spec: { kind: "stat", source: "provider_latency", title: "Latency" },
          pinned: false,
          createdInMode: "sideways",
        },
      ],
    });
    expect(parsed[0].createdInMode).toBeUndefined();
    expect(parsed[0].pinned).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// normalizeProfile — mirrors main.py `_profile_get`. A partial/missing payload
// must degrade to the protected Simple/SAFE surface, never expose Developer
// affordances.
// ---------------------------------------------------------------------------
describe("normalizeProfile", () => {
  it("round-trips a realistic profile.get payload (Developer/OPEN)", () => {
    const wire = {
      activeProfile: "developer",
      mode: "open",
      profiles: [
        { id: "simple", label: "Simple", description: "Approachable by default." },
        { id: "developer", label: "Developer", description: "Power on request." },
      ],
      flags: {
        exposeRoutinePlan: true,
        rawDiagnostics: true,
        headlessCli: true,
        byokFirstOnboarding: true,
      },
    };
    expect(normalizeProfile(wire)).toEqual({
      activeProfile: "developer",
      mode: "open",
      profiles: [
        { id: "simple", label: "Simple", description: "Approachable by default." },
        { id: "developer", label: "Developer", description: "Power on request." },
      ],
      flags: {
        exposeRoutinePlan: true,
        rawDiagnostics: true,
        headlessCli: true,
        byokFirstOnboarding: true,
      },
    });
  });

  it("returns null for a non-object payload", () => {
    expect(normalizeProfile(null)).toBeNull();
    expect(normalizeProfile(undefined)).toBeNull();
    expect(normalizeProfile("simple")).toBeNull();
    expect(normalizeProfile(7)).toBeNull();
  });

  it("degrades a partial payload to Simple/SAFE with all flags off", () => {
    expect(normalizeProfile({})).toEqual({
      activeProfile: "simple",
      mode: "safe",
      profiles: [],
      flags: {
        exposeRoutinePlan: false,
        rawDiagnostics: false,
        headlessCli: false,
        byokFirstOnboarding: false,
      },
    });
  });

  it("falls back to SAFE for an unknown/missing mode and never over-permits", () => {
    expect(normalizeProfile({ mode: "wide-open" }).mode).toBe("safe");
    expect(normalizeProfile({ mode: 1 }).mode).toBe("safe");
    expect(normalizeProfile({ activeProfile: "developer" }).mode).toBe("safe");
  });

  it("ignores truthy-but-non-true flag values (only strict true enables)", () => {
    const parsed = normalizeProfile({
      flags: { exposeRoutinePlan: 1, rawDiagnostics: "yes", headlessCli: {}, byokFirstOnboarding: "true" },
    });
    expect(parsed.flags).toEqual({
      exposeRoutinePlan: false,
      rawDiagnostics: false,
      headlessCli: false,
      byokFirstOnboarding: false,
    });
  });

  it("drops profile options without a string id and fills label/description", () => {
    const parsed = normalizeProfile({
      profiles: [
        { id: "simple" }, // label/description fall back
        { label: "no id" }, // dropped
        "garbage", // dropped
        { id: "developer", label: "Developer", description: "d" },
      ],
    });
    expect(parsed.profiles).toEqual([
      { id: "simple", label: "simple", description: "" },
      { id: "developer", label: "Developer", description: "d" },
    ]);
  });
});

// ---------------------------------------------------------------------------
// normalizeRoles / normalizeCloudModels — mirror main.py `_available_roles`.
// The core sends `roles` as bare strings alongside separate `localModels` /
// `cloudModels`; the parser also tolerates object-shaped role entries.
// ---------------------------------------------------------------------------
describe("normalizeRoles", () => {
  it("round-trips the realistic string-array roles from available_roles", () => {
    const wire = {
      roles: ["primary", "local"],
      localModels: ["llama3.1"],
      cloudModels: [],
    };
    expect(normalizeRoles(wire)).toEqual([
      { role: "primary", label: "Cloud", configured: true },
      { role: "local", label: "On this computer", configured: true },
    ]);
  });

  it("accepts a bare top-level array of role strings", () => {
    expect(normalizeRoles(["primary"])).toEqual([
      { role: "primary", label: "Cloud", configured: true },
    ]);
  });

  it("drops roles that aren't user-selectable (e.g. setup_assistant)", () => {
    expect(normalizeRoles({ roles: ["primary", "setup_assistant", "local"] })).toEqual([
      { role: "primary", label: "Cloud", configured: true },
      { role: "local", label: "On this computer", configured: true },
    ]);
  });

  it("attaches and normalizes models from an object role entry (models or localModels)", () => {
    const parsed = normalizeRoles({
      roles: [
        { role: "local", label: "On device", configured: true, models: ["m1", { id: "m2", label: "Two" }] },
      ],
    });
    expect(parsed).toEqual([
      {
        role: "local",
        label: "On device",
        configured: true,
        models: [
          { id: "m1", label: "m1" },
          { id: "m2", label: "Two" },
        ],
      },
    ]);
    // `localModels` is accepted as an alias, and a model's `name` fills the id.
    const aliased = normalizeRoles({ roles: [{ id: "local", localModels: [{ name: "n1" }] }] });
    expect(aliased).toEqual([
      { role: "local", label: "On this computer", configured: true, models: [{ id: "n1", label: "n1" }] },
    ]);
  });

  it("honors configured: false", () => {
    expect(normalizeRoles({ roles: [{ role: "primary", configured: false }] })).toEqual([
      { role: "primary", label: "Cloud", configured: false },
    ]);
  });

  it("returns an empty list for junk / missing roles", () => {
    expect(normalizeRoles(null)).toEqual([]);
    expect(normalizeRoles({})).toEqual([]);
    expect(normalizeRoles({ roles: "nope" })).toEqual([]);
    expect(normalizeRoles("garbage")).toEqual([]);
  });
});

describe("normalizeCloudModels", () => {
  it("round-trips a realistic cloudModels entry (to_wire shape)", () => {
    const wire = {
      cloudModels: [
        {
          id: "claude-opus-4-8",
          label: "Claude Opus 4.8",
          description: "",
          effortLevels: [
            { id: "low", label: "low", default: false },
            { id: "high", label: "high", default: true },
            { id: "xhigh", label: "xhigh", default: false },
          ],
          default: true,
          provider: "anthropic",
          providerLabel: "Anthropic",
        },
      ],
    };
    // The parser keeps id/label + {id,label} effort levels and drops the wire's
    // `description` and per-level `default`.
    expect(normalizeCloudModels(wire)).toEqual([
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
    ]);
  });

  it("falls back cleanly for missing fields and junk", () => {
    expect(normalizeCloudModels(null)).toEqual([]);
    expect(normalizeCloudModels({})).toEqual([]);
    expect(normalizeCloudModels({ cloudModels: "nope" })).toEqual([]);
    // No effortLevels -> empty; `name` fills id; label falls back to id; default false.
    expect(normalizeCloudModels({ cloudModels: [{ name: "m1" }, { label: "no id" }] })).toEqual([
      { id: "m1", label: "m1", effortLevels: [], default: false, provider: undefined, providerLabel: undefined },
    ]);
  });
});
