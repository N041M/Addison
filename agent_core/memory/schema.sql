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

-- Workspace trust (step 5) — directories the user has trusted for the OPEN-mode
-- coding harness. Inside a trusted root a typed, undoable file edit skips the
-- per-change card (the edit is still logged and reversible); commands still card
-- every time. The root is canonicalized (realpath) at grant time, so the caller's
-- confinement check (rpc/workspace.is_trusted) is realpath-vs-realpath. EXCLUDED
-- from snapshots (snapshots/scope.py) like tool_grants: trust is standing consent,
-- and a restore reinstating a trust the user had revoked would be a privilege grant
-- delivered by the ungated one-action restore button.
CREATE TABLE IF NOT EXISTS workspace_trust (
    root        TEXT PRIMARY KEY,       -- canonical absolute directory path
    granted_at  INTEGER NOT NULL
);

-- External MCP servers Addison consumes as a CLIENT (step 7, phase 1). Addison is
-- never an MCP server or gateway. A row here is INERT CONFIGURATION: phase 1 ships
-- no protocol client, no tool discovery and no dispatch, so nothing in the tree
-- reads this table to reach anything. Discovery is phase 2 (docs/step-7-mcp-plan.md,
-- which owns the phase order).
--
-- TRANSPORT IS HTTP ONLY for v1 (owner decision 2026-08-06), which is why the row
-- holds a `url` and NEVER a command. stdio would mean launching an arbitrary
-- executable from the Agent Core — a process with no OS permissions of its own and
-- no seatbelt around it — so the column that would carry a launch command does not
-- exist, and `transport` is CHECK-constrained to the one value that needs none.
-- Widening that vocabulary is a deliberate migration, not an insert.
--
-- The URL is validated where it is STORED (agent_core/rpc/mcp.py, `_mcp_url_problem`),
-- on the `provider_config.base_url` precedent and for the same G1 reason: this table
-- is snapshot-CAPTURED (snapshots/scope.py), so whatever lands here is copied into
-- `config_snapshots.state_blob` and the plaintext sidecars, forever. https:// in
-- general; http:// only for a server on this computer (loopback), which is the
-- narrowed form of the ONE plain-http case rpc/providers.py already permits.
--
-- No token, no header, no credential column — and that is not an oversight: any
-- secret an MCP server needs belongs in the OS keychain (G1), never here. Phase 1
-- connects to nothing, so it needs no token at all; see the plan's §4.
--
-- `name` is the person's own plain label and is UNIQUE (case-insensitively, via the
-- index below). Phase 2 namespaces every discovered tool as `mcp:<server>:<tool>`
-- and must refuse a collision rather than replace, so two servers sharing a name
-- would put two strangers' tools behind one id.
CREATE TABLE IF NOT EXISTS mcp_servers (
    id          TEXT PRIMARY KEY,        -- uuid4
    name        TEXT NOT NULL,           -- plain user-given label, e.g. "Design docs"
    url         TEXT NOT NULL,           -- streamable-HTTP endpoint. NEVER a command.
    transport   TEXT NOT NULL DEFAULT 'http'
                    CHECK(transport IN ('http')),
    -- Rows are created enabled. There is no toggle RPC in phase 1 because there is
    -- nothing yet to disable; the column exists so phase 2 can stop consuming a
    -- server without the person losing its configuration.
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_servers_name
    ON mcp_servers(name COLLATE NOCASE);

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
    -- Is a key SAVED for this provider (plan §4.1)? 'present' | 'absent' | 'unknown'.
    -- Non-secret by construction: it records WHETHER a key exists, never any part of
    -- one. This column — not the OS keychain — is the authority for every presence
    -- question with no person behind it, which is what retired the 60-second keychain
    -- poll. 'unknown' MUST never read as "no key": see agent_core/secret_presence.py.
    -- Distinct from `connected`, which records whether provider.connect's validating
    -- request PASSED — a saved-but-rejected key is present + not connected.
    secret_presence TEXT NOT NULL DEFAULT 'unknown'
                        CHECK(secret_presence IN ('present','absent','unknown')),
    -- When this provider DEFINITIVELY rejected the saved key (plan §5.2): epoch
    -- seconds of the first 401/403 since the last successful connect, or NULL for
    -- "not rejected". Cleared when provider.connect passes, which is the person
    -- adding a working key.
    --
    -- A THIRD signal, deliberately, and not either of the two above:
    --   * NOT `connected` — an auth failure mid-conversation must not silently
    --     disconnect a provider; `connected` gates the reconnect path.
    --   * NOT `secret_presence` — a rejected key is PRESENT and rejected. Writing
    --     'absent' here would make may_reach_setup_relay() true and route the
    --     person's next message to the external Setup Assistant relay while their
    --     key sits in the keychain: the 2026-07-25 bug, reached by a new road.
    --   * NOT `last_check_ok`, which was the obvious candidate and is wrong. It
    --     answers "did the last CONNECT PING pass", and every write of 0 to it is
    --     paired with connected = 0 — so a reader could not tell "never connected"
    --     from "connected, then revoked", which is the only state that earns the
    --     sentence. It also has nowhere to record that the person has been TOLD,
    --     and telling them once is half the requirement.
    -- A timestamp rather than a flag: non-NULL IS the "already told them" latch, so
    -- repeated 401s cannot re-notify, and "when" is worth having in a support
    -- screenshot. NOT captured by snapshots (snapshots/scope.py) — like
    -- secret_presence it is an observation about the live world, not configuration.
    key_rejected_at INTEGER,
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
-- Widgets are DECLARATIVE specs only — a saved-routine Run pill, a whitelisted
-- stat display, or one of the three interactive SAFE kinds (checklist, note,
-- timer). NEVER code, expressions, or templates; validated at save AND at
-- render (agent_core/widgets.py). See CLAUDE.md invariants.

-- Per-widget STATE (Phase-2 step 6, half A) --------------------------------
-- What the PERSON has since done with a widget: a ticked box, an edited note, a
-- paused timer. Deliberately a SEPARATE TABLE rather than a mutation of
-- widgets.spec_json, for two reasons:
--
--   * The spec is what was DECLARED — it is validated at save and again at every
--     render, and the whole safety argument for widgets is that a stored spec
--     can be re-judged against the current mode. State is what has HAPPENED
--     since. Writing state into spec_json would conflate the two and make every
--     tick a re-validation of the declaration.
--   * `widgets` is snapshot-captured (snapshots/scope.py) and this table is NOT.
--     Ticks are user content, like memory_facts: restoring a CONFIGURATION must
--     never un-tick somebody's shopping list. Keeping them in spec_json would
--     have put user content inside a captured table with no way to separate it.
--
-- state_json is validated per KIND, server-side, by widgets.validate_widget_state
-- on the way in AND on the way out; a state that no longer matches its spec (a
-- checklist whose length disagrees) is dropped at render, never guessed at.
-- No FK cascade on purpose: apply_config_state deletes and reinserts `widgets`
-- wholesale during a restore, and ON DELETE CASCADE would wipe the state of
-- every widget that survives it. Orphans are cleaned explicitly instead (store.py).
CREATE TABLE IF NOT EXISTS widget_state (
    widget_id   TEXT PRIMARY KEY REFERENCES widgets(id),
    state_json  TEXT NOT NULL,           -- per-kind, see agent_core/widgets.py
    updated_at  INTEGER NOT NULL
);

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
--     provenance (provenance is `reason`). THREE kinds of row carry it: the G4
--     anchor minted when a safety guard is turned OFF in Custom mode and saved
--     (reason='guard_weakened'), and the two possible bottom rows of the restore
--     walk — genesis (reason='genesis') on a fresh install, pre_upgrade
--     (reason='pre_upgrade') on a database predating this subsystem. _ensure_genesis
--     writes the flag in BOTH of those branches, because whichever one this install
--     got is the floor the walk rests on and must outlive every retention rule.
--     Removable by NEITHER user NOR model; an anchor survives the guard being
--     switched back on; retention pruning skips all three. Enforcement is in the
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

-- The tool-call audit trail (step 5.5, item 4) -------------------------------
-- Every tool DECISION, on every branch, including the ones that never ran. It
-- exists because the shipped record had a hole exactly where it mattered most:
-- `read_web_page` is LOW risk, so it writes no `action_snapshots` row — the tool
-- most exposed to prompt injection left no persistent trace of which URLs it
-- fetched. A refusal left none either, so "Addison declined something" was
-- unanswerable after the fact.
--
-- `detail` is the SAME value the permission card and the Activity Panel already
-- show (tools/base.call_permission_detail), capped and deliberately narrow —
-- read_web_page contributes a HOST, never a full URL. No tool output lands here.
--
-- ARGUMENTS DO, THOUGH, for one tool: run_command's detail is the command text
-- itself, so `export ANTHROPIC_API_KEY=… && ./deploy.sh` arrives here verbatim.
-- This comment used to say "never a secret" and that was simply false. Every
-- `detail` is now run through agent_core/redaction.py inside
-- Store.insert_tool_audit, before the row exists. That is a backstop and not a
-- boundary: a KNOWN secret shape never persists here; an unenumerated one still
-- can. Do not restore the stronger sentence.
--
-- EXCLUDED from snapshots (snapshots/scope.py) on the `tool_grants` precedent:
-- an audit log is history, and a restore that rewrote the record of what
-- happened would be worse than having no record at all.
CREATE TABLE IF NOT EXISTS tool_audit (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT,                    -- NULL for a routine/widget run
    tool_id         TEXT NOT NULL,
    detail          TEXT,                    -- call_permission_detail, redacted on write
    mode            TEXT NOT NULL CHECK(mode IN ('safe','open')),
    destructive     INTEGER NOT NULL DEFAULT 0,
    -- granted      = the gate said yes (auto-allow or the person approved)
    -- denied       = the person said "not now"
    -- forbidden    = the hardline denylist refused it before the gate (item 3)
    -- confined_out = outside every trusted root (step 5 confinement)
    -- dev_only     = a dev tool named outside OPEN mode
    outcome         TEXT NOT NULL
                        CHECK(outcome IN ('granted','denied','forbidden',
                                          'confined_out','dev_only')),
    -- What the redactor removed from THIS call's output on its way to the model
    -- (kinds only, comma-separated, e.g. "AWS access key"). Empty = nothing
    -- matched. This is the only durable evidence that a leak was caught.
    redacted        TEXT,
    created_at      INTEGER NOT NULL
);

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
CREATE INDEX IF NOT EXISTS idx_tool_audit_created
    ON tool_audit(created_at);

-- Provider attempts that FAILED (2026-08-07). The mirror of tool_audit for the
-- other decision Addison makes on a person's behalf: which model answers.
--
-- WHY THIS EXISTS. `usage_log` records a provider call that SUCCEEDED — tokens
-- and latency — so a provider that never once succeeded left no trace anywhere.
-- A Google key spent an evening answering 404 while the Connections panel said
-- "connected", and the only evidence was a line in the activity strip that
-- scrolled away: "gemini-2.0-flash was busy". Nobody could tell a rate limit from
-- a bad request from a model that does not exist, because the status code was
-- discarded at the point it was classified. Diagnosing it took hours of probing a
-- third-party API; with this table it is one query.
--
-- Only failures are stored. A success is already a `usage_log` row, and writing
-- both would make the common case pay for the rare one.
CREATE TABLE IF NOT EXISTS provider_attempts (
    id              TEXT PRIMARY KEY,        -- uuid4
    conversation_id TEXT,                    -- NULL for a routine/widget turn
    provider        TEXT NOT NULL,           -- 'anthropic' | 'openai' | 'google' | 'ollama' | 'custom'
    model           TEXT NOT NULL,           -- the raw model id that was called
    -- unavailable  = transient; the chain walked on (429, 5xx, timeout)
    -- key_rejected = the server answered 401/403 about the key Addison holds
    -- auth_failed  = no key to send, or unusable bytes — never reached a server
    -- rejected     = a 4xx the next provider would repeat; the turn failed
    outcome         TEXT NOT NULL
                        CHECK(outcome IN ('unavailable','key_rejected',
                                          'auth_failed','rejected')),
    -- The HTTP status, or NULL when the request never reached a server. NULL is
    -- the honest answer for a timeout; inventing a 0 would claim a reply.
    status_code     INTEGER,
    -- The plain sentence the person was shown, redacted on write like tool_audit.
    detail          TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_attempts_created
    ON provider_attempts(created_at);
