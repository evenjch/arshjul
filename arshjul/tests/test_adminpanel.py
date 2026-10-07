"""Administratorpanelet (/admin): ser alt, retter alt, rydder og tar sikkerhetskopi."""
import pytest

import app as arshjul
import auth
import bom
import config
from conftest import som
from hjelp import lag_bom

PASSORD = "et-langt-passord"
G = "/api/gjennomforinger"


def ny_klient():
    arshjul.app.config["TESTING"] = True
    return arshjul.app.test_client()


def bruker(navn, rolle="ingenior"):
    return arshjul.store.ny_bruker(navn, navn.title(), rolle, auth.hash_passord(PASSORD))


@pytest.fixture
def bjorns(client):
    """Et prosjekt med BOM i Bjørns emne – ikke administratorens."""
    bjorn = bruker("bjorn")
    e = arshjul.store.nytt_emne({"kode": "E1", "ingenior_id": bjorn})["id"]
    b = som(ny_klient(), bjorn)
    g = b.post(G, json={"navn": "Bjørns", "skolear": 2030, "start": 44, "sett": 10, "emne": e}).get_json()
    fil = lag_bom(rader=[("Arduino", "Elfa", "A1", 1, 4, 250.0)]).name
    b.patch(f"{G}/{g['id']}", json={"bom": fil})
    return {"bjorn": b, "g": g, "fil": fil, "emne": e}


def lager(fil):
    return bom.read_bom(config.BOM_DIR / fil)["rader"][0]["lager"]


# ---------- tilgang ----------

@pytest.mark.parametrize("rolle", ["ingenior", "faglarer"])
def test_bare_administrator(client, rolle):
    c = som(ny_klient(), bruker("vanlig", rolle))
    assert c.get("/admin").status_code == 403
    for metode, sti in (("get", "/api/admin/data"), ("get", "/api/admin/logg"), ("post", "/api/admin/kvitter-alle"),
                        ("post", "/api/admin/sikkerhetskopi"), ("delete", "/api/admin/skolear/2030"),
                        ("patch", "/api/admin/gjennomforinger/1")):
        assert getattr(c, metode)(sti, json={}).status_code == 403, sti


def test_hovedsiden_lenker_til_panelet(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/admin"' in html and "adminToggle" not in html
    assert 'href="/admin"' not in som(ny_klient(), bruker("vanlig")).get("/").get_data(as_text=True)
    assert "Administrator" in client.get("/admin").get_data(as_text=True)


# ---------- oversikt og data ----------

def test_oversikt_finner_det_som_ma_ryddes(client, bjorns):
    client.post(G, json={"navn": "Uten emne", "skolear": 2030})
    arshjul.store.nytt_emne({"kode": "TOMT"})  # uten ingeniør og faglærer
    bruker("aldri")
    (config.BOM_DIR / bjorns["fil"]).unlink()
    d = client.get("/api/admin/data").get_json()
    o = d["oversikt"]
    assert [r["navn"] for r in o["utenEmne"]] == ["Uten emne"]
    assert [r["navn"] for r in o["bomMangler"]] == ["Bjørns"]
    assert [e["kode"] for e in o["emneUtenIngenior"]] == ["TOMT"]
    assert {u["brukernavn"] for u in o["aldriInne"]} >= {"aldri", "bjorn"}
    assert o["perSkolear"] == {"2030": 2} or o["perSkolear"] == {2030: 2}
    import db
    assert d["system"]["dbVersjon"] == len(db.MIGRERINGER)


def test_ser_bestillinger_og_tellinger_overalt(client, bjorns):
    b, gid = bjorns["bjorn"], bjorns["g"]["id"]
    b.post(f"{G}/{gid}/bestilling", json={"handling": "bestilt", "leverandor": "elfa", "ordrenr": "PO-7"})
    b.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 9}]})
    d = client.get("/api/admin/data").get_json()
    assert [(r["prosjekt"], r["leverandor"], r["ordrenr"], r["av"]) for r in d["bestillinger"]] == [("Bjørns", "Elfa", "PO-7", "Bjorn")]
    assert [(r["prosjekt"], r["iBom"]) for r in d["tellinger"]] == [("Bjørns", 0)]
    assert d["gjennomforinger"][0]["bestillinger"] == 1 and d["gjennomforinger"][0]["telling"] == "lagret"


