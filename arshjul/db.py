"""SQLite-databasen i datamappen: prosjekter, gjennomføringer og stengte perioder.

Prosjekt        det som går igjen hvert år: navn, type, BOM, farge, leveringstid og buffer
Gjennomføring   én kjøring av et prosjekt i et skoleår: uker og antall sett
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import frister

DEFAULT_STENGT = [
    {"navn": "Juleferie", "fra": 52, "til": 1},
    {"navn": "Påske", "fra": 12, "til": 12},
    {"navn": "Fellesferie", "fra": 28, "til": 31},
]

# Hver oppgradering kjøres én gang, i rekkefølge. Legg nye til nederst – endre aldri en gammel.
MIGRERINGER = [
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
    # 2: bestillinger per leverandør, med frossen kopi av linjene som ble bestilt (JSON)
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
    # 3: telling etter prosjektet, og om en linje er utstyr eller forbruk (huskes per prosjekt)
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
    # 4: hva som sist ble skrevet til BOM-en per linje (for retting og angring), og en logg
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
    # 5: varemottak – hvor mye av hver linje som er lagt til «På lager» i BOM-en, og en logg
    """
    ALTER TABLE bestilling ADD COLUMN paa_lager TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE bestilling ADD COLUMN lager_logg TEXT NOT NULL DEFAULT '[]';
    """,
    # 6: linjenøkkelen er nå «leverandør|varenummer» (varenumre er bare unike per leverandør).
    #    Bestillinger kjenner leverandøren sin, så de gjøres om nøyaktig. Tellinger og
    #    utstyr/forbruk lagret ikke leverandøren og kan ikke gjøres om trygt; de slettes.
    #    (Da dette ble innført, fantes det bare testdata, og ingeniøren godkjente det.)
    """
    UPDATE bestilling SET
        linjer = (SELECT json_group_array(json_set(value, '$.key', bestilling.leverandor || '|' || json_extract(value, '$.key')))
                  FROM json_each(bestilling.linjer)),
        paa_lager = COALESCE((SELECT json_group_object(bestilling.leverandor || '|' || key, value)
                              FROM json_each(bestilling.paa_lager)), '{}');
    DELETE FROM telling;
    DELETE FROM linjetype;
    """,
    # 7: brukere og roller, faglærer per prosjekt, myk sletting og endringslogg
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
    # 8: emner. Et prosjekt hører til et emne; emnet har én faglærer og én ansvarlig ingeniør.
    #    prosjekt.faglarer_id brukes ikke lenger (kan ikke droppes – den har en fremmednøkkel);
    #    eksisterende prosjekter får ikke emne ennå og vises for alle ingeniører til de får et.
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
    # 9: administrator – en ingeniør som har alle emner og styrer brukerne
    """
    ALTER TABLE bruker ADD COLUMN admin INTEGER NOT NULL DEFAULT 0;
    """,
]
_JSON_BESTILLING = ("linjer", "paa_lager", "lager_logg")

PROSJEKT_FELT = ("navn", "type", "lev", "buf", "notat", "color", "bom", "emne_id")
EMNE_FELT = ("kode", "navn", "program", "faglarer_id", "ingenior_id", "aktiv")
GJENNOMFORING_FELT = ("start", "slutt", "sett")


class Db:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def tx(self):
        """Én transaksjon: alt eller ingenting."""
        with closing(sqlite3.connect(self.path)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            with con:
                yield con

    # ---------- oppstart ----------

    def init(self, json_file: Path | None = None) -> None:
        """Lag eller oppgrader databasen. Første gang hentes prosjektene fra arshjul.json."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as con:
            con.execute("BEGIN")  # oppgradering og import i én transaksjon
            versjon = con.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(MIGRERINGER[versjon:], start=versjon + 1):
                for stmt in sql.split(";"):
                    if stmt.strip():
                        con.execute(stmt)
                con.execute(f"PRAGMA user_version = {i}")
            if versjon == 0:
                if json_file and json_file.exists():
                    self._importer(con, json_file)
                else:
                    self._sett_stengte(con, DEFAULT_STENGT)
        if versjon == 0 and json_file and json_file.exists():
            json_file.rename(json_file.with_name(json_file.name + ".importert"))

    def _importer(self, con, json_file: Path) -> None:
        data = json.loads(json_file.read_text(encoding="utf-8"))
        sy = frister.skolear_for(dt.date.today())
        for p in data.get("prosjekter", []):
            con.execute("INSERT INTO prosjekt (id, navn, type, lev, buf, notat, color, bom) VALUES (?,?,?,?,?,?,?,?)",
                        (p["id"], p.get("navn") or "Prosjekt", p.get("type") or "hack", p.get("lev") or 0,
                         p.get("buf") or 0, p.get("notat") or "", p.get("color") or 0, p.get("bom")))
            con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES (?,?,?,?,?)",
                        (p["id"], sy, p.get("start") or 40, p.get("slutt") or p.get("start") or 40, p.get("sett")))
        self._sett_stengte(con, data.get("stengte_perioder") or DEFAULT_STENGT)

    # ---------- stengte perioder ----------

    @staticmethod
    def _sett_stengte(con, perioder: list[dict]) -> None:
        con.execute("DELETE FROM stengt_periode")
        con.executemany("INSERT INTO stengt_periode (navn, fra, til) VALUES (?,?,?)",
                        [(c["navn"], c["fra"], c["til"]) for c in perioder])

    def stengte(self) -> list[dict]:
        with self.tx() as con:
            return [dict(r) for r in con.execute("SELECT navn, fra, til FROM stengt_periode ORDER BY id")]

    def sett_stengte(self, perioder: list[dict]) -> None:
        with self.tx() as con:
            self._sett_stengte(con, perioder)

    # ---------- gjennomføringer ----------

    _SELECT = """SELECT g.id, g.prosjekt_id AS prosjektId, g.skolear, g.start, g.slutt, g.sett,
                        g.slettet, g.slettet_av AS slettetAv,
                        p.navn, p.type, p.lev, p.buf, p.notat, p.color, p.bom,
                        p.emne_id AS emneId, e.kode AS emneKode, e.navn AS emneNavn, e.program,
                        e.faglarer_id AS faglarerId, f.navn AS faglarerNavn,
                        e.ingenior_id AS ingeniorId, i.navn AS ingeniorNavn
                 FROM gjennomforing g JOIN prosjekt p ON p.id = g.prosjekt_id
                 LEFT JOIN emne e ON e.id = p.emne_id
                 LEFT JOIN bruker f ON f.id = e.faglarer_id
                 LEFT JOIN bruker i ON i.id = e.ingenior_id"""

    @staticmethod
    def _rad(r) -> dict:
        d = dict(r)
        d["id"] = str(d["id"])
        return d

    def gjennomforinger(self) -> list[dict]:
        with self.tx() as con:
            return [self._rad(r) for r in con.execute(self._SELECT + " ORDER BY g.skolear, g.id")]

    def gjennomforing(self, gid: str) -> dict:
        with self.tx() as con:
            r = con.execute(self._SELECT + " WHERE g.id = ?", (gid,)).fetchone()
        if r is None:
            raise KeyError(gid)
        return self._rad(r)

    def ny(self, prosjekt_id: str, skolear: int, felt: dict) -> dict:
        """Nytt prosjekt med sin første gjennomføring."""
        with self.tx() as con:
            con.execute("""INSERT INTO prosjekt (id, navn, type, lev, buf, notat, color, bom, emne_id)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (prosjekt_id, felt["navn"], felt.get("type", "hack"), felt.get("lev", 2), felt.get("buf", 2),
                         felt.get("notat", ""), felt.get("color", 0), felt.get("bom"), felt.get("emne_id")))
            cur = con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES (?,?,?,?,?)",
                              (prosjekt_id, skolear, felt.get("start", 40), felt.get("slutt", felt.get("start", 40)),
                               felt.get("sett")))
            gid = cur.lastrowid
        return self.gjennomforing(str(gid))

    def endre(self, gid: str, felt: dict) -> dict:
        """Endre en gjennomføring. Prosjektfeltene gjelder prosjektet (alle år)."""
        g = self.gjennomforing(gid)
        pf = {k: v for k, v in felt.items() if k in PROSJEKT_FELT}
        gf = {k: v for k, v in felt.items() if k in GJENNOMFORING_FELT}
        with self.tx() as con:
            if pf:
                con.execute(f"UPDATE prosjekt SET {', '.join(k + ' = ?' for k in pf)} WHERE id = ?",
                            (*pf.values(), g["prosjektId"]))
            if gf:
                con.execute(f"UPDATE gjennomforing SET {', '.join(k + ' = ?' for k in gf)} WHERE id = ?",
                            (*gf.values(), gid))
        return self.gjennomforing(gid)

    def kopier(self, fra: int, ids: list[str] | None = None) -> list[str]:
        """Kopier gjennomføringer fra skoleåret «fra» til året etter, med samme uker og antall sett.

        ids: hvilke gjennomføringer (standard: alle). Prosjekter som allerede finnes
        i neste skoleår hoppes over. Bestillinger kopieres ikke. Returnerer de nye id-ene.
        """
        nye = []
        with self.tx() as con:
            rader = con.execute("SELECT * FROM gjennomforing WHERE skolear = ? AND slettet IS NULL", (fra,)).fetchall()
            finnes = {r[0] for r in con.execute("SELECT prosjekt_id FROM gjennomforing WHERE skolear = ?", (fra + 1,))}
            for r in rader:
                if (ids is not None and str(r["id"]) not in ids) or r["prosjekt_id"] in finnes:
                    continue
                cur = con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES (?,?,?,?,?)",
                                  (r["prosjekt_id"], fra + 1, r["start"], r["slutt"], r["sett"]))
                finnes.add(r["prosjekt_id"])
                nye.append(str(cur.lastrowid))
        return nye

    def myk_slett(self, gid: str, av: str) -> None:
        """Faglærer fjerner: gjennomføringen skjules til ingeniøren bekrefter eller gjenoppretter."""
        with self.tx() as con:
            con.execute("UPDATE gjennomforing SET slettet = ?, slettet_av = ? WHERE id = ?",
                        (dt.datetime.now().isoformat(timespec="minutes"), av, gid))

    def gjenopprett(self, gid: str) -> None:
        with self.tx() as con:
            con.execute("UPDATE gjennomforing SET slettet = NULL, slettet_av = NULL WHERE id = ?", (gid,))

    # ---------- brukere ----------

    def ny_bruker(self, brukernavn: str, navn: str, rolle: str, passord_hash: str, admin: bool = False) -> int:
        with self.tx() as con:
            cur = con.execute("INSERT INTO bruker (brukernavn, navn, rolle, passord, opprettet, admin) VALUES (?,?,?,?,?,?)",
                              (brukernavn, navn, rolle, passord_hash, dt.datetime.now().isoformat(timespec="seconds"),
                               1 if admin and rolle == "ingenior" else 0))
            return cur.lastrowid

    def antall_brukere(self) -> int:
        with self.tx() as con:
            return con.execute("SELECT COUNT(*) FROM bruker").fetchone()[0]

    def aktive_admins(self) -> int:
        with self.tx() as con:
            return con.execute("SELECT COUNT(*) FROM bruker WHERE admin = 1 AND aktiv = 1").fetchone()[0]

    def bruker(self, *, id: int | None = None, brukernavn: str | None = None) -> dict | None:
        with self.tx() as con:
            if id is not None:
                r = con.execute("SELECT * FROM bruker WHERE id = ?", (id,)).fetchone()
            else:
                r = con.execute("SELECT * FROM bruker WHERE brukernavn = ?", (brukernavn,)).fetchone()
        return dict(r) if r else None

    def brukere(self) -> list[dict]:
        with self.tx() as con:
            return [dict(r) for r in con.execute(
                "SELECT id, brukernavn, navn, rolle, admin, aktiv, sist_inne, last_til FROM bruker ORDER BY rolle, navn")]

    def endre_bruker(self, id: int, **felt) -> None:
        with self.tx() as con:
            con.execute(f"UPDATE bruker SET {', '.join(k + ' = ?' for k in felt)} WHERE id = ?", (*felt.values(), id))

    # ---------- emner ----------

    _EMNE = """SELECT e.id, e.kode, e.navn, e.program, e.aktiv,
                      e.faglarer_id AS faglarerId, f.navn AS faglarerNavn,
                      e.ingenior_id AS ingeniorId, i.navn AS ingeniorNavn,
                      (SELECT COUNT(*) FROM prosjekt p WHERE p.emne_id = e.id) AS prosjekter
               FROM emne e LEFT JOIN bruker f ON f.id = e.faglarer_id LEFT JOIN bruker i ON i.id = e.ingenior_id"""

    def emner(self) -> list[dict]:
        with self.tx() as con:
            return [dict(r) for r in con.execute(self._EMNE + " ORDER BY e.aktiv DESC, e.kode")]

    def emne(self, eid: int) -> dict:
        with self.tx() as con:
            r = con.execute(self._EMNE + " WHERE e.id = ?", (eid,)).fetchone()
        if r is None:
            raise KeyError(eid)
        return dict(r)

    def nytt_emne(self, felt: dict) -> dict:
        with self.tx() as con:
            cur = con.execute("INSERT INTO emne (kode, navn, program, faglarer_id, ingenior_id) VALUES (?,?,?,?,?)",
                              (felt["kode"], felt.get("navn", ""), felt.get("program", "bachelor"),
                               felt.get("faglarer_id"), felt.get("ingenior_id")))
            eid = cur.lastrowid
        return self.emne(eid)

    def endre_emne(self, eid: int, felt: dict) -> dict:
        felt = {k: v for k, v in felt.items() if k in EMNE_FELT}
        if felt:
            with self.tx() as con:
                con.execute(f"UPDATE emne SET {', '.join(k + ' = ?' for k in felt)} WHERE id = ?", (*felt.values(), eid))
        return self.emne(eid)

    # ---------- endringslogg ----------

    def logg(self, rader: list[dict]) -> None:
        """rader: {bruker_id, bruker_navn, rolle, gjennomforing_id, prosjekt_navn, skolear, handling, felt, fra, til}"""
        tid = dt.datetime.now().isoformat(timespec="seconds")
        with self.tx() as con:
            con.executemany("""INSERT INTO endring (tid, bruker_id, bruker_navn, rolle, gjennomforing_id, prosjekt_navn,
                                                    skolear, handling, felt, fra, til)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            [(tid, r.get("bruker_id"), r["bruker_navn"], r["rolle"], r.get("gjennomforing_id"),
                              r["prosjekt_navn"], r.get("skolear"), r["handling"], r.get("felt"),
                              None if r.get("fra") is None else str(r["fra"]),
                              None if r.get("til") is None else str(r["til"])) for r in rader])

    def endringer(self, ukvitterte: bool = True, rolle: str | None = "faglarer") -> list[dict]:
        sql = "SELECT * FROM endring WHERE 1=1"
        args: list = []
        if ukvitterte:
            sql += " AND kvittert IS NULL"
        if rolle:
            sql += " AND rolle = ?"
            args.append(rolle)
        with self.tx() as con:
            return [dict(r) for r in con.execute(sql + " ORDER BY id", args)]

    def kvitter(self, gid: str) -> int:
        with self.tx() as con:
            return con.execute("""UPDATE endring SET kvittert = ?
                                  WHERE gjennomforing_id = ? AND kvittert IS NULL AND rolle = 'faglarer'""",
                               (dt.datetime.now().isoformat(timespec="seconds"), gid)).rowcount

    # ---------- bestillinger ----------

    def bestillinger(self) -> dict[str, dict[str, dict]]:
        """Alle registrerte bestillinger: {gjennomføring-id: {leverandør-nøkkel: rad}}."""
        out: dict[str, dict[str, dict]] = {}
        with self.tx() as con:
            for r in con.execute("SELECT * FROM bestilling"):
                d = dict(r)
                for k in _JSON_BESTILLING:
                    d[k] = json.loads(d[k])
                out.setdefault(str(d["gjennomforing_id"]), {})[d["leverandor"]] = d
        return out

    def ny_bestilling(self, gid: str, key: str, navn: str, *, dato: str, ordrenr: str, sett: int | None,
                      linjer: list[dict], av: str | None = None) -> None:
        with self.tx() as con:
            con.execute("""INSERT INTO bestilling (gjennomforing_id, leverandor, navn, status, bestilt_dato, ordrenr, sett, linjer, av)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (gid, key, navn, "bestilt", dato, ordrenr, sett, json.dumps(linjer, ensure_ascii=False), av))

    def endre_bestilling(self, gid: str, key: str, **felt) -> None:
        for k in _JSON_BESTILLING:
            if k in felt:
                felt[k] = json.dumps(felt[k], ensure_ascii=False)
        with self.tx() as con:
            cur = con.execute(f"UPDATE bestilling SET {', '.join(k + ' = ?' for k in felt)} "
                              "WHERE gjennomforing_id = ? AND leverandor = ?", (*felt.values(), gid, key))
            if not cur.rowcount:
                raise KeyError(key)

    def slett_bestilling(self, gid: str, key: str) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM bestilling WHERE gjennomforing_id = ? AND leverandor = ?", (gid, key))

    # ---------- telling ----------

    def tellinger(self) -> dict[str, dict]:
        with self.tx() as con:
            out = {}
            for r in con.execute("SELECT * FROM telling"):
                d = dict(r)
                for k in ("linjer", "skrevet_verdier", "logg"):
                    d[k] = json.loads(d[k])
                out[str(d.pop("gjennomforing_id"))] = d
        return out

    def lagre_telling(self, gid: str, prosjekt_id: str, dato: str, linjer: list[dict]) -> None:
        """Lagre tellingen og husk utstyr/forbruk for prosjektet til neste år."""
        with self.tx() as con:
            # en rettet telling beholder det som er skrevet til BOM-en og loggen
            con.execute("""INSERT INTO telling (gjennomforing_id, dato, linjer) VALUES (?,?,?)
                           ON CONFLICT (gjennomforing_id) DO UPDATE SET dato = excluded.dato, linjer = excluded.linjer""",
                        (gid, dato, json.dumps(linjer, ensure_ascii=False)))
            con.executemany("INSERT OR REPLACE INTO linjetype (prosjekt_id, key, type) VALUES (?,?,?)",
                            [(prosjekt_id, l["key"], l["type"]) for l in linjer])

    def telling_skrevet(self, gid: str, skrevet_verdier: dict[str, float], logg: list[dict],
                        dato: str | None, backup: str | None) -> None:
        """Etter skriving eller angring: hva BOM-en nå har fra tellingen. Tomt = ingenting skrevet."""
        with self.tx() as con:
            con.execute("UPDATE telling SET skrevet = ?, backup = ?, skrevet_verdier = ?, logg = ? WHERE gjennomforing_id = ?",
                        (dato if skrevet_verdier else None, backup, json.dumps(skrevet_verdier, ensure_ascii=False),
                         json.dumps(logg, ensure_ascii=False), gid))

    def slett_telling(self, gid: str) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM telling WHERE gjennomforing_id = ?", (gid,))

    def linjetyper(self) -> dict[str, dict[str, str]]:
        """{prosjekt-id: {linje-nøkkel: «utstyr»/«forbruk»}}"""
        out: dict[str, dict[str, str]] = {}
        with self.tx() as con:
            for r in con.execute("SELECT prosjekt_id, key, type FROM linjetype"):
                out.setdefault(r["prosjekt_id"], {})[r["key"]] = r["type"]
        return out

    def slett(self, gid: str) -> None:
        """Slett gjennomføringen. Prosjektet slettes også hvis det ikke har flere."""
        g = self.gjennomforing(gid)
        with self.tx() as con:
            con.execute("DELETE FROM gjennomforing WHERE id = ?", (gid,))
            if not con.execute("SELECT 1 FROM gjennomforing WHERE prosjekt_id = ?", (g["prosjektId"],)).fetchone():
                con.execute("DELETE FROM prosjekt WHERE id = ?", (g["prosjektId"],))
