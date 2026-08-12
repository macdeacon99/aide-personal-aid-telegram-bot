from __future__ import annotations
"""Aide v0 — read-only CalDAV client (iCloud, Fastmail, Nextcloud all speak CalDAV).
Fails soft: any error returns [] and the brief carries on without the calendar."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import CFG


@dataclass
class Event:
    start: datetime | None   # None => all-day
    end: datetime | None
    title: str
    location: str = ""

    def line(self) -> str:
        if self.start is None:
            return f"• (all day) {self.title}"
        s = self.start.astimezone(CFG.tz).strftime("%H:%M")
        e = self.end.astimezone(CFG.tz).strftime("%H:%M") if self.end else "?"
        loc = f" @ {self.location}" if self.location else ""
        return f"• {s}–{e} {self.title}{loc}"


def _client():
    import caldav
    return caldav.DAVClient(url=CFG.caldav_url,
                            username=CFG.caldav_user,
                            password=CFG.caldav_pass)


def _writable_calendar():
    """First calendar that accepts VEVENT, or just the first one."""
    principal = _client().principal()
    cals = principal.calendars()
    for cal in cals:
        try:
            comps = cal.get_supported_components()
            if not comps or "VEVENT" in comps:
                return cal
        except Exception:
            continue
    return cals[0] if cals else None


def create_event(title: str, start_iso: str, duration_minutes: int = 60,
                 location: str = "", description: str = "") -> tuple[bool, str]:
    """Write an event to the calendar. Returns (ok, human message)."""
    if not CFG.caldav_url:
        return False, "No calendar configured — set CALDAV_URL in .env."
    try:
        from icalendar import Calendar, Event as ICalEvent
        import uuid

        start = datetime.fromisoformat(start_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=CFG.tz)
        end = start + timedelta(minutes=duration_minutes)

        cal = _writable_calendar()
        if cal is None:
            return False, "No writable calendar found."

        ical = Calendar()
        ical.add("prodid", "-//Aide//EN")
        ical.add("version", "2.0")
        ev = ICalEvent()
        ev.add("uid", str(uuid.uuid4()))
        ev.add("summary", title)
        ev.add("dtstart", start)
        ev.add("dtend", end)
        ev.add("dtstamp", datetime.now(CFG.tz))
        if location:
            ev.add("location", location)
        if description:
            ev.add("description", description)
        ical.add_component(ev)

        cal.save_event(ical.to_ical().decode())
        when = start.strftime("%a %d %b %H:%M")
        return True, f"Added '{title}' to your calendar — {when}."
    except Exception as e:                                   # noqa: BLE001
        return False, f"Couldn't write to the calendar: {e}"


def upcoming_events(days: int = 7) -> list[Event]:
    if not CFG.caldav_url:
        return []
    try:
        principal = _client().principal()
        start = datetime.combine(date.today(), time.min, tzinfo=CFG.tz)
        end = start + timedelta(days=days + 1)
        events: list[Event] = []
        for cal in principal.calendars():
            try:
                for ev in cal.search(start=start, end=end, event=True, expand=True):
                    comp = ev.icalendar_component
                    dtstart = comp.get("dtstart")
                    dtend = comp.get("dtend")
                    if dtstart is None:
                        continue
                    sv = dtstart.dt
                    title = str(comp.get("summary", "Untitled"))
                    loc = str(comp.get("location", "") or "")
                    if isinstance(sv, datetime):
                        e_end = dtend.dt if dtend is not None and isinstance(dtend.dt, datetime) else None
                        events.append(Event(sv, e_end, title, loc))
                    else:
                        events.append(Event(None, None, title, loc))
            except Exception:
                continue
        events.sort(key=lambda e: (e.start is None, e.start or datetime.min.replace(tzinfo=CFG.tz)))
        return events
    except Exception:
        return []


def todays_events() -> list[Event]:
    if not CFG.caldav_url:
        return []
    try:
        import caldav
        client = caldav.DAVClient(url=CFG.caldav_url,
                                  username=CFG.caldav_user,
                                  password=CFG.caldav_pass)
        principal = client.principal()
        start = datetime.combine(date.today(), time.min, tzinfo=CFG.tz)
        end = start + timedelta(days=1)
        events: list[Event] = []
        for cal in principal.calendars():
            try:
                for ev in cal.search(start=start, end=end, event=True, expand=True):
                    comp = ev.icalendar_component
                    dtstart = comp.get("dtstart")
                    dtend = comp.get("dtend")
                    title = str(comp.get("summary", "Untitled"))
                    location = str(comp.get("location", "") or "")
                    if dtstart is None:
                        continue
                    sv = dtstart.dt
                    if isinstance(sv, datetime):
                        e_end = dtend.dt if dtend is not None and isinstance(dtend.dt, datetime) else None
                        events.append(Event(sv, e_end, title, location))
                    else:  # date => all-day
                        events.append(Event(None, None, title, location))
            except Exception:
                continue
        events.sort(key=lambda e: (e.start is None, e.start or datetime.min.replace(tzinfo=CFG.tz)))
        return events
    except Exception:
        return []
