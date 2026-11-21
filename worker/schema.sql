PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  chat_id TEXT PRIMARY KEY,
  first_name TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 0,
  flow TEXT,
  city_input TEXT NOT NULL DEFAULT '',
  street_input TEXT NOT NULL DEFAULT '',
  pending_matches TEXT NOT NULL DEFAULT '[]',
  address_key TEXT NOT NULL DEFAULT '',
  address_label TEXT NOT NULL DEFAULT '',
  group_name TEXT NOT NULL DEFAULT '',
  snapshot TEXT NOT NULL DEFAULT '{}',
  message_id INTEGER,
  fail_count INTEGER NOT NULL DEFAULT 0,
  error_notified INTEGER NOT NULL DEFAULT 0,
  no_schedule_notified INTEGER NOT NULL DEFAULT 0,
  last_ok TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);
CREATE INDEX IF NOT EXISTS idx_users_address ON users(address_key, group_name);

CREATE TABLE IF NOT EXISTS address_index (
  source_id TEXT PRIMARY KEY,
  city_id TEXT NOT NULL,
  city_name TEXT NOT NULL,
  city_norm TEXT NOT NULL,
  street_id TEXT NOT NULL,
  street_name TEXT NOT NULL,
  street_norm TEXT NOT NULL,
  building_names TEXT NOT NULL,
  group_gpv TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_address_city_street ON address_index(city_name, street_name);
CREATE INDEX IF NOT EXISTS idx_address_city_norm ON address_index(city_norm);

CREATE TABLE IF NOT EXISTS address_index_staging (
  source_id TEXT PRIMARY KEY,
  city_id TEXT NOT NULL,
  city_name TEXT NOT NULL,
  city_norm TEXT NOT NULL,
  street_id TEXT NOT NULL,
  street_name TEXT NOT NULL,
  street_norm TEXT NOT NULL,
  building_names TEXT NOT NULL,
  group_gpv TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS processed_updates (
  update_id INTEGER PRIMARY KEY,
  received_at INTEGER NOT NULL DEFAULT (unixepoch())
);


CREATE TABLE IF NOT EXISTS bot_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
