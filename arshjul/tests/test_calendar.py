"""The calendar feed: valid iCalendar, only your own projects, and a link that can be replaced."""
import datetime as dt

import app as arshjul
import auth
import calendar_feed
import deadlines
from conftest import as_user

G = "/api/runs"
NEXT_SY = deadlines.school_year_for(dt.date.today()) + 1


def unfold(ics: str) -> list[str]:
    return ics.replace("\r\n ", "").split("\r\n")


def link(client, new=False) -> str:
    url = client.post("/api/calendar", json={"new": True} if new else None).get_json()["url"]
    return url.split("localhost", 1)[-1]


def test_escape_and_fold():
    assert calendar_feed.escape("a,b;c\\d\nny linje") == "a\\,b\\;c\\\\d\\nny linje"
    line = "SUMMARY:" + "Bestillingsfrist: Lodde-workshop med æøå " * 4
    folded = calendar_feed.fold(line)
    assert all(len(part.encode("utf-8")) <= 75 for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line  # nothing lost, and never split inside a character


def test_engineer_feed(client):
    g = client.post(G, json={"name": "Kit, med komma", "school_year": NEXT_SY, "start": 34, "finish": 50,
                             "type": "semester_kit", "students": 30}).get_json()
    r = client.get(link(client))
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    ics = r.get_data(as_text=True)
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    lines = unfold(ics)
    assert f"UID:run-{g['id']}-use@arshjul" in lines and f"UID:run-{g['id']}-deadline@arshjul" in lines
    assert f"UID:run-{g['id']}-returns@arshjul" in lines and f"UID:run-{g['id']}-prep@arshjul" in lines
    start = deadlines.week_date(NEXT_SY, 34)
    assert f"DTSTART;VALUE=DATE:{start:%Y%m%d}" in lines  # the use period starts on Monday of week 34
    assert any(l.startswith("SUMMARY:Bestillingsfrist: Kit\\, med komma (uke ") for l in lines)
    assert "DESCRIPTION:uke 34–50 · 30 sett" in lines
    assert client.get(link(client) + "?download=1").headers["Content-Disposition"].startswith("attachment")


def test_lecturer_sees_only_the_use_period(client):
    lecturer = arshjul.store.new_user("ola", "Ola", "lecturer", auth.hash_password("passord-som-er-langt"))
    course = client.post("/api/courses", json={"code": "IELS1001", "lecturer": lecturer}).get_json()
    g = client.post(G, json={"name": "Lodding", "school_year": NEXT_SY, "start": 44, "course": course["id"]}).get_json()
    client.post(G, json={"name": "Ikke Olas", "school_year": NEXT_SY, "start": 40})  # no course: not Ola's
    with arshjul.app.test_client() as ola:
        as_user(ola, lecturer)
        lines = unfold(ola.get(link(ola)).get_data(as_text=True))
    assert f"UID:run-{g['id']}-use@arshjul" in lines
    assert not any("-deadline@" in l or "-prep@" in l or "-returns@" in l for l in lines)
    assert not any("Ikke Olas" in l for l in lines)


def test_other_engineers_projects_are_not_in_the_feed(client):
    other = arshjul.store.new_user("bjorn", "Bjørn", "engineer", auth.hash_password("passord-som-er-langt"))
    course = client.post("/api/courses", json={"code": "IELS2001", "engineer": other}).get_json()
    client.post(G, json={"name": "Bjørns prosjekt", "school_year": NEXT_SY, "start": 44, "course": course["id"]})
    ics = client.get(link(client)).get_data(as_text=True)
    assert "Bjørns prosjekt" not in ics


def test_unknown_new_and_deactivated_links(client):
    assert client.get("/calendar/finnes-ikke.ics").status_code == 404
    old = link(client)
    assert link(client) == old  # the same link until you ask for a new one
    new = link(client, new=True)
    assert new != old and client.get(old).status_code == 404 and client.get(new).status_code == 200
    with arshjul.app.test_client() as anonymous:  # Outlook isn't logged in
        assert anonymous.get(new).status_code == 200
    admin = arshjul.store.user(username="admin")
    arshjul.store.update_user(admin["id"], active=0)
    with arshjul.app.test_client() as anonymous:
        assert anonymous.get(new).status_code == 404
