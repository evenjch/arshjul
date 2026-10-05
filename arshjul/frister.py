"""Uker, skoleår og frister – det eneste stedet de regnes ut.

Et skoleår går fra mandag i uke 32 til søndag i uke 31 året etter, og heter etter
året det starter («2026/27» = 2026). Uke 32–53 hører til første kalenderår, uke 1–31
til det neste. Alt regnes med ekte datoer, så årsskifte og uke 53 blir riktig.
"""
from __future__ import annotations

import datetime as dt

SKOLEAR_UKE = 32
UKE = dt.timedelta(weeks=1)


def mandag(ar: int, uke: int) -> dt.date:
    return dt.date.fromisocalendar(ar, uke, 1)


def uker_i_ar(ar: int) -> int:
    return dt.date(ar, 12, 28).isocalendar()[1]


def skolear_start(sy: int) -> dt.date:
    return mandag(sy, SKOLEAR_UKE)


def skolear_for(d: dt.date) -> int:
    return d.year if d >= skolear_start(d.year) else d.year - 1


def skolear_navn(sy: int) -> str:
    return f"{sy}/{(sy + 1) % 100:02d}"


def uke_dato(sy: int, uke: int) -> dt.date:
    """Mandagen i en uke i et skoleår. Uke 53 i et år uten uke 53 blir uke 52."""
    ar = sy if uke >= SKOLEAR_UKE else sy + 1
    return mandag(ar, min(uke, uker_i_ar(ar)))


def in_range(w: int, a: int, b: int) -> bool:
    return a <= w <= b if a <= b else (w >= a or w <= b)


def stengt(d: dt.date, closed: list[dict]) -> bool:
    w = d.isocalendar()[1]
    return any(in_range(w, c["fra"], c["til"]) for c in closed)


def back_open(d: dt.date, n: int, closed: list[dict]) -> dt.date:
    """Gå n åpne uker bakover fra uken d. Stengte uker telles ikke."""
    while n > 0:
        d -= UKE
        if not stengt(d, closed):
            n -= 1
    return d


def uke_info(d: dt.date, closed: list[dict] | None = None) -> dict:
    ar, uke, _ = d.isocalendar()
    out = {"dato": (d - dt.timedelta(days=d.weekday())).isoformat(), "uke": uke, "ar": ar}
    if closed is not None:
        out["stengt"] = stengt(d, closed)
    return out


def skolear_uker(sy: int, closed: list[dict]) -> list[dict]:
    """Alle ukene i skoleåret (52 eller 53)."""
    d, slutt, out = skolear_start(sy), skolear_start(sy + 1), []
    while d < slutt:
        out.append(uke_info(d, closed))
        d += UKE
    return out


def semester(uke: int) -> str:
    return "Høst" if uke >= SKOLEAR_UKE else "Vår"


def frister(g: dict, closed: list[dict]) -> dict:
    """Datoer for én gjennomføring: frist, forberedelse, start, slutt og innlevering."""
    start = uke_dato(g["skolear"], g["start"])
    slutt = max(start, uke_dato(g["skolear"], g["slutt"]))
    frist = back_open(start, (g.get("lev") or 0) + (g.get("buf") or 0), closed)
    prep = back_open(frist, 2, closed)
    sett = g.get("type") == "sett"
    retur = slutt if sett else slutt + UKE
    hack = "Hackathon" if start == slutt else "Gjennomføring starter"
    hendelser = [
        {"kind": "prep", "what": "Oppdater BOM og tell lager", **uke_info(prep)},
        {"kind": "frist", "what": "Bestillingsfrist", **uke_info(frist)},
        {"kind": "start", "what": "Utdeling" if sett else hack, **uke_info(start)},
        {"kind": "retur", "what": "Innlevering og telling" if sett else "Tell opp restlager", **uke_info(retur)},
    ]
    return {"frist": uke_info(frist), "prep": uke_info(prep), "start": uke_info(start),
            "slutt": uke_info(slutt), "retur": uke_info(retur), "hendelser": hendelser}
