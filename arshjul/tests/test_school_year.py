import datetime as dt

import app as arshjul
import orders
import config
import deadlines
from db import Db
from helpers import make_bom

G = "/api/runs"
CLOSED = [{"name": "Juleferie", "from_week": 52, "to_week": 1}, {"name": "Fellesferie", "from_week": 28, "to_week": 31}]


def run(pid, sy, start, **kw):
    return {"id": pid + str(sy), "projectId": pid, "name": pid.title(), "school_year": sy, "start": start,
            "finish": start, "lead": 2, "buf": 3, "type": "hackathon", **kw}


# ---------- warning about the next school year ----------

def test_warning_in_spring():
    rows = [run("kit", 2026, 34), run("insekt", 2026, 44)]
    # kit 2027/28: week 34 → deadline week 25, 21 June 2027
    assert deadlines.setup_warning(rows, CLOSED, dt.date(2027, 3, 15)) is None  # 14 uker igjen
    v = deadlines.setup_warning(rows, CLOSED, dt.date(2027, 4, 5))
    assert (v["school_year"], v["from"], v["name"], v["project"], v["missing"]) == (2027, 2026, "2027/28", "Kit", 2)
    assert (v["deadline"]["week"], v["deadline"]["year"], v["weeks"]) == (25, 2027, 11)


def test_no_warning_when_next_year_is_set_up():
    rows = [run("kit", 2026, 34), run("kit", 2027, 34)]
    assert deadlines.setup_warning(rows, CLOSED, dt.date(2027, 4, 5)) is None


def test_warning_after_the_deadline_has_passed():
    # August 2027: 2027/28 has started without being set up
    v = deadlines.setup_warning([run("kit", 2026, 34)], CLOSED, dt.date(2027, 8, 9))
    assert v["school_year"] == 2027 and v["weeks"] < 0


def test_warning_for_one_missing_project():
    rows = [run("kit", 2026, 34), run("insekt", 2026, 44), run("insekt", 2027, 44)]
    v = deadlines.setup_warning(rows, CLOSED, dt.date(2027, 4, 5))
    assert (v["missing"], v["project"]) == (1, "Kit")


# ---------- kopiering ----------

def test_copy_to_next_school_year():
    db = Db(config.DB_FILE)
    a = db.new("kit", 2026, {"name": "Kit", "start": 34, "finish": 50, "students": 60})
    b = db.new("insekt", 2026, {"name": "Insekt", "start": 44, "finish": 44, "students": 30})
    db.new_order(a["id"], "elfa", "Elfa", date="2026-06-01", order_no="", sets=60, lines=[])

    assert len(db.copy_year(2026, [a["id"]])) == 1
    next_ = [g for g in db.runs() if g["school_year"] == 2027]
    assert [(g["projectId"], g["start"], g["finish"], g["sets"]) for g in next_] == [("kit", 34, 50, 60)]
    assert list(db.all_orders()) == [a["id"]]  # orders don't come along

    assert len(db.copy_year(2026)) == 1  # only the insect project is left
    assert db.copy_year(2026) == []
    assert b["id"] not in {g["id"] for g in db.runs() if g["school_year"] == 2027}


def test_copy_via_api(client):
    a = client.post(G, json={"name": "Kit", "school_year": 2026, "start": 34, "finish": 50, "students": 60}).get_json()
    client.post(G, json={"name": "Insekt", "school_year": 2026, "start": 44})
    r = client.post("/api/school-years/2026/copy", json={"ids": [a["id"]]})
    assert r.get_json()["name"] == "2027/28" and len(r.get_json()["created"]) == 1
    st = client.get("/api/state").get_json()
    copy = [p for p in st["projects"] if p["school_year"] == 2027]
    assert [(p["name"], p["deadlines"]["start"]["year"]) for p in copy] == [("Kit", 2027)]
    assert client.post("/api/school-years/2026/copy", json={"ids": "all"}).status_code == 400


# ---------- historikk ----------

def test_cost_from_frozen_copies():
    assert orders.cost({}) is None
    saved = {"elfa": {"lines": [{"quantity": 6, "price": 250.0}, {"quantity": 10, "price": 0.5}]},
              "kjell": {"lines": [{"quantity": 20, "price": 10.0}]}}
    assert orders.cost(saved) == 1705.0


def test_ordered_cost_in_state(client):
    g = client.post(G, json={"name": "Test", "school_year": 2026, "start": 44, "students": 10}).get_json()
    file = make_bom(rows=[("Arduino", "Elfa", "A1", 1, 4, 250.0), ("Ledning", "Kjell", "L1", 2, 0, 10.0)]).name
    client.patch(f"{G}/{g['id']}", json={"bom": file})
    state = lambda: client.get("/api/state").get_json()["projects"][0]
    assert state()["orderedCost"] is None
    client.post(f"{G}/{g['id']}/order", json={"action": "ordered", "supplier": "elfa"})
    assert state()["orderedCost"] == 1500.0
    # the cost stands even if the BOM is removed later
    (config.BOM_DIR / file).unlink()
    assert state()["orderedCost"] == 1500.0


def test_copy_with_changes(client):
    a = client.post(G, json={"name": "Kit", "school_year": 2026, "start": 34, "finish": 50, "students": 60, "group_size": 3}).get_json()
    b = client.post(G, json={"name": "Lodding", "school_year": 2026, "start": 44, "students": 30}).get_json()
    r = client.post("/api/school-years/2026/copy", json={"ids": [a["id"], b["id"]],
                                                       "changes": {a["id"]: {"start": 35, "students": 72, "sets": 999}}})
    assert r.status_code == 200, r.get_json()
    next_ = {g["name"]: g for g in arshjul.store.runs() if g["school_year"] == 2027}
    assert (next_["Kit"]["start"], next_["Kit"]["finish"], next_["Kit"]["students"], next_["Kit"]["sets"]) == (35, 50, 72, 24)
    assert (next_["Lodding"]["start"], next_["Lodding"]["sets"]) == (44, 30)  # without changes: as this year
    assert client.post("/api/school-years/2026/copy", json={"changes": {a["id"]: "x"}}).status_code == 400
