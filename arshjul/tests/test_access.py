"""Login, roles and the change log – as it's meant to work on the server."""
import datetime as dt
import sqlite3
from contextlib import closing

import pytest

import app as arshjul
import auth
import config
import deadlines
import manage
from conftest import with_csrf
from helpers import make_bom

G = "/api/runs"
SY = deadlines.school_year_for(dt.date.today()) + 1
PASSWORD = "et-langt-passord"


@pytest.fixture
def logins(monkeypatch):
    ids = {}
    for name, full_name, role in (("eng", "Ingrid Ingeniør", "engineer"), ("ola", "Ola Faglærer", "lecturer"),
                               ("kari", "Kari Faglærer", "lecturer")):
        ids[name] = arshjul.store.new_user(name, full_name, role, auth.hash_password(PASSWORD))
    ids["course_ola"] = arshjul.store.new_course({"code": "OLA1", "lecturer_id": ids["ola"], "engineer_id": ids["eng"]})["id"]
    ids["course_kari"] = arshjul.store.new_course({"code": "KARI1", "lecturer_id": ids["kari"], "engineer_id": ids["eng"]})["id"]
    auth._ip_failures.clear()
    return ids


def new_client():
    arshjul.app.config["TESTING"] = True
    return arshjul.app.test_client()


def log_in(username, password=PASSWORD, next_=None):
    c = new_client()
    c.get("/login")  # gives the session a CSRF token
    with c.session_transaction() as s:
        token = s["csrf"]
    r = c.post("/login" + (f"?next={next_}" if next_ else ""), data={"username": username, "password": password, "csrf": token})
    with c.session_transaction() as s:
        new = s.get("csrf")
    if r.status_code == 302:
        assert new != token  # nytt token ved innlogging
    c.environ_base["HTTP_X_CSRF_TOKEN"] = new or token
    c.reply = r
    return c


def state(c):
    return c.get("/api/state").get_json()


# ---------- innlogging ----------

def test_not_logged_in(logins):
    c = new_client()
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    r = c.get("/api/state")
    assert r.status_code == 401 and r.get_json()["login"] is True


def test_log_in_and_out(logins):
    c = log_in("ola")
    assert c.reply.status_code == 302 and c.reply.headers["Location"] == "/"
    assert state(c)["me"] == {"id": logins["ola"], "name": "Ola Faglærer", "role": "lecturer", "admin": False}
    assert "Ola Faglærer" in c.get("/").get_data(as_text=True)
    c.post("/logout", data={"csrf": c.environ_base["HTTP_X_CSRF_TOKEN"]})
    assert c.get("/api/state").status_code == 401


def test_usernames_are_case_insensitive(logins):
    assert log_in("OLA").reply.status_code == 302


def test_wrong_password_and_lockout(logins):
    for _ in range(auth.MAX_FAILURES):
        r = log_in("ola", "feil-passord").reply
        assert r.status_code == 401 and "Feil brukernavn eller passord" in r.get_data(as_text=True)
    r = log_in("ola").reply  # right password, but locked
    assert r.status_code == 401 and "For mange feil forsøk" in r.get_data(as_text=True)
    arshjul.store.update_user(logins["ola"], locked_until=None)  # like «manage.py aktiver»
    assert log_in("ola").reply.status_code == 302


def test_unknown_user_gives_the_same_message(logins):
    r = log_in("finnes-ikke").reply
    assert r.status_code == 401 and "Feil brukernavn eller passord" in r.get_data(as_text=True)


def test_next_cant_send_you_off_site(logins):
    assert log_in("ola", next_="//ondsinnet.no").reply.headers["Location"] == "/"
    assert log_in("ola", next_="https://ondsinnet.no").reply.headers["Location"] == "/"
    assert log_in("ola", next_="/api/state").reply.headers["Location"] == "/api/state"


def test_login_keeps_the_query(logins):
    # a widget (/uker?widget) whose session ran out should come back as a widget
    r = new_client().get("/uker?widget")
    assert r.status_code == 302
    next_ = r.headers["Location"].split("next=", 1)[1]
    assert log_in("ola", next_=next_).reply.headers["Location"] == "/uker?widget"
    assert new_client().get("/uker").headers["Location"].endswith("/login?next=/uker")


def test_deactivation_and_new_password_end_the_session(logins):
    c = log_in("ola")
    arshjul.store.update_user(logins["ola"], active=0)
    assert c.get("/api/state").status_code == 401
    arshjul.store.update_user(logins["ola"], active=1)
    c = log_in("ola")
    arshjul.store.update_user(logins["ola"], password=auth.hash_password("et-helt-nytt-passord"))
    assert c.get("/api/state").status_code == 401


