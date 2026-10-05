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
]

PROSJEKT_FELT = ("navn", "type", "lev", "buf", "notat", "color", "bom")
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
                        p.navn, p.type, p.lev, p.buf, p.notat, p.color, p.bom
                 FROM gjennomforing g JOIN prosjekt p ON p.id = g.prosjekt_id"""

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
            con.execute("INSERT INTO prosjekt (id, navn, type, lev, buf, notat, color, bom) VALUES (?,?,?,?,?,?,?,?)",
                        (prosjekt_id, felt["navn"], felt.get("type", "hack"), felt.get("lev", 2), felt.get("buf", 2),
                         felt.get("notat", ""), felt.get("color", 0), felt.get("bom")))
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
            rader = con.execute("SELECT * FROM gjennomforing WHERE skolear = ?", (fra,)).fetchall()
            finnes = {r[0] for r in con.execute("SELECT prosjekt_id FROM gjennomforing WHERE skolear = ?", (fra + 1,))}
            for r in rader:
                if (ids is not None and str(r["id"]) not in ids) or r["prosjekt_id"] in finnes:
                    continue
                cur = con.execute("INSERT INTO gjennomforing (prosjekt_id, skolear, start, slutt, sett) VALUES (?,?,?,?,?)",
                                  (r["prosjekt_id"], fra + 1, r["start"], r["slutt"], r["sett"]))
                finnes.add(r["prosjekt_id"])
                nye.append(str(cur.lastrowid))
        return nye

    # ---------- bestillinger ----------

    def bestillinger(self) -> dict[str, dict[str, dict]]:
        """Alle registrerte bestillinger: {gjennomføring-id: {leverandør-nøkkel: rad}}."""
        out: dict[str, dict[str, dict]] = {}
        with self.tx() as con:
            for r in con.execute("SELECT * FROM bestilling"):
                d = dict(r)
                d["linjer"] = json.loads(d["linjer"])
                out.setdefault(str(d["gjennomforing_id"]), {})[d["leverandor"]] = d
        return out

    def ny_bestilling(self, gid: str, key: str, navn: str, *, dato: str, ordrenr: str, sett: int | None,
                      linjer: list[dict]) -> None:
        with self.tx() as con:
            con.execute("""INSERT INTO bestilling (gjennomforing_id, leverandor, navn, status, bestilt_dato, ordrenr, sett, linjer)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (gid, key, navn, "bestilt", dato, ordrenr, sett, json.dumps(linjer, ensure_ascii=False)))

    def endre_bestilling(self, gid: str, key: str, **felt) -> None:
        if "linjer" in felt:
            felt["linjer"] = json.dumps(felt["linjer"], ensure_ascii=False)
        with self.tx() as con:
            cur = con.execute(f"UPDATE bestilling SET {', '.join(k + ' = ?' for k in felt)} "
                              "WHERE gjennomforing_id = ? AND leverandor = ?", (*felt.values(), gid, key))
            if not cur.rowcount:
                raise KeyError(key)

    def slett_bestilling(self, gid: str, key: str) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM bestilling WHERE gjennomforing_id = ? AND leverandor = ?", (gid, key))

    def slett(self, gid: str) -> None:
        """Slett gjennomføringen. Prosjektet slettes også hvis det ikke har flere."""
        g = self.gjennomforing(gid)
        with self.tx() as con:
            con.execute("DELETE FROM gjennomforing WHERE id = ?", (gid,))
            if not con.execute("SELECT 1 FROM gjennomforing WHERE prosjekt_id = ?", (g["prosjektId"],)).fetchone():
                con.execute("DELETE FROM prosjekt WHERE id = ?", (g["prosjektId"],))
