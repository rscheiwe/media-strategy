-- SQLite schema for the chat API (no row-level security or user tables). Ids are UUID strings generated in Python; timestamps are
-- ISO-8601 strings; JSON columns hold serialized text.

CREATE TABLE IF NOT EXISTS chats (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  title       TEXT NOT NULL DEFAULT 'New chat',
  agent_uuid  TEXT,
  is_archived INTEGER NOT NULL DEFAULT 0,
  pinned_at   TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            TEXT PRIMARY KEY,
  chat_id       TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role          TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
  content       TEXT NOT NULL DEFAULT '',
  parts_json    TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  display_order INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
  user_id             TEXT PRIMARY KEY,
  theme               TEXT NOT NULL DEFAULT 'system' CHECK (theme IN ('system', 'light', 'dark')),
  default_thinking    INTEGER NOT NULL DEFAULT 0,
  thinking_effort     TEXT NOT NULL DEFAULT 'high'
                      CHECK (thinking_effort IN ('low', 'medium', 'high', 'extra', 'max')),
  default_agent_uuid  TEXT,
  custom_instructions TEXT NOT NULL DEFAULT '' CHECK (length(custom_instructions) <= 4000),
  send_on_enter       INTEGER NOT NULL DEFAULT 1,
  extra               TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_feedback (
  id         TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL,
  rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
  comment    TEXT CHECK (comment IS NULL OR length(comment) <= 2000),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (message_id, user_id)
);

CREATE TABLE IF NOT EXISTS usage_events (
  id                  TEXT PRIMARY KEY,
  user_id             TEXT NOT NULL,
  chat_id             TEXT REFERENCES chats(id) ON DELETE SET NULL,
  message_id          TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
  agent_uuid          TEXT,
  model               TEXT,
  thinking            INTEGER NOT NULL DEFAULT 0,
  status              TEXT NOT NULL CHECK (status IN ('completed', 'stopped', 'error')),
  input_tokens        INTEGER NOT NULL DEFAULT 0,
  output_tokens       INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
  cached_input_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens        INTEGER NOT NULL DEFAULT 0,
  steps               INTEGER NOT NULL DEFAULT 0,
  duration_ms         INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL
);

-- Recommendation workflow state per chat: the details gathered so far (with where each came
-- from), the pending request and active form (the human-in-the-loop pause), a run lock and the
-- latest run. One row per chat; deleted with the chat.
CREATE TABLE IF NOT EXISTS chat_workflow_state (
  chat_id         TEXT PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
  slots_json      TEXT NOT NULL DEFAULT '{}',
  pending_request INTEGER NOT NULL DEFAULT 0,
  asked_for_json  TEXT NOT NULL DEFAULT '[]',
  active_form_id  TEXT,
  running_since   TEXT,
  last_run_json   TEXT,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS chats_user_recent_idx ON chats (user_id, is_archived, updated_at DESC);
CREATE INDEX IF NOT EXISTS chats_user_pinned_idx
  ON chats (user_id, is_archived, pinned_at DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS chat_messages_chat_order_idx
  ON chat_messages (chat_id, display_order, created_at);
CREATE INDEX IF NOT EXISTS message_feedback_chat_idx ON message_feedback (chat_id);
CREATE INDEX IF NOT EXISTS usage_events_user_recent_idx ON usage_events (user_id, created_at DESC);
