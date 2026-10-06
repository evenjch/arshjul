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

import bestilling
import bom as bomlib
import config
import frister
import telling
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


def bom_status(p: dict, snaps: dict) -> tuple[dict | None, bool, dict | None]:
    """Returnerer (status, snaps_endret, utregning)."""
    fil = p.get("bom")
    if not fil:
        return None, False, None
    path = BOM_DIR / fil
    if not path.exists():
        return {"fil": fil, "finnes": False}, False, None
    try:
        b = read_bom_cached(path)
    except bomlib.BomError as e:
        return {"fil": fil, "finnes": True, "feil": str(e)}, False, None
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
    }, changed, c


def telling_status(g: dict, c: dict | None, st: dict | None, lagret: dict, tell: dict | None,
                   typer: dict) -> dict | None:
    """Lagret telling, eller grunnlaget for en ny (fra BOM-en slik den er nå)."""
    if tell:
        return {"lagret": {**tell, **telling.oppsummering(tell["linjer"])}, "grunnlag": None, "antattBestilt": False}
    if not c or not c["linjer"]:
        return None
    sett = g.get("sett") or (st or {}).get("settFil") or 0
    grunnlag = telling.grunnlag(c["rader"], telling.bestilt_per_linje(lagret), typer.get(g["prosjektId"], {}), sett)
    return {"lagret": None, "grunnlag": grunnlag, "antattBestilt": any(l["antatt"] for l in grunnlag)}


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
    bestilt = store.bestillinger()
    tellinger = store.tellinger()
    typer = store.linjetyper()
    with _lock:
        snaps = _read_json(SNAP_FILE, {})
        dirty = False
        out = []
        for g in rader:
            st, ch, c = bom_status(g, snaps)
            dirty |= ch
            f = frister.frister(g, closed)
            lagret = bestilt.get(g["id"], {})
            best = bestilling.oversikt(c["rader"], lagret) if c and c["linjer"] else None
            out.append({**g, "semester": frister.semester(g["start"]), "hendelser": f.pop("hendelser"),
                        "frister": f, "bomStatus": st, "bestilling": best,
                        "bestiltKr": bestilling.kostnad(lagret),
                        "telling": telling_status(g, c, st, lagret, tellinger.get(g["id"]), typer)})
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
                    "oppsett": frister.oppsett_varsel(rader, closed, idag),
                    "mapper": {"bom": str(BOM_DIR), "lister": str(LIST_DIR)}})


