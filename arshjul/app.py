"""Årshjul for innkjøp – Flask-app som kjører lokalt eller på en server.

Datamappen (standard: mappen over denne, se config.py):
    BOM/                 Excel-filene, sjefen for komponentene
    Bestillingslister/   genererte bestillingslister (HTML)
    arshjul.db           prosjekter, gjennomføringer per skoleår og stengte perioder

Start med «Start årshjul.bat», eller:  python app.py   (utvikling: python app.py --debug)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import bom as bomlib
import config
import frister
from config import BOM_DIR, DATA_FILE, LIST_DIR, SNAP_FILE, TEMPLATE_BOM, TEMPLATE_LIST
from db import Db

FIELDS = {"navn": str, "type": str, "start": int, "slutt": int, "lev": int, "buf": int,
          "sett": int, "notat": str, "color": int, "bom": str}

app = Flask(__name__)
app.json.ensure_ascii = False
app.secret_key = config.SECRET_KEY
_lock = threading.Lock()
store = Db(config.DB_FILE)


def init() -> None:
    """Lag mappene og databasen (første gang importeres arshjul.json)."""
    BOM_DIR.mkdir(parents=True, exist_ok=True)
    LIST_DIR.mkdir(exist_ok=True)
    config.move_old_cache()
    store.init(DATA_FILE)


_ready = False


@app.before_request
def _ensure_init():
    """Også når appen startes av en annen server enn main() (f.eks. på en VPS)."""
    global _ready
    if not _ready:
        with _lock:
            if not _ready:
                init()
                _ready = True


# ---------- lagring av «sist sett» ----------

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return json.loads(json.dumps(default))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------- åpne filer og mapper ----------

def open_path(path: Path) -> None:
    if not config.OPEN_LOCAL:
        return
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', " ", s)
    return re.sub(r"\s+", " ", s).strip() or "Prosjekt"


# ---------- BOM-status ----------

_bom_cache: dict[str, tuple[float, dict]] = {}


def read_bom_cached(path: Path) -> dict:
    mtime = path.stat().st_mtime
    hit = _bom_cache.get(path.name)
    if hit and hit[0] == mtime:
        return hit[1]
    data = bomlib.read_bom(path)
    _bom_cache[path.name] = (mtime, data)
    return data


def bom_status(p: dict, snaps: dict) -> tuple[dict | None, bool]:
    """Returnerer (status, snaps_endret)."""
    fil = p.get("bom")
    if not fil:
        return None, False
    path = BOM_DIR / fil
    if not path.exists():
        return {"fil": fil, "finnes": False}, False
    try:
        b = read_bom_cached(path)
    except bomlib.BomError as e:
        return {"fil": fil, "finnes": True, "feil": str(e)}, False
    sett = p.get("sett") or b["sett"] or 0
    c = bomlib.calc(b, sett)
    changed = False
    snap = snaps.get(fil)
    if snap is None:
        snaps[fil] = {"rader": b["rader"], "lest": b["endret"]}
        changed, endringer, sist = True, [], b["endret"]
    else:
        endringer, sist = bomlib.diff(snap["rader"], b["rader"]), snap.get("lest", "")
    return {
        "fil": fil, "finnes": True, "linjer": c["linjer"], "bestill": c["bestill"], "eks": c["eks"],
        "inkl": c["inkl"], "endret": b["endret"], "settFil": b["sett"], "prosjektFil": b["prosjekt"],
        "endringer": endringer, "sistLest": sist,
    }, changed


def bom_path(g: dict) -> Path:
    path = BOM_DIR / (g.get("bom") or "")
    if not g.get("bom") or not path.exists():
        raise bomlib.BomError("Fant ikke BOM-filen i BOM-mappen.")
    return path


# ---------- API ----------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def state():
    closed = store.stengte()
    rader = store.gjennomforinger()
    with _lock:
        snaps = _read_json(SNAP_FILE, {})
        dirty = False
        out = []
        for g in rader:
            st, ch = bom_status(g, snaps)
            dirty |= ch
            f = frister.frister(g, closed)
            out.append({**g, "semester": frister.semester(g["start"]), "hendelser": f.pop("hendelser"),
                        "frister": f, "bomStatus": st})
        if dirty:
            _write_json(SNAP_FILE, snaps)

    idag = dt.date.today()
    na = frister.skolear_for(idag)
    ar = {na, na + 1} | {g["skolear"] for g in rader}
    skolear = [{"ar": y, "navn": frister.skolear_navn(y), "start": frister.skolear_start(y).isoformat(),
                "uker": frister.skolear_uker(y, closed)} for y in range(min(ar), max(ar) + 1)]
    mandag = idag - dt.timedelta(days=idag.weekday())
    neste8 = [frister.uke_info(mandag + i * frister.UKE, closed) for i in range(8)]

    linked = {g.get("bom") for g in rader}
    ledige = sorted(f.name for f in BOM_DIR.glob("*.xlsx") if not f.name.startswith("~$") and f.name not in linked) \
        if BOM_DIR.exists() else []
    return jsonify({"prosjekter": out, "stengte": closed, "ledigeBom": ledige, "modus": config.MODUS,
                    "idag": frister.uke_info(idag), "skolearNa": na, "skolear": skolear, "neste8": neste8,
                    "mapper": {"bom": str(BOM_DIR), "lister": str(LIST_DIR)}})


def _clean(body: dict, partial: bool) -> dict:
    out = {}
    for k, typ in FIELDS.items():
        if k not in body:
            continue
        v = body[k]
        if v in (None, "") and k in ("sett", "bom", "notat"):
            out[k] = None if k != "notat" else ""
            continue
        try:
            out[k] = typ(v)
        except (TypeError, ValueError):
            raise bomlib.BomError(f"Ugyldig verdi for {k}.")
    for k, lo, hi in (("start", 1, 53), ("slutt", 1, 53), ("lev", 0, 20), ("buf", 0, 20), ("sett", 0, 999)):
        if out.get(k) is not None:
            out[k] = min(hi, max(lo, out[k]))
    if "type" in out and out["type"] not in ("hack", "sett"):
        raise bomlib.BomError("Ugyldig type.")
    if "navn" in out:
        out["navn"] = out["navn"].strip()
        if not out["navn"]:
            raise bomlib.BomError("Gi prosjektet et navn.")
    elif not partial:
        raise bomlib.BomError("Gi prosjektet et navn.")
    return out


@app.errorhandler(bomlib.BomError)
def bom_error(e):
    return jsonify({"feil": str(e)}), 400


@app.errorhandler(KeyError)
def not_found(e):
    return jsonify({"feil": "Fant ikke prosjektet."}), 404


@app.post("/api/gjennomforinger")
def create():
    raw = request.get_json(force=True)
    body = _clean(raw, partial=False)
    try:
        sy = int(raw.get("skolear") or frister.skolear_for(dt.date.today()))
    except (TypeError, ValueError):
        raise bomlib.BomError("Ugyldig skoleår.")
    if not 2000 <= sy <= 2100:
        raise bomlib.BomError("Ugyldig skoleår.")
    slug = re.sub(r"[^a-z0-9]+", "-", body["navn"].lower()).strip("-")[:24] or "prosjekt"
    return jsonify(store.ny(f"{slug}-{secrets.token_hex(2)}", sy, body))


@app.patch("/api/gjennomforinger/<gid>")
def update(gid):
    return jsonify(store.endre(gid, _clean(request.get_json(force=True), partial=True)))


@app.delete("/api/gjennomforinger/<gid>")
def delete(gid):
    store.slett(gid)
    return jsonify({"ok": True})


@app.post("/api/gjennomforinger/<gid>/mal")
def make_template(gid):
    g = store.gjennomforing(gid)
    frist = dt.date.fromisoformat(frister.frister(g, store.stengte())["frist"]["dato"])
    target = BOM_DIR / f"{safe_name(g['navn'])} – BOM.xlsx"
    BOM_DIR.mkdir(exist_ok=True)
    bomlib.make_template(TEMPLATE_BOM, target, prosjekt=g["navn"], dato=dt.date.today(), frist=frist, sett=g.get("sett"))
    store.endre(gid, {"bom": target.name})
    open_path(target)
    return jsonify({"fil": target.name})


def only_local() -> None:
    if not config.LOKAL:
        raise bomlib.BomError("Dette fungerer bare når årshjulet kjører på din egen PC.")


@app.post("/api/gjennomforinger/<gid>/apne")
def open_bom(gid):
    only_local()
    open_path(bom_path(store.gjennomforing(gid)))
    return jsonify({"ok": True})


@app.post("/api/gjennomforinger/<gid>/liste")
def order_list(gid):
    g = store.gjennomforing(gid)
    path = bom_path(g)
    b = bomlib.read_bom(path)
    sett = g.get("sett") or b["sett"] or 0
    c = bomlib.calc(b, sett)
    LIST_DIR.mkdir(exist_ok=True)
    target = LIST_DIR / f"Bestilling – {safe_name(b['prosjekt'] or g['navn'])}.html"
    bomlib.make_order_list(TEMPLATE_LIST, target, bom=b, beregnet=c, prosjekt=g["navn"], sett=sett,
                           list_id=f"{g['prosjektId']}-{g['id']}-{int(path.stat().st_mtime)}")
    open_path(target)
    return jsonify({"fil": target.name, "linjer": c["bestill"]})


@app.post("/api/gjennomforinger/<gid>/sett")
def acknowledge(gid):
    """Marker endringene i BOM-en som sett."""
    g = store.gjennomforing(gid)
    b = bomlib.read_bom(bom_path(g))
    with _lock:
        snaps = _read_json(SNAP_FILE, {})
        snaps[g["bom"]] = {"rader": b["rader"], "lest": b["endret"]}
        _write_json(SNAP_FILE, snaps)
    return jsonify({"ok": True})


@app.put("/api/stengte")
def set_closed():
    body = request.get_json(force=True)
    if not isinstance(body, list) or len(body) > 20:
        raise bomlib.BomError("Ugyldige stengte perioder.")
    out = []
    for c in body:
        try:
            navn, fra, til = str(c["navn"]).strip(), int(c["fra"]), int(c["til"])
        except (KeyError, TypeError, ValueError):
            raise bomlib.BomError("Ugyldig stengt periode.")
        if not navn or not (1 <= fra <= 53 and 1 <= til <= 53):
            raise bomlib.BomError("En stengt periode trenger navn og uker mellom 1 og 53.")
        out.append({"navn": navn, "fra": fra, "til": til})
    store.sett_stengte(out)
    return jsonify(out)


@app.post("/api/mappe/<hvilken>")
def open_folder(hvilken):
    only_local()
    path = {"bom": BOM_DIR, "lister": LIST_DIR}.get(hvilken)
    if not path:
        raise bomlib.BomError("Ukjent mappe.")
    path.mkdir(exist_ok=True)
    open_path(path)
    return jsonify({"ok": True})


# ---------- oppstart ----------

def main() -> None:
    debug = "--debug" in sys.argv
    url = f"http://127.0.0.1:{config.PORT}/"
    # Flask sin omlaster starter skriptet to ganger; bare første runde sjekker og åpner nettleseren
    first_run = not os.environ.get("WERKZEUG_RUN_MAIN")
    if first_run:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", config.PORT)) == 0:  # kjører allerede
                print("Årshjulet kjører allerede – åpner nettleseren.")
                if config.OPEN_LOCAL:
                    webbrowser.open(url)
                return
    global _ready
    init()
    _ready = True
    if first_run:
        if config.OPEN_LOCAL:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"Årshjulet kjører på {url}  (data: {config.DATA_DIR}, modus: {config.MODUS})")
        if config.LOKAL:
            print("Lukk dette vinduet for å stoppe.")
    if debug:
        app.run(host=config.HOST, port=config.PORT, debug=True)
    else:
        from waitress import serve
        serve(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
