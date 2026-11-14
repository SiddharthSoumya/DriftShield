from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors (
    id INTEGER PRIMARY KEY,
    repository TEXT NOT NULL UNIQUE COLLATE NOCASE,
    mode TEXT NOT NULL CHECK (mode IN ('public', 'runner')),
    token_hash TEXT,
    repo_id INTEGER,
    baseline_version INTEGER,
    created_at TEXT NOT NULL,
    last_attempt TEXT,
    last_success TEXT,
    last_error TEXT,
    latest TEXT
);
CREATE TABLE IF NOT EXISTS baselines (
    monitor_id INTEGER NOT NULL REFERENCES monitors(id),
    version INTEGER NOT NULL,
    values_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (monitor_id, version)
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id),
    observed_at TEXT NOT NULL,
    delivery_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    UNIQUE (monitor_id, delivery_id)
);
CREATE INDEX IF NOT EXISTS scans_monitor ON scans(monitor_id, id DESC);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id),
    baseline_version INTEGER NOT NULL,
    field TEXT NOT NULL,
    expected TEXT NOT NULL,
    actual TEXT NOT NULL,
    severity TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    acknowledged_at TEXT,
    closed_at TEXT,
    resolution TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS open_field
ON incidents(monitor_id, field) WHERE closed_at IS NULL;
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id),
    occurred_at TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def audit(db, monitor_id, action, detail):
    db.execute(
        "INSERT INTO audit(monitor_id, occurred_at, action, detail) VALUES (?, ?, ?, ?)",
        (monitor_id, now(), action, detail),
    )
