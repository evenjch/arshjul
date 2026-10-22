"""Årshjul for innkjøp – a Flask app that runs locally or on a server.

The data folder (default: the folder above this one, see config.py):
    BOM/                 the Excel files, the master copy of the components
    Bestillingslister/   generated order lists (HTML)
    arshjul.db           projects, runs per school year, courses, users, orders and counts

Start with «Start årshjul.bat», or:  python app.py   (development: python app.py --debug)
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

from flask import Flask, g as req, jsonify, render_template, request, url_for
from werkzeug.exceptions import NotFound
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import bom as bomlib
import calendar_feed as calendar_feed_lib
import config
import counting
import deadlines
import orders
from config import BOM_DIR, DATA_FILE, LIST_DIR, SNAP_FILE, TEMPLATE_BOM, TEMPLATE_LIST
from db import Db

FIELDS = {"name": str, "type": str, "start": int, "finish": int, "lead": int, "buf": int,
          "students": int, "group_size": int, "spare": int, "note": str, "color": int, "bom": str, "course": int}
# what a lecturer may change on their own projects (the engineer owns lead time, buffer, BOM, colour and course)
LECTURER_FIELDS = {"name", "type", "start", "finish", "students", "group_size", "spare", "note"}
# the endpoints a lecturer may use – everything else is for engineers only
LECTURER_ALLOWED = {"index", "state", "create", "update", "delete", "create_course", "update_course", "calendar_link", "calendar_feed",
                    "wheel_view", "weeks_view", "share_link", "shared_wheel", "shared_wheel_data",
                    "acknowledge_mine", "auth.login", "auth.logout", "static"}
PROGRAM = {"bachelor": "Bachelor", "master": "Master"}
# admin only
ADMIN_ENDPOINTS = {"create_user", "update_user"}

app = Flask(__name__)
app.json.ensure_ascii = False
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_NAME="arshjul",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not config.LOCAL,  # HTTPS only on the server
    PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=config.SESSION_HOURS),
)
if config.BEHIND_PROXY:
    # Caddy/nginx in front: the right client address (lock-out per IP) and https URLs
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
_lock = threading.Lock()
store = Db(config.DB_FILE)


class Forbidden(Exception):
    """Logged in, but not allowed (403)."""


def init() -> None:
    """Create the folders and the database (the very first time, arshjul.json is imported)."""
    BOM_DIR.mkdir(parents=True, exist_ok=True)
    LIST_DIR.mkdir(exist_ok=True)
    config.move_old_cache()
    config.convert_old_snapshot()
    store.init(DATA_FILE)


_ready = False


@app.before_request
def _ensure_init():
    """Also when the app is started by another server than main() (e.g. on a VPS)."""
    global _ready
    if not _ready:
        with _lock:
            if not _ready:
                init()
                _ready = True


auth.init(app, store)  # after _ensure_init: login needs the database


@app.before_request
def _role():
    if req.user and not auth.is_engineer() and request.endpoint not in LECTURER_ALLOWED:
        if request.path.startswith("/api/"):
            return jsonify({"error": "Bare ingeniøren kan gjøre dette."}), 403
        return "Ikke tilgang", 403
    is_admin_page = request.endpoint in ADMIN_ENDPOINTS or (request.endpoint or "").startswith("admin_panel.")
    if req.user and is_admin_page and not auth.is_admin():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Bare administratoren kan gjøre dette."}), 403
        return "Ikke tilgang", 403
    return None


def can_handle(g: dict, user: dict | None = None) -> bool:
    """May the user (default: the logged-in user) do something with this run?

    Engineer (also the admin – on the main page they're a regular engineer): projects in courses
    they're responsible for, and projects without a course or without an engineer (so nothing is left behind).
    Lecturer: projects in their own courses.
    The admin panel (/api/admin/…) has its own endpoints that see and fix everything.
    """
    me = user or auth.me()
    if me["role"] == "engineer":
        return g["engineerId"] in (None, me["id"])
    return g["lecturerId"] is not None and g["lecturerId"] == me["id"]


def course_visible(e: dict, user: dict | None = None) -> bool:
    """A course waiting for the admin's approval is only seen (and used) by those on it: whoever proposed it,
    its lecturer and its engineer. The admin sees everything."""
    me = user or auth.me()
    return bool(e["approved"]) or bool(me["admin"]) or me["id"] in (e["proposedBy"], e["lecturerId"], e["engineerId"])


@app.before_request
def _ownership():
    """Anything that changes a particular run requires it to be yours (see can_handle)."""
    gid = (request.view_args or {}).get("gid")
    if gid is None or request.method in ("GET", "HEAD", "OPTIONS") or req.user is None:
        return None
    if (request.endpoint or "").startswith("admin_panel.") and auth.is_admin():
        return None  # the admin panel fixes things in every project (and logs it)
    try:
        g = store.run(gid)
    except KeyError:
        if request.endpoint == "acknowledge_changes" and auth.is_engineer():
            return None  # notifications about a project that has been deleted can always be acknowledged
        return jsonify({"error": "Fant ikke prosjektet."}), 404
    if not can_handle(g):
        if auth.is_engineer():
            who = g["engineerName"] or "en annen ingeniør"
            return jsonify({"error": f"Prosjektet hører til {g['courseCode']}, som {who} har ansvaret for."}), 403
        return jsonify({"error": "Du kan bare endre prosjekter i dine egne emner."}), 403
    return None


@app.after_request
def _security(resp):
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


@app.errorhandler(Forbidden)
def forbidden(e):
    return jsonify({"error": str(e)}), 403


# ---------- endringslogg ----------

FIELD_LABELS = {"name": "navn", "type": "type", "start": "uke fra", "finish": "uke til", "sets": "antall sett",
            "students": "studenter", "group_size": "per gruppe", "spare": "ekstra sett", "bomSets": "antall sett i BOM-en", "orderedWithoutBom": "bestilt (uten BOM)", "note": "notat", "lead": "leveringstid", "buf": "buffer", "bom": "BOM", "color": "farge",
            "courseId": "emne", "school_year": "skoleår"}


def changes(g0: dict, g1: dict, body: dict) -> dict:
    """{field: (old, new)} for the fields in body that actually changed – and the set count, which follows from them."""
    changed = {}
    for k in [*body, "sets"]:
        col = "courseId" if k == "course_id" else k
        if g0[col] != g1[col]:
            changed[col] = (g0["courseCode"], g1["courseCode"]) if col == "courseId" else (g0[col], g1[col])
    return changed


def log(g: dict, action: str, changed: dict | None = None, role: str | None = None) -> None:
    """Write to the change log. changed: {field: (old, new)}. Only lecturers' changes are notified.
    role="admin" for corrections made in the admin panel."""
    me = auth.me()
    common = {"user_id": me["id"], "user_name": me["name"], "role": role or me["role"],
              "run_id": int(g["id"]), "project_name": g["name"], "school_year": g["school_year"], "action": action}
    if changed:
        store.log([{**common, "field": FIELD_LABELS.get(k, k), "from_value": from_, "to_value": to} for k, (from_, to) in changed.items()])
    else:
        store.log([common])


# ---------- storing "last seen" ----------

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


# ---------- opening files and folders ----------

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
    file = p.get("bom")
    if not file:
        return None, False, None
    path = BOM_DIR / file
    if not path.exists():
        return {"file": file, "exists": False}, False, None
    try:
        b = read_bom_cached(path)
    except bomlib.BomError as e:
        return {"file": file, "exists": True, "error": str(e)}, False, None
    sets = p.get("sets") or b["sets"] or 0
    c = bomlib.calc(b, sets)
    changed = False
    snap = snaps.get(file)
    if snap is None:
        snaps[file] = {"rows": b["rows"], "read": b["changed"]}
        changed, changes, last_read = True, [], b["changed"]
    else:
        changes, last_read = bomlib.diff(snap["rows"], b["rows"]), snap.get("read", "")
    return {
        "file": file, "exists": True, "lines": c["lines"], "to_order": c["to_order"], "excl": c["excl"],
        "incl": c["incl"], "changed": b["changed"], "setsInFile": b["sets"], "projectFile": b["project"],
        "changes": changes, "lastRead": last_read,
    }, changed, c


def count_status(g: dict, c: dict | None, st: dict | None, saved: dict, cnt: dict | None,
                   types: dict) -> dict | None:
    """The saved count, or the basis for a new one (from the BOM as it is now)."""
    if cnt:
        saved_ = {**cnt, **counting.summary(cnt["lines"]),
                   "pending": len(counting.pending(cnt["lines"], cnt["written_values"])),
                   "inBom": len(cnt["written_values"]),
                   "changedInExcel": counting.changed_in_bom(cnt["written_values"], c["rows"] if c else [])}
        return {"saved": saved_, "basis": None, "assumedOrdered": False}
    if not c or not c["lines"]:
        return None
    sets = g.get("sets") or (st or {}).get("setsInFile") or 0
    basis = counting.basis(c["rows"], counting.ordered_per_line(saved), types.get(g["projectId"], {}), sets)
    return {"saved": None, "basis": basis, "assumedOrdered": any(l["assumed"] for l in basis)}


def bom_path(g: dict) -> Path:
    path = BOM_DIR / (g.get("bom") or "")
    if not g.get("bom") or not path.exists():
        raise bomlib.BomError("Fant ikke BOM-filen i BOM-mappen.")
    return path


# ---------- API ----------

@app.get("/")
def index():
    return render_template("index.html")


# what a lecturer gets to see of a run – not BOM, orders, counts or costs
_PUBLIC = ("id", "projectId", "school_year", "start", "finish", "sets", "students", "group_size", "spare",
              "name", "type", "lead", "buf", "note", "color", "courseId", "courseCode", "courseName", "program", "courseStudents",
              "lecturerId", "lecturerName", "engineerId", "engineerName")


def _school_years(rows: list[dict], closed: list[dict], today: dt.date) -> dict:
    """The school years the wheel can show (this one, the next and any with runs), today and the next 8 weeks."""
    current = deadlines.school_year_for(today)
    year = {current, current + 1} | {g["school_year"] for g in rows}
    monday = today - dt.timedelta(days=today.weekday())
    return {"today": deadlines.week_info(today), "schoolYearNow": current, "schoolYearShown": deadlines.school_year_shown(today),
            "school_year": [{"year": y, "name": deadlines.school_year_name(y), "start": deadlines.school_year_start(y).isoformat(),
                             "weeks": deadlines.school_year_weeks(y, closed)} for y in range(min(year), max(year) + 1)],
            "next8": [deadlines.week_info(monday + i * deadlines.WEEK, closed) for i in range(8)]}


@app.get("/api/state")
def state():
    closed = store.closed_periods()
    # You only see what you're involved in: lecturers their own courses, engineers their own courses (and projects
    # without a course/engineer), the admin everything. The rest isn't even sent to the browser.
    all_runs = [g for g in store.runs() if can_handle(g)]
    rows = [g for g in all_runs if not g["deleted"]]
    eng = auth.is_engineer()
    me = auth.me()
    out = []
    if eng:
        ordered = store.all_orders()
        counts = store.counts()
        types = store.line_types()
        active = active_bom_runs(closed)
        with _lock:
            snaps = _read_json(SNAP_FILE, {})
            dirty = False
            for g in rows:
                st, ch, c = bom_status(g, snaps)
                dirty |= ch
                f = deadlines.deadlines(g, closed)
                saved = ordered.get(g["id"], {})
                # "needs more" only makes sense before the project starts and before it has been counted
                compare = f["start"]["date"] > dt.date.today().isoformat() and g["id"] not in counts
                overview = orders.overview(c["rows"], saved, compare) if c and c["lines"] else None
                out.append({**g, "semester": deadlines.semester(g["start"]), "events": f.pop("events"),
                            "deadlines": f, "bomStatus": st, "orders": overview,
                            "orderedCost": orders.cost(saved), "own": can_handle(g),
                            "bomSetsActive": active.get(g["bom"]) == g["id"],
                            "count": count_status(g, c, st, saved, counts.get(g["id"]), types)})
            if dirty:
                _write_json(SNAP_FILE, snaps)
    else:
        for g in rows:
            f = deadlines.deadlines(g, closed)
            out.append({**{k: g[k] for k in _PUBLIC}, "semester": deadlines.semester(g["start"]),
                        "events": f.pop("events"), "deadlines": f, "own": can_handle(g)})

    today = dt.date.today()
    reply = {"projects": out, "closed_periods": closed, "mode": config.MODE, **_school_years(rows, closed, today),
            "me": {"id": me["id"], "name": me["name"], "role": me["role"], "admin": me["admin"]},
            "users": [],
            "freeBoms": [], "setup": None, "folders": {}, "changes": [], "deletedRuns": [],
            "courses": [], "lecturers": [], "engineers": [], "program": PROGRAM,
            "types": {k: t["name"] for k, t in deadlines.TYPES.items()}}
    courses = [e for e in store.courses() if course_visible(e, me)]
    if eng:
        mine = [g for g in all_runs if can_handle(g)]  # notifications and reminders are about your own courses
        mine_ids = {int(g["id"]) for g in mine}
        existing = {int(g["id"]) for g in store.runs()}  # all of them, other people's too – for deleted projects
        linked = {g.get("bom") for g in store.runs()}  # other people's too – a BOM belongs to one project
        reply["freeBoms"] = sorted(f.name for f in BOM_DIR.glob("*.xlsx")
                                   if not f.name.startswith("~$") and f.name not in linked) if BOM_DIR.exists() else []
        reply["setup"] = deadlines.setup_warning([g for g in mine if not g["deleted"]], closed, today)
        reply["folders"] = {"bom": str(BOM_DIR), "lists": str(LIST_DIR)}
        def is_mine(e):
            if e["recipient_id"] is not None:  # a notification to one particular person (e.g. "you have been chosen as engineer")
                return e["recipient_id"] == me["id"]
            # a lecturer's changes to a project: the course's engineer (a new engineer takes over the unacknowledged ones)
            return e["run_id"] in mine_ids or e["run_id"] not in existing
        reply["changes"] = [e for e in store.changes() if is_mine(e)]
        reply["deletedRuns"] = [{k: g[k] for k in ("id", "name", "school_year", "start", "finish", "deleted", "deletedBy", "courseCode")}
                            for g in mine if g["deleted"]]
        users = [u for u in store.users() if u["active"]]
        reply["lecturers"] = [{"id": u["id"], "name": u["name"]} for u in users if u["role"] == "lecturer"]
        reply["engineers"] = [{"id": u["id"], "name": u["name"]} for u in users if u["role"] == "engineer"]
        reply["courses"] = courses
    else:
        # lecturers manage their own courses and choose the engineer for them
        reply["courses"] = [e for e in courses if e["lecturerId"] == me["id"]]
        reply["changes"] = [e for e in store.changes() if e["recipient_id"] == me["id"]]  # e.g. "your course was approved"
        reply["engineers"] = [{"id": u["id"], "name": u["name"]} for u in store.users()
                              if u["active"] and u["role"] == "engineer"]
    return jsonify(reply)


@app.post("/api/school-years/<int:from_>/copy")
def copy_year(from_):
    """Copy runs to the next school year. «ids» chooses which (default: all).
    «changes»: {id: {start, finish, students, group_size, spare}} for what is different next year."""
    body = request.get_json(silent=True) or {}
    ids, raw = body.get("ids"), body.get("changes") or {}
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, (str, int)) for i in ids)):
        raise bomlib.BomError("Ugyldig liste over prosjekter.")
    if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
        raise bomlib.BomError("Ugyldige endringer.")
    allowed = {"start", "finish", "students", "group_size", "spare"}
    changes = {str(k): _clean({f: v[f] for f in allowed if f in v}, partial=True) for k, v in raw.items()}
    # only projects in your own courses (and without a course) are copied
    mine = {g["id"] for g in store.runs() if g["school_year"] == from_ and can_handle(g)}
    chosen = mine if ids is None else {str(i) for i in ids} & mine
    created = store.copy_year(from_, sorted(chosen), changes)
    return jsonify({"created": created, "to": from_ + 1, "name": deadlines.school_year_name(from_ + 1)})


def _clean(body: dict, partial: bool) -> dict:
    out = {}
    for k, typ in FIELDS.items():
        if k not in body:
            continue
        v = body[k]
        if v in (None, "") and k in ("students", "bom", "note", "course"):
            out[k] = None if k != "note" else ""
            continue
        try:
            out[k] = typ(v)
        except (TypeError, ValueError):
            raise bomlib.BomError(f"Ugyldig verdi for {k}.")
    for k, lo, hi in (("start", 1, 53), ("finish", 1, 53), ("lead", 0, 20), ("buf", 0, 20),
                      ("students", 0, 2000), ("group_size", 1, 20), ("spare", 0, 99)):
        if out.get(k) is not None:
            out[k] = min(hi, max(lo, out[k]))
    if "type" in out and out["type"] not in deadlines.TYPES:
        raise bomlib.BomError("Ugyldig type.")
    if "name" in out:
        out["name"] = out["name"].strip()
        if not out["name"]:
            raise bomlib.BomError("Gi prosjektet et navn.")
    elif not partial:
        raise bomlib.BomError("Gi prosjektet et navn.")
    return out


@app.errorhandler(bomlib.BomError)
def bom_error(e):
    return jsonify({"error": str(e)}), 400


@app.errorhandler(KeyError)
def not_found(e):
    return jsonify({"error": "Fant ikke prosjektet."}), 404


@app.post("/api/runs")
def create():
    raw = request.get_json(force=True)
    body = _clean(raw, partial=False)
    try:
        sy = int(raw.get("school_year") or deadlines.school_year_for(dt.date.today()))
    except (TypeError, ValueError):
        raise bomlib.BomError("Ugyldig skoleår.")
    if not 2000 <= sy <= 2100:
        raise bomlib.BomError("Ugyldig skoleår.")
    course = _course(body.pop("course", None))
    if not auth.is_engineer():
        # a lecturer puts the project in one of their courses; lead time, buffer and BOM get the engineer's defaults
        if course is None or course["lecturerId"] != auth.me()["id"] or not course["active"]:
            raise Forbidden("Velg ett av dine emner.")
        body = {k: v for k, v in body.items() if k in LECTURER_FIELDS or k == "color"}
    body["course_id"] = course["id"] if course else None
    if body.get("students") is None and course:
        body["students"] = course["students"]  # the course's number is the default; the project may differ
    slug = re.sub(r"[^a-z0-9]+", "-", body["name"].lower()).strip("-")[:24] or "project"
    g = store.new(f"{slug}-{secrets.token_hex(2)}", sy, body)
    log(g, "created")
    return jsonify(g)


def _course(cid) -> dict | None:
    if cid in (None, "", 0):
        return None
    try:
        e = store.course(int(cid))
    except (KeyError, TypeError, ValueError):
        raise bomlib.BomError("Fant ikke emnet.")
    if not course_visible(e):
        raise bomlib.BomError("Fant ikke emnet.")  # someone else's proposal
    return e


def _active(gid: str) -> dict:
    """The run, if it hasn't been removed. (Whether it's yours is checked in _ownership.)"""
    g = store.run(gid)
    if g["deleted"]:
        raise KeyError(gid)
    return g


@app.patch("/api/runs/<gid>")
def update(gid):
    body = _clean(request.get_json(force=True), partial=True)
    g0 = _active(gid)
    if not auth.is_engineer():
        not_allowed = set(body) - LECTURER_FIELDS
        if not_allowed:
            raise Forbidden("Det er ingeniøren som endrer " + ", ".join(sorted(FIELD_LABELS.get(k, k) for k in not_allowed)) + ".")
    if "course" in body:
        course = _course(body.pop("course"))
        body["course_id"] = course["id"] if course else None
    g1 = store.update(gid, body)
    changed = changes(g0, g1, body)
    if changed:
        log(g1, "changed", changed)
    return jsonify({**g1, "bomSets": sets_to_bom(g0, g1)})


@app.delete("/api/runs/<gid>")
def delete(gid):
    """Lecturer: removed (softly) until the engineer confirms. Engineer: deleted – also a removed project."""
    g = store.run(gid)
    if auth.is_engineer():
        store.delete(gid)
        store.acknowledge(gid)
        log(g, "deleted")
        return jsonify({"ok": True})
    g = _active(gid)
    store.soft_delete(gid, auth.me()["name"])
    log(g, "removed")
    return jsonify({"ok": True, "waiting": True})


# ---------- courses (engineers: all; lecturers: their own) ----------

def _course_fields(body: dict, is_new: bool) -> dict:
    out = {}
    if "code" in body or is_new:
        code = str(body.get("code") or "").strip().upper()
        if not code or len(code) > 20:
            raise bomlib.BomError("Gi emnet en emnekode, f.eks. IELS1001.")
        out["code"] = code
    if "name" in body:
        out["name"] = str(body["name"] or "").strip()[:120]
    if "program" in body:
        if body["program"] not in PROGRAM:
            raise bomlib.BomError("Studieprogram må være bachelor eller master.")
        out["program"] = body["program"]
    if "students" in body:
        try:
            out["students"] = None if body["students"] in (None, "") else min(2000, max(0, int(body["students"])))
        except (TypeError, ValueError):
            raise bomlib.BomError("Antall studenter må være et tall.")
    for fields, role, text in (("lecturer", "lecturer", "faglærer"), ("engineer", "engineer", "ingeniør")):
        if fields in body:
            uid = body[fields]
            if uid in (None, "", 0):
                out[f"{fields}_id"] = None
                continue
            try:
                u = store.user(id=int(uid))
            except (TypeError, ValueError):
                u = None
            if not u or u["role"] != role or not u["active"]:
                raise bomlib.BomError(f"Fant ikke {text}en.")
            out[f"{fields}_id"] = u["id"]
    if "active" in body:
        out["active"] = 1 if body["active"] else 0
    return out


def _notify_engineer(from_: dict | None, to: dict) -> None:
    """Tell the engineer who was chosen (and the one who was taken off) – but not yourself."""
    me = auth.me()
    common = {"user_id": me["id"], "user_name": me["name"], "role": me["role"], "course_id": to["id"],
              "project_name": to["code"], "field": "ingeniør"}
    rows = []
    new, old_count = to["engineerId"], from_["engineerId"] if from_ else None
    if new == old_count:
        return
    if new and new != me["id"]:
        rows.append({**common, "action": "engineer_assigned", "recipient_id": new, "to_value": to["engineerName"],
                      "from_value": from_["engineerName"] if from_ else None})
    if old_count and old_count != me["id"]:
        rows.append({**common, "action": "engineer_removed", "recipient_id": old_count, "from_value": from_["engineerName"],
                      "to_value": to["engineerName"]})
    if rows:
        store.log(rows)


@app.post("/api/courses")
def create_course():
    """The admin adds courses. Everyone else proposes one, which waits for the admin's approval
    (but those on it can use it straight away). A lecturer proposes their own (and becomes its lecturer)."""
    body = request.get_json(force=True) or {}
    me = auth.me()
    if not auth.is_engineer():
        if "lecturer" in body and body["lecturer"] not in (None, "", me["id"]):
            raise Forbidden("Du kan bare foreslå emner der du selv er faglærer.")
        body = {**body, "lecturer": me["id"]}
    fields = _course_fields(body, is_new=True)
    fields.pop("active", None)
    if not auth.is_admin():
        fields.update(approved=0, proposed_by=me["id"])
    try:
        e = store.new_course(fields)
    except sqlite3.IntegrityError:
        raise bomlib.BomError("Det finnes allerede et emne med den koden (kanskje venter det på godkjenning).")
    _notify_engineer(None, e)
    return jsonify(e)


@app.patch("/api/courses/<int:cid>")
def update_course(cid):
    """Engineers change every course; a lecturer their own – but not who the lecturer is."""
    e0 = store.course(cid)  # 404 if it doesn't exist
    if not course_visible(e0):
        raise KeyError(cid)
    body = request.get_json(force=True) or {}
    if not auth.is_engineer():
        if e0["lecturerId"] != auth.me()["id"]:
            raise Forbidden("Du kan bare endre dine egne emner.")
        if "lecturer" in body:
            raise Forbidden("Det er administratoren eller en ingeniør som bytter faglærer på et emne.")
    try:
        e1 = store.update_course(cid, _course_fields(body, is_new=False))
    except sqlite3.IntegrityError:
        raise bomlib.BomError("Det finnes allerede et emne med den koden.")
    _notify_engineer(e0, e1)
    return jsonify(e1)


@app.post("/api/changes/acknowledge")
def acknowledge_mine():
    """«OK, sett» on notifications addressed to you (e.g. "you have been chosen as engineer")."""
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise bomlib.BomError("Ugyldige varsler.")
    return jsonify({"acknowledged": store.acknowledge_to(auth.me()["id"], ids)})


# ---------- brukere (administratoren) ----------

def _user_fields(body: dict, new: bool) -> dict:
    out = {}
    if new:
        username = str(body.get("username") or "").strip()
        if not username or any(c.isspace() for c in username) or len(username) > 40:
            raise bomlib.BomError("Brukernavnet kan ikke være tomt eller inneholde mellomrom.")
        out["username"] = username
    if "name" in body or new:
        name = str(body.get("name") or "").strip()[:80]
        if not name:
            raise bomlib.BomError("Gi brukeren et navn.")
        out["name"] = name
    if "role" in body or new:
        if body.get("role") not in auth.ROLES:
            raise bomlib.BomError("Rollen må være ingeniør eller faglærer.")
        out["role"] = body["role"]
    if "admin" in body:
        out["admin"] = 1 if body["admin"] else 0
    if "active" in body:
        out["active"] = 1 if body["active"] else 0
    if "password" in body or new:
        try:
            out["password"] = auth.hash_password(str(body.get("password") or ""))
        except ValueError as e:
            raise bomlib.BomError(str(e))
    if out.get("role") == "lecturer":
        out["admin"] = 0  # the admin is always an engineer
    return out


def _user_out(u: dict) -> dict:
    return {k: (bool(u[k]) if k in ("admin", "active") else u[k])
            for k in ("id", "username", "name", "role", "admin", "active", "last_login", "locked_until")}


@app.post("/api/users")
def create_user():
    f = _user_fields(request.get_json(force=True) or {}, new=True)
    try:
        uid = store.new_user(f["username"], f["name"], f["role"], f["password"], admin=bool(f.get("admin")))
    except sqlite3.IntegrityError:
        raise bomlib.BomError(f"Brukernavnet «{f['username']}» er tatt.")
    return jsonify(_user_out(store.user(id=uid)))


@app.patch("/api/users/<int:uid>")
def update_user(uid):
    u = store.user(id=uid)
    if not u:
        raise KeyError(uid)
    f = _user_fields(request.get_json(force=True) or {}, new=False)
    new = {**u, **f}
    # there must always be an active admin, otherwise nobody can get into «Brukere» again
    was_admin = u["admin"] and u["active"]
    becomes_admin = new["admin"] and new["active"] and new["role"] == "engineer"
    if was_admin and not becomes_admin and store.active_admins() <= 1:
        raise bomlib.BomError("Det må finnes minst én aktiv administrator.")
    if f.get("role", u["role"]) != u["role"]:
        in_courses = [e["code"] for e in store.courses() if uid in (e["lecturerId"], e["engineerId"])]
        if in_courses:
            raise bomlib.BomError("Brukeren står på " + ", ".join(in_courses) + ". Bytt person der først.")
    if "password" in f or "active" in f:
        f.update(failed_attempts=0, locked_until=None)  # new password or activated: unlock
    store.update_user(uid, **f)
    return jsonify(_user_out(store.user(id=uid)))


@app.post("/api/runs/<gid>/restore")
def restore(gid):
    """Undo a lecturer removing a project."""
    g = store.run(gid)
    if not g["deleted"]:
        raise bomlib.BomError("Prosjektet er ikke fjernet.")
    store.restore(gid)
    store.acknowledge(gid)
    log(g, "restored")
    return jsonify({"ok": True})


@app.post("/api/changes/<gid>/acknowledge")
def acknowledge_changes(gid):
    """«OK, sett» on a lecturer's changes to a run."""
    return jsonify({"acknowledged": store.acknowledge(gid)})


