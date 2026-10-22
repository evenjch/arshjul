"""The stylesheet only styles what exists: every class and id in arshjul.css is used by a template or the JS.

Catches selectors left behind when markup is renamed (the move to English left .nabo, .status.feil,
table.tell and others styling nothing). The check is loose – the name only has to occur as a word somewhere –
so it never fails on classes built with ${…}, but it does fail on a name that is gone.
"""
import re

import config


def _css_names():
    css = (config.APP_DIR / "static" / "arshjul.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = " ".join(re.findall(r"([^{}]+)\{", css))
    selectors = re.sub(r"\[[^\]]*\]", "", selectors)  # attribute selectors aren't classes
    selectors = re.sub(r":[\w-]+(\([^)]*\))?", "", selectors)  # nor pseudo-classes
    classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", selectors))
    ids = {i for i in re.findall(r"#([a-zA-Z][\w-]*)", selectors) if not re.fullmatch(r"[0-9a-fA-F]{3,8}", i)}
    return classes, ids


def test_every_css_class_and_id_is_used():
    files = [*(config.APP_DIR / "templates").glob("*.html"), *(config.APP_DIR / "static").glob("*.js")]
    words = set(re.findall(r"[\w-]+", "\n".join(f.read_text(encoding="utf-8") for f in files)))
    classes, ids = _css_names()
    assert sorted(c for c in classes if c not in words) == []
    assert sorted(i for i in ids if i not in words) == []
