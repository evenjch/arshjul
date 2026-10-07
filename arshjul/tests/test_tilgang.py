"""Innlogging, roller og endringslogg – slik det skal virke på serveren."""
import datetime as dt

import pytest

import app as arshjul
import auth
import config
import frister
import manage
from conftest import med_csrf
from hjelp import lag_bom

G = "/api/gjennomforinger"
SY = frister.skolear_for(dt.date.today()) + 1
PASSORD = "et-langt-passord"


@pytest.fixture
def innlogging(monkeypatch):
    ids = {}
    for navn, fullt, rolle in (("ing", "Ingrid Ingeniør", "ingenior"), ("ola", "Ola Faglærer", "faglarer"),
                               ("kari", "Kari Faglærer", "faglarer")):
        ids[navn] = arshjul.store.ny_bruker(navn, fullt, rolle, auth.hash_passord(PASSORD))
    ids["emne_ola"] = arshjul.store.nytt_emne({"kode": "OLA1", "faglarer_id": ids["ola"], "ingenior_id": ids["ing"]})["id"]
    ids["emne_kari"] = arshjul.store.nytt_emne({"kode": "KARI1", "faglarer_id": ids["kari"], "ingenior_id": ids["ing"]})["id"]
    auth._ip_feil.clear()
    return ids


def ny_klient():
    arshjul.app.config["TESTING"] = True
    return arshjul.app.test_client()


def logg_inn(brukernavn, passord=PASSORD, neste=None):
    c = ny_klient()
    c.get("/login")  # gir økten et CSRF-token
    with c.session_transaction() as s:
        token = s["csrf"]
    r = c.post("/login" + (f"?neste={neste}" if neste else ""), data={"brukernavn": brukernavn, "passord": passord, "csrf": token})
    with c.session_transaction() as s:
        ny = s.get("csrf")
    if r.status_code == 302:
        assert ny != token  # nytt token ved innlogging
    c.environ_base["HTTP_X_CSRF_TOKEN"] = ny or token
    c.svar = r
    return c


def state(c):
    return c.get("/api/state").get_json()


# ---------- innlogging ----------

def test_ikke_innlogget(innlogging):
    c = ny_klient()
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    r = c.get("/api/state")
    assert r.status_code == 401 and r.get_json()["login"] is True


def test_logg_inn_og_ut(innlogging):
    c = logg_inn("ola")
    assert c.svar.status_code == 302 and c.svar.headers["Location"] == "/"
    assert state(c)["meg"] == {"id": innlogging["ola"], "navn": "Ola Faglærer", "rolle": "faglarer", "admin": False}
    assert "Ola Faglærer" in c.get("/").get_data(as_text=True)
    c.post("/logout", data={"csrf": c.environ_base["HTTP_X_CSRF_TOKEN"]})
    assert c.get("/api/state").status_code == 401


def test_brukernavn_skiller_ikke_store_og_sma_bokstaver(innlogging):
    assert logg_inn("OLA").svar.status_code == 302


def test_feil_passord_og_lasing(innlogging):
    for _ in range(auth.MAKS_FEIL):
        r = logg_inn("ola", "feil-passord").svar
        assert r.status_code == 401 and "Feil brukernavn eller passord" in r.get_data(as_text=True)
    r = logg_inn("ola").svar  # riktig passord, men låst
    assert r.status_code == 401 and "For mange feil forsøk" in r.get_data(as_text=True)
    arshjul.store.endre_bruker(innlogging["ola"], last_til=None)  # som «manage.py aktiver»
    assert logg_inn("ola").svar.status_code == 302


def test_ukjent_bruker_gir_samme_melding(innlogging):
    r = logg_inn("finnes-ikke").svar
    assert r.status_code == 401 and "Feil brukernavn eller passord" in r.get_data(as_text=True)


def test_neste_kan_ikke_sende_deg_ut(innlogging):
    assert logg_inn("ola", neste="//ondsinnet.no").svar.headers["Location"] == "/"
    assert logg_inn("ola", neste="https://ondsinnet.no").svar.headers["Location"] == "/"
    assert logg_inn("ola", neste="/api/state").svar.headers["Location"] == "/api/state"