@app.post("/api/runs/<gid>/template")
def make_template(gid):
    g = store.run(gid)
    deadline = dt.date.fromisoformat(deadlines.deadlines(g, store.closed_periods())["deadline"]["date"])
    target = BOM_DIR / f"{safe_name(g['name'])} – BOM.xlsx"
    BOM_DIR.mkdir(exist_ok=True)
    bomlib.make_template(TEMPLATE_BOM, target, project=g["name"], date=dt.date.today(), deadline=deadline, sets=g.get("sets"))
    store.update(gid, {"bom": target.name})
    open_path(target)
    return jsonify({"file": target.name})


def only_local() -> None:
    if not config.LOCAL:
        raise bomlib.BomError("Dette fungerer bare når årshjulet kjører på din egen PC.")


@app.post("/api/runs/<gid>/open")
def open_bom(gid):
    only_local()
    open_path(bom_path(store.run(gid)))
    return jsonify({"ok": True})


def active_bom_runs(closed: list[dict]) -> dict[str, str]:
    """BOM file → the run whose set count the BOM (B10) should follow.

    Several school years share one BOM, but B10 holds only one number: it belongs to the next run
    that hasn't started and hasn't been counted. Finished runs no longer care about B10.
    """
    today, counted, next_ = dt.date.today().isoformat(), store.counts(), {}
    for g in store.runs():
        if not g["bom"] or g["deleted"] or g["id"] in counted:
            continue
        start = deadlines.deadlines(g, closed)["start"]["date"]
        if start > today and (g["bom"] not in next_ or start < next_[g["bom"]][0]):
            next_[g["bom"]] = (start, g["id"])
    return {bom: gid for bom, (_, gid) in next_.items()}


