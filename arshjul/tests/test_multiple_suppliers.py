"""The same part number at two suppliers (RS and Farnell) must be two different lines everywhere."""
import bom
import config
from helpers import make_bom, write_rows

G = "/api/runs"
ROWS = [("rs test", "RS-Online", "1234", 1, 0, 10.0),
         ("farnell test", "Farnell", "1234", 1, 0, 5.0)]


def rows(*lines):
    return [{"description": b, "supplier": l, "part_number": v, "perSet": p, "in_stock": stk, "price": pr, "comment": ""}
            for b, l, v, p, stk, pr in lines]


def test_key_has_supplier():
    a, b = rows(*ROWS)
    assert (bom.row_key(a), bom.row_key(b)) == ("rs-online|1234", "farnell|1234")
    assert bom.row_key({"description": "Skrue", "supplier": "", "part_number": ""}) == "|skrue"


def test_diff_doesnt_mix_up_suppliers():
    old_count = rows(*ROWS)
    assert bom.diff(old_count, rows(*ROWS)) == []
    new = rows(ROWS[0], ("farnell test", "Farnell", "1234", 1, 0, 6.0))
    assert [(e["description"], e["field"], e["from"], e["to"]) for e in bom.diff(old_count, new)] == [("farnell test", "pris", 5.0, 6.0)]


def test_diff_with_identical_lines():
    """The same supplier and part number twice: compared in order, not mixed up."""
    to = rows(("A", "Elfa", "1", 1, 0, 1.0), ("A igjen", "Elfa", "1", 2, 0, 1.0))
    assert bom.diff(to, rows(*[(r["description"], "Elfa", "1", r["perSet"], 0, 1.0) for r in to])) == []
    assert len(bom.diff(to, to[:1])) == 1  # the second one was removed


def test_ok_seen_works(client):
    """The engineer's test: add two lines with the same part number, save, press «OK, sett»."""
    g = client.post(G, json={"name": "Multi leverandør - test", "school_year": 2026, "start": 44, "students": 10}).get_json()
    file = make_bom("Multi – BOM.xlsx", rows=[("Første", "Elfa", "E1", 1, 0, 1.0)]).name
    client.patch(f"{G}/{g['id']}", json={"bom": file})
    client.get("/api/state")  # "last seen" is saved
    write_rows(config.BOM_DIR / file, [("Første", "Elfa", "E1", 1, 0, 1.0), *ROWS])
    changes = lambda: client.get("/api/state").get_json()["projects"][0]["bomStatus"]["changes"]
    assert sorted((e["type"], e["description"]) for e in changes()) == [("new", "farnell test"), ("new", "rs test")]
    assert client.post(f"{G}/{g['id']}/seen").status_code == 200
    assert changes() == []


def test_count_and_receipt_keep_lines_apart(client):
    g = client.post(G, json={"name": "Multi", "school_year": 2026, "start": 44, "students": 10}).get_json()
    file = make_bom("Multi – BOM.xlsx", rows=ROWS).name
    client.patch(f"{G}/{g['id']}", json={"bom": file})
    stock = lambda: {r["supplier"]: r["in_stock"] for r in bom.read_bom(config.BOM_DIR / file)["rows"]}

    # order and receive only at RS
    client.post(f"{G}/{g['id']}/order", json={"action": "ordered", "supplier": "rs-online"})
    client.post(f"{G}/{g['id']}/order", json={"action": "receipt", "supplier": "rs-online"})
    assert stock() == {"RS-Online": 10, "Farnell": 0}

    # counting writes each line separately
    client.post(f"{G}/{g['id']}/count", json={"lines": [{"key": "rs-online|1234", "counted": 9},
                                                             {"key": "farnell|1234", "counted": 7}]})
    r = client.post(f"{G}/{g['id']}/count/write").get_json()
    assert sorted(r["written"]) == ["farnell|1234", "rs-online|1234"] and r["skipped"] == []
    assert stock() == {"RS-Online": 9, "Farnell": 7}