# ---------- rette prosjekter ----------

def test_rette_alt_og_det_logges(client, bjorns):
    gid = bjorns["g"]["id"]
    r = client.patch(f"/api/admin/gjennomforinger/{gid}", json={"skolear": 2031, "lev": 6, "emne": None, "navn": "Nytt navn"})
    g = r.get_json()
    assert (g["skolear"], g["lev"], g["emneId"], g["navn"]) == (2031, 6, None, "Nytt navn")
    logg = client.get("/api/admin/logg?rolle=admin").get_json()
    assert {(e["felt"], e["fra"], e["til"]) for e in logg} >= {("skolear", "2030", "2031"), ("leveringstid", "2", "6"),
                                                                ("emne", "E1", None)}
    assert all(e["bruker_navn"] == "Admin Testesen" for e in logg)
    assert client.patch(f"/api/admin/gjennomforinger/{gid}", json={"skolear": 1900}).status_code == 400


def test_slette_viser_konsekvens_forst(client, bjorns):
    b, gid = bjorns["bjorn"], bjorns["g"]["id"]
    b.post(f"{G}/{gid}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    k = client.get(f"/api/admin/gjennomforinger/{gid}/konsekvens").get_json()
    assert (k["bestillinger"], k["telling"], k["sisteAvProsjektet"]) == (1, False, True)
    assert client.delete(f"/api/admin/gjennomforinger/{gid}").status_code == 200
    assert client.get("/api/admin/data").get_json()["gjennomforinger"] == []
    assert arshjul.store.bestillinger() == {}


def test_gjenopprette_fjernet_prosjekt(client):
    ola = bruker("ola", "faglarer")
    e = arshjul.store.nytt_emne({"kode": "E2", "faglarer_id": ola})["id"]
    o = som(ny_klient(), ola)
    g = o.post(G, json={"navn": "Olas", "skolear": 2030, "emne": e}).get_json()
    o.delete(f"{G}/{g['id']}")
    assert [r["navn"] for r in client.get("/api/admin/data").get_json()["oversikt"]["fjernet"]] == ["Olas"]
    assert client.post(f"/api/admin/gjennomforinger/{g['id']}/gjenopprett").status_code == 200
    assert client.get("/api/admin/data").get_json()["oversikt"]["fjernet"] == []


# ---------- bestillinger og tellinger ----------

def test_fjerne_feilregistrert_bestilling(client, bjorns):
    b, gid, fil = bjorns["bjorn"], bjorns["g"]["id"], bjorns["fil"]
    b.post(f"{G}/{gid}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    b.post(f"{G}/{gid}/bestilling", json={"handling": "mottak", "leverandor": "elfa"})
    assert lager(fil) == 10
    r = client.delete(f"/api/admin/gjennomforinger/{gid}/bestilling?leverandor=elfa")
    assert r.status_code == 400 and "Angre mottaket" in r.get_json()["feil"]  # lagt på lager: angre først
    r = client.post(f"/api/admin/gjennomforinger/{gid}/angre-mottak?leverandor=elfa").get_json()
    assert r["skrevet"] == ["elfa|a1"] and lager(fil) == 4
    assert client.delete(f"/api/admin/gjennomforinger/{gid}/bestilling?leverandor=elfa").status_code == 200
    assert arshjul.store.bestillinger() == {}
    assert client.delete(f"/api/admin/gjennomforinger/{gid}/bestilling?leverandor=elfa").status_code == 404


def test_fjerne_og_angre_telling(client, bjorns):
    b, gid, fil = bjorns["bjorn"], bjorns["g"]["id"], bjorns["fil"]
    b.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 9}]})
    b.post(f"{G}/{gid}/telling/skriv")
    assert lager(fil) == 9
    r = client.delete(f"/api/admin/gjennomforinger/{gid}/telling")
    assert r.status_code == 400 and "Angre tellingen" in r.get_json()["feil"]
    assert client.post(f"/api/admin/gjennomforinger/{gid}/telling/angre").status_code == 200
    assert lager(fil) == 4
    assert client.delete(f"/api/admin/gjennomforinger/{gid}/telling").status_code == 200
    assert arshjul.store.tellinger() == {}


