import datetime as dt
import json

import config
import frister
from db import DEFAULT_STENGT, MIGRERINGER, Db, antall_sett


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
    assert db.stengte() == [{"navn": "Jul", "fra": 51, "til": 2, "paske": False}]
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
    db.endre(a["id"], {"navn": "Nytt navn", "start": 42})
    rader = {g["skolear"]: g for g in db.gjennomforinger()}
    assert rader[2026]["navn"] == rader[2027]["navn"] == "Nytt navn"
    assert (rader[2026]["start"], rader[2027]["start"]) == (42, 41)


def test_bom_og_leveringstid_endrer_aldri_tidligere_ar():
    """En ny BOM-versjon neste år skal ikke endre historikken – men følger med til senere år."""
    db = Db(config.DB_FILE)
    a = db.ny("p1", 2026, {"navn": "Test", "bom": "v1.xlsx", "lev": 2})
    db.kopier(2026)
    b = next(g for g in db.gjennomforinger() if g["skolear"] == 2027)
    assert (b["bom"], b["lev"]) == ("v1.xlsx", 2)  # kopien tar med BOM og leveringstid

    db.endre(b["id"], {"bom": "v2.xlsx", "lev": 4})  # neste år: ny BOM
    rader = {g["skolear"]: g for g in db.gjennomforinger()}
    assert (rader[2026]["bom"], rader[2026]["lev"]) == ("v1.xlsx", 2)  # i år står
    assert (rader[2027]["bom"], rader[2027]["lev"]) == ("v2.xlsx", 4)

    db.endre(a["id"], {"lev": 3})  # i år: 2027 har sin egen leveringstid og røres ikke
    db.endre(a["id"], {"bom": "v1b.xlsx"})
    rader = {g["skolear"]: g for g in db.gjennomforinger()}
    assert (rader[2027]["bom"], rader[2027]["lev"]) == ("v2.xlsx", 4)

    c = db.ny("p2", 2026, {"navn": "Annet", "lev": 2})
    db.kopier(2026, [c["id"]])
    db.endre(c["id"], {"lev": 6})  # 2027 hadde samme verdi: følger med
    assert [g["lev"] for g in db.gjennomforinger() if g["prosjektId"] == "p2"] == [6, 6]


def test_migrering_14_bom_per_gjennomforing(tmp_path):
    db = Db(tmp_path / "gammel.db")
    with db.tx() as con:
        for sql in MIGRERINGER[:13]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 13")
        con.execute("INSERT INTO prosjekt (id, navn, lev, buf, bom) VALUES ('kit', 'Kit', 3, 4, 'kit.xlsx')")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt) VALUES ('kit', 2025, 34, 50)")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt) VALUES ('kit', 2026, 34, 50)")
    db.init()
    assert [(g["bom"], g["lev"], g["buf"]) for g in db.gjennomforinger()] == [("kit.xlsx", 3, 4)] * 2


def test_slett_siste_gjennomforing_sletter_prosjektet():
    db = Db(config.DB_FILE)
    a = db.ny("p1", 2026, {"navn": "Test"})
    db.slett(a["id"])
    with db.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM prosjekt").fetchone()[0] == 0


def test_antall_sett():
    assert antall_sett(60, 3, 0) == 20
    assert antall_sett(61, 3, 0) == 21  # en gruppe med to er også et sett
    assert antall_sett(60, 3, 2) == 22
    assert antall_sett(60, None, None) == 60
    assert antall_sett(None, 3, 2) is None


def test_sett_regnes_ut_og_kopieres():
    db = Db(config.DB_FILE)
    a = db.ny("p1", 2026, {"navn": "Test", "studenter": 60, "gruppe": 3, "ekstra": 2})
    assert (a["studenter"], a["gruppe"], a["ekstra"], a["sett"]) == (60, 3, 2, 22)
    assert db.endre(a["id"], {"gruppe": 2})["sett"] == 32
    assert db.endre(a["id"], {"studenter": None})["sett"] is None
    db.endre(a["id"], {"studenter": 60})
    db.kopier(2026)
    neste = [g for g in db.gjennomforinger() if g["skolear"] == 2027]
    assert [(g["studenter"], g["gruppe"], g["ekstra"], g["sett"]) for g in neste] == [(60, 2, 2, 32)]


def test_migrering_beholder_antall_sett(tmp_path):
    """Gjennomføringer fra før migrering 11 får studenter = sett og grupper på 1."""
    db = Db(tmp_path / "gammel.db")
    with db.tx() as con:
        for sql in MIGRERINGER[:10]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 10")
        con.execute("INSERT INTO prosjekt (id, navn) VALUES ('kit', 'Kit')")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES ('kit', 2026, 34, 50, 30)")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES ('kit', 2027, 34, 50, NULL)")
    db.init()
    assert [(g["studenter"], g["gruppe"], g["ekstra"], g["sett"]) for g in db.gjennomforinger()] == [
        (30, 1, 0, 30), (None, 1, 0, None)]


def test_migrering_13_paske_folger_paskedagen(tmp_path):
    db = Db(tmp_path / "gammel.db")
    with db.tx() as con:
        for sql in MIGRERINGER[:12]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 12")
        con.executemany("INSERT INTO stengt_periode (navn, fra, til) VALUES (?,?,?)",
                        [("Juleferie", 52, 1), ("Påske", 12, 12), ("Høstferie", 40, 40)])
    db.init()
    assert [(c["navn"], c["paske"]) for c in db.stengte()] == [("Juleferie", False), ("Påske", True), ("Høstferie", False)]
