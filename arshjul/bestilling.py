"""Bestillingsstatus per leverandør for én gjennomføring.

Linjene i BOM-en grupperes etter leverandør. Når en leverandør markeres som bestilt,
lagres en frossen kopi av hva som ble bestilt. Den sammenlignes senere med det BOM-en
og antall sett krever nå, så du ser hva som må bestilles i tillegg.
"""
from __future__ import annotations

from bom import rad_key

EPS = 1e-9
UKJENT = "Ukjent leverandør"
# fra «verst» til «best»; en gjennomføring får den dårligste statusen blant leverandørene
REKKE = ["ikke", "bestilt", "delvis", "mottatt"]
STATUSNAVN = {
    "ikke": "Ikke bestilt", "delvis_bestilt": "Delvis bestilt", "bestilt": "Bestilt",
    "delvis": "Delvis mottatt", "mottatt": "Mottatt", "lager": "Alt på lager",
}
LAGRET_STATUS = ("bestilt", "delvis", "mottatt")


def lev_key(navn: str | None) -> str:
    return (navn or "").strip().lower()


def grupper(rader: list[dict]) -> list[dict]:
    """BOM-linjer (fra bom.calc) gruppert etter leverandør, i rekkefølgen de står i BOM-en."""
    out: dict[str, dict] = {}
    for r in rader:
        k = lev_key(r.get("leverandor"))
        g = out.setdefault(k, {"key": k, "navn": (r.get("leverandor") or "").strip() or UKJENT, "rader": []})
        g["rader"].append(r)
    return list(out.values())


def gruppe(rader: list[dict], key: str) -> dict | None:
    return next((g for g in grupper(rader) if g["key"] == key), None)


def frys(rader: list[dict]) -> list[dict]:
    """Det som skal bestilles nå, slik det lagres når du trykker «Bestilt»."""
    return [{"key": rad_key(r), "beskrivelse": r["beskrivelse"], "varenummer": r.get("varenummer", ""),
             "antall": r["bestill"], "pris": r["pris"]} for r in rader if r["bestill"] > EPS]


def slaa_sammen(gammel: list[dict], ny: list[dict]) -> list[dict]:
    """Etter en tilleggsbestilling: det største av det som er bestilt og det som trengs, per linje."""
    out = {l["key"]: dict(l) for l in gammel}
    for l in ny:
        if l["key"] not in out or l["antall"] > out[l["key"]]["antall"]:
            out[l["key"]] = dict(l)
    return list(out.values())


def avvik(frosset: list[dict], rader: list[dict]) -> tuple[list[dict], list[dict]]:
    """(tillegg, overskudd): linjer det trengs mer av, og linjer det er bestilt mer av enn nødvendig."""
    bestilt = {l["key"]: l for l in frosset}
    trenger = {rad_key(r): r for r in rader}
    tillegg = []
    for k, r in trenger.items():
        har = bestilt[k]["antall"] if k in bestilt else 0.0
        if r["bestill"] > har + EPS:
            tillegg.append({"beskrivelse": r["beskrivelse"], "varenummer": r.get("varenummer", ""),
                            "bestilt": har, "trenger": r["bestill"], "mangler": r["bestill"] - har})
    overskudd = []
    for k, l in bestilt.items():
        trengs = trenger[k]["bestill"] if k in trenger else 0.0
        if l["antall"] > trengs + EPS:
            overskudd.append({"beskrivelse": l["beskrivelse"], "varenummer": l.get("varenummer", ""),
                              "bestilt": l["antall"], "trenger": trengs})
    return tillegg, overskudd


def kostnad(lagret: dict[str, dict]) -> float | None:
    """Hva som faktisk er bestilt (eks. mva), fra de frosne kopiene. None hvis ingenting er registrert."""
    if not lagret:
        return None
    return round(sum(l["antall"] * l["pris"] for b in lagret.values() for l in b["linjer"]), 2)


