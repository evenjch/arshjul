"""Samme varenummer hos to leverandører (RS og Farnell) skal være to ulike linjer overalt."""
import bom
import config
from hjelp import lag_bom, skriv_rader

G = "/api/gjennomforinger"
RADER = [("rs test", "RS-Online", "1234", 1, 0, 10.0),
         ("farnell test", "Farnell", "1234", 1, 0, 5.0)]


def rader(*linjer):
    return [{"beskrivelse": b, "leverandor": l, "varenummer": v, "prSett": p, "lager": la, "pris": pr, "kommentar": ""}
            for b, l, v, p, la, pr in linjer]


def test_nokkel_har_leverandor():
    a, b = rader(*RADER)
    assert (bom.rad_key(a), bom.rad_key(b)) == ("rs-online|1234", "farnell|1234")
    assert bom.rad_key({"beskrivelse": "Skrue", "leverandor": "", "varenummer": ""}) == "|skrue"


def test_diff_blander_ikke_leverandorene():
    gammel = rader(*RADER)
    assert bom.diff(gammel, rader(*RADER)) == []
    ny = rader(RADER[0], ("farnell test", "Farnell", "1234", 1, 0, 6.0))
    assert [(e["beskrivelse"], e["felt"], e["fra"], e["til"]) for e in bom.diff(gammel, ny)] == [("farnell test", "pris", 5.0, 6.0)]


def test_diff_med_helt_like_linjer():
    """Samme leverandør og varenummer to ganger: sammenlignes i rekkefølge, ikke blandet."""
    to = rader(("A", "Elfa", "1", 1, 0, 1.0), ("A igjen", "Elfa", "1", 2, 0, 1.0))
    assert bom.diff(to, rader(*[(r["beskrivelse"], "Elfa", "1", r["prSett"], 0, 1.0) for r in to])) == []
    assert len(bom.diff(to, to[:1])) == 1  # den andre er fjernet


def test_ok_sett_virker(client):
    """Ingeniørens test: legg til to linjer med samme varenummer, lagre, trykk «OK, sett»."""
    g = client.post(G, json={"navn": "Multi leverandør - test", "skolear": 2026, "start": 44, "studenter": 10}).get_json()
    fil = lag_bom("Multi – BOM.xlsx", rader=[("Første", "Elfa", "E1", 1, 0, 1.0)]).name
    client.patch(f"{G}/{g['id']}", json={"bom": fil})
    client.get("/api/state")  # «sist sett» lagres
    skriv_rader(config.BOM_DIR / fil, [("Første", "Elfa", "E1", 1, 0, 1.0), *RADER])
    endringer = lambda: client.get("/api/state").get_json()["prosjekter"][0]["bomStatus"]["endringer"]
    assert sorted((e["type"], e["beskrivelse"]) for e in endringer()) == [("ny", "farnell test"), ("ny", "rs test")]
    assert client.post(f"{G}/{g['id']}/sett").status_code == 200
    assert endringer() == []


def test_telling_og_mottak_holder_linjene_fra_hverandre(client):
    g = client.post(G, json={"navn": "Multi", "skolear": 2026, "start": 44, "studenter": 10}).get_json()
    fil = lag_bom("Multi – BOM.xlsx", rader=RADER).name
    client.patch(f"{G}/{g['id']}", json={"bom": fil})
    lager = lambda: {r["leverandor"]: r["lager"] for r in bom.read_bom(config.BOM_DIR / fil)["rader"]}

    # bestill og motta bare hos RS
    client.post(f"{G}/{g['id']}/bestilling", json={"handling": "bestilt", "leverandor": "rs-online"})
    client.post(f"{G}/{g['id']}/bestilling", json={"handling": "mottak", "leverandor": "rs-online"})
    assert lager() == {"RS-Online": 10, "Farnell": 0}

    # telling skriver hver linje for seg
    client.post(f"{G}/{g['id']}/telling", json={"linjer": [{"key": "rs-online|1234", "telt": 9},
                                                             {"key": "farnell|1234", "telt": 7}]})
    r = client.post(f"{G}/{g['id']}/telling/skriv").get_json()
    assert sorted(r["skrevet"]) == ["farnell|1234", "rs-online|1234"] and r["hoppet"] == []
    assert lager() == {"RS-Online": 9, "Farnell": 7}
