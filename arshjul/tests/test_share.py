"""/hjul and /uker show one panel on its own; /hjul/<key> is the wheel shared with students, without login.

The shared wheel must only send what it draws – project names and the weeks they're in use – never deadlines,
orders, BOMs, courses or people.
"""
import json
import time
from pathlib import Path

import app as arshjul
import config
from test_access import new_client, state
from test_courses import G, SY, inst, log_in  # noqa: F401 – inst is a fixture
from test_page import STUB, MiniRacer

PUBLIC_FIELDS = {"id", "school_year", "name", "type", "color", "start", "finish", "semester", "deadlines", "events"}


def share_url(c, body=None):
    return (c.post("/api/share", json=body or {}) if body is not None else c.get("/api/share")).get_json()["url"]


def path(url):
    return "/" + url.split("/", 3)[3]


def test_shared_wheel_sends_only_names_and_weeks(inst):
    anne = log_in("anne")
    assert share_url(anne) is None  # nothing is shared until you ask
    url = share_url(anne, {})
    assert share_url(anne) == url and share_url(anne, {}) == url  # the same link until you make a new one
    anon = new_client()
    page = anon.get(path(url))
    assert page.status_code == 200 and "Delt av <b>Anne</b>" in page.get_data(as_text=True)
    d = anon.get(path(url) + "/data")
    assert d.headers["Cache-Control"] == "no-store"
    d = d.get_json()
    # Anne's projects only: courses A and C, and the one without a course – as on her own wheel
    assert sorted(p["name"] for p in d["projects"]) == ["Prosjekt A", "Prosjekt C", "Prosjekt uten emne"]
    for p in d["projects"]:
        assert set(p) == PUBLIC_FIELDS and set(p["deadlines"]) == {"start", "finish"} and p["events"] == []
    assert d["me"]["role"] == "lecturer" and d["courses"] == d["changes"] == d["engineers"] == []
    assert "Anne" not in json.dumps(d) and "EMNE-A" not in json.dumps(d)


def test_new_link_and_stop_sharing(inst):
    anne, anon = log_in("anne"), new_client()
    old = share_url(anne, {})
    new = share_url(anne, {"new": True})
    assert new != old
    assert anon.get(path(old)).status_code == 404 and anon.get(path(old) + "/data").status_code == 404
    assert anon.get(path(new) + "/data").status_code == 200
    assert share_url(anne, {"stop": True}) is None
    assert anon.get(path(new)).status_code == 404
    # a deactivated user's link stops working
    url = share_url(anne, {})
    arshjul.store.update_user(inst["u"]["anne"], active=0)
    assert anon.get(path(url) + "/data").status_code == 404


def test_removed_projects_arent_shared(inst):
    log_in("ola").delete(f"{G}/{inst['p']['EMNE-A']}")  # removed by the lecturer, waiting for Anne
    url = share_url(log_in("anne"), {})
    assert "Prosjekt A" not in [p["name"] for p in new_client().get(path(url) + "/data").get_json()["projects"]]


def test_views_need_login_and_work_for_lecturers(inst):
    anon = new_client()
    assert anon.get("/hjul").status_code == 302 and anon.get("/uker").status_code == 302
    assert anon.get("/api/share").status_code == 401
    ola = log_in("ola")
    assert 'data-view="wheel"' in ola.get("/hjul").get_data(as_text=True)
    assert 'data-view="weeks"' in ola.get("/uker").get_data(as_text=True)
    assert share_url(ola, {}) is not None
    html = ola.get("/").get_data(as_text=True)
    assert 'href="/hjul"' in html and 'href="/uker"' in html and "data-view" not in html


def run_view(state, dataset):
    """The page's JS in V8 with data-view (and data-share) on <body>: draws only that panel."""
    src = (Path(config.APP_DIR) / "static" / "arshjul.js").read_text(encoding="utf-8")
    ctx = MiniRacer()
    ctx.eval("const STATE = " + json.dumps(state) + ";")
    ctx.eval(STUB)
    ctx.eval("document.body.dataset = " + json.dumps(dataset) + "; globalThis.localStorage = { getItem: () => null, setItem() {} };"
             "globalThis.__urls = []; const __f = globalThis.fetch; globalThis.fetch = async (u, o) => { __urls.push(u); return __f(u, o); };")
    ctx.eval(src)
    ctx.eval("""setTimeout = (f) => 0; (async () => { for (let i = 0; i < 50; i++) await Promise.resolve();
      globalThis.__out = JSON.stringify({ wheel: __el("wheel").innerHTML, list: __el("wheelList").innerHTML, weeks: __el("weeks").innerHTML, tbody: __el("tbody").innerHTML,
                                          status: __el("status").textContent, urls: __urls, errors: globalThis.__consoleErrors || [] }); })();""")
    for _ in range(100):
        out = ctx.eval("globalThis.__out")
        if out:
            return json.loads(out)
        time.sleep(0.02)
    raise AssertionError("siden ble ikke ferdig")


def test_shared_wheel_draws_from_the_shared_data(inst):
    url = share_url(log_in("anne"), {})
    data = new_client().get(path(url) + "/data").get_json()
    data["schoolYearShown"] = SY  # the test's projects are in next school year
    res = run_view(data, {"view": "wheel", "share": url.rsplit("/", 1)[1]})
    assert res["errors"] == [] and res["status"] == ""
    assert res["urls"] and all(u.startswith("/hjul/") and u.endswith("/data") for u in res["urls"])  # never /api/state
    assert "Prosjekt A" in res["wheel"] and "bestillingsfrist" not in res["wheel"]
    # what each arc is: the projects beside the wheel, with type and weeks
    assert "<b>Prosjekt A</b>" in res["list"] and "Hackathon · uke 44" in res["list"] and 'class="nr"' in res["list"]
    assert 'font-weight="700" style="fill:var(--ink)">1</text>' in res["wheel"]  # the same numbers on the arcs
    assert res["tbody"] == "" and res["weeks"] == ""  # only the wheel is drawn


def test_weeks_view_draws_only_the_weeks(inst):
    res = run_view(state(log_in("anne")), {"view": "weeks"})
    assert res["errors"] == [] and "Uke " in res["weeks"] and res["wheel"] == "" and res["tbody"] == ""
