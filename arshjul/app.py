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
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, g as req, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import bestilling
import bom as bomlib
import config
import frister
import telling
from config import BOM_DIR, DATA_FILE, LIST_DIR, SNAP_FILE, TEMPLATE_BOM, TEMPLATE_LIST
from db import Db

FIELDS = {"navn": str, "type": str, "start": int, "slutt": int, "lev": int, "buf": int,
          "sett": int, "notat": str, "color": int, "bom": str, "emne": int}
# det en faglærer kan endre på sine egne prosjekter (ingeniøren eier leveringstid, buffer, BOM, farge og emne)
FAGLARER_FELT = {"navn", "type", "start", "slutt", "sett", "notat"}
# endepunktene en faglærer kan bruke – alt annet er bare for ingeniører
FAGLARER_TILLATT = {"index", "state", "create", "update", "delete", "create_subject", "update_subject",
                    "acknowledge_mine", "auth.login", "auth.logout", "static"}
PROGRAM = {"bachelor": "Bachelor", "master": "Master"}
# bare for administratoren
ADMIN_ENDEPUNKT = {"create_user", "update_user"}

app = Flask(__name__)
app.json.ensure_ascii = False
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_NAME="arshjul",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not config.LOKAL,  # bare over HTTPS på serveren
    PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=config.OKT_TIMER),
)
if config.BAK_PROXY:
    # Caddy/nginx foran: riktig klientadresse (låsing per IP) og https-adresser
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
_lock = threading.Lock()
store = Db(config.DB_FILE)


class Nektet(Exception):
    """Innlogget, men ikke lov (403)."""


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


auth.init(app, store)  # etter _ensure_init: innloggingen trenger databasen


@app.before_request
def _rolle():
    if req.bruker and not auth.er_ingenior() and request.endpoint not in FAGLARER_TILLATT:
        if request.path.startswith("/api/"):
            return jsonify({"feil": "Bare ingeniøren kan gjøre dette."}), 403
        return "Ikke tilgang", 403
    if req.bruker and request.endpoint in ADMIN_ENDEPUNKT and not auth.er_admin():
        return jsonify({"feil": "Bare administratoren kan gjøre dette."}), 403
    return None


def kan_handle(g: dict) -> bool:
    """Får den innloggede gjøre noe med denne gjennomføringen?

    Administrator: alt.
    Ingeniør: prosjekter i emner der hen er ansvarlig, og prosjekter uten emne eller uten
    ansvarlig ingeniør (så ingenting blir liggende).
    Faglærer: prosjekter i egne emner.
    """
    meg = auth.meg()
    if auth.er_admin():
        return True
    if auth.er_ingenior():
        return g["ingeniorId"] in (None, meg["id"])
    return g["faglarerId"] is not None and g["faglarerId"] == meg["id"]


@app.before_request
def _eierskap():
    """Alt som endrer en bestemt gjennomføring, krever at den er din (se kan_handle)."""
    gid = (request.view_args or {}).get("gid")
    if gid is None or request.method in ("GET", "HEAD", "OPTIONS") or req.bruker is None:
        return None
    try:
        g = store.gjennomforing(gid)
    except KeyError:
        if request.endpoint == "acknowledge_changes" and auth.er_ingenior():
            return None  # varsler om et prosjekt som er slettet, kan alltid kvitteres
        return jsonify({"feil": "Fant ikke prosjektet."}), 404
    if not kan_handle(g):
        if auth.er_ingenior():
            hvem = g["ingeniorNavn"] or "en annen ingeniør"
            return jsonify({"feil": f"Prosjektet hører til {g['emneKode']}, som {hvem} har ansvaret for."}), 403
        return jsonify({"feil": "Du kan bare endre prosjekter i dine egne emner."}), 403
    return None


@app.after_request
def _sikkerhet(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; script-src 'self'; img-src 'self' data:; "
                            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                            "font-src https://fonts.gstatic.com; frame-ancestors 'none'; form-action 'self'")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.errorhandler(Nektet)
def nektet(e):
    return jsonify({"feil": str(e)}), 403


# ---------- endringslogg ----------

