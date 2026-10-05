import openpyxl
import pytest

import bom
import config
from hjelp import lag_bom


def test_mal_kan_leses_tilbake():
    b = bom.read_bom(lag_bom())
    assert b["prosjekt"] == "Testprosjekt"
    assert b["sett"] == 10
    assert b["rader"] == []


def test_mal_overskriver_ikke():
    lag_bom()
    with pytest.raises(bom.BomError):
        lag_bom()


def test_les_rader_og_regn_ut():
    path = lag_bom(rader=[("Arduino Uno", "Elfa", "A1", 1, 4, 250.0),
                          ("Motstand 220", "Elfa", "R220", 10, 500, 0.5)])
    b = bom.read_bom(path)
    assert [r["beskrivelse"] for r in b["rader"]] == ["Arduino Uno", "Motstand 220"]
    c = bom.calc(b, 10)
    arduino, motstand = c["rader"]
    assert arduino["bestill"] == 6  # 1 × 10 − 4
    assert motstand["bestill"] == 0  # aldri negativt
    assert c["bestill"] == 1
    assert c["eks"] == 1500.0
    assert c["inkl"] == round(1500.0 * (1 + b["mva"]), 2)


def test_ikke_en_bom():
    path = config.BOM_DIR / "tilfeldig.xlsx"
    openpyxl.Workbook().save(path)
    with pytest.raises(bom.BomError):
        bom.read_bom(path)


def test_diff():
    gammel = [{"beskrivelse": "A", "varenummer": "1", "prSett": 1, "lager": 0, "pris": 10},
              {"beskrivelse": "B", "varenummer": "2", "prSett": 1, "lager": 0, "pris": 10}]
    ny = [{"beskrivelse": "A", "varenummer": "1", "prSett": 2, "lager": 0, "pris": 10},
          {"beskrivelse": "C", "varenummer": "3", "prSett": 1, "lager": 0, "pris": 10}]
    d = bom.diff(gammel, ny)
    typer = sorted((e["type"], e["beskrivelse"]) for e in d)
    assert typer == [("endret", "A"), ("fjernet", "B"), ("ny", "C")]
    endret = next(e for e in d if e["type"] == "endret")
    assert (endret["felt"], endret["fra"], endret["til"]) == ("per sett", 1, 2)
