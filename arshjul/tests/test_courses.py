"""Courses: every project belongs to a course with one lecturer and one responsible engineer.

The department's scenario:
    Course A – lecturer A (Ola),  engineer A (Anne)
    Course B – lecturer A (Ola),  engineer B (Bjørn)
    Course C – lecturer B (Kari), engineer A (Anne)
    Course D – lecturer B (Kari), engineer C (Cecilie)
"""
import datetime as dt

import pytest

import app as arshjul
import auth
import config
import deadlines
from test_access import PASSWORD, log_in, state

G = "/api/runs"
SY = deadlines.school_year_for(dt.date.today()) + 1


@pytest.fixture
def inst(monkeypatch):
    s = arshjul.store
    u = {k: s.new_user(k, name, role, auth.hash_password(PASSWORD)) for k, name, role in (
        ("anne", "Anne", "engineer"), ("bjorn", "Bjørn", "engineer"), ("cecilie", "Cecilie", "engineer"),
        ("ola", "Ola", "lecturer"), ("kari", "Kari", "lecturer"))}
    e = {k: s.new_course({"code": k, "name": f"Emne {k[-1]}", "lecturer_id": u[f], "engineer_id": u[i]})["id"]
         for k, f, i in (("EMNE-A", "ola", "anne"), ("EMNE-B", "ola", "bjorn"),
                         ("EMNE-C", "kari", "anne"), ("EMNE-D", "kari", "cecilie"))}
    auth._ip_failures.clear()
    anne = log_in("anne")
    p = {k: anne.post(G, json={"name": f"Prosjekt {k[-1]}", "school_year": SY, "start": 44, "course": e[k]}).get_json()["id"]
         for k in e}
    p["uten"] = anne.post(G, json={"name": "Prosjekt uten emne", "school_year": SY, "start": 44}).get_json()["id"]
    return {"u": u, "e": e, "p": p}


def own_runs(c):
    return sorted(p["name"] for p in state(c)["projects"] if p["own"])


def test_each_engineer_has_their_courses(inst):
    assert own_runs(log_in("anne")) == ["Prosjekt A", "Prosjekt C", "Prosjekt uten emne"]
    assert own_runs(log_in("bjorn")) == ["Prosjekt B", "Prosjekt uten emne"]
    assert own_runs(log_in("cecilie")) == ["Prosjekt D", "Prosjekt uten emne"]
    # other people's courses aren't even sent to the browser
    assert len(state(log_in("bjorn"))["projects"]) == 2


def test_lecturer_owns_the_projects_in_their_courses(inst):
    assert own_runs(log_in("ola")) == ["Prosjekt A", "Prosjekt B"]
    assert own_runs(log_in("kari")) == ["Prosjekt C", "Prosjekt D"]


def test_engineer_cant_act_in_others_courses(inst):
    anne, pb = log_in("anne"), inst["p"]["EMNE-B"]
    r = anne.patch(f"{G}/{pb}", json={"start": 40})
    assert r.status_code == 403 and "Bjørn" in r.get_json()["error"] and "EMNE-B" in r.get_json()["error"]
    assert anne.delete(f"{G}/{pb}").status_code == 403
    assert anne.post(f"{G}/{pb}/order", json={"action": "ordered", "supplier": "x"}).status_code == 403
    assert anne.post(f"{G}/{pb}/count", json={"lines": [{"key": "x|1", "counted": 1}]}).status_code == 403
    assert anne.post(f"{G}/{pb}/template").status_code == 403
    # Bjørn can, and anyone can act on projects without a course
    assert log_in("bjorn").patch(f"{G}/{pb}", json={"start": 40}).status_code == 200
    assert log_in("cecilie").patch(f"{G}/{inst['p']['uten']}", json={"start": 40}).status_code == 200


def test_notification_goes_to_the_courses_engineer(inst):
    ola = log_in("ola")
    ola.patch(f"{G}/{inst['p']['EMNE-B']}", json={"note": "endret 25"})  # Ola makes a change in course B
    assert [e["project_name"] for e in state(log_in("bjorn"))["changes"]] == ["Prosjekt B"]
    assert state(log_in("anne"))["changes"] == []  # Anne has courses A and C, not B

    ola.patch(f"{G}/{inst['p']['EMNE-A']}", json={"note": "endret 12"})
    assert [e["project_name"] for e in state(log_in("anne"))["changes"]] == ["Prosjekt A"]

    # Bjørn can't acknowledge Anne's notifications
    assert log_in("bjorn").post(f"/api/changes/{inst['p']['EMNE-A']}/acknowledge").status_code == 403


