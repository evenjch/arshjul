"""Lesing og skriving av BOM-filer (Excel) etter malen.

Excel-filen er sjefen for komponentene: dette modulet leser den og lager nye maler.
To unntak, begge alltid med en sikkerhetskopi først:
- «På lager»-kolonnen (H) etter telling og varemottak, når brukeren har bekreftet det (write_stock)
- antall sett (B10), som årshjulet styrer – ellers regner Excel feil «Å bestille» (write_sett)
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import openpyxl

FIRST_ROW, LAST_ROW = 14, 73


class BomError(Exception):
    """Feil som kan vises direkte til brukeren."""


def _num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return 0.0


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def read_bom(path: Path) -> dict:
    """Leser en BOM laget fra malen. Regner ikke ut antall å bestille –
    det gjøres med årshjulets antall sett (se calc)."""
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    except PermissionError as e:
        raise BomError(f"«{path.name}» er låst. Lukk den i Excel og prøv igjen.") from e
    except Exception as e:  # noqa: BLE001
        raise BomError(f"Kunne ikke lese «{path.name}». Er det en .xlsx-fil?") from e
    ws = wb.worksheets[0]
    if _txt(ws["C13"].value).lower() != "beskrivelse":
        raise BomError(f"«{path.name}» ser ikke ut som en BOM laget fra malen.")

    dato = ws["F7"].value or ws["E7"].value
    if isinstance(dato, (dt.datetime, dt.date)):
        dato = dato.strftime("%d.%m.%Y")
    mva = ws["D10"].value
    rader = []
    for r in range(FIRST_ROW, LAST_ROW + 1):
        besk = _txt(ws[f"C{r}"].value)
        if not besk:
            continue
        rader.append({
            "beskrivelse": besk,
            "kategori": _txt(ws[f"D{r}"].value),
            "leverandor": _txt(ws[f"E{r}"].value),
            "varenummer": _txt(ws[f"F{r}"].value),
            "prSett": _num(ws[f"G{r}"].value),
            "lager": _num(ws[f"H{r}"].value),
            "pris": _num(ws[f"J{r}"].value),
            "kommentar": _txt(ws[f"L{r}"].value),
        })
    return {
        "fil": path.name,
        "prosjekt": _txt(ws["B7"].value),
        "ansvarlig": _txt(ws["D7"].value),
        "dato": _txt(dato),
        "sett": int(_num(ws["B10"].value)) or None,
        "mva": 0.25 if mva in (None, "") else _num(mva),
        "rader": rader,
        "endret": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
    }


def calc(bom: dict, sett: int) -> dict:
    """Antall å bestille = per sett × antall sett − på lager."""
    rader = []
    for r in bom["rader"]:
        bestill = max(0.0, r["prSett"] * sett - r["lager"])
        rader.append({**r, "bestill": bestill, "total": round(bestill * r["pris"], 2)})
    eks = round(sum(r["total"] for r in rader), 2)
    return {
        "rader": rader,
        "linjer": len(rader),
        "bestill": sum(1 for r in rader if r["bestill"] > 0),
        "eks": eks,
        "inkl": round(eks * (1 + bom["mva"]), 2),
    }


# ---------- endringer siden sist ----------

def rad_key(r: dict) -> str:
    """Identiteten til en BOM-linje: leverandør + varenummer (eller beskrivelse uten varenummer).

    Varenumre er bare unike hos én leverandør – RS og Farnell kan ha samme nummer på hver sin vare.
    """
    lev = (r.get("leverandor") or "").strip().lower()
    return f"{lev}|{(r.get('varenummer') or r.get('beskrivelse') or '').strip().lower()}"


def _unike(rader: list[dict]) -> list[tuple[str, dict]]:
    """(nøkkel, rad) der like nøkler får «#2», «#3» … i rekkefølge, så to helt like linjer
    i samme BOM ikke blandes sammen."""
    sett: dict[str, int] = {}
    out = []
    for r in rader:
        k = rad_key(r)
        sett[k] = sett.get(k, 0) + 1
        out.append((k if sett[k] == 1 else f"{k}#{sett[k]}", r))
    return out


# leverandør er en del av nøkkelen: bytte leverandør vises som en linje fjernet og en ny
FIELDS = [("beskrivelse", "beskrivelse"), ("prSett", "per sett"),
          ("lager", "på lager"), ("pris", "pris"), ("kommentar", "kommentar")]


def diff(old: list[dict], new: list[dict]) -> list[dict]:
    old_k, new_k = _unike(old), _unike(new)
    a, b = dict(old_k), dict(new_k)
    out = []
    for k, r in new_k:
        if k not in a:
            out.append({"type": "ny", "beskrivelse": r["beskrivelse"], "varenummer": r["varenummer"], "prSett": r["prSett"]})
    for k, r in old_k:
        if k not in b:
            out.append({"type": "fjernet", "beskrivelse": r["beskrivelse"], "varenummer": r["varenummer"]})
    for k, r in new_k:
        o = a.get(k)
        if not o:
            continue
        for k, label in FIELDS:
            x, y = o.get(k, ""), r.get(k, "")
            changed = abs(_num(x) - _num(y)) > 1e-9 if isinstance(y, (int, float)) else _txt(x) != _txt(y)
            if changed:
                out.append({"type": "endret", "beskrivelse": r["beskrivelse"], "felt": label, "fra": x, "til": y})
    return out


# ---------- oppdatere lager etter telling ----------

def write_stock(path: Path, verdier: dict[str, float], backup_dir: Path,
                forventet: dict[str, float] | None = None, legg_til: bool = False) -> dict:
    """Skriv nye «På lager»-tall (kolonne H) i en eksisterende BOM.

    verdier:   {linje-nøkkel: nytt antall}, eller med legg_til: antall som legges til
               (negativt trekker fra). Bare kolonne H endres.
    forventet: {linje-nøkkel: antall cellen skal ha nå}. Står det noe annet der (endret i
               Excel siden sist), skrives linjen ikke, men meldes som konflikt.
    legg_til:  varemottak. Legger til det som står i cellen nå – riktig også om cellen er
               endret i Excel. Blir resultatet negativt, er det en konflikt.
    Svar: skrevet/hoppet/konflikt, «nye» = {nøkkel: tallet som nå står i cellen}, og backup.
    Linjer som ikke finnes, eller der nøkkelen står på flere rader, hoppes over.
    Lager en sikkerhetskopi først, men bare hvis noe faktisk skal skrives.
    """
    forventet = forventet or {}
    wb = _apne_for_skriving(path)
    ws = wb.worksheets[0]
    rader: dict[str, list[int]] = {}
    for r in range(FIRST_ROW, LAST_ROW + 1):
        besk = _txt(ws[f"C{r}"].value)
        if besk:
            rader.setdefault(rad_key({"beskrivelse": besk, "varenummer": _txt(ws[f"F{r}"].value),
                                      "leverandor": _txt(ws[f"E{r}"].value)}), []).append(r)
    skrevet, hoppet, konflikt, nye = [], [], [], {}
    for key, antall in verdier.items():
        rows = rader.get(key, [])
        if len(rows) != 1:
            hoppet.append(key)
            continue
        celle = ws[f"H{rows[0]}"]
        if key in forventet and abs(_num(celle.value) - float(forventet[key])) > 1e-9:
            konflikt.append({"key": key, "forventet": float(forventet[key]), "faktisk": _num(celle.value)})
            continue
        ny = _num(celle.value) + float(antall) if legg_til else float(antall)
        if ny < -1e-9:
            konflikt.append({"key": key, "forventet": -float(antall), "faktisk": _num(celle.value)})
            continue
        celle.value = int(ny) if float(ny).is_integer() else ny
        skrevet.append(key)
        nye[key] = ny
    if not skrevet:
        return {"skrevet": [], "hoppet": hoppet, "konflikt": konflikt, "nye": {}, "backup": None}
    backup = _lagre_med_backup(wb, path, backup_dir)
    return {"skrevet": skrevet, "hoppet": hoppet, "konflikt": konflikt, "nye": nye, "backup": backup}


# ---------- antall sett (B10) ----------

_INGEN = object()


def write_sett(path: Path, sett: int | None, backup_dir: Path, forventet=_INGEN) -> dict:
    """Skriv årshjulets antall sett i B10, som «Å bestille»-formlene i BOM-en bruker.

    forventet: det B10 skal inneholde nå (årshjulets forrige tall). Står det noe annet der
               (endret for hånd i Excel), skrives det ikke, men meldes som konflikt.
    Svar: {"skrevet": bool, "for": tallet som stod der, "fil": tallet i B10 nå, "backup": navn eller None}.
    """
    wb = _apne_for_skriving(path)
    celle = wb.worksheets[0]["B10"]
    na = int(_num(celle.value)) or None
    if na == (sett or None):
        return {"skrevet": False, "for": na, "fil": na, "backup": None}
    if forventet is not _INGEN and na != (forventet or None):
        return {"skrevet": False, "for": na, "fil": na, "backup": None, "konflikt": True}
    celle.value = sett or None
    return {"skrevet": True, "for": na, "fil": sett or None, "backup": _lagre_med_backup(wb, path, backup_dir)}


# ---------- felles for skriving ----------

def _apne_for_skriving(path: Path):
    """Åpne en BOM med formlene (ikke verdiene), og nekt hvis den er åpen i Excel."""
    if (path.parent / f"~${path.name}").exists():
        raise BomError(f"«{path.name}» er åpen i Excel. Lukk den og prøv igjen.")
    try:
        wb = openpyxl.load_workbook(path)
    except PermissionError as e:
        raise BomError(f"«{path.name}» er låst. Lukk den i Excel og prøv igjen.") from e
    if _txt(wb.worksheets[0]["C13"].value).lower() != "beskrivelse":
        raise BomError(f"«{path.name}» ser ikke ut som en BOM laget fra malen.")
    return wb


def _lagre_med_backup(wb, path: Path, backup_dir: Path) -> str:
    """Sikkerhetskopi av filen slik den er, så lagre. Returnerer navnet på kopien."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{path.stem} {dt.datetime.now():%Y-%m-%d %H%M%S}"
    backup, n = backup_dir / f"{stamp}{path.suffix}", 2
    while backup.exists():  # aldri overskriv en tidligere sikkerhetskopi
        backup, n = backup_dir / f"{stamp} ({n}){path.suffix}", n + 1
    shutil.copy2(path, backup)
    wb.calculation.fullCalcOnLoad = True  # Excel regner ut «Å bestille» og «Total» på nytt
    try:
        wb.save(path)
    except PermissionError as e:
        raise BomError(f"«{path.name}» er låst. Lukk den i Excel og prøv igjen.") from e
    return backup.name


