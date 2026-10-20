import datetime as dt

import app as arshjul
import bestilling
import config
import frister
from db import Db
from hjelp import lag_bom

G = "/api/gjennomforinger"
STENGT = [{"navn": "Juleferie", "fra": 52, "til": 1}, {"navn": "Fellesferie", "fra": 28, "til": 31}]


def run(pid, sy, start, **kw):
    return {"id": pid + str(sy), "prosjektId": pid, "navn": pid.title(), "skolear": sy, "start": start,
            "slutt": start, "lev": 2, "buf": 3, "type": "hack", **kw}


# ---------- varsel om neste skoleår ----------

def test_varsel_om_varen():
    rader = [run("kit", 2026, 34), run("insekt", 2026, 44)]
    # kit 2027/28: uke 34 → frist uke 25, 21. juni 2027
    assert frister.oppsett_varsel(rader, STENGT, dt.date(2027, 3, 15)) is None  # 14 uker igjen
    v = frister.oppsett_varsel(rader, STENGT, dt.date(2027, 4, 5))
    assert (v["skolear"], v["fra"], v["navn"], v["prosjekt"], v["mangler"]) == (2027, 2026, "2027/28", "Kit", 2)
    assert (v["frist"]["uke"], v["frist"]["ar"], v["uker"]) == (25, 2027, 11)


def test_ingen_varsel_naar_neste_aar_er_satt_opp():
    rader = [run("kit", 2026, 34), run("kit", 2027, 34)]
    assert frister.oppsett_varsel(rader, STENGT, dt.date(2027, 4, 5)) is None


def test_varsel_etter_at_fristen_er_passert():
    # august 2027: 2027/28 har startet uten å være satt opp
    v = frister.oppsett_varsel([run("kit", 2026, 34)], STENGT, dt.date(2027, 8, 9))
    assert v["skolear"] == 2027 and v["uker"] < 0


def test_varsel_for_ett_manglende_prosjekt():
    rader = [run("kit", 2026, 34), run("insekt", 2026, 44), run("insekt", 2027, 44)]
    v = frister.oppsett_varsel(rader, STENGT, dt.date(2027, 4, 5))
    assert (v["mangler"], v["prosjekt"]) == (1, "Kit")


# ---------- kopiering ----------

def test_kopier_til_neste_skolear():
    db = Db(config.DB_FILE)
    a = db.ny("kit", 2026, {"navn": "Kit", "start": 34, "slutt": 50, "studenter": 60})
    b = db.ny("insekt", 2026, {"navn": "Insekt", "start": 44, "slutt": 44, "studenter": 30})
    db.ny_bestilling(a["id"], "elfa", "Elfa", dato="2026-06-01", ordrenr="", sett=60, linjer=[])

    assert len(db.kopier(2026, [a["id"]])) == 1
    neste = [g for g in db.gjennomforinger() if g["skolear"] == 2027]
    assert [(g["prosjektId"], g["start"], g["slutt"], g["sett"]) for g in neste] == [("kit", 34, 50, 60)]
    assert list(db.bestillinger()) == [a["id"]]  # bestillinger følger ikke med

    assert len(db.kopier(2026)) == 1  # bare insekt gjenstår
    assert db.kopier(2026) == []
    assert b["id"] not in {g["id"] for g in db.gjennomforinger() if g["skolear"] == 2027}


def test_kopier_via_api(client):
    a = client.post(G, json={"navn": "Kit", "skolear": 2026, "start": 34, "slutt": 50, "studenter": 60}).get_json()
    client.post(G, json={"navn": "Insekt", "skolear": 2026, "start": 44})
    r = client.post("/api/skolear/2026/kopier", json={"ids": [a["id"]]})
    assert r.get_json()["navn"] == "2027/28" and len(r.get_json()["nye"]) == 1
    st = client.get("/api/state").get_json()
    kopi = [p for p in st["prosjekter"] if p["skolear"] == 2027]
    assert [(p["navn"], p["frister"]["start"]["ar"]) for p in kopi] == [("Kit", 2027)]
    assert client.post("/api/skolear/2026/kopier", json={"ids": "alle"}).status_code == 400


# ---------- historikk ----------

def test_kostnad_fra_frosne_kopier():
    assert bestilling.kostnad({}) is None
    lagret = {"elfa": {"linjer": [{"antall": 6, "pris": 250.0}, {"antall": 10, "pris": 0.5}]},
              "kjell": {"linjer": [{"antall": 20, "pris": 10.0}]}}
    assert bestilling.kostnad(lagret) == 1705.0


def test_bestilt_kr_i_state(client):
    g = client.post(G, json={"navn": "Test", "skolear": 2026, "start": 44, "studenter": 10}).get_json()
    fil = lag_bom(rader=[("Arduino", "Elfa", "A1", 1, 4, 250.0), ("Ledning", "Kjell", "L1", 2, 0, 10.0)]).name
    client.patch(f"{G}/{g['id']}", json={"bom": fil})
    state = lambda: client.get("/api/state").get_json()["prosjekter"][0]
    assert state()["bestiltKr"] is None
    client.post(f"{G}/{g['id']}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    assert state()["bestiltKr"] == 1500.0
    # kostnaden står seg selv om BOM-en senere fjernes
    (config.BOM_DIR / fil).unlink()
    assert state()["bestiltKr"] == 1500.0


def test_kopier_med_endringer(client):
    a = client.post(G, json={"navn": "Kit", "skolear": 2026, "start": 34, "slutt": 50, "studenter": 60, "gruppe": 3}).get_json()
    b = client.post(G, json={"navn": "Lodding", "skolear": 2026, "start": 44, "studenter": 30}).get_json()
    r = client.post("/api/skolear/2026/kopier", json={"ids": [a["id"], b["id"]],
                                                       "endringer": {a["id"]: {"start": 35, "studenter": 72, "sett": 999}}})
    assert r.status_code == 200, r.get_json()
    neste = {g["navn"]: g for g in arshjul.store.gjennomforinger() if g["skolear"] == 2027}
    assert (neste["Kit"]["start"], neste["Kit"]["slutt"], neste["Kit"]["studenter"], neste["Kit"]["sett"]) == (35, 50, 72, 24)
    assert (neste["Lodding"]["start"], neste["Lodding"]["sett"]) == (44, 30)  # uten endringer: som i år
    assert client.post("/api/skolear/2026/kopier", json={"endringer": {a["id"]: "x"}}).status_code == 400
