import openpyxl
import pytest

import bom
import config
import telling
from hjelp import lag_bom

G = "/api/gjennomforinger"


def rad(besk, lev, pr_sett, lager, bestill, pris=10.0, varenr=""):
    return {"beskrivelse": besk, "leverandor": lev, "varenummer": varenr, "prSett": pr_sett, "lager": lager,
            "bestill": bestill, "pris": pris, "total": bestill * pris}


# ---------- logikken ----------

def test_grunnlag_bruker_registrert_bestilling_eller_antar_bom():
    rader = [rad("Arduino", "Elfa", 1, 4, 6, varenr="A1"), rad("Ledning", "Kjell", 2, 0, 20, varenr="L1")]
    # Elfa er registrert (og det ble bestilt 8), Kjell ikke
    g = telling.grunnlag(rader, ({"a1": 8.0}, {"elfa"}), {"l1": "forbruk"}, sett=10)
    arduino, ledning = g
    assert (arduino["bestilt"], arduino["antatt"], arduino["type"], arduino["utdelt"]) == (8.0, False, "utstyr", 10)
    assert (ledning["bestilt"], ledning["antatt"], ledning["type"]) == (20, True, "forbruk")


def test_forventet():
    l = {"lagerFor": 4, "bestilt": 6, "utdelt": 10}
    assert telling.forventet({**l, "type": "utstyr"}) == 10  # alt skal tilbake
    assert telling.forventet({**l, "type": "forbruk"}) == 0  # alt er brukt opp
    assert telling.forventet({"lagerFor": 50, "bestilt": 0, "utdelt": 20, "type": "forbruk"}) == 30


def test_lag_og_oppsummering():
    g = telling.grunnlag([rad("Arduino", "Elfa", 1, 4, 6, pris=250.0, varenr="A1"),
                          rad("Ledning", "Kjell", 2, 0, 20, varenr="L1"),
                          rad("Skrue", "", 1, 0, 10, varenr="S1")], ({}, set()), {}, sett=10)
    linjer = telling.lag(g, {"a1": {"type": "utstyr", "telt": 8}, "l1": {"type": "forbruk", "telt": 3}})
    assert [l["key"] for l in linjer] == ["a1", "l1"]  # skrue er ikke telt
    o = telling.oppsummering(linjer)
    # Arduino: forventet 10, telt 8 → svinn 2 × 250; ledning: forventet 0, telt 3 → avvik, ikke svinn
    assert o == {"linjer": 2, "avvik": 2, "svinn": 1, "svinnKr": 500.0}
    with pytest.raises(ValueError):
        telling.lag(g, {"a1": {"type": "noe", "telt": 1}})
    with pytest.raises(ValueError):
        telling.lag(g, {"a1": {"telt": -1}})


# ---------- skriving til BOM ----------

def test_write_stock_endrer_bare_lager():
    path = lag_bom(rader=[("Arduino", "Elfa", "A1", 1, 4, 250.0), ("Ledning", "Kjell", "L1", 2, 0, 10.0)])
    backup_dir = config.BOM_DIR / "Sikkerhetskopi"
    res = bom.write_stock(path, {"a1": 9, "l1": 2.5, "finnes-ikke": 1}, backup_dir)
    assert res["skrevet"] == ["a1", "l1"] and res["hoppet"] == ["finnes-ikke"]
    assert (backup_dir / res["backup"]).exists()

    b = bom.read_bom(path)
    assert [(r["beskrivelse"], r["lager"], r["prSett"], r["pris"]) for r in b["rader"]] == [
        ("Arduino", 9, 1, 250.0), ("Ledning", 2.5, 2, 10.0)]
    ws = openpyxl.load_workbook(path).worksheets[0]
    assert str(ws["I14"].value).startswith("=")  # formlene står
    assert str(ws["K14"].value).startswith("=")
    # sikkerhetskopien har de gamle tallene
    assert bom.read_bom(backup_dir / res["backup"])["rader"][0]["lager"] == 4


def test_write_stock_hopper_over_doble_linjer():
    path = lag_bom(rader=[("Arduino", "Elfa", "A1", 1, 4, 250.0), ("Arduino igjen", "Elfa", "A1", 1, 1, 250.0)])
    res = bom.write_stock(path, {"a1": 9}, config.BOM_DIR / "Sikkerhetskopi")
    assert res["hoppet"] == ["a1"]
    assert [r["lager"] for r in bom.read_bom(path)["rader"]] == [4, 1]


