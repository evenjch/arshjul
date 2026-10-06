"""Alltid innlogging, administrator og brukere på nettsiden."""
import pytest

import app as arshjul
import auth
import config
from conftest import ADMIN_PASSORD, som

PASSORD = "et-langt-passord"


def ny_klient():
    arshjul.app.config["TESTING"] = True
    return arshjul.app.test_client()


def bruker(navn, rolle="ingenior", admin=False):
    return arshjul.store.ny_bruker(navn, navn.title(), rolle, auth.hash_passord(PASSORD), admin=admin)


# ---------- første gang ----------

def oppsett(c, **felt):
    c.get("/oppsett")
    with c.session_transaction() as s:
        token = s.get("csrf", "ingen")  # finnes det brukere, sendes du videre uten skjema
    data = {"brukernavn": "even", "navn": "Even", "passord": "mitt-gode-passord", "passord2": "mitt-gode-passord",
            "csrf": token, **felt}
    return c.post("/oppsett", data=data)


def test_forste_gang_lokalt_lages_administrator():
    c = ny_klient()
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/oppsett")
    assert c.get("/login").headers["Location"].endswith("/oppsett")
    assert oppsett(c, passord2="noe-annet-her").status_code == 400
    assert oppsett(c, passord="kort", passord2="kort").status_code == 400
    r = oppsett(c)
    assert r.status_code == 302 and r.headers["Location"] == "/"
    with c.session_transaction() as s:
        c.environ_base["HTTP_X_CSRF_TOKEN"] = s["csrf"]
    st = c.get("/api/state").get_json()
    assert st["meg"]["navn"] == "Even" and st["meg"]["admin"] is True and st["meg"]["rolle"] == "ingenior"
    # bare én gang: nå finnes det en bruker
    assert ny_klient().get("/oppsett").headers["Location"].endswith("/login")
    assert oppsett(ny_klient(), brukernavn="inntrenger").status_code == 302  # sendt til innlogging
    assert arshjul.store.bruker(brukernavn="inntrenger") is None


def test_forste_gang_pa_server_bare_med_manage(monkeypatch):
    monkeypatch.setattr(config, "LOKAL", False)
    c = ny_klient()
    assert c.get("/").headers["Location"].startswith("/login")
    assert "manage.py ny-bruker" in c.get("/login").get_data(as_text=True)
    oppsett(c)
    assert arshjul.store.antall_brukere() == 0


def test_ingen_anonym_tilgang_lokalt():
    bruker("ing")
    c = ny_klient()
    assert c.get("/api/state").status_code == 401
    assert c.get("/").headers["Location"].startswith("/login")
    # brukere fra før administrator fantes: siden forteller hvordan du blir administrator
    assert "manage.py admin" in c.get("/login").get_data(as_text=True)


def test_hemmelig_nokkel_lagres_lokalt():
    # laget én gang og gjenbrukt, så du forblir innlogget etter omstart
    assert len(config.SECRET_KEY) >= 32 and config.OKT_TIMER == 24 * 30


# ---------- administrator ----------

def test_admin_har_alle_emner(client):
    anne, bjorn = bruker("anne"), bruker("bjorn")
    e = arshjul.store.nytt_emne({"kode": "E1", "ingenior_id": bjorn})["id"]
    g = client.post("/api/gjennomforinger", json={"navn": "Bjørns", "skolear": 2030, "emne": e}).get_json()
    assert client.get("/api/state").get_json()["prosjekter"][0]["egen"] is True  # admin
    a = som(ny_klient(), anne)
    assert a.get("/api/state").get_json()["prosjekter"] == []  # Anne ser ikke Bjørns emne i det hele tatt
    assert a.patch(f"/api/gjennomforinger/{g['id']}", json={"start": 40}).status_code == 403
    assert client.patch(f"/api/gjennomforinger/{g['id']}", json={"start": 40}).status_code == 200