def sets_to_bom(g0: dict, g1: dict) -> dict | None:
    """The set count has changed: write it to the BOM's B10, which Excel computes «Å bestille» from.

    Written only for the run B10 follows (see active_bom_runs), when the file isn't open in Excel,
    and when B10 still holds the app's previous number (not changed by hand). Otherwise it shows up
    as a mismatch for the engineer, who can write it with a button (write_bom_sets). The change in the
    app applies either way. error=True: something needs doing (shown in red); otherwise just for information.
    """
    if g0["sets"] == g1["sets"] or not g1.get("bom") or not (BOM_DIR / g1["bom"]).exists():
        return None
    active = active_bom_runs(store.closed_periods()).get(g1["bom"])
    if active != g1["id"]:
        if active is None:
            return {"written": False, "error": False, "reason": "Prosjektet har startet eller er telt – BOM-en er ikke endret."}
        a = store.run(active)
        return {"written": False, "error": False,
                "reason": f"BOM-en følger «{a['name']}» {deadlines.school_year_name(a['school_year'])} til det har startet – ikke endret nå."}
    try:
        with _lock:
            res = bomlib.write_sets(BOM_DIR / g1["bom"], g1["sets"], BOM_DIR / "Sikkerhetskopi", expected=g0["sets"])
    except bomlib.BomError as e:
        return {"written": False, "error": True, "reason": f"{e} BOM-en har fortsatt det gamle antallet."}
    if res.get("conflict"):
        res["error"] = True
        res["reason"] = f"Antall sett i BOM-en er endret i Excel ({res['file']}) – ikke overskrevet."
    if res["written"]:
        log(g1, "changed", {"bomSets": (res["for"], res["file"])})
    return res