def test_write_stock_nekter_naar_filen_er_aapen_i_excel():
    path = lag_bom(rader=[("Arduino", "Elfa", "A1", 1, 4, 250.0)])
    (path.parent / f"~${path.name}").write_text("lås")
    with pytest.raises(bom.BomError, match="åpen i Excel"):
        bom.write_stock(path, {"a1": 9}, config.BOM_DIR / "Sikkerhetskopi")
    assert bom.read_bom(path)["rader"][0]["lager"] == 4


# ---------- gjennom API-et ----------

def oppsett(client):
    g = client.post(G, json={"navn": "Test", "skolear": 2026, "start": 44, "sett": 10}).get_json()
    fil = lag_bom(rader=[("Arduino", "Elfa", "A1", 1, 4, 250.0), ("Ledning", "Kjell", "L1", 2, 0, 10.0)]).name
    client.patch(f"{G}/{g['id']}", json={"bom": fil})
    return g["id"], fil


def tell(client):
    return client.get("/api/state").get_json()["prosjekter"][0]["telling"]


def test_tellingsflyt(client):
    gid, fil = oppsett(client)
    client.post(f"{G}/{gid}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    t = tell(client)
    assert t["lagret"] is None and t["antattBestilt"]  # Kjell er ikke registrert
    assert [(l["key"], l["lagerFor"], l["bestilt"], l["utdelt"]) for l in t["grunnlag"]] == [
        ("a1", 4, 6, 10), ("l1", 0, 20, 20)]

    r = client.post(f"{G}/{gid}/telling", json={"linjer": [
        {"key": "a1", "type": "utstyr", "telt": 9}, {"key": "l1", "type": "forbruk", "telt": 4}]})
    assert r.get_json() == {"linjer": 2, "avvik": 2, "svinn": 1, "svinnKr": 250.0}
    L = tell(client)["lagret"]
    assert (L["svinnKr"], L["skrevet"]) == (250.0, None)

    # typen huskes for prosjektet (neste år)
    import app as arshjul
    assert arshjul.store.linjetyper()[client.get("/api/state").get_json()["prosjekter"][0]["prosjektId"]] == {
        "a1": "utstyr", "l1": "forbruk"}

    # endre en lagret telling
    client.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "a1", "type": "utstyr", "telt": 10},
                                                         {"key": "l1", "type": "forbruk", "telt": 4}]})
    assert tell(client)["lagret"]["svinn"] == 0

    r = client.post(f"{G}/{gid}/telling/skriv")
    assert r.status_code == 200 and r.get_json()["skrevet"] == ["a1", "l1"]
    assert [x["lager"] for x in bom.read_bom(config.BOM_DIR / fil)["rader"]] == [10, 4]
    L = tell(client)["lagret"]
    assert L["skrevet"] and L["backup"]

    # vår egen skriving vises ikke som «BOM-en er endret»
    assert client.get("/api/state").get_json()["prosjekter"][0]["bomStatus"]["endringer"] == []

    # låst etter skriving
    assert client.post(f"{G}/{gid}/telling/skriv").status_code == 400
    assert client.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "a1", "telt": 1}]}).status_code == 400
    assert client.delete(f"{G}/{gid}/telling").status_code == 400


def test_neste_aar_bruker_ny_lagerbeholdning(client):
    gid, fil = oppsett(client)
    client.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "a1", "telt": 10}, {"key": "l1", "type": "forbruk", "telt": 4}]})
    client.post(f"{G}/{gid}/telling/skriv")
    client.post("/api/skolear/2026/kopier")
    neste = [p for p in client.get("/api/state").get_json()["prosjekter"] if p["skolear"] == 2027][0]
    # 10 sett: Arduino 10 − 10 på lager = 0; Ledning 20 − 4 = 16
    assert neste["bomStatus"]["bestill"] == 1
    g = {l["key"]: l for l in neste["telling"]["grunnlag"]}
    assert (g["a1"]["lagerFor"], g["l1"]["type"]) == (10, "forbruk")  # typen er husket


def test_slett_og_ugyldig_telling(client):
    gid, _ = oppsett(client)
    assert client.post(f"{G}/{gid}/telling", json={"linjer": []}).status_code == 400
    assert client.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "a1", "telt": -2}]}).status_code == 400
    assert client.post(f"{G}/{gid}/telling/skriv").status_code == 400  # ikke lagret
    client.post(f"{G}/{gid}/telling", json={"linjer": [{"key": "a1", "telt": 3}]})
    assert client.delete(f"{G}/{gid}/telling").status_code == 200
    assert tell(client)["lagret"] is None
