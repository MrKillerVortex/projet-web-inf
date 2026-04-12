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
