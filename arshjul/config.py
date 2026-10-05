"""Innstillinger fra miljøvariabler, så samme kode kan kjøre lokalt og på en server.

    ARSHJUL_DATA        datamappen (BOM/, Bestillingslister/, arshjul.db, .cache/).
                        Standard: mappen over arshjul/.
    ARSHJUL_MODUS       «lokal» (standard) eller «server». I servermodus åpnes aldri
                        filer, mapper eller nettleser på maskinen appen kjører på.
    ARSHJUL_HOST        standard 127.0.0.1
    ARSHJUL_PORT        standard 5057
    ARSHJUL_SECRET_KEY  nøkkel for innlogging senere. Påkrevd i servermodus.
    ARSHJUL_NO_OPEN     satt = ikke åpne filer eller nettleser (også i lokal modus)
"""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

MODUS = os.environ.get("ARSHJUL_MODUS", "lokal").strip().lower()
if MODUS not in ("lokal", "server"):
    raise SystemExit(f"ARSHJUL_MODUS må være «lokal» eller «server», ikke «{MODUS}».")
LOKAL = MODUS == "lokal"

DATA_DIR = Path(os.environ.get("ARSHJUL_DATA") or APP_DIR.parent).resolve()
BOM_DIR = DATA_DIR / "BOM"
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
    SECRET_KEY = secrets.token_hex(32)


def move_old_cache() -> None:
    """Før lå cachen i arshjul/.cache. Flytt den til datamappen én gang."""
    old = APP_DIR / ".cache" / SNAP_FILE.name
    if old.exists() and not SNAP_FILE.exists() and old.resolve() != SNAP_FILE.resolve():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(SNAP_FILE))
