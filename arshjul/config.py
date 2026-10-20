"""Settings from environment variables, so the same code can run locally and on a server.

    ARSHJUL_DATA        the data folder (BOM/, Bestillingslister/, arshjul.db, .cache/).
                        Default: the folder above arshjul/.
    ARSHJUL_BOM         the BOM folder, if it's somewhere other than the data folder,
                        e.g. on a network share: \\\\server\\deling\\BOM.
                        Default: <data folder>/BOM.
    ARSHJUL_MODUS       «lokal» (default) or «server». In server mode, files, folders and the
                        browser are never opened on the machine the app runs on.
    ARSHJUL_HOST        default 127.0.0.1
    ARSHJUL_PORT        default 5057
    ARSHJUL_SECRET_KEY  the key that signs the login. Required in server mode (at least 32 characters).
                        Locally it's created automatically and stored in <data folder>/.cache.
    ARSHJUL_BAK_PROXY   «på» when Caddy/nginx is in front (the default in server mode).
    ARSHJUL_NO_OPEN     set = don't open files or the browser (in local mode too)

The settings can also be in arshjul/innstillinger.env, one per line (NAME=value,
# for a comment). Environment variables that are set take precedence over the file. Same format as
systemd's EnvironmentFile, so the file can be used on a server too.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "innstillinger.env"


def read_settings(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


for _k, _v in read_settings(SETTINGS_FILE).items():
    os.environ.setdefault(_k, _v)

MODE = os.environ.get("ARSHJUL_MODUS", "lokal").strip().lower()
if MODE not in ("lokal", "server"):
    raise SystemExit(f"ARSHJUL_MODUS må være «lokal» eller «server», ikke «{MODE}».")
LOCAL = MODE == "lokal"

DATA_DIR = Path(os.environ.get("ARSHJUL_DATA") or APP_DIR.parent).resolve()
BOM_DIR = Path(os.environ.get("ARSHJUL_BOM") or DATA_DIR / "BOM")
LIST_DIR = DATA_DIR / "Bestillingslister"
DB_FILE = DATA_DIR / "arshjul.db"
DATA_FILE = DATA_DIR / "arshjul.json"  # old format; imported into the database once
CACHE_DIR = DATA_DIR / ".cache"
SNAP_FILE = CACHE_DIR / "bom-last-seen.json"  # how each BOM looked when the changes were last acknowledged
OLD_SNAP_FILE = CACHE_DIR / "bom-sist-sett.json"  # the same, from before the code was in English

TEMPLATE_BOM = APP_DIR / "maler" / "bom-mal.xlsx"
TEMPLATE_LIST = APP_DIR / "maler" / "bestillingsliste.html"

HOST = os.environ.get("ARSHJUL_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARSHJUL_PORT", "5057"))

# open files, folders and the browser on this machine?
OPEN_LOCAL = LOCAL and not os.environ.get("ARSHJUL_NO_OPEN")

SECRET_KEY = os.environ.get("ARSHJUL_SECRET_KEY") or ""
if not SECRET_KEY:
    if not LOCAL:
        raise SystemExit("ARSHJUL_SECRET_KEY må settes i servermodus.")
    # locally: create the key once and keep it, so you stay logged in after a restart
    _key = CACHE_DIR / "hemmelig-nokkel.txt"
    if _key.exists():
        SECRET_KEY = _key.read_text(encoding="utf-8").strip()
    if len(SECRET_KEY) < 32:
        SECRET_KEY = secrets.token_hex(32)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _key.write_text(SECRET_KEY, encoding="utf-8")
elif not LOCAL and len(SECRET_KEY) < 32:
    raise SystemExit("ARSHJUL_SECRET_KEY må være minst 32 tegn i servermodus.")

# how long you stay logged in: long on your own PC, a working day on the server
SESSION_HOURS = 24 * 30 if LOCAL else 12


def _on_off(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    if v not in ("på", "pa", "av", "1", "0", "ja", "nei"):
        raise SystemExit(f"{name} må være «på» eller «av», ikke «{v}».")
    return v in ("på", "pa", "1", "ja")


# Login is always on, locally too – there's no nameless "superuser".
# (ARSHJUL_INNLOGGING from an older innstillinger.env is no longer used.)

# Behind Caddy/nginx: trust X-Forwarded-For/-Proto from one proxy (HTTPS and the client's address).
BEHIND_PROXY = _on_off("ARSHJUL_BAK_PROXY", default=not LOCAL)


def move_old_cache() -> None:
    """The cache used to live in arshjul/.cache. Move it to the data folder once."""
    old = APP_DIR / ".cache" / OLD_SNAP_FILE.name
    if old.exists() and not OLD_SNAP_FILE.exists() and old.resolve() != OLD_SNAP_FILE.resolve():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(OLD_SNAP_FILE))


_OLD_ROW_KEYS = {"beskrivelse": "description", "kategori": "category", "leverandor": "supplier",
                 "varenummer": "part_number", "prSett": "perSet", "lager": "in_stock", "pris": "price",
                 "kommentar": "comment"}


def convert_old_snapshot() -> None:
    """The "BOM last seen" file from before the code was in English has Norwegian keys. Convert it once, so
    changes nobody has acknowledged yet don't disappear (and every BOM doesn't look new)."""
    if not OLD_SNAP_FILE.exists() or SNAP_FILE.exists():
        return
    old = json.loads(OLD_SNAP_FILE.read_text(encoding="utf-8"))
    new = {name: {"rows": [{_OLD_ROW_KEYS.get(k, k): v for k, v in r.items()} for r in s.get("rader", [])],
                  "read": s.get("lest", "")} for name, s in old.items()}
    SNAP_FILE.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    OLD_SNAP_FILE.rename(OLD_SNAP_FILE.with_name(OLD_SNAP_FILE.name + ".konvertert"))