# ---------- the wheel and «neste 8 uker» on their own, and the wheel shared with students ----------
# The page is index.html with data-view: the JS draws only that part, and the CSS hides the rest.

@app.get("/hjul")
def wheel_view():
    return render_template("index.html", view="wheel")


@app.get("/uker")
def weeks_view():
    return render_template("index.html", view="weeks")


def _shared_user(token: str) -> dict:
    user = store.user_by_share_token(token) if token else None
    if not user:
        raise NotFound()
    return user


@app.get("/hjul/<token>")
def shared_wheel(token):
    """Someone's wheel, for students: open without login – the secret link is the key (like the calendar)."""
    try:
        user = _shared_user(token)
    except NotFound:
        return "Lenken virker ikke lenger. Be om en ny.", 404
    return render_template("index.html", view="wheel", share=token, shared_by=user["name"])


@app.get("/hjul/<token>/data")
def shared_wheel_data(token):
    """Only what the shared wheel draws: project names and the weeks they're in use – as on a lecturer's wheel.
    No deadlines, orders, BOMs, courses or people are sent."""
    user = _shared_user(token)
    closed = store.closed_periods()
    rows = [g for g in store.runs() if not g["deleted"] and can_handle(g, user)]
    out = []
    for g in rows:
        d = deadlines.deadlines(g, closed)
        out.append({"id": g["id"], "school_year": g["school_year"], "name": g["name"], "type": g["type"], "color": g["color"],
                    "start": g["start"], "finish": g["finish"], "semester": deadlines.semester(g["start"]),
                    "deadlines": {"start": d["start"], "finish": d["finish"]}, "events": []})
    resp = jsonify({"projects": out, "closed_periods": [], "mode": config.MODE, **_school_years(rows, closed, dt.date.today()),
                    "me": {"id": None, "name": "", "role": "lecturer", "admin": False},
                    "users": [], "freeBoms": [], "setup": None, "folders": {}, "changes": [], "deletedRuns": [],
                    "courses": [], "lecturers": [], "engineers": [], "program": PROGRAM,
                    "types": {k: t["name"] for k, t in deadlines.TYPES.items()}})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/share", methods=["GET", "POST"])
