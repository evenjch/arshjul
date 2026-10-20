import datetime as dt
import json

import config
import deadlines
from db import DEFAULT_CLOSED, MIGRATIONS, Db, set_count


def test_new_database_has_default_closed_periods():
    assert Db(config.DB_FILE).closed_periods() == DEFAULT_CLOSED


def test_import_from_arshjul_json(tmp_path):
    old_file = tmp_path / "arshjul.json"
    old_file.write_text(json.dumps({
        "stengte_perioder": [{"navn": "Jul", "fra": 51, "til": 2}],
        "prosjekter": [
            {"id": "kit", "navn": "Arduino starter kit", "type": "sett", "semester": "Høst", "start": 34, "slutt": 50,
             "lev": 2, "buf": 3, "sett": 60, "notat": "Beholdes", "color": 0, "bom": "kit.xlsx"},
            {"id": "led", "navn": "LED-list", "type": "hack", "start": 46, "slutt": 46, "lev": 2, "buf": 2,
             "sett": None, "notat": "", "color": 2, "bom": None},
        ],
    }), encoding="utf-8")
    db = Db(tmp_path / "arshjul.db")
    db.init(old_file)

    rows = db.runs()
    assert [(g["projectId"], g["start"], g["finish"], g["sets"]) for g in rows] == [("kit", 34, 50, 60), ("led", 46, 46, None)]
    assert {g["school_year"] for g in rows} == {deadlines.school_year_for(dt.date.today())}
    assert rows[0]["bom"] == "kit.xlsx" and rows[0]["note"] == "Beholdes"
    assert db.closed_periods() == [{"name": "Jul", "from_week": 51, "to_week": 2, "easter": False}]
    # the old file is kept, but no longer used
    assert not old_file.exists() and (tmp_path / "arshjul.json.importert").exists()

    db.init(old_file)  # andre oppstart: ingenting skjer
    assert len(db.runs()) == 2


def test_version(tmp_path):
    db = Db(tmp_path / "x.db")
    db.init()
    with db.tx() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_project_fields_apply_to_every_year():
    db = Db(config.DB_FILE)
    a = db.new("p1", 2026, {"name": "Test", "start": 40, "finish": 40, "lead": 2})
    with db.tx() as con:
        con.execute("INSERT INTO runs (project_id, school_year, start, finish) VALUES ('p1', 2027, 41, 41)")
    db.update(a["id"], {"name": "Nytt navn", "start": 42})
    rows = {g["school_year"]: g for g in db.runs()}
    assert rows[2026]["name"] == rows[2027]["name"] == "Nytt navn"
    assert (rows[2026]["start"], rows[2027]["start"]) == (42, 41)


def test_bom_and_lead_time_never_change_earlier_years():
    """A new BOM version next year mustn't change history – but carries on to later years."""
    db = Db(config.DB_FILE)
    a = db.new("p1", 2026, {"name": "Test", "bom": "v1.xlsx", "lead": 2})
    db.copy_year(2026)
    b = next(g for g in db.runs() if g["school_year"] == 2027)
    assert (b["bom"], b["lead"]) == ("v1.xlsx", 2)  # the copy brings BOM and lead time along

    db.update(b["id"], {"bom": "v2.xlsx", "lead": 4})  # next year: a new BOM
    rows = {g["school_year"]: g for g in db.runs()}
    assert (rows[2026]["bom"], rows[2026]["lead"]) == ("v1.xlsx", 2)  # this year stays
    assert (rows[2027]["bom"], rows[2027]["lead"]) == ("v2.xlsx", 4)

    db.update(a["id"], {"lead": 3})  # this year: 2027 has its own lead time and isn't touched
    db.update(a["id"], {"bom": "v1b.xlsx"})
    rows = {g["school_year"]: g for g in db.runs()}
    assert (rows[2027]["bom"], rows[2027]["lead"]) == ("v2.xlsx", 4)

    c = db.new("p2", 2026, {"name": "Annet", "lead": 2})
    db.copy_year(2026, [c["id"]])
    db.update(c["id"], {"lead": 6})  # 2027 had the same value: follows along
    assert [g["lead"] for g in db.runs() if g["projectId"] == "p2"] == [6, 6]


def test_migration_14_bom_per_run(tmp_path):
    db = Db(tmp_path / "gammel.db")
    with db.tx() as con:
        for sql in MIGRATIONS[:13]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 13")
        con.execute("INSERT INTO prosjekt (id, navn, lev, buf, bom) VALUES ('kit', 'Kit', 3, 4, 'kit.xlsx')")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt) VALUES ('kit', 2025, 34, 50)")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt) VALUES ('kit', 2026, 34, 50)")
    db.init()
    assert [(g["bom"], g["lead"], g["buf"]) for g in db.runs()] == [("kit.xlsx", 3, 4)] * 2


