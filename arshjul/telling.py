"""Telling etter prosjektet: hva som kom tilbake, svinn, og nytt «på lager» til neste år.

For hver BOM-linje:
    før prosjektet   = på lager (BOM) + bestilt (registrert, ellers det BOM-en sa skulle bestilles)
    forventet        = før prosjektet                  for utstyr (skal komme tilbake)
                     = før prosjektet − delt ut          for forbruk (brukes opp)
    telt             = det du faktisk har etterpå → blir nytt «på lager» i BOM-en
    svinn            = forventet − telt (utstyr som ikke kom tilbake)
"""
from __future__ import annotations

from bestilling import lev_key
from bom import rad_key

TYPER = ("utstyr", "forbruk")


def bestilt_per_linje(lagret: dict[str, dict]) -> tuple[dict[str, float], set[str]]:
    """(sum bestilt per linje, leverandører som er registrert bestilt) fra de frosne kopiene."""
    out: dict[str, float] = {}
    for b in lagret.values():
        for l in b["linjer"]:
            out[l["key"]] = out.get(l["key"], 0.0) + l["antall"]
    return out, set(lagret)


def grunnlag(rader: list[dict], bestilt: tuple[dict[str, float], set[str]], typer: dict[str, str],
             sett: int) -> list[dict]:
    """Tallene en telling bygger på, per BOM-linje (rader fra bom.calc).

    Er leverandøren registrert som bestilt, brukes det som faktisk ble bestilt. Ellers antas
    det at det BOM-en sa skulle bestilles, ble bestilt («antatt»).
    """
    per_linje, leverandorer = bestilt
    out = []
    for r in rader:
        k = rad_key(r)
        registrert = lev_key(r.get("leverandor")) in leverandorer
        out.append({
            "key": k, "beskrivelse": r["beskrivelse"], "varenummer": r.get("varenummer", ""),
            "type": typer.get(k, "utstyr"), "lagerFor": r["lager"],
            "bestilt": per_linje.get(k, 0.0) if registrert else r["bestill"], "antatt": not registrert,
            "utdelt": r["prSett"] * sett, "pris": r["pris"],
        })
    return out


def forventet(l: dict) -> float:
    total = l["lagerFor"] + l["bestilt"]
    return max(0.0, total - l["utdelt"]) if l["type"] == "forbruk" else total


def lag(grunnlag_: list[dict], innspill: dict[str, dict]) -> list[dict]:
    """Telling fra brukerens tall. innspill: {nøkkel: {"type": …, "telt": …}}; linjer uten tall telles ikke."""
    out = []
    for l in grunnlag_:
        i = innspill.get(l["key"])
        if i is None:
            continue
        typ = i.get("type", l["type"])
        if typ not in TYPER:
            raise ValueError(f"Ugyldig type: {typ}")
        telt = float(i["telt"])
        if telt < 0:
            raise ValueError("Telt antall kan ikke være negativt.")
        ny = {**l, "type": typ, "telt": telt}
        ny["forventet"] = forventet(ny)
        out.append(ny)
    return out


def ventende(linjer: list[dict], skrevet_verdier: dict[str, float]) -> list[str]:
    """Linjer der det telte ikke er det som sist ble skrevet til BOM-en."""
    return [l["key"] for l in linjer
            if l["key"] not in skrevet_verdier or abs(l["telt"] - skrevet_verdier[l["key"]]) > 1e-9]


def oppsummering(linjer: list[dict]) -> dict:
    """Svinn regnes bare for utstyr; forbruk som ikke er brukt opp er bare et avvik."""
    svinn = [l for l in linjer if l["type"] == "utstyr" and l["telt"] < l["forventet"] - 1e-9]
    return {
        "telte": len(linjer),
        "avvik": sum(1 for l in linjer if abs(l["telt"] - l["forventet"]) > 1e-9),
        "svinn": len(svinn),
        "svinnKr": round(sum((l["forventet"] - l["telt"]) * l["pris"] for l in svinn), 2),
    }
