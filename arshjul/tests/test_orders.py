import datetime as dt

import orders
import config
import deadlines
from helpers import make_bom, write_rows

G = "/api/runs"
# "needs more" is only checked before the project starts: use next school year, so the tests don't expire
NEXT_SY = deadlines.school_year_for(dt.date.today()) + 1


def row(desc, supplier, to_order, price=10.0, part_no=""):
    return {"description": desc, "supplier": supplier, "part_number": part_no, "to_order": to_order,
            "price": price, "total": round(to_order * price, 2)}


def saved(lines, status="ordered", name="Elfa"):
    return {"name": name, "status": status, "lines": lines, "ordered_date": "2026-09-01",
            "received_date": None, "order_no": ""}


# ---------- logikken ----------

def test_groups_by_supplier():
    g = orders.groups([row("A", "Elfa", 1), row("B", " elfa ", 1), row("C", "", 1), row("D", "Kjell", 1)])
    assert [(x["key"], x["name"], len(x["rows"])) for x in g] == [
        ("elfa", "Elfa", 2), ("", "Ukjent leverandør", 1), ("kjell", "Kjell", 1)]


def test_freeze_takes_only_what_is_to_be_ordered():
    lines = orders.freeze([row("A", "Elfa", 6, part_no="A1"), row("B", "Elfa", 0)])
    assert [(l["key"], l["quantity"]) for l in lines] == [("elfa|a1", 6)]


def test_deviation_extra_and_surplus():
    frozen = orders.freeze([row("A", "Elfa", 6), row("B", "Elfa", 5)])
    extra, surplus = orders.deviation(frozen, [row("A", "Elfa", 8), row("B", "Elfa", 3), row("C", "Elfa", 2)])
    assert [(t["description"], t["missing"]) for t in extra] == [("A", 2), ("C", 2)]
    assert [(o["description"], o["ordered"], o["needs"]) for o in surplus] == [("B", 5, 3)]


def test_merge_keeps_the_largest():
    old_count = orders.freeze([row("A", "Elfa", 6), row("B", "Elfa", 5)])
    new = orders.freeze([row("A", "Elfa", 8), row("B", "Elfa", 3)])
    assert {l["key"]: l["quantity"] for l in orders.merge(old_count, new)} == {"elfa|a": 8, "elfa|b": 5}


def test_overview_status():
    rows = [row("A", "Elfa", 6), row("B", "Kjell", 2), row("C", "Lager AS", 0)]
    o = orders.overview(rows, {})
    assert (o["status"], o["done"], o["suppliers"]) == ("none", False, 2)
    assert {g["key"]: g["status"] for g in o["groups"]}["lager as"] == "in_stock"

    elfa = saved(orders.freeze(rows[:1]))
    o = orders.overview(rows, {"elfa": elfa})
    assert (o["status"], o["orderedCount"]) == ("partially_ordered", 1)

    kjell = saved(orders.freeze(rows[1:2]), status="received", name="Kjell")
    o = orders.overview(rows, {"elfa": elfa, "kjell": kjell})
    assert (o["status"], o["done"]) == ("ordered", True)  # the worse of ordered and received


def test_everything_in_stock():
    o = orders.overview([row("A", "Elfa", 0)], {})
    assert (o["status"], o["done"]) == ("in_stock", True)


def test_supplier_removed_from_bom_after_ordering():
    o = orders.overview([row("A", "Kjell", 0)], {"elfa": saved(orders.freeze([row("X", "Elfa", 3)]))})
    elfa = next(g for g in o["groups"] if g["key"] == "elfa")
    assert elfa["removed"] and elfa["surplus"][0]["ordered"] == 3


# ---------- through the API ----------

ROWS = [
    ("Arduino Uno", "Elfa", "A1", 1, 4, 250.0),    # 1 × 10 − 4 = 6
    ("Motstand 220", "Elfa", "R220", 10, 500, 0.5),  # in stock
    ("Ledning", "Kjell", "L1", 2, 0, 10.0),          # 20
    ("Skrue", "", "S1", 4, 0, 1.0),                  # 40, unknown supplier
]


def setup(client):
    g = client.post(G, json={"name": "Testprosjekt", "school_year": NEXT_SY, "start": 44, "finish": 44, "students": 10}).get_json()
    file = make_bom(rows=ROWS).name
    client.patch(f"{G}/{g['id']}", json={"bom": file})
    return g["id"]


