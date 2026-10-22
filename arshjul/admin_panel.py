"""The admin panel: /admin – sees everything, fixes everything, cleans up and takes backups.

Everything here requires the admin account (checked in app._role for every «admin_panel.*» endpoint),
and skips the ownership check on projects. Every correction is logged with role «admin».

Rules that apply here too:
- what has been written to a BOM («På lager») is undone – it isn't just deleted from the app
- anything that deletes more than one thing shows first what will disappear (…/consequence)
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

import auth
import config
import counting
import deadlines
import orders
from db import BACKUP_DIR_NAME  # the same folder as the backup taken before upgrades

bp = Blueprint("admin_panel", __name__)
A = None  # the app module (set in init – avoids importing app.py twice when it's run as a script)


def init(app, app_module) -> None:
    global A
    A = app_module
    app.register_blueprint(bp)


def _backup_dir() -> Path:
    return config.DATA_DIR / BACKUP_DIR_NAME


def _log(g: dict, action: str, changed: dict | None = None) -> None:
    A.log(g, action, changed, role="admin")


# ---------- the page and its data ----------

@bp.get("/admin")
def side():
    return render_template("admin.html")


def _row(g: dict, ordered: dict, counts: dict) -> dict:
    saved = ordered.get(g["id"], {})
    t = counts.get(g["id"])
    bom = (config.BOM_DIR / g["bom"]) if g["bom"] else None
    return {
        **{k: g[k] for k in ("id", "projectId", "school_year", "name", "type", "start", "finish", "sets", "students",
                             "group_size", "spare", "lead", "buf",
                             "note", "bom", "courseId", "courseCode", "lecturerName", "engineerId", "engineerName",
                             "deleted", "deletedBy")},
        "bomExists": bool(bom and bom.exists()),
        "orders": len(saved), "orderedCost": orders.cost(saved),
        "count": None if not t else ("written" if t["written_values"] else "saved"),
    }


def _overview(rows: list[dict], courses: list[dict], users: list[dict], changes: list[dict]) -> dict:
    active_users = {u["id"] for u in users if u["active"]}
    oldest = min((e["time"] for e in changes), default=None)
    return {
        "withoutCourse": [r for r in rows if not r["deleted"] and not r["courseId"]],
        "removed": [r for r in rows if r["deleted"]],
        "bomMissing": [r for r in rows if not r["deleted"] and r["bom"] and not r["bomExists"]],
        "coursesPending": [e for e in courses if not e["approved"]],
        "courseWithoutEngineer": [e for e in courses if e["active"] and not e["engineerId"]],
        "courseWithoutLecturer": [e for e in courses if e["active"] and not e["lecturerId"]],
        # courses with a deactivated engineer or lecturer: nobody gets the notifications / can make changes
        "courseWithInactive": [e for e in courses if e["active"] and (
            (e["engineerId"] and e["engineerId"] not in active_users) or
            (e["lecturerId"] and e["lecturerId"] not in active_users))],
        "unacknowledged": len(changes), "oldestUnacknowledged": oldest,
        "neverLoggedIn": [u for u in users if u["active"] and not u["last_login"]],
        "locked": [u for u in users if u["locked_until"] and u["locked_until"] > dt.datetime.now().isoformat()],
        "perSchoolYear": {sy: sum(1 for r in rows if r["school_year"] == sy and not r["deleted"])
                       for sy in sorted({r["school_year"] for r in rows})},
    }


def _system() -> dict:
    bom_ok, bom_count = False, 0
    try:
        bom_count = sum(1 for f in config.BOM_DIR.glob("*.xlsx") if not f.name.startswith("~$"))
        bom_ok = config.BOM_DIR.exists()
    except OSError:
        pass
    backups = sorted(_backup_dir().glob("*.db"), reverse=True) if _backup_dir().exists() else []
    return {
        "dbVersion": A.store.version(), "dbFile": str(A.store.path),
        "dbSize": A.store.path.stat().st_size if A.store.path.exists() else 0,
        "dataFolder": str(config.DATA_DIR), "bomFolder": str(config.BOM_DIR), "bomReachable": bom_ok,
        "bomCount": bom_count, "mode": config.MODE,
        "backups": [{"name": f.name, "size": f.stat().st_size} for f in backups[:20]],
        "backupFolder": str(_backup_dir()),
    }


@bp.get("/api/admin/data")
def data():
    """Everything the panel needs, except the log (fetched with filters)."""
    ordered, counts = A.store.all_orders(), A.store.counts()
    rows = [_row(g, ordered, counts) for g in A.store.runs()]
    name = {g["id"]: g for g in rows}
    courses, users, changes = A.store.courses(), A.store.users(), A.store.changes()
    reg_o = [{"gid": gid, "project": name[gid]["name"], "school_year": name[gid]["school_year"], "supplier": b["name"],
              "key": key, "status": b["status"], "statusName": orders.STATUS_NAMES[b["status"]],
              "date": b["ordered_date"], "order_no": b["order_no"], "by": b["made_by"], "lines": len(b["lines"]),
              "addedToStock": sum(1 for v in (b["stocked"] or {}).values() if v)}
             for gid, per in ordered.items() if gid in name for key, b in per.items()]
    reg_c = [{"gid": gid, "project": name[gid]["name"], "school_year": name[gid]["school_year"], "date": t["date"],
              "counted_lines": len(t["lines"]), "inBom": len(t["written_values"]),
              "pending": len(counting.pending(t["lines"], t["written_values"]))}
             for gid, t in counts.items() if gid in name]
    return jsonify({
        "overview": _overview(rows, courses, users, changes), "runs": rows,
        "courses": courses, "users": [A._user_out(u) for u in users],
        "orders": sorted(reg_o, key=lambda r: (r["school_year"], r["project"], r["supplier"])),
        "counts": sorted(reg_c, key=lambda r: (r["school_year"], r["project"])),
        "system": _system(), "program": A.PROGRAM,
        "schoolYearName": {sy: deadlines.school_year_name(sy) for sy in {r["school_year"] for r in rows}},
    })


# ---------- projects (runs) ----------

@bp.patch("/api/admin/runs/<gid>")
def update(gid):
    """Fix anything on a run – also school year, BOM and course."""
    raw = request.get_json(force=True) or {}
    body = A._clean(raw, partial=True)
    g0 = A.store.run(gid)
    if "course" in body:
        e = A._course(body.pop("course"))
        body["course_id"] = e["id"] if e else None
    if "school_year" in raw:
        try:
            sy = int(raw["school_year"])
        except (TypeError, ValueError):
            raise A.bomlib.BomError("Ugyldig skoleår.")
        if not 2000 <= sy <= 2100:
            raise A.bomlib.BomError("Ugyldig skoleår.")
        body["school_year"] = sy
    g1 = A.store.update(gid, body)
    changed = A.changes(g0, g1, body)
    if changed:
        _log(g1, "changed", changed)
    return jsonify({**g1, "bomSets": A.sets_to_bom(g0, g1)})


@bp.get("/api/admin/runs/<gid>/consequence")
def consequence(gid):
    """What disappears if the run is deleted."""
    g = A.store.run(gid)
    saved = A.store.all_orders().get(gid, {})
    t = A.store.counts().get(gid)
    last = sum(1 for x in A.store.runs() if x["projectId"] == g["projectId"]) == 1
    return jsonify({"name": g["name"], "school_year": deadlines.school_year_name(g["school_year"]), "orders": len(saved),
                    "addedToStock": sum(1 for b in saved.values() for v in (b["stocked"] or {}).values() if v),
                    "count": bool(t), "countInBom": bool(t and t["written_values"]), "lastOfProject": last})


@bp.delete("/api/admin/runs/<gid>")
def delete(gid):
    g = A.store.run(gid)
    A.store.delete(gid)
    A.store.acknowledge(gid)
    _log(g, "deleted")
    return jsonify({"ok": True})


@bp.post("/api/admin/runs/<gid>/restore")
def restore(gid):
    g = A.store.run(gid)
    if not g["deleted"]:
        raise A.bomlib.BomError("Prosjektet er ikke fjernet.")
    A.store.restore(gid)
    A.store.acknowledge(gid)
    _log(g, "restored")
    return jsonify({"ok": True})


# ---------- orders and counts ----------

@bp.delete("/api/admin/runs/<gid>/order")
def delete_order(gid):
    """Remove a wrongly registered order. If anything has been put in stock, the receipt must be undone first."""
    g = A.store.run(gid)
    key = request.args.get("supplier", "")
    saved = A.store.all_orders().get(gid, {}).get(key)
    if not saved:
        raise KeyError(key)
    if any((saved["stocked"] or {}).values()):
        raise A.bomlib.BomError("Noe fra denne bestillingen er lagt på lager. Angre mottaket først.")
    A.store.delete_order(gid, key)
    _log(g, "order_deleted", {"leverandør": (saved["name"], None)})
    return jsonify({"ok": True})


@bp.post("/api/admin/runs/<gid>/undo-receipt")
def undo_receipt(gid):
    """Subtract a receipt from «På lager» in the BOM again (same rules as on the project)."""
    g = A.store.run(gid)
    key = request.args.get("supplier", "")
    saved = A.store.all_orders().get(gid, {}).get(key)
    res = A._receipt(g, gid, key, saved, "undo_receipt", None)
    if res["written"]:
        _log(g, "receipt_undone", {"leverandør": (saved["name"], None)})
    return jsonify(res)


@bp.delete("/api/admin/runs/<gid>/count")
def delete_count(gid):
    g = A.store.run(gid)
    t = A.store.counts().get(gid)
    if not t:
        raise KeyError(gid)
    if t["written_values"]:
        raise A.bomlib.BomError("Noe fra tellingen står i BOM-en. Angre tellingen først.")
    A.store.delete_count(gid)
    _log(g, "count_deleted")
    return jsonify({"ok": True})


@bp.post("/api/admin/runs/<gid>/count/undo")
def undo_count(gid):
    g = A.store.run(gid)
    reply = A.undo_count(gid)
    _log(g, "count_undone")
    return reply


# ---------- endringslogg ----------

@bp.get("/api/admin/log")
def log():
    a = request.args
    try:
        limit = min(2000, max(1, int(a.get("limit", 500))))
    except ValueError:
        limit = 500
    return jsonify(A.store.log_search(user=a.get("user", ""), project=a.get("project", ""), role=a.get("role", ""),
                                    from_=a.get("from", ""), to=a.get("to", ""), limit=limit))


# ---------- opprydding ----------

@bp.post("/api/admin/acknowledge-all")
def acknowledge_all():
    return jsonify({"acknowledged": A.store.acknowledge_all()})


@bp.get("/api/admin/school-years/<int:sy>/consequence")
def school_year_consequence(sy):
    rows = [g for g in A.store.runs() if g["school_year"] == sy]
    ids = {g["id"] for g in rows}
    ordered, counts = A.store.all_orders(), A.store.counts()
    return jsonify({"name": deadlines.school_year_name(sy), "runs": len(rows),
                    "orders": sum(len(ordered.get(i, {})) for i in ids),
                    "counts": sum(1 for i in ids if i in counts)})


@bp.delete("/api/admin/school-years/<int:sy>")
def delete_school_year(sy):
    """Delete everything from an old school year: runs, orders and counts.
    What the BOMs say («På lager») stays – that's today's stock."""
    rows = [g for g in A.store.runs() if g["school_year"] == sy]
    for g in rows:
        A.store.delete(g["id"])
        A.store.acknowledge(g["id"])
    if rows:
        me = auth.me()
        A.store.log([{"user_id": me["id"], "user_name": me["name"], "role": "admin", "school_year": sy,
                       "project_name": f"Skoleår {deadlines.school_year_name(sy)}", "action": "school_year_deleted",
                       "field": "gjennomføringer", "from_value": len(rows), "to_value": 0}])
    return jsonify({"deleted": len(rows)})


# ---------- proposed courses ----------

def _tell_proposer(e: dict, action: str, to_value=None) -> None:
    me = auth.me()
    if e["proposedBy"] and e["proposedBy"] != me["id"]:
        A.store.log([{"user_id": me["id"], "user_name": me["name"], "role": "admin", "course_id": e["id"],
                      "project_name": e["code"], "action": action, "recipient_id": e["proposedBy"], "to_value": to_value}])


@bp.post("/api/admin/courses/<int:cid>/approve")
def approve_course(cid):
    e = A.store.course(cid)
    if e["approved"]:
        raise A.bomlib.BomError("Emnet er allerede godkjent.")
    e = A.store.approve_course(cid)
    _tell_proposer(e, "course_approved")
    return jsonify(e)


@bp.delete("/api/admin/courses/<int:cid>")
def reject_course(cid):
    """Reject a proposed course: it's deleted, and its projects end up without a course."""
    e = A.store.course(cid)
    if e["approved"]:
        raise A.bomlib.BomError("Bare emner som venter på godkjenning kan avvises. Et godkjent emne gjøres inaktivt.")
    A.store.delete_course(cid)
    _tell_proposer(e, "course_rejected", e["projects"] or None)
    return jsonify({"ok": True, "projects": e["projects"]})


