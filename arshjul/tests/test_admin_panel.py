"""The admin panel (/admin): sees everything, fixes everything, cleans up and takes backups."""
import pytest

import app as arshjul
import auth
import bom
import config
from conftest import as_user
from helpers import make_bom

PASSWORD = "et-langt-passord"
G = "/api/runs"


def new_client():
    arshjul.app.config["TESTING"] = True
    return arshjul.app.test_client()


def user(name, role="engineer"):
    return arshjul.store.new_user(name, name.title(), role, auth.hash_password(PASSWORD))


@pytest.fixture
def bjorns(client):
    """A project with a BOM in Bjørn's course – not the admin's."""
    bjorn = user("bjorn")
    e = arshjul.store.new_course({"code": "E1", "engineer_id": bjorn})["id"]
    b = as_user(new_client(), bjorn)
    g = b.post(G, json={"name": "Bjørns", "school_year": 2030, "start": 44, "students": 10, "course": e}).get_json()
    file = make_bom(rows=[("Arduino", "Elfa", "A1", 1, 4, 250.0)]).name
    b.patch(f"{G}/{g['id']}", json={"bom": file})
    return {"bjorn": b, "g": g, "file": file, "course": e}


def stock(file):
    return bom.read_bom(config.BOM_DIR / file)["rows"][0]["in_stock"]


# ---------- tilgang ----------

@pytest.mark.parametrize("role", ["engineer", "lecturer"])
def test_admin_only(client, role):
    c = as_user(new_client(), user("vanlig", role))
    assert c.get("/admin").status_code == 403
    for method, file_path in (("get", "/api/admin/data"), ("get", "/api/admin/log"), ("post", "/api/admin/acknowledge-all"),
                        ("post", "/api/admin/backup"), ("delete", "/api/admin/school-years/2030"),
                        ("patch", "/api/admin/runs/1")):
        assert getattr(c, method)(file_path, json={}).status_code == 403, file_path


