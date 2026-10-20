"""Emner: hvert prosjekt hører til et emne med én faglærer og én ansvarlig ingeniør.

Scenarioet fra instituttet:
    Emne A – faglærer A (Ola),  ingeniør A (Anne)
    Emne B – faglærer A (Ola),  ingeniør B (Bjørn)
    Emne C – faglærer B (Kari), ingeniør A (Anne)
    Emne D – faglærer B (Kari), ingeniør C (Cecilie)
"""
import datetime as dt

import pytest

import app as arshjul
import auth
import config
import frister
from test_tilgang import PASSORD, logg_inn, state

G = "/api/gjennomforinger"
SY = frister.skolear_for(dt.date.today()) + 1


@pytest.fixture
def inst(monkeypatch):
    s = arshjul.store
    u = {k: s.ny_bruker(k, navn, rolle, auth.hash_passord(PASSORD)) for k, navn, rolle in (
        ("anne", "Anne", "ingenior"), ("bjorn", "Bjørn", "ingenior"), ("cecilie", "Cecilie", "ingenior"),
        ("ola", "Ola", "faglarer"), ("kari", "Kari", "faglarer"))}
    e = {k: s.nytt_emne({"kode": k, "navn": f"Emne {k[-1]}", "faglarer_id": u[f], "ingenior_id": u[i]})["id"]
         for k, f, i in (("EMNE-A", "ola", "anne"), ("EMNE-B", "ola", "bjorn"),
                         ("EMNE-C", "kari", "anne"), ("EMNE-D", "kari", "cecilie"))}
    auth._ip_feil.clear()
    anne = logg_inn("anne")
    p = {k: anne.post(G, json={"navn": f"Prosjekt {k[-1]}", "skolear": SY, "start": 44, "emne": e[k]}).get_json()["id"]
         for k in e}
    p["uten"] = anne.post(G, json={"navn": "Prosjekt uten emne", "skolear": SY, "start": 44}).get_json()["id"]
    return {"u": u, "e": e, "p": p}


def egne(c):
    return sorted(p["navn"] for p in state(c)["prosjekter"] if p["egen"])


def test_hver_ingenior_har_sine_emner(inst):
    assert egne(logg_inn("anne")) == ["Prosjekt A", "Prosjekt C", "Prosjekt uten emne"]
    assert egne(logg_inn("bjorn")) == ["Prosjekt B", "Prosjekt uten emne"]
    assert egne(logg_inn("cecilie")) == ["Prosjekt D", "Prosjekt uten emne"]
    # andres emner sendes ikke engang til nettleseren
    assert len(state(logg_inn("bjorn"))["prosjekter"]) == 2


def test_faglarer_eier_prosjektene_i_sine_emner(inst):
    assert egne(logg_inn("ola")) == ["Prosjekt A", "Prosjekt B"]
    assert egne(logg_inn("kari")) == ["Prosjekt C", "Prosjekt D"]


def test_ingenior_kan_ikke_handle_i_andres_emner(inst):
    anne, pb = logg_inn("anne"), inst["p"]["EMNE-B"]
    r = anne.patch(f"{G}/{pb}", json={"start": 40})
    assert r.status_code == 403 and "Bjørn" in r.get_json()["feil"] and "EMNE-B" in r.get_json()["feil"]
    assert anne.delete(f"{G}/{pb}").status_code == 403
    assert anne.post(f"{G}/{pb}/bestilling", json={"handling": "bestilt", "leverandor": "x"}).status_code == 403
    assert anne.post(f"{G}/{pb}/telling", json={"linjer": [{"key": "x|1", "telt": 1}]}).status_code == 403
    assert anne.post(f"{G}/{pb}/mal").status_code == 403
    # Bjørn kan, og alle kan handle på prosjekter uten emne
    assert logg_inn("bjorn").patch(f"{G}/{pb}", json={"start": 40}).status_code == 200
    assert logg_inn("cecilie").patch(f"{G}/{inst['p']['uten']}", json={"start": 40}).status_code == 200