# ---------- nye filer ----------

def make_template(template: Path, target: Path, *, prosjekt: str, dato: dt.date,
                  frist: dt.date, sett: int | None) -> None:
    if target.exists():
        raise BomError(f"«{target.name}» finnes allerede i BOM-mappen.")
    wb = openpyxl.load_workbook(template)
    ws = wb.worksheets[0]
    ws["B7"] = prosjekt
    ws["F7"] = dt.datetime.combine(dato, dt.time())
    ws["F10"] = dt.datetime.combine(frist, dt.time())
    ws["B10"] = sett or None
    wb.save(target)


def make_order_list(template: Path, target: Path, *, bom: dict, beregnet: dict, prosjekt: str,
                    sett: int, list_id: str) -> None:
    rows = []
    for i, r in enumerate(beregnet["rader"], start=1):
        rows.append({"id": i, "nr": i, "beskrivelse": r["beskrivelse"], "kategori": r["kategori"],
                     "leverandor": r["leverandor"], "varenummer": r["varenummer"],
                     "antall": r["bestill"], "pris": r["pris"], "total": r["total"], "kommentar": r["kommentar"]})
    now = dt.datetime.now()
    data = {
        "meta": {"prosjekt": bom["prosjekt"] or prosjekt, "ansvarlig": bom["ansvarlig"], "dato": bom["dato"],
                 "sett": str(sett) if sett else ""},
        "cols": [{"key": "nr", "label": "Nr"}, {"key": "beskrivelse", "label": "Beskrivelse"},
                 {"key": "kategori", "label": "Kategori"}, {"key": "leverandor", "label": "Leverandør"},
                 {"key": "varenummer", "label": "Varenummer"}, {"key": "antall", "label": "Antall", "kind": "qty"},
                 {"key": "pris", "label": "Pris pr enhet", "kind": "money"},
                 {"key": "total", "label": "Total", "kind": "money"}, {"key": "kommentar", "label": "Kommentar"}],
        "rows": rows,
        "totals": {"eks": beregnet["eks"], "inkl": beregnet["inkl"]},
        "source": bom["fil"],
        "generated": f"{now.day}.{now.month}.{now.year} {now:%H:%M}",
        "id": list_id,
    }
    js = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    html = template.read_text(encoding="utf-8").replace("__BOM_JSON__", js, 1)
    target.write_text(html, encoding="utf-8")
