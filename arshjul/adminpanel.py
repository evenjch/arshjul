"""Administratorpanelet: /admin – ser alt, retter alt, rydder og tar sikkerhetskopi.

Alt her krever administratorkontoen (sjekket i app._rolle for alle endepunkter «adminpanel.*»),
og hopper over eierskapssjekken på prosjekter. Hver retting logges med rolle «admin».

Regler som gjelder også her:
- det som er skrevet til en BOM («På lager»), angres – det slettes ikke bare fra årshjulet
- noe som sletter mer enn én ting, viser først hva som blir borte (…/konsekvens)
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

import auth
import bestilling
import config
import frister
import telling
from db import KOPI_DIR_NAVN  # samme mappe som sikkerhetskopien før oppgraderinger

bp = Blueprint("adminpanel", __name__)
A = None  # app-modulen (settes i init – unngår å importere app.py to ganger når den kjøres som skript)


def init(app, app_modul) -> None:
    global A
    A = app_modul
    app.register_blueprint(bp)


def _kopidir() -> Path:
    return config.DATA_DIR / KOPI_DIR_NAVN


def _logg(g: dict, handling: str, endret: dict | None = None) -> None:
    A.logg(g, handling, endret, rolle="admin")


# ---------- siden og data ----------

@bp.get("/admin")
def side():
    return render_template("admin.html")


def _rad(g: dict, bestilt: dict, tellinger: dict) -> dict:
    lagret = bestilt.get(g["id"], {})
    t = tellinger.get(g["id"])
    bom = (config.BOM_DIR / g["bom"]) if g["bom"] else None
    return {
        **{k: g[k] for k in ("id", "prosjektId", "skolear", "navn", "type", "start", "slutt", "sett", "studenter",
                             "gruppe", "ekstra", "lev", "buf",
                             "notat", "bom", "emneId", "emneKode", "faglarerNavn", "ingeniorId", "ingeniorNavn",
                             "slettet", "slettetAv")},
        "bomFinnes": bool(bom and bom.exists()),
        "bestillinger": len(lagret), "bestiltKr": bestilling.kostnad(lagret),
        "telling": None if not t else ("skrevet" if t["skrevet_verdier"] else "lagret"),
    }


def _oversikt(rader: list[dict], emner: list[dict], brukere: list[dict], endringer: list[dict]) -> dict:
    aktive_brukere = {u["id"] for u in brukere if u["aktiv"]}
    eldste = min((e["tid"] for e in endringer), default=None)
    return {
        "utenEmne": [r for r in rader if not r["slettet"] and not r["emneId"]],
        "fjernet": [r for r in rader if r["slettet"]],
        "bomMangler": [r for r in rader if not r["slettet"] and r["bom"] and not r["bomFinnes"]],
        "emneUtenIngenior": [e for e in emner if e["aktiv"] and not e["ingeniorId"]],
        "emneUtenFaglarer": [e for e in emner if e["aktiv"] and not e["faglarerId"]],
        # emner med en deaktivert ingeniør eller faglærer: ingen får varslene / kan endre
        "emneMedInaktiv": [e for e in emner if e["aktiv"] and (
            (e["ingeniorId"] and e["ingeniorId"] not in aktive_brukere) or
            (e["faglarerId"] and e["faglarerId"] not in aktive_brukere))],
        "ukvitterte": len(endringer), "eldsteUkvitterte": eldste,
        "aldriInne": [u for u in brukere if u["aktiv"] and not u["sist_inne"]],
        "last": [u for u in brukere if u["last_til"] and u["last_til"] > dt.datetime.now().isoformat()],
        "perSkolear": {sy: sum(1 for r in rader if r["skolear"] == sy and not r["slettet"])
                       for sy in sorted({r["skolear"] for r in rader})},
    }


def _system() -> dict:
    bom_ok, antall_bom = False, 0
    try:
        antall_bom = sum(1 for f in config.BOM_DIR.glob("*.xlsx") if not f.name.startswith("~$"))
        bom_ok = config.BOM_DIR.exists()
    except OSError:
        pass
    kopier = sorted(_kopidir().glob("*.db"), reverse=True) if _kopidir().exists() else []
    return {
        "dbVersjon": A.store.versjon(), "dbFil": str(A.store.path),
        "dbStorrelse": A.store.path.stat().st_size if A.store.path.exists() else 0,
        "datamappe": str(config.DATA_DIR), "bomMappe": str(config.BOM_DIR), "bomNaabar": bom_ok,
        "antallBom": antall_bom, "modus": config.MODUS,
        "kopier": [{"navn": f.name, "storrelse": f.stat().st_size} for f in kopier[:20]],
        "kopiMappe": str(_kopidir()),
    }


@bp.get("/api/admin/data")
def data():
    """Alt panelet trenger, unntatt loggen (den hentes med filtre)."""
    bestilt, tellinger = A.store.bestillinger(), A.store.tellinger()
    rader = [_rad(g, bestilt, tellinger) for g in A.store.gjennomforinger()]
    navn = {g["id"]: g for g in rader}
    emner, brukere, endringer = A.store.emner(), A.store.brukere(), A.store.endringer()
    reg_b = [{"gid": gid, "prosjekt": navn[gid]["navn"], "skolear": navn[gid]["skolear"], "leverandor": b["navn"],
              "key": key, "status": b["status"], "statusNavn": bestilling.STATUSNAVN[b["status"]],
              "dato": b["bestilt_dato"], "ordrenr": b["ordrenr"], "av": b["av"], "linjer": len(b["linjer"]),
              "lagtPaaLager": sum(1 for v in (b["paa_lager"] or {}).values() if v)}
             for gid, per in bestilt.items() if gid in navn for key, b in per.items()]
    reg_t = [{"gid": gid, "prosjekt": navn[gid]["navn"], "skolear": navn[gid]["skolear"], "dato": t["dato"],
              "telte": len(t["linjer"]), "iBom": len(t["skrevet_verdier"]),
              "ventende": len(telling.ventende(t["linjer"], t["skrevet_verdier"]))}
             for gid, t in tellinger.items() if gid in navn]
    return jsonify({
        "oversikt": _oversikt(rader, emner, brukere, endringer), "gjennomforinger": rader,
        "emner": emner, "brukere": [A._bruker_ut(u) for u in brukere],
        "bestillinger": sorted(reg_b, key=lambda r: (r["skolear"], r["prosjekt"], r["leverandor"])),
        "tellinger": sorted(reg_t, key=lambda r: (r["skolear"], r["prosjekt"])),
        "system": _system(), "program": A.PROGRAM,
        "skolearNavn": {sy: frister.skolear_navn(sy) for sy in {r["skolear"] for r in rader}},
    })


# ---------- prosjekter (gjennomføringer) ----------

@bp.patch("/api/admin/gjennomforinger/<gid>")
def endre(gid):
    """Rett hva som helst på en gjennomføring – også skoleår, BOM og emne."""
    raw = request.get_json(force=True) or {}
    body = A._clean(raw, partial=True)
    g0 = A.store.gjennomforing(gid)
    if "emne" in body:
        e = A._emne(body.pop("emne"))
        body["emne_id"] = e["id"] if e else None
    if "skolear" in raw:
        try:
            sy = int(raw["skolear"])
        except (TypeError, ValueError):
            raise A.bomlib.BomError("Ugyldig skoleår.")
        if not 2000 <= sy <= 2100:
            raise A.bomlib.BomError("Ugyldig skoleår.")
        body["skolear"] = sy
    g1 = A.store.endre(gid, body)
    endret = A.endringer(g0, g1, body)
    if endret:
        _logg(g1, "endret", endret)
    return jsonify({**g1, "bomSett": A.sett_til_bom(g0, g1)})


@bp.get("/api/admin/gjennomforinger/<gid>/konsekvens")
def konsekvens(gid):
    """Hva som blir borte om gjennomføringen slettes."""
    g = A.store.gjennomforing(gid)
    lagret = A.store.bestillinger().get(gid, {})
    t = A.store.tellinger().get(gid)
    siste = sum(1 for x in A.store.gjennomforinger() if x["prosjektId"] == g["prosjektId"]) == 1
    return jsonify({"navn": g["navn"], "skolear": frister.skolear_navn(g["skolear"]), "bestillinger": len(lagret),
                    "lagtPaaLager": sum(1 for b in lagret.values() for v in (b["paa_lager"] or {}).values() if v),
                    "telling": bool(t), "tellingIBom": bool(t and t["skrevet_verdier"]), "sisteAvProsjektet": siste})


@bp.delete("/api/admin/gjennomforinger/<gid>")
def slett(gid):
    g = A.store.gjennomforing(gid)
    A.store.slett(gid)
    A.store.kvitter(gid)
    _logg(g, "slettet")
    return jsonify({"ok": True})


@bp.post("/api/admin/gjennomforinger/<gid>/gjenopprett")
def gjenopprett(gid):
    g = A.store.gjennomforing(gid)
    if not g["slettet"]:
        raise A.bomlib.BomError("Prosjektet er ikke fjernet.")
    A.store.gjenopprett(gid)
    A.store.kvitter(gid)
    _logg(g, "gjenopprettet")
    return jsonify({"ok": True})


# ---------- bestillinger og tellinger ----------

@bp.delete("/api/admin/gjennomforinger/<gid>/bestilling")
def slett_bestilling(gid):
    """Fjern en feilregistrert bestilling. Er noe lagt på lager, må mottaket angres først."""
    g = A.store.gjennomforing(gid)
    key = request.args.get("leverandor", "")
    lagret = A.store.bestillinger().get(gid, {}).get(key)
    if not lagret:
        raise KeyError(key)
    if any((lagret["paa_lager"] or {}).values()):
        raise A.bomlib.BomError("Noe fra denne bestillingen er lagt på lager. Angre mottaket først.")
    A.store.slett_bestilling(gid, key)
    _logg(g, "slettet bestilling", {"leverandør": (lagret["navn"], None)})
    return jsonify({"ok": True})


@bp.post("/api/admin/gjennomforinger/<gid>/angre-mottak")
def angre_mottak(gid):
    """Trekk et varemottak fra «På lager» i BOM-en igjen (samme regler som på prosjektet)."""
    g = A.store.gjennomforing(gid)
    key = request.args.get("leverandor", "")
    lagret = A.store.bestillinger().get(gid, {}).get(key)
    res = A._varemottak(g, gid, key, lagret, "angre_mottak", None)
    if res["skrevet"]:
        _logg(g, "angret varemottak", {"leverandør": (lagret["navn"], None)})
    return jsonify(res)


@bp.delete("/api/admin/gjennomforinger/<gid>/telling")
def slett_telling(gid):
    g = A.store.gjennomforing(gid)
    t = A.store.tellinger().get(gid)
    if not t:
        raise KeyError(gid)
    if t["skrevet_verdier"]:
        raise A.bomlib.BomError("Noe fra tellingen står i BOM-en. Angre tellingen først.")
    A.store.slett_telling(gid)
    _logg(g, "slettet telling")
    return jsonify({"ok": True})


@bp.post("/api/admin/gjennomforinger/<gid>/telling/angre")
def angre_telling(gid):
    g = A.store.gjennomforing(gid)
    svar = A.undo_count(gid)
    _logg(g, "angret telling")
    return svar


# ---------- endringslogg ----------

@bp.get("/api/admin/logg")
def logg():
    a = request.args
    try:
        grense = min(2000, max(1, int(a.get("grense", 500))))
    except ValueError:
        grense = 500
    return jsonify(A.store.logg_sok(bruker=a.get("bruker", ""), prosjekt=a.get("prosjekt", ""), rolle=a.get("rolle", ""),
                                    fra=a.get("fra", ""), til=a.get("til", ""), grense=grense))


# ---------- opprydding ----------

@bp.post("/api/admin/kvitter-alle")
def kvitter_alle():
    return jsonify({"kvittert": A.store.kvitter_alle()})


@bp.get("/api/admin/skolear/<int:sy>/konsekvens")
def skolear_konsekvens(sy):
    rader = [g for g in A.store.gjennomforinger() if g["skolear"] == sy]
    ids = {g["id"] for g in rader}
    bestilt, tellinger = A.store.bestillinger(), A.store.tellinger()
    return jsonify({"navn": frister.skolear_navn(sy), "gjennomforinger": len(rader),
                    "bestillinger": sum(len(bestilt.get(i, {})) for i in ids),
                    "tellinger": sum(1 for i in ids if i in tellinger)})


@bp.delete("/api/admin/skolear/<int:sy>")
def slett_skolear(sy):
    """Slett alt fra et gammelt skoleår: gjennomføringer, bestillinger og tellinger.
    Det som står i BOM-ene («På lager») blir stående – det er dagens lager."""
    rader = [g for g in A.store.gjennomforinger() if g["skolear"] == sy]
    for g in rader:
        A.store.slett(g["id"])
        A.store.kvitter(g["id"])
    if rader:
        meg = auth.meg()
        A.store.logg([{"bruker_id": meg["id"], "bruker_navn": meg["navn"], "rolle": "admin", "skolear": sy,
                       "prosjekt_navn": f"Skoleår {frister.skolear_navn(sy)}", "handling": "slettet skoleår",
                       "felt": "gjennomføringer", "fra": len(rader), "til": 0}])
    return jsonify({"slettet": len(rader)})


@bp.delete("/api/admin/brukere/<int:uid>")
def slett_bruker(uid):
    """Slett en bruker som aldri har vært i bruk (f.eks. en testbruker). Ellers: deaktiver."""
    u = A.store.bruker(id=uid)
    if not u:
        raise KeyError(uid)
    if uid == auth.meg()["id"]:
        raise A.bomlib.BomError("Du kan ikke slette deg selv.")
    if u["sist_inne"]:
        raise A.bomlib.BomError("Brukeren har vært logget inn. Deaktiver i stedet, så navnet står i loggene.")
    if any(uid in (e["faglarerId"], e["ingeniorId"]) for e in A.store.emner()):
        raise A.bomlib.BomError("Brukeren står på et emne. Bytt person der først.")
    if u["admin"] and u["aktiv"] and A.store.aktive_admins() <= 1:
        raise A.bomlib.BomError("Det må finnes minst én aktiv administrator.")
    A.store.slett_bruker(uid)
    return jsonify({"ok": True})


# ---------- system og sikkerhetskopi ----------

@bp.post("/api/admin/sikkerhetskopi")
def ta_kopi():
    mal = _kopidir() / f"arshjul {dt.datetime.now():%Y-%m-%d %H%M%S}.db"
    A.store.sikkerhetskopi(mal)
    return jsonify({"navn": mal.name, "mappe": str(_kopidir()), "storrelse": mal.stat().st_size})


@bp.get("/api/admin/sikkerhetskopi/<navn>")
def last_ned_kopi(navn):
    # bare filnavn vi selv har laget – aldri en sti ut av mappen
    if not re.fullmatch(r"arshjul [0-9-]+ [0-9]+\.db", navn):
        raise KeyError(navn)
    fil = _kopidir() / navn
    if not fil.exists():
        raise KeyError(navn)
    return send_file(fil, as_attachment=True, download_name=navn)