def test_admin_lager_og_endrer_brukere(client):
    r = client.post("/api/brukere", json={"brukernavn": "ola", "navn": "Ola", "rolle": "faglarer", "passord": PASSORD,
                                          "admin": True})
    u = r.get_json()
    assert (u["rolle"], u["admin"], u["aktiv"]) == ("faglarer", False, True)  # faglærer kan ikke være admin
    assert "passord" not in u
    assert client.post("/api/brukere", json={"brukernavn": "OLA", "navn": "X", "rolle": "faglarer", "passord": PASSORD}).status_code == 400
    assert client.post("/api/brukere", json={"brukernavn": "kort", "navn": "X", "rolle": "faglarer", "passord": "kort"}).status_code == 400
    assert client.post("/api/brukere", json={"brukernavn": "a b", "navn": "X", "rolle": "faglarer", "passord": PASSORD}).status_code == 400
    assert client.post("/api/brukere", json={"brukernavn": "x", "navn": "X", "rolle": "sjef", "passord": PASSORD}).status_code == 400

    assert client.patch(f"/api/brukere/{u['id']}", json={"navn": "Ola Nordmann"}).get_json()["navn"] == "Ola Nordmann"
    brukere = client.get("/api/state").get_json()["brukere"]
    assert {b["brukernavn"] for b in brukere} == {"admin", "ola"} and all("passord" not in b for b in brukere)

    # ny ingeniør som admin
    r = client.post("/api/brukere", json={"brukernavn": "kari", "navn": "Kari", "rolle": "ingenior", "passord": PASSORD, "admin": True})
    assert r.get_json()["admin"] is True


def test_nytt_passord_og_deaktivering_avslutter_okten(client):
    uid = bruker("anne")
    a = som(ny_klient(), uid)
    assert a.get("/api/state").status_code == 200
    client.patch(f"/api/brukere/{uid}", json={"passord": "et-helt-nytt-passord"})
    assert a.get("/api/state").status_code == 401
    a = som(ny_klient(), uid)
    client.patch(f"/api/brukere/{uid}", json={"aktiv": False})
    assert a.get("/api/state").status_code == 401


def test_aktivering_og_nytt_passord_laaser_opp(client):
    uid = bruker("anne")
    arshjul.store.endre_bruker(uid, last_til="2999-01-01T00:00:00", feilforsok=3)
    client.patch(f"/api/brukere/{uid}", json={"aktiv": True})
    u = arshjul.store.bruker(id=uid)
    assert (u["last_til"], u["feilforsok"]) == (None, 0)


def test_det_finnes_alltid_en_administrator(client):
    admin = arshjul.store.bruker(brukernavn="admin")["id"]
    for endring in ({"admin": False}, {"aktiv": False}, {"rolle": "faglarer"}):
        r = client.patch(f"/api/brukere/{admin}", json=endring)
        assert r.status_code == 400 and "minst én aktiv administrator" in r.get_json()["feil"]
    bruker("kari", admin=True)
    assert client.patch(f"/api/brukere/{admin}", json={"admin": False}).status_code == 200


def test_rolle_kan_ikke_byttes_mens_brukeren_star_pa_emner(client):
    uid = bruker("anne")
    arshjul.store.nytt_emne({"kode": "E1", "ingenior_id": uid})
    r = client.patch(f"/api/brukere/{uid}", json={"rolle": "faglarer"})
    assert r.status_code == 400 and "E1" in r.get_json()["feil"]


@pytest.mark.parametrize("rolle", ["ingenior", "faglarer"])
def test_bare_admin_styrer_brukere(client, rolle):
    c = som(ny_klient(), bruker("vanlig", rolle))
    assert c.post("/api/brukere", json={"brukernavn": "x", "navn": "X", "rolle": "faglarer", "passord": PASSORD}).status_code == 403
    assert c.patch("/api/brukere/1", json={"admin": True}).status_code == 403
    assert c.get("/api/state").get_json()["brukere"] == []


def test_manage_admin(capsys, monkeypatch):
    import manage
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", ADMIN_PASSORD)
    manage.main(["ny-bruker", "sjef", "Sjefen", "ingenior", "--admin"])
    assert arshjul.store.bruker(brukernavn="sjef")["admin"] == 1
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "ola", "Ola", "faglarer", "--admin"])
    with pytest.raises(SystemExit):
        manage.main(["admin", "sjef", "--fjern"])  # den eneste
    manage.main(["ny-bruker", "anne", "Anne", "ingenior"])
    manage.main(["admin", "anne"])
    manage.main(["admin", "sjef", "--fjern"])
    assert arshjul.store.bruker(brukernavn="sjef")["admin"] == 0
    manage.main(["liste"])
    assert "(admin)" in capsys.readouterr().out


def test_siden_som_admin(client):
    from test_side import kjor_siden
    bruker("anne")
    res = kjor_siden(client.get("/api/state").get_json())
    assert res["errors"] == []