def test_deleting_the_last_run_deletes_the_project():
    db = Db(config.DB_FILE)
    a = db.new("p1", 2026, {"name": "Test"})
    db.delete(a["id"])
    with db.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_set_count():
    assert set_count(60, 3, 0) == 20
    assert set_count(61, 3, 0) == 21  # a group of two is a set too
    assert set_count(60, 3, 2) == 22
    assert set_count(60, None, None) == 60
    assert set_count(None, 3, 2) is None


def test_sets_are_computed_and_copied():
    db = Db(config.DB_FILE)
    a = db.new("p1", 2026, {"name": "Test", "students": 60, "group_size": 3, "spare": 2})
    assert (a["students"], a["group_size"], a["spare"], a["sets"]) == (60, 3, 2, 22)
    assert db.update(a["id"], {"group_size": 2})["sets"] == 32
    assert db.update(a["id"], {"students": None})["sets"] is None
    db.update(a["id"], {"students": 60})
    db.copy_year(2026)
    next_ = [g for g in db.runs() if g["school_year"] == 2027]
    assert [(g["students"], g["group_size"], g["spare"], g["sets"]) for g in next_] == [(60, 2, 2, 32)]


def test_migration_keeps_the_set_count(tmp_path):
    """Runs from before migration 11 get students = sets and groups of 1."""
    db = Db(tmp_path / "gammel.db")
    with db.tx() as con:
        for sql in MIGRATIONS[:10]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 10")
        con.execute("INSERT INTO prosjekt (id, navn) VALUES ('kit', 'Kit')")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES ('kit', 2026, 34, 50, 30)")
        con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES ('kit', 2027, 34, 50, NULL)")
    db.init()
    assert [(g["students"], g["group_size"], g["spare"], g["sets"]) for g in db.runs()] == [
        (30, 1, 0, 30), (None, 1, 0, None)]


def test_migration_13_easter_follows_easter_sunday(tmp_path):
    db = Db(tmp_path / "gammel.db")
    with db.tx() as con:
        for sql in MIGRATIONS[:12]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 12")
        con.executemany("INSERT INTO stengt_periode (navn, fra, til) VALUES (?,?,?)",
                        [("Juleferie", 52, 1), ("Påske", 12, 12), ("Høstferie", 40, 40)])
    db.init()
    assert [(c["name"], c["easter"]) for c in db.closed_periods()] == [("Juleferie", False), ("Påske", True), ("Høstferie", False)]


def test_backup_before_upgrade(tmp_path):
    """A database that's about to be upgraded is copied first – a new or up-to-date database isn't."""
    copy_year = tmp_path / "Sikkerhetskopi-db"
    Db(tmp_path / "ny.db").init()
    assert not copy_year.exists()  # new database: nothing to keep

    db = Db(tmp_path / "gammel.db")
    with db.tx() as con:
        for sql in MIGRATIONS[:14]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 14")
        con.execute("INSERT INTO prosjekt (id, navn) VALUES ('kit', 'Kit')")
    db.init()
    files = list(copy_year.glob("arshjul *.db"))
    assert len(files) == 1 and Db(files[0]).version() == 14  # the copy is as it was before
    db.init()  # up to date: no new copy
    assert len(list(copy_year.glob("arshjul *.db"))) == 1


