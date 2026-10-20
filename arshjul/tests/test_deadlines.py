import datetime as dt

import deadlines

CLOSED = [
    {"name": "Juleferie", "from_week": 52, "to_week": 1},
    {"name": "Påske", "from_week": 12, "to_week": 12},
    {"name": "Fellesferie", "from_week": 28, "to_week": 31},
]


def run(**kw):
    return {"school_year": 2026, "start": 40, "finish": 40, "lead": 2, "buf": 2, "type": "hackathon", **kw}


def test_in_range_over_new_year():
    assert deadlines.in_range(52, 52, 1)
    assert deadlines.in_range(53, 52, 1)
    assert deadlines.in_range(1, 52, 1)
    assert not deadlines.in_range(2, 52, 1)


def test_school_year_starts_in_week_32():
    assert deadlines.school_year_start(2026) == dt.date(2026, 8, 3)
    assert deadlines.school_year_for(dt.date(2026, 8, 2)) == 2025
    assert deadlines.school_year_for(dt.date(2026, 8, 3)) == 2026
    assert deadlines.school_year_for(dt.date(2027, 6, 1)) == 2026
    assert deadlines.school_year_name(2026) == "2026/27"
    assert deadlines.school_year_name(2099) == "2099/00"


def test_week_date_autumn_and_spring():
    assert deadlines.week_date(2026, 34) == dt.date(2026, 8, 17)
    assert deadlines.week_date(2026, 3) == dt.date(2027, 1, 18)


def test_week_53():
    assert deadlines.weeks_in_year(2026) == 53
    assert deadlines.weeks_in_year(2027) == 52
    assert len(deadlines.school_year_weeks(2026, CLOSED)) == 53
    assert len(deadlines.school_year_weeks(2027, CLOSED)) == 52
    assert deadlines.week_date(2026, 53) == dt.date(2026, 12, 28)
    assert deadlines.week_date(2027, 53) == deadlines.week_date(2027, 52)  # 2027 has no week 53


def test_arduino_deadline_in_june_of_the_previous_school_year():
    # week 34, 2 + 3 weeks, the summer closure is skipped → week 25, 15 June 2026
    f = deadlines.deadlines(run(start=34, finish=50, lead=2, buf=3, type="semester_kit"), CLOSED)
    assert f["deadline"] == {"date": "2026-06-15", "week": 25, "year": 2026}
    assert deadlines.school_year_for(dt.date(2026, 6, 15)) == 2025


def test_deadline_over_christmas_with_week_53():
    # week 2 of 2027, 2 weeks: weeks 1, 53 and 52 are closed → week 50 of 2026
    f = deadlines.deadlines(run(start=2, finish=2, lead=1, buf=1), CLOSED)
    assert (f["deadline"]["week"], f["deadline"]["year"]) == (50, 2026)


def test_without_lead_time_deadline_equals_start():
    f = deadlines.deadlines(run(lead=0, buf=0), CLOSED)
    assert f["deadline"] == f["start"]


def test_finish_before_start_becomes_start():
    f = deadlines.deadlines(run(start=44, finish=40), CLOSED)
    assert f["finish"] == f["start"]


def test_events():
    f = deadlines.deadlines(run(start=44, finish=44), CLOSED)
    kinds = {h["kind"]: h for h in f["events"]}
    assert set(kinds) == {"prep", "deadline", "start", "returns"}
    assert kinds["start"]["what"] == "Hackathon"
    assert kinds["returns"]["week"] == 45  # hackathon: counting the week after
    sets = deadlines.deadlines(run(start=34, finish=50, type="semester_kit"), CLOSED)
    assert {h["kind"]: h for h in sets["events"]}["returns"]["week"] == 50


def test_semester():
    assert deadlines.semester(34) == "Høst"
    assert deadlines.semester(3) == "Vår"


def test_events_per_type():
    def events(type, start, finish):
        h = {x["kind"]: x for x in deadlines.deadlines(run(start=start, finish=finish, type=type), CLOSED)["events"]}
        return h["start"]["what"], h["returns"]["what"], h["returns"]["week"]
    assert events("semester_kit", 34, 50) == ("Utdeling", "Innlevering og telling", 50)
    assert events("project", 40, 45) == ("Prosjektstart", "Innlevering og telling", 46)
    assert events("hackathon", 44, 44) == ("Hackathon", "Tell opp restlager", 45)
    assert events("lab", 44, 44) == ("Lab", "Innlevering og telling", 44)  # utstyret kommer tilbake samme dag
    assert events("ukjent", 44, 44) == ("Hackathon", "Tell opp restlager", 45)


def test_page_opens_on_next_school_year_in_summer():
    assert deadlines.school_year_shown(dt.date(2026, 6, 21)) == 2025  # week 25: still the current year
    assert deadlines.school_year_shown(dt.date(2026, 6, 22)) == 2026  # week 26: the autumn's orders are done
    assert deadlines.school_year_shown(dt.date(2026, 8, 3)) == 2026
    assert deadlines.school_year_shown(dt.date(2027, 1, 10)) == 2026


def test_easter_moves():
    assert deadlines.easter_sunday(2026) == dt.date(2026, 4, 5)
    assert deadlines.easter_sunday(2027) == dt.date(2027, 3, 28)
    assert deadlines.easter_sunday(2028) == dt.date(2028, 4, 16)
    assert [deadlines.easter_week(year) for year in (2026, 2027, 2028)] == [14, 12, 15]
    easter = [{"name": "Påske", "from_week": 12, "to_week": 12, "easter": True}]
    assert deadlines.is_closed(dt.date(2028, 4, 13), easter) and not deadlines.is_closed(dt.date(2028, 3, 23), easter)
    assert deadlines.week_info(dt.date(2028, 4, 13), easter)["closed"] == "Påske"


def test_deadline_skips_the_right_easter_week():
    # start week 17 of 2028, 2 weeks: Easter is week 15 in 2028 → the deadline lands in week 14
    easter = [{"name": "Påske", "from_week": 12, "to_week": 12, "easter": True}]
    f = deadlines.deadlines(run(school_year=2027, start=17, finish=17, lead=1, buf=1), easter)
    assert f["deadline"]["week"] == 14