def test_new_engineer_takes_over_notifications(inst):
    log_in("ola").patch(f"{G}/{inst['p']['EMNE-B']}", json={"note": "endret 25"})
    anne = log_in("anne")
    r = anne.patch(f"/api/courses/{inst['e']['EMNE-B']}", json={"engineer": inst["u"]["anne"]})
    assert r.get_json()["engineerName"] == "Anne"
    assert [e["project_name"] for e in state(anne)["changes"]] == ["Prosjekt B"]
    # Bjørn no longer sees Ola's change – only a note that he was taken off the course
    assert [(e["action"], e["project_name"], e["to_value"]) for e in state(log_in("bjorn"))["changes"]] == [
        ("engineer_removed", "EMNE-B", "Anne")]


def test_removed_project_shows_for_the_courses_engineer(inst):
    log_in("kari").delete(f"{G}/{inst['p']['EMNE-D']}")
    assert [s["name"] for s in state(log_in("cecilie"))["deletedRuns"]] == ["Prosjekt D"]
    assert state(log_in("anne"))["deletedRuns"] == []


def test_copy_only_own_courses(inst):
    r = log_in("bjorn").post(f"/api/school-years/{SY}/copy").get_json()
    next_ = {g["name"] for g in arshjul.store.runs() if g["school_year"] == SY + 1}
    assert len(r["created"]) == 2 and next_ == {"Prosjekt B", "Prosjekt uten emne"}
    # asks for other people's projects: skipped
    r = log_in("bjorn").post(f"/api/school-years/{SY}/copy", json={"ids": [inst["p"]["EMNE-A"]]}).get_json()
    assert r["created"] == []


def test_courses_are_added_and_changed(inst):
    anne = log_in("anne")
    r = anne.post("/api/courses", json={"code": " iels2001 ", "name": "Elektronikk 2", "program": "master",
                                       "lecturer": inst["u"]["kari"], "engineer": inst["u"]["bjorn"]})
    e = r.get_json()
    assert (e["code"], e["program"], e["lecturerName"], e["engineerName"], e["active"]) == ("IELS2001", "master", "Kari", "Bjørn", 1)
    assert anne.post("/api/courses", json={"code": "IELS2001"}).status_code == 400  # finnes
    assert anne.post("/api/courses", json={"code": ""}).status_code == 400
    assert anne.post("/api/courses", json={"code": "X1", "program": "phd"}).status_code == 400
    assert anne.post("/api/courses", json={"code": "X2", "lecturer": inst["u"]["anne"]}).status_code == 400  # not a lecturer
    assert anne.post("/api/courses", json={"code": "X3", "engineer": inst["u"]["ola"]}).status_code == 400  # not an engineer
    assert anne.patch(f"/api/courses/{e['id']}", json={"code": "EMNE-A"}).status_code == 400  # kode tatt
    assert anne.patch("/api/courses/9999", json={"name": "x"}).status_code == 404


def test_deactivated_course(inst):
    anne = log_in("anne")
    anne.patch(f"/api/courses/{inst['e']['EMNE-A']}", json={"active": False})
    ola = log_in("ola")
    assert [(e["code"], e["active"]) for e in state(ola)["courses"]] == [("EMNE-B", 1), ("EMNE-A", 0)]  # can switch it back on
    r = ola.post(G, json={"name": "Nytt", "school_year": SY, "course": inst["e"]["EMNE-A"]})
    assert r.status_code == 403
    # the projects in the course still exist, and the history stays
    assert "Prosjekt A" in own_runs(ola)


def test_page_for_engineer_with_others_courses(inst):
    from test_page import run_page
    anne = log_in("anne")
    res = run_page(state(anne))
    assert res["errors"] == []
    assert "Prosjekt A" in res["tbody"] and "Prosjekt C" in res["tbody"] and "Prosjekt uten emne" in res["tbody"]
    assert "Prosjekt B" not in res["tbody"] and "Prosjekt D" not in res["tbody"]  # only their own courses
    assert 'data-act="course"' in res["tbody"]

    res = run_page(state(log_in("ola")))  # lecturer A: courses A and B
    assert res["errors"] == []
    assert "Prosjekt A" in res["tbody"] and "Prosjekt B" in res["tbody"]
    assert "Prosjekt C" not in res["tbody"] and "Prosjekt uten emne" not in res["tbody"]


def test_other_peoples_notifications_dont_leak(inst):
    """Notifications about projects you can't see shouldn't be shown either – even if the project exists."""
    log_in("kari").patch(f"{G}/{inst['p']['EMNE-D']}", json={"note": "endret 9"})  # Cecilie's course
    assert state(log_in("anne"))["changes"] == []
    assert len(state(log_in("cecilie"))["changes"]) == 1


