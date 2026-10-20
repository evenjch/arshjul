import datetime as dt

import bestilling
import config
import frister
from hjelp import lag_bom, skriv_rader

G = "/api/gjennomforinger"
# «trenger mer» sjekkes bare før prosjektet starter: bruk neste skoleår, så testene ikke går ut på dato
NESTE = frister.skolear_for(dt.date.today()) + 1


def rad(besk, lev, bestill, pris=10.0, varenr=""):
    return {"beskrivelse": besk, "leverandor": lev, "varenummer": varenr, "bestill": bestill,
            "pris": pris, "total": round(bestill * pris, 2)}


def lagret(linjer, status="bestilt", navn="Elfa"):
    return {"navn": navn, "status": status, "linjer": linjer, "bestilt_dato": "2026-09-01",
            "mottatt_dato": None, "ordrenr": ""}


# ---------- logikken ----------

def test_grupper_etter_leverandor():
    g = bestilling.grupper([rad("A", "Elfa", 1), rad("B", " elfa ", 1), rad("C", "", 1), rad("D", "Kjell", 1)])
    assert [(x["key"], x["navn"], len(x["rader"])) for x in g] == [
        ("elfa", "Elfa", 2), ("", "Ukjent leverandør", 1), ("kjell", "Kjell", 1)]


def test_frys_tar_bare_med_det_som_skal_bestilles():
    linjer = bestilling.frys([rad("A", "Elfa", 6, varenr="A1"), rad("B", "Elfa", 0)])
    assert [(l["key"], l["antall"]) for l in linjer] == [("elfa|a1", 6)]


def test_avvik_tillegg_og_overskudd():
    frosset = bestilling.frys([rad("A", "Elfa", 6), rad("B", "Elfa", 5)])
    tillegg, overskudd = bestilling.avvik(frosset, [rad("A", "Elfa", 8), rad("B", "Elfa", 3), rad("C", "Elfa", 2)])
    assert [(t["beskrivelse"], t["mangler"]) for t in tillegg] == [("A", 2), ("C", 2)]
    assert [(o["beskrivelse"], o["bestilt"], o["trenger"]) for o in overskudd] == [("B", 5, 3)]


def test_slaa_sammen_beholder_det_storste():
    gammel = bestilling.frys([rad("A", "Elfa", 6), rad("B", "Elfa", 5)])
    ny = bestilling.frys([rad("A", "Elfa", 8), rad("B", "Elfa", 3)])
    assert {l["key"]: l["antall"] for l in bestilling.slaa_sammen(gammel, ny)} == {"elfa|a": 8, "elfa|b": 5}


def test_oversikt_status():
    rader = [rad("A", "Elfa", 6), rad("B", "Kjell", 2), rad("C", "Lager AS", 0)]
    o = bestilling.oversikt(rader, {})
    assert (o["status"], o["ferdig"], o["leverandorer"]) == ("ikke", False, 2)
    assert {g["key"]: g["status"] for g in o["grupper"]}["lager as"] == "lager"

    elfa = lagret(bestilling.frys(rader[:1]))
    o = bestilling.oversikt(rader, {"elfa": elfa})
    assert (o["status"], o["bestilte"]) == ("delvis_bestilt", 1)

    kjell = lagret(bestilling.frys(rader[1:2]), status="mottatt", navn="Kjell")
    o = bestilling.oversikt(rader, {"elfa": elfa, "kjell": kjell})
    assert (o["status"], o["ferdig"]) == ("bestilt", True)  # dårligste av bestilt og mottatt


def test_alt_paa_lager():
    o = bestilling.oversikt([rad("A", "Elfa", 0)], {})
    assert (o["status"], o["ferdig"]) == ("lager", True)


def test_leverandor_fjernet_fra_bom_etter_bestilling():
    o = bestilling.oversikt([rad("A", "Kjell", 0)], {"elfa": lagret(bestilling.frys([rad("X", "Elfa", 3)]))})
    elfa = next(g for g in o["grupper"] if g["key"] == "elfa")
    assert elfa["fjernet"] and elfa["overskudd"][0]["bestilt"] == 3


# ---------- gjennom API-et ----------

RADER = [
    ("Arduino Uno", "Elfa", "A1", 1, 4, 250.0),    # 1 × 10 − 4 = 6
    ("Motstand 220", "Elfa", "R220", 10, 500, 0.5),  # på lager
    ("Ledning", "Kjell", "L1", 2, 0, 10.0),          # 20
    ("Skrue", "", "S1", 4, 0, 1.0),                  # 40, ukjent leverandør
]


def oppsett(client):
    g = client.post(G, json={"navn": "Testprosjekt", "skolear": NESTE, "start": 44, "slutt": 44, "studenter": 10}).get_json()
    fil = lag_bom(rader=RADER).name
    client.patch(f"{G}/{g['id']}", json={"bom": fil})
    return g["id"]


