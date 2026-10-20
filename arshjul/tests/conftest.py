"""The tests run against their own temporary data folder – never against the real BOMs."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# must be set before config/app are imported
_DATA = Path(tempfile.mkdtemp(prefix="arshjul-test-"))
# set explicitly, so a local innstillinger.env never makes the tests touch real folders
os.environ["ARSHJUL_DATA"] = str(_DATA)
os.environ["ARSHJUL_BOM"] = str(_DATA / "BOM")
os.environ["ARSHJUL_NO_OPEN"] = "1"
os.environ["ARSHJUL_MODUS"] = "lokal"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as arshjul  # noqa: E402
import auth  # noqa: E402
import config  # noqa: E402

# scrypt is slow on purpose; the tests create many users
auth.HASH_METHOD = "pbkdf2:sha256:1000"
ADMIN_PASSWORD = "admin-passord-123"


@pytest.fixture(autouse=True)
def empty_data_dir():
    """Every test starts with an empty data folder."""
    for p in _DATA.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    arshjul._bom_cache.clear()
    arshjul.init()
    arshjul._ready = True
    auth._ip_failures.clear()
    yield


@pytest.fixture
def client():
    """A test client logged in as admin (sees and does everything), with a valid CSRF token."""
    arshjul.app.config["TESTING"] = True
    uid = arshjul.store.new_user("admin", "Admin Testesen", "engineer", auth.hash_password(ADMIN_PASSWORD), admin=True)
    with arshjul.app.test_client() as c:
        as_user(c, uid)
        yield c


def as_user(c, uid, token="test-csrf"):
    """Log the test client in as user uid (without going through the login page)."""
    u = arshjul.store.user(id=uid)
    with c.session_transaction() as s:
        s["uid"], s["pv"], s["csrf"] = uid, u["password"][-16:], token
    c.environ_base["HTTP_X_CSRF_TOKEN"] = token
    return c


def with_csrf(c, token="test-csrf"):
    with c.session_transaction() as s:
        s["csrf"] = token
    c.environ_base["HTTP_X_CSRF_TOKEN"] = token
    return c