def test_varsel_gar_til_emnets_ingenior(inst):
    ola = logg_inn("ola")
    ola.patch(f"{G}/{inst['p']['EMNE-B']}", json={"notat": "endret 25"})  # Ola endrer i emne B
    assert [e["prosjekt_navn"] for e in state(logg_inn("bjorn"))["endringer"]] == ["Prosjekt B"]
    assert state(logg_inn("anne"))["endringer"] == []  # Anne har emne A og C, ikke B

    ola.patch(f"{G}/{inst['p']['EMNE-A']}", json={"notat": "endret 12"})
    assert [e["prosjekt_navn"] for e in state(logg_inn("anne"))["endringer"]] == ["Prosjekt A"]

    # Bjørn kan ikke kvittere for Annes varsler
    assert logg_inn("bjorn").post(f"/api/endringer/{inst['p']['EMNE-A']}/kvitter").status_code == 403


def test_ny_ingenior_overtar_varslene(inst):
    logg_inn("ola").patch(f"{G}/{inst['p']['EMNE-B']}", json={"notat": "endret 25"})
    anne = logg_inn("anne")
    r = anne.patch(f"/api/emner/{inst['e']['EMNE-B']}", json={"ingenior": inst["u"]["anne"]})
    assert r.get_json()["ingeniorNavn"] == "Anne"
    assert [e["prosjekt_navn"] for e in state(anne)["endringer"]] == ["Prosjekt B"]
    # Bjørn ser ikke lenger Olas endring – bare beskjed om at han er tatt av emnet
    assert [(e["handling"], e["prosjekt_navn"], e["til"]) for e in state(logg_inn("bjorn"))["endringer"]] == [
        ("fjernet_ingenior", "EMNE-B", "Anne")]


def test_fjernet_prosjekt_vises_for_emnets_ingenior(inst):
    logg_inn("kari").delete(f"{G}/{inst['p']['EMNE-D']}")
    assert [s["navn"] for s in state(logg_inn("cecilie"))["slettede"]] == ["Prosjekt D"]
    assert state(logg_inn("anne"))["slettede"] == []


def test_kopier_bare_egne_emner(inst):
    r = logg_inn("bjorn").post(f"/api/skolear/{SY}/kopier").get_json()
    neste = {g["navn"] for g in arshjul.store.gjennomforinger() if g["skolear"] == SY + 1}
    assert len(r["nye"]) == 2 and neste == {"Prosjekt B", "Prosjekt uten emne"}
    # ber om andres prosjekter: hoppes over
    r = logg_inn("bjorn").post(f"/api/skolear/{SY}/kopier", json={"ids": [inst["p"]["EMNE-A"]]}).get_json()
    assert r["nye"] == []


def test_emner_legges_inn_og_endres(inst):
    anne = logg_inn("anne")
    r = anne.post("/api/emner", json={"kode": " iels2001 ", "navn": "Elektronikk 2", "program": "master",
                                       "faglarer": inst["u"]["kari"], "ingenior": inst["u"]["bjorn"]})
    e = r.get_json()
    assert (e["kode"], e["program"], e["faglarerNavn"], e["ingeniorNavn"], e["aktiv"]) == ("IELS2001", "master", "Kari", "Bjørn", 1)
    assert anne.post("/api/emner", json={"kode": "IELS2001"}).status_code == 400  # finnes
    assert anne.post("/api/emner", json={"kode": ""}).status_code == 400
    assert anne.post("/api/emner", json={"kode": "X1", "program": "phd"}).status_code == 400
    assert anne.post("/api/emner", json={"kode": "X2", "faglarer": inst["u"]["anne"]}).status_code == 400  # ikke faglærer
    assert anne.post("/api/emner", json={"kode": "X3", "ingenior": inst["u"]["ola"]}).status_code == 400  # ikke ingeniør
    assert anne.patch(f"/api/emner/{e['id']}", json={"kode": "EMNE-A"}).status_code == 400  # kode tatt
    assert anne.patch("/api/emner/9999", json={"navn": "x"}).status_code == 404


