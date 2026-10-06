import app as arshjul
import config

G = "/api/gjennomforinger"


def lag(client, **felt):
    r = client.post(G, json={"navn": "Insektsløp", "skolear": 2026, **felt})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_tom_state(client):
    st = client.get("/api/state").get_json()
    assert st["prosjekter"] == []
    assert st["modus"] == "lokal"
    assert len(st["neste8"]) == 8
    na = st["skolearNa"]
    assert {s["ar"] for s in st["skolear"]} >= {na, na + 1}
    assert all(len(s["uker"]) in (52, 53) for s in st["skolear"])


def test_opprett_endre_slett(client):
    p = lag(client, start=44, slutt=44)
    assert p["prosjektId"].startswith("insektsl")
    rad = client.get("/api/state").get_json()["prosjekter"][0]
    assert rad["navn"] == "Insektsløp" and rad["skolear"] == 2026 and rad["semester"] == "Høst"
    assert rad["frister"]["start"] == {"dato": "2026-10-26", "uke": 44, "ar": 2026}
    assert len(rad["hendelser"]) == 4

    r = client.patch(f"{G}/{p['id']}", json={"sett": 30, "notat": "IELS1001", "lev": 4})
    assert (r.get_json()["sett"], r.get_json()["lev"]) == (30, 4)

    assert client.delete(f"{G}/{p['id']}").status_code == 200
    assert client.get("/api/state").get_json()["prosjekter"] == []


def test_skolearet_vises_i_state(client):
    lag(client, skolear=2030, start=3, slutt=3)
    st = client.get("/api/state").get_json()
    assert 2030 in {s["ar"] for s in st["skolear"]}
    assert st["prosjekter"][0]["frister"]["start"]["ar"] == 2031


def test_uker_holdes_innenfor_1_til_53(client):
    p = lag(client, start=60, slutt=0)
    assert (p["start"], p["slutt"]) == (53, 1)


def test_navn_kreves(client):
    r = client.post(G, json={"navn": "  "})
    assert r.status_code == 400
    assert "navn" in r.get_json()["feil"]
    p = lag(client)
    assert client.patch(f"{G}/{p['id']}", json={"navn": ""}).status_code == 400


def test_ugyldig_skolear_og_type(client):
    assert client.post(G, json={"navn": "X", "skolear": "abc"}).status_code == 400
    assert client.post(G, json={"navn": "X", "type": "noe"}).status_code == 400


def test_ukjent_gjennomforing(client):
    assert client.patch(f"{G}/999", json={"sett": 1}).status_code == 404


def test_lag_mal_og_kobles(client):
    p = lag(client)
    r = client.post(f"{G}/{p['id']}/mal")
    assert r.status_code == 200
    fil = r.get_json()["fil"]
    assert (config.BOM_DIR / fil).exists()
    st = client.get("/api/state").get_json()["prosjekter"][0]
    assert st["bom"] == fil
    assert st["bomStatus"]["finnes"] is True


def test_stengte_perioder(client):
    lag(client, start=34, slutt=34, lev=1, buf=0)
    frist = lambda: client.get("/api/state").get_json()["prosjekter"][0]["frister"]["frist"]["uke"]
    assert frist() == 33
    r = client.put("/api/stengte", json=[{"navn": "Test", "fra": 33, "til": 33}])
    assert r.status_code == 200
    assert frist() == 32
    assert client.put("/api/stengte", json=[{"navn": "", "fra": 1, "til": 1}]).status_code == 400
    assert client.put("/api/stengte", json=[{"navn": "X", "fra": 60, "til": 1}]).status_code == 400


def test_bare_lokalt_i_servermodus(client, monkeypatch):
    monkeypatch.setattr(config, "LOKAL", False)
    monkeypatch.setattr(config, "MODUS", "server")
    p = lag(client)
    assert client.get("/api/state").get_json()["modus"] == "server"
    assert client.post(f"{G}/{p['id']}/apne").status_code == 400
    assert client.post("/api/mappe/bom").status_code == 400
    # mal fungerer fortsatt, men åpnes ikke
    assert client.post(f"{G}/{p['id']}/mal").status_code == 200


def test_datafiler_ligger_i_datamappen():
    assert arshjul.store.path.parent == config.DATA_DIR
    assert config.SNAP_FILE.parent.parent == config.DATA_DIR
    assert config.BOM_DIR == config.DATA_DIR / "BOM"


def test_innstillingsfil(tmp_path):
    f = tmp_path / "innstillinger.env"
    f.write_text('\ufeff# kommentar\nARSHJUL_BOM=\\\\server\\deling\\BOM\n\nARSHJUL_PORT = "6000"\nugyldig linje\n',
                 encoding="utf-8")
    assert config.les_innstillinger(f) == {"ARSHJUL_BOM": r"\\server\deling\BOM", "ARSHJUL_PORT": "6000"}
    assert config.les_innstillinger(tmp_path / "finnes-ikke.env") == {}
