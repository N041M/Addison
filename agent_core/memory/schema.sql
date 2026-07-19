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
    last_run_at     INTEGER
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
    created_at      INTEGER NOT NULL
);
-- Widgets are DECLARATIVE specs only — a saved-routine Run pill or a whitelisted
-- stat display. NEVER code, expressions, or templates; validated at save AND at
-- render (agent_core/widgets.py). See CLAUDE.md invariants.
