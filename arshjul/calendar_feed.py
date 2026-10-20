"""The calendar feed (iCalendar, RFC 5545) – deadlines and project weeks in Outlook.

Each user has a secret link (users.calendar_token): Outlook can't log in, so the link itself is the key.
Engineers get every event on the wheel (prep, deadline, use period, count week); lecturers get the use
period only, as on their wheel. All events are all-day, and every event has a stable UID, so when a project
changes, Outlook updates the event instead of adding a new one. Text users see is Norwegian.
"""
from __future__ import annotations

import datetime as dt

import deadlines

PRODID = "-//NTNU//Arshjul for innkjop//NO"


def escape(text: str) -> str:
    """TEXT values: backslash, semicolon, comma and newlines are escaped (RFC 5545 §3.3.11)."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Lines longer than 75 octets are folded: CRLF + a space, never in the middle of a UTF-8 character."""
    out, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not out else 74):
            out.append(current.decode("utf-8"))
            current = b""
        current += b
    out.append(current.decode("utf-8"))
    return "\r\n ".join(out)


def _date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _event(uid: str, start: dt.date, end: dt.date, summary: str, description: str, stamp: str) -> list[str]:
    """One all-day event from start to end (end exclusive, as iCalendar wants it)."""
    return ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{_date(start)}",
            f"DTEND;VALUE=DATE:{_date(end)}", f"SUMMARY:{escape(summary)}", f"DESCRIPTION:{escape(description)}",
            "TRANSP:TRANSPARENT", "END:VEVENT"]


def run_events(g: dict, closed: list[dict], engineer: bool, stamp: str) -> list[str]:
    """The events for one run. A week-based event (deadline, prep, count) sits on the Monday of its week;
    the use period covers Monday of the start week to Friday of the finish week."""
    d = deadlines.deadlines(g, closed)
    type_name = deadlines.TYPES.get(g.get("type"), deadlines.TYPES["hackathon"])["name"]
    weeks = f"uke {d['start']['week']}" + (f"–{d['finish']['week']}" if d["finish"]["week"] != d["start"]["week"] else "")
    info = " · ".join(x for x in (g.get("courseCode") and f"Emne {g['courseCode']}", weeks,
                                  g.get("sets") and f"{g['sets']} sett", g.get("note")) if x)
    start, finish = dt.date.fromisoformat(d["start"]["date"]), dt.date.fromisoformat(d["finish"]["date"])
    lines = _event(f"run-{g['id']}-use@arshjul", start, finish + dt.timedelta(days=5),
                   f"{g['name']} ({type_name})", info, stamp)
    if not engineer:
        return lines
    for e in d["events"]:
        if e["kind"] == "start":
            continue  # the use period already shows it
        day = dt.date.fromisoformat(e["date"])
        lines += _event(f"run-{g['id']}-{e['kind']}@arshjul", day, day + dt.timedelta(days=1),
                        f"{e['what']}: {g['name']} (uke {e['week']})", info, stamp)
    return lines


def build(runs: list[dict], closed: list[dict], user: dict, now: dt.datetime | None = None) -> str:
    """The whole calendar for one user. runs: the runs the user is involved in (already filtered)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    current = deadlines.school_year_for(now.date())
    engineer = user["role"] == "engineer"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape('Årshjul – ' + user['name'])}", "X-WR-TIMEZONE:Europe/Oslo",
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for g in runs:
        if not g["deleted"] and current <= g["school_year"] <= current + 1:
            lines += run_events(g, closed, engineer, stamp)
    lines.append("END:VCALENDAR")
    return "".join(fold(l) + "\r\n" for l in lines)
