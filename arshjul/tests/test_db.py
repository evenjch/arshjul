import datetime as dt
import json

import config
import frister
from db import DEFAULT_STENGT, MIGRERINGER, Db


def test_ny_database_har_standard_stengte_perioder():
    assert Db(config.DB_FILE).stengte() == DEFAULT_STENGT


def test_import_fra_arshjul_json(tmp_path):
    gammel = tmp_path / "arshjul.json"
    gammel.write_text(json.dumps({
        "stengte_perioder": [{"navn": "Jul", "fra": 51, "til": 2}],
        "prosjekter": [
            {"id": "kit", "navn": "Arduino starter kit", "type": "sett", "semester": "Høst", "start": 34, "slutt": 50,
             "lev": 2, "buf": 3, "sett": 60, "notat": "Beholdes", "color": 0, "bom": "kit.xlsx"},
            {"id": "led", "navn": "LED-list", "type": "hack", "start": 46, "slutt": 46, "lev": 2, "buf": 2,
             "sett": None, "notat": "", "color": 2, "bom": None},
        ],
    }), encoding="utf-8")
    db = Db(tmp_path / "arshjul.db")
    db.init(gammel)

    rader = db.gjennomforinger()
    assert [(g["prosjektId"], g["start"], g["slutt"], g["sett"]) for g in rader] == [("kit", 34, 50, 60), ("led", 46, 46, None)]
    assert {g["skolear"] for g in rader} == {frister.skolear_for(dt.date.today())}
    assert rader[0]["bom"] == "kit.xlsx" and rader[0]["notat"] == "Beholdes"
    assert db.stengte() == [{"navn": "Jul", "fra": 51, "til": 2}]
    # gammel fil er tatt vare på, men brukes ikke lenger
    assert not gammel.exists() and (tmp_path / "arshjul.json.importert").exists()

    db.init(gammel)  # andre oppstart: ingenting skjer
    assert len(db.gjennomforinger()) == 2


def test_versjon(tmp_path):
    db = Db(tmp_path / "x.db")
    db.init()
    with db.tx() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRERINGER)


def test_prosjektfelt_gjelder_alle_ar():
    db = Db(config.DB_FILE)
    a = db.ny("p1", 2026, {"navn": "Test", "start": 40, "slutt": 40, "lev": 2})
    with db.tx() as con:
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt) VALUES ('p1', 2027, 41, 41)")
    db.endre(a["id"], {"lev": 5, "start": 42})
    rader = {g["skolear"]: g for g in db.gjennomforinger()}
    assert rader[2026]["lev"] == rader[2027]["lev"] == 5
    assert (rader[2026]["start"], rader[2027]["start"]) == (42, 41)


def test_slett_siste_gjennomforing_sletter_prosjektet():
    db = Db(config.DB_FILE)
    a = db.ny("p1", 2026, {"navn": "Test"})
    db.slett(a["id"])
    with db.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM prosjekt").fetchone()[0] == 0