def test_someone_elses_bom_is_not_free(inst):
    from helpers import make_bom
    file = make_bom("Bjørns – BOM.xlsx").name
    make_bom("Ledig – BOM.xlsx")
    log_in("bjorn").patch(f"{G}/{inst['p']['EMNE-B']}", json={"bom": file})
    assert state(log_in("anne"))["freeBoms"] == ["Ledig – BOM.xlsx"]


def test_courses_count_everyones_projects(inst):
    anne = state(log_in("anne"))
    assert {e["code"]: e["projects"] for e in anne["courses"]} == {"EMNE-A": 1, "EMNE-B": 1, "EMNE-C": 1, "EMNE-D": 1}


# ---------- the lecturer creates courses and chooses the engineer ----------

def test_lecturer_creates_course_and_chooses_engineer(inst):
    ola = log_in("ola")
    assert [i["name"] for i in state(ola)["engineers"]] == ["Anne", "Bjørn", "Cecilie"]
    r = ola.post("/api/courses", json={"code": "ola2", "name": "Robotikk", "engineer": inst["u"]["cecilie"]})
    e = r.get_json()
    assert (e["code"], e["lecturerName"], e["engineerName"]) == ("OLA2", "Ola", "Cecilie")
    # can't create courses for other lecturers
    assert ola.post("/api/courses", json={"code": "KARIS", "lecturer": inst["u"]["kari"]}).status_code == 403

    # Cecilie is notified
    cec = log_in("cecilie")
    v = [x for x in state(cec)["changes"] if x["recipient_id"]]
    assert [(x["action"], x["user_name"], x["project_name"]) for x in v] == [("engineer_assigned", "Ola", "OLA2")]
    assert state(log_in("anne"))["changes"] == []

    # Ola creates a project in the course – it ends up with Cecilie
    g = ola.post(G, json={"name": "Robot", "school_year": SY, "course": e["id"]}).get_json()
    assert g["engineerName"] == "Cecilie"
    assert "Robot" in own_runs(cec)

    # Cecilie acknowledges; Anne can't acknowledge for her
    assert log_in("anne").post("/api/changes/acknowledge", json={"ids": [v[0]["id"]]}).get_json() == {"acknowledged": 0}
    assert cec.post("/api/changes/acknowledge", json={"ids": [v[0]["id"]]}).get_json() == {"acknowledged": 1}
    assert [x for x in state(cec)["changes"] if x["recipient_id"]] == []


def test_lecturer_changes_engineer_on_own_course(inst):
    ola = log_in("ola")
    r = ola.patch(f"/api/courses/{inst['e']['EMNE-A']}", json={"engineer": inst["u"]["bjorn"]})
    assert r.get_json()["engineerName"] == "Bjørn"
    assert [x["action"] for x in state(log_in("bjorn"))["changes"] if x["recipient_id"]] == ["engineer_assigned"]
    assert [x["action"] for x in state(log_in("anne"))["changes"] if x["recipient_id"]] == ["engineer_removed"]
    assert "Prosjekt A" not in own_runs(log_in("anne")) and "Prosjekt A" in own_runs(log_in("bjorn"))


def test_lecturer_cant_change_others_courses_or_the_lecturer(inst):
    ola = log_in("ola")
    assert ola.patch(f"/api/courses/{inst['e']['EMNE-C']}", json={"engineer": inst["u"]["bjorn"]}).status_code == 403
    assert ola.patch(f"/api/courses/{inst['e']['EMNE-A']}", json={"lecturer": inst["u"]["kari"]}).status_code == 403
    assert ola.patch(f"/api/courses/{inst['e']['EMNE-A']}", json={"engineer": inst["u"]["kari"]}).status_code == 400  # not an engineer


def test_no_notification_when_you_choose_yourself(inst):
    anne = log_in("anne")
    anne.post("/api/courses", json={"code": "ANNE1", "engineer": inst["u"]["anne"]})
    assert state(anne)["changes"] == []


def test_page_for_lecturer_with_courses(inst):
    from test_page import run_page
    ola = log_in("ola")
    ola.post("/api/courses", json={"code": "OLA2", "engineer": inst["u"]["cecilie"]})
    res = run_page(state(ola))
    assert res["errors"] == []
    res = run_page(state(log_in("cecilie")))
    assert res["errors"] == []
    assert "Ola valgte deg som ansvarlig ingeniør for OLA2" in res["notices"] and "ackid" in res["notices"]


