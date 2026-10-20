import app as arshjul
import config
from hjelp import lag_bom

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

    r = client.patch(f"{G}/{p['id']}", json={"studenter": 30, "notat": "IELS1001", "lev": 4})
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
    assert client.patch(f"{G}/999", json={"studenter": 1}).status_code == 404


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


def test_gruppestorrelse_styrer_antall_sett(client):
    p = lag(client, start=44, studenter=60, gruppe=3)
    assert p["sett"] == 20
    r = client.patch(f"{G}/{p['id']}", json={"gruppe": 2}).get_json()
    assert (r["studenter"], r["gruppe"], r["sett"]) == (60, 2, 30)
    r = client.patch(f"{G}/{p['id']}", json={"ekstra": 3, "sett": 5}).get_json()  # sett kan ikke settes direkte
    assert r["sett"] == 33
    r = client.patch(f"{G}/{p['id']}", json={"gruppe": 0, "studenter": 9999}).get_json()  # grensene
    assert (r["studenter"], r["gruppe"]) == (2000, 1)
    assert client.patch(f"{G}/{p['id']}", json={"studenter": ""}).get_json()["sett"] is None


def test_emnets_studenter_er_standard_for_nye_prosjekter(client):
    e = client.post("/api/emner", json={"kode": "IELS1001", "studenter": "60"}).get_json()
    assert e["studenter"] == 60
    p = lag(client, start=44, gruppe=3, emne=e["id"])
    assert (p["studenter"], p["sett"], p["emneStudenter"]) == (60, 20, 60)
    spesiell = lag(client, navn="Halv klasse", start=45, studenter=30, emne=e["id"])  # avviker fra emnet
    assert spesiell["studenter"] == 30
    # å endre emnet endrer ikke prosjekter som finnes
    assert client.patch(f"/api/emner/{e['id']}", json={"studenter": 70}).get_json()["studenter"] == 70
    assert client.get("/api/state").get_json()["prosjekter"][0]["studenter"] == 60
    assert client.patch(f"/api/emner/{e['id']}", json={"studenter": ""}).get_json()["studenter"] is None
    assert client.patch(f"/api/emner/{e['id']}", json={"studenter": "mange"}).status_code == 400


def test_prosjekttyper(client):
    p = lag(client, start=40, slutt=45, type="prosjekt")
    assert p["type"] == "prosjekt"
    assert client.patch(f"{G}/{p['id']}", json={"type": "lab"}).get_json()["type"] == "lab"
    assert client.patch(f"{G}/{p['id']}", json={"type": "workshop"}).status_code == 400
    assert client.get("/api/state").get_json()["typer"] == {
        "sett": "Semestersett", "prosjekt": "Prosjekt", "hack": "Hackathon", "lab": "Lab"}


def test_stengt_periode_kan_folge_pasken(client):
    r = client.put("/api/stengte", json=[{"navn": "Påske", "fra": 1, "til": 1, "paske": True}])
    assert r.get_json()[0]["paske"] is True
    st = client.get("/api/state").get_json()
    stengt = {u["uke"]: u["stengt"] for s in st["skolear"] for u in s["uker"] if s["ar"] == 2027}
    assert stengt[15] == "Påske" and stengt[1] == ""  # påsken 2028 er i uke 15, ikke uke 1


def test_prosjekt_uten_bom_kan_markeres_som_bestilt(client):
    p = lag(client, start=44)
    r = client.post(f"{G}/{p['id']}/bestilt", json={"bestilt": True}).get_json()
    assert r["bestiltUtenBom"] and r["bestiltUtenBomAv"]
    assert client.get("/api/state").get_json()["prosjekter"][0]["bestiltUtenBom"] == r["bestiltUtenBom"]
    assert client.post(f"{G}/{p['id']}/bestilt", json={"bestilt": False}).get_json()["bestiltUtenBom"] is None
    felt = [e["felt"] for e in client.get("/api/admin/logg").get_json()]
    assert felt.count("bestilt (uten BOM)") == 2

    client.patch(f"{G}/{p['id']}", json={"bom": lag_bom().name})  # med BOM: registreres per leverandør
    assert client.post(f"{G}/{p['id']}/bestilt", json={"bestilt": True}).status_code == 400


def test_navn_og_notat_kan_endres(client):
    p = lag(client, start=44)
    r = client.patch(f"{G}/{p['id']}", json={"navn": " Nytt navn ", "notat": "IELS1001"}).get_json()
    assert (r["navn"], r["notat"]) == ("Nytt navn", "IELS1001")
    assert client.patch(f"{G}/{p['id']}", json={"navn": "  "}).status_code == 400