def best(client):
    return client.get("/api/state").get_json()["prosjekter"][0]["bestilling"]


def post(client, gid, **body):
    return client.post(f"{G}/{gid}/bestilling", json=body)


def test_bestillingsflyt(client):
    gid = oppsett(client)
    b = best(client)
    assert (b["status"], b["leverandorer"]) == ("ikke", 3)
    assert [g["key"] for g in b["grupper"]] == ["elfa", "kjell", ""]

    assert post(client, gid, handling="bestilt", leverandor="elfa", ordrenr="PO-1").status_code == 200
    b = best(client)
    assert (b["status"], b["bestilte"]) == ("delvis_bestilt", 1)
    elfa = b["grupper"][0]
    assert (elfa["status"], elfa["ordrenr"]) == ("bestilt", "PO-1") and elfa["bestiltDato"]
    assert post(client, gid, handling="bestilt", leverandor="elfa").status_code == 400  # allerede bestilt

    post(client, gid, handling="bestilt", leverandor="kjell")
    post(client, gid, handling="bestilt", leverandor="")
    b = best(client)
    assert (b["status"], b["ferdig"], b["tillegg"]) == ("bestilt", True, False)

    # faglæreren øker til 12 sett etter bestilling
    client.patch(f"{G}/{gid}", json={"studenter": 12})
    b = best(client)
    assert (b["ferdig"], b["tillegg"]) == (False, True)
    mangler = {g["key"]: [t["mangler"] for t in g["tillegg"]] for g in b["grupper"]}
    assert mangler == {"elfa": [2], "kjell": [4], "": [8]}

    post(client, gid, handling="tillegg", leverandor="elfa", ordrenr="PO-2")
    elfa = best(client)["grupper"][0]
    assert (elfa["tillegg"], elfa["ordrenr"]) == ([], "PO-1 + PO-2")

    post(client, gid, handling="status", leverandor="kjell", status="mottatt")
    kjell = best(client)["grupper"][1]
    assert kjell["status"] == "mottatt" and kjell["mottattDato"]

    post(client, gid, handling="ordrenr", leverandor="kjell", ordrenr="K-9")
    assert best(client)["grupper"][1]["ordrenr"] == "K-9"

    post(client, gid, handling="angre", leverandor="")
    assert best(client)["grupper"][2]["status"] == "ikke"


def test_bom_endret_etter_bestilling(client):
    gid = oppsett(client)
    post(client, gid, handling="bestilt", leverandor="kjell")
    rader = [r if r[0] != "Ledning" else ("Ledning", "Kjell", "L1", 3, 0, 10.0) for r in RADER]
    rader.append(("Loddetinn", "Kjell", "T1", 1, 0, 50.0))
    skriv_rader(config.BOM_DIR / "Test – BOM.xlsx", rader)
    kjell = best(client)["grupper"][1]
    assert [(t["beskrivelse"], t["mangler"]) for t in kjell["tillegg"]] == [("Ledning", 10), ("Loddetinn", 10)]


def test_ugyldige_handlinger(client):
    gid = oppsett(client)
    assert post(client, gid, handling="noe", leverandor="elfa").status_code == 400
    assert post(client, gid, handling="status", leverandor="elfa", status="mottatt").status_code == 400  # ikke bestilt
    assert post(client, gid, handling="tillegg", leverandor="elfa").status_code == 400
    assert post(client, gid, handling="bestilt", leverandor="finnes ikke").status_code == 400
    assert post(client, gid, handling="bestilt", leverandor="elfa", dato="i går").status_code == 400
    post(client, gid, handling="bestilt", leverandor="elfa")
    assert post(client, gid, handling="status", leverandor="elfa", status="kansellert").status_code == 400


def test_liste_for_en_leverandor(client):
    gid = oppsett(client)
    r = client.post(f"{G}/{gid}/liste", json={"leverandor": "kjell"})
    assert r.status_code == 200
    assert r.get_json()["linjer"] == 1
    assert (config.LIST_DIR / r.get_json()["fil"]).exists()
    assert "Kjell" in r.get_json()["fil"]
    assert client.post(f"{G}/{gid}/liste").get_json()["linjer"] == 3  # hele BOM-en


def test_sletting_fjerner_bestillingene(client):
    import app as arshjul
    gid = oppsett(client)
    post(client, gid, handling="bestilt", leverandor="elfa")
    client.delete(f"{G}/{gid}")
    assert arshjul.store.bestillinger() == {}


def test_ingen_bestilling_uten_bom(client):
    client.post(G, json={"navn": "Uten BOM", "skolear": 2026})
    assert best(client) is None
