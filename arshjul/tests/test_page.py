"""Runs the page's JavaScript (static/arshjul.js) with real responses from the API.

Catches errors that only happen in the browser, e.g. the page crashing when it draws a panel.
Uses a simple fake DOM: we check that everything can be drawn without errors, not how it looks.
"""
import datetime as dt
import importlib.metadata
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

import bom
import config
import deadlines
from helpers import make_bom


def _mini_racer():
    """V8 (mini-racer) crashes when it's under a path with æøå – like «Innkjøp undervisning».
    Then the package is copied to a plain temp folder and imported from there."""
    spec = importlib.util.find_spec("py_mini_racer")
    if spec is None:
        pytest.skip("mini-racer er ikke installert (pip install -r requirements-dev.txt)")
    pkg = Path(spec.origin).parent
    if not str(pkg).isascii():
        dst = Path(tempfile.gettempdir()) / f"arshjul-mini-racer-{importlib.metadata.version('mini-racer')}"
        if not str(dst).isascii():
            pytest.skip("finner ingen sti uten æøå til mini-racer")
        if not (dst / "py_mini_racer").exists():
            shutil.copytree(pkg, dst / "py_mini_racer")
        sys.path.insert(0, str(dst))
    from py_mini_racer import MiniRacer
    return MiniRacer


MiniRacer = _mini_racer()

G = "/api/runs"
# next school year: the projects haven't started, so "needs more" can be shown
SY = deadlines.school_year_for(dt.date.today()) + 1

STUB = r"""
const __els = {};
function __el(id) {
  return __els[id] || (__els[id] = {
    id, innerHTML: "", textContent: "", hidden: false, title: "", disabled: false, value: "",
    setAttribute() {}, addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    contains() { return false; }, scrollIntoView() {}, focus() {}, reset() {},
  });
}
globalThis.document = { getElementById: __el, querySelector: () => null, querySelectorAll: () => [],
                        activeElement: null, addEventListener() {}, hidden: false, body: { dataset: {} } };
globalThis.window = { addEventListener() {} };
globalThis.console = { error: (e) => { globalThis.__consoleErrors = (globalThis.__consoleErrors || []).concat(String(e && e.stack || e)); },
                       log() {}, warn() {} };
globalThis.CSS = { escape: (s) => String(s) };
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => STATE });
globalThis.__out = null;
"""

TEST = r"""
(async () => {
  const log = [], t = globalThis.__t;
  const tryit = (name, fn) => { try { fn(); } catch (e) { log.push(name + ": " + e.stack); } };
  await t.load();
  if (__el("status").textContent) log.push("status: " + __el("status").textContent);
  (globalThis.__consoleErrors || []).forEach((e) => log.push("console.error: " + e));
  tryit("renderAll(false)", () => t.renderAll(false));
  const tables = [];
  for (const ar of t.years) {
    t.goSY(ar);
    for (const p of t.projects) {
      t.openOrders.add(p.id); t.countOpen.set(p.id, true); t.setsEditing.add(p.id);
      tryit(`panel ${p.school_year} ${p.name}`, () => t.drawTable());
    }
    tables.push(__el("tbody").innerHTML);  // every panel in the school year is open now
  }
  tryit("renderAll(true)", () => t.renderAll(true));
  tryit("openCopy", () => t.openCopy(true));  // the last school year → the year after
  tryit("visDatoer", () => { __el("f-start").value = "3"; __el("f-finish").value = "23"; t.showDates(); });
  globalThis.__out = JSON.stringify({ errors: log, tbody: tables.join("\n"), stats: __el("stats").innerHTML,
                                      notices: __el("notices").innerHTML, wheel: __el("wheel").innerHTML,
                                      history: __el("histTable").innerHTML, courses: __el("courseTable").innerHTML,
                                      copy: __el("copyList").innerHTML, dates: __el("f-dates").textContent });
})().catch((e) => { globalThis.__out = JSON.stringify({ errors: ["uncaught: " + e.stack] }); });
"""