def test_csrf_required(logins):
    c = log_in("eng")
    c.environ_base.pop("HTTP_X_CSRF_TOKEN")
    r = c.post(G, json={"name": "Uten token", "school_year": SY})
    assert r.status_code == 400 and "utdatert" in r.get_json()["error"]


def test_csrf_required_locally_too():
    """Without this, another website could make the browser change the app."""
    r = new_client().post(G, json={"name": "Fra en annen side", "school_year": SY})
    assert r.status_code == 400


def test_security_headers(client):
    h = client.get("/").headers
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert client.get("/api/state").headers["Cache-Control"] == "no-store"


# ---------- lecturer ----------

def run_with_bom(c, name="Insektsløp", **kw):
    g = c.post(G, json={"name": name, "school_year": SY, "start": 44, "finish": 44, "students": 30, **kw}).get_json()
    file = make_bom(f"{name} – BOM.xlsx", rows=[("Arduino", "Elfa", "A1", 1, 0, 250.0)]).name
    c.patch(f"{G}/{g['id']}", json={"bom": file})
    return g


def test_lecturer_doesnt_see_bom_or_costs(logins):
    eng = log_in("eng")
    g = run_with_bom(eng, course=logins["course_ola"])
    eng.post(f"{G}/{g['id']}/order", json={"action": "ordered", "supplier": "elfa"})
    ola = log_in("ola")
    st = state(ola)
    p = st["projects"][0]
    for fields in ("bom", "bomStatus", "orders", "count", "orderedCost", "deleted"):
        assert fields not in p
    assert p["own"] is True and p["lecturerName"] == "Ola Faglærer" and p["deadlines"]["deadline"]["week"]
    assert (st["freeBoms"], st["folders"], st["changes"], st["deletedRuns"], st["lecturers"], st["setup"]) == ([], {}, [], [], [], None)
    assert [e["code"] for e in st["courses"]] == ["OLA1"]  # only their own courses
    assert state(log_in("kari"))["projects"] == []  # not Kari's course: can't see it


def test_lecturer_creates_and_changes_own_projects(logins):
    ola = log_in("ola")
    assert ola.post(G, json={"name": "Uten emne", "school_year": SY}).status_code == 403
    assert ola.post(G, json={"name": "Karis emne", "school_year": SY, "course": logins["course_kari"]}).status_code == 403
    r = ola.post(G, json={"name": "Lodding", "school_year": SY, "start": 40, "lead": 9, "buf": 9, "bom": "x.xlsx", "color": 3,
                          "course": logins["course_ola"]})
    g = r.get_json()
    assert (g["lecturerId"], g["courseCode"], g["lead"], g["buf"], g["bom"], g["color"]) == (logins["ola"], "OLA1", 2, 2, None, 3)

    assert ola.patch(f"{G}/{g['id']}", json={"start": 42, "students": 12, "note": "IELS1001"}).status_code == 200
    r = ola.patch(f"{G}/{g['id']}", json={"lead": 1})
    assert r.status_code == 403 and "leveringstid" in r.get_json()["error"]
    assert ola.patch(f"{G}/{g['id']}", json={"bom": "annen.xlsx"}).status_code == 403
    assert ola.patch(f"{G}/{g['id']}", json={"course": logins["course_kari"]}).status_code == 403

    kari = log_in("kari")
    r = kari.patch(f"{G}/{g['id']}", json={"start": 30})
    assert r.status_code == 403 and "egne emner" in r.get_json()["error"]
    assert kari.delete(f"{G}/{g['id']}").status_code == 403


@pytest.mark.parametrize("method,file_path,body", [
    ("post", "/{id}/template", None), ("post", "/{id}/open", None), ("post", "/{id}/order-list", None),
    ("post", "/{id}/order", {"action": "ordered", "supplier": "elfa"}), ("post", "/{id}/seen", None),
    ("post", "/{id}/count", {"lines": [{"key": "elfa|a1", "counted": 1}]}), ("post", "/{id}/count/write", None),
    ("post", "/{id}/count/undo", None), ("post", "/{id}/restore", None), ("post", "/{id}/bom/sets", None), ("post", "/{id}/ordered", {"ordered": True}),
])
def test_lecturer_cant_do_engineer_actions(logins, method, file_path, body):
    eng = log_in("eng")
    g = run_with_bom(eng, course=logins["course_ola"])
    ola = log_in("ola")
    r = getattr(ola, method)(G + file_path.format(id=g["id"]), json=body)
    assert r.status_code == 403


def test_lecturer_cant_do_global_actions(logins):
    ola = log_in("ola")
    assert ola.put("/api/closed-periods", json=[]).status_code == 403
    assert ola.post(f"/api/school-years/{SY}/copy").status_code == 403
    assert ola.post("/api/changes/1/acknowledge").status_code == 403
    assert ola.post("/api/folder/bom").status_code == 403