FELTNAVN = {"navn": "navn", "type": "type", "start": "uke fra", "slutt": "uke til", "sett": "antall sett",
            "notat": "notat", "lev": "leveringstid", "buf": "buffer", "bom": "BOM", "color": "farge",
            "emneId": "emne"}


def logg(g: dict, handling: str, endret: dict | None = None) -> None:
    """Skriv i endringsloggen. endret: {felt: (fra, til)}. Bare faglærernes endringer varsles."""
    meg = auth.meg()
    felles = {"bruker_id": meg["id"], "bruker_navn": meg["navn"], "rolle": meg["rolle"],
              "gjennomforing_id": int(g["id"]), "prosjekt_navn": g["navn"], "skolear": g["skolear"], "handling": handling}
    if endret:
        store.logg([{**felles, "felt": FELTNAVN.get(k, k), "fra": fra, "til": til} for k, (fra, til) in endret.items()])
    else:
        store.logg([felles])


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
        lagret_ = {**tell, **telling.oppsummering(tell["linjer"]),
                   "ventende": len(telling.ventende(tell["linjer"], tell["skrevet_verdier"])),
                   "iBom": len(tell["skrevet_verdier"]),
                   "endretIExcel": telling.endret_i_bom(tell["skrevet_verdier"], c["rader"] if c else [])}
        return {"lagret": lagret_, "grunnlag": None, "antattBestilt": False}
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


# det en faglærer får se av en gjennomføring – ikke BOM, bestilling, telling eller kostnader
_OFFENTLIG = ("id", "prosjektId", "skolear", "start", "slutt", "sett", "navn", "type", "lev", "buf", "notat",
              "color", "emneId", "emneKode", "emneNavn", "program", "faglarerId", "faglarerNavn",
              "ingeniorId", "ingeniorNavn")


