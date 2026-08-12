"""Aide v0 — morning brief. Deterministic assembly first (NFR-7), LLM polish second.
If every external dependency is down, the user still gets calendar-less, stat-less,
correctly-formatted brief with their tasks."""
from datetime import date, timedelta

from . import fmt
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
    tasks_all = db.open_tasks()
    lines = [fmt.header(today, len(tasks_all))]

    # Calendar
    events = todays_events()
    if events:
        lines.append(f"\n{fmt.SECTION['calendar']} <b>Calendar</b>")
        lines += [fmt.esc(e.line()) for e in events[:8]]
    else:
        lines.append(f"\n{fmt.SECTION['calendar']} Calendar clear")

    # Carry-over
    carry = db.carry_over()
    if carry:
        lines.append(f"\n{fmt.SECTION['carry']} <b>Carry-over</b>")
        for t in carry[:6]:
            line = fmt.task_line(t)
            if t.get("age_days", 0) >= 2:
                line += f" · <i>day {t['age_days'] + 1}</i>"
            lines.append(line)

    # Suggested top 3
    tasks = tasks_all
    if tasks:
        lines.append(f"\n{fmt.SECTION['top3']} <b>Top 3</b>")
        for i, t in enumerate(tasks[:3], 1):
            lines.append(f"{i}. {fmt.esc(t['title'])}")

    # Email
    try:
        mc = MailClient()
        if mc.configured():
            c = mc.counts(days=1)
            if c["unread"]:
                lines.append(f"\n{fmt.SECTION['inbox']} <b>{c['unread']}</b> unread "
                             f"· {c['personal']} personal, {c['newsletters']} newsletters")
    except Exception:
        pass

    # Telemetry
    tl = telemetry_line(db)
    if tl:
        lines.append(f"\n{fmt.SECTION['health']} {fmt.esc(tl)}")

    lines.append("\n<i>Priorities today — confirm mine or tell me yours.</i>")
    raw = "\n".join(lines)

    # Only pay for polish when there is something to shape. An empty day
    # (no events, no tasks, no telemetry) has nothing for the model to add.
    if not events and not carry and not tasks:
        return raw
    polished = llm.polish_brief(raw, context=f"Open task count: {len(tasks)}")
    return polished or raw