def test_migration_16_keeps_everything(tmp_path):
    """A database at version 15 with realistic data: after migration 16 everything is there under English
    names, and the course keeps its lecturer and engineer (the users table is rebuilt)."""
    db = Db(tmp_path / "old.db")
    with db.tx() as con:
        for sql in MIGRATIONS[:15]:
            for stmt in sql.split(";"):
                if stmt.strip():
                    con.execute(stmt)
        con.execute("PRAGMA user_version = 15")
        con.execute("""INSERT INTO bruker (id, brukernavn, navn, rolle, passord, opprettet, admin)
                       VALUES (1, 'ola', 'Ola', 'faglarer', 'h1', 'x', 0), (2, 'ing', 'Ing', 'ingenior', 'h2', 'x', 1)""")
        con.execute("INSERT INTO emne (id, kode, faglarer_id, ingenior_id, studenter) VALUES (1, 'E1', 1, 2, 60)")
        con.execute("INSERT INTO prosjekt (id, navn, type, notat, emne_id) VALUES ('p', 'Kit', 'sett', 'IELS1001', 1)")
        con.execute("""INSERT INTO gjennomforing (id, prosjekt_id, skolear, start, slutt, sett, studenter, gruppe, ekstra,
                                                  bom, lev, buf) VALUES (5, 'p', 2026, 34, 50, 22, 60, 3, 2, 'kit.xlsx', 3, 2)""")
        con.execute("""INSERT INTO bestilling (gjennomforing_id, leverandor, navn, status, bestilt_dato, linjer, av, paa_lager,
                                               lager_logg) VALUES (5, 'elfa', 'Elfa', 'delvis', '2026-06-01', ?, 'Ing', ?, ?)""",
                    (json.dumps([{"key": "elfa|a1", "beskrivelse": "Arduino", "varenummer": "A1", "antall": 10, "pris": 250}]),
                     json.dumps({"elfa|a1": 4}),
                     json.dumps([{"tid": "t", "handling": "mottatt", "av": "Ing", "linjer": {"elfa|a1": 4}, "backup": "b.xlsx"}])))
        con.execute("""INSERT INTO telling (gjennomforing_id, dato, linjer, skrevet, skrevet_verdier, logg)
                       VALUES (5, '2026-12-01', ?, '2026-12-02', ?, ?)""",
                    (json.dumps([{"key": "elfa|a1", "beskrivelse": "Arduino", "varenummer": "A1", "type": "forbruk",
                                  "lagerFor": 0, "bestilt": 10, "antatt": True, "utdelt": 20, "pris": 250, "telt": 3,
                                  "forventet": 0}]),
                     json.dumps({"elfa|a1": 3}),
                     json.dumps([{"tid": "t", "handling": "skrevet", "av": "Ing", "linjer": 1, "konflikter": 0,
                                  "backup": "k.xlsx"}])))
        con.execute("INSERT INTO linjetype VALUES ('p', 'elfa|a1', 'utstyr')")
        con.execute("""INSERT INTO endring (tid, bruker_navn, rolle, gjennomforing_id, prosjekt_navn, handling, felt, fra, til)
                       VALUES ('t', 'Ola', 'faglarer', 5, 'Kit', 'endret', 'type', 'hack', 'sett')""")
        con.execute("DELETE FROM stengt_periode")
        con.execute("INSERT INTO stengt_periode (navn, fra, til, paske) VALUES ('Påske', 12, 12, 1)")
    db.init()

    assert db.version() == len(MIGRATIONS)
    c = db.courses()[0]
    assert (c["code"], c["students"], c["lecturerName"], c["engineerName"], c["projects"]) == ("E1", 60, "Ola", "Ing", 1)
    r = db.run("5")
    assert (r["type"], r["note"], r["sets"], r["students"], r["group_size"], r["spare"], r["bom"], r["lead"]) == (
        "semester_kit", "IELS1001", 22, 60, 3, 2, "kit.xlsx", 3)
    o = db.all_orders()["5"]["elfa"]
    assert (o["status"], o["made_by"], o["stocked"]) == ("partial", "Ing", {"elfa|a1": 4})
    assert o["lines"] == [{"key": "elfa|a1", "description": "Arduino", "part_number": "A1", "quantity": 10, "price": 250}]
    assert o["stock_log"] == [{"time": "t", "action": "received", "by": "Ing", "lines": {"elfa|a1": 4}, "backup": "b.xlsx"}]
    cnt = db.counts()["5"]
    assert cnt["lines"][0] == {"key": "elfa|a1", "description": "Arduino", "part_number": "A1", "type": "consumable",
                               "stockBefore": 0, "ordered": 10, "assumed": True, "handed_out": 20, "price": 250,
                               "counted": 3, "expected": 0}
    assert cnt["log"][0]["action"] == "written" and cnt["written_values"] == {"elfa|a1": 3}
    assert db.line_types() == {"p": {"elfa|a1": "equipment"}}
    ch = db.changes()[0]
    assert (ch["role"], ch["action"], ch["from_value"], ch["to_value"]) == ("lecturer", "changed", "hackathon", "semester_kit")
    assert db.closed_periods() == [{"name": "Påske", "from_week": 12, "to_week": 12, "easter": True}]
    assert [(u["username"], u["role"], u["admin"]) for u in db.users()] == [("ing", "engineer", 1), ("ola", "lecturer", 0)]
    assert db.user(username="ola")["password"] == "h1"
    with db.tx() as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_old_bom_snapshot_is_converted(monkeypatch, tmp_path):
    import config as cfg
    old, new = tmp_path / "bom-sist-sett.json", tmp_path / "bom-last-seen.json"
    monkeypatch.setattr(cfg, "OLD_SNAP_FILE", old)
    monkeypatch.setattr(cfg, "SNAP_FILE", new)
    old.write_text(json.dumps({"kit.xlsx": {"rader": [{"beskrivelse": "Arduino", "leverandor": "Elfa", "varenummer": "A1",
                                                        "prSett": 1, "lager": 4, "pris": 250, "kategori": "", "kommentar": ""}],
                                            "lest": "01.06.2026 10:00"}}), encoding="utf-8")
    cfg.convert_old_snapshot()
    s = json.loads(new.read_text(encoding="utf-8"))["kit.xlsx"]
    assert s["read"] == "01.06.2026 10:00" and s["rows"][0]["in_stock"] == 4 and s["rows"][0]["part_number"] == "A1"
    assert not old.exists()
