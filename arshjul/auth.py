"""Innlogging, roller og CSRF-beskyttelse.

Roller:
    ingenior   alt
    faglarer   ser hele hjulet; legger til, endrer og fjerner sine egne prosjekter
               (navn, type, uker, antall sett, notat). Ser ikke BOM, bestilling, telling eller kostnader.

Brukere lages fra kommandolinjen (manage.py). Innlogging er alltid på i servermodus.
Lokalt er den av som standard, og du er ingeniøren (ARSHJUL_INNLOGGING=på for å teste).

Sikkerhet:
- passord lagres som hash (werkzeug, scrypt)
- låst i 15 minutter etter 5 feil passord; i tillegg en grense per IP-adresse
- informasjonskapselen er HttpOnly og SameSite=Lax, og Secure i servermodus
- alt som endrer noe krever et CSRF-token (skjemafelt eller X-CSRF-Token-header), også lokalt,
  så en annen nettside ikke kan få nettleseren din til å endre årshjulet
- økten slutter å gjelde når passordet endres eller brukeren deaktiveres
"""
from __future__ import annotations

import datetime as dt
import hmac
import secrets
import time
from functools import wraps

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config

ROLLER = ("ingenior", "faglarer")
ROLLENAVN = {"ingenior": "Ingeniør", "faglarer": "Faglærer"}
MAKS_FEIL = 5
LAS_MINUTTER = 15
IP_MAKS = 20  # feil per IP-adresse per kvarter
MIN_PASSORD = 10
LOKAL_INGENIOR = {"id": None, "navn": "Ingeniør", "brukernavn": "", "rolle": "ingenior"}
_TRYGGE = ("GET", "HEAD", "OPTIONS")
_APNE = {"auth.login", "static"}
# en hash å sjekke mot når brukeren ikke finnes, så svartiden ikke avslører hvilke brukernavn som finnes
_DUMMY = generate_password_hash(secrets.token_hex(8))

bp = Blueprint("auth", __name__)
_store = None
_ip_feil: dict[str, list[float]] = {}


def init(app, store) -> None:
    global _store
    _store = store
    app.register_blueprint(bp)
    app.before_request(_vakt)
    app.context_processor(lambda: {"csrf_token": csrf_token(), "meg": getattr(g, "bruker", None),
                                   "innlogging": config.INNLOGGING, "rollenavn": ROLLENAVN})


# ---------- passord ----------

def passordfeil(passord: str) -> str | None:
    if len(passord) < MIN_PASSORD:
        return f"Passordet må være minst {MIN_PASSORD} tegn."
    return None


def hash_passord(passord: str) -> str:
    feil = passordfeil(passord)
    if feil:
        raise ValueError(feil)
    return generate_password_hash(passord)


def _passordversjon(bruker: dict) -> str:
    """Endres når passordet endres, så gamle økter slutter å gjelde."""
    return bruker["passord"][-16:]


# ---------- CSRF ----------

def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _csrf_ok() -> bool:
    sendt = request.headers.get("X-CSRF-Token") or request.form.get("csrf") or ""
    return bool(session.get("csrf")) and hmac.compare_digest(sendt, session["csrf"])


# ---------- hvem er du ----------

def meg() -> dict:
    return g.bruker


def er_ingenior() -> bool:
    return g.bruker is not None and g.bruker["rolle"] == "ingenior"


def krever_ingenior(f):
    @wraps(f)
    def inner(*a, **k):
        if not er_ingenior():
            return jsonify({"feil": "Bare ingeniøren kan gjøre dette."}), 403
        return f(*a, **k)
    return inner


def _vakt():
    g.bruker = None
    if not config.INNLOGGING:
        g.bruker = LOKAL_INGENIOR
    elif session.get("uid"):
        u = _store.bruker(id=session["uid"])
        if u and u["aktiv"] and session.get("pv") == _passordversjon(u):
            g.bruker = {"id": u["id"], "navn": u["navn"], "brukernavn": u["brukernavn"], "rolle": u["rolle"]}
        else:
            session.clear()

    if request.method not in _TRYGGE and not _csrf_ok():
        if request.path.startswith("/api/"):
            return jsonify({"feil": "Siden er utdatert. Last den på nytt."}), 400
        return redirect(url_for("auth.login"))
    if g.bruker is None and request.endpoint not in _APNE:
        if request.path.startswith("/api/"):
            return jsonify({"feil": "Du er ikke logget inn.", "login": True}), 401
        return redirect(url_for("auth.login", neste=request.path))
    return None


# ---------- innlogging ----------

def _ip_sperret(ip: str) -> bool:
    na = time.time()
    _ip_feil[ip] = [t for t in _ip_feil.get(ip, []) if na - t < LAS_MINUTTER * 60]
    return len(_ip_feil[ip]) >= IP_MAKS


def _trygg_neste(neste: str | None) -> str:
    # bare stier på samme nettsted – aldri «//annet.no» eller «https://…»
    return neste if neste and neste.startswith("/") and not neste.startswith("//") and "\\" not in neste else "/"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not config.INNLOGGING:
        return redirect("/")
    feil = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        brukernavn = (request.form.get("brukernavn") or "").strip()
        passord = request.form.get("passord") or ""
        u = _store.bruker(brukernavn=brukernavn) if brukernavn else None
        na = dt.datetime.now()
        if _ip_sperret(ip):
            feil = f"For mange feil forsøk. Prøv igjen om {LAS_MINUTTER} minutter."
        elif u and u["last_til"] and dt.datetime.fromisoformat(u["last_til"]) > na:
            igjen = max(1, int((dt.datetime.fromisoformat(u["last_til"]) - na).total_seconds() // 60) + 1)
            feil = f"For mange feil forsøk. Prøv igjen om {igjen} minutter."
        else:
            riktig = check_password_hash(u["passord"] if u else _DUMMY, passord)
            if u and riktig and u["aktiv"]:
                session.clear()
                session.permanent = True
                session["uid"] = u["id"]
                session["pv"] = _passordversjon(u)
                csrf_token()
                _store.endre_bruker(u["id"], feilforsok=0, last_til=None, sist_inne=na.isoformat(timespec="seconds"))
                return redirect(_trygg_neste(request.args.get("neste")))
            _ip_feil.setdefault(ip, []).append(time.time())
            if u:
                n = u["feilforsok"] + 1
                if n >= MAKS_FEIL:
                    _store.endre_bruker(u["id"], feilforsok=0,
                                        last_til=(na + dt.timedelta(minutes=LAS_MINUTTER)).isoformat(timespec="seconds"))
                else:
                    _store.endre_bruker(u["id"], feilforsok=n)
            time.sleep(0.3)
            feil = "Feil brukernavn eller passord."
    return render_template("login.html", feil=feil, neste=request.args.get("neste", "")), (401 if feil else 200)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login") if config.INNLOGGING else "/")