@bp.delete("/api/admin/users/<int:uid>")
def delete_user(uid):
    """Delete a user who has never been used (e.g. a test user). Otherwise: deactivate."""
    u = A.store.user(id=uid)
    if not u:
        raise KeyError(uid)
    if uid == auth.me()["id"]:
        raise A.bomlib.BomError("Du kan ikke slette deg selv.")
    if u["last_login"]:
        raise A.bomlib.BomError("Brukeren har vært logget inn. Deaktiver i stedet, så navnet står i loggene.")
    if any(uid in (e["lecturerId"], e["engineerId"]) for e in A.store.courses()):
        raise A.bomlib.BomError("Brukeren står på et emne. Bytt person der først.")
    if u["admin"] and u["active"] and A.store.active_admins() <= 1:
        raise A.bomlib.BomError("Det må finnes minst én aktiv administrator.")
    A.store.delete_user(uid)
    return jsonify({"ok": True})


# ---------- system and backups ----------

@bp.post("/api/admin/backup")
def take_backup():
    target = _backup_dir() / f"arshjul {dt.datetime.now():%Y-%m-%d %H%M%S}.db"
    A.store.backup(target)
    return jsonify({"name": target.name, "mappe": str(_backup_dir()), "size": target.stat().st_size})


@bp.get("/api/admin/backup/<name>")
def download_backup(name):
    # only file names we created ourselves – never a path out of the folder
    if not re.fullmatch(r"arshjul [0-9-]+ [0-9]+\.db", name):
        raise KeyError(name)
    file = _backup_dir() / name
    if not file.exists():
        raise KeyError(name)
    return send_file(file, as_attachment=True, download_name=name)
