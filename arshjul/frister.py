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


VIS_NESTE_UKE = 26  # fra denne uka om sommeren åpner årshjulet på skoleåret som kommer


def skolear_vis(d: dt.date) -> int:
    """Skoleåret siden åpner på. Om sommeren er det året som kommer: høstens bestillinger er gjort,
    og det er neste år du jobber med – selv om skoleåret formelt skifter først i uke 32."""
    na = skolear_for(d)
    return na + 1 if d >= mandag(na + 1, VIS_NESTE_UKE) else na


def skolear_navn(sy: int) -> str:
    return f"{sy}/{(sy + 1) % 100:02d}"


def uke_dato(sy: int, uke: int) -> dt.date:
    """Mandagen i en uke i et skoleår. Uke 53 i et år uten uke 53 blir uke 52."""
    ar = sy if uke >= SKOLEAR_UKE else sy + 1
    return mandag(ar, min(uke, uker_i_ar(ar)))


def in_range(w: int, a: int, b: int) -> bool:
    return a <= w <= b if a <= b else (w >= a or w <= b)


def paskedag(ar: int) -> dt.date:
    """Første påskedag (gregoriansk, «anonym» algoritme)."""
    a, b, c = ar % 19, ar // 100, ar % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    maned = (h + l - 7 * m + 114) // 31
    return dt.date(ar, maned, (h + l - 7 * m + 114) % 31 + 1)


def paskeuke(ar: int) -> int:
    """Uka med skjærtorsdag, langfredag og påskedag (søndagen er siste dag i ISO-uka)."""
    return paskedag(ar).isocalendar()[1]


def i_periode(d: dt.date, c: dict) -> bool:
    """Er uka d i den stengte perioden c? En periode som følger påsken, har ikke faste uker."""
    ar, w, _ = d.isocalendar()
    return w == paskeuke(ar) if c.get("paske") else in_range(w, c["fra"], c["til"])


def stengt(d: dt.date, closed: list[dict]) -> bool:
    return any(i_periode(d, c) for c in closed)


def stengt_navn(d: dt.date, closed: list[dict]) -> str:
    return ", ".join(c["navn"] for c in closed if i_periode(d, c))


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
        out["stengt"] = stengt_navn(d, closed)  # navnet på perioden, tomt hvis åpen
    return out


def skolear_uker(sy: int, closed: list[dict]) -> list[dict]:
    """Alle ukene i skoleåret (52 eller 53)."""
    d, slutt, out = skolear_start(sy), skolear_start(sy + 1), []
    while d < slutt:
        out.append(uke_info(d, closed))
        d += UKE
    return out


VARSEL_UKER = 12  # si fra om neste skoleår så lenge før den første fristen


def oppsett_varsel(rader: list[dict], closed: list[dict], idag: dt.date) -> dict | None:
    """Si fra hvis et skoleår ikke er satt opp, og den første fristen i det nærmer seg.

    Ser på prosjekter som gikk året før, men mangler i skoleåret (i år og neste år).
    Høstens frister havner ofte i juni, altså i skoleåret før – derfor må neste år
    settes opp om våren.
    """
    na = skolear_for(idag)
    mandag_na = idag - dt.timedelta(days=idag.weekday())
    for sy in (na, na + 1):
        finnes = {g["prosjektId"] for g in rader if g["skolear"] == sy}
        mangler = [g for g in rader if g["skolear"] == sy - 1 and g["prosjektId"] not in finnes]
        if not mangler:
            continue
        kandidater = sorted(((frister({**g, "skolear": sy}, closed)["frist"], g["navn"]) for g in mangler),
                            key=lambda x: x[0]["dato"])
        frist, navn = kandidater[0]
        uker = (dt.date.fromisoformat(frist["dato"]) - mandag_na).days // 7
        if uker <= VARSEL_UKER:
            return {"skolear": sy, "navn": skolear_navn(sy), "fra": sy - 1, "fraNavn": skolear_navn(sy - 1),
                    "frist": frist, "prosjekt": navn, "uker": uker, "mangler": len(mangler)}
    return None


def semester(uke: int) -> str:
    return "Høst" if uke >= SKOLEAR_UKE else "Vår"


# prosjekttyper: navn, hva starten heter, når det telles (uker etter siste bruksuke) og hva tellingen heter
TYPER = {
    "sett":     {"navn": "Semestersett", "start": "Utdeling",      "retur": 0, "telling": "Innlevering og telling"},
    "prosjekt": {"navn": "Prosjekt",     "start": "Prosjektstart", "retur": 1, "telling": "Innlevering og telling"},
    "hack":     {"navn": "Hackathon",    "start": "Hackathon",     "retur": 1, "telling": "Tell opp restlager"},
    "lab":      {"navn": "Lab",          "start": "Lab",           "retur": 0, "telling": "Innlevering og telling"},
}


def frister(g: dict, closed: list[dict]) -> dict:
    """Datoer for én gjennomføring: frist, forberedelse, start, slutt og innlevering."""
    start = uke_dato(g["skolear"], g["start"])
    slutt = max(start, uke_dato(g["skolear"], g["slutt"]))
    frist = back_open(start, (g.get("lev") or 0) + (g.get("buf") or 0), closed)
    prep = back_open(frist, 2, closed)
    typ = TYPER.get(g.get("type"), TYPER["hack"])
    retur = slutt + typ["retur"] * UKE
    hendelser = [
        {"kind": "prep", "what": "Oppdater BOM og tell lager", **uke_info(prep)},
        {"kind": "frist", "what": "Bestillingsfrist", **uke_info(frist)},
        {"kind": "start", "what": typ["start"], **uke_info(start)},
        {"kind": "retur", "what": typ["telling"], **uke_info(retur)},
    ]
    return {"frist": uke_info(frist), "prep": uke_info(prep), "start": uke_info(start),
            "slutt": uke_info(slutt), "retur": uke_info(retur), "hendelser": hendelser}
