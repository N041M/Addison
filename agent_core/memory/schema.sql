-- agent_core/memory/schema.sql
-- Canonical SQLite schema for Addison's local-first storage.
-- See engineering-spec §3. All timestamps are unix epoch seconds.
-- Python dataclasses mirror these tables 1:1 (see the various */model.py files).

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,       -- uuid4
    title           TEXT,
    started_at      INTEGER NOT NULL,       -- unix epoch seconds
    provider_id     TEXT NOT NULL,          -- which ModelProvider was active
    -- §4.8 substrate (v2 Context Budget Manager). Unused by v1 logic:
    summary         TEXT,                   -- condensed older history, set on continuation
    continued_from_conversation_id TEXT REFERENCES conversations(id)  -- lineage
);

CREATE TABLE IF NOT EXISTS messages (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id),
    role                TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
    content             TEXT NOT NULL,       -- text content, or JSON for tool calls/results
    tool_call_id        TEXT,                -- set if role='tool' or this message triggered a tool call
    created_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_facts (
    id                          TEXT PRIMARY KEY,
    fact                        TEXT NOT NULL,
    source_conversation_id      TEXT REFERENCES conversations(id),
    confirmed_by_user           INTEGER NOT NULL DEFAULT 0,   -- boolean
    created_at                  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_grants (
    tool_id         TEXT PRIMARY KEY,       -- e.g. 'web_search'
    granted_at      INTEGER NOT NULL,
    scope_details   TEXT                    -- JSON, tool-specific (e.g. remembered file handles)
);

CREATE TABLE IF NOT EXISTS action_snapshots (
    id                  TEXT PRIMARY KEY,
    tool_call_id        TEXT NOT NULL,
    tool_id             TEXT NOT NULL,
    undo_payload        TEXT NOT NULL,       -- JSON, tool-specific (e.g. {"deleted_file_backup": "..."})
    created_at          INTEGER NOT NULL,
    reverted            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_identity (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    device_id       TEXT NOT NULL,           -- uuid4, public identifier
    -- private key material lives ONLY in the OS keychain (via shell/keychain.rs), never here
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_config (
    provider_id     TEXT PRIMARY KEY
                        CHECK(provider_id IN ('anthropic','openai','google','custom')),
    -- API keys are NEVER stored here. This table only holds non-secret connection
    -- metadata; keys live in the OS keychain (§5, §8.3).
    connected       INTEGER NOT NULL DEFAULT 0,   -- did provider.connect succeed
    added_at        INTEGER,                       -- epoch seconds the key was first connected
    base_url        TEXT,                          -- custom (OpenAI-compatible) server only
    catalog_json    TEXT,                          -- optional cached model catalog for this provider
    last_check_ok   INTEGER,                       -- 1/0/NULL: did the last connect ping pass
    updated_at      INTEGER NOT NULL
);
-- Multi-provider (owner decision 2026-07-18): several providers can be connected
-- at once — anthropic + openai + google + a custom server — and the picker shows
-- every connected provider's models together. See §4.1.1 (ModelRouter).

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);
-- Non-secret key/value app config. Notably 'active_profile' ('simple' |
-- 'developer'), default 'simple' — see §4.7 (Profiles). NEVER holds secrets;
-- API keys live in the OS keychain (§5), never here.

CREATE TABLE IF NOT EXISTS routines (
    id              TEXT PRIMARY KEY,        -- uuid4
    name            TEXT NOT NULL,           -- user-facing, e.g. "Weekly invoice summary"
    description     TEXT NOT NULL,           -- plain-language, shown in the library UI
    plan_json       TEXT NOT NULL,           -- the Routine plan, see §6.2
    created_from_conversation_id TEXT REFERENCES conversations(id),
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    run_count       INTEGER NOT NULL DEFAULT 0,
    last_run_at     INTEGER,
    -- Mode-scoped safety (owner decision 2026-07-19, policy.py): the policy mode
    -- this routine was SAVED under ('safe' | 'open'). A routine created in OPEN
    -- (Developer) mode is HIDDEN and REFUSED in SAFE mode — never listed, never
    -- runnable — and returns untouched when Developer mode is active again.
    created_in_mode TEXT NOT NULL DEFAULT 'safe'
);

CREATE TABLE IF NOT EXISTS routine_runs (
    id              TEXT PRIMARY KEY,
    routine_id      TEXT NOT NULL REFERENCES routines(id),
    started_at      INTEGER NOT NULL,
    completed_at    INTEGER,
    status          TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled')),
    step_log_json   TEXT                     -- array of {step_index, tool_id, result_summary}
);

CREATE TABLE IF NOT EXISTS usage_log (
    id              TEXT PRIMARY KEY,        -- uuid4
    conversation_id TEXT,                    -- which conversation the call belonged to (nullable)
    provider        TEXT NOT NULL,           -- 'anthropic' | 'openai' | 'google' | 'ollama' | 'custom'
    model           TEXT NOT NULL,           -- raw model id used for the call
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    latency_ms      INTEGER,                 -- wall-clock ms of the provider call
    created_at      INTEGER NOT NULL         -- unix epoch seconds
);
-- §4.8 usage substrate: one row per provider call that reported token usage.
-- Written by ORCHESTRATOR MACHINERY (main.py after each turn's model calls),
-- never by a registry tool. Backs the token-meter and provider-latency widgets.

CREATE TABLE IF NOT EXISTS widgets (
    id              TEXT PRIMARY KEY,        -- uuid4
    spec_json       TEXT NOT NULL,           -- a DECLARATIVE widget spec (see agent_core/widgets.py)
    pinned          INTEGER NOT NULL DEFAULT 1,   -- boolean: shown as a card vs. behind the tray
    position        INTEGER NOT NULL DEFAULT 0,   -- user-visible order
    created_at      INTEGER NOT NULL,
    -- Mode-scoped safety (owner decision 2026-07-19): the policy mode this widget
    -- was saved under ('safe' | 'open'). Widgets created in OPEN mode (e.g. a
    -- command widget, or a widget wrapping a dev routine) are hidden in SAFE mode.
    created_in_mode TEXT NOT NULL DEFAULT 'safe'
);
-- Widgets are DECLARATIVE specs only — a saved-routine Run pill or a whitelisted
-- stat display. NEVER code, expressions, or templates; validated at save AND at
-- render (agent_core/widgets.py). See CLAUDE.md invariants.

CREATE TABLE IF NOT EXISTS skills (
    id              TEXT PRIMARY KEY,        -- uuid4
    name            TEXT NOT NULL,           -- user-facing, e.g. "Answer briefly"
    instructions    TEXT NOT NULL,           -- plain-text guidance appended to the system prompt
    enabled         INTEGER NOT NULL DEFAULT 1,   -- boolean: on => steers the next turns
    created_at      INTEGER NOT NULL
);
-- Guidance skills (owner-directed 2026-07-20) are a DECLARATIVE primitive: a named
-- plain-text note the person writes to steer HOW Addison approaches tasks. When
-- enabled, the text is appended to the TRANSIENT per-turn system prompt (never
-- persisted into the transcript). A skill is NOT executable — not a tool, not a
-- routine, no code/eval field — so it respects SAFE-mode invariant 1, and its text
-- can NEVER widen what Addison may DO: the ToolRegistry + PermissionGate stay the
-- sole authority (mirrors the Routine no-escalation rule). Skills therefore apply in
-- BOTH SAFE and OPEN modes and carry no created_in_mode column. See agent_core/skills.py.

CREATE TABLE IF NOT EXISTS config_snapshots (
    id                 TEXT PRIMARY KEY,        -- uuid4
    created_at         INTEGER NOT NULL,        -- unix epoch seconds
    trigger            TEXT NOT NULL
                           CHECK(trigger IN ('auto','on_command')),
    reason             TEXT NOT NULL,           -- closed slug set, see snapshot_manager.REASONS
    payload_version    INTEGER NOT NULL,        -- state_blob format version (currently 1)
    state_blob         TEXT NOT NULL,           -- JSON row-image of the captured tables
    state_fingerprint  TEXT NOT NULL,           -- sha256 of the canonical blob, minus timestamps
    verified_working   INTEGER NOT NULL DEFAULT 0,  -- boolean: a turn completed against this config
    undeletable        INTEGER NOT NULL DEFAULT 0,  -- boolean: 1 = G4 anchor, never removable
    captures_binary    INTEGER NOT NULL DEFAULT 0,  -- boolean: 1 only when binary_ref was obtained
    binary_ref         TEXT,                    -- JSON build reference, anchors only; NEVER bytes
    created_in_mode    TEXT NOT NULL DEFAULT 'safe'
                           CHECK(created_in_mode IN ('safe','open','custom'))
);
-- The backing store for GLOBAL FLOOR G3 (guaranteed rollback) — amendment §3,
-- spec §4.9. DISTINCT from action_snapshots above: that table reverses ONE tool
-- call (§4.5, UndoManager); this one restores Addison's whole mutable
-- CONFIGURATION. The two are complementary and never touch each other.
--
-- Each row is a point-in-time row-image of the captured config tables
-- (app_settings, provider_config, skills, widgets, routines — the authoritative
-- list lives in agent_core/snapshots/scope.py and tests assert that every schema
-- table AND every column of every captured table is either captured or
-- explicitly excluded). Taken AUTOMATICALLY before any risky or sweeping change (a mode
-- switch, a provider/endpoint change, a delete of a saved artifact) and ON
-- COMMAND from Settings. `trigger` records which; `reason` is a short slug from
-- a closed vocabulary (never free text, never model-authored prose).
--
-- HARD RULES (MUST — these are the floor, not preferences):
--   * state_blob NEVER contains API keys or any keychain material (G1). It is
--     built by SELECTing named columns of the tables above, none of which can
--     hold a key; restore leaves the keychain untouched, so a restored provider
--     config re-binds to whatever key is in the keychain by provider id.
--   * The conversation transcript is NOT captured and NOT restored — history is
--     append-only and orthogonal. Neither are usage_log, action_snapshots,
--     memory_facts, device_identity, routine_runs, or this table itself.
--   * verified_working = 1 marks a config a turn actually completed against.
--     Restore ALWAYS targets the newest USABLE verified_working row, never
--     merely "the state before the last edit" (amendment §3.2).
--   * undeletable = 1 names the GUARANTEE THE DELETE PATH ENFORCES, not the
--     provenance (provenance is `reason`). Two kinds of row carry it: the G4
--     anchor minted when a safety guard is turned OFF in Custom mode and saved
--     (reason='guard_weakened'), and the genesis row (reason='genesis'), which is
--     the bottom of the restore walk and must therefore outlive every retention
--     rule. Removable by NEITHER user NOR model; an anchor survives the guard
--     being switched back on; retention pruning skips both. Enforcement is in the
--     DATABASE (the triggers below), not only in a WHERE clause.
--   * created_in_mode is RECORDED FOR DISPLAY ONLY. Unlike routines and widgets,
--     snapshots are NEVER hidden by mode — a user who breaks things in Developer
--     or Custom and returns to Simple must still see and restore every snapshot,
--     or G3 fails in exactly the moment it exists for. No query in the codebase
--     may filter on this column.
--   * This table is NEVER dropped or recreated by a migration. The
--     drop-and-recreate path used for provider_config (store.py
--     _migrate_provider_config) is FORBIDDEN here — it would destroy anchors and
--     break G4. Future column changes use _add_column_if_missing only.
--   * binary_ref holds a short JSON build REFERENCE ({"version","identifier"}),
--     never binary content and never a filesystem path — anchors are unbounded,
--     so they must stay tiny (amendment §13 Q8: "without bloating storage").
--     It records THE BUILD THE ANCHOR WAS MINTED ON, which is not necessarily the
--     build the copied payload was captured on.

-- Permanence, enforced by the DATABASE rather than by a WHERE clause. G4 says
-- "removable by neither user nor model", and a floor that must survive code
-- nobody has written yet cannot live in one method's SQL: prune_config_snapshots
-- is already a SECOND independent DELETE statement that has to remember the same
-- predicate, and there will eventually be a third. With these triggers, no
-- statement can reach a permanent row regardless of its WHERE clause.
CREATE TRIGGER IF NOT EXISTS trg_config_snapshots_permanent_no_delete
BEFORE DELETE ON config_snapshots WHEN OLD.undeletable = 1
BEGIN SELECT RAISE(ABORT, 'this restore point is permanent'); END;

CREATE TRIGGER IF NOT EXISTS trg_config_snapshots_permanent_stays_permanent
BEFORE UPDATE OF undeletable ON config_snapshots
WHEN OLD.undeletable = 1 AND NEW.undeletable = 0
BEGIN SELECT RAISE(ABORT, 'this restore point is permanent'); END;
-- NOTE: a trigger does NOT stop DROP TABLE. The "never drop this table" rule
-- above and test_reopening_never_drops_config_snapshots remain load-bearing —
-- the triggers close the DELETE/UPDATE hole, not the DROP one.

-- Indexes -------------------------------------------------------------------
-- All IF NOT EXISTS so this script stays idempotent on every open (store.py
-- runs it via executescript on both fresh and existing databases). These back
-- the hot read paths: transcript replay (messages_for_conversation) and the
-- §4.8 usage widgets (usage_totals_since, latest_latency_per_provider).
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_log_created
    ON usage_log(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_log_provider_created
    ON usage_log(provider, created_at);
-- Backs list_enabled_skills (the hot read path: every non-setup turn composes the
-- enabled skills into its system prompt).
CREATE INDEX IF NOT EXISTS idx_skills_enabled ON skills(enabled);
-- Backs the G3 read paths: the Settings list (newest first) and, hotter, the
-- "newest usable verified-working row" lookup that restore_last_working() walks.
CREATE INDEX IF NOT EXISTS idx_config_snapshots_created
    ON config_snapshots(created_at);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_verified_created
    ON config_snapshots(verified_working, created_at);