def overview(client):
    return client.get("/api/state").get_json()["projects"][0]["orders"]


def post(client, gid, **body):
    return client.post(f"{G}/{gid}/order", json=body)


def test_order_flow(client):
    gid = setup(client)
    b = overview(client)
    assert (b["status"], b["suppliers"]) == ("none", 3)
    assert [g["key"] for g in b["groups"]] == ["elfa", "kjell", ""]

    assert post(client, gid, action="ordered", supplier="elfa", order_no="PO-1").status_code == 200
    b = overview(client)
    assert (b["status"], b["orderedCount"]) == ("partially_ordered", 1)
    elfa = b["groups"][0]
    assert (elfa["status"], elfa["order_no"]) == ("ordered", "PO-1") and elfa["orderedDate"]
    assert post(client, gid, action="ordered", supplier="elfa").status_code == 400  # allerede bestilt

    post(client, gid, action="ordered", supplier="kjell")
    post(client, gid, action="ordered", supplier="")
    b = overview(client)
    assert (b["status"], b["done"], b["extra"]) == ("ordered", True, False)

    # the lecturer increases to 12 sets after ordering
    client.patch(f"{G}/{gid}", json={"students": 12})
    b = overview(client)
    assert (b["done"], b["extra"]) == (False, True)
    missing = {g["key"]: [t["missing"] for t in g["extra"]] for g in b["groups"]}
    assert missing == {"elfa": [2], "kjell": [4], "": [8]}

    post(client, gid, action="extra", supplier="elfa", order_no="PO-2")
    elfa = overview(client)["groups"][0]
    assert (elfa["extra"], elfa["order_no"]) == ([], "PO-1 + PO-2")

    post(client, gid, action="status", supplier="kjell", status="received")
    kjell = overview(client)["groups"][1]
    assert kjell["status"] == "received" and kjell["receivedDate"]

    post(client, gid, action="order_no", supplier="kjell", order_no="K-9")
    assert overview(client)["groups"][1]["order_no"] == "K-9"

    post(client, gid, action="undo", supplier="")
    assert overview(client)["groups"][2]["status"] == "none"


def test_bom_changed_after_ordering(client):
    gid = setup(client)
    post(client, gid, action="ordered", supplier="kjell")
    rows = [r if r[0] != "Ledning" else ("Ledning", "Kjell", "L1", 3, 0, 10.0) for r in ROWS]
    rows.append(("Loddetinn", "Kjell", "T1", 1, 0, 50.0))
    write_rows(config.BOM_DIR / "Test – BOM.xlsx", rows)
    kjell = overview(client)["groups"][1]
    assert [(t["description"], t["missing"]) for t in kjell["extra"]] == [("Ledning", 10), ("Loddetinn", 10)]


def test_invalid_actions(client):
    gid = setup(client)
    assert post(client, gid, action="noe", supplier="elfa").status_code == 400
    assert post(client, gid, action="status", supplier="elfa", status="received").status_code == 400  # not ordered
    assert post(client, gid, action="extra", supplier="elfa").status_code == 400
    assert post(client, gid, action="ordered", supplier="finnes ikke").status_code == 400
    assert post(client, gid, action="ordered", supplier="elfa", date="i går").status_code == 400
    post(client, gid, action="ordered", supplier="elfa")
    assert post(client, gid, action="status", supplier="elfa", status="kansellert").status_code == 400


def test_list_for_one_supplier(client):
    gid = setup(client)
    r = client.post(f"{G}/{gid}/order-list", json={"supplier": "kjell"})
    assert r.status_code == 200
    assert r.get_json()["lines"] == 1
    assert (config.LIST_DIR / r.get_json()["file"]).exists()
    assert "Kjell" in r.get_json()["file"]
    assert client.post(f"{G}/{gid}/order-list").get_json()["lines"] == 3  # the whole BOM


def test_deleting_removes_the_orders(client):
    import app as arshjul
    gid = setup(client)
    post(client, gid, action="ordered", supplier="elfa")
    client.delete(f"{G}/{gid}")
    assert arshjul.store.all_orders() == {}


def test_no_order_without_bom(client):
    client.post(G, json={"name": "Uten BOM", "school_year": 2026})
    assert overview(client) is None