def test_deaktivert_emne(inst):
    anne = logg_inn("anne")
    anne.patch(f"/api/emner/{inst['e']['EMNE-A']}", json={"aktiv": False})
    ola = logg_inn("ola")
    assert [(e["kode"], e["aktiv"]) for e in state(ola)["emner"]] == [("EMNE-B", 1), ("EMNE-A", 0)]  # kan slå på igjen
    r = ola.post(G, json={"navn": "Nytt", "skolear": SY, "emne": inst["e"]["EMNE-A"]})
    assert r.status_code == 403
    # prosjektene i emnet finnes fortsatt, og historikken står
    assert "Prosjekt A" in egne(ola)


def test_siden_for_ingenior_med_andres_emner(inst):
    from test_side import kjor_siden
    anne = logg_inn("anne")
    res = kjor_siden(state(anne))
    assert res["errors"] == []
    assert "Prosjekt A" in res["tbody"] and "Prosjekt C" in res["tbody"] and "Prosjekt uten emne" in res["tbody"]
    assert "Prosjekt B" not in res["tbody"] and "Prosjekt D" not in res["tbody"]  # bare egne emner
    assert 'data-act="emne"' in res["tbody"]

    res = kjor_siden(state(logg_inn("ola")))  # faglærer A: emne A og B
    assert res["errors"] == []
    assert "Prosjekt A" in res["tbody"] and "Prosjekt B" in res["tbody"]
    assert "Prosjekt C" not in res["tbody"] and "Prosjekt uten emne" not in res["tbody"]


def test_andres_varsler_lekker_ikke(inst):
    """Varsler om prosjekter du ikke ser, skal heller ikke vises – selv om prosjektet finnes."""
    logg_inn("kari").patch(f"{G}/{inst['p']['EMNE-D']}", json={"notat": "endret 9"})  # Ceciles emne
    assert state(logg_inn("anne"))["endringer"] == []
    assert len(state(logg_inn("cecilie"))["endringer"]) == 1


def test_andres_bom_er_ikke_ledig(inst):
    from hjelp import lag_bom
    fil = lag_bom("Bjørns – BOM.xlsx").name
    lag_bom("Ledig – BOM.xlsx")
    logg_inn("bjorn").patch(f"{G}/{inst['p']['EMNE-B']}", json={"bom": fil})
    assert state(logg_inn("anne"))["ledigeBom"] == ["Ledig – BOM.xlsx"]


def test_emner_teller_prosjekter_for_alle(inst):
    anne = state(logg_inn("anne"))
    assert {e["kode"]: e["prosjekter"] for e in anne["emner"]} == {"EMNE-A": 1, "EMNE-B": 1, "EMNE-C": 1, "EMNE-D": 1}


# ---------- faglæreren lager emner og velger ingeniør ----------

