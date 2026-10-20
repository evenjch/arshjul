"""Order status per supplier for one run.

The BOM's lines are grouped by supplier. When a supplier is marked as ordered, a frozen copy of
what was ordered is saved. It's compared later with what the BOM and the set count require now,
so you see what needs ordering on top.
"""
from __future__ import annotations

from bom import row_key

EPS = 1e-9
UNKNOWN = "Ukjent leverandør"
# from "worst" to "best"; a run gets the worst status among its suppliers
ORDER_OF_STATUS = ["none", "ordered", "partial", "received"]
STATUS_NAMES = {
    "none": "Ikke bestilt", "partially_ordered": "Delvis bestilt", "ordered": "Bestilt",
    "partial": "Delvis mottatt", "received": "Mottatt", "in_stock": "Alt på lager",
}
SAVED_STATUSES = ("ordered", "partial", "received")


def supplier_key(name: str | None) -> str:
    return (name or "").strip().lower()


def groups(rows: list[dict]) -> list[dict]:
    """BOM lines (from bom.calc) grouped by supplier, in the order they appear in the BOM."""
    out: dict[str, dict] = {}
    for r in rows:
        k = supplier_key(r.get("supplier"))
        g = out.setdefault(k, {"key": k, "name": (r.get("supplier") or "").strip() or UNKNOWN, "rows": []})
        g["rows"].append(r)
    return list(out.values())


def group(rows: list[dict], key: str) -> dict | None:
    return next((g for g in groups(rows) if g["key"] == key), None)


def freeze(rows: list[dict]) -> list[dict]:
    """What is to be ordered now, as it's saved when you press «Bestilt»."""
    return [{"key": row_key(r), "description": r["description"], "part_number": r.get("part_number", ""),
             "quantity": r["to_order"], "price": r["price"]} for r in rows if r["to_order"] > EPS]


def merge(old_count: list[dict], new: list[dict]) -> list[dict]:
    """After an extra order: the larger of what's been ordered and what's needed, per line."""
    out = {l["key"]: dict(l) for l in old_count}
    for l in new:
        if l["key"] not in out or l["quantity"] > out[l["key"]]["quantity"]:
            out[l["key"]] = dict(l)
    return list(out.values())


def deviation(frozen: list[dict], rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(extra, surplus): lines that need more, and lines ordered more of than necessary."""
    ordered = {l["key"]: l for l in frozen}
    needs = {row_key(r): r for r in rows}
    extra = []
    for k, r in needs.items():
        has = ordered[k]["quantity"] if k in ordered else 0.0
        if r["to_order"] > has + EPS:
            extra.append({"description": r["description"], "part_number": r.get("part_number", ""),
                            "ordered": has, "needs": r["to_order"], "missing": r["to_order"] - has})
    surplus = []
    for k, l in ordered.items():
        needed = needs[k]["to_order"] if k in needs else 0.0
        if l["quantity"] > needed + EPS:
            surplus.append({"description": l["description"], "part_number": l.get("part_number", ""),
                              "ordered": l["quantity"], "needs": needed})
    return extra, surplus


def cost(saved: dict[str, dict]) -> float | None:
    """What has actually been ordered (excl. VAT), from the frozen copies. None if nothing is registered."""
    if not saved:
        return None
    return round(sum(l["quantity"] * l["price"] for b in saved.values() for l in b["lines"]), 2)


def receipt(b: dict) -> list[dict]:
    """Per ordered line: ordered, put in stock, and what's still to be put in stock."""
    added = b.get("stocked") or {}
    return [{"key": l["key"], "description": l["description"], "part_number": l.get("part_number", ""),
             "ordered": l["quantity"], "added": added.get(l["key"], 0.0),
             "remaining": max(0.0, l["quantity"] - added.get(l["key"], 0.0))} for l in b["lines"]]


def _with_received(rows: list[dict], added: dict[str, float]) -> list[dict]:
    """What this order has put in stock has already lowered «å bestille» in the BOM.
    Add it back before comparing, otherwise it looks as if too much was ordered."""
    return [{**r, "to_order": r["to_order"] + added.get(row_key(r), 0.0)} for r in rows]


def overview(rows: list[dict], saved: dict[str, dict], compare: bool = True) -> dict:
    """Status for each supplier and for the whole run.

    rows:    bom.calc(...)["rows"] for the run
    saved:   registered orders, by supplier key
    compare: look for "needs more / ordered too much". Only makes sense before the project
             starts – afterwards the BOM is changed by counts and next year's purchases.
    """
    gr = []
    for g in groups(rows):
        active = [r for r in g["rows"] if r["to_order"] > EPS]
        b = saved.get(g["key"])
        extra, surplus = (deviation(b["lines"], _with_received(g["rows"], b.get("stocked") or {}))
                              if b and compare else ([], []))
        gr.append(_group_out(g["key"], g["name"], active, b, extra, surplus))
    known = {g["key"] for g in gr}
    for key, b in saved.items():  # the supplier was removed from the BOM after ordering
        if key not in known:
            _, surplus = deviation(b["lines"], []) if compare else ([], [])
            gr.append({**_group_out(key, b["name"], [], b, [], surplus), "removed": True})

    relevant = [g for g in gr if g["status"] != "in_stock"]
    if not relevant:
        status = "in_stock"
    else:
        lowest = min(ORDER_OF_STATUS.index(g["status"]) for g in relevant)
        if lowest == 0:
            status = "partially_ordered" if any(g["status"] != "none" for g in relevant) else "none"
        else:
            status = ORDER_OF_STATUS[lowest]
    extra = any(g["extra"] for g in gr)
    return {
        "status": status, "statusName": STATUS_NAMES[status], "extra": extra,
        "done": all(g["status"] != "none" for g in relevant) and not extra,
        "suppliers": len(relevant), "orderedCount": sum(1 for g in relevant if g["status"] != "none"),
        "groups": gr,
    }


def _group_out(key, name, active, b, extra, surplus) -> dict:
    status = b["status"] if b else ("none" if active else "in_stock")
    # ordered: what was actually ordered (the BOM may say 0 now, once the parts are in stock)
    return {
        "key": key, "name": name, "lines": len(b["lines"]) if b else len(active),
        "excl": round(sum(l["quantity"] * l["price"] for l in b["lines"]) if b else sum(r["total"] for r in active), 2),
        "status": status, "statusName": STATUS_NAMES[status],
        "orderedDate": b["ordered_date"] if b else None, "receivedDate": b["received_date"] if b else None,
        "order_no": b["order_no"] if b else "", "extra": extra, "surplus": surplus,
        "receipt": receipt(b) if b else [], "stockLog": (b.get("stock_log") or []) if b else [],
    }