def run_page(state: dict, show_all: bool = False) -> dict:
    src = (Path(config.APP_DIR) / "static" / "arshjul.js").read_text(encoding="utf-8").rstrip()
    assert src.endswith("})();")
    src = src[:-5] + ("globalThis.__t = { renderAll, drawTable, openOrders, countOpen, setsEditing, load, goSY, openCopy, showDates,"
                      " get years() { return SYS.map((s) => s.year); }, get projects() { return projects; } };\n})();")
    ctx = MiniRacer()
    ctx.eval("const STATE = " + json.dumps(state) + ";")
    ctx.eval(STUB)
    ctx.eval("globalThis.localStorage = { getItem: () => %s, setItem() {} };" % ('"1"' if show_all else "null"))
    ctx.eval(src)
    ctx.eval(TEST)
    for _ in range(100):
        out = ctx.eval("globalThis.__out")
        if out:
            return json.loads(out)
        time.sleep(0.02)
    raise AssertionError("siden ble ikke ferdig")


def test_page_draws_every_state(client):
    """One project in each state: without a BOM, not ordered, partly ordered, needs more,
    counted, counted and written to the BOM – in two school years."""
    rows = [("Arduino", "Elfa", "A1", 1, 4, 250.0), ("Ledning", "Kjell", "L1", 2, 0, 10.0)]

    def project(name, **kw):
        g = client.post(G, json={"name": name, "school_year": SY, "start": 44, "students": 10, **kw}).get_json()
        file = make_bom(f"{name} – BOM.xlsx", rows=rows).name
        client.patch(f"{G}/{g['id']}", json={"bom": file})
        return g["id"]

    client.post(G, json={"name": "Uten BOM", "school_year": SY, "start": 40})
    course = client.post("/api/courses", json={"code": "IELS1001", "students": 60}).get_json()["id"]
    client.post(G, json={"name": "Grupper", "school_year": SY, "start": 41, "students": 61, "group_size": 3, "spare": 1, "course": course})
    project("Ikke bestilt")
    partial = project("Delvis")
    client.post(f"{G}/{partial}/order", json={"action": "ordered", "supplier": "elfa", "order_no": "PO-1"})
    more = project("Mer")
    client.post(f"{G}/{more}/order", json={"action": "ordered", "supplier": "elfa"})
    client.patch(f"{G}/{more}", json={"students": 12})
    counted = project("Telt", start=2, finish=2)
    client.post(f"{G}/{counted}/count", json={"lines": [{"key": "elfa|a1", "counted": 8}, {"key": "kjell|l1", "type": "consumable", "counted": 1}]})
    written = project("Skrevet", start=3, finish=3)
    client.post(f"{G}/{written}/order", json={"action": "ordered", "supplier": "kjell"})
    client.post(f"{G}/{written}/count", json={"lines": [{"key": "elfa|a1", "counted": 10}]})
    assert client.post(f"{G}/{written}/count/write").status_code == 200
    corrected = project("Rettet", start=4, finish=4)
    client.post(f"{G}/{corrected}/count", json={"lines": [{"key": "elfa|a1", "counted": 10}, {"key": "kjell|l1", "counted": 2}]})
    client.post(f"{G}/{corrected}/count/write")
    client.post(f"{G}/{corrected}/count", json={"lines": [{"key": "elfa|a1", "counted": 9}, {"key": "kjell|l1", "counted": 2}]})
    undone = project("Angret", start=5, finish=5)
    client.post(f"{G}/{undone}/count", json={"lines": [{"key": "elfa|a1", "counted": 10}]})
    client.post(f"{G}/{undone}/count/write")
    client.post(f"{G}/{undone}/count/undo")
    excel = project("Excel", start=6, finish=6)
    client.post(f"{G}/{excel}/count", json={"lines": [{"key": "elfa|a1", "counted": 10}]})
    client.post(f"{G}/{excel}/count/write")
    bom.write_stock(config.BOM_DIR / "Excel – BOM.xlsx", {"elfa|a1": 30}, config.BOM_DIR / "Sikkerhetskopi")
    received = project("Mottatt")
    client.post(f"{G}/{received}/order", json={"action": "ordered", "supplier": "elfa"})
    client.post(f"{G}/{received}/order", json={"action": "receipt", "supplier": "elfa"})
    client.post(f"{G}/{received}/order", json={"action": "ordered", "supplier": "kjell"})
    client.post(f"{G}/{received}/order", json={"action": "receipt", "supplier": "kjell", "lines": {"kjell|l1": 5}})
    client.post(f"/api/school-years/{SY}/copy")

    state = client.get("/api/state").get_json()
    res = run_page(state)
    assert res["errors"] == []
    assert "Telling etter prosjektet" in res["tbody"]
    assert "Registrer bestilt" in res["tbody"]
    assert "Skriv endringene til BOM-en" in res["tbody"] and "i BOM-en: 10" in res["tbody"]
    assert "Angre – sett «På lager» tilbake" in res["tbody"] and "satt tilbake" in res["tbody"]
    assert "endret i Excel: 30" in res["tbody"] and "Bruk tallene fra BOM-en i tellingen" in res["tbody"]
    assert "Alt er lagt på lager" in res["tbody"] and "Registrer mottak" in res["tbody"] and "Angre mottak" in res["tbody"]
    assert "Registrer tilleggsbestilling" in res["tbody"]  # «Mer»: sets increased after ordering
    assert 'data-k="group_size"' in res["tbody"] and 'data-k="spare"' in res["tbody"]  # in the open sets row
    assert "<b>22</b> sett" in res["tbody"] and "61 ÷ 3 + 1" in res["tbody"]  # «Grupper»: 61 students in groups of 3 + 1 spare
    assert 'class="sets-sum" data-id=' in res["tbody"] and 'data-act="setsdone"' in res["tbody"]
    assert "Antall studenter?" in res["tbody"]  # «Uten BOM»: no students yet
    assert "Marker som bestilt" in res["tbody"] and 'data-act="nameedit"' in res["tbody"]  # «Uten BOM»
    assert 'data-act="type"' in res["tbody"] and ">Lab</option>" in res["tbody"]  # the type can be changed in the table
    d = res["dates"]  # weeks 3–23 are the following spring (January–June of the next calendar year), not January this year
    assert d.startswith("Vår ") and " jan" in d and " jun" in d and d.count(d[4:8]) == 3, d
    assert "Emnet: 60 studenter" in res["tbody"]  # «Grupper» differs from the course
    assert 'data-cf="students"' in res["courses"] and 'value="60"' in res["courses"]
    assert "Historikk" not in res["history"]  # the table has been drawn (the heading is in the HTML template)
    assert "Sum" in res["history"]


