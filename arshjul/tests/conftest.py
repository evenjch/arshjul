"""Testene kjører mot en egen, midlertidig datamappe – aldri mot de ekte BOM-ene."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# må settes før config/app importeres
_DATA = Path(tempfile.mkdtemp(prefix="arshjul-test-"))
# satt eksplisitt, så en lokal innstillinger.env aldri får testene til å røre ekte mapper
os.environ["ARSHJUL_DATA"] = str(_DATA)
os.environ["ARSHJUL_BOM"] = str(_DATA / "BOM")
os.environ["ARSHJUL_NO_OPEN"] = "1"
os.environ["ARSHJUL_MODUS"] = "lokal"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as arshjul  # noqa: E402
import auth  # noqa: E402
import config  # noqa: E402

# scrypt er bevisst tregt; testene lager mange brukere
auth.HASH_METODE = "pbkdf2:sha256:1000"
ADMIN_PASSORD = "admin-passord-123"


@pytest.fixture(autouse=True)
def tom_datamappe():
    """Hver test starter med en tom datamappe."""
    for p in _DATA.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    arshjul._bom_cache.clear()
    arshjul.init()
    arshjul._ready = True
    auth._ip_feil.clear()
    yield


@pytest.fixture
def client():
    """Testklient innlogget som administrator (ser og gjør alt), med gyldig CSRF-token."""
    arshjul.app.config["TESTING"] = True
    uid = arshjul.store.ny_bruker("admin", "Admin Testesen", "ingenior", auth.hash_passord(ADMIN_PASSORD), admin=True)
    with arshjul.app.test_client() as c:
        som(c, uid, adminmodus=True)
        yield c


def som(c, uid, token="test-csrf", adminmodus=False):
    """Logg testklienten inn som brukeren uid (uten å gå via innloggingssiden)."""
    u = arshjul.store.bruker(id=uid)
    with c.session_transaction() as s:
        s["uid"], s["pv"], s["csrf"] = uid, u["passord"][-16:], token
        s["adminmodus"] = adminmodus
    c.environ_base["HTTP_X_CSRF_TOKEN"] = token
    return c


def med_csrf(c, token="test-csrf"):
    with c.session_transaction() as s:
        s["csrf"] = token
    c.environ_base["HTTP_X_CSRF_TOKEN"] = token
    return c