# ---------- change log and notifications ----------

def test_changes_are_notified_and_acknowledged(logins):
    eng = log_in("eng")
    g = run_with_bom(eng, course=logins["course_ola"])
    assert state(eng)["changes"] == []  # the engineer's own changes aren't notified

    ola = log_in("ola")
    ola.patch(f"{G}/{g['id']}", json={"start": 42, "finish": 42})
    ola.patch(f"{G}/{g['id']}", json={"students": 60, "group_size": 2, "type": "semester_kit"})
    chg = state(eng)["changes"]
    # 60 students in groups of 2 is still 30 sets – so the set count isn't logged
    assert sorted((e["user_name"], e["field"], e["from_value"], e["to_value"]) for e in chg) == sorted([
        ("Ola Faglærer", "uke fra", "44", "42"), ("Ola Faglærer", "uke til", "44", "42"),
        ("Ola Faglærer", "studenter", "30", "60"), ("Ola Faglærer", "per gruppe", "1", "2"),
        ("Ola Faglærer", "type", "hackathon", "semester_kit")])
    assert {e["run_id"] for e in chg} == {int(g["id"])}

    ola.patch(f"{G}/{g['id']}", json={"students": 60})  # no change – no log
    assert len(state(eng)["changes"]) == 5

    ola.patch(f"{G}/{g['id']}", json={"group_size": 3})  # 60/3 = 20 sets: what the engineer orders from, notified too
    created = [(e["field"], e["from_value"], e["to_value"]) for e in state(eng)["changes"]]
    assert ("per gruppe", "2", "3") in created and ("antall sett", "30", "20") in created and len(created) == 7

    assert eng.post(f"/api/changes/{g['id']}/acknowledge").get_json() == {"acknowledged": 7}
    assert state(eng)["changes"] == []


def test_engineer_chooses_course(logins):
    eng = log_in("eng")
    g = eng.post(G, json={"name": "Uten emne", "school_year": SY}).get_json()
    assert (g["courseId"], g["lecturerId"]) == (None, None)
    assert log_in("kari").patch(f"{G}/{g['id']}", json={"start": 30}).status_code == 403  # no lecturer yet
    assert eng.patch(f"{G}/{g['id']}", json={"course": 9999}).status_code == 400
    r = eng.patch(f"{G}/{g['id']}", json={"course": logins["course_kari"]})
    assert (r.get_json()["courseCode"], r.get_json()["lecturerName"]) == ("KARI1", "Kari Faglærer")
    assert log_in("kari").patch(f"{G}/{g['id']}", json={"start": 30}).status_code == 200
    st = state(eng)
    assert [f["name"] for f in st["lecturers"]] == ["Kari Faglærer", "Ola Faglærer"]
    assert [f["name"] for f in st["engineers"]] == ["Ingrid Ingeniør"]


def test_lecturer_removes_engineer_restores_or_deletes(logins):
    eng = log_in("eng")
    g = run_with_bom(eng, course=logins["course_ola"])
    ola = log_in("ola")
    assert ola.delete(f"{G}/{g['id']}").get_json() == {"ok": True, "waiting": True}
    assert state(ola)["projects"] == [] and state(eng)["projects"] == []
    dl = state(eng)["deletedRuns"]
    assert [(s["name"], s["deletedBy"]) for s in dl] == [("Insektsløp", "Ola Faglærer")]
    assert ola.patch(f"{G}/{g['id']}", json={"start": 40}).status_code == 404

    eng.post(f"{G}/{g['id']}/restore")
    assert [p["name"] for p in state(ola)["projects"]] == ["Insektsløp"]
    assert state(eng)["deletedRuns"] == [] and state(eng)["changes"] == []

    ola.delete(f"{G}/{g['id']}")
    assert eng.delete(f"{G}/{g['id']}").status_code == 200  # «Bekreft sletting»
    assert state(eng)["deletedRuns"] == [] and state(eng)["projects"] == []


def test_removed_project_is_not_copied(logins):
    eng = log_in("eng")
    g = eng.post(G, json={"name": "Kurs", "school_year": SY, "course": logins["course_ola"]}).get_json()
    log_in("ola").delete(f"{G}/{g['id']}")
    assert eng.post(f"/api/school-years/{SY}/copy").get_json()["created"] == []


def test_who_ordered_is_saved(logins):
    eng = log_in("eng")
    g = run_with_bom(eng)
    eng.post(f"{G}/{g['id']}/order", json={"action": "ordered", "supplier": "elfa"})
    eng.post(f"{G}/{g['id']}/order", json={"action": "receipt", "supplier": "elfa"})
    b = arshjul.store.all_orders()[g["id"]]["elfa"]
    assert b["made_by"] == "Ingrid Ingeniør" and b["stock_log"][0]["by"] == "Ingrid Ingeniør"


