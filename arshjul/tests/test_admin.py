"""Always logged in; admin and users on the web page."""
import pytest

import app as arshjul
import auth
import config
from conftest import ADMIN_PASSWORD, som

PASSWORD = "et-langt-passord"


def new_client():
    arshjul.app.config["TESTING"] = True
    return arshjul.app.test_client()


def user(name, role="engineer", admin=False):
    return arshjul.store.new_user(name, name.title(), role, auth.hash_password(PASSWORD), admin=admin)


# ---------- first time ----------

def setup(c, **fields):
    c.get("/oppsett")
    with c.session_transaction() as s:
        token = s.get("csrf", "ingen")  # if there are users, you're sent on without the form
    data = {"username": "even", "name": "Even", "password": "mitt-gode-passord", "password2": "mitt-gode-passord",
            "csrf": token, **fields}
    return c.post("/oppsett", data=data)


def test_first_time_locally_creates_the_admin():
    c = new_client()
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/oppsett")
    assert c.get("/login").headers["Location"].endswith("/oppsett")
    assert setup(c, password2="noe-annet-her").status_code == 400
    assert setup(c, password="kort", password2="kort").status_code == 400
    r = setup(c)
    assert r.status_code == 302 and r.headers["Location"] == "/admin#brukere"  # allowed to create users
    with c.session_transaction() as s:
        c.environ_base["HTTP_X_CSRF_TOKEN"] = s["csrf"]
    st = c.get("/api/state").get_json()
    assert st["me"]["name"] == "Even" and st["me"]["admin"] is True and st["me"]["role"] == "engineer"
    # only once: now there is a user
    assert new_client().get("/oppsett").headers["Location"].endswith("/login")
    assert setup(new_client(), username="inntrenger").status_code == 302  # sent to login
    assert arshjul.store.user(username="inntrenger") is None


def test_first_time_on_server_only_with_manage(monkeypatch):
    monkeypatch.setattr(config, "LOCAL", False)
    c = new_client()
    assert c.get("/").headers["Location"].startswith("/login")
    assert "manage.py ny-bruker" in c.get("/login").get_data(as_text=True)
    setup(c)
    assert arshjul.store.user_count() == 0


def test_no_anonymous_access_locally():
    user("eng")
    c = new_client()
    assert c.get("/api/state").status_code == 401
    assert c.get("/").headers["Location"].startswith("/login")
    # users from before there was an admin: the page explains how to become admin
    assert "manage.py admin" in c.get("/login").get_data(as_text=True)


def test_secret_key_is_stored_locally():
    # created once and reused, so you stay logged in after a restart
    assert len(config.SECRET_KEY) >= 32 and config.SESSION_HOURS == 24 * 30


# ---------- administrator ----------

def test_admin_is_a_regular_engineer_on_the_main_page(client):
    """On the main page the admin has exactly a regular engineer's rights – the panel is the place for everything."""
    bjorn = user("bjorn")
    e = arshjul.store.new_course({"code": "E1", "engineer_id": bjorn})["id"]
    g = som(new_client(), bjorn).post("/api/runs", json={"name": "Bjørns", "school_year": 2030, "course": e}).get_json()
    assert client.get("/api/state").get_json()["projects"] == []  # not the admin's course
    assert client.patch(f"/api/runs/{g['id']}", json={"start": 40}).status_code == 403
    # but in the panel the admin sees and fixes everything
    assert [r["name"] for r in client.get("/api/admin/data").get_json()["runs"]] == ["Bjørns"]
    assert client.patch(f"/api/admin/runs/{g['id']}", json={"start": 40}).status_code == 200