def test_faglarer_lager_emne_og_velger_ingenior(inst):
    ola = logg_inn("ola")
    assert [i["navn"] for i in state(ola)["ingeniorer"]] == ["Anne", "Bjørn", "Cecilie"]
    r = ola.post("/api/emner", json={"kode": "ola2", "navn": "Robotikk", "ingenior": inst["u"]["cecilie"]})
    e = r.get_json()
    assert (e["kode"], e["faglarerNavn"], e["ingeniorNavn"]) == ("OLA2", "Ola", "Cecilie")
    # kan ikke lage emner for andre faglærere
    assert ola.post("/api/emner", json={"kode": "KARIS", "faglarer": inst["u"]["kari"]}).status_code == 403

    # Cecilie får beskjed
    cec = logg_inn("cecilie")
    v = [x for x in state(cec)["endringer"] if x["mottaker_id"]]
    assert [(x["handling"], x["bruker_navn"], x["prosjekt_navn"]) for x in v] == [("valgt_ingenior", "Ola", "OLA2")]
    assert state(logg_inn("anne"))["endringer"] == []

    # Ola lager et prosjekt i emnet – det havner hos Cecilie
    g = ola.post(G, json={"navn": "Robot", "skolear": SY, "emne": e["id"]}).get_json()
    assert g["ingeniorNavn"] == "Cecilie"
    assert "Robot" in egne(cec)

    # Cecilie kvitterer; Anne kan ikke kvittere for henne
    assert logg_inn("anne").post("/api/endringer/kvitter", json={"ids": [v[0]["id"]]}).get_json() == {"kvittert": 0}
    assert cec.post("/api/endringer/kvitter", json={"ids": [v[0]["id"]]}).get_json() == {"kvittert": 1}
    assert [x for x in state(cec)["endringer"] if x["mottaker_id"]] == []


def test_faglarer_bytter_ingenior_pa_eget_emne(inst):
    ola = logg_inn("ola")
    r = ola.patch(f"/api/emner/{inst['e']['EMNE-A']}", json={"ingenior": inst["u"]["bjorn"]})
    assert r.get_json()["ingeniorNavn"] == "Bjørn"
    assert [x["handling"] for x in state(logg_inn("bjorn"))["endringer"] if x["mottaker_id"]] == ["valgt_ingenior"]
    assert [x["handling"] for x in state(logg_inn("anne"))["endringer"] if x["mottaker_id"]] == ["fjernet_ingenior"]
    assert "Prosjekt A" not in egne(logg_inn("anne")) and "Prosjekt A" in egne(logg_inn("bjorn"))


def test_faglarer_kan_ikke_endre_andres_emner_eller_bytte_faglarer(inst):
    ola = logg_inn("ola")
    assert ola.patch(f"/api/emner/{inst['e']['EMNE-C']}", json={"ingenior": inst["u"]["bjorn"]}).status_code == 403
    assert ola.patch(f"/api/emner/{inst['e']['EMNE-A']}", json={"faglarer": inst["u"]["kari"]}).status_code == 403
    assert ola.patch(f"/api/emner/{inst['e']['EMNE-A']}", json={"ingenior": inst["u"]["kari"]}).status_code == 400  # ikke ingeniør


def test_ingen_varsel_nar_du_velger_deg_selv(inst):
    anne = logg_inn("anne")
    anne.post("/api/emner", json={"kode": "ANNE1", "ingenior": inst["u"]["anne"]})
    assert state(anne)["endringer"] == []


def test_siden_for_faglarer_med_emner(inst):
    from test_side import kjor_siden
    ola = logg_inn("ola")
    ola.post("/api/emner", json={"kode": "OLA2", "ingenior": inst["u"]["cecilie"]})
    res = kjor_siden(state(ola))
    assert res["errors"] == []
    res = kjor_siden(state(logg_inn("cecilie")))
    assert res["errors"] == []
    assert "Ola valgte deg som ansvarlig ingeniør for OLA2" in res["notices"] and "kvitterid" in res["notices"]


def test_faglarer_setter_studenter_i_emnet(inst):
    ola = logg_inn("ola")
    assert ola.patch(f"/api/emner/{inst['e']['EMNE-A']}", json={"studenter": 48}).get_json()["studenter"] == 48
    p = ola.post(G, json={"navn": "Lodding", "skolear": SY, "gruppe": 4, "emne": inst["e"]["EMNE-A"]}).get_json()
    assert (p["studenter"], p["sett"]) == (48, 12)
    assert state(ola)["prosjekter"][0]["emneStudenter"] == 48  # faglæreren ser emnets tall også
    assert logg_inn("kari").patch(f"/api/emner/{inst['e']['EMNE-A']}", json={"studenter": 1}).status_code == 403
