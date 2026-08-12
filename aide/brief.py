"""Aide v0 — morning brief. Deterministic assembly first (NFR-7), LLM polish second.
If every external dependency is down, the user still gets calendar-less, stat-less,
correctly-formatted brief with their tasks."""
from datetime import date, timedelta

from .calendar_client import todays_events
from .mail import MailClient
from .db import DB
from .llm import LLM


def _fmt_min(m: float) -> str:
    m = int(m)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m}m"


def telemetry_line(db: DB) -> str:
    yday = (date.today() - timedelta(days=1)).isoformat()
    t = db.telemetry_for(yday)
    parts = []
    if "steps" in t:
        parts.append(f"Steps {int(t['steps']):,}")
    if "sleep_min" in t:
        parts.append(f"Sleep {_fmt_min(t['sleep_min'])}")
    if "workout_min" in t:
        parts.append(f"Workout {_fmt_min(t['workout_min'])}")
    if "screen_min" in t:
        s = f"Screen {_fmt_min(t['screen_min'])}"
        if t.get("apps"):
            top = max(t["apps"].items(), key=lambda kv: kv[1])
            s += f" ({top[0]} {_fmt_min(top[1])})"
        parts.append(s)
    return "Yesterday: " + " · ".join(parts) if parts else ""


def build_brief(db: DB, llm: LLM) -> str:
    today = date.today()
    lines = [f"Morning. {today.strftime('%A %d %b')}."]

    # Calendar
    events = todays_events()
    if events:
        lines.append("\nCalendar:")
        lines += [e.line() for e in events[:8]]
    else:
        lines.append("\nCalendar: clear (or unreachable).")

    # Carry-over + open tasks
    carry = db.carry_over()
    if carry:
        lines.append("\nCarry-over:")
        for t in carry[:6]:
            age = f" — day {t['age_days'] + 1} rolling" if t.get("age_days", 0) >= 2 else ""
            defer = f" (deferred x{t['defer_count']})" if t["defer_count"] >= 3 else ""
            due = f" [due {t['due']}]" if t["due"] else ""
            lines.append(f"• #{t['id']} {t['title']}{due}{age}{defer}")

    # Suggested top 3 = highest priority, oldest, nearest due
    tasks = db.open_tasks()
    if tasks:
        lines.append("\nMy suggested top 3:")
        for t in tasks[:3]:
            lines.append(f"{tasks.index(t) + 1}. {t['title']}")

    # Email
    try:
        mc = MailClient()
        if mc.configured():
            c = mc.counts(days=1)
            if c["unread"]:
                lines.append(f"\nInbox: {c['unread']} unread "
                             f"({c['personal']} personal, {c['newsletters']} newsletters)")
    except Exception:
        pass

    # Telemetry
    tl = telemetry_line(db)
    if tl:
        lines.append("\n" + tl)

    lines.append("\nWhat are your priorities today? Confirm mine, or tell me yours.")
    raw = "\n".join(lines)

    # Only pay for polish when there is something to shape. An empty day
    # (no events, no tasks, no telemetry) has nothing for the model to add.
    if not events and not carry and not tasks:
        return raw
    polished = llm.polish_brief(raw, context=f"Open task count: {len(tasks)}")
    return polished or raw
