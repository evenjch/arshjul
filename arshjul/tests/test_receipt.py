"""Receipts: what arrives is added to «På lager» in the BOM, and the count doesn't count it twice."""
import datetime as dt

import bom
import config
import deadlines
from helpers import make_bom

G = "/api/runs"
NOW_SY = deadlines.school_year_for(dt.date.today())


def stock(file):
    return {r["part_number"]: r["in_stock"] for r in bom.read_bom(config.BOM_DIR / file)["rows"]}


def row(client, sy):
    return next(p for p in client.get("/api/state").get_json()["projects"] if p["school_year"] == sy)


def post(client, gid, **body):
    return client.post(f"{G}/{gid}/order", json=body)


def arduino_project(client, sy, stock_before=30):
    """One project, 30 students, one Arduino Uno each."""
    g = client.post(G, json={"name": "Arduino-kurs", "school_year": sy, "start": 34, "finish": 50, "students": 30, "type": "semester_kit"}).get_json()
    file = make_bom(rows=[("Arduino Uno", "Elfa", "A1", 1, stock_before, 250.0)]).name
    client.patch(f"{G}/{g['id']}", json={"bom": file})
    return g["id"], file


def test_lost_two_order_two_receive_two(client):
    """The engineer's example: 30 lent out, 28 came back, order 2, they arrive."""
    last_year, file = arduino_project(client, NOW_SY - 1)
    client.post(f"{G}/{last_year}/count", json={"lines": [{"key": "elfa|a1", "counted": 28}]})
    client.post(f"{G}/{last_year}/count/write")
    assert stock(file) == {"A1": 28}

    client.post(f"/api/school-years/{NOW_SY - 1}/copy")
    this_year = row(client, NOW_SY)["id"]
    assert row(client, NOW_SY)["bomStatus"]["to_order"] == 1  # 30 − 28 = 2 Arduinos to order
    post(client, this_year, action="ordered", supplier="elfa")

    # the two arrive
    r = post(client, this_year, action="receipt", supplier="elfa").get_json()
    assert r["written"] == ["elfa|a1"] and r["backup"]
    assert stock(file) == {"A1": 30}
    p = row(client, NOW_SY)
    elfa = p["orders"]["groups"][0]
    assert (elfa["status"], elfa["extra"], elfa["surplus"]) == ("received", [], [])  # not "ordered too much"
    assert (elfa["lines"], elfa["excl"]) == (1, 500.0)  # what was ordered, even though the BOM says 0 now
    assert p["orders"]["done"] and p["bomStatus"]["to_order"] == 0
    assert [m["remaining"] for m in elfa["receipt"]] == [0]

    # the count after the next project: before = 30, not 32
    g = {l["key"]: l for l in p["count"]["basis"]}["elfa|a1"]
    assert (g["stockBefore"], g["ordered"]) == (30, 0)

    # last year's run no longer compares against the BOM
    assert not row(client, NOW_SY - 1)["orders"]["extra"]


def test_partial_receipt_and_undo(client):
    gid, file = arduino_project(client, NOW_SY + 1, stock_before=27)  # 3 to order
    post(client, gid, action="ordered", supplier="elfa")
    assert post(client, gid, action="receipt", supplier="elfa", lines={"elfa|a1": 1}).status_code == 200
    elfa = row(client, NOW_SY + 1)["orders"]["groups"][0]
    assert (elfa["status"], elfa["receipt"][0]["added"], elfa["receipt"][0]["remaining"]) == ("partial", 1, 2)
    assert stock(file) == {"A1": 28}
    assert elfa["extra"] == [] and elfa["surplus"] == []

    # the count: before = 28 in stock + 2 not put in stock yet = 30
    g = row(client, NOW_SY + 1)["count"]["basis"][0]
    assert g["stockBefore"] + g["ordered"] == 30

    # can't set back to "ordered" or delete the registration while something is in stock
    assert post(client, gid, action="status", supplier="elfa", status="ordered").status_code == 400
    assert post(client, gid, action="undo", supplier="elfa").status_code == 400

    post(client, gid, action="receipt", supplier="elfa")  # resten
    assert stock(file) == {"A1": 30}
    elfa = row(client, NOW_SY + 1)["orders"]["groups"][0]
    assert elfa["status"] == "received" and [x["action"] for x in elfa["stockLog"]] == ["received", "received"]
    assert post(client, gid, action="receipt", supplier="elfa").status_code == 400  # ingenting igjen

    r = post(client, gid, action="undo_receipt", supplier="elfa").get_json()
    assert r["written"] == ["elfa|a1"]
    assert stock(file) == {"A1": 27}
    elfa = row(client, NOW_SY + 1)["orders"]["groups"][0]
    assert (elfa["status"], elfa["receipt"][0]["added"]) == ("ordered", 0)
    assert post(client, gid, action="undo", supplier="elfa").status_code == 200  # now it works


def test_receipt_adds_to_what_is_in_excel(client):
    """If «På lager» has been changed in Excel meanwhile, the receipt is added to the new number."""
    gid, file = arduino_project(client, NOW_SY + 1, stock_before=28)
    post(client, gid, action="ordered", supplier="elfa")
    bom.write_stock(config.BOM_DIR / file, {"elfa|a1": 25}, config.BOM_DIR / "Sikkerhetskopi")  # "Excel"
    post(client, gid, action="receipt", supplier="elfa")
    assert stock(file) == {"A1": 27}


def test_undo_receipt_never_goes_negative(client):
    gid, file = arduino_project(client, NOW_SY + 1, stock_before=28)
    post(client, gid, action="ordered", supplier="elfa")
    post(client, gid, action="receipt", supplier="elfa")  # 28 + 2 = 30
    bom.write_stock(config.BOM_DIR / file, {"elfa|a1": 1}, config.BOM_DIR / "Sikkerhetskopi")  # "Excel": 1 left
    r = post(client, gid, action="undo_receipt", supplier="elfa").get_json()
    assert r["written"] == [] and r["conflict"][0]["description"] == "Arduino Uno"
    assert stock(file) == {"A1": 1}
    assert row(client, NOW_SY + 1)["orders"]["groups"][0]["status"] == "received"  # uendret


def test_invalid_receipt(client):
    gid, _ = arduino_project(client, NOW_SY + 1, stock_before=28)
    assert post(client, gid, action="receipt", supplier="elfa").status_code == 400  # not ordered
    post(client, gid, action="ordered", supplier="elfa")
    assert post(client, gid, action="receipt", supplier="elfa", lines={"elfa|a1": -1}).status_code == 400
    assert post(client, gid, action="receipt", supplier="elfa", lines={"finnes-ikke": 1}).status_code == 400
    assert post(client, gid, action="receipt", supplier="elfa", lines={"elfa|a1": "mange"}).status_code == 400
    assert post(client, gid, action="undo_receipt", supplier="elfa").status_code == 400


def test_write_stock_add():
    path = make_bom(rows=[("Arduino Uno", "Elfa", "A1", 1, 28, 250.0)])
    res = bom.write_stock(path, {"elfa|a1": 2}, config.BOM_DIR / "Sikkerhetskopi", add=True)
    assert res["created"] == {"elfa|a1": 30} and stock(path.name) == {"A1": 30}
    res = bom.write_stock(path, {"elfa|a1": -31}, config.BOM_DIR / "Sikkerhetskopi", add=True)
    assert res["written"] == [] and res["conflict"][0]["actual"] == 30