@app.post("/api/skolear/<int:fra>/kopier")
def copy_year(fra):
    """Kopier gjennomføringer til neste skoleår. «ids» velger hvilke (standard: alle)."""
    ids = (request.get_json(silent=True) or {}).get("ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, (str, int)) for i in ids)):
        raise bomlib.BomError("Ugyldig liste over prosjekter.")
    nye = store.kopier(fra, [str(i) for i in ids] if ids is not None else None)
    return jsonify({"nye": nye, "til": fra + 1, "navn": frister.skolear_navn(fra + 1)})


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


def beregn(g: dict) -> tuple[dict, dict, int, Path]:
    """(bom, utregning, sett, sti) for en gjennomføring."""
    path = bom_path(g)
    b = bomlib.read_bom(path)
    sett = g.get("sett") or b["sett"] or 0
    return b, bomlib.calc(b, sett), sett, path


@app.post("/api/gjennomforinger/<gid>/liste")
def order_list(gid):
    """Bestillingsliste for hele BOM-en, eller bare én leverandør hvis «leverandor» er gitt."""
    g = store.gjennomforing(gid)
    b, c, sett, path = beregn(g)
    navn = safe_name(b["prosjekt"] or g["navn"])
    lev = (request.get_json(silent=True) or {}).get("leverandor")
    if lev is not None:
        gr = bestilling.gruppe(c["rader"], bestilling.lev_key(lev))
        if gr is None:
            raise bomlib.BomError("Fant ikke leverandøren i BOM-en.")
        eks = round(sum(r["total"] for r in gr["rader"]), 2)
        c = {"rader": gr["rader"], "linjer": len(gr["rader"]), "bestill": sum(1 for r in gr["rader"] if r["bestill"] > 0),
             "eks": eks, "inkl": round(eks * (1 + b["mva"]), 2)}
        navn += " – " + safe_name(gr["navn"])
    LIST_DIR.mkdir(exist_ok=True)
    target = LIST_DIR / f"Bestilling – {navn}.html"
    bomlib.make_order_list(TEMPLATE_LIST, target, bom=b, beregnet=c, prosjekt=g["navn"], sett=sett,
                           list_id=f"{g['prosjektId']}-{g['id']}-{int(path.stat().st_mtime)}")
    open_path(target)
    return jsonify({"fil": target.name, "linjer": c["bestill"]})


@app.post("/api/gjennomforinger/<gid>/bestilling")
def order_status(gid):
    """Registrer bestilling hos én leverandør.

    handling: «bestilt» (ny, fryser linjene), «tillegg» (bestilt det som manglet),
              «status» (bestilt / delvis / mottatt), «ordrenr», «angre» (fjern registreringen)
    """
    body = request.get_json(force=True) or {}
    handling = body.get("handling")
    key = bestilling.lev_key(body.get("leverandor"))
    g = store.gjennomforing(gid)
    lagret = store.bestillinger().get(gid, {}).get(key)
    ordrenr = str(body.get("ordrenr") or "").strip()[:100]

    if handling in ("bestilt", "tillegg"):
        _, c, sett, _ = beregn(g)
        gr = bestilling.gruppe(c["rader"], key)
        linjer = bestilling.frys(gr["rader"]) if gr else []
        if handling == "bestilt":
            if lagret:
                raise bomlib.BomError("Denne leverandøren er allerede registrert som bestilt.")
            if not linjer:
                raise bomlib.BomError("Det er ingenting å bestille hos denne leverandøren.")
            dato = body.get("dato") or dt.date.today().isoformat()
            try:
                dt.date.fromisoformat(dato)
            except (TypeError, ValueError):
                raise bomlib.BomError("Ugyldig dato.")
            store.ny_bestilling(gid, key, gr["navn"], dato=dato, ordrenr=ordrenr, sett=sett, linjer=linjer)
        else:
            if not lagret:
                raise bomlib.BomError("Registrer først den vanlige bestillingen.")
            felt = {"linjer": bestilling.slaa_sammen(lagret["linjer"], linjer), "status": "bestilt",
                    "mottatt_dato": None, "sett": sett}
            if ordrenr:
                felt["ordrenr"] = f"{lagret['ordrenr']} + {ordrenr}" if lagret["ordrenr"] else ordrenr
            store.endre_bestilling(gid, key, **felt)
    elif handling == "status":
        status = body.get("status")
        if status not in bestilling.LAGRET_STATUS:
            raise bomlib.BomError("Ugyldig status.")
        if not lagret:
            raise bomlib.BomError("Leverandøren er ikke registrert som bestilt.")
        mottatt = lagret["mottatt_dato"] or dt.date.today().isoformat()
        store.endre_bestilling(gid, key, status=status, mottatt_dato=None if status == "bestilt" else mottatt)
    elif handling == "ordrenr":
        if not lagret:
            raise bomlib.BomError("Leverandøren er ikke registrert som bestilt.")
        store.endre_bestilling(gid, key, ordrenr=ordrenr)
    elif handling == "angre":
        store.slett_bestilling(gid, key)
    else:
        raise bomlib.BomError("Ukjent handling.")
    return jsonify({"ok": True})


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


@app.post("/api/gjennomforinger/<gid>/telling")
def save_count(gid):
    """Lagre tellingen. linjer: [{key, type, telt}]. Tallene før prosjektet hentes fra BOM-en nå."""
    g = store.gjennomforing(gid)
    gammel = store.tellinger().get(gid)
    if gammel and gammel["skrevet"]:
        raise bomlib.BomError("Tellingen er allerede skrevet til BOM-en og kan ikke endres.")
    body = request.get_json(force=True) or {}
    try:
        innspill = {str(l["key"]): {"type": l.get("type", "utstyr"), "telt": l["telt"]}
                    for l in body.get("linjer", []) if l.get("telt") not in (None, "")}
    except (TypeError, KeyError, AttributeError):
        raise bomlib.BomError("Ugyldig telling.")
    if not innspill:
        raise bomlib.BomError("Fyll inn minst én linje.")
    if gammel:  # endre en lagret telling: behold tallene den ble laget fra
        grunnlag = gammel["linjer"]
    else:
        b, c, sett, _ = beregn(g)
        grunnlag = telling.grunnlag(c["rader"], telling.bestilt_per_linje(store.bestillinger().get(gid, {})),
                                    store.linjetyper().get(g["prosjektId"], {}), sett)
    try:
        linjer = telling.lag(grunnlag, innspill)
    except (TypeError, ValueError) as e:
        raise bomlib.BomError(f"Ugyldig telling: {e}")
    store.lagre_telling(gid, g["prosjektId"], dt.date.today().isoformat(), linjer)
    return jsonify(telling.oppsummering(linjer))


@app.post("/api/gjennomforinger/<gid>/telling/skriv")
def write_count(gid):
    """Skriv de telte tallene inn i «På lager» i BOM-en (etter bekreftelse i nettleseren)."""
    g = store.gjennomforing(gid)
    tell = store.tellinger().get(gid)
    if not tell:
        raise bomlib.BomError("Lagre tellingen først.")
    if tell["skrevet"]:
        raise bomlib.BomError("Tellingen er allerede skrevet til BOM-en.")
    path = bom_path(g)
    with _lock:
        res = bomlib.write_stock(path, {l["key"]: l["telt"] for l in tell["linjer"]}, BOM_DIR / "Sikkerhetskopi")
        # vår egen endring skal ikke dukke opp som «BOM-en er endret»
        snaps = _read_json(SNAP_FILE, {})
        if g["bom"] in snaps:
            nye = {l["key"]: l["telt"] for l in tell["linjer"] if l["key"] in res["skrevet"]}
            for r in snaps[g["bom"]]["rader"]:
                if bomlib.rad_key(r) in nye:
                    r["lager"] = nye[bomlib.rad_key(r)]
            _write_json(SNAP_FILE, snaps)
    store.telling_skrevet(gid, dt.date.today().isoformat(), res["backup"])
    return jsonify(res)


@app.delete("/api/gjennomforinger/<gid>/telling")
def delete_count(gid):
    store.gjennomforing(gid)
    tell = store.tellinger().get(gid)
    if tell and tell["skrevet"]:
        raise bomlib.BomError("Tellingen er skrevet til BOM-en og kan ikke slettes. Bruk sikkerhetskopien om noe ble feil.")
    store.slett_telling(gid)
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
        print(f"Årshjulet kjører på {url}  (modus: {config.MODUS})")
        print(f"  Data:      {config.DATA_DIR}")
        print(f"  BOM-mappe: {BOM_DIR}")
        if config.SETTINGS_FILE.exists():
            print(f"  Innstillinger fra {config.SETTINGS_FILE.name}")
        if config.LOKAL:
            print("Lukk dette vinduet for å stoppe.")
    if debug:
        app.run(host=config.HOST, port=config.PORT, debug=True)
    else:
        from waitress import serve
        serve(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
