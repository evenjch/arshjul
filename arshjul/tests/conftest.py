"""Testene kjører mot en egen, midlertidig datamappe – aldri mot de ekte BOM-ene."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# må settes før config/app importeres
_DATA = Path(tempfile.mkdtemp(prefix="arshjul-test-"))
os.environ["ARSHJUL_DATA"] = str(_DATA)
os.environ["ARSHJUL_NO_OPEN"] = "1"
os.environ.pop("ARSHJUL_MODUS", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as arshjul  # noqa: E402
import config  # noqa: E402


@pytest.fixture(autouse=True)
def tom_datamappe():
    """Hver test starter med en tom datamappe."""
    for p in _DATA.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    arshjul._bom_cache.clear()
    arshjul.init()
    arshjul._ready = True
    yield


@pytest.fixture
def client():
    arshjul.app.config["TESTING"] = True
    with arshjul.app.test_client() as c:
        yield c
