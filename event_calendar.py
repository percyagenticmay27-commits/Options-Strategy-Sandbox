"""Fail closed unless both scheduled macro calendars cover the trading day."""

import re
from calendar import month_abbr
from datetime import date, datetime, time, timedelta, timezone
from urllib.request import Request, urlopen

from replay import ET, iso, timestamp

BLS = ("https://blsmon1.bls.gov/schedule/news_release/bls.ics",
       "https://www.bls.gov/schedule/news_release/bls.ics")
FED = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
RELEASES = {"Employment Situation", "Consumer Price Index"}
BLACKOUT = timedelta(minutes=30)
CACHE_AGE = timedelta(hours=2)


def event(name, at):
    return {"name": name, "at": iso(at.astimezone(timezone.utc))}


def bls_events(body, day):
    body = re.sub(r"\r?\n[ \t]", "", body).strip()
    if not body.startswith("BEGIN:VCALENDAR") or not body.endswith("END:VCALENDAR"):
        raise ValueError("BLS response is not an iCalendar")
    events = []
    coverage = set()
    for block in re.findall(r"BEGIN:VEVENT\s+(.*?)END:VEVENT", body, re.S):
        title = re.search(r"^SUMMARY:([^\r\n]+)", block, re.M)
        if title is None or title[1] not in RELEASES:
            continue
        start = re.search(r"^DTSTART(?:;TZID=([\w/\-]+))?:(\d{8}T\d{6})(Z?)\r?$", block, re.M)
        if not start:
            raise ValueError("BLS event has no usable start")
        zone, value, utc = start.groups()
        if (zone, utc) not in {("US-Eastern", ""), ("America/New_York", ""), (None, "Z")}:
            raise ValueError("Unrecognized BLS time zone")
        at = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc if utc else ET)
        local = at.astimezone(ET)
        if (local.year, local.month) == (day.year, day.month):
            coverage.add(title[1])
        events.append(event(title[1], at))
    if coverage != RELEASES:
        raise ValueError("BLS is missing CPI or Employment Situation coverage for this month")
    return events


def fed_events(body, year):
    sections = re.split(r"\b(20\d{2}) FOMC Meetings\b", body)
    if str(year) not in sections[1::2]:
        raise ValueError("Fed calendar has no section for this year")
    section = sections[sections[1::2].index(str(year)) * 2 + 2]
    fields = re.findall(r'class="[^"]*\bfomc-meeting__(month|date)\b[^"]*"[^>]*>(.*?)</div>',
                        section, re.S)
    dates = set()
    for (left, month), (right, span) in zip(fields[::2], fields[1::2]):
        if (left, right) != ("month", "date"):
            raise ValueError("Unexpected Fed calendar layout")
        day = re.fullmatch(r"\d+\s*[-–]\s*(\d+)\*?", re.sub(r"<[^>]*>", "", span).strip())
        if day is None:
            continue
        end_month = re.sub(r"<[^>]*>", "", month).strip().split("/")[-1][:3]
        dates.add(date(year, list(month_abbr).index(end_month), int(day[1])))
    if len(fields) % 2 or len(dates) < 8:
        raise ValueError("Fed calendar does not cover the full year")
    return [event(name, datetime.combine(day, start, ET)) for day in sorted(dates)
            for name, start in (("FOMC rate decision", time(14)), ("FOMC press conference", time(14, 30)))]


def fetch(url):
    request = Request(url, headers={"User-Agent": "OptionsStrategySandbox/0.1.4"})
    with urlopen(request, timeout=8) as response:
        return response.read(3_000_000).decode("utf-8-sig")


def refresh(now):
    day = now.astimezone(ET).date()
    result = {"verified_at": iso(now), "sources": {}, "events": [], "errors": {}}
    for url in BLS:
        try:
            result["events"].extend(bls_events(fetch(url), day))
            result["sources"]["BLS"] = url
            result["errors"].pop("BLS", None)
            break
        except (OSError, ValueError) as exc:
            result["errors"]["BLS"] = str(exc)
    try:
        result["events"].extend(fed_events(fetch(FED), day.year))
        result["sources"]["FED"] = FED
    except (OSError, ValueError) as exc:
        result["errors"]["FED"] = str(exc)
    return result


def clearance(cache, now):
    unavailable = {"status": "CALENDAR_UNAVAILABLE", "event_clear": False}
    try:
        observed = timestamp(cache["verified_at"])
        if (set(cache["sources"]) != {"BLS", "FED"} or not timedelta(0) <= now - observed < CACHE_AGE
                or now.astimezone(ET).date() != observed.astimezone(ET).date()):
            return unavailable
        events = [(timestamp(row["at"]), row) for row in cache["events"]]
        nearby = [row for at, row in events if abs(now - at) <= BLACKOUT]
        upcoming = [row for at, row in sorted(events, key=lambda pair: pair[0]) if at > now]
    except (KeyError, ValueError, TypeError, AttributeError):
        return unavailable
    return {"status": "EVENT_BLACKOUT" if nearby else "CLEAR", "event_clear": not nearby,
            "active_events": nearby, "next_event": upcoming[0] if upcoming else None}
