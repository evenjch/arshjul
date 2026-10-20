"""Weeks, school years and deadlines – the only place they're computed.

A school year runs from Monday of week 32 to Sunday of week 31 the year after, and is named after
the year it starts («2026/27» = 2026). Weeks 32–53 belong to the first calendar year, weeks 1–31
to the next. Everything is computed with real dates, so the new year and week 53 come out right.
"""
from __future__ import annotations

import datetime as dt

SCHOOL_YEAR_WEEK = 32
WEEK = dt.timedelta(weeks=1)


def monday(year: int, week: int) -> dt.date:
    return dt.date.fromisocalendar(year, week, 1)


def weeks_in_year(year: int) -> int:
    return dt.date(year, 12, 28).isocalendar()[1]


def school_year_start(sy: int) -> dt.date:
    return monday(sy, SCHOOL_YEAR_WEEK)


def school_year_for(d: dt.date) -> int:
    return d.year if d >= school_year_start(d.year) else d.year - 1


SHOW_NEXT_WEEK = 26  # from this week in the summer, the app opens on the coming school year


def school_year_shown(d: dt.date) -> int:
    """The school year the page opens on. In the summer it's the coming one: the autumn's orders are done,
    and next year is what you're working on – even though the school year formally changes in week 32."""
    current = school_year_for(d)
    return current + 1 if d >= monday(current + 1, SHOW_NEXT_WEEK) else current


def school_year_name(sy: int) -> str:
    return f"{sy}/{(sy + 1) % 100:02d}"


def week_date(sy: int, week: int) -> dt.date:
    """The Monday of a week in a school year. Week 53 in a year without week 53 becomes week 52."""
    year = sy if week >= SCHOOL_YEAR_WEEK else sy + 1
    return monday(year, min(week, weeks_in_year(year)))


def in_range(w: int, a: int, b: int) -> bool:
    return a <= w <= b if a <= b else (w >= a or w <= b)


def easter_sunday(year: int) -> dt.date:
    """Easter Sunday (Gregorian, the «anonymous» algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return dt.date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def easter_week(year: int) -> int:
    """The week with Maundy Thursday, Good Friday and Easter Sunday (Sunday is the last day of the ISO week)."""
    return easter_sunday(year).isocalendar()[1]


def in_period(d: dt.date, c: dict) -> bool:
    """Is week d in the closed period c? A period that follows Easter has no fixed weeks."""
    year, w, _ = d.isocalendar()
    return w == easter_week(year) if c.get("easter") else in_range(w, c["from_week"], c["to_week"])


def is_closed(d: dt.date, closed: list[dict]) -> bool:
    return any(in_period(d, c) for c in closed)


def closed_name(d: dt.date, closed: list[dict]) -> str:
    return ", ".join(c["name"] for c in closed if in_period(d, c))


def back_open(d: dt.date, n: int, closed: list[dict]) -> dt.date:
    """Go n open weeks back from week d. Closed weeks don't count."""
    while n > 0:
        d -= WEEK
        if not is_closed(d, closed):
            n -= 1
    return d


def week_info(d: dt.date, closed: list[dict] | None = None) -> dict:
    year, week, _ = d.isocalendar()
    out = {"date": (d - dt.timedelta(days=d.weekday())).isoformat(), "week": week, "year": year}
    if closed is not None:
        out["closed"] = closed_name(d, closed)  # the period's name, empty if open
    return out


def school_year_weeks(sy: int, closed: list[dict]) -> list[dict]:
    """Every week of the school year (52 or 53)."""
    d, finish, out = school_year_start(sy), school_year_start(sy + 1), []
    while d < finish:
        out.append(week_info(d, closed))
        d += WEEK
    return out


WARNING_WEEKS = 12  # warn about the next school year this long before its first deadline


def setup_warning(rows: list[dict], closed: list[dict], today: dt.date) -> dict | None:
    """Warn if a school year hasn't been set up and its first deadline is getting close.

    Looks at projects that ran the year before but are missing in the school year (this year and next).
    The autumn's deadlines often fall in June, i.e. in the school year before – that's why next year
    has to be set up in the spring.
    """
    current = school_year_for(today)
    monday_now = today - dt.timedelta(days=today.weekday())
    for sy in (current, current + 1):
        existing = {g["projectId"] for g in rows if g["school_year"] == sy}
        missing = [g for g in rows if g["school_year"] == sy - 1 and g["projectId"] not in existing]
        if not missing:
            continue
        candidates = sorted(((deadlines({**g, "school_year": sy}, closed)["deadline"], g["name"]) for g in missing),
                            key=lambda x: x[0]["date"])
        deadline, name = candidates[0]
        weeks_ = (dt.date.fromisoformat(deadline["date"]) - monday_now).days // 7
        if weeks_ <= WARNING_WEEKS:
            return {"school_year": sy, "name": school_year_name(sy), "from": sy - 1, "fromName": school_year_name(sy - 1),
                    "deadline": deadline, "project": name, "weeks": weeks_, "missing": len(missing)}
    return None


def semester(week: int) -> str:
    return "Høst" if week >= SCHOOL_YEAR_WEEK else "Vår"


# project types: name, what the start is called, when counting happens (weeks after the last week in use) and what it's called
TYPES = {
    "semester_kit": {"name": "Semestersett", "start": "Utdeling",      "returns": 0, "count": "Innlevering og telling"},
    "project":  {"name": "Prosjekt",     "start": "Prosjektstart", "returns": 1, "count": "Innlevering og telling"},
    "hackathon": {"name": "Hackathon",    "start": "Hackathon",     "returns": 1, "count": "Tell opp restlager"},
    "lab":      {"name": "Lab",          "start": "Lab",           "returns": 0, "count": "Innlevering og telling"},
}


def deadlines(g: dict, closed: list[dict]) -> dict:
    """Dates for one run: deadline, prep, start, finish and return."""
    start = week_date(g["school_year"], g["start"])
    finish = max(start, week_date(g["school_year"], g["finish"]))
    deadline = back_open(start, (g.get("lead") or 0) + (g.get("buf") or 0), closed)
    prep = back_open(deadline, 2, closed)
    typ = TYPES.get(g.get("type"), TYPES["hackathon"])
    returns = finish + typ["returns"] * WEEK
    events = [
        {"kind": "prep", "what": "Oppdater BOM og tell lager", **week_info(prep)},
        {"kind": "deadline", "what": "Bestillingsfrist", **week_info(deadline)},
        {"kind": "start", "what": typ["start"], **week_info(start)},
        {"kind": "returns", "what": typ["count"], **week_info(returns)},
    ]
    return {"deadline": week_info(deadline), "prep": week_info(prep), "start": week_info(start),
            "finish": week_info(finish), "returns": week_info(returns), "events": events}