@app.get("/api/state")
def state():
    closed = store.stengte()
    # Du ser bare det du er involvert i: faglærer egne emner, ingeniør egne emner (og prosjekter
    # uten emne/ingeniør), administrator alt. Resten sendes ikke engang til nettleseren.
    alle = [g for g in store.gjennomforinger() if kan_handle(g)]
    rader = [g for g in alle if not g["slettet"]]
    ing = auth.er_ingenior()
    meg = auth.meg()
    out = []
    if ing:
        bestilt = store.bestillinger()
        tellinger = store.tellinger()
        typer = store.linjetyper()
        with _lock:
            snaps = _read_json(SNAP_FILE, {})
            dirty = False
            for g in rader:
                st, ch, c = bom_status(g, snaps)
                dirty |= ch
                f = frister.frister(g, closed)
                lagret = bestilt.get(g["id"], {})
                # «trenger mer» gir bare mening før prosjektet starter og før det er telt
                sammenlign = f["start"]["dato"] > dt.date.today().isoformat() and g["id"] not in tellinger
                best = bestilling.oversikt(c["rader"], lagret, sammenlign) if c and c["linjer"] else None
                out.append({**g, "semester": frister.semester(g["start"]), "hendelser": f.pop("hendelser"),
                            "frister": f, "bomStatus": st, "bestilling": best,
                            "bestiltKr": bestilling.kostnad(lagret), "egen": kan_handle(g),
                            "telling": telling_status(g, c, st, lagret, tellinger.get(g["id"]), typer)})
            if dirty:
                _write_json(SNAP_FILE, snaps)
    else:
        for g in rader:
            f = frister.frister(g, closed)
            out.append({**{k: g[k] for k in _OFFENTLIG}, "semester": frister.semester(g["start"]),
                        "hendelser": f.pop("hendelser"), "frister": f, "egen": kan_handle(g)})

    idag = dt.date.today()
    na = frister.skolear_for(idag)
    ar = {na, na + 1} | {g["skolear"] for g in rader}
    skolear = [{"ar": y, "navn": frister.skolear_navn(y), "start": frister.skolear_start(y).isoformat(),
                "uker": frister.skolear_uker(y, closed)} for y in range(min(ar), max(ar) + 1)]
    mandag = idag - dt.timedelta(days=idag.weekday())
    neste8 = [frister.uke_info(mandag + i * frister.UKE, closed) for i in range(8)]

    svar = {"prosjekter": out, "stengte": closed, "modus": config.MODUS,
            "idag": frister.uke_info(idag), "skolearNa": na, "skolear": skolear, "neste8": neste8,
            "meg": {"id": meg["id"], "navn": meg["navn"], "rolle": meg["rolle"], "admin": meg["admin"]},
            "brukere": [],
            "ledigeBom": [], "oppsett": None, "mapper": {}, "endringer": [], "slettede": [],
            "emner": [], "faglarere": [], "ingeniorer": [], "program": PROGRAM}
    emner = store.emner()
    if ing:
        mine = [g for g in alle if kan_handle(g)]  # varsler og påminnelser gjelder egne emner
        mine_id = {int(g["id"]) for g in mine}
        finnes = {int(g["id"]) for g in store.gjennomforinger()}  # alle, også andres – for slettede prosjekter
        linked = {g.get("bom") for g in store.gjennomforinger()}  # også andres – en BOM hører til ett prosjekt
        svar["ledigeBom"] = sorted(f.name for f in BOM_DIR.glob("*.xlsx")
                                   if not f.name.startswith("~$") and f.name not in linked) if BOM_DIR.exists() else []
        svar["oppsett"] = frister.oppsett_varsel([g for g in mine if not g["slettet"]], closed, idag)
        svar["mapper"] = {"bom": str(BOM_DIR), "lister": str(LIST_DIR)}
        def mitt(e):
            if e["mottaker_id"] is not None:  # varsel til én bestemt (f.eks. «du er valgt som ingeniør»)
                return e["mottaker_id"] == meg["id"]
            # faglærerens endringer i et prosjekt: emnets ingeniør (en ny ingeniør overtar de ukvitterte)
            return e["gjennomforing_id"] in mine_id or e["gjennomforing_id"] not in finnes
        svar["endringer"] = [e for e in store.endringer() if mitt(e)]
        svar["slettede"] = [{k: g[k] for k in ("id", "navn", "skolear", "start", "slutt", "slettet", "slettetAv", "emneKode")}
                            for g in mine if g["slettet"]]
        brukere = [u for u in store.brukere() if u["aktiv"]]
        svar["faglarere"] = [{"id": u["id"], "navn": u["navn"]} for u in brukere if u["rolle"] == "faglarer"]
        svar["ingeniorer"] = [{"id": u["id"], "navn": u["navn"]} for u in brukere if u["rolle"] == "ingenior"]
        svar["emner"] = emner
        if auth.er_admin():
            svar["brukere"] = [{**u, "admin": bool(u["admin"]), "aktiv": bool(u["aktiv"])} for u in store.brukere()]
    else:
        # faglæreren styrer sine egne emner og velger ingeniør til dem
        svar["emner"] = [e for e in emner if e["faglarerId"] == meg["id"]]
        svar["ingeniorer"] = [{"id": u["id"], "navn": u["navn"]} for u in store.brukere()
                              if u["aktiv"] and u["rolle"] == "ingenior"]
    return jsonify(svar)