def test_admin_creates_and_changes_users(client):
    r = client.post("/api/users", json={"username": "ola", "name": "Ola", "role": "lecturer", "password": PASSWORD,
                                          "admin": True})
    u = r.get_json()
    assert (u["role"], u["admin"], u["active"]) == ("lecturer", False, True)  # a lecturer can't be admin
    assert "password" not in u
    assert client.post("/api/users", json={"username": "OLA", "name": "X", "role": "lecturer", "password": PASSWORD}).status_code == 400
    assert client.post("/api/users", json={"username": "kort", "name": "X", "role": "lecturer", "password": "kort"}).status_code == 400
    assert client.post("/api/users", json={"username": "a b", "name": "X", "role": "lecturer", "password": PASSWORD}).status_code == 400
    assert client.post("/api/users", json={"username": "x", "name": "X", "role": "sjef", "password": PASSWORD}).status_code == 400

    assert client.patch(f"/api/users/{u['id']}", json={"name": "Ola Nordmann"}).get_json()["name"] == "Ola Nordmann"
    users = client.get("/api/admin/data").get_json()["users"]
    assert {b["username"] for b in users} == {"admin", "ola"} and all("password" not in b for b in users)

    # a new engineer as admin
    r = client.post("/api/users", json={"username": "kari", "name": "Kari", "role": "engineer", "password": PASSWORD, "admin": True})
    assert r.get_json()["admin"] is True


def test_new_password_and_deactivation_end_the_session(client):
    uid = user("anne")
    a = som(new_client(), uid)
    assert a.get("/api/state").status_code == 200
    client.patch(f"/api/users/{uid}", json={"password": "et-helt-nytt-passord"})
    assert a.get("/api/state").status_code == 401
    a = som(new_client(), uid)
    client.patch(f"/api/users/{uid}", json={"active": False})
    assert a.get("/api/state").status_code == 401


def test_activation_and_new_password_unlock(client):
    uid = user("anne")
    arshjul.store.update_user(uid, locked_until="2999-01-01T00:00:00", failed_attempts=3)
    client.patch(f"/api/users/{uid}", json={"active": True})
    u = arshjul.store.user(id=uid)
    assert (u["locked_until"], u["failed_attempts"]) == (None, 0)


def test_there_is_always_an_admin(client):
    admin = arshjul.store.user(username="admin")["id"]
    for change in ({"admin": False}, {"active": False}, {"role": "lecturer"}):
        r = client.patch(f"/api/users/{admin}", json=change)
        assert r.status_code == 400 and "minst én aktiv administrator" in r.get_json()["error"]
    user("kari", admin=True)
    assert client.patch(f"/api/users/{admin}", json={"admin": False}).status_code == 200


def test_role_cant_change_while_the_user_is_on_courses(client):
    uid = user("anne")
    arshjul.store.new_course({"code": "E1", "engineer_id": uid})
    r = client.patch(f"/api/users/{uid}", json={"role": "lecturer"})
    assert r.status_code == 400 and "E1" in r.get_json()["error"]


@pytest.mark.parametrize("role", ["engineer", "lecturer"])
def test_only_the_admin_manages_users(client, role):
    c = som(new_client(), user("vanlig", role))
    assert c.post("/api/users", json={"username": "x", "name": "X", "role": "lecturer", "password": PASSWORD}).status_code == 403
    assert c.patch("/api/users/1", json={"admin": True}).status_code == 403
    assert c.get("/api/admin/data").status_code == 403


def test_manage_admin(capsys, monkeypatch):
    import manage
    monkeypatch.setenv("ARSHJUL_NYTT_PASSORD", ADMIN_PASSWORD)
    manage.main(["ny-bruker", "sjef", "Sjefen", "ingenior", "--admin"])
    assert arshjul.store.user(username="sjef")["admin"] == 1
    with pytest.raises(SystemExit):
        manage.main(["ny-bruker", "ola", "Ola", "faglarer", "--admin"])
    with pytest.raises(SystemExit):
        manage.main(["admin", "sjef", "--fjern"])  # the only one
    manage.main(["ny-bruker", "anne", "Anne", "ingenior"])
    manage.main(["admin", "anne"])
    manage.main(["admin", "sjef", "--fjern"])
    assert arshjul.store.user(username="sjef")["admin"] == 0
    manage.main(["liste"])
    assert "(admin)" in capsys.readouterr().out


def test_page_as_admin(client):
    from test_page import run_page
    user("anne")
    res = run_page(client.get("/api/state").get_json())
    assert res["errors"] == []