# ---------- manage.py ----------

def test_manage(monkeypatch, capsys):
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", "passord-som-er-langt")
    manage.main(["ny-bruker", "per", "Per Hansen", "faglarer"])
    u = arshjul.store.user(username="per")
    assert (u["name"], u["role"], u["active"]) == ("Per Hansen", "lecturer", 1)
    assert u["password"] != "passord-som-er-langt"  # only the hash
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "PER", "Dobbel", "faglarer"])
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "med mellomrom", "X", "faglarer"])
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", "kort")
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "lise", "Lise", "ingenior"])
    manage.main(["deaktiver", "per"])
    assert arshjul.store.user(username="per")["active"] == 0
    manage.main(["aktiver", "per"])
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", "et-annet-langt-passord")
    old_count = u["password"]
    manage.main(["passord", "per"])
    assert arshjul.store.user(username="per")["password"] != old_count
    manage.main(["liste"])
    assert "Per Hansen" in capsys.readouterr().out


def test_manage_daily_backup(client, capsys):
    folder = config.DATA_DIR / "Sikkerhetskopi-db"
    folder.mkdir(exist_ok=True)
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        (folder / f"arshjul {day} daglig.db").write_bytes(b"old")
    (folder / "arshjul 2026-08-01 120000.db").write_bytes(b"before an upgrade")  # never cleaned up
    manage.main(["sikkerhetskopi", "--behold", "2"])
    assert "Fjernet 2 gamle" in capsys.readouterr().out
    names = sorted(f.name for f in folder.iterdir())
    today = f"arshjul {dt.date.today():%Y-%m-%d} daglig.db"
    assert names == ["arshjul 2026-08-01 120000.db", "arshjul 2026-09-03 daglig.db", today]
    with closing(sqlite3.connect(folder / today)) as con:  # a real database, not a half-written file
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 1
    manage.main(["sikkerhetskopi", "--behold", "2"])  # twice the same day: replaced, not a third copy
    assert len(list(folder.glob("*daglig.db"))) == 2
    # the admin panel can download it
    r = client.get(f"/api/admin/backup/{today}")
    assert r.status_code == 200
    r.close()  # Windows: the file can't be removed by the next test while it's open


# ---------- the page, as lecturer and as engineer with notifications ----------

def test_page_for_both_roles(logins):
    from test_page import run_page

    eng = log_in("eng")
    g = run_with_bom(eng, course=logins["course_ola"])
    eng.post(f"{G}/{g['id']}/order", json={"action": "ordered", "supplier": "elfa"})
    annen = eng.post(G, json={"name": "Smart City", "school_year": SY, "start": 15, "course": logins["course_ola"]}).get_json()
    eng.post(G, json={"name": "Karis prosjekt", "school_year": SY, "course": logins["course_kari"]})

    ola = log_in("ola")
    ola.patch(f"{G}/{g['id']}", json={"start": 40, "students": 35})
    assert ola.post(G, json={"name": "Olas nye", "school_year": SY, "start": 20, "course": logins["course_ola"]}).status_code == 200
    ola.delete(f"{G}/{annen['id']}")

    res = run_page(state(ola))
    assert res["errors"] == []
    assert "Fjern" in res["tbody"] and "OLA1 · faglærer Ola Faglærer (deg) · ingeniør Ingrid Ingeniør" in res["tbody"]
    assert "Registrer bestilt" not in res["tbody"] and "Lag BOM-mal" not in res["tbody"]
    assert 'data-k="lead"' not in res["tbody"]  # a lecturer can't change the lead time
    assert "Karis prosjekt" not in res["tbody"]  # sees only their own courses
    # the wheel: only the period the project is in use – no ordering/delivery, deadline or count
    assert 'data-kind="use"' in res["wheel"]
    assert 'data-kind="lead"' not in res["wheel"] and "bestillingsfrist" not in res["wheel"]
    assert "neste frist" not in res["wheel"] and "innlevering og telling" not in res["wheel"]

    res = run_page(state(eng))
    assert res["errors"] == []
    n = res["notices"]
    assert "Ola Faglærer endret «Insektsløp»" in n and "uke fra" in n and "OK, sett" in n
    assert "Ola Faglærer fjernet «Smart City»" in n and "Gjenopprett" in n
    assert "La til prosjektet" in n  # Olas nye
    assert 'data-act="course"' in res["tbody"]
    assert 'data-kind="lead"' in res["wheel"] and "bestillingsfrist" in res["wheel"]  # the engineer sees everything