def test_deaktivert_og_nytt_passord_avslutter_okten(innlogging):
    c = logg_inn("ola")
    arshjul.store.endre_bruker(innlogging["ola"], aktiv=0)
    assert c.get("/api/state").status_code == 401
    arshjul.store.endre_bruker(innlogging["ola"], aktiv=1)
    c = logg_inn("ola")
    arshjul.store.endre_bruker(innlogging["ola"], passord=auth.hash_passord("et-helt-nytt-passord"))
    assert c.get("/api/state").status_code == 401


def test_csrf_kreves(innlogging):
    c = logg_inn("ing")
    c.environ_base.pop("HTTP_X_CSRF_TOKEN")
    r = c.post(G, json={"navn": "Uten token", "skolear": SY})
    assert r.status_code == 400 and "utdatert" in r.get_json()["feil"]


def test_csrf_kreves_ogsa_lokalt():
    """Uten innlogging kan en annen nettside ellers få nettleseren til å endre årshjulet."""
    r = ny_klient().post(G, json={"navn": "Fra en annen side", "skolear": SY})
    assert r.status_code == 400


def test_sikkerhetshoder(client):
    h = client.get("/").headers
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert client.get("/api/state").headers["Cache-Control"] == "no-store"


# ---------- faglærer ----------

def prosjekt_med_bom(c, navn="Insektsløp", **kw):
    g = c.post(G, json={"navn": navn, "skolear": SY, "start": 44, "slutt": 44, "sett": 30, **kw}).get_json()
    fil = lag_bom(f"{navn} – BOM.xlsx", rader=[("Arduino", "Elfa", "A1", 1, 0, 250.0)]).name
    c.patch(f"{G}/{g['id']}", json={"bom": fil})
    return g