# ---------- logg og opprydding ----------

def test_loggsok(client, bjorns):
    client.patch(f"/api/admin/gjennomforinger/{bjorns['g']['id']}", json={"sett": 12})
    assert client.get("/api/admin/logg?bruker=Admin").get_json()[0]["felt"] == "antall sett"
    assert all(e["bruker_navn"] == "Bjorn" for e in client.get("/api/admin/logg?bruker=Bjorn").get_json())
    assert client.get("/api/admin/logg?prosjekt=finnes-ikke").get_json() == []
    assert len(client.get("/api/admin/logg?grense=1").get_json()) == 1


def test_kvitter_alle(client):
    ola = bruker("ola", "faglarer")
    e = arshjul.store.nytt_emne({"kode": "E2", "faglarer_id": ola})["id"]
    som(ny_klient(), ola).post(G, json={"navn": "Olas", "skolear": 2030, "emne": e})
    assert client.get("/api/admin/data").get_json()["oversikt"]["ukvitterte"] == 1
    assert client.post("/api/admin/kvitter-alle").get_json() == {"kvittert": 1}
    assert client.get("/api/admin/data").get_json()["oversikt"]["ukvitterte"] == 0


def test_slette_et_gammelt_skolear(client, bjorns):
    client.post(G, json={"navn": "Annet år", "skolear": 2031})
    bjorns["bjorn"].post(f"{G}/{bjorns['g']['id']}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    k = client.get("/api/admin/skolear/2030/konsekvens").get_json()
    assert (k["navn"], k["gjennomforinger"], k["bestillinger"]) == ("2030/31", 1, 1)
    assert client.delete("/api/admin/skolear/2030").get_json() == {"slettet": 1}
    assert [r["skolear"] for r in client.get("/api/admin/data").get_json()["gjennomforinger"]] == [2031]
    assert lager(bjorns["fil"]) == 4  # BOM-en er ikke rørt
    assert client.get("/api/admin/logg?rolle=admin").get_json()[0]["handling"] == "slettet skoleår"


def test_slette_brukere(client):
    admin = arshjul.store.bruker(brukernavn="admin")["id"]
    test = bruker("test")
    assert client.delete(f"/api/admin/brukere/{test}").status_code == 200
    assert arshjul.store.bruker(id=test) is None
    brukt = bruker("brukt")
    arshjul.store.endre_bruker(brukt, sist_inne="2026-10-01T10:00:00")
    assert "Deaktiver" in client.delete(f"/api/admin/brukere/{brukt}").get_json()["feil"]
    paa_emne = bruker("paaemne")
    arshjul.store.nytt_emne({"kode": "E9", "ingenior_id": paa_emne})
    assert client.delete(f"/api/admin/brukere/{paa_emne}").status_code == 400
    assert client.delete(f"/api/admin/brukere/{admin}").status_code == 400  # ikke deg selv


# ---------- system og sikkerhetskopi ----------

def test_sikkerhetskopi(client, bjorns):
    r = client.post("/api/admin/sikkerhetskopi").get_json()
    assert r["navn"].startswith("arshjul ") and r["storrelse"] > 0
    import sqlite3
    kopi = sqlite3.connect(config.DATA_DIR / "Sikkerhetskopi-db" / r["navn"])
    assert kopi.execute("SELECT COUNT(*) FROM gjennomforing").fetchone()[0] == 1  # en ekte kopi
    kopi.close()
    assert [k["navn"] for k in client.get("/api/admin/data").get_json()["system"]["kopier"]] == [r["navn"]]
    nedlasting = client.get(f"/api/admin/sikkerhetskopi/{r['navn']}")
    assert nedlasting.status_code == 200 and nedlasting.data[:15] == b"SQLite format 3"
    nedlasting.close()
    assert client.get("/api/admin/sikkerhetskopi/..%2Farshjul.db").status_code == 404
    assert client.get("/api/admin/sikkerhetskopi/arshjul.db").status_code == 404


def test_system(client):
    s = client.get("/api/admin/data").get_json()["system"]
    assert s["bomNaabar"] is True and s["modus"] == "lokal" and s["datamappe"] == str(config.DATA_DIR)


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
                        activeElement: null, addEventListener() {}, body: { dataset: { meg: "1" } } };