def share_link():
    """Your link for sharing the wheel. GET: the link, or null. POST: make one; {"new": true} makes a new one
    (the old one stops working); {"stop": true} stops sharing."""
    me = store.user(id=auth.me()["id"])
    token = me["share_token"]
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if body.get("stop"):
            token = None
        elif not token or body.get("new"):
            token = secrets.token_urlsafe(24)
        store.update_user(me["id"], share_token=token)
    return jsonify({"url": url_for("shared_wheel", token=token, _external=True) if token else None})


# ---------- calendar feed ----------

@app.get("/calendar/<token>.ics")
def calendar_feed(token):
    """The user's calendar (see calendar_feed.py). Open without login – the secret link is the key, and
    unknown or old links give 404. ?download=1 saves it as a file for a one-off import."""
    user = store.user_by_calendar_token(token) if token else None
    if not user:
        return "Ukjent kalenderlenke", 404
    runs = [g for g in store.runs() if can_handle(g, user)]
    body = calendar_feed_lib.build(runs, store.closed_periods(), user)
    resp = app.response_class(body, mimetype="text/calendar")
    resp.headers["Content-Disposition"] = ("attachment" if request.args.get("download") else "inline") + '; filename="arshjul.ics"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/calendar")
def calendar_link():
    """Your private calendar link. {"new": true} makes a new one – the old link stops working."""
    me = store.user(id=auth.me()["id"])
    token = me["calendar_token"]
    if not token or (request.get_json(silent=True) or {}).get("new"):
        token = secrets.token_urlsafe(24)
        store.update_user(me["id"], calendar_token=token)
    return jsonify({"url": url_for("calendar_feed", token=token, _external=True), "local": config.LOCAL})