def test_faglarer_ser_ikke_bom_eller_kostnader(innlogging):
    ing = logg_inn("ing")
    g = prosjekt_med_bom(ing, emne=innlogging["emne_ola"])
    ing.post(f"{G}/{g['id']}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    ola = logg_inn("ola")
    st = state(ola)
    p = st["prosjekter"][0]
    for felt in ("bom", "bomStatus", "bestilling", "telling", "bestiltKr", "slettet"):
        assert felt not in p
    assert p["egen"] is True and p["faglarerNavn"] == "Ola Faglærer" and p["frister"]["frist"]["uke"]
    assert (st["ledigeBom"], st["mapper"], st["endringer"], st["slettede"], st["faglarere"], st["oppsett"]) == ([], {}, [], [], [], None)
    assert [e["kode"] for e in st["emner"]] == ["OLA1"]  # bare egne emner
    assert state(logg_inn("kari"))["prosjekter"] == []  # ikke Karis emne: ser det ikke


def test_faglarer_lager_og_endrer_egne_prosjekter(innlogging):
    ola = logg_inn("ola")
    assert ola.post(G, json={"navn": "Uten emne", "skolear": SY}).status_code == 403
    assert ola.post(G, json={"navn": "Karis emne", "skolear": SY, "emne": innlogging["emne_kari"]}).status_code == 403
    r = ola.post(G, json={"navn": "Lodding", "skolear": SY, "start": 40, "lev": 9, "buf": 9, "bom": "x.xlsx", "color": 3,
                          "emne": innlogging["emne_ola"]})
    g = r.get_json()
    assert (g["faglarerId"], g["emneKode"], g["lev"], g["buf"], g["bom"], g["color"]) == (innlogging["ola"], "OLA1", 2, 2, None, 3)

    assert ola.patch(f"{G}/{g['id']}", json={"start": 42, "sett": 12, "notat": "IELS1001"}).status_code == 200
    r = ola.patch(f"{G}/{g['id']}", json={"lev": 1})
    assert r.status_code == 403 and "leveringstid" in r.get_json()["feil"]
    assert ola.patch(f"{G}/{g['id']}", json={"bom": "annen.xlsx"}).status_code == 403
    assert ola.patch(f"{G}/{g['id']}", json={"emne": innlogging["emne_kari"]}).status_code == 403

    kari = logg_inn("kari")
    r = kari.patch(f"{G}/{g['id']}", json={"start": 30})
    assert r.status_code == 403 and "egne emner" in r.get_json()["feil"]
    assert kari.delete(f"{G}/{g['id']}").status_code == 403


@pytest.mark.parametrize("metode,sti,body", [
    ("post", "/{id}/mal", None), ("post", "/{id}/apne", None), ("post", "/{id}/liste", None),
    ("post", "/{id}/bestilling", {"handling": "bestilt", "leverandor": "elfa"}), ("post", "/{id}/sett", None),
    ("post", "/{id}/telling", {"linjer": [{"key": "elfa|a1", "telt": 1}]}), ("post", "/{id}/telling/skriv", None),
    ("post", "/{id}/telling/angre", None), ("post", "/{id}/gjenopprett", None),
])
def test_faglarer_kan_ikke_ingeniorens_handlinger(innlogging, metode, sti, body):
    ing = logg_inn("ing")
    g = prosjekt_med_bom(ing, emne=innlogging["emne_ola"])
    ola = logg_inn("ola")
    r = getattr(ola, metode)(G + sti.format(id=g["id"]), json=body)
    assert r.status_code == 403


def test_faglarer_kan_ikke_globale_handlinger(innlogging):
    ola = logg_inn("ola")
    assert ola.put("/api/stengte", json=[]).status_code == 403
    assert ola.post(f"/api/skolear/{SY}/kopier").status_code == 403
    assert ola.post("/api/endringer/1/kvitter").status_code == 403
    assert ola.post("/api/mappe/bom").status_code == 403


# ---------- endringslogg og varsler ----------

def test_endringer_varsles_og_kvitteres(innlogging):
    ing = logg_inn("ing")
    g = prosjekt_med_bom(ing, emne=innlogging["emne_ola"])
    assert state(ing)["endringer"] == []  # ingeniørens egne endringer varsles ikke

    ola = logg_inn("ola")
    ola.patch(f"{G}/{g['id']}", json={"start": 42, "slutt": 42})
    ola.patch(f"{G}/{g['id']}", json={"sett": 32, "type": "sett"})
    endr = state(ing)["endringer"]
    assert sorted((e["bruker_navn"], e["felt"], e["fra"], e["til"]) for e in endr) == sorted([
        ("Ola Faglærer", "uke fra", "44", "42"), ("Ola Faglærer", "uke til", "44", "42"),
        ("Ola Faglærer", "antall sett", "30", "32"), ("Ola Faglærer", "type", "hack", "sett")])
    assert {e["gjennomforing_id"] for e in endr} == {int(g["id"])}

    ola.patch(f"{G}/{g['id']}", json={"sett": 32})  # ingen endring – ingen logg
    assert len(state(ing)["endringer"]) == 4

    assert ing.post(f"/api/endringer/{g['id']}/kvitter").get_json() == {"kvittert": 4}
    assert state(ing)["endringer"] == []


def test_ingenior_velger_emne(innlogging):
    ing = logg_inn("ing")
    g = ing.post(G, json={"navn": "Uten emne", "skolear": SY}).get_json()
    assert (g["emneId"], g["faglarerId"]) == (None, None)
    assert logg_inn("kari").patch(f"{G}/{g['id']}", json={"start": 30}).status_code == 403  # ingen faglærer ennå
    assert ing.patch(f"{G}/{g['id']}", json={"emne": 9999}).status_code == 400
    r = ing.patch(f"{G}/{g['id']}", json={"emne": innlogging["emne_kari"]})
    assert (r.get_json()["emneKode"], r.get_json()["faglarerNavn"]) == ("KARI1", "Kari Faglærer")
    assert logg_inn("kari").patch(f"{G}/{g['id']}", json={"start": 30}).status_code == 200
    st = state(ing)
    assert [f["navn"] for f in st["faglarere"]] == ["Kari Faglærer", "Ola Faglærer"]
    assert [f["navn"] for f in st["ingeniorer"]] == ["Ingrid Ingeniør"]


def test_faglarer_fjerner_ingenior_gjenoppretter_eller_sletter(innlogging):
    ing = logg_inn("ing")
    g = prosjekt_med_bom(ing, emne=innlogging["emne_ola"])
    ola = logg_inn("ola")
    assert ola.delete(f"{G}/{g['id']}").get_json() == {"ok": True, "venter": True}
    assert state(ola)["prosjekter"] == [] and state(ing)["prosjekter"] == []
    sl = state(ing)["slettede"]
    assert [(s["navn"], s["slettetAv"]) for s in sl] == [("Insektsløp", "Ola Faglærer")]
    assert ola.patch(f"{G}/{g['id']}", json={"start": 40}).status_code == 404

    ing.post(f"{G}/{g['id']}/gjenopprett")
    assert [p["navn"] for p in state(ola)["prosjekter"]] == ["Insektsløp"]
    assert state(ing)["slettede"] == [] and state(ing)["endringer"] == []

    ola.delete(f"{G}/{g['id']}")
    assert ing.delete(f"{G}/{g['id']}").status_code == 200  # «Bekreft sletting»
    assert state(ing)["slettede"] == [] and state(ing)["prosjekter"] == []


def test_fjernet_prosjekt_kopieres_ikke(innlogging):
    ing = logg_inn("ing")
    g = ing.post(G, json={"navn": "Kurs", "skolear": SY, "emne": innlogging["emne_ola"]}).get_json()
    logg_inn("ola").delete(f"{G}/{g['id']}")
    assert ing.post(f"/api/skolear/{SY}/kopier").get_json()["nye"] == []


def test_hvem_som_bestilte_lagres(innlogging):
    ing = logg_inn("ing")
    g = prosjekt_med_bom(ing)
    ing.post(f"{G}/{g['id']}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    ing.post(f"{G}/{g['id']}/bestilling", json={"handling": "mottak", "leverandor": "elfa"})
    b = arshjul.store.bestillinger()[g["id"]]["elfa"]
    assert b["av"] == "Ingrid Ingeniør" and b["lager_logg"][0]["av"] == "Ingrid Ingeniør"


# ---------- manage.py ----------

def test_manage(monkeypatch, capsys):
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", "passord-som-er-langt")
    manage.main(["ny-bruker", "per", "Per Hansen", "faglarer"])
    u = arshjul.store.bruker(brukernavn="per")
    assert (u["navn"], u["rolle"], u["aktiv"]) == ("Per Hansen", "faglarer", 1)
    assert u["passord"] != "passord-som-er-langt"  # bare hash
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "PER", "Dobbel", "faglarer"])
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "med mellomrom", "X", "faglarer"])
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", "kort")
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "lise", "Lise", "ingenior"])
    manage.main(["deaktiver", "per"])
    assert arshjul.store.bruker(brukernavn="per")["aktiv"] == 0
    manage.main(["aktiver", "per"])
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", "et-annet-langt-passord")
    gammel = u["passord"]
    manage.main(["passord", "per"])
    assert arshjul.store.bruker(brukernavn="per")["passord"] != gammel
    manage.main(["liste"])
    assert "Per Hansen" in capsys.readouterr().out


