from __future__ import annotations
"""Aide v0 — SQLite state store. Single-file, WAL mode, thread-safe via one connection per call."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT DEFAULT '',
    due TEXT,                       -- ISO date or NULL
    priority INTEGER DEFAULT 3,     -- 1 (P1) .. 4 (P4)
    effort_min INTEGER,
    goal_id INTEGER,
    status TEXT DEFAULT 'open',     -- open | done | dropped
    defer_count INTEGER DEFAULT 0,
    source TEXT DEFAULT 'chat',
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    tier TEXT CHECK(tier IN ('horizon','yearly','quarterly')),
    title TEXT NOT NULL,
    measure TEXT DEFAULT '',
    target_date TEXT,
    status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,              -- ISO date the data refers to
    kind TEXT NOT NULL,             -- steps | sleep_min | screen_min | app_screen | workout_min
    key TEXT DEFAULT '',            -- app name for app_screen, else ''
    value REAL NOT NULL,
    received_at TEXT DEFAULT (datetime('now')),
    UNIQUE(day, kind, key) ON CONFLICT REPLACE
);
CREATE TABLE IF NOT EXISTS plans (
    day TEXT PRIMARY KEY,
    priorities_json TEXT,
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    day TEXT PRIMARY KEY,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    ts TEXT DEFAULT (datetime('now')),
    direction TEXT,                 -- in | out
    text TEXT,
    intent TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS config_kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- tasks ----------
    def add_task(self, title: str, due: str | None = None, priority: int = 3,
                 notes: str = "", source: str = "chat") -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO tasks (title, due, priority, notes, source) VALUES (?,?,?,?,?)",
                (title.strip(), due, priority, notes, source))
            return cur.lastrowid

    def open_tasks(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM tasks WHERE status='open' "
                "ORDER BY priority ASC, due IS NULL, due ASC, id ASC").fetchall()
            return [dict(r) for r in rows]

    def carry_over(self) -> list[dict]:
        """Open tasks created before today, or due today/overdue."""
        today = date.today().isoformat()
        with self._conn() as c:
            rows = c.execute(
                "SELECT *, CAST(julianday('now') - julianday(created_at) AS INT) AS age_days "
                "FROM tasks WHERE status='open' AND (date(created_at) < ? OR (due IS NOT NULL AND due <= ?)) "
                "ORDER BY priority ASC, due ASC", (today, today)).fetchall()
            return [dict(r) for r in rows]

    def complete_task(self, task_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='done', completed_at=datetime('now') "
                "WHERE id=? AND status='open'", (task_id,))
            return cur.rowcount > 0

    def defer_task(self, task_id: int, new_due: str | None = None) -> int | None:
        """Returns new defer_count or None if not found."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET defer_count = defer_count + 1, due = COALESCE(?, due) "
                "WHERE id=? AND status='open'", (new_due, task_id))
            if cur.rowcount == 0:
                return None
            row = c.execute("SELECT defer_count FROM tasks WHERE id=?", (task_id,)).fetchone()
            return row["defer_count"]

    def drop_task(self, task_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE tasks SET status='dropped' WHERE id=? AND status='open'", (task_id,))
            return cur.rowcount > 0

    # ---------- telemetry ----------
    def ingest_telemetry(self, day: str, kind: str, value: float, key: str = ""):
        with self._conn() as c:
            c.execute("INSERT INTO telemetry (day, kind, key, value) VALUES (?,?,?,?)",
                      (day, kind, key, value))

    def telemetry_for(self, day: str) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT kind, key, value FROM telemetry WHERE day=?", (day,)).fetchall()
        out: dict = {"apps": {}}
        for r in rows:
            if r["kind"] == "app_screen":
                out["apps"][r["key"]] = r["value"]
            else:
                out[r["kind"]] = r["value"]
        return out

    # ---------- plans ----------
    def save_priorities(self, day: str, priorities: list[str]):
        with self._conn() as c:
            c.execute("INSERT INTO plans (day, priorities_json, confirmed_at) VALUES (?,?,datetime('now')) "
                      "ON CONFLICT(day) DO UPDATE SET priorities_json=excluded.priorities_json, "
                      "confirmed_at=excluded.confirmed_at",
                      (day, json.dumps(priorities)))

    def priorities_for(self, day: str) -> list[str]:
        with self._conn() as c:
            row = c.execute("SELECT priorities_json FROM plans WHERE day=?", (day,)).fetchone()
        return json.loads(row["priorities_json"]) if row and row["priorities_json"] else []

    # ---------- messages / budget ----------
    def log_msg(self, direction: str, text: str, intent: str = "",
                tokens_in: int = 0, tokens_out: int = 0):
        with self._conn() as c:
            c.execute("INSERT INTO messages (direction, text, intent, tokens_in, tokens_out) "
                      "VALUES (?,?,?,?,?)", (direction, text[:4000], intent, tokens_in, tokens_out))

    def tokens_today(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(SUM(tokens_in + tokens_out),0) AS t FROM messages "
                            "WHERE date(ts)=date('now')").fetchone()
            return row["t"]

    def recent_dialogue(self, n: int = 12) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT direction, text FROM messages ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---------- kv ----------
    def set_kv(self, key: str, value: str):
        with self._conn() as c:
            c.execute("INSERT INTO config_kv (key,value) VALUES (?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_kv(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM config_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def dnd_until(self) -> datetime | None:
        v = self.get_kv("dnd_until")
        return datetime.fromisoformat(v) if v else None

    def set_dnd(self, hours: float):
        self.set_kv("dnd_until", (datetime.now() + timedelta(hours=hours)).isoformat())
