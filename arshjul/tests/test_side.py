"""Kjører nettsidens JavaScript (static/arshjul.js) med ekte svar fra API-et.

Fanger feil som bare skjer i nettleseren, f.eks. at siden krasjer når den tegner et panel.
Bruker en enkel falsk DOM: vi sjekker at alt kan tegnes uten feil, ikke hvordan det ser ut.
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
import frister
from hjelp import lag_bom


def _mini_racer():
    """V8 (mini-racer) krasjer når den ligger under en sti med æøå – som «Innkjøp undervisning».
    Da kopieres pakken til en vanlig temp-mappe og importeres derfra."""
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

G = "/api/gjennomforinger"
# neste skoleår: prosjektene har ikke startet, så «må bestille mer» kan vises
SY = frister.skolear_for(dt.date.today()) + 1

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
  const tabeller = [];
  for (const ar of t.years) {
    t.goSY(ar);
    for (const p of t.projects) {
      t.openOrders.add(p.id); t.tellOpen.set(p.id, true);
      tryit(`panel ${p.skolear} ${p.navn}`, () => t.drawTable());
    }
    tabeller.push(__el("tbody").innerHTML);  // alle panelene i skoleåret er åpne nå
  }
  tryit("renderAll(true)", () => t.renderAll(true));
  globalThis.__out = JSON.stringify({ errors: log, tbody: tabeller.join("\n"), stats: __el("stats").innerHTML,
                                      notices: __el("notices").innerHTML, wheel: __el("wheel").innerHTML,
                                      history: __el("histTable").innerHTML });
})().catch((e) => { globalThis.__out = JSON.stringify({ errors: ["uncaught: " + e.stack] }); });
"""


def kjor_siden(state: dict, vis_alle: bool = False) -> dict:
    src = (Path(config.APP_DIR) / "static" / "arshjul.js").read_text(encoding="utf-8").rstrip()
    assert src.endswith("})();")
    src = src[:-5] + ("globalThis.__t = { renderAll, drawTable, openOrders, tellOpen, load, goSY,"
                      " get years() { return SYS.map((s) => s.ar); }, get projects() { return projects; } };\n})();")
    ctx = MiniRacer()
    ctx.eval("const STATE = " + json.dumps(state) + ";")
    ctx.eval(STUB)
    ctx.eval("globalThis.localStorage = { getItem: () => %s, setItem() {} };" % ('"1"' if vis_alle else "null"))
    ctx.eval(src)
    ctx.eval(TEST)
    for _ in range(100):
        out = ctx.eval("globalThis.__out")
        if out:
            return json.loads(out)
        time.sleep(0.02)
    raise AssertionError("siden ble ikke ferdig")


def test_siden_tegner_alle_tilstander(client):
    """Ett prosjekt i hver tilstand: uten BOM, ikke bestilt, delvis bestilt, må bestille mer,
    telt, telt og skrevet til BOM – i to skoleår."""
    rader = [("Arduino", "Elfa", "A1", 1, 4, 250.0), ("Ledning", "Kjell", "L1", 2, 0, 10.0)]

    def prosjekt(navn, **kw):
        g = client.post(G, json={"navn": navn, "skolear": SY, "start": 44, "sett": 10, **kw}).get_json()
        fil = lag_bom(f"{navn} – BOM.xlsx", rader=rader).name
        client.patch(f"{G}/{g['id']}", json={"bom": fil})
        return g["id"]

    client.post(G, json={"navn": "Uten BOM", "skolear": SY, "start": 40})
    prosjekt("Ikke bestilt")
    delvis = prosjekt("Delvis")
    client.post(f"{G}/{delvis}/bestilling", json={"handling": "bestilt", "leverandor": "elfa", "ordrenr": "PO-1"})
    mer = prosjekt("Mer")
    client.post(f"{G}/{mer}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    client.patch(f"{G}/{mer}", json={"sett": 12})
    telt = prosjekt("Telt", start=2, slutt=2)
    client.post(f"{G}/{telt}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 8}, {"key": "kjell|l1", "type": "forbruk", "telt": 1}]})
    skrevet = prosjekt("Skrevet", start=3, slutt=3)
    client.post(f"{G}/{skrevet}/bestilling", json={"handling": "bestilt", "leverandor": "kjell"})
    client.post(f"{G}/{skrevet}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 10}]})
    assert client.post(f"{G}/{skrevet}/telling/skriv").status_code == 200
    rettet = prosjekt("Rettet", start=4, slutt=4)
    client.post(f"{G}/{rettet}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 10}, {"key": "kjell|l1", "telt": 2}]})
    client.post(f"{G}/{rettet}/telling/skriv")
    client.post(f"{G}/{rettet}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 9}, {"key": "kjell|l1", "telt": 2}]})
    angret = prosjekt("Angret", start=5, slutt=5)
    client.post(f"{G}/{angret}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 10}]})
    client.post(f"{G}/{angret}/telling/skriv")
    client.post(f"{G}/{angret}/telling/angre")
    excel = prosjekt("Excel", start=6, slutt=6)
    client.post(f"{G}/{excel}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 10}]})
    client.post(f"{G}/{excel}/telling/skriv")
    bom.write_stock(config.BOM_DIR / "Excel – BOM.xlsx", {"elfa|a1": 30}, config.BOM_DIR / "Sikkerhetskopi")
    mottatt = prosjekt("Mottatt")
    client.post(f"{G}/{mottatt}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    client.post(f"{G}/{mottatt}/bestilling", json={"handling": "mottak", "leverandor": "elfa"})
    client.post(f"{G}/{mottatt}/bestilling", json={"handling": "bestilt", "leverandor": "kjell"})
    client.post(f"{G}/{mottatt}/bestilling", json={"handling": "mottak", "leverandor": "kjell", "linjer": {"kjell|l1": 5}})
    client.post(f"/api/skolear/{SY}/kopier")

    state = client.get("/api/state").get_json()
    res = kjor_siden(state)
    assert res["errors"] == []
    assert "Telling etter prosjektet" in res["tbody"]
    assert "Registrer bestilt" in res["tbody"]
    assert "Skriv endringene til BOM-en" in res["tbody"] and "i BOM-en: 10" in res["tbody"]
    assert "Angre – sett «På lager» tilbake" in res["tbody"] and "satt tilbake" in res["tbody"]
    assert "endret i Excel: 30" in res["tbody"] and "Bruk tallene fra BOM-en i tellingen" in res["tbody"]
    assert "Alt er lagt på lager" in res["tbody"] and "Registrer mottak" in res["tbody"] and "Angre mottak" in res["tbody"]
    assert "Registrer tilleggsbestilling" in res["tbody"]  # «Mer»: sett økt etter bestilling
    assert "Historikk" not in res["history"]  # tabellen er tegnet (overskriften står i HTML-malen)
    assert "Sum" in res["history"]
