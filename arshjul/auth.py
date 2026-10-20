"""Login, roles and CSRF protection.

Login is always on – locally and on the server – so what is tested locally is what runs.

Roles:
    engineer        projects in their own courses (and projects without a course)
    engineer+admin  a regular engineer on the main page, plus the admin panel (/admin):
                    sees and fixes everything, manages users, cleans up and takes backups
    lecturer        sees the whole wheel; adds, changes and removes the projects in their own courses
                    (name, type, weeks, students and groups, note). Doesn't see BOMs, orders, counts or costs.

The first admin:
    locally    the «Opprett administrator» page is shown when there are no users
    server     python manage.py ny-bruker <brukernavn> "<Navn>" ingenior --admin
               (not on the web page, so nobody else can make themselves admin first)

Security:
- passwords are stored as hashes (werkzeug, scrypt)
- locked for 15 minutes after 5 wrong passwords; plus a limit per IP address
- the cookie is HttpOnly and SameSite=Lax, and Secure in server mode
- anything that changes something requires a CSRF token (form field or X-CSRF-Token header),
  so another website can't make your browser change the app
- the session stops working when the password changes or the user is deactivated
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

ROLES = ("engineer", "lecturer")
ROLE_NAMES = {"engineer": "Ingeniør", "lecturer": "Faglærer"}
MAX_FAILURES = 5
LOCK_MINUTES = 15
IP_MAX = 20  # feil per IP-adresse per kvarter
MIN_PASSWORD = 10
HASH_METHOD = "scrypt"  # the tests use a faster method
_SAFE = ("GET", "HEAD", "OPTIONS")
_OPEN = {"auth.login", "auth.setup", "static", "calendar_feed"}  # the calendar link is its own key
# a hash to check against when the user doesn't exist, so the response time doesn't reveal which usernames exist
_DUMMY = generate_password_hash(secrets.token_hex(8))

bp = Blueprint("auth", __name__)
_store = None
_ip_failures: dict[str, list[float]] = {}


def init(app, store) -> None:
    global _store
    _store = store
    app.register_blueprint(bp)
    app.before_request(_guard)
    app.context_processor(lambda: {"csrf_token": csrf_token(), "me": getattr(g, "user", None),
                                   "roleName": ROLE_NAMES})


# ---------- passord ----------

def password_error(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Passordet må være minst {MIN_PASSWORD} tegn."
    return None


def hash_password(password: str) -> str:
    error = password_error(password)
    if error:
        raise ValueError(error)
    return generate_password_hash(password, method=HASH_METHOD)


def _password_version(user: dict) -> str:
    """Changes when the password changes, so old sessions stop working."""
    return user["password"][-16:]


# ---------- CSRF ----------

def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _csrf_ok() -> bool:
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf") or ""
    return bool(session.get("csrf")) and hmac.compare_digest(sent, session["csrf"])


# ---------- who are you ----------

def me() -> dict:
    return g.user


def is_engineer() -> bool:
    """Engineer – the admin too."""
    return g.user is not None and g.user["role"] == "engineer"


def is_admin() -> bool:
    return is_engineer() and bool(g.user["admin"])


def requires_admin(f):
    @wraps(f)
    def inner(*a, **k):
        if not is_admin():
            return jsonify({"error": "Bare administratoren kan gjøre dette."}), 403
        return f(*a, **k)
    return inner


def _can_create_first() -> bool:
    """The first admin can only be created on the web page locally, and only before there are any users."""
    return config.LOCAL and _store.user_count() == 0


def _guard():
    g.user = None
    if session.get("uid"):
        u = _store.user(id=session["uid"])
        if u and u["active"] and session.get("pv") == _password_version(u):
            # admin: the account has the admin panel (/admin). On the main page you're a regular engineer.
            g.user = {"id": u["id"], "name": u["name"], "username": u["username"], "role": u["role"],
                        "admin": bool(u["admin"]) and u["role"] == "engineer"}
        else:
            session.clear()

    if request.method not in _SAFE and not _csrf_ok():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Siden er utdatert. Last den på nytt."}), 400
        return redirect(url_for("auth.login"))
    if g.user is None and request.endpoint not in _OPEN:
        if request.path.startswith("/api/"):
            return jsonify({"error": "Du er ikke logget inn.", "login": True}), 401
        if _can_create_first():
            return redirect(url_for("auth.setup"))
        return redirect(url_for("auth.login", next=request.path))
    return None


def _log_in(u: dict) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    session["pv"] = _password_version(u)
    csrf_token()  # nytt token ved innlogging
    _store.update_user(u["id"], failed_attempts=0, locked_until=None,
                        last_login=dt.datetime.now().isoformat(timespec="seconds"))


# ---------- innlogging ----------

def _ip_blocked(ip: str) -> bool:
    current = time.time()
    _ip_failures[ip] = [t for t in _ip_failures.get(ip, []) if current - t < LOCK_MINUTES * 60]
    return len(_ip_failures[ip]) >= IP_MAX


def _safe_next(next_: str | None) -> str:
    # only paths on the same site – never «//other.no» or «https://…»
    return next_ if next_ and next_.startswith("/") and not next_.startswith("//") and "\\" not in next_ else "/"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if _can_create_first():
        return redirect(url_for("auth.setup"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        u = _store.user(username=username) if username else None
        current = dt.datetime.now()
        if _ip_blocked(ip):
            error = f"For mange feil forsøk. Prøv igjen om {LOCK_MINUTES} minutter."
        elif u and u["locked_until"] and dt.datetime.fromisoformat(u["locked_until"]) > current:
            left = max(1, int((dt.datetime.fromisoformat(u["locked_until"]) - current).total_seconds() // 60) + 1)
            error = f"For mange feil forsøk. Prøv igjen om {left} minutter."
        else:
            correct = check_password_hash(u["password"] if u else _DUMMY, password)
            if u and correct and u["active"]:
                _log_in(u)
                return redirect(_safe_next(request.args.get("next")))
            _ip_failures.setdefault(ip, []).append(time.time())
            if u:
                n = u["failed_attempts"] + 1
                if n >= MAX_FAILURES:
                    _store.update_user(u["id"], failed_attempts=0,
                                        locked_until=(current + dt.timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds"))
                else:
                    _store.update_user(u["id"], failed_attempts=n)
            time.sleep(0.3)
            error = "Feil brukernavn eller passord."
    no_users = _store.user_count() == 0  # on the server: create the admin with manage.py
    no_admin = not no_users and _store.active_admins() == 0  # e.g. users from before there was an admin
    return render_template("login.html", error=error, next_=request.args.get("next", ""),
                           no_users=no_users, no_admin=no_admin), (401 if error else 200)


@bp.route("/oppsett", methods=["GET", "POST"])
def setup():
    """First time, locally: create the admin (you)."""
    if not _can_create_first():
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        if not username or any(c.isspace() for c in username) or not name:
            error = "Fyll inn brukernavn (uten mellomrom) og navn."
        elif password != request.form.get("password2"):
            error = "Passordene er ikke like."
        else:
            error = password_error(password)
        if not error:
            try:
                uid = _store.new_user(username, name, "engineer", hash_password(password), admin=True)
            except sqlite3.IntegrityError:
                error = "Brukernavnet er tatt."
            else:
                _log_in(_store.user(id=uid))
                return redirect("/admin#brukere")  # first time: create users for the others
    return render_template("setup.html", error=error, min_password=MIN_PASSWORD), (400 if error else 200)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