globalThis.window = { addEventListener() {} };
globalThis.fetch = async (url) => ({ ok: true, status: 200, json: async () => (url.startsWith("/api/admin/logg") ? LOGG : DATA) });
globalThis.__out = null;
"""


def kjor_admin(data, logg):
    import json
    import time
    from test_side import MiniRacer
    src = (config.APP_DIR / "static" / "admin.js").read_text(encoding="utf-8").rstrip()
    assert src.endswith("})();")
    src = src[:-5] + "globalThis.__t = { tegn, last };\n})();"
    ctx = MiniRacer()
    ctx.eval("const DATA = " + json.dumps(data) + "; const LOGG = " + json.dumps(logg) + ";")
    ctx.eval(ADMIN_STUB)
    ctx.eval(src)
    ctx.eval(r"""
    (async () => {
      const ut = {}, feil = [];
      await globalThis.__t.last();
      for (const f of ["oversikt", "prosjekter", "emner", "brukere", "registreringer", "logg", "opprydding", "system"]) {
        location.hash = "#" + f;
        try {
          globalThis.__t.tegn();
          for (let i = 0; i < 20; i++) await Promise.resolve();  // la henting (loggen) bli ferdig
          ut[f] = __el("innhold").innerHTML;
        }
        catch (e) { feil.push(f + ": " + e.stack); }
      }
      ut.loggTabell = __el("loggTabell").innerHTML;
      globalThis.__out = JSON.stringify({ feil, ut, status: __el("status").textContent });
    })().catch((e) => { globalThis.__out = JSON.stringify({ feil: ["uncaught: " + e.stack] }); });
    """)
    for _ in range(200):
        out = ctx.eval("globalThis.__out")
        if out:
            return json.loads(out)
        time.sleep(0.02)
    raise AssertionError("panelet ble ikke ferdig")


def test_panelet_tegner_alle_faner(client, bjorns):
    b, gid = bjorns["bjorn"], bjorns["g"]["id"]
    b.post(f"{G}/{gid}/bestilling", json={"handling": "bestilt", "leverandor": "elfa", "ordrenr": "PO-1"})
    b.post(f"{G}/{gid}/bestilling", json={"handling": "mottak", "leverandor": "elfa"})
    b.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 9}]})
    client.post(G, json={"navn": "Uten emne", "skolear": 2031})
    client.post("/api/admin/sikkerhetskopi")
    res = kjor_admin(client.get("/api/admin/data").get_json(), client.get("/api/admin/logg").get_json())
    assert res["feil"] == []
    u = res["ut"]
    assert "Prosjekter uten emne: 1" in u["oversikt"] and "Uten emne" in u["oversikt"]
    assert "Bjørns" in u["prosjekter"] and 'data-f="skolear"' in u["prosjekter"] and "Slett …" in u["prosjekter"]
    assert "E1" in u["emner"] and "Bjorn" in u["brukere"] and "(deg)" in u["brukere"]
    assert "Angre mottak" in u["registreringer"] and "PO-1" in u["registreringer"]  # lagt på lager: angres, ikke fjernes
    assert "Kvitter alle" in u["opprydding"] and "Slett et gammelt skoleår" in u["opprydding"]
    assert "Ta sikkerhetskopi nå" in u["system"] and "arshjul " in u["system"]
    assert "Bjorn" in res["ut"]["loggTabell"], res["status"]
