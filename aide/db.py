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
CREATE TABLE IF NOT EXISTS mail_cache (
    uid TEXT PRIMARY KEY,
    sender_name TEXT, sender_addr TEXT, subject TEXT,
    date TEXT, body TEXT, unsub_json TEXT,
    cached_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS facts (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    message TEXT NOT NULL,
    fire_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending',   -- pending | sent | cancelled
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(status, fire_at);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
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

    def tasks_by_status(self, status: str = "open") -> list[dict]:
        with self._conn() as c:
            if status == "all":
                rows = c.execute(
                    "SELECT * FROM tasks ORDER BY status, priority, due IS NULL, due, id").fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM tasks WHERE status=? "
                    "ORDER BY priority, due IS NULL, due, id", (status,)).fetchall()
            return [dict(r) for r in rows]

    def update_task(self, task_id: int, title=None, due=None, clear_due=False,
                    priority=None) -> bool:
        sets, vals = [], []
        if title:
            sets.append("title=?"); vals.append(title)
        if clear_due:
            sets.append("due=NULL")
        elif due:
            sets.append("due=?"); vals.append(due)
        if priority:
            sets.append("priority=?"); vals.append(int(priority))
        if not sets:
            return False
        vals.append(task_id)
        with self._conn() as c:
            cur = c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
            return cur.rowcount > 0

    # ---------- mail cache ----------
    def cache_mails(self, mails):
        import json as _json
        with self._conn() as c:
            c.execute("DELETE FROM mail_cache WHERE cached_at < datetime('now','-1 day')")
            for m in mails:
                c.execute(
                    "INSERT INTO mail_cache (uid,sender_name,sender_addr,subject,date,body,unsub_json,cached_at) "
                    "VALUES (?,?,?,?,?,?,?,datetime('now')) "
                    "ON CONFLICT(uid) DO UPDATE SET cached_at=datetime('now')",
                    (m.uid, m.sender_name, m.sender_addr, m.subject,
                     m.date.isoformat() if m.date else None, m.body[:4000],
                     _json.dumps(m.unsubscribe)))

    def get_cached_mail(self, uid: str):
        import json as _json
        from datetime import datetime as _dt
        from .mail import Mail
        with self._conn() as c:
            r = c.execute("SELECT * FROM mail_cache WHERE uid=?", (uid,)).fetchone()
        if not r:
            return None
        return Mail(
            uid=r["uid"], sender_name=r["sender_name"], sender_addr=r["sender_addr"],
            subject=r["subject"],
            date=_dt.fromisoformat(r["date"]) if r["date"] else None,
            body=r["body"] or "", unread=False,
            unsubscribe=_json.loads(r["unsub_json"] or "{}"))

    # ---------- facts (durable memory) ----------
    def set_fact(self, key: str, value: str):
        with self._conn() as c:
            c.execute("INSERT INTO facts (key,value,updated_at) VALUES (?,?,datetime('now')) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                      "updated_at=excluded.updated_at", (key, value))

    def get_fact(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def all_facts(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT key,value FROM facts ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ---------- reminders ----------
    def add_reminder(self, message: str, fire_at: str) -> int:
        with self._conn() as c:
            cur = c.execute("INSERT INTO reminders (message, fire_at) VALUES (?,?)",
                            (message, fire_at))
            return cur.lastrowid

    def due_reminders(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM reminders WHERE status='pending' AND fire_at <= ? "
                "ORDER BY fire_at", (datetime.now().isoformat(),)).fetchall()
            return [dict(r) for r in rows]

    def pending_reminders(self, include_future: bool = True) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM reminders WHERE status='pending' ORDER BY fire_at").fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_sent(self, rid: int):
        with self._conn() as c:
            c.execute("UPDATE reminders SET status='sent' WHERE id=?", (rid,))

    def cancel_reminder(self, rid: int) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE reminders SET status='cancelled' "
                            "WHERE id=? AND status='pending'", (rid,))
            return cur.rowcount > 0

    # ---------- history search ----------
    def search_messages(self, query: str, limit: int = 12) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, direction, text FROM messages "
                "WHERE text LIKE ? AND direction IN ('in','out') "
                "ORDER BY id DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
            return [dict(r) for r in rows]

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