@app.post("/api/runs/<gid>/ordered")
def order_without_bom(gid):
    """Engineer: projects without a BOM are marked as ordered (or not), so the deadline doesn't show as passed."""
    g = _active(gid)
    if g["bom"]:
        raise bomlib.BomError("Prosjektet har en BOM – registrer bestillingen per leverandør.")
    ordered = bool((request.get_json(silent=True) or {}).get("ordered"))
    if ordered == bool(g["orderedWithoutBom"]):
        return jsonify(g)
    store.ordered_without_bom(gid, dt.date.today().isoformat() if ordered else None, auth.me()["name"] if ordered else None)
    log(g, "changed", {"orderedWithoutBom": ("nei" if ordered else "ja", "ja" if ordered else "nei")})
    return jsonify(store.run(gid))


@app.post("/api/runs/<gid>/bom/sets")
def write_bom_sets(gid):
    """Engineer: write the app's set count to the BOM (B10), also over whatever is there."""
    g = _active(gid)
    with _lock:
        res = bomlib.write_sets(bom_path(g), g["sets"], BOM_DIR / "Sikkerhetskopi")
    if res["written"]:
        log(g, "changed", {"bomSets": (res["for"], res["file"])})
    return jsonify(res)


def calculate(g: dict) -> tuple[dict, dict, int, Path]:
    """(bom, calculation, sets, path) for a run."""
    path = bom_path(g)
    b = bomlib.read_bom(path)
    sets = g.get("sets") or b["sets"] or 0
    return b, bomlib.calc(b, sets), sets, path


@app.post("/api/runs/<gid>/order-list")
def order_list(gid):
    """An order list for the whole BOM, or just one supplier if «supplier» is given."""
    g = store.run(gid)
    b, c, sets, path = calculate(g)
    name = safe_name(b["project"] or g["name"])
    supplier = (request.get_json(silent=True) or {}).get("supplier")
    if supplier is not None:
        gr = orders.group(c["rows"], orders.supplier_key(supplier))
        if gr is None:
            raise bomlib.BomError("Fant ikke leverandøren i BOM-en.")
        excl = round(sum(r["total"] for r in gr["rows"]), 2)
        c = {"rows": gr["rows"], "lines": len(gr["rows"]), "to_order": sum(1 for r in gr["rows"] if r["to_order"] > 0),
             "excl": excl, "incl": round(excl * (1 + b["vat"]), 2)}
        name += " – " + safe_name(gr["name"])
    LIST_DIR.mkdir(exist_ok=True)
    target = LIST_DIR / f"Bestilling – {name}.html"
    bomlib.make_order_list(TEMPLATE_LIST, target, bom=b, calculated=c, project=g["name"], sets=sets,
                           list_id=f"{g['projectId']}-{g['id']}-{int(path.stat().st_mtime)}")
    open_path(target)
    return jsonify({"file": target.name, "lines": c["to_order"]})