def test_wheel_shows_neighbouring_school_year_faintly(client):
    """Last year's project that ends in week 31 is counted in week 32 – it's shown faintly on the new wheel."""
    client.post(G, json={"name": "Sommerkurs", "school_year": SY, "start": 26, "finish": 31, "type": "project"})
    client.post(G, json={"name": "Høstprosjekt", "school_year": SY + 1, "start": 44})
    res = run_page(client.get("/api/state").get_json())  # the last wheel drawn: SY + 1
    assert res["errors"] == []
    assert 'class="proj neighbour"' in res["wheel"] and "Sommerkurs (" in res["wheel"] and 'data-ev="returns"' in res["wheel"]
    assert 'class="proj"' in res["wheel"]  # this year's own project, not faint


def test_copy_to_next_year_is_a_checklist(client):
    course = client.post("/api/courses", json={"code": "IELS1001", "students": 60}).get_json()["id"]
    g = client.post(G, json={"name": "Insektsløp", "school_year": SY, "start": 44, "students": 61, "group_size": 4, "course": course}).get_json()
    client.patch(f"{G}/{g['id']}", json={"bom": make_bom().name})
    client.post(G, json={"name": "Uten BOM", "school_year": SY, "start": 40})
    res = run_page(client.get("/api/state").get_json())
    assert res["errors"] == []
    k = res["copy"]
    assert 'data-c="start"' in k and 'data-c="students"' in k and "÷ 4 + 0" in k
    assert "Bruk emnets 60" in k  # the class in the course is different from last year
    assert "Ikke ferdig ennå" in k and "Ingen BOM koblet til" in k


def test_marked_ordered_without_bom(client):
    g = client.post(G, json={"name": "Lodding", "school_year": SY, "start": 44}).get_json()
    client.post(f"{G}/{g['id']}/ordered", json={"ordered": True})
    res = run_page(client.get("/api/state").get_json())
    assert res["errors"] == []
    assert '<span class="ostat ok">Bestilt ' in res["tbody"] and 'data-v="0"' in res["tbody"]
