"""Innlogging, roller og CSRF-beskyttelse.

Innlogging er alltid på – lokalt og på serveren – så det som testes lokalt, er det som kjører.

Roller:
    ingenior        prosjekter i egne emner (og prosjekter uten emne)
    ingenior+admin  en vanlig ingeniør på hovedsiden, pluss administratorpanelet (/admin):
                    ser og retter alt, styrer brukere, rydder og tar sikkerhetskopi
    faglarer        ser hele hjulet; legger til, endrer og fjerner prosjektene i sine egne emner
                    (navn, type, uker, antall sett, notat). Ser ikke BOM, bestilling, telling eller kostnader.

Første administrator:
    lokalt     siden «Opprett administrator» vises når det ikke finnes noen brukere
    server     python manage.py ny-bruker <brukernavn> "<Navn>" ingenior --admin
               (ikke på nettsiden, så ingen andre rekker å gjøre seg til administrator først)

Sikkerhet:
- passord lagres som hash (werkzeug, scrypt)
- låst i 15 minutter etter 5 feil passord; i tillegg en grense per IP-adresse
- informasjonskapselen er HttpOnly og SameSite=Lax, og Secure i servermodus
- alt som endrer noe krever et CSRF-token (skjemafelt eller X-CSRF-Token-header),
  så en annen nettside ikke kan få nettleseren din til å endre årshjulet
- økten slutter å gjelde når passordet endres eller brukeren deaktiveres
"""
from __future__ import annotations

import datetime as dt
import hmac
import secrets
import sqlite3
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
HASH_METODE = "scrypt"  # testene bruker en raskere metode
_TRYGGE = ("GET", "HEAD", "OPTIONS")
_APNE = {"auth.login", "auth.oppsett", "static"}
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
                                   "rollenavn": ROLLENAVN})


# ---------- passord ----------

def passordfeil(passord: str) -> str | None:
    if len(passord) < MIN_PASSORD:
        return f"Passordet må være minst {MIN_PASSORD} tegn."
    return None


def hash_passord(passord: str) -> str:
    feil = passordfeil(passord)
    if feil:
        raise ValueError(feil)
    return generate_password_hash(passord, method=HASH_METODE)


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
    """Ingeniør – også administratoren."""
    return g.bruker is not None and g.bruker["rolle"] == "ingenior"


def er_admin() -> bool:
    return er_ingenior() and bool(g.bruker["admin"])


def krever_admin(f):
    @wraps(f)
    def inner(*a, **k):
        if not er_admin():
            return jsonify({"feil": "Bare administratoren kan gjøre dette."}), 403
        return f(*a, **k)
    return inner


def _kan_opprette_forste() -> bool:
    """Første administrator kan bare lages på nettsiden lokalt, og bare før det finnes brukere."""
    return config.LOKAL and _store.antall_brukere() == 0


def _vakt():
    g.bruker = None
    if session.get("uid"):
        u = _store.bruker(id=session["uid"])
        if u and u["aktiv"] and session.get("pv") == _passordversjon(u):
            # admin: kontoen har administratorpanelet (/admin). På hovedsiden er du en vanlig ingeniør.
            g.bruker = {"id": u["id"], "navn": u["navn"], "brukernavn": u["brukernavn"], "rolle": u["rolle"],
                        "admin": bool(u["admin"]) and u["rolle"] == "ingenior"}
        else:
            session.clear()

    if request.method not in _TRYGGE and not _csrf_ok():
        if request.path.startswith("/api/"):
            return jsonify({"feil": "Siden er utdatert. Last den på nytt."}), 400
        return redirect(url_for("auth.login"))
    if g.bruker is None and request.endpoint not in _APNE:
        if request.path.startswith("/api/"):
            return jsonify({"feil": "Du er ikke logget inn.", "login": True}), 401
        if _kan_opprette_forste():
            return redirect(url_for("auth.oppsett"))
        return redirect(url_for("auth.login", neste=request.path))
    return None


def _logg_inn(u: dict) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    session["pv"] = _passordversjon(u)
    csrf_token()  # nytt token ved innlogging
    _store.endre_bruker(u["id"], feilforsok=0, last_til=None,
                        sist_inne=dt.datetime.now().isoformat(timespec="seconds"))


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
    if _kan_opprette_forste():
        return redirect(url_for("auth.oppsett"))
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
                _logg_inn(u)
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
    ingen_brukere = _store.antall_brukere() == 0  # på serveren: lag administrator med manage.py
    ingen_admin = not ingen_brukere and _store.aktive_admins() == 0  # f.eks. brukere fra før administrator fantes
    return render_template("login.html", feil=feil, neste=request.args.get("neste", ""),
                           ingen_brukere=ingen_brukere, ingen_admin=ingen_admin), (401 if feil else 200)


@bp.route("/oppsett", methods=["GET", "POST"])
def oppsett():
    """Første gang, lokalt: lag administratoren (deg)."""
    if not _kan_opprette_forste():
        return redirect(url_for("auth.login"))
    feil = None
    if request.method == "POST":
        brukernavn = (request.form.get("brukernavn") or "").strip()
        navn = (request.form.get("navn") or "").strip()
        passord = request.form.get("passord") or ""
        if not brukernavn or any(c.isspace() for c in brukernavn) or not navn:
            feil = "Fyll inn brukernavn (uten mellomrom) og navn."
        elif passord != request.form.get("passord2"):
            feil = "Passordene er ikke like."
        else:
            feil = passordfeil(passord)
        if not feil:
            try:
                uid = _store.ny_bruker(brukernavn, navn, "ingenior", hash_passord(passord), admin=True)
            except sqlite3.IntegrityError:
                feil = "Brukernavnet er tatt."
            else:
                _logg_inn(_store.bruker(id=uid))
                return redirect("/admin#brukere")  # første gang: lag brukere til de andre
    return render_template("oppsett.html", feil=feil, min_passord=MIN_PASSORD), (400 if feil else 200)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