@app.post("/api/runs/<gid>/order")
def order_status(gid):
    """Register an order with one supplier.

    action: «ordered» (new, freezes the lines), «extra» (ordered what was missing),
            «status» (ordered / partial / received), «order_no», «undo» (remove the registration)
    """
    body = request.get_json(force=True) or {}
    action = body.get("action")
    key = orders.supplier_key(body.get("supplier"))
    g = store.run(gid)
    saved = store.all_orders().get(gid, {}).get(key)
    order_no = str(body.get("order_no") or "").strip()[:100]

    if action in ("ordered", "extra"):
        _, c, sets, _ = calculate(g)
        gr = orders.group(c["rows"], key)
        lines = orders.freeze(gr["rows"]) if gr else []
        if action == "ordered":
            if saved:
                raise bomlib.BomError("Denne leverandøren er allerede registrert som bestilt.")
            if not lines:
                raise bomlib.BomError("Det er ingenting å bestille hos denne leverandøren.")
            date = body.get("date") or dt.date.today().isoformat()
            try:
                dt.date.fromisoformat(date)
            except (TypeError, ValueError):
                raise bomlib.BomError("Ugyldig dato.")
            store.new_order(gid, key, gr["name"], date=date, order_no=order_no, sets=sets, lines=lines, by=auth.me()["name"])
        else:
            if not saved:
                raise bomlib.BomError("Registrer først den vanlige bestillingen.")
            fields = {"lines": orders.merge(saved["lines"], lines), "status": "ordered",
                    "received_date": None, "sets": sets}
            if order_no:
                fields["order_no"] = f"{saved['order_no']} + {order_no}" if saved["order_no"] else order_no
            store.update_order(gid, key, **fields)
    elif action == "status":
        status = body.get("status")
        if status not in orders.SAVED_STATUSES:
            raise bomlib.BomError("Ugyldig status.")
        if not saved:
            raise bomlib.BomError("Leverandøren er ikke registrert som bestilt.")
        if status == "ordered" and any(saved["stocked"].values()):
            raise bomlib.BomError("Noe fra denne bestillingen er lagt på lager. Angre mottaket først.")
        received = saved["received_date"] or dt.date.today().isoformat()
        store.update_order(gid, key, status=status, received_date=None if status == "ordered" else received)
    elif action == "order_no":
        if not saved:
            raise bomlib.BomError("Leverandøren er ikke registrert som bestilt.")
        store.update_order(gid, key, order_no=order_no)
    elif action == "undo":
        if saved and any(saved["stocked"].values()):
            raise bomlib.BomError("Noe fra denne bestillingen er lagt på lager. Angre mottaket først.")
        store.delete_order(gid, key)
    elif action in ("receipt", "undo_receipt"):
        return jsonify(_receipt(g, gid, key, saved, action, body.get("lines")))
    else:
        raise bomlib.BomError("Ukjent handling.")
    return jsonify({"ok": True})


def _receipt(g: dict, gid: str, key: str, saved: dict | None, action: str, lines: dict | None) -> dict:
    """Receipt: add what has arrived to «På lager» in the BOM, or subtract it again.

    receipt:      lines = {key: quantity that arrived}; without lines: everything still outstanding.
                  The status becomes «received» when everything is in stock, otherwise «partial».
    undo_receipt: subtract everything this order put in stock; status «ordered».
    Added to what the cell holds now, so changes made in Excel meanwhile aren't lost.
    """
    if not saved:
        raise bomlib.BomError("Leverandøren er ikke registrert som bestilt.")
    rows = orders.receipt(saved)
    stocked = dict(saved["stocked"])
    if action == "receipt":
        if lines is None:
            delta = {r["key"]: r["remaining"] for r in rows if r["remaining"] > 0}
        else:
            known = {r["key"] for r in rows}
            try:
                delta = {str(k): float(v) for k, v in lines.items() if v not in (None, "") and float(v) != 0}
            except (TypeError, ValueError, AttributeError):
                raise bomlib.BomError("Ugyldig antall.")
            if any(v < 0 for v in delta.values()) or not set(delta) <= known:
                raise bomlib.BomError("Ugyldig antall.")
        if not delta:
            raise bomlib.BomError("Ingenting å legge på lager.")
    else:
        delta = {k: -v for k, v in stocked.items() if v}
        if not delta:
            raise bomlib.BomError("Ingenting fra denne bestillingen er lagt på lager.")

    res = _write_stock(g, saved["lines"], delta, add=True)
    for k in res["written"]:
        stocked[k] = max(0.0, stocked.get(k, 0.0) + delta[k])
    stocked = {k: v for k, v in stocked.items() if v > 1e-9}
    if res["written"]:
        if action == "undo_receipt":
            status = "ordered" if not stocked else saved["status"]
        else:
            all_in = all(stocked.get(r["key"], 0.0) >= r["ordered"] - 1e-9 for r in rows)
            status = "received" if all_in else "partial"
        log = saved["stock_log"] + [{"time": dt.datetime.now().isoformat(timespec="minutes"),
                                        "action": "received" if action == "receipt" else "undone", "by": auth.me()["name"],
                                        "lines": {k: delta[k] for k in res["written"]}, "backup": res["backup"]}]
        store.update_order(gid, key, stocked=stocked, stock_log=log, status=status,
                               received_date=None if status == "ordered" else (saved["received_date"] or dt.date.today().isoformat()))
    return res


@app.post("/api/runs/<gid>/seen")
def acknowledge(gid):
    """Mark the changes in the BOM as seen."""
    g = store.run(gid)
    b = bomlib.read_bom(bom_path(g))
    with _lock:
        snaps = _read_json(SNAP_FILE, {})
        snaps[g["bom"]] = {"rows": b["rows"], "read": b["changed"]}
        _write_json(SNAP_FILE, snaps)
    return jsonify({"ok": True})


@app.post("/api/runs/<gid>/count")
def save_count(gid):
    """Save or correct the count. lines: [{key, type, counted}].

    A new count takes the "before" numbers from the BOM now. A corrected count keeps the numbers it
    was made from – otherwise stock already written to the BOM would be counted twice.
    """
    g = store.run(gid)
    old_count = store.counts().get(gid)
    body = request.get_json(force=True) or {}
    try:
        entries = {str(l["key"]): {"type": l.get("type", "equipment"), "counted": l["counted"]}
                    for l in body.get("lines", []) if l.get("counted") not in (None, "")}
    except (TypeError, KeyError, AttributeError):
        raise bomlib.BomError("Ugyldig telling.")
    if not entries:
        raise bomlib.BomError("Fyll inn minst én linje.")
    if old_count:  # changing a saved count: keep the numbers it was made from
        basis = old_count["lines"]
    else:
        b, c, sets, _ = calculate(g)
        basis = counting.basis(c["rows"], counting.ordered_per_line(store.all_orders().get(gid, {})),
                                    store.line_types().get(g["projectId"], {}), sets)
    try:
        lines = counting.make(basis, entries)
    except (TypeError, ValueError) as e:
        raise bomlib.BomError(f"Ugyldig telling: {e}")
    store.save_count(gid, g["projectId"], dt.date.today().isoformat(), lines)
    return jsonify(counting.summary(lines))


