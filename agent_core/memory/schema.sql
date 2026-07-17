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
    role            TEXT PRIMARY KEY CHECK(role IN ('primary','local','setup_assistant')),
    provider_id     TEXT NOT NULL,        -- 'anthropic', 'openai', 'ollama', 'setup_assistant_relay'
    -- API keys are NEVER stored here. This table only holds non-secret config
    -- (selected model name, Ollama base URL, etc.). Keys live in OS keychain.
    config_json     TEXT,
    updated_at      INTEGER NOT NULL
);
-- Unlike a single "active provider" flag, multiple roles can be configured
-- and populated simultaneously — e.g. role='primary' -> Anthropic, AND
-- role='local' -> Ollama, both present at once. See §4.1.1 (ModelRouter).

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
