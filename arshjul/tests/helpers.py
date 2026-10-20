"""Felles hjelpere for testene."""
import datetime as dt

import openpyxl

import bom
import config


def make_bom(name="Test – BOM.xlsx", rows=(), sets=10):
    """Make a BOM from the template in the test data folder.

    rows: (description, supplier, part number, per set, in stock, price)
    """
    target = config.BOM_DIR / name
    bom.make_template(config.TEMPLATE_BOM, target, project="Testprosjekt", date=dt.date(2026, 8, 1),
                      deadline=dt.date(2026, 6, 15), sets=sets)
    if rows:
        write_rows(target, rows)
    return target


def write_rows(target, rows):
    wb = openpyxl.load_workbook(target)
    ws = wb.worksheets[0]
    for r in range(bom.FIRST_ROW, bom.LAST_ROW + 1):
        for c in "CEFGHJ":
            ws[f"{c}{r}"] = None
    for i, (desc, supplier, part_no, per_set, stock, price) in enumerate(rows):
        r = bom.FIRST_ROW + i
        ws[f"C{r}"], ws[f"E{r}"], ws[f"F{r}"] = desc, supplier, part_no
        ws[f"G{r}"], ws[f"H{r}"], ws[f"J{r}"] = per_set, stock, price
    wb.save(target)