def test_lecturer_sets_students_in_the_course(inst):
    ola = log_in("ola")
    assert ola.patch(f"/api/courses/{inst['e']['EMNE-A']}", json={"students": 48}).get_json()["students"] == 48
    p = ola.post(G, json={"name": "Lodding", "school_year": SY, "group_size": 4, "course": inst["e"]["EMNE-A"]}).get_json()
    assert (p["students"], p["sets"]) == (48, 12)
    assert state(ola)["projects"][0]["courseStudents"] == 48  # the lecturer sees the course's number too
    assert log_in("kari").patch(f"/api/courses/{inst['e']['EMNE-A']}", json={"students": 1}).status_code == 403


# ---------- proposed courses: the admin approves them ----------

@pytest.fixture
def admin(inst):
    arshjul.store.new_user("sjef", "Sjefen", "engineer", auth.hash_password(PASSWORD), admin=True)
    return log_in("sjef")


def codes(c):
    return {e["code"]: e["approved"] for e in state(c)["courses"]}


def test_proposed_course_waits_for_the_admin(inst, admin):
    ola = log_in("ola")
    e = ola.post("/api/courses", json={"code": "OLA3", "engineer": inst["u"]["cecilie"]}).get_json()
    assert (e["approved"], e["proposedByName"]) == (0, "Ola")
    # the proposer can use it straight away, and so can the course's engineer
    g = ola.post(G, json={"name": "Robot", "school_year": SY, "course": e["id"]})
    assert g.status_code == 200 and "Robot" in own_runs(log_in("cecilie"))
    assert codes(log_in("cecilie"))["OLA3"] == 0
    # nobody else sees it, uses it or changes it
    bjorn = log_in("bjorn")
    assert "OLA3" not in codes(bjorn)
    r = bjorn.patch(f"{G}/{inst['p']['EMNE-B']}", json={"course": e["id"]})
    assert r.status_code == 400 and r.get_json()["error"] == "Fant ikke emnet."
    assert bjorn.patch(f"/api/courses/{e['id']}", json={"name": "x"}).status_code == 404
    # the admin sees it in the overview, and approves it
    assert [x["code"] for x in admin.get("/api/admin/data").get_json()["overview"]["coursesPending"]] == ["OLA3"]
    assert bjorn.post(f"/api/admin/courses/{e['id']}/approve").status_code == 403
    assert admin.post(f"/api/admin/courses/{e['id']}/approve").get_json()["approved"] == 1
    assert codes(bjorn)["OLA3"] == 1
    assert admin.post(f"/api/admin/courses/{e['id']}/approve").status_code == 400  # already approved
    # Ola is told – a lecturer gets notifications addressed to them too
    v = state(ola)["changes"]
    assert [(x["action"], x["project_name"], x["user_name"]) for x in v] == [("course_approved", "OLA3", "Sjefen")]
    assert ola.post("/api/changes/acknowledge", json={"ids": [v[0]["id"]]}).get_json() == {"acknowledged": 1}


def test_rejected_course_is_deleted(inst, admin):
    anne = log_in("anne")
    e = anne.post("/api/courses", json={"code": "ANNE9", "engineer": inst["u"]["anne"]}).get_json()
    anne.patch(f"{G}/{inst['p']['uten']}", json={"course": e["id"]})
    assert admin.delete(f"/api/admin/courses/{e['id']}").get_json() == {"ok": True, "projects": 1}
    assert "ANNE9" not in codes(anne)
    assert arshjul.store.run(inst["p"]["uten"])["courseId"] is None  # the project is left without a course
    assert [(x["action"], x["to_value"]) for x in state(anne)["changes"]] == [("course_rejected", "1")]
    # an approved course is made inactive, not rejected
    assert admin.delete(f"/api/admin/courses/{inst['e']['EMNE-A']}").status_code == 400


def test_the_admins_courses_need_no_approval(inst, admin):
    e = admin.post("/api/courses", json={"code": "SJEF1"}).get_json()
    assert (e["approved"], e["proposedBy"]) == (1, None)
    assert codes(log_in("bjorn"))["SJEF1"] == 1
    # «approved» can't be set by anyone through the normal API
    p = log_in("anne").post("/api/courses", json={"code": "LURT", "approved": 1}).get_json()
    assert p["approved"] == 0
    assert admin.patch(f"/api/courses/{p['id']}", json={"approved": 1}).get_json()["approved"] == 0


def test_page_shows_proposals_and_answers(inst, admin):
    from test_page import run_page
    ola = log_in("ola")
    e = ola.post("/api/courses", json={"code": "OLA3"}).get_json()
    res = run_page(state(ola))
    assert res["errors"] == []
    assert "venter på godkjenning" in res["courses"]
    admin.post(f"/api/admin/courses/{e['id']}/approve")
    res = run_page(state(ola))
    assert res["errors"] == []
    assert "Emnet OLA3 er godkjent" in res["notices"] and "ackid" in res["notices"]
