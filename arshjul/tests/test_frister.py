import datetime as dt

import frister

STENGT = [
    {"navn": "Juleferie", "fra": 52, "til": 1},
    {"navn": "Påske", "fra": 12, "til": 12},
    {"navn": "Fellesferie", "fra": 28, "til": 31},
]


def run(**kw):
    return {"skolear": 2026, "start": 40, "slutt": 40, "lev": 2, "buf": 2, "type": "hack", **kw}


def test_in_range_over_nyttar():
    assert frister.in_range(52, 52, 1)
    assert frister.in_range(53, 52, 1)
    assert frister.in_range(1, 52, 1)
    assert not frister.in_range(2, 52, 1)


def test_skolear_starter_i_uke_32():
    assert frister.skolear_start(2026) == dt.date(2026, 8, 3)
    assert frister.skolear_for(dt.date(2026, 8, 2)) == 2025
    assert frister.skolear_for(dt.date(2026, 8, 3)) == 2026
    assert frister.skolear_for(dt.date(2027, 6, 1)) == 2026
    assert frister.skolear_navn(2026) == "2026/27"
    assert frister.skolear_navn(2099) == "2099/00"


def test_uke_dato_hoster_og_var():
    assert frister.uke_dato(2026, 34) == dt.date(2026, 8, 17)
    assert frister.uke_dato(2026, 3) == dt.date(2027, 1, 18)


def test_uke_53():
    assert frister.uker_i_ar(2026) == 53
    assert frister.uker_i_ar(2027) == 52
    assert len(frister.skolear_uker(2026, STENGT)) == 53
    assert len(frister.skolear_uker(2027, STENGT)) == 52
    assert frister.uke_dato(2026, 53) == dt.date(2026, 12, 28)
    assert frister.uke_dato(2027, 53) == frister.uke_dato(2027, 52)  # 2027 har ikke uke 53


def test_arduino_frist_i_juni_forrige_skolear():
    # uke 34, 2 + 3 uker, fellesferien hoppes over → uke 25, 15. juni 2026
    f = frister.frister(run(start=34, slutt=50, lev=2, buf=3, type="sett"), STENGT)
    assert f["frist"] == {"dato": "2026-06-15", "uke": 25, "ar": 2026}
    assert frister.skolear_for(dt.date(2026, 6, 15)) == 2025


def test_frist_over_jul_med_uke_53():
    # uke 2 i 2027, 2 uker: uke 1, 53 og 52 er stengt → uke 50 i 2026
    f = frister.frister(run(start=2, slutt=2, lev=1, buf=1), STENGT)
    assert (f["frist"]["uke"], f["frist"]["ar"]) == (50, 2026)


def test_uten_ledetid_er_frist_lik_start():
    f = frister.frister(run(lev=0, buf=0), STENGT)
    assert f["frist"] == f["start"]


def test_slutt_for_start_blir_start():
    f = frister.frister(run(start=44, slutt=40), STENGT)
    assert f["slutt"] == f["start"]


def test_hendelser():
    f = frister.frister(run(start=44, slutt=44), STENGT)
    kinds = {h["kind"]: h for h in f["hendelser"]}
    assert set(kinds) == {"prep", "frist", "start", "retur"}
    assert kinds["start"]["what"] == "Hackathon"
    assert kinds["retur"]["uke"] == 45  # hackathon: telling uka etter
    sett = frister.frister(run(start=34, slutt=50, type="sett"), STENGT)
    assert {h["kind"]: h for h in sett["hendelser"]}["retur"]["uke"] == 50


def test_semester():
    assert frister.semester(34) == "Høst"
    assert frister.semester(3) == "Vår"