def _write_stock(g: dict, lines: list[dict], values_: dict[str, float], expected: dict[str, float] | None = None,
                 add: bool = False) -> dict:
    """Write to «På lager» (see bom.write_stock), and keep "last seen" in step so our own change
    doesn't show up as "the BOM has changed". Conflicts get a description from «lines» for the browser."""
    with _lock:
        res = bomlib.write_stock(bom_path(g), values_, BOM_DIR / "Sikkerhetskopi", expected, add)
        snaps = _read_json(SNAP_FILE, {})
        if g["bom"] in snaps and res["created"]:
            for r in snaps[g["bom"]]["rows"]:
                if bomlib.row_key(r) in res["created"]:
                    r["in_stock"] = res["created"][bomlib.row_key(r)]
            _write_json(SNAP_FILE, snaps)
    name = {l["key"]: l["description"] for l in lines}
    for k in res["conflict"]:
        k["description"] = name.get(k["key"], k["key"])
    return res


def _log(cnt: dict, action: str, res: dict) -> list[dict]:
    return cnt["log"] + [{"time": dt.datetime.now().isoformat(timespec="minutes"), "action": action, "by": auth.me()["name"],
                            "lines": len(res["written"]), "conflicts": len(res["conflict"]), "backup": res["backup"]}]


@app.post("/api/runs/<gid>/count/write")
def write_count(gid):
    """Write what has been counted (and not written already) to «På lager» in the BOM.

    Only cells that still hold the value the app last knew are overwritten: what was written last,
    or «på lager» when the count was made. If the cell has been changed in Excel since, the line
    is skipped and reported as a conflict.
    """
    g = store.run(gid)
    cnt = store.counts().get(gid)
    if not cnt:
        raise bomlib.BomError("Lagre tellingen først.")
    wv = cnt["written_values"]
    created = counting.pending(cnt["lines"], wv)
    if not created:
        raise bomlib.BomError("Alt som er telt, står allerede i BOM-en.")
    lines = {l["key"]: l for l in cnt["lines"]}
    res = _write_stock(g, cnt["lines"], {k: lines[k]["counted"] for k in created},
                       {k: wv.get(k, lines[k]["stockBefore"]) for k in created})
    if res["written"]:
        wv = {**wv, **{k: lines[k]["counted"] for k in res["written"]}}
        store.count_written(gid, wv, _log(cnt, "written", res), dt.date.today().isoformat(), res["backup"])
    return jsonify(res)


@app.post("/api/runs/<gid>/count/undo")
def undo_count(gid):
    """Set «På lager» back to the numbers from before the count, for lines the app has written."""
    g = store.run(gid)
    cnt = store.counts().get(gid)
    if not cnt or not cnt["written_values"]:
        raise bomlib.BomError("Ingenting fra denne tellingen står i BOM-en.")
    wv = cnt["written_values"]
    lines = {l["key"]: l for l in cnt["lines"]}
    keys = [k for k in wv if k in lines]
    res = _write_stock(g, cnt["lines"], {k: lines[k]["stockBefore"] for k in keys}, {k: wv[k] for k in keys})
    if res["written"]:
        wv = {k: v for k, v in wv.items() if k not in res["written"]}
        store.count_written(gid, wv, _log(cnt, "undone", res), cnt["written"], cnt["backup"])
    return jsonify(res)


@app.post("/api/runs/<gid>/count/fetch")
def adopt_count(gid):
    """Use «På lager» from the BOM as the counted quantity, for lines changed in Excel after writing.

    For when the change in Excel was a correction of the count. Loss is then computed from the new number.
    """
    g = store.run(gid)
    cnt = store.counts().get(gid)
    if not cnt:
        raise bomlib.BomError("Det finnes ingen telling.")
    _, c, _, _ = calculate(g)
    changed = counting.changed_in_bom(cnt["written_values"], c["rows"])
    if not changed:
        raise bomlib.BomError("Ingen linjer er endret i BOM-en siden tellingen ble skrevet.")
    lines = [{**l, "counted": changed[l["key"]]} if l["key"] in changed else l for l in cnt["lines"]]
    store.save_count(gid, g["projectId"], cnt["date"], lines)
    log = cnt["log"] + [{"time": dt.datetime.now().isoformat(timespec="minutes"), "action": "fetched", "by": auth.me()["name"],
                            "lines": len(changed), "conflicts": 0, "backup": None}]
    store.count_written(gid, {**cnt["written_values"], **changed}, log, cnt["written"], cnt["backup"])
    return jsonify({"fetched": len(changed), **counting.summary(lines)})


@app.delete("/api/runs/<gid>/count")
def delete_count(gid):
    store.run(gid)
    cnt = store.counts().get(gid)
    if cnt and cnt["written_values"]:
        raise bomlib.BomError("Noe fra tellingen står i BOM-en. Angre først, så kan den slettes.")
    store.delete_count(gid)
    return jsonify({"ok": True})


@app.put("/api/closed-periods")
def set_closed():
    body = request.get_json(force=True)
    if not isinstance(body, list) or len(body) > 20:
        raise bomlib.BomError("Ugyldige stengte perioder.")
    out = []
    for c in body:
        try:
            name, from_, to = str(c["name"]).strip(), int(c["from_week"]), int(c["to_week"])
        except (KeyError, TypeError, ValueError):
            raise bomlib.BomError("Ugyldig stengt periode.")
        if not name or not (1 <= from_ <= 53 and 1 <= to <= 53):
            raise bomlib.BomError("En stengt periode trenger navn og uker mellom 1 og 53.")
        out.append({"name": name, "from_week": from_, "to_week": to, "easter": bool(c.get("easter"))})
    store.set_closed(out)
    return jsonify(out)


@app.post("/api/folder/<which>")
def open_folder(which):
    only_local()
    path = {"bom": BOM_DIR, "lists": LIST_DIR}.get(which)
    if not path:
        raise bomlib.BomError("Ukjent mappe.")
    path.mkdir(exist_ok=True)
    open_path(path)
    return jsonify({"ok": True})


import admin_panel  # noqa: E402  (after everything it uses)

admin_panel.init(app, sys.modules[__name__])


# ---------- oppstart ----------

def main() -> None:
    debug = "--debug" in sys.argv
    url = f"http://127.0.0.1:{config.PORT}/"
    # Flask's reloader starts the script twice; only the first run checks and opens the browser
    first_run = not os.environ.get("WERKZEUG_RUN_MAIN")
    if first_run:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", config.PORT)) == 0:  # already running
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
        print(f"Årshjulet kjører på {url}  (modus: {config.MODE})")
        print(f"  Data:      {config.DATA_DIR}")
        print(f"  BOM-mappe: {BOM_DIR}")
        if config.SETTINGS_FILE.exists():
            print(f"  Innstillinger fra {config.SETTINGS_FILE.name}")
        if config.LOCAL:
            print("Lukk dette vinduet for å stoppe.")
    if debug:
        app.run(host=config.HOST, port=config.PORT, debug=True)
    else:
        from waitress import serve
        serve(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
