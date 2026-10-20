import app as arshjul
import config
from helpers import make_bom

G = "/api/runs"


def make(client, **fields):
    r = client.post(G, json={"name": "Insektsløp", "school_year": 2026, **fields})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_empty_state(client):
    st = client.get("/api/state").get_json()
    assert st["projects"] == []
    assert st["mode"] == "lokal"
    assert len(st["next8"]) == 8
    current = st["schoolYearNow"]
    assert {s["year"] for s in st["school_year"]} >= {current, current + 1}
    assert all(len(s["weeks"]) in (52, 53) for s in st["school_year"])


def test_create_change_delete(client):
    p = make(client, start=44, finish=44)
    assert p["projectId"].startswith("insektsl")
    row = client.get("/api/state").get_json()["projects"][0]
    assert row["name"] == "Insektsløp" and row["school_year"] == 2026 and row["semester"] == "Høst"
    assert row["deadlines"]["start"] == {"date": "2026-10-26", "week": 44, "year": 2026}
    assert len(row["events"]) == 4

    r = client.patch(f"{G}/{p['id']}", json={"students": 30, "note": "IELS1001", "lead": 4})
    assert (r.get_json()["sets"], r.get_json()["lead"]) == (30, 4)

    assert client.delete(f"{G}/{p['id']}").status_code == 200
    assert client.get("/api/state").get_json()["projects"] == []


def test_school_year_shows_in_state(client):
    make(client, school_year=2030, start=3, finish=3)
    st = client.get("/api/state").get_json()
    assert 2030 in {s["year"] for s in st["school_year"]}
    assert st["projects"][0]["deadlines"]["start"]["year"] == 2031


def test_weeks_stay_within_1_to_53(client):
    p = make(client, start=60, finish=0)
    assert (p["start"], p["finish"]) == (53, 1)


def test_name_required(client):
    r = client.post(G, json={"name": "  "})
    assert r.status_code == 400
    assert "navn" in r.get_json()["error"]
    p = make(client)
    assert client.patch(f"{G}/{p['id']}", json={"name": ""}).status_code == 400


def test_invalid_school_year_and_type(client):
    assert client.post(G, json={"name": "X", "school_year": "abc"}).status_code == 400
    assert client.post(G, json={"name": "X", "type": "noe"}).status_code == 400


def test_unknown_run(client):
    assert client.patch(f"{G}/999", json={"students": 1}).status_code == 404


def test_make_template_and_link(client):
    p = make(client)
    r = client.post(f"{G}/{p['id']}/template")
    assert r.status_code == 200
    file = r.get_json()["file"]
    assert (config.BOM_DIR / file).exists()
    st = client.get("/api/state").get_json()["projects"][0]
    assert st["bom"] == file
    assert st["bomStatus"]["exists"] is True


def test_closed_periods(client):
    make(client, start=34, finish=34, lead=1, buf=0)
    deadline = lambda: client.get("/api/state").get_json()["projects"][0]["deadlines"]["deadline"]["week"]
    assert deadline() == 33
    r = client.put("/api/closed-periods", json=[{"name": "Test", "from_week": 33, "to_week": 33}])
    assert r.status_code == 200
    assert deadline() == 32
    assert client.put("/api/closed-periods", json=[{"name": "", "from_week": 1, "to_week": 1}]).status_code == 400
    assert client.put("/api/closed-periods", json=[{"name": "X", "from_week": 60, "to_week": 1}]).status_code == 400


def test_local_only_in_server_mode(client, monkeypatch):
    monkeypatch.setattr(config, "LOCAL", False)
    monkeypatch.setattr(config, "MODE", "server")
    p = make(client)
    assert client.get("/api/state").get_json()["mode"] == "server"
    assert client.post(f"{G}/{p['id']}/open").status_code == 400
    assert client.post("/api/folder/bom").status_code == 400
    # the template still works, but isn't opened
    assert client.post(f"{G}/{p['id']}/template").status_code == 200


def test_data_files_live_in_the_data_folder():
    assert arshjul.store.path.parent == config.DATA_DIR
    assert config.SNAP_FILE.parent.parent == config.DATA_DIR
    assert config.BOM_DIR == config.DATA_DIR / "BOM"


