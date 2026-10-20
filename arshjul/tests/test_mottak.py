"""Varemottak: det som kommer legges til «På lager» i BOM-en, og tellingen teller det ikke to ganger."""
import datetime as dt

import bom
import config
import frister
from hjelp import lag_bom

G = "/api/gjennomforinger"
NA = frister.skolear_for(dt.date.today())


def lager(fil):
    return {r["varenummer"]: r["lager"] for r in bom.read_bom(config.BOM_DIR / fil)["rader"]}


def rad(client, sy):
    return next(p for p in client.get("/api/state").get_json()["prosjekter"] if p["skolear"] == sy)


def post(client, gid, **body):
    return client.post(f"{G}/{gid}/bestilling", json=body)


def arduino_prosjekt(client, sy, lager_for=30):
    """Ett prosjekt, 30 studenter, én Arduino Uno hver."""
    g = client.post(G, json={"navn": "Arduino-kurs", "skolear": sy, "start": 34, "slutt": 50, "studenter": 30, "type": "sett"}).get_json()
    fil = lag_bom(rader=[("Arduino Uno", "Elfa", "A1", 1, lager_for, 250.0)]).name
    client.patch(f"{G}/{g['id']}", json={"bom": fil})
    return g["id"], fil


def test_tapt_to_bestill_to_motta_to(client):
    """Ingeniørens eksempel: 30 lånt ut, 28 kom tilbake, bestill 2, de kommer."""
    i_fjor, fil = arduino_prosjekt(client, NA - 1)
    client.post(f"{G}/{i_fjor}/telling", json={"linjer": [{"key": "elfa|a1", "telt": 28}]})
    client.post(f"{G}/{i_fjor}/telling/skriv")
    assert lager(fil) == {"A1": 28}

    client.post(f"/api/skolear/{NA - 1}/kopier")
    i_ar = rad(client, NA)["id"]
    assert rad(client, NA)["bomStatus"]["bestill"] == 1  # 30 − 28 = 2 Arduinoer å bestille
    post(client, i_ar, handling="bestilt", leverandor="elfa")

    # de to kommer
    r = post(client, i_ar, handling="mottak", leverandor="elfa").get_json()
    assert r["skrevet"] == ["elfa|a1"] and r["backup"]
    assert lager(fil) == {"A1": 30}
    p = rad(client, NA)
    elfa = p["bestilling"]["grupper"][0]
    assert (elfa["status"], elfa["tillegg"], elfa["overskudd"]) == ("mottatt", [], [])  # ikke «bestilt for mye»
    assert (elfa["linjer"], elfa["eks"]) == (1, 500.0)  # det som ble bestilt, selv om BOM-en sier 0 nå
    assert p["bestilling"]["ferdig"] and p["bomStatus"]["bestill"] == 0
    assert [m["gjenstar"] for m in elfa["mottak"]] == [0]

    # tellingen etter neste prosjekt: før = 30, ikke 32
    g = {l["key"]: l for l in p["telling"]["grunnlag"]}["elfa|a1"]
    assert (g["lagerFor"], g["bestilt"]) == (30, 0)

    # fjorårets gjennomføring sammenligner ikke lenger mot BOM-en
    assert not rad(client, NA - 1)["bestilling"]["tillegg"]


