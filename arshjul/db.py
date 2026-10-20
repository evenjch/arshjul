"""The SQLite database in the data folder: projects, runs, courses, users, orders, counts and closed periods.

Project   what comes back every year: name, type, course, colour
Run       one project in one school year: weeks, students, group size, sets, BOM, lead time and buffer
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import deadlines

DEFAULT_CLOSED = [
    {"name": "Juleferie", "from_week": 52, "to_week": 1, "easter": False},
    {"name": "Påske", "from_week": 12, "to_week": 12, "easter": True},  # follows Easter; the weeks are not used
    {"name": "Fellesferie", "from_week": 28, "to_week": 31, "easter": False},
]

# Each migration runs once, in order. Add new ones at the end – never change an old one (it has run on
# someone's database). Statements are split on ";", so no semicolons inside a statement.
# Migrations 1–15 are from before the code was in English and use the old Norwegian names on purpose.
MIGRATIONS = [
    """
    CREATE TABLE prosjekt (
        id     TEXT PRIMARY KEY,
        navn   TEXT NOT NULL,
        type   TEXT NOT NULL DEFAULT 'hack',
        lev    INTEGER NOT NULL DEFAULT 2,
        buf    INTEGER NOT NULL DEFAULT 2,
        notat  TEXT NOT NULL DEFAULT '',
        color  INTEGER NOT NULL DEFAULT 0,
        bom    TEXT
    );
    CREATE TABLE gjennomforing (
        id          INTEGER PRIMARY KEY,
        prosjekt_id TEXT NOT NULL REFERENCES prosjekt(id) ON DELETE CASCADE,
        skolear     INTEGER NOT NULL,
        start       INTEGER NOT NULL,
        slutt       INTEGER NOT NULL,
        sett        INTEGER
    );
    CREATE INDEX gjennomforing_skolear ON gjennomforing(skolear);
    CREATE TABLE stengt_periode (
        id   INTEGER PRIMARY KEY,
        navn TEXT NOT NULL,
        fra  INTEGER NOT NULL,
        til  INTEGER NOT NULL
    );
    """,
    # 2: orders per supplier, with a frozen copy of the lines that were ordered (JSON)
    """
    CREATE TABLE bestilling (
        id               INTEGER PRIMARY KEY,
        gjennomforing_id INTEGER NOT NULL REFERENCES gjennomforing(id) ON DELETE CASCADE,
        leverandor       TEXT NOT NULL,
        navn             TEXT NOT NULL,
        status           TEXT NOT NULL,
        bestilt_dato     TEXT NOT NULL,
        mottatt_dato     TEXT,
        ordrenr          TEXT NOT NULL DEFAULT '',
        av               TEXT,
        sett             INTEGER,
        linjer           TEXT NOT NULL,
        UNIQUE (gjennomforing_id, leverandor)
    );
    """,
    # 3: counting after the project, and whether a line is equipment or consumable (remembered per project)
    """
    CREATE TABLE telling (
        gjennomforing_id INTEGER PRIMARY KEY REFERENCES gjennomforing(id) ON DELETE CASCADE,
        dato             TEXT NOT NULL,
        linjer           TEXT NOT NULL,
        skrevet          TEXT,
        backup           TEXT
    );
    CREATE TABLE linjetype (
        prosjekt_id TEXT NOT NULL REFERENCES prosjekt(id) ON DELETE CASCADE,
        key         TEXT NOT NULL,
        type        TEXT NOT NULL,
        PRIMARY KEY (prosjekt_id, key)
    );
    """,
    # 4: what was last written to the BOM per line (for corrections and undo), and a log
    """
    ALTER TABLE telling ADD COLUMN skrevet_verdier TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE telling ADD COLUMN logg TEXT NOT NULL DEFAULT '[]';
    UPDATE telling SET
        skrevet_verdier = (SELECT json_group_object(json_extract(value, '$.key'), json_extract(value, '$.telt'))
                           FROM json_each(telling.linjer)),
        logg = json_array(json_object('tid', skrevet, 'handling', 'skrevet',
                                      'linjer', json_array_length(linjer), 'backup', backup))
    WHERE skrevet IS NOT NULL;
    """,
    # 5: receipts – how much of each line has been added to «På lager» in the BOM, and a log
    """
    ALTER TABLE bestilling ADD COLUMN paa_lager TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE bestilling ADD COLUMN lager_logg TEXT NOT NULL DEFAULT '[]';
    """,
    # 6: the line key is now «supplier|part number» (part numbers are only unique per supplier).
    #    Orders know their supplier, so they're converted exactly. Counts and
    #    equipment/consumable didn't store the supplier and can't be converted safely; they're deleted.
    #    (When this was introduced there was only test data, and the engineer approved it.)
    """
    UPDATE bestilling SET
        linjer = (SELECT json_group_array(json_set(value, '$.key', bestilling.leverandor || '|' || json_extract(value, '$.key')))
                  FROM json_each(bestilling.linjer)),
        paa_lager = COALESCE((SELECT json_group_object(bestilling.leverandor || '|' || key, value)
                              FROM json_each(bestilling.paa_lager)), '{}');
    DELETE FROM telling;
    DELETE FROM linjetype;
    """,
    # 7: users and roles, lecturer per project, soft delete and the change log
    """
    CREATE TABLE bruker (
        id         INTEGER PRIMARY KEY,
        brukernavn TEXT NOT NULL UNIQUE COLLATE NOCASE,
        navn       TEXT NOT NULL,
        rolle      TEXT NOT NULL CHECK (rolle IN ('ingenior', 'faglarer')),
        passord    TEXT NOT NULL,
        aktiv      INTEGER NOT NULL DEFAULT 1,
        feilforsok INTEGER NOT NULL DEFAULT 0,
        last_til   TEXT,
        sist_inne  TEXT,
        opprettet  TEXT NOT NULL
    );
    ALTER TABLE prosjekt ADD COLUMN faglarer_id INTEGER REFERENCES bruker(id) ON DELETE SET NULL;
    ALTER TABLE gjennomforing ADD COLUMN slettet TEXT;
    ALTER TABLE gjennomforing ADD COLUMN slettet_av TEXT;
    CREATE TABLE endring (
        id               INTEGER PRIMARY KEY,
        tid              TEXT NOT NULL,
        bruker_id        INTEGER,
        bruker_navn      TEXT NOT NULL,
        rolle            TEXT NOT NULL,
        gjennomforing_id INTEGER,
        prosjekt_navn    TEXT NOT NULL,
        skolear          INTEGER,
        handling         TEXT NOT NULL,
        felt             TEXT,
        fra              TEXT,
        til              TEXT,
        kvittert         TEXT
    );
    CREATE INDEX endring_ukvittert ON endring(kvittert, gjennomforing_id);
    """,
    # 8: courses. A project belongs to a course; the course has one lecturer and one responsible engineer.
    #    prosjekt.faglarer_id is no longer used (can't be dropped – it has a foreign key);
    #    existing projects don't get a course yet and are shown to every engineer until they get one.
    """
    CREATE TABLE emne (
        id          INTEGER PRIMARY KEY,
        kode        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        navn        TEXT NOT NULL DEFAULT '',
        program     TEXT NOT NULL DEFAULT 'bachelor' CHECK (program IN ('bachelor', 'master')),
        faglarer_id INTEGER REFERENCES bruker(id) ON DELETE SET NULL,
        ingenior_id INTEGER REFERENCES bruker(id) ON DELETE SET NULL,
        aktiv       INTEGER NOT NULL DEFAULT 1
    );
    ALTER TABLE prosjekt ADD COLUMN emne_id INTEGER REFERENCES emne(id) ON DELETE SET NULL;
    UPDATE prosjekt SET faglarer_id = NULL;
    """,
    # 9: admin – an engineer who has every course and manages the users
    """
    ALTER TABLE bruker ADD COLUMN admin INTEGER NOT NULL DEFAULT 0;
    """,
    # 10: notifications about courses (an engineer chosen or taken off) – go to one particular recipient
    """
    ALTER TABLE endring ADD COLUMN emne_id INTEGER;
    ALTER TABLE endring ADD COLUMN mottaker_id INTEGER;
    """,
    # 11: the set count is computed from students, group size and spare sets (sets is still stored).
    #     Existing runs get students = sets and groups of 1, so no numbers change.
    """
    ALTER TABLE gjennomforing ADD COLUMN studenter INTEGER;
    ALTER TABLE gjennomforing ADD COLUMN gruppe INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE gjennomforing ADD COLUMN ekstra INTEGER NOT NULL DEFAULT 0;
    UPDATE gjennomforing SET studenter = sett;
    """,
    # 12: the number of students in the course – the default for new projects in the course (a project may differ)
    """
    ALTER TABLE emne ADD COLUMN studenter INTEGER;
    """,
    # 13: a closed period can follow Easter (the week with Easter Sunday), which moves from year to year
    """
    ALTER TABLE stengt_periode ADD COLUMN paske INTEGER NOT NULL DEFAULT 0;
    UPDATE stengt_periode SET paske = 1 WHERE navn = 'Påske'
    """,
    # 14: BOM, lead time and buffer belong to the run, not the project – so a new BOM version
    #     next year doesn't change history. The columns in prosjekt are no longer used (migrations never change).
    """
    ALTER TABLE gjennomforing ADD COLUMN bom TEXT;
    ALTER TABLE gjennomforing ADD COLUMN lev INTEGER NOT NULL DEFAULT 2;
    ALTER TABLE gjennomforing ADD COLUMN buf INTEGER NOT NULL DEFAULT 2;
    UPDATE gjennomforing SET
        bom = (SELECT p.bom FROM prosjekt p WHERE p.id = gjennomforing.prosjekt_id),
        lev = (SELECT p.lev FROM prosjekt p WHERE p.id = gjennomforing.prosjekt_id),
        buf = (SELECT p.buf FROM prosjekt p WHERE p.id = gjennomforing.prosjekt_id)
    """,
    # 15: projects without a BOM can be marked as ordered (date and by whom) – otherwise they show «frist passert»
    """
    ALTER TABLE gjennomforing ADD COLUMN bestilt_uten_bom TEXT;
    ALTER TABLE gjennomforing ADD COLUMN bestilt_uten_bom_av TEXT
    """,
    # 16: everything in English – tables, columns, codes and the keys inside stored JSON. Runs with
    #     foreign keys off (see init): users and projects are rebuilt, and dropping the old table with
    #     foreign keys on would set every course's lecturer/engineer to NULL.
    """
    ALTER TABLE prosjekt RENAME TO projects;
    ALTER TABLE gjennomforing RENAME TO runs;
    ALTER TABLE bestilling RENAME TO orders;
    ALTER TABLE telling RENAME TO counts;
    ALTER TABLE linjetype RENAME TO line_types;
    ALTER TABLE stengt_periode RENAME TO closed_periods;
    ALTER TABLE endring RENAME TO change_log;
    ALTER TABLE emne RENAME TO courses;
    ALTER TABLE bruker RENAME TO users;

    ALTER TABLE runs RENAME COLUMN prosjekt_id TO project_id;
    ALTER TABLE runs RENAME COLUMN skolear TO school_year;
    ALTER TABLE runs RENAME COLUMN slutt TO finish;
    ALTER TABLE runs RENAME COLUMN sett TO sets;
    ALTER TABLE runs RENAME COLUMN slettet TO deleted;
    ALTER TABLE runs RENAME COLUMN slettet_av TO deleted_by;
    ALTER TABLE runs RENAME COLUMN studenter TO students;
    ALTER TABLE runs RENAME COLUMN gruppe TO group_size;
    ALTER TABLE runs RENAME COLUMN ekstra TO spare;
    ALTER TABLE runs RENAME COLUMN lev TO lead;
    ALTER TABLE runs RENAME COLUMN bestilt_uten_bom TO ordered_without_bom;
    ALTER TABLE runs RENAME COLUMN bestilt_uten_bom_av TO ordered_without_bom_by;
    DROP INDEX gjennomforing_skolear;
    CREATE INDEX runs_school_year ON runs(school_year);

    ALTER TABLE orders RENAME COLUMN gjennomforing_id TO run_id;
    ALTER TABLE orders RENAME COLUMN leverandor TO supplier;
    ALTER TABLE orders RENAME COLUMN navn TO name;
    ALTER TABLE orders RENAME COLUMN bestilt_dato TO ordered_date;
    ALTER TABLE orders RENAME COLUMN mottatt_dato TO received_date;
    ALTER TABLE orders RENAME COLUMN ordrenr TO order_no;
    ALTER TABLE orders RENAME COLUMN av TO made_by;
    ALTER TABLE orders RENAME COLUMN sett TO sets;
    ALTER TABLE orders RENAME COLUMN linjer TO lines;
    ALTER TABLE orders RENAME COLUMN paa_lager TO stocked;
    ALTER TABLE orders RENAME COLUMN lager_logg TO stock_log;
    UPDATE orders SET status = CASE status WHEN 'bestilt' THEN 'ordered' WHEN 'delvis' THEN 'partial'
                                           WHEN 'mottatt' THEN 'received' ELSE status END;
    UPDATE orders SET
        lines = (SELECT json_group_array(json_object(
                    'key', json_extract(value, '$.key'), 'description', json_extract(value, '$.beskrivelse'),
                    'part_number', json_extract(value, '$.varenummer'), 'quantity', json_extract(value, '$.antall'),
                    'price', json_extract(value, '$.pris')))
                 FROM json_each(orders.lines)),
        stock_log = (SELECT json_group_array(json_object(
                    'time', json_extract(value, '$.tid'),
                    'action', CASE json_extract(value, '$.handling') WHEN 'mottatt' THEN 'received'
                                   WHEN 'angret' THEN 'undone' ELSE json_extract(value, '$.handling') END,
                    'by', json_extract(value, '$.av'), 'lines', json(json_extract(value, '$.linjer')),
                    'backup', json_extract(value, '$.backup')))
                 FROM json_each(orders.stock_log));

    ALTER TABLE counts RENAME COLUMN gjennomforing_id TO run_id;
    ALTER TABLE counts RENAME COLUMN dato TO date;
    ALTER TABLE counts RENAME COLUMN linjer TO lines;
    ALTER TABLE counts RENAME COLUMN skrevet TO written;
    ALTER TABLE counts RENAME COLUMN skrevet_verdier TO written_values;
    ALTER TABLE counts RENAME COLUMN logg TO log;
    UPDATE counts SET
        lines = (SELECT json_group_array(json_object(
                    'key', json_extract(value, '$.key'), 'description', json_extract(value, '$.beskrivelse'),
                    'part_number', json_extract(value, '$.varenummer'),
                    'type', CASE json_extract(value, '$.type') WHEN 'utstyr' THEN 'equipment'
                                 WHEN 'forbruk' THEN 'consumable' ELSE json_extract(value, '$.type') END,
                    'stockBefore', json_extract(value, '$.lagerFor'), 'ordered', json_extract(value, '$.bestilt'),
                    'assumed', json(CASE json_extract(value, '$.antatt') WHEN 1 THEN 'true' ELSE 'false' END),
                    'handed_out', json_extract(value, '$.utdelt'), 'price', json_extract(value, '$.pris'),
                    'counted', json_extract(value, '$.telt'), 'expected', json_extract(value, '$.forventet')))
                 FROM json_each(counts.lines)),
        log = (SELECT json_group_array(json_object(
                    'time', json_extract(value, '$.tid'),
                    'action', CASE json_extract(value, '$.handling') WHEN 'skrevet' THEN 'written'
                                   WHEN 'angret' THEN 'undone' WHEN 'hentet' THEN 'fetched'
                                   ELSE json_extract(value, '$.handling') END,
                    'by', json_extract(value, '$.av'), 'lines', json_extract(value, '$.linjer'),
                    'conflicts', json_extract(value, '$.konflikter'), 'backup', json_extract(value, '$.backup')))
               FROM json_each(counts.log));

    ALTER TABLE line_types RENAME COLUMN prosjekt_id TO project_id;
    UPDATE line_types SET type = CASE type WHEN 'utstyr' THEN 'equipment' WHEN 'forbruk' THEN 'consumable'
                                           ELSE type END;

    ALTER TABLE closed_periods RENAME COLUMN navn TO name;
    ALTER TABLE closed_periods RENAME COLUMN fra TO from_week;
    ALTER TABLE closed_periods RENAME COLUMN til TO to_week;
    ALTER TABLE closed_periods RENAME COLUMN paske TO easter;

    ALTER TABLE change_log RENAME COLUMN tid TO time;
    ALTER TABLE change_log RENAME COLUMN bruker_id TO user_id;
    ALTER TABLE change_log RENAME COLUMN bruker_navn TO user_name;
    ALTER TABLE change_log RENAME COLUMN rolle TO role;
    ALTER TABLE change_log RENAME COLUMN gjennomforing_id TO run_id;
    ALTER TABLE change_log RENAME COLUMN prosjekt_navn TO project_name;
    ALTER TABLE change_log RENAME COLUMN skolear TO school_year;
    ALTER TABLE change_log RENAME COLUMN handling TO action;
    ALTER TABLE change_log RENAME COLUMN felt TO field;
    ALTER TABLE change_log RENAME COLUMN fra TO from_value;
    ALTER TABLE change_log RENAME COLUMN til TO to_value;
    ALTER TABLE change_log RENAME COLUMN kvittert TO acknowledged;
    ALTER TABLE change_log RENAME COLUMN emne_id TO course_id;
    ALTER TABLE change_log RENAME COLUMN mottaker_id TO recipient_id;
    DROP INDEX endring_ukvittert;
    CREATE INDEX change_log_unacknowledged ON change_log(acknowledged, run_id);
    UPDATE change_log SET role = CASE role WHEN 'ingenior' THEN 'engineer' WHEN 'faglarer' THEN 'lecturer'
                                           ELSE role END;
    UPDATE change_log SET action = CASE action
        WHEN 'opprettet' THEN 'created' WHEN 'endret' THEN 'changed' WHEN 'slettet' THEN 'deleted'
        WHEN 'fjernet' THEN 'removed' WHEN 'gjenopprettet' THEN 'restored'
        WHEN 'valgt_ingenior' THEN 'engineer_assigned' WHEN 'fjernet_ingenior' THEN 'engineer_removed'
        WHEN 'slettet bestilling' THEN 'order_deleted' WHEN 'angret varemottak' THEN 'receipt_undone'
        WHEN 'slettet telling' THEN 'count_deleted' WHEN 'angret telling' THEN 'count_undone'
        WHEN 'slettet skoleår' THEN 'school_year_deleted' ELSE action END;
    UPDATE change_log SET
        from_value = CASE from_value WHEN 'sett' THEN 'semester_kit' WHEN 'prosjekt' THEN 'project'
                                     WHEN 'hack' THEN 'hackathon' ELSE from_value END,
        to_value = CASE to_value WHEN 'sett' THEN 'semester_kit' WHEN 'prosjekt' THEN 'project'
                                 WHEN 'hack' THEN 'hackathon' ELSE to_value END
    WHERE field = 'type';

    ALTER TABLE courses RENAME COLUMN kode TO code;
    ALTER TABLE courses RENAME COLUMN navn TO name;
    ALTER TABLE courses RENAME COLUMN faglarer_id TO lecturer_id;
    ALTER TABLE courses RENAME COLUMN ingenior_id TO engineer_id;
    ALTER TABLE courses RENAME COLUMN aktiv TO active;
    ALTER TABLE courses RENAME COLUMN studenter TO students;

    CREATE TABLE users_new (
        id              INTEGER PRIMARY KEY,
        username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name            TEXT NOT NULL,
        role            TEXT NOT NULL CHECK (role IN ('engineer', 'lecturer')),
        password        TEXT NOT NULL,
        active          INTEGER NOT NULL DEFAULT 1,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until    TEXT,
        last_login      TEXT,
        created         TEXT NOT NULL,
        admin           INTEGER NOT NULL DEFAULT 0
    );
    INSERT INTO users_new (id, username, name, role, password, active, failed_attempts, locked_until, last_login,
                           created, admin)
        SELECT id, brukernavn, navn, CASE rolle WHEN 'ingenior' THEN 'engineer' ELSE 'lecturer' END, passord,
               aktiv, feilforsok, last_til, sist_inne, opprettet, admin FROM users;
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;

    CREATE TABLE projects_new (
        id        TEXT PRIMARY KEY,
        name      TEXT NOT NULL,
        type      TEXT NOT NULL DEFAULT 'hackathon',
        note      TEXT NOT NULL DEFAULT '',
        color     INTEGER NOT NULL DEFAULT 0,
        course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL
    );
    INSERT INTO projects_new (id, name, type, note, color, course_id)
        SELECT id, navn, CASE type WHEN 'sett' THEN 'semester_kit' WHEN 'prosjekt' THEN 'project'
                                   WHEN 'hack' THEN 'hackathon' ELSE type END, notat, color, emne_id FROM projects;
    DROP TABLE projects;
    ALTER TABLE projects_new RENAME TO projects
    """,
    # 17: a secret calendar link per user (the calendar feed can't log in, so the link is the key)
    """
    ALTER TABLE users ADD COLUMN calendar_token TEXT;
    CREATE UNIQUE INDEX users_calendar_token ON users(calendar_token)
    """,
]
_JSON_ORDER = ("lines", "stocked", "stock_log")
BACKUP_DIR_NAME = "Sikkerhetskopi-db"  # database backups, in the data folder (users see this folder)

PROJECT_FIELDS = ("name", "type", "note", "color", "course_id")  # apply to every school year
COURSE_FIELDS = ("code", "name", "program", "students", "lecturer_id", "engineer_id", "active")
RUN_FIELDS = ("start", "finish", "students", "group_size", "spare", "bom", "lead", "buf",
              "school_year")  # school year: only from the admin panel
# when these change, later school years follow – but only those that had the same value (never history,
# never something that was set on purpose)
FORWARD_FIELDS = ("bom", "lead", "buf")
SET_FIELDS = ("students", "group_size", "spare")


def set_count(students: int | None, group: int | None, spare: int | None) -> int | None:
    """Sets = students / group size, rounded up, + spare sets. The ONLY place this is computed."""
    if students is None:
        return None
    return math.ceil(students / max(group or 1, 1)) + (spare or 0)


class Db:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def tx(self):
        """One transaction: all or nothing."""
        with closing(sqlite3.connect(self.path)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            with con:
                yield con

    # ---------- start-up ----------

    def init(self, json_file: Path | None = None) -> None:
        """Create or upgrade the database. The very first time, projects are imported from arshjul.json.

        Before an upgrade the database is backed up to Sikkerhetskopi-db/ (same place as in the admin panel).
        Migrations run with foreign keys off – a table rebuild would otherwise trigger ON DELETE actions –
        and the foreign keys are checked before the transaction is committed.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and 0 < self.version() < len(MIGRATIONS):
            self.backup(self.path.parent / BACKUP_DIR_NAME / f"arshjul {dt.datetime.now():%Y-%m-%d %H%M%S}.db")
        with closing(sqlite3.connect(self.path)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = OFF")  # must be set outside a transaction
            with con:
                con.execute("BEGIN")  # upgrade and import in one transaction
                version = con.execute("PRAGMA user_version").fetchone()[0]
                for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
                    for stmt in sql.split(";"):
                        if stmt.strip():
                            con.execute(stmt)
                    con.execute(f"PRAGMA user_version = {i}")
                broken = con.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise sqlite3.IntegrityError(f"Foreign keys broken after migration: {[tuple(r) for r in broken]}")
                if version == 0:
                    if json_file and json_file.exists():
                        self._import(con, json_file)
                    else:
                        self._set_closed(con, DEFAULT_CLOSED)
        if version == 0 and json_file and json_file.exists():
            json_file.rename(json_file.with_name(json_file.name + ".importert"))

    def _import(self, con, json_file: Path) -> None:
        """The old arshjul.json format (Norwegian keys) – read once, into today's tables."""
        data = json.loads(json_file.read_text(encoding="utf-8"))
        sy = deadlines.school_year_for(dt.date.today())
        types = {"sett": "semester_kit", "prosjekt": "project", "hack": "hackathon"}
        for p in data.get("prosjekter", []):
            con.execute("INSERT INTO projects (id, name, type, note, color) VALUES (?,?,?,?,?)",
                        (p["id"], p.get("navn") or "Prosjekt", types.get(p.get("type") or "hack", p.get("type")),
                         p.get("notat") or "", p.get("color") or 0))
            con.execute("""INSERT INTO runs (project_id, school_year, start, finish, sets, students, bom, lead, buf)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (p["id"], sy, p.get("start") or 40, p.get("slutt") or p.get("start") or 40, p.get("sett"),
                         p.get("sett"), p.get("bom"), p.get("lev") or 0, p.get("buf") or 0))
        old = data.get("stengte_perioder")
        self._set_closed(con, [{"name": c["navn"], "from_week": c["fra"], "to_week": c["til"]} for c in old]
                         if old else DEFAULT_CLOSED)

    # ---------- closed periods ----------

    @staticmethod
    def _set_closed(con, periods: list[dict]) -> None:
        table = "closed_periods" if con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'closed_periods'").fetchone() else None
        if table is None:  # only in tests that stop before migration 16: the old table
            con.execute("DELETE FROM stengt_periode")
            con.executemany("INSERT INTO stengt_periode (navn, fra, til) VALUES (?,?,?)",
                            [(c["name"], c["from_week"], c["to_week"]) for c in periods])
            return
        con.execute("DELETE FROM closed_periods")
        con.executemany("INSERT INTO closed_periods (name, from_week, to_week, easter) VALUES (?,?,?,?)",
                        [(c["name"], c["from_week"], c["to_week"], 1 if c.get("easter") else 0) for c in periods])

    def closed_periods(self) -> list[dict]:
        with self.tx() as con:
            return [{**dict(r), "easter": bool(r["easter"])}
                    for r in con.execute("SELECT name, from_week, to_week, easter FROM closed_periods ORDER BY id")]

    def set_closed(self, periods: list[dict]) -> None:
        with self.tx() as con:
            self._set_closed(con, periods)

    # ---------- runs ----------

    _SELECT = """SELECT g.id, g.project_id AS projectId, g.school_year, g.start, g.finish, g.sets,
                        g.students, g.group_size, g.spare, g.bom, g.lead, g.buf, g.deleted, g.deleted_by AS deletedBy,
                        g.ordered_without_bom AS orderedWithoutBom, g.ordered_without_bom_by AS orderedWithoutBomBy,
                        p.name, p.type, p.note, p.color,
                        p.course_id AS courseId, e.code AS courseCode, e.name AS courseName, e.program,
                        e.students AS courseStudents,
                        e.lecturer_id AS lecturerId, f.name AS lecturerName,
                        e.engineer_id AS engineerId, i.name AS engineerName
                 FROM runs g JOIN projects p ON p.id = g.project_id
                 LEFT JOIN courses e ON e.id = p.course_id
                 LEFT JOIN users f ON f.id = e.lecturer_id
                 LEFT JOIN users i ON i.id = e.engineer_id"""

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["id"] = str(d["id"])
        return d

    def runs(self) -> list[dict]:
        with self.tx() as con:
            return [self._row(r) for r in con.execute(self._SELECT + " ORDER BY g.school_year, g.id")]

    def run(self, gid: str) -> dict:
        with self.tx() as con:
            r = con.execute(self._SELECT + " WHERE g.id = ?", (gid,)).fetchone()
        if r is None:
            raise KeyError(gid)
        return self._row(r)

    def new(self, project_id: str, school_year: int, fields: dict) -> dict:
        """A new project with its first run."""
        with self.tx() as con:
            con.execute("INSERT INTO projects (id, name, type, note, color, course_id) VALUES (?,?,?,?,?,?)",
                        (project_id, fields["name"], fields.get("type", "hackathon"), fields.get("note", ""),
                         fields.get("color", 0), fields.get("course_id")))
            students, group, spare = fields.get("students"), fields.get("group_size") or 1, fields.get("spare") or 0
            cur = con.execute("""INSERT INTO runs (project_id, school_year, start, finish, sets, students, group_size,
                                                   spare, bom, lead, buf)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                              (project_id, school_year, fields.get("start", 40), fields.get("finish", fields.get("start", 40)),
                               set_count(students, group, spare), students, group, spare,
                               fields.get("bom"), fields.get("lead", 2), fields.get("buf", 2)))
            gid = cur.lastrowid
        return self.run(str(gid))

    def update(self, gid: str, fields: dict) -> dict:
        """Change a run. Project fields apply to the project (every year); BOM, lead time and buffer apply to
        this and later school years that had the same value (see FORWARD_FIELDS) – never earlier years."""
        g = self.run(gid)
        pf = {k: v for k, v in fields.items() if k in PROJECT_FIELDS}
        gf = {k: v for k, v in fields.items() if k in RUN_FIELDS}
        if any(k in gf for k in SET_FIELDS):
            new = {**{k: g[k] for k in SET_FIELDS}, **gf}
            gf["group_size"], gf["spare"] = new["group_size"] or 1, new["spare"] or 0
            gf["sets"] = set_count(new["students"], gf["group_size"], gf["spare"])
        with self.tx() as con:
            if pf:
                con.execute(f"UPDATE projects SET {', '.join(k + ' = ?' for k in pf)} WHERE id = ?",
                            (*pf.values(), g["projectId"]))
            if gf:
                con.execute(f"UPDATE runs SET {', '.join(k + ' = ?' for k in gf)} WHERE id = ?",
                            (*gf.values(), gid))
            for k in FORWARD_FIELDS:
                if k in gf and gf[k] != g[k]:
                    con.execute(f"UPDATE runs SET {k} = ? WHERE project_id = ? AND school_year > ? AND {k} IS ?",
                                (gf[k], g["projectId"], g["school_year"], g[k]))
        return self.run(gid)

    def copy_year(self, from_year: int, ids: list[str] | None = None, changes: dict[str, dict] | None = None) -> list[str]:
        """Copy runs from school year «from_year» to the year after, with the same weeks, students and groups.

        ids: which runs (default: all). Projects that already exist in the next school year are skipped.
        Orders are not copied. Returns the new ids.
        changes: {id: {start, finish, students, group_size, spare}} – what should be different next year.
        """
        created, changes = [], changes or {}
        with self.tx() as con:
            rows = con.execute("SELECT * FROM runs WHERE school_year = ? AND deleted IS NULL", (from_year,)).fetchall()
            existing = {r[0] for r in con.execute("SELECT project_id FROM runs WHERE school_year = ?", (from_year + 1,))}
            for r in rows:
                if (ids is not None and str(r["id"]) not in ids) or r["project_id"] in existing:
                    continue
                n = {**dict(r), **{k: v for k, v in changes.get(str(r["id"]), {}).items()
                                   if k in ("start", "finish", *SET_FIELDS)}}
                n["group_size"], n["spare"] = n["group_size"] or 1, n["spare"] or 0
                cur = con.execute("""INSERT INTO runs (project_id, school_year, start, finish, sets, students, group_size,
                                                       spare, bom, lead, buf)
                                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                                  (r["project_id"], from_year + 1, n["start"], n["finish"],
                                   set_count(n["students"], n["group_size"], n["spare"]), n["students"], n["group_size"],
                                   n["spare"], r["bom"], r["lead"], r["buf"]))
                existing.add(r["project_id"])
                created.append(str(cur.lastrowid))
        return created

    def ordered_without_bom(self, gid: str, date: str | None, by: str | None) -> None:
        """Mark (or unmark) that what a project without a BOM needs has been ordered."""
        with self.tx() as con:
            con.execute("UPDATE runs SET ordered_without_bom = ?, ordered_without_bom_by = ? WHERE id = ?", (date, by, gid))

    def soft_delete(self, gid: str, by: str) -> None:
        """A lecturer removes a run: it is hidden until the engineer confirms or restores it."""
        with self.tx() as con:
            con.execute("UPDATE runs SET deleted = ?, deleted_by = ? WHERE id = ?",
                        (dt.datetime.now().isoformat(timespec="minutes"), by, gid))

    def restore(self, gid: str) -> None:
        with self.tx() as con:
            con.execute("UPDATE runs SET deleted = NULL, deleted_by = NULL WHERE id = ?", (gid,))

    # ---------- users ----------

    def new_user(self, username: str, name: str, role: str, password_hash: str, admin: bool = False) -> int:
        with self.tx() as con:
            cur = con.execute("INSERT INTO users (username, name, role, password, created, admin) VALUES (?,?,?,?,?,?)",
                              (username, name, role, password_hash, dt.datetime.now().isoformat(timespec="seconds"),
                               1 if admin and role == "engineer" else 0))
            return cur.lastrowid

    def user_count(self) -> int:
        with self.tx() as con:
            return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def active_admins(self) -> int:
        with self.tx() as con:
            return con.execute("SELECT COUNT(*) FROM users WHERE admin = 1 AND active = 1").fetchone()[0]

    def user(self, *, id: int | None = None, username: str | None = None) -> dict | None:
        with self.tx() as con:
            if id is not None:
                r = con.execute("SELECT * FROM users WHERE id = ?", (id,)).fetchone()
            else:
                r = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(r) if r else None

    def users(self) -> list[dict]:
        with self.tx() as con:
            return [dict(r) for r in con.execute(
                "SELECT id, username, name, role, admin, active, last_login, locked_until FROM users ORDER BY role, name")]

    def user_by_calendar_token(self, token: str) -> dict | None:
        with self.tx() as con:
            r = con.execute("SELECT * FROM users WHERE calendar_token = ? AND active = 1", (token,)).fetchone()
        return dict(r) if r else None

    def update_user(self, id: int, **fields) -> None:
        with self.tx() as con:
            con.execute(f"UPDATE users SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), id))

    # ---------- courses ----------

    _COURSE = """SELECT e.id, e.code, e.name, e.program, e.students, e.active,
                      e.lecturer_id AS lecturerId, f.name AS lecturerName,
                      e.engineer_id AS engineerId, i.name AS engineerName,
                      (SELECT COUNT(*) FROM projects p WHERE p.course_id = e.id) AS projects
               FROM courses e LEFT JOIN users f ON f.id = e.lecturer_id LEFT JOIN users i ON i.id = e.engineer_id"""

    def courses(self) -> list[dict]:
        with self.tx() as con:
            return [dict(r) for r in con.execute(self._COURSE + " ORDER BY e.active DESC, e.code")]

    def course(self, cid: int) -> dict:
        with self.tx() as con:
            r = con.execute(self._COURSE + " WHERE e.id = ?", (cid,)).fetchone()
        if r is None:
            raise KeyError(cid)
        return dict(r)

    def new_course(self, fields: dict) -> dict:
        with self.tx() as con:
            cur = con.execute("""INSERT INTO courses (code, name, program, students, lecturer_id, engineer_id)
                                 VALUES (?,?,?,?,?,?)""",
                              (fields["code"], fields.get("name", ""), fields.get("program", "bachelor"), fields.get("students"),
                               fields.get("lecturer_id"), fields.get("engineer_id")))
            cid = cur.lastrowid
        return self.course(cid)

    def update_course(self, cid: int, fields: dict) -> dict:
        fields = {k: v for k, v in fields.items() if k in COURSE_FIELDS}
        if fields:
            with self.tx() as con:
                con.execute(f"UPDATE courses SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), cid))
        return self.course(cid)

    # ---------- change log ----------

    def log(self, rows: list[dict]) -> None:
        """rows: {user_id, user_name, role, run_id, project_name, school_year, action, field, from_value, to_value,
                  course_id, recipient_id}. With recipient_id the notification is for one particular user."""
        when = dt.datetime.now().isoformat(timespec="seconds")
        with self.tx() as con:
            con.executemany("""INSERT INTO change_log (time, user_id, user_name, role, run_id, project_name, school_year,
                                                       action, field, from_value, to_value, course_id, recipient_id)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            [(when, r.get("user_id"), r["user_name"], r["role"], r.get("run_id"),
                              r["project_name"], r.get("school_year"), r["action"], r.get("field"),
                              None if r.get("from_value") is None else str(r["from_value"]),
                              None if r.get("to_value") is None else str(r["to_value"]),
                              r.get("course_id"), r.get("recipient_id")) for r in rows])

    def changes(self) -> list[dict]:
        """Unacknowledged notifications: lecturers' changes to projects, and notifications to one recipient."""
        with self.tx() as con:
            return [dict(r) for r in con.execute(
                """SELECT * FROM change_log WHERE acknowledged IS NULL
                   AND ((role = 'lecturer' AND recipient_id IS NULL) OR recipient_id IS NOT NULL) ORDER BY id""")]

    def log_search(self, *, user: str = "", project: str = "", role: str = "", from_: str = "", to: str = "",
                   limit: int = 500) -> list[dict]:
        """The whole change log, newest first, with filters (for the admin panel)."""
        sql, args = "SELECT * FROM change_log WHERE 1=1", []
        if user:
            sql += " AND user_name LIKE ?"
            args.append(f"%{user}%")
        if project:
            sql += " AND project_name LIKE ?"
            args.append(f"%{project}%")
        if role:
            sql += " AND role = ?"
            args.append(role)
        if from_:
            sql += " AND time >= ?"
            args.append(from_)
        if to:
            sql += " AND time < ?"
            args.append(to + "T99")  # include the whole day
        with self.tx() as con:
            return [dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def acknowledge_all(self) -> int:
        with self.tx() as con:
            return con.execute("UPDATE change_log SET acknowledged = ? WHERE acknowledged IS NULL",
                               (dt.datetime.now().isoformat(timespec="seconds"),)).rowcount

    def delete_user(self, uid: int) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM users WHERE id = ?", (uid,))

    def backup(self, target: Path) -> None:
        """A copy of the database while it's in use (SQLite's own backup – safe in the middle of a write)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(target)) as copy:
            source.backup(copy)

    def version(self) -> int:
        with self.tx() as con:
            return con.execute("PRAGMA user_version").fetchone()[0]

    def acknowledge_to(self, recipient_id: int, ids: list[int]) -> int:
        """«OK, sett» on notifications to you."""
        if not ids:
            return 0
        with self.tx() as con:
            return con.execute(f"""UPDATE change_log SET acknowledged = ? WHERE acknowledged IS NULL AND recipient_id = ?
                                   AND id IN ({','.join('?' * len(ids))})""",
                               (dt.datetime.now().isoformat(timespec="seconds"), recipient_id, *ids)).rowcount

    def acknowledge(self, gid: str) -> int:
        with self.tx() as con:
            return con.execute("""UPDATE change_log SET acknowledged = ?
                                  WHERE run_id = ? AND acknowledged IS NULL AND role = 'lecturer'
                                  AND recipient_id IS NULL""",
                               (dt.datetime.now().isoformat(timespec="seconds"), gid)).rowcount

    # ---------- orders ----------

    def all_orders(self) -> dict[str, dict[str, dict]]:
        """All registered orders: {run id: {supplier key: row}}."""
        out: dict[str, dict[str, dict]] = {}
        with self.tx() as con:
            for r in con.execute("SELECT * FROM orders"):
                d = dict(r)
                for k in _JSON_ORDER:
                    d[k] = json.loads(d[k])
                out.setdefault(str(d["run_id"]), {})[d["supplier"]] = d
        return out

    def new_order(self, gid: str, key: str, name: str, *, date: str, order_no: str, sets: int | None,
                  lines: list[dict], by: str | None = None) -> None:
        with self.tx() as con:
            con.execute("""INSERT INTO orders (run_id, supplier, name, status, ordered_date, order_no, sets, lines, made_by)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (gid, key, name, "ordered", date, order_no, sets, json.dumps(lines, ensure_ascii=False), by))

    def update_order(self, gid: str, key: str, **fields) -> None:
        for k in _JSON_ORDER:
            if k in fields:
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        with self.tx() as con:
            cur = con.execute(f"UPDATE orders SET {', '.join(k + ' = ?' for k in fields)} "
                              "WHERE run_id = ? AND supplier = ?", (*fields.values(), gid, key))
            if not cur.rowcount:
                raise KeyError(key)

    def delete_order(self, gid: str, key: str) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM orders WHERE run_id = ? AND supplier = ?", (gid, key))

    # ---------- counting ----------

    def counts(self) -> dict[str, dict]:
        with self.tx() as con:
            out = {}
            for r in con.execute("SELECT * FROM counts"):
                d = dict(r)
                for k in ("lines", "written_values", "log"):
                    d[k] = json.loads(d[k])
                out[str(d.pop("run_id"))] = d
        return out

    def save_count(self, gid: str, project_id: str, date: str, lines: list[dict]) -> None:
        """Save the count, and remember equipment/consumable per line for the project next year."""
        with self.tx() as con:
            # a corrected count keeps what was written to the BOM, and the log
            con.execute("""INSERT INTO counts (run_id, date, lines) VALUES (?,?,?)
                           ON CONFLICT (run_id) DO UPDATE SET date = excluded.date, lines = excluded.lines""",
                        (gid, date, json.dumps(lines, ensure_ascii=False)))
            con.executemany("INSERT OR REPLACE INTO line_types (project_id, key, type) VALUES (?,?,?)",
                            [(project_id, l["key"], l["type"]) for l in lines])

    def count_written(self, gid: str, written_values: dict[str, float], log: list[dict],
                      date: str | None, backup: str | None) -> None:
        """After writing or undoing: what the BOM now has from the count. Empty = nothing written."""
        with self.tx() as con:
            con.execute("UPDATE counts SET written = ?, backup = ?, written_values = ?, log = ? WHERE run_id = ?",
                        (date if written_values else None, backup, json.dumps(written_values, ensure_ascii=False),
                         json.dumps(log, ensure_ascii=False), gid))

    def delete_count(self, gid: str) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM counts WHERE run_id = ?", (gid,))

    def line_types(self) -> dict[str, dict[str, str]]:
        """{project id: {line key: "equipment"/"consumable"}}"""
        out: dict[str, dict[str, str]] = {}
        with self.tx() as con:
            for r in con.execute("SELECT project_id, key, type FROM line_types"):
                out.setdefault(r["project_id"], {})[r["key"]] = r["type"]
        return out

    def delete(self, gid: str) -> None:
        """Delete the run. The project is deleted too if it has no other runs."""
        g = self.run(gid)
        with self.tx() as con:
            con.execute("DELETE FROM runs WHERE id = ?", (gid,))
            if not con.execute("SELECT 1 FROM runs WHERE project_id = ?", (g["projectId"],)).fetchone():
                con.execute("DELETE FROM projects WHERE id = ?", (g["projectId"],))
