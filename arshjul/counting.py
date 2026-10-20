"""Counting after the project: what came back, loss, and the new «på lager» for next year.

For each BOM line:
    before the project = in stock (BOM) + ordered but not put in stock yet
                         (registered, otherwise what the BOM said to order)
    expected           = before the project               for equipment (should come back)
                       = before the project − handed out  for consumables (used up)
    counted            = what you actually have afterwards → becomes the new «på lager» in the BOM
    loss               = expected − counted (equipment that didn't come back)
"""
from __future__ import annotations

from bom import row_key
from orders import supplier_key

TYPES = ("equipment", "consumable")


def ordered_per_line(saved: dict[str, dict]) -> tuple[dict[str, float], set[str]]:
    """(ordered but not put in stock yet – per line, suppliers registered as ordered).

    What a receipt put in stock is already in «På lager» in the BOM and
    must not be counted a second time.
    """
    out: dict[str, float] = {}
    for b in saved.values():
        added = b.get("stocked") or {}
        for l in b["lines"]:
            out[l["key"]] = out.get(l["key"], 0.0) + max(0.0, l["quantity"] - added.get(l["key"], 0.0))
    return out, set(saved)


def basis(rows: list[dict], ordered: tuple[dict[str, float], set[str]], types: dict[str, str],
             sets: int) -> list[dict]:
    """The numbers a count is based on, per BOM line (rows from bom.calc).

    If the supplier is registered as ordered, what was actually ordered is used. Otherwise it's
    assumed that what the BOM said to order was ordered («assumed»).
    """
    per_line, suppliers = ordered
    out = []
    for r in rows:
        k = row_key(r)
        registered = supplier_key(r.get("supplier")) in suppliers
        out.append({
            "key": k, "description": r["description"], "part_number": r.get("part_number", ""),
            "type": types.get(k, "equipment"), "stockBefore": r["in_stock"],
            "ordered": per_line.get(k, 0.0) if registered else r["to_order"], "assumed": not registered,
            "handed_out": r["perSet"] * sets, "price": r["price"],
        })
    return out


def expected(l: dict) -> float:
    total = l["stockBefore"] + l["ordered"]
    return max(0.0, total - l["handed_out"]) if l["type"] == "consumable" else total


def make(basis_: list[dict], entries: dict[str, dict]) -> list[dict]:
    """A count from the user's numbers. entries: {key: {"type": …, "counted": …}}; lines without a number aren't counted."""
    out = []
    for l in basis_:
        i = entries.get(l["key"])
        if i is None:
            continue
        typ = i.get("type", l["type"])
        if typ not in TYPES:
            raise ValueError(f"Ugyldig type: {typ}")
        counted = float(i["counted"])
        if counted < 0:
            raise ValueError("Telt antall kan ikke være negativt.")
        new = {**l, "type": typ, "counted": counted}
        new["expected"] = expected(new)
        out.append(new)
    return out


def pending(lines: list[dict], written_values: dict[str, float]) -> list[str]:
    """Lines where what was counted isn't what was last written to the BOM."""
    return [l["key"] for l in lines
            if l["key"] not in written_values or abs(l["counted"] - written_values[l["key"]]) > 1e-9]


def changed_in_bom(written_values: dict[str, float], rows: list[dict]) -> dict[str, float]:
    """Lines where «På lager» in the BOM is no longer what the app wrote (changed in Excel since).
    rows: the BOM lines as they are now. Returns: {key: the number in the BOM now}."""
    current = {row_key(r): r["in_stock"] for r in rows}
    return {k: current[k] for k, v in written_values.items() if k in current and abs(current[k] - v) > 1e-9}


def summary(lines: list[dict]) -> dict:
    """Loss is only counted for equipment; consumables that weren't used up are just a deviation."""
    loss = [l for l in lines if l["type"] == "equipment" and l["counted"] < l["expected"] - 1e-9]
    return {
        "counted_lines": len(lines),
        "deviation": sum(1 for l in lines if abs(l["counted"] - l["expected"]) > 1e-9),
        "loss": len(loss),
        "lossCost": round(sum((l["expected"] - l["counted"]) * l["price"] for l in loss), 2),
    }
