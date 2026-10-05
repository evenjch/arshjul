"""Lesing og skriving av BOM-filer (Excel) etter malen.

Excel-filen er sjefen for komponentene: dette modulet leser den, men skriver
aldri til en eksisterende BOM. Det eneste det skriver er nye maler.
"""
from __future__ import annotations

import datetime as dt
import json
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

def _key(r: dict) -> str:
    return (r.get("varenummer") or r.get("beskrivelse") or "").strip().lower()


FIELDS = [("beskrivelse", "beskrivelse"), ("leverandor", "leverandør"), ("prSett", "per sett"),
          ("lager", "på lager"), ("pris", "pris"), ("kommentar", "kommentar")]


def diff(old: list[dict], new: list[dict]) -> list[dict]:
    a = {_key(r): r for r in old}
    b = {_key(r): r for r in new}
    out = []
    for r in new:
        if _key(r) not in a:
            out.append({"type": "ny", "beskrivelse": r["beskrivelse"], "varenummer": r["varenummer"], "prSett": r["prSett"]})
    for r in old:
        if _key(r) not in b:
            out.append({"type": "fjernet", "beskrivelse": r["beskrivelse"], "varenummer": r["varenummer"]})
    for r in new:
        o = a.get(_key(r))
        if not o:
            continue
        for k, label in FIELDS:
            x, y = o.get(k, ""), r.get(k, "")
            changed = abs(_num(x) - _num(y)) > 1e-9 if isinstance(y, (int, float)) else _txt(x) != _txt(y)
            if changed:
                out.append({"type": "endret", "beskrivelse": r["beskrivelse"], "felt": label, "fra": x, "til": y})
    return out


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