@app.post("/api/skolear/<int:fra>/kopier")
def copy_year(fra):
    """Kopier gjennomføringer til neste skoleår. «ids» velger hvilke (standard: alle)."""
    ids = (request.get_json(silent=True) or {}).get("ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, (str, int)) for i in ids)):
        raise bomlib.BomError("Ugyldig liste over prosjekter.")
    # bare prosjekter i egne emner (og uten emne) kopieres
    mine = {g["id"] for g in store.gjennomforinger() if g["skolear"] == fra and kan_handle(g)}
    valgt = mine if ids is None else {str(i) for i in ids} & mine
    nye = store.kopier(fra, sorted(valgt))
    return jsonify({"nye": nye, "til": fra + 1, "navn": frister.skolear_navn(fra + 1)})


def _clean(body: dict, partial: bool) -> dict:
    out = {}
    for k, typ in FIELDS.items():
        if k not in body:
            continue
        v = body[k]
        if v in (None, "") and k in ("sett", "bom", "notat", "emne"):
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
    emne = _emne(body.pop("emne", None))
    if not auth.er_ingenior():
        # faglæreren legger prosjektet i ett av sine emner; leveringstid, buffer og BOM får ingeniørens standard
        if emne is None or emne["faglarerId"] != auth.meg()["id"] or not emne["aktiv"]:
            raise Nektet("Velg ett av dine emner.")
        body = {k: v for k, v in body.items() if k in FAGLARER_FELT or k == "color"}
    body["emne_id"] = emne["id"] if emne else None
    slug = re.sub(r"[^a-z0-9]+", "-", body["navn"].lower()).strip("-")[:24] or "prosjekt"
    g = store.ny(f"{slug}-{secrets.token_hex(2)}", sy, body)
    logg(g, "opprettet")
    return jsonify(g)


def _emne(eid) -> dict | None:
    if eid in (None, "", 0):
        return None
    try:
        return store.emne(int(eid))
    except (KeyError, TypeError, ValueError):
        raise bomlib.BomError("Fant ikke emnet.")


def _aktiv(gid: str) -> dict:
    """Gjennomføringen, hvis den ikke er fjernet. (Om den er din, er sjekket i _eierskap.)"""
    g = store.gjennomforing(gid)
    if g["slettet"]:
        raise KeyError(gid)
    return g


@app.patch("/api/gjennomforinger/<gid>")
def update(gid):
    body = _clean(request.get_json(force=True), partial=True)
    g0 = _aktiv(gid)
    if not auth.er_ingenior():
        ulovlig = set(body) - FAGLARER_FELT
        if ulovlig:
            raise Nektet("Det er ingeniøren som endrer " + ", ".join(sorted(FELTNAVN.get(k, k) for k in ulovlig)) + ".")
    if "emne" in body:
        emne = _emne(body.pop("emne"))
        body["emne_id"] = emne["id"] if emne else None
    g1 = store.endre(gid, body)
    endret = {}
    for k in body:
        kol = "emneId" if k == "emne_id" else k
        if g0[kol] != g1[kol]:
            endret[kol] = ((g0["emneKode"], g1["emneKode"]) if kol == "emneId" else (g0[kol], g1[kol]))
    if endret:
        logg(g1, "endret", endret)
    return jsonify(g1)


@app.delete("/api/gjennomforinger/<gid>")
def delete(gid):
    """Faglærer: fjernes (mykt) til ingeniøren bekrefter. Ingeniør: slettes – også et fjernet prosjekt."""
    g = store.gjennomforing(gid)
    if auth.er_ingenior():
        store.slett(gid)
        store.kvitter(gid)
        logg(g, "slettet")
        return jsonify({"ok": True})
    g = _aktiv(gid)
    store.myk_slett(gid, auth.meg()["navn"])
    logg(g, "fjernet")
    return jsonify({"ok": True, "venter": True})


# ---------- emner (ingeniører: alle; faglærer: sine egne) ----------

def _emnefelt(body: dict, nytt: bool) -> dict:
    out = {}
    if "kode" in body or nytt:
        kode = str(body.get("kode") or "").strip().upper()
        if not kode or len(kode) > 20:
            raise bomlib.BomError("Gi emnet en emnekode, f.eks. IELS1001.")
        out["kode"] = kode
    if "navn" in body:
        out["navn"] = str(body["navn"] or "").strip()[:120]
    if "program" in body:
        if body["program"] not in PROGRAM:
            raise bomlib.BomError("Studieprogram må være bachelor eller master.")
        out["program"] = body["program"]
    for felt, rolle, tekst in (("faglarer", "faglarer", "faglærer"), ("ingenior", "ingenior", "ingeniør")):
        if felt in body:
            uid = body[felt]
            if uid in (None, "", 0):
                out[f"{felt}_id"] = None
                continue
            try:
                u = store.bruker(id=int(uid))
            except (TypeError, ValueError):
                u = None
            if not u or u["rolle"] != rolle or not u["aktiv"]:
                raise bomlib.BomError(f"Fant ikke {tekst}en.")
            out[f"{felt}_id"] = u["id"]
    if "aktiv" in body:
        out["aktiv"] = 1 if body["aktiv"] else 0
    return out


def _varsle_ingenior(fra: dict | None, til: dict) -> None:
    """Fortell ingeniøren som er valgt (og den som er tatt av) – men ikke deg selv."""
    meg = auth.meg()
    felles = {"bruker_id": meg["id"], "bruker_navn": meg["navn"], "rolle": meg["rolle"], "emne_id": til["id"],
              "prosjekt_navn": til["kode"], "felt": "ingeniør"}
    rader = []
    ny, gammel = til["ingeniorId"], fra["ingeniorId"] if fra else None
    if ny == gammel:
        return
    if ny and ny != meg["id"]:
        rader.append({**felles, "handling": "valgt_ingenior", "mottaker_id": ny, "til": til["ingeniorNavn"],
                      "fra": fra["ingeniorNavn"] if fra else None})
    if gammel and gammel != meg["id"]:
        rader.append({**felles, "handling": "fjernet_ingenior", "mottaker_id": gammel, "fra": fra["ingeniorNavn"],
                      "til": til["ingeniorNavn"]})
    if rader:
        store.logg(rader)


@app.post("/api/emner")
def create_subject():
    """Ingeniører legger inn hvilke emner som helst; en faglærer lager sine egne (og blir faglærer for dem)."""
    body = request.get_json(force=True) or {}
    if not auth.er_ingenior():
        if "faglarer" in body and body["faglarer"] not in (None, "", auth.meg()["id"]):
            raise Nektet("Du kan bare lage emner der du selv er faglærer.")
        body = {**body, "faglarer": auth.meg()["id"]}
    try:
        e = store.nytt_emne(_emnefelt(body, nytt=True))
    except sqlite3.IntegrityError:
        raise bomlib.BomError("Det finnes allerede et emne med den koden.")
    _varsle_ingenior(None, e)
    return jsonify(e)


@app.patch("/api/emner/<int:eid>")
def update_subject(eid):
    """Ingeniører endrer alle emner; en faglærer sine egne – men ikke hvem som er faglærer."""
    e0 = store.emne(eid)  # 404 hvis det ikke finnes
    body = request.get_json(force=True) or {}
    if not auth.er_ingenior():
        if e0["faglarerId"] != auth.meg()["id"]:
            raise Nektet("Du kan bare endre dine egne emner.")
        if "faglarer" in body:
            raise Nektet("Det er administratoren eller en ingeniør som bytter faglærer på et emne.")
    try:
        e1 = store.endre_emne(eid, _emnefelt(body, nytt=False))
    except sqlite3.IntegrityError:
        raise bomlib.BomError("Det finnes allerede et emne med den koden.")
    _varsle_ingenior(e0, e1)
    return jsonify(e1)


@app.post("/api/endringer/kvitter")
def acknowledge_mine():
    """«OK, sett» på varsler som er til deg (f.eks. «du er valgt som ingeniør»)."""
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise bomlib.BomError("Ugyldige varsler.")
    return jsonify({"kvittert": store.kvitter_til(auth.meg()["id"], ids)})


# ---------- brukere (administratoren) ----------

def _brukerfelt(body: dict, ny: bool) -> dict:
    out = {}
    if ny:
        brukernavn = str(body.get("brukernavn") or "").strip()
        if not brukernavn or any(c.isspace() for c in brukernavn) or len(brukernavn) > 40:
            raise bomlib.BomError("Brukernavnet kan ikke være tomt eller inneholde mellomrom.")
        out["brukernavn"] = brukernavn
    if "navn" in body or ny:
        navn = str(body.get("navn") or "").strip()[:80]
        if not navn:
            raise bomlib.BomError("Gi brukeren et navn.")
        out["navn"] = navn
    if "rolle" in body or ny:
        if body.get("rolle") not in auth.ROLLER:
            raise bomlib.BomError("Rollen må være ingeniør eller faglærer.")
        out["rolle"] = body["rolle"]
    if "admin" in body:
        out["admin"] = 1 if body["admin"] else 0
    if "aktiv" in body:
        out["aktiv"] = 1 if body["aktiv"] else 0
    if "passord" in body or ny:
        try:
            out["passord"] = auth.hash_passord(str(body.get("passord") or ""))
        except ValueError as e:
            raise bomlib.BomError(str(e))
    if out.get("rolle") == "faglarer":
        out["admin"] = 0  # administrator er alltid en ingeniør
    return out


def _bruker_ut(u: dict) -> dict:
    return {k: (bool(u[k]) if k in ("admin", "aktiv") else u[k])
            for k in ("id", "brukernavn", "navn", "rolle", "admin", "aktiv", "sist_inne", "last_til")}


@app.post("/api/brukere")
def create_user():
    f = _brukerfelt(request.get_json(force=True) or {}, ny=True)
    try:
        uid = store.ny_bruker(f["brukernavn"], f["navn"], f["rolle"], f["passord"], admin=bool(f.get("admin")))
    except sqlite3.IntegrityError:
        raise bomlib.BomError(f"Brukernavnet «{f['brukernavn']}» er tatt.")
    return jsonify(_bruker_ut(store.bruker(id=uid)))


@app.patch("/api/brukere/<int:uid>")
def update_user(uid):
    u = store.bruker(id=uid)
    if not u:
        raise KeyError(uid)
    f = _brukerfelt(request.get_json(force=True) or {}, ny=False)
    ny = {**u, **f}
    # det må alltid finnes en aktiv administrator, ellers kommer ingen inn i «Brukere» igjen
    var_admin = u["admin"] and u["aktiv"]
    blir_admin = ny["admin"] and ny["aktiv"] and ny["rolle"] == "ingenior"
    if var_admin and not blir_admin and store.aktive_admins() <= 1:
        raise bomlib.BomError("Det må finnes minst én aktiv administrator.")
    if f.get("rolle", u["rolle"]) != u["rolle"]:
        i_emner = [e["kode"] for e in store.emner() if uid in (e["faglarerId"], e["ingeniorId"])]
        if i_emner:
            raise bomlib.BomError("Brukeren står på " + ", ".join(i_emner) + ". Bytt person der først.")
    if "passord" in f or "aktiv" in f:
        f.update(feilforsok=0, last_til=None)  # nytt passord eller aktivert: lås opp
    store.endre_bruker(uid, **f)
    return jsonify(_bruker_ut(store.bruker(id=uid)))


@app.post("/api/gjennomforinger/<gid>/gjenopprett")
def restore(gid):
    """Angre at en faglærer fjernet et prosjekt."""
    g = store.gjennomforing(gid)
    if not g["slettet"]:
        raise bomlib.BomError("Prosjektet er ikke fjernet.")
    store.gjenopprett(gid)
    store.kvitter(gid)
    logg(g, "gjenopprettet")
    return jsonify({"ok": True})


@app.post("/api/endringer/<gid>/kvitter")
def acknowledge_changes(gid):
    """«OK, sett» på faglærerens endringer i en gjennomføring."""
    return jsonify({"kvittert": store.kvitter(gid)})


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
            store.ny_bestilling(gid, key, gr["navn"], dato=dato, ordrenr=ordrenr, sett=sett, linjer=linjer, av=auth.meg()["navn"])
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
        if status == "bestilt" and any(lagret["paa_lager"].values()):
            raise bomlib.BomError("Noe fra denne bestillingen er lagt på lager. Angre mottaket først.")
        mottatt = lagret["mottatt_dato"] or dt.date.today().isoformat()
        store.endre_bestilling(gid, key, status=status, mottatt_dato=None if status == "bestilt" else mottatt)
    elif handling == "ordrenr":
        if not lagret:
            raise bomlib.BomError("Leverandøren er ikke registrert som bestilt.")
        store.endre_bestilling(gid, key, ordrenr=ordrenr)
    elif handling == "angre":
        if lagret and any(lagret["paa_lager"].values()):
            raise bomlib.BomError("Noe fra denne bestillingen er lagt på lager. Angre mottaket først.")
        store.slett_bestilling(gid, key)
    elif handling in ("mottak", "angre_mottak"):
        return jsonify(_varemottak(g, gid, key, lagret, handling, body.get("linjer")))
    else:
        raise bomlib.BomError("Ukjent handling.")
    return jsonify({"ok": True})


def _varemottak(g: dict, gid: str, key: str, lagret: dict | None, handling: str, linjer: dict | None) -> dict:
    """Varemottak: legg det som har kommet til «På lager» i BOM-en, eller trekk det fra igjen.

    mottak:       linjer = {nøkkel: antall som kom}; uten linjer: alt som gjenstår.
                  Status blir «mottatt» når alt er lagt på lager, ellers «delvis».
    angre_mottak: trekk fra alt som er lagt på lager fra denne bestillingen; status «bestilt».
    Legges til det cellen har nå, så endringer gjort i Excel imens blir ikke borte.
    """
    if not lagret:
        raise bomlib.BomError("Leverandøren er ikke registrert som bestilt.")
    rader = bestilling.mottak(lagret)
    paa_lager = dict(lagret["paa_lager"])
    if handling == "mottak":
        if linjer is None:
            delta = {r["key"]: r["gjenstar"] for r in rader if r["gjenstar"] > 0}
        else:
            kjente = {r["key"] for r in rader}
            try:
                delta = {str(k): float(v) for k, v in linjer.items() if v not in (None, "") and float(v) != 0}
            except (TypeError, ValueError, AttributeError):
                raise bomlib.BomError("Ugyldig antall.")
            if any(v < 0 for v in delta.values()) or not set(delta) <= kjente:
                raise bomlib.BomError("Ugyldig antall.")
        if not delta:
            raise bomlib.BomError("Ingenting å legge på lager.")
    else:
        delta = {k: -v for k, v in paa_lager.items() if v}
        if not delta:
            raise bomlib.BomError("Ingenting fra denne bestillingen er lagt på lager.")

    res = _skriv_lager(g, lagret["linjer"], delta, legg_til=True)
    for k in res["skrevet"]:
        paa_lager[k] = max(0.0, paa_lager.get(k, 0.0) + delta[k])
    paa_lager = {k: v for k, v in paa_lager.items() if v > 1e-9}
    if res["skrevet"]:
        if handling == "angre_mottak":
            status = "bestilt" if not paa_lager else lagret["status"]
        else:
            alt = all(paa_lager.get(r["key"], 0.0) >= r["bestilt"] - 1e-9 for r in rader)
            status = "mottatt" if alt else "delvis"
        logg = lagret["lager_logg"] + [{"tid": dt.datetime.now().isoformat(timespec="minutes"),
                                        "handling": "mottatt" if handling == "mottak" else "angret", "av": auth.meg()["navn"],
                                        "linjer": {k: delta[k] for k in res["skrevet"]}, "backup": res["backup"]}]
        store.endre_bestilling(gid, key, paa_lager=paa_lager, lager_logg=logg, status=status,
                               mottatt_dato=None if status == "bestilt" else (lagret["mottatt_dato"] or dt.date.today().isoformat()))
    return res


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
    """Lagre eller rett tellingen. linjer: [{key, type, telt}].

    En ny telling henter «før»-tallene fra BOM-en nå. En rettet telling beholder tallene den
    ble laget fra – ellers ville et lager som allerede er skrevet til BOM-en, blitt telt to ganger.
    """
    g = store.gjennomforing(gid)
    gammel = store.tellinger().get(gid)
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


def _skriv_lager(g: dict, linjer: list[dict], verdier: dict[str, float], forventet: dict[str, float] | None = None,
                 legg_til: bool = False) -> dict:
    """Skriv til «På lager» (se bom.write_stock), og hold «sist sett» i takt så vår egen endring
    ikke vises som «BOM-en er endret». Konflikter får beskrivelse fra «linjer» med til nettleseren."""
    with _lock:
        res = bomlib.write_stock(bom_path(g), verdier, BOM_DIR / "Sikkerhetskopi", forventet, legg_til)
        snaps = _read_json(SNAP_FILE, {})
        if g["bom"] in snaps and res["nye"]:
            for r in snaps[g["bom"]]["rader"]:
                if bomlib.rad_key(r) in res["nye"]:
                    r["lager"] = res["nye"][bomlib.rad_key(r)]
            _write_json(SNAP_FILE, snaps)
    navn = {l["key"]: l["beskrivelse"] for l in linjer}
    for k in res["konflikt"]:
        k["beskrivelse"] = navn.get(k["key"], k["key"])
    return res


def _logg(tell: dict, handling: str, res: dict) -> list[dict]:
    return tell["logg"] + [{"tid": dt.datetime.now().isoformat(timespec="minutes"), "handling": handling, "av": auth.meg()["navn"],
                            "linjer": len(res["skrevet"]), "konflikter": len(res["konflikt"]), "backup": res["backup"]}]


@app.post("/api/gjennomforinger/<gid>/telling/skriv")
def write_count(gid):
    """Skriv det som er telt (og ikke allerede skrevet) inn i «På lager» i BOM-en.

    Bare celler som fortsatt har verdien årshjulet sist kjente til, overskrives: det som ble
    skrevet sist, eller «på lager» da tellingen ble gjort. Er cellen endret i Excel siden,
    hoppes linjen over og meldes som konflikt.
    """
    g = store.gjennomforing(gid)
    tell = store.tellinger().get(gid)
    if not tell:
        raise bomlib.BomError("Lagre tellingen først.")
    sv = tell["skrevet_verdier"]
    nye = telling.ventende(tell["linjer"], sv)
    if not nye:
        raise bomlib.BomError("Alt som er telt, står allerede i BOM-en.")
    linjer = {l["key"]: l for l in tell["linjer"]}
    res = _skriv_lager(g, tell["linjer"], {k: linjer[k]["telt"] for k in nye},
                       {k: sv.get(k, linjer[k]["lagerFor"]) for k in nye})
    if res["skrevet"]:
        sv = {**sv, **{k: linjer[k]["telt"] for k in res["skrevet"]}}
        store.telling_skrevet(gid, sv, _logg(tell, "skrevet", res), dt.date.today().isoformat(), res["backup"])
    return jsonify(res)


@app.post("/api/gjennomforinger/<gid>/telling/angre")
def undo_count(gid):
    """Sett «På lager» tilbake til tallene fra før tellingen, for linjer årshjulet har skrevet."""
    g = store.gjennomforing(gid)
    tell = store.tellinger().get(gid)
    if not tell or not tell["skrevet_verdier"]:
        raise bomlib.BomError("Ingenting fra denne tellingen står i BOM-en.")
    sv = tell["skrevet_verdier"]
    linjer = {l["key"]: l for l in tell["linjer"]}
    keys = [k for k in sv if k in linjer]
    res = _skriv_lager(g, tell["linjer"], {k: linjer[k]["lagerFor"] for k in keys}, {k: sv[k] for k in keys})
    if res["skrevet"]:
        sv = {k: v for k, v in sv.items() if k not in res["skrevet"]}
        store.telling_skrevet(gid, sv, _logg(tell, "angret", res), tell["skrevet"], tell["backup"])
    return jsonify(res)


@app.post("/api/gjennomforinger/<gid>/telling/hent")
def adopt_count(gid):
    """Bruk «På lager» fra BOM-en som telt antall, for linjer som er endret i Excel etter skriving.

    For når endringen i Excel var en retting av tellingen. Svinn regnes da ut fra det nye tallet.
    """
    g = store.gjennomforing(gid)
    tell = store.tellinger().get(gid)
    if not tell:
        raise bomlib.BomError("Det finnes ingen telling.")
    _, c, _, _ = beregn(g)
    endret = telling.endret_i_bom(tell["skrevet_verdier"], c["rader"])
    if not endret:
        raise bomlib.BomError("Ingen linjer er endret i BOM-en siden tellingen ble skrevet.")
    linjer = [{**l, "telt": endret[l["key"]]} if l["key"] in endret else l for l in tell["linjer"]]
    store.lagre_telling(gid, g["prosjektId"], tell["dato"], linjer)
    logg = tell["logg"] + [{"tid": dt.datetime.now().isoformat(timespec="minutes"), "handling": "hentet", "av": auth.meg()["navn"],
                            "linjer": len(endret), "konflikter": 0, "backup": None}]
    store.telling_skrevet(gid, {**tell["skrevet_verdier"], **endret}, logg, tell["skrevet"], tell["backup"])
    return jsonify({"hentet": len(endret), **telling.oppsummering(linjer)})


@app.delete("/api/gjennomforinger/<gid>/telling")
def delete_count(gid):
    store.gjennomforing(gid)
    tell = store.tellinger().get(gid)
    if tell and tell["skrevet_verdier"]:
        raise bomlib.BomError("Noe fra tellingen står i BOM-en. Angre først, så kan den slettes.")
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