def test_main_page_links_to_the_panel(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/admin"' in html and "adminToggle" not in html
    assert 'href="/admin"' not in as_user(new_client(), user("vanlig")).get("/").get_data(as_text=True)
    assert "Administrator" in client.get("/admin").get_data(as_text=True)


# ---------- overview and data ----------

def test_overview_finds_what_needs_cleaning(client, bjorns):
    client.post(G, json={"name": "Uten emne", "school_year": 2030})
    arshjul.store.new_course({"code": "TOMT"})  # without an engineer and a lecturer
    user("aldri")
    (config.BOM_DIR / bjorns["file"]).unlink()
    d = client.get("/api/admin/data").get_json()
    o = d["overview"]
    assert [r["name"] for r in o["withoutCourse"]] == ["Uten emne"]
    assert [r["name"] for r in o["bomMissing"]] == ["Bjørns"]
    assert [e["code"] for e in o["courseWithoutEngineer"]] == ["TOMT"]
    assert {u["username"] for u in o["neverLoggedIn"]} >= {"aldri", "bjorn"}
    assert o["perSchoolYear"] == {"2030": 2} or o["perSchoolYear"] == {2030: 2}
    import db
    assert d["system"]["dbVersion"] == len(db.MIGRATIONS)


def test_sees_orders_and_counts_everywhere(client, bjorns):
    b, gid = bjorns["bjorn"], bjorns["g"]["id"]
    b.post(f"{G}/{gid}/order", json={"action": "ordered", "supplier": "elfa", "order_no": "PO-7"})
    b.post(f"{G}/{gid}/count", json={"lines": [{"key": "elfa|a1", "counted": 9}]})
    d = client.get("/api/admin/data").get_json()
    assert [(r["project"], r["supplier"], r["order_no"], r["by"]) for r in d["orders"]] == [("Bjørns", "Elfa", "PO-7", "Bjorn")]
    assert [(r["project"], r["inBom"]) for r in d["counts"]] == [("Bjørns", 0)]
    assert d["runs"][0]["orders"] == 1 and d["runs"][0]["count"] == "saved"


# ---------- rette prosjekter ----------

def test_fix_anything_and_it_is_logged(client, bjorns):
    gid = bjorns["g"]["id"]
    r = client.patch(f"/api/admin/runs/{gid}", json={"school_year": 2031, "lead": 6, "course": None, "name": "Nytt navn"})
    g = r.get_json()
    assert (g["school_year"], g["lead"], g["courseId"], g["name"]) == (2031, 6, None, "Nytt navn")
    log = client.get("/api/admin/log?role=admin").get_json()
    assert {(e["field"], e["from_value"], e["to_value"]) for e in log} >= {("skoleår", "2030", "2031"), ("leveringstid", "2", "6"),
                                                                ("emne", "E1", None)}
    assert all(e["user_name"] == "Admin Testesen" for e in log)
    assert client.patch(f"/api/admin/runs/{gid}", json={"school_year": 1900}).status_code == 400


def test_delete_shows_consequence_first(client, bjorns):
    b, gid = bjorns["bjorn"], bjorns["g"]["id"]
    b.post(f"{G}/{gid}/order", json={"action": "ordered", "supplier": "elfa"})
    k = client.get(f"/api/admin/runs/{gid}/consequence").get_json()
    assert (k["orders"], k["count"], k["lastOfProject"]) == (1, False, True)
    assert client.delete(f"/api/admin/runs/{gid}").status_code == 200
    assert client.get("/api/admin/data").get_json()["runs"] == []
    assert arshjul.store.all_orders() == {}


def test_restore_removed_project(client):
    ola = user("ola", "lecturer")
    e = arshjul.store.new_course({"code": "E2", "lecturer_id": ola})["id"]
    o = as_user(new_client(), ola)
    g = o.post(G, json={"name": "Olas", "school_year": 2030, "course": e}).get_json()
    o.delete(f"{G}/{g['id']}")
    assert [r["name"] for r in client.get("/api/admin/data").get_json()["overview"]["removed"]] == ["Olas"]
    assert client.post(f"/api/admin/runs/{g['id']}/restore").status_code == 200
    assert client.get("/api/admin/data").get_json()["overview"]["removed"] == []


# ---------- orders and counts ----------

def test_remove_wrongly_registered_order(client, bjorns):
    b, gid, file = bjorns["bjorn"], bjorns["g"]["id"], bjorns["file"]
    b.post(f"{G}/{gid}/order", json={"action": "ordered", "supplier": "elfa"})
    b.post(f"{G}/{gid}/order", json={"action": "receipt", "supplier": "elfa"})
    assert stock(file) == 10
    r = client.delete(f"/api/admin/runs/{gid}/order?supplier=elfa")
    assert r.status_code == 400 and "Angre mottaket" in r.get_json()["error"]  # put in stock: undo first
    r = client.post(f"/api/admin/runs/{gid}/undo-receipt?supplier=elfa").get_json()
    assert r["written"] == ["elfa|a1"] and stock(file) == 4
    assert client.delete(f"/api/admin/runs/{gid}/order?supplier=elfa").status_code == 200
    assert arshjul.store.all_orders() == {}
    assert client.delete(f"/api/admin/runs/{gid}/order?supplier=elfa").status_code == 404


def test_remove_and_undo_count(client, bjorns):
    b, gid, file = bjorns["bjorn"], bjorns["g"]["id"], bjorns["file"]
    b.post(f"{G}/{gid}/count", json={"lines": [{"key": "elfa|a1", "counted": 9}]})
    b.post(f"{G}/{gid}/count/write")
    assert stock(file) == 9
    r = client.delete(f"/api/admin/runs/{gid}/count")
    assert r.status_code == 400 and "Angre tellingen" in r.get_json()["error"]
    assert client.post(f"/api/admin/runs/{gid}/count/undo").status_code == 200
    assert stock(file) == 4
    assert client.delete(f"/api/admin/runs/{gid}/count").status_code == 200
    assert arshjul.store.counts() == {}


# ---------- log and cleanup ----------

def test_log_search(client, bjorns):
    client.patch(f"/api/admin/runs/{bjorns['g']['id']}", json={"students": 12})
    fields = {r["field"] for r in client.get("/api/admin/log?user=Admin").get_json()}
    assert {"studenter", "antall sett"} <= fields
    assert all(e["user_name"] == "Bjorn" for e in client.get("/api/admin/log?user=Bjorn").get_json())
    assert client.get("/api/admin/log?project=finnes-ikke").get_json() == []
    assert len(client.get("/api/admin/log?limit=1").get_json()) == 1


def test_acknowledge_all(client):
    ola = user("ola", "lecturer")
    e = arshjul.store.new_course({"code": "E2", "lecturer_id": ola})["id"]
    as_user(new_client(), ola).post(G, json={"name": "Olas", "school_year": 2030, "course": e})
    assert client.get("/api/admin/data").get_json()["overview"]["unacknowledged"] == 1
    assert client.post("/api/admin/acknowledge-all").get_json() == {"acknowledged": 1}
    assert client.get("/api/admin/data").get_json()["overview"]["unacknowledged"] == 0


def test_delete_an_old_school_year(client, bjorns):
    client.post(G, json={"name": "Annet år", "school_year": 2031})
    bjorns["bjorn"].post(f"{G}/{bjorns['g']['id']}/order", json={"action": "ordered", "supplier": "elfa"})
    k = client.get("/api/admin/school-years/2030/consequence").get_json()
    assert (k["name"], k["runs"], k["orders"]) == ("2030/31", 1, 1)
    assert client.delete("/api/admin/school-years/2030").get_json() == {"deleted": 1}
    assert [r["school_year"] for r in client.get("/api/admin/data").get_json()["runs"]] == [2031]
    assert stock(bjorns["file"]) == 4  # the BOM hasn't been touched
    assert client.get("/api/admin/log?role=admin").get_json()[0]["action"] == "school_year_deleted"


def test_delete_users(client):
    admin = arshjul.store.user(username="admin")["id"]
    test = user("test")
    assert client.delete(f"/api/admin/users/{test}").status_code == 200
    assert arshjul.store.user(id=test) is None
    used = user("used")
    arshjul.store.update_user(used, last_login="2026-10-01T10:00:00")
    assert "Deaktiver" in client.delete(f"/api/admin/users/{used}").get_json()["error"]
    on_course = user("onCourse")
    arshjul.store.new_course({"code": "E9", "engineer_id": on_course})
    assert client.delete(f"/api/admin/users/{on_course}").status_code == 400
    assert client.delete(f"/api/admin/users/{admin}").status_code == 400  # not yourself


# ---------- system and backups ----------

def test_backup(client, bjorns):
    r = client.post("/api/admin/backup").get_json()
    assert r["name"].startswith("arshjul ") and r["size"] > 0
    import sqlite3
    copy = sqlite3.connect(config.DATA_DIR / "Sikkerhetskopi-db" / r["name"])
    assert copy.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1  # a real copy
    copy.close()
    assert [k["name"] for k in client.get("/api/admin/data").get_json()["system"]["backups"]] == [r["name"]]
    download = client.get(f"/api/admin/backup/{r['name']}")
    assert download.status_code == 200 and download.data[:15] == b"SQLite format 3"
    download.close()
    assert client.get("/api/admin/backup/..%2Farshjul.db").status_code == 404
    assert client.get("/api/admin/backup/arshjul.db").status_code == 404


def test_system(client):
    s = client.get("/api/admin/data").get_json()["system"]
    assert s["bomReachable"] is True and s["mode"] == "lokal" and s["dataFolder"] == str(config.DATA_DIR)


# ---------- selve siden (admin.js) ----------

ADMIN_STUB = r"""
const __els = {};
function __el(id) {
  return __els[id] || (__els[id] = {
    id, innerHTML: "", textContent: "", hidden: false, value: "", dataset: {},
    setAttribute() {}, getAttribute() { return null; }, toggleAttribute() {}, addEventListener() {}, appendChild() {},
    querySelector() { return null; }, querySelectorAll() { return id === "faner" ? __faner : []; },
    contains() { return false; }, focus() {}, setSelectionRange() {}, set onchange(f) {}, set oninput(f) {}, set onsubmit(f) {},
  });
}
const __faner = ["oversikt", "prosjekter", "emner", "brukere", "registreringer", "logg", "opprydding", "system"].map((n) => ({
  getAttribute: () => "#" + n, toggleAttribute() {}, setAttribute() {}, innerHTML: "" }));
globalThis.location = { hash: "#oversikt", href: "" };
globalThis.document = { getElementById: __el, querySelector: () => ({ content: "t" }), querySelectorAll: () => [],
                        activeElement: null, addEventListener() {}, body: { dataset: { me: "1" } } };
globalThis.window = { addEventListener() {} };
globalThis.fetch = async (url) => ({ ok: true, status: 200, json: async () => (url.startsWith("/api/admin/log") ? LOGG : DATA) });
globalThis.__out = null;
"""


def run_admin(data, log):
    import json
    import time
    from test_page import MiniRacer
    src = (config.APP_DIR / "static" / "admin.js").read_text(encoding="utf-8").rstrip()
    assert src.endswith("})();")
    src = src[:-5] + "globalThis.__t = { draw, loadData };\n})();"
    ctx = MiniRacer()
    ctx.eval("const DATA = " + json.dumps(data) + "; const LOGG = " + json.dumps(log) + ";")
    ctx.eval(ADMIN_STUB)
    ctx.eval(src)
    ctx.eval(r"""
    (async () => {
      const out = {}, errors = [];
      await globalThis.__t.loadData();
      for (const f of ["overview", "projects", "courses", "users", "registrations", "log", "cleanup", "system"]) {
        location.hash = "#" + f;
        try {
          globalThis.__t.draw();
          for (let i = 0; i < 20; i++) await Promise.resolve();  // let the fetch (the log) finish
          out[f] = __el("content").innerHTML;
        }
        catch (e) { errors.push(f + ": " + e.stack); }
      }
      out.logTable = __el("logTable").innerHTML;
      globalThis.__out = JSON.stringify({ errors, out, status: __el("status").textContent });
    })().catch((e) => { globalThis.__out = JSON.stringify({ errors: ["uncaught: " + e.stack] }); });
    """)
    for _ in range(200):
        out = ctx.eval("globalThis.__out")
        if out:
            return json.loads(out)
        time.sleep(0.02)
    raise AssertionError("panelet ble ikke ferdig")


def test_panel_draws_every_tab(client, bjorns):
    b, gid = bjorns["bjorn"], bjorns["g"]["id"]
    b.post(f"{G}/{gid}/order", json={"action": "ordered", "supplier": "elfa", "order_no": "PO-1"})
    b.post(f"{G}/{gid}/order", json={"action": "receipt", "supplier": "elfa"})
    b.post(f"{G}/{gid}/count", json={"lines": [{"key": "elfa|a1", "counted": 9}]})
    client.post(G, json={"name": "Uten emne", "school_year": 2031})
    client.post("/api/admin/backup")
    b.post("/api/courses", json={"code": "FORSLAG"})  # a proposal from Bjørn, waiting for the admin
    res = run_admin(client.get("/api/admin/data").get_json(), client.get("/api/admin/log").get_json())
    assert res["errors"] == []
    u = res["out"]
    assert "Prosjekter uten emne: 1" in u["overview"] and "Uten emne" in u["overview"]
    assert "Bjørns" in u["projects"] and 'data-f="school_year"' in u["projects"] and "Slett …" in u["projects"]
    assert 'data-f="group_size"' in u["projects"] and "= 10 sett" in u["projects"]
    assert "E1" in u["courses"] and "Bjorn" in u["users"] and "(deg)" in u["users"]
    assert "Forslag som venter på godkjenning (1)" in u["courses"] and 'data-act="courseapprove"' in u["courses"]
    assert "Emner som venter på godkjenning: 1" in u["overview"] and "FORSLAG" in u["overview"]
    assert "Angre mottak" in u["registrations"] and "PO-1" in u["registrations"]  # put in stock: undone, not removed
    assert "Kvitter alle" in u["cleanup"] and "Slett et gammelt skoleår" in u["cleanup"]
    assert "Ta sikkerhetskopi nå" in u["system"] and "arshjul " in u["system"]
    assert "Bjorn" in res["out"]["logTable"], res["status"]
