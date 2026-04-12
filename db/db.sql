-- INF5190 - Projet de session
-- Script de creation de la base SQLite (vide).
--
-- Le script d'import ne cree pas la base: il insere seulement.
-- La cle unique `source_hash` permet d'eviter les doublons.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS violations (
  id INTEGER PRIMARY KEY,
  source_hash TEXT NOT NULL UNIQUE,

  date_iso TEXT,
  establishment TEXT,
  owner TEXT,
  street TEXT,
  category TEXT,
  description TEXT,
  amount REAL,
  status TEXT,
  city TEXT,

  searchable TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_violations_date ON violations(date_iso);
CREATE INDEX IF NOT EXISTS idx_violations_status ON violations(status);
CREATE INDEX IF NOT EXISTS idx_violations_category ON violations(category);
CREATE INDEX IF NOT EXISTS idx_violations_city ON violations(city);
CREATE INDEX IF NOT EXISTS idx_violations_establishment ON violations(establishment);
CREATE INDEX IF NOT EXISTS idx_violations_owner ON violations(owner);
CREATE INDEX IF NOT EXISTS idx_violations_street ON violations(street);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  profile_photo BLOB,
  profile_photo_mime TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_watchlist (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  establishment TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, establishment),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_user_watchlist_user_id ON user_watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_user_watchlist_establishment ON user_watchlist(establishment);