# ---------- siden, som faglærer og som ingeniør med varsler ----------

def test_siden_for_begge_roller(innlogging):
    from test_side import kjor_siden

    ing = logg_inn("ing")
    g = prosjekt_med_bom(ing, emne=innlogging["emne_ola"])
    ing.post(f"{G}/{g['id']}/bestilling", json={"handling": "bestilt", "leverandor": "elfa"})
    annen = ing.post(G, json={"navn": "Smart City", "skolear": SY, "start": 15, "emne": innlogging["emne_ola"]}).get_json()
    ing.post(G, json={"navn": "Karis prosjekt", "skolear": SY, "emne": innlogging["emne_kari"]})

    ola = logg_inn("ola")
    ola.patch(f"{G}/{g['id']}", json={"start": 40, "sett": 35})
    assert ola.post(G, json={"navn": "Olas nye", "skolear": SY, "start": 20, "emne": innlogging["emne_ola"]}).status_code == 200
    ola.delete(f"{G}/{annen['id']}")

    res = kjor_siden(state(ola))
    assert res["errors"] == []
    assert "Fjern" in res["tbody"] and "OLA1 · faglærer Ola Faglærer (deg) · ingeniør Ingrid Ingeniør" in res["tbody"]
    assert "Registrer bestilt" not in res["tbody"] and "Lag BOM-mal" not in res["tbody"]
    assert 'data-k="lev"' not in res["tbody"]  # leveringstid kan ikke endres av faglærer
    assert "Karis prosjekt" not in res["tbody"]  # ser bare egne emner

    res = kjor_siden(state(ing))
    assert res["errors"] == []
    n = res["notices"]
    assert "Ola Faglærer endret «Insektsløp»" in n and "uke fra" in n and "OK, sett" in n
    assert "Ola Faglærer fjernet «Smart City»" in n and "Gjenopprett" in n
    assert "La til prosjektet" in n  # Olas nye
    assert 'data-act="emne"' in res["tbody"]