def test_settings_file(tmp_path):
    f = tmp_path / "innstillinger.env"
    f.write_text('\ufeff# kommentar\nARSHJUL_BOM=\\\\server\\deling\\BOM\n\nARSHJUL_PORT = "6000"\nugyldig linje\n',
                 encoding="utf-8")
    assert config.read_settings(f) == {"ARSHJUL_BOM": r"\\server\deling\BOM", "ARSHJUL_PORT": "6000"}
    assert config.read_settings(tmp_path / "finnes-ikke.env") == {}


def test_group_size_drives_the_set_count(client):
    p = make(client, start=44, students=60, group_size=3)
    assert p["sets"] == 20
    r = client.patch(f"{G}/{p['id']}", json={"group_size": 2}).get_json()
    assert (r["students"], r["group_size"], r["sets"]) == (60, 2, 30)
    r = client.patch(f"{G}/{p['id']}", json={"spare": 3, "sets": 5}).get_json()  # sets can't be set directly
    assert r["sets"] == 33
    r = client.patch(f"{G}/{p['id']}", json={"group_size": 0, "students": 9999}).get_json()  # grensene
    assert (r["students"], r["group_size"]) == (2000, 1)
    assert client.patch(f"{G}/{p['id']}", json={"students": ""}).get_json()["sets"] is None


def test_course_students_are_the_default_for_new_projects(client):
    e = client.post("/api/courses", json={"code": "IELS1001", "students": "60"}).get_json()
    assert e["students"] == 60
    p = make(client, start=44, group_size=3, course=e["id"])
    assert (p["students"], p["sets"], p["courseStudents"]) == (60, 20, 60)
    special = make(client, name="Halv klasse", start=45, students=30, course=e["id"])  # differs from the course
    assert special["students"] == 30
    # changing the course doesn't change existing projects
    assert client.patch(f"/api/courses/{e['id']}", json={"students": 70}).get_json()["students"] == 70
    assert client.get("/api/state").get_json()["projects"][0]["students"] == 60
    assert client.patch(f"/api/courses/{e['id']}", json={"students": ""}).get_json()["students"] is None
    assert client.patch(f"/api/courses/{e['id']}", json={"students": "mange"}).status_code == 400


def test_project_types(client):
    p = make(client, start=40, finish=45, type="project")
    assert p["type"] == "project"
    assert client.patch(f"{G}/{p['id']}", json={"type": "lab"}).get_json()["type"] == "lab"
    assert client.patch(f"{G}/{p['id']}", json={"type": "workshop"}).status_code == 400
    assert client.get("/api/state").get_json()["types"] == {
        "semester_kit": "Semestersett", "project": "Prosjekt", "hackathon": "Hackathon", "lab": "Lab"}


def test_closed_period_can_follow_easter(client):
    r = client.put("/api/closed-periods", json=[{"name": "Påske", "from_week": 1, "to_week": 1, "easter": True}])
    assert r.get_json()[0]["easter"] is True
    st = client.get("/api/state").get_json()
    is_closed = {u["week"]: u["closed"] for s in st["school_year"] for u in s["weeks"] if s["year"] == 2027}
    assert is_closed[15] == "Påske" and is_closed[1] == ""  # Easter 2028 is in week 15, not week 1


def test_project_without_bom_can_be_marked_ordered(client):
    p = make(client, start=44)
    r = client.post(f"{G}/{p['id']}/ordered", json={"ordered": True}).get_json()
    assert r["orderedWithoutBom"] and r["orderedWithoutBomBy"]
    assert client.get("/api/state").get_json()["projects"][0]["orderedWithoutBom"] == r["orderedWithoutBom"]
    assert client.post(f"{G}/{p['id']}/ordered", json={"ordered": False}).get_json()["orderedWithoutBom"] is None
    fields = [e["field"] for e in client.get("/api/admin/log").get_json()]
    assert fields.count("bestilt (uten BOM)") == 2

    client.patch(f"{G}/{p['id']}", json={"bom": make_bom().name})  # with a BOM: registered per supplier
    assert client.post(f"{G}/{p['id']}/ordered", json={"ordered": True}).status_code == 400


def test_name_and_note_can_be_changed(client):
    p = make(client, start=44)
    r = client.patch(f"{G}/{p['id']}", json={"name": " Nytt navn ", "note": "IELS1001"}).get_json()
    assert (r["name"], r["note"]) == ("Nytt navn", "IELS1001")
    assert client.patch(f"{G}/{p['id']}", json={"name": "  "}).status_code == 400
