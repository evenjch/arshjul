import openpyxl
import pytest

import bom
import config
from helpers import make_bom


def test_template_can_be_read_back():
    b = bom.read_bom(make_bom())
    assert b["project"] == "Testprosjekt"
    assert b["sets"] == 10
    assert b["rows"] == []


def test_template_doesnt_overwrite():
    make_bom()
    with pytest.raises(bom.BomError):
        make_bom()


def test_read_rows_and_calculate():
    path = make_bom(rows=[("Arduino Uno", "Elfa", "A1", 1, 4, 250.0),
                          ("Motstand 220", "Elfa", "R220", 10, 500, 0.5)])
    b = bom.read_bom(path)
    assert [r["description"] for r in b["rows"]] == ["Arduino Uno", "Motstand 220"]
    c = bom.calc(b, 10)
    arduino, resistor = c["rows"]
    assert arduino["to_order"] == 6  # 1 × 10 − 4
    assert resistor["to_order"] == 0  # aldri negativt
    assert c["to_order"] == 1
    assert c["excl"] == 1500.0
    assert c["incl"] == round(1500.0 * (1 + b["vat"]), 2)


def test_not_a_bom():
    path = config.BOM_DIR / "tilfeldig.xlsx"
    openpyxl.Workbook().save(path)
    with pytest.raises(bom.BomError):
        bom.read_bom(path)


def test_diff():
    old_count = [{"description": "A", "part_number": "1", "perSet": 1, "in_stock": 0, "price": 10},
              {"description": "B", "part_number": "2", "perSet": 1, "in_stock": 0, "price": 10}]
    new = [{"description": "A", "part_number": "1", "perSet": 2, "in_stock": 0, "price": 10},
          {"description": "C", "part_number": "3", "perSet": 1, "in_stock": 0, "price": 10}]
    d = bom.diff(old_count, new)
    types = sorted((e["type"], e["description"]) for e in d)
    assert types == [("changed", "A"), ("new", "C"), ("removed", "B")]
    changed = next(e for e in d if e["type"] == "changed")
    assert (changed["field"], changed["from"], changed["to"]) == ("per sett", 1, 2)
