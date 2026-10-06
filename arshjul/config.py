"""Innstillinger fra miljøvariabler, så samme kode kan kjøre lokalt og på en server.

    ARSHJUL_DATA        datamappen (BOM/, Bestillingslister/, arshjul.db, .cache/).
                        Standard: mappen over arshjul/.
    ARSHJUL_BOM         BOM-mappen, hvis den ligger et annet sted enn i datamappen,
                        f.eks. på en nettverksmappe: \\\\server\\deling\\BOM.
                        Standard: <datamappen>/BOM.
    ARSHJUL_MODUS       «lokal» (standard) eller «server». I servermodus åpnes aldri
                        filer, mapper eller nettleser på maskinen appen kjører på.
    ARSHJUL_HOST        standard 127.0.0.1
    ARSHJUL_PORT        standard 5057
    ARSHJUL_SECRET_KEY  nøkkel som signerer innloggingen. Påkrevd i servermodus (minst 32 tegn).
                        Lokalt lages den selv og lagres i <datamappen>/.cache.
    ARSHJUL_BAK_PROXY   «på» når Caddy/nginx står foran (standard i servermodus).
    ARSHJUL_NO_OPEN     satt = ikke åpne filer eller nettleser (også i lokal modus)

Innstillingene kan også stå i arshjul/innstillinger.env, én per linje (NAVN=verdi,
# for kommentar). Miljøvariabler som er satt, går foran filen. Samme format som
systemd sin EnvironmentFile, så filen kan brukes på en server også.
"""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "innstillinger.env"


def les_innstillinger(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for linje in path.read_text(encoding="utf-8-sig").splitlines():
            linje = linje.strip()
            if linje and not linje.startswith("#") and "=" in linje:
                k, v = linje.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


for _k, _v in les_innstillinger(SETTINGS_FILE).items():
    os.environ.setdefault(_k, _v)

MODUS = os.environ.get("ARSHJUL_MODUS", "lokal").strip().lower()
if MODUS not in ("lokal", "server"):
    raise SystemExit(f"ARSHJUL_MODUS må være «lokal» eller «server», ikke «{MODUS}».")
LOKAL = MODUS == "lokal"

DATA_DIR = Path(os.environ.get("ARSHJUL_DATA") or APP_DIR.parent).resolve()
BOM_DIR = Path(os.environ.get("ARSHJUL_BOM") or DATA_DIR / "BOM")
LIST_DIR = DATA_DIR / "Bestillingslister"
DB_FILE = DATA_DIR / "arshjul.db"
DATA_FILE = DATA_DIR / "arshjul.json"  # gammelt format; importeres til databasen én gang
CACHE_DIR = DATA_DIR / ".cache"
SNAP_FILE = CACHE_DIR / "bom-sist-sett.json"

TEMPLATE_BOM = APP_DIR / "maler" / "bom-mal.xlsx"
TEMPLATE_LIST = APP_DIR / "maler" / "bestillingsliste.html"

HOST = os.environ.get("ARSHJUL_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARSHJUL_PORT", "5057"))

# åpne filer, mapper og nettleser på denne maskinen?
OPEN_LOCAL = LOKAL and not os.environ.get("ARSHJUL_NO_OPEN")

SECRET_KEY = os.environ.get("ARSHJUL_SECRET_KEY") or ""
if not SECRET_KEY:
    if not LOKAL:
        raise SystemExit("ARSHJUL_SECRET_KEY må settes i servermodus.")
    # lokalt: lag nøkkelen én gang og ta vare på den, så du forblir innlogget etter omstart
    _nokkel = CACHE_DIR / "hemmelig-nokkel.txt"
    if _nokkel.exists():
        SECRET_KEY = _nokkel.read_text(encoding="utf-8").strip()
    if len(SECRET_KEY) < 32:
        SECRET_KEY = secrets.token_hex(32)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _nokkel.write_text(SECRET_KEY, encoding="utf-8")
elif not LOKAL and len(SECRET_KEY) < 32:
    raise SystemExit("ARSHJUL_SECRET_KEY må være minst 32 tegn i servermodus.")

# hvor lenge du er logget inn: lenge på din egen PC, en arbeidsdag på serveren
OKT_TIMER = 24 * 30 if LOKAL else 12


def _av_paa(navn: str, standard: bool) -> bool:
    v = os.environ.get(navn, "").strip().lower()
    if not v:
        return standard
    if v not in ("på", "pa", "av", "1", "0", "ja", "nei"):
        raise SystemExit(f"{navn} må være «på» eller «av», ikke «{v}».")
    return v in ("på", "pa", "1", "ja")


# Innlogging er alltid på, også lokalt – det finnes ingen «superbruker» uten navn.
# (ARSHJUL_INNLOGGING fra en eldre innstillinger.env blir ikke lenger brukt.)

# Bak Caddy/nginx: stol på X-Forwarded-For/-Proto fra én proxy (HTTPS og klientens adresse).
BAK_PROXY = _av_paa("ARSHJUL_BAK_PROXY", standard=not LOKAL)


def move_old_cache() -> None:
    """Før lå cachen i arshjul/.cache. Flytt den til datamappen én gang."""
    old = APP_DIR / ".cache" / SNAP_FILE.name
    if old.exists() and not SNAP_FILE.exists() and old.resolve() != SNAP_FILE.resolve():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(SNAP_FILE))