def mottak(b: dict) -> list[dict]:
    """Per bestilt linje: bestilt, lagt på lager og hva som gjenstår å legge på lager."""
    lagt = b.get("paa_lager") or {}
    return [{"key": l["key"], "beskrivelse": l["beskrivelse"], "varenummer": l.get("varenummer", ""),
             "bestilt": l["antall"], "lagt": lagt.get(l["key"], 0.0),
             "gjenstar": max(0.0, l["antall"] - lagt.get(l["key"], 0.0))} for l in b["linjer"]]


def _med_mottatt(rader: list[dict], lagt: dict[str, float]) -> list[dict]:
    """Det som er lagt på lager fra denne bestillingen, har allerede senket «å bestille» i BOM-en.
    Legg det til igjen før sammenligningen, ellers ser det ut som det er bestilt for mye."""
    return [{**r, "bestill": r["bestill"] + lagt.get(rad_key(r), 0.0)} for r in rader]


def oversikt(rader: list[dict], lagret: dict[str, dict], sammenlign: bool = True) -> dict:
    """Status for hver leverandør og for hele gjennomføringen.

    rader:      bom.calc(...)["rader"] for gjennomføringen
    lagret:     registrerte bestillinger, etter leverandør-nøkkel
    sammenlign: se etter «trenger mer / bestilt for mye». Gir bare mening før prosjektet
                starter – etterpå endres BOM-en av tellinger og neste års innkjøp.
    """
    gr = []
    for g in grupper(rader):
        aktive = [r for r in g["rader"] if r["bestill"] > EPS]
        b = lagret.get(g["key"])
        tillegg, overskudd = (avvik(b["linjer"], _med_mottatt(g["rader"], b.get("paa_lager") or {}))
                              if b and sammenlign else ([], []))
        gr.append(_gruppe_ut(g["key"], g["navn"], aktive, b, tillegg, overskudd))
    kjente = {g["key"] for g in gr}
    for key, b in lagret.items():  # leverandøren er fjernet fra BOM-en etter bestilling
        if key not in kjente:
            _, overskudd = avvik(b["linjer"], []) if sammenlign else ([], [])
            gr.append({**_gruppe_ut(key, b["navn"], [], b, [], overskudd), "fjernet": True})

    relevante = [g for g in gr if g["status"] != "lager"]
    if not relevante:
        status = "lager"
    else:
        laveste = min(REKKE.index(g["status"]) for g in relevante)
        if laveste == 0:
            status = "delvis_bestilt" if any(g["status"] != "ikke" for g in relevante) else "ikke"
        else:
            status = REKKE[laveste]
    tillegg = any(g["tillegg"] for g in gr)
    return {
        "status": status, "statusNavn": STATUSNAVN[status], "tillegg": tillegg,
        "ferdig": all(g["status"] != "ikke" for g in relevante) and not tillegg,
        "leverandorer": len(relevante), "bestilte": sum(1 for g in relevante if g["status"] != "ikke"),
        "grupper": gr,
    }


def _gruppe_ut(key, navn, aktive, b, tillegg, overskudd) -> dict:
    status = b["status"] if b else ("ikke" if aktive else "lager")
    # bestilt: det som faktisk ble bestilt (BOM-en sier kanskje 0 nå, når varene er lagt på lager)
    return {
        "key": key, "navn": navn, "linjer": len(b["linjer"]) if b else len(aktive),
        "eks": round(sum(l["antall"] * l["pris"] for l in b["linjer"]) if b else sum(r["total"] for r in aktive), 2),
        "status": status, "statusNavn": STATUSNAVN[status],
        "bestiltDato": b["bestilt_dato"] if b else None, "mottattDato": b["mottatt_dato"] if b else None,
        "ordrenr": b["ordrenr"] if b else "", "tillegg": tillegg, "overskudd": overskudd,
        "mottak": mottak(b) if b else [], "lagerLogg": (b.get("lager_logg") or []) if b else [],
    }