def test_delvis_mottak_og_angre(client):
    gid, fil = arduino_prosjekt(client, NA + 1, lager_for=27)  # 3 å bestille
    post(client, gid, handling="bestilt", leverandor="elfa")
    assert post(client, gid, handling="mottak", leverandor="elfa", linjer={"elfa|a1": 1}).status_code == 200
    elfa = rad(client, NA + 1)["bestilling"]["grupper"][0]
    assert (elfa["status"], elfa["mottak"][0]["lagt"], elfa["mottak"][0]["gjenstar"]) == ("delvis", 1, 2)
    assert lager(fil) == {"A1": 28}
    assert elfa["tillegg"] == [] and elfa["overskudd"] == []

    # tellingen: før = 28 på lager + 2 som ikke er lagt på lager ennå = 30
    g = rad(client, NA + 1)["telling"]["grunnlag"][0]
    assert g["lagerFor"] + g["bestilt"] == 30

    # kan ikke sette tilbake til «bestilt» eller slette registreringen mens noe er lagt på lager
    assert post(client, gid, handling="status", leverandor="elfa", status="bestilt").status_code == 400
    assert post(client, gid, handling="angre", leverandor="elfa").status_code == 400

    post(client, gid, handling="mottak", leverandor="elfa")  # resten
    assert lager(fil) == {"A1": 30}
    elfa = rad(client, NA + 1)["bestilling"]["grupper"][0]
    assert elfa["status"] == "mottatt" and [x["handling"] for x in elfa["lagerLogg"]] == ["mottatt", "mottatt"]
    assert post(client, gid, handling="mottak", leverandor="elfa").status_code == 400  # ingenting igjen

    r = post(client, gid, handling="angre_mottak", leverandor="elfa").get_json()
    assert r["skrevet"] == ["elfa|a1"]
    assert lager(fil) == {"A1": 27}
    elfa = rad(client, NA + 1)["bestilling"]["grupper"][0]
    assert (elfa["status"], elfa["mottak"][0]["lagt"]) == ("bestilt", 0)
    assert post(client, gid, handling="angre", leverandor="elfa").status_code == 200  # nå går det


def test_mottak_legger_til_det_som_star_i_excel(client):
    """Er «På lager» endret i Excel imens, legges mottaket til det nye tallet."""
    gid, fil = arduino_prosjekt(client, NA + 1, lager_for=28)
    post(client, gid, handling="bestilt", leverandor="elfa")
    bom.write_stock(config.BOM_DIR / fil, {"elfa|a1": 25}, config.BOM_DIR / "Sikkerhetskopi")  # «Excel»
    post(client, gid, handling="mottak", leverandor="elfa")
    assert lager(fil) == {"A1": 27}


def test_angre_mottak_blir_ikke_negativt(client):
    gid, fil = arduino_prosjekt(client, NA + 1, lager_for=28)
    post(client, gid, handling="bestilt", leverandor="elfa")
    post(client, gid, handling="mottak", leverandor="elfa")  # 28 + 2 = 30
    bom.write_stock(config.BOM_DIR / fil, {"elfa|a1": 1}, config.BOM_DIR / "Sikkerhetskopi")  # «Excel»: 1 igjen
    r = post(client, gid, handling="angre_mottak", leverandor="elfa").get_json()
    assert r["skrevet"] == [] and r["konflikt"][0]["beskrivelse"] == "Arduino Uno"
    assert lager(fil) == {"A1": 1}
    assert rad(client, NA + 1)["bestilling"]["grupper"][0]["status"] == "mottatt"  # uendret


def test_ugyldig_mottak(client):
    gid, _ = arduino_prosjekt(client, NA + 1, lager_for=28)
    assert post(client, gid, handling="mottak", leverandor="elfa").status_code == 400  # ikke bestilt
    post(client, gid, handling="bestilt", leverandor="elfa")
    assert post(client, gid, handling="mottak", leverandor="elfa", linjer={"elfa|a1": -1}).status_code == 400
    assert post(client, gid, handling="mottak", leverandor="elfa", linjer={"finnes-ikke": 1}).status_code == 400
    assert post(client, gid, handling="mottak", leverandor="elfa", linjer={"elfa|a1": "mange"}).status_code == 400
    assert post(client, gid, handling="angre_mottak", leverandor="elfa").status_code == 400


def test_write_stock_legg_til():
    path = lag_bom(rader=[("Arduino Uno", "Elfa", "A1", 1, 28, 250.0)])
    res = bom.write_stock(path, {"elfa|a1": 2}, config.BOM_DIR / "Sikkerhetskopi", legg_til=True)
    assert res["nye"] == {"elfa|a1": 30} and lager(path.name) == {"A1": 30}
    res = bom.write_stock(path, {"elfa|a1": -31}, config.BOM_DIR / "Sikkerhetskopi", legg_til=True)
    assert res["skrevet"] == [] and res["konflikt"][0]["faktisk"] == 30
