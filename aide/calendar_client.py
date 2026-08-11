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
