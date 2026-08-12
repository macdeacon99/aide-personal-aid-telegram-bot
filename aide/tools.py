"""Aide v1 — tool definitions and dispatch.

This is the fix for v0's central flaw: the chat path had no idea it was Aide.
It would say "I don't have access to your task system" while reading the same
database that generated the morning brief. Tools close that gap — the model can
now query and act instead of apologising.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .calendar_client import create_event, todays_events, upcoming_events
from .db import DB

# ---------------------------------------------------------------------------
# Schemas exposed to the model
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_tasks",
        "description": (
            "List the user's tasks. Call this before answering ANY question "
            "about what they have on, what's outstanding, or what to do next. "
            "Never guess at task contents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "done", "dropped", "all"],
                    "description": "Which tasks to return. Defaults to open.",
                },
                "due_filter": {
                    "type": "string",
                    "enum": ["today", "overdue", "week", "any"],
                    "description": "Optional due-date filter.",
                },
            },
        },
    },
    {
        "name": "add_task",
        "description": "Create a task. Use for anything the user says they need to do.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "due": {"type": "string", "description": "ISO date YYYY-MM-DD, or omit."},
                "priority": {
                    "type": "integer",
                    "description": "1 (urgent) to 4 (someday). Default 3.",
                },
                "notes": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "complete_tasks",
        "description": (
            "Mark one or more tasks done. Accepts explicit IDs, or 'all' to "
            "close every open task. Confirm with the user before using 'all'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs to complete.",
                },
                "all_open": {
                    "type": "boolean",
                    "description": "Complete every open task. Use only on explicit instruction.",
                },
            },
        },
    },
    {
        "name": "update_task",
        "description": "Change a task's due date, priority, title, or defer it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "due": {"type": "string", "description": "ISO date, or 'none' to clear."},
                "priority": {"type": "integer"},
                "defer": {"type": "boolean", "description": "Increment the defer counter."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "drop_tasks",
        "description": "Delete/abandon tasks (not the same as completing them).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "integer"}},
                "all_open": {"type": "boolean"},
            },
        },
    },
    {
        "name": "get_calendar",
        "description": "Read calendar events. Call before answering anything about the user's schedule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days from today. 0 = today only. Default 0.",
                }
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Add an event to the user's calendar. Use for appointments, "
            "meetings, blocked-out work time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "description": "ISO datetime, e.g. 2026-08-14T14:00"},
                "duration_minutes": {"type": "integer", "description": "Default 60."},
                "location": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "set_reminder",
        "description": (
            "Schedule a one-off message to the user at a specific time. Use for "
            "'remind me at 6', 'nudge me before the call', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to say when it fires."},
                "when": {"type": "string", "description": "ISO datetime."},
            },
            "required": ["message", "when"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List pending reminders.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder by ID.",
        "input_schema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
    },
    {
        "name": "get_telemetry",
        "description": "Read health/phone data (steps, sleep, screen time) for a given day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ago": {"type": "integer", "description": "0 = today, 1 = yesterday. Default 1."}
            },
        },
    },
    {
        "name": "remember",
        "description": (
            "Store a durable fact about the user — preferences, goals, context, "
            "recurring commitments. Use when they tell you something worth "
            "carrying into future conversations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short slug, e.g. 'peak_hours'."},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": "Retrieve stored facts. Call with no key to list everything known.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
        },
    },
    {
        "name": "search_history",
        "description": (
            "Search past conversation for something the user referenced but "
            "didn't restate. Use when they say 'that thing we discussed'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _fmt_task(t: dict) -> str:
    bits = [f"#{t['id']}", f"P{t['priority']}", t["title"]]
    if t.get("due"):
        bits.append(f"(due {t['due']})")
    if t.get("defer_count"):
        bits.append(f"[deferred x{t['defer_count']}]")
    if t.get("status") and t["status"] != "open":
        bits.append(f"<{t['status']}>")
    return " ".join(bits)


def execute(name: str, args: dict, db: DB) -> str:
    """Run a tool and return a plain-text result for the model."""
    try:
        return _execute(name, args, db)
    except Exception as e:                                  # noqa: BLE001
        return f"Tool error ({name}): {e}"


def _execute(name: str, args: dict, db: DB) -> str:
    today = date.today()

    if name == "list_tasks":
        status = args.get("status", "open")
        tasks = db.tasks_by_status(status)
        df = args.get("due_filter", "any")
        if df == "today":
            tasks = [t for t in tasks if t["due"] == today.isoformat()]
        elif df == "overdue":
            tasks = [t for t in tasks if t["due"] and t["due"] < today.isoformat()]
        elif df == "week":
            end = (today + timedelta(days=7)).isoformat()
            tasks = [t for t in tasks if t["due"] and t["due"] <= end]
        if not tasks:
            return "No tasks match."
        return "\n".join(_fmt_task(t) for t in tasks)

    if name == "add_task":
        tid = db.add_task(
            args["title"],
            due=args.get("due"),
            priority=int(args.get("priority") or 3),
            notes=args.get("notes", ""),
        )
        return f"Created #{tid}: {args['title']}"

    if name == "complete_tasks":
        if args.get("all_open"):
            ids = [t["id"] for t in db.open_tasks()]
        else:
            ids = args.get("task_ids") or []
        done = [i for i in ids if db.complete_task(i)]
        if not done:
            return "Nothing completed — no matching open tasks."
        return f"Completed {len(done)}: {', '.join('#' + str(i) for i in done)}"

    if name == "drop_tasks":
        if args.get("all_open"):
            ids = [t["id"] for t in db.open_tasks()]
        else:
            ids = args.get("task_ids") or []
        dropped = [i for i in ids if db.drop_task(i)]
        if not dropped:
            return "Nothing dropped — no matching open tasks."
        return f"Dropped {len(dropped)}: {', '.join('#' + str(i) for i in dropped)}"

    if name == "update_task":
        tid = args["task_id"]
        if args.get("defer"):
            count = db.defer_task(tid, args.get("due"))
            if count is None:
                return f"No open task #{tid}."
            note = " — that's 3+ deferrals, worth a decision" if count >= 3 else ""
            return f"Deferred #{tid} (x{count}){note}"
        ok = db.update_task(
            tid,
            title=args.get("title"),
            due=(None if args.get("due") == "none" else args.get("due")),
            clear_due=(args.get("due") == "none"),
            priority=args.get("priority"),
        )
        return f"Updated #{tid}." if ok else f"No task #{tid}."

    if name == "get_calendar":
        days = int(args.get("days_ahead") or 0)
        events = todays_events() if days == 0 else upcoming_events(days)
        if not events:
            return "Calendar is clear (or unreachable)."
        return "\n".join(e.line() for e in events)

    if name == "create_calendar_event":
        ok, msg = create_event(
            title=args["title"],
            start_iso=args["start"],
            duration_minutes=int(args.get("duration_minutes") or 60),
            location=args.get("location", ""),
            description=args.get("description", ""),
        )
        return msg

    if name == "set_reminder":
        rid = db.add_reminder(args["message"], args["when"])
        return f"Reminder #{rid} set for {args['when']}."

    if name == "list_reminders":
        rems = db.pending_reminders(include_future=True)
        if not rems:
            return "No pending reminders."
        return "\n".join(f"#{r['id']} {r['fire_at']} — {r['message']}" for r in rems)

    if name == "cancel_reminder":
        ok = db.cancel_reminder(args["reminder_id"])
        return "Cancelled." if ok else "No such pending reminder."

    if name == "get_telemetry":
        d = (today - timedelta(days=int(args.get("days_ago", 1)))).isoformat()
        t = db.telemetry_for(d)
        if not t or (len(t) == 1 and not t.get("apps")):
            return f"No telemetry for {d}."
        return f"{d}: {json.dumps(t)}"

    if name == "remember":
        db.set_fact(args["key"], args["value"])
        return f"Noted: {args['key']}"

    if name == "recall":
        if args.get("key"):
            v = db.get_fact(args["key"])
            return f"{args['key']}: {v}" if v else f"Nothing stored for '{args['key']}'."
        facts = db.all_facts()
        if not facts:
            return "Nothing stored yet."
        return "\n".join(f"{k}: {v}" for k, v in facts.items())

    if name == "search_history":
        rows = db.search_messages(args["query"])
        if not rows:
            return "Nothing found in past conversation."
        return "\n".join(f"[{r['ts'][:16]}] {r['direction']}: {r['text'][:200]}" for r in rows)

    return f"Unknown tool: {name}"
