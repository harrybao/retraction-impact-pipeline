"""The contact address every API and plain-HTTP request carries (2026-10-13).

config.POLITE_CONTACT was a fixed placeholder group address that nobody could
change, and twelve scripts wrote the student's own university address into
their User-Agent instead. Now one variable, RWPIPE_CONTACT, sets the address
for every request, with the student's address as the default, and R reads the
same variable (R/python.R, polite_contact()).

config is read when it is imported, so each case runs in a fresh interpreter.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = "xbao0816@uni.sydney.edu.au"

# Every script that sends a contact address, and the name it keeps it under.
SCRIPTS = {
    "backfill_hosts": "UA", "collect_elsevier": "UA", "collect_plain_http": "UA",
    "collect_pmc": "UA", "collect_remainder": "UA", "probe_aggregators": "UA",
    "probe_alt_paths": "UA", "probe_asm": "UA", "probe_oa_hosts": "UA",
    "probe_plain_http": "UA", "probe_remainder": "UA", "collect_wayback": "HEADERS",
    "collect_wikipedia": "HEADERS", "collect_oa_locations": "HEADERS",
    "collect_altmetric_detail": "HEADERS", "collect_altmetric_notice_detail": "HEADERS",
    "probe_campus": "UA",
}

PROBE = r"""
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "scripts"))
from rwpipe import config, apis, altmetric, engagement, plumx
out = {"config": config.POLITE_CONTACT,
       "apis": apis.HEADERS["User-Agent"],
       "altmetric": altmetric.HEADERS["User-Agent"],
       "engagement": engagement.HEADERS["User-Agent"],
       "plumx": plumx.headers("test-only-key")["User-Agent"]}
for name, attr in json.loads(sys.argv[2]).items():
    spec = importlib.util.spec_from_file_location(name, root / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out[name] = json.dumps(getattr(mod, attr))
    if name == "collect_pmc":
        out["collect_pmc.EMAIL"] = mod.EMAIL
print(json.dumps(out))
"""


def contacts(tmp_path: Path, contact: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "RWPIPE_CONTACT"}
    env.update(RWPIPE_DATA=str(tmp_path / "data"), PYTHONIOENCODING="utf-8")
    if contact is not None:
        env["RWPIPE_CONTACT"] = contact
    r = subprocess.run([sys.executable, "-c", PROBE, str(ROOT), json.dumps(SCRIPTS)],
                       env=env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_default_is_the_students_address(tmp_path):
    seen = contacts(tmp_path, None)
    assert seen["config"] == DEFAULT
    assert all(DEFAULT in v for v in seen.values())


def test_a_blank_variable_is_no_address(tmp_path):
    assert contacts(tmp_path, "  ")["config"] == DEFAULT


def test_the_variable_reaches_every_request(tmp_path):
    seen = contacts(tmp_path, " client@example.org ")
    assert seen["config"] == "client@example.org"
    assert seen["collect_pmc.EMAIL"] == "client@example.org"   # NCBI's email parameter
    for where, value in seen.items():
        assert "client@example.org" in value, where
        assert DEFAULT not in value, where


@pytest.mark.parametrize("space", ["\v", "\f", "\x1c", "\u00a0", "\u2003", "\u3000"])
def test_unicode_and_control_whitespace_uses_the_same_trim_and_blank_fallback(tmp_path, space):
    blank = contacts(tmp_path, space)
    padded = contacts(tmp_path, space + "client@example.org" + space)
    assert all(DEFAULT in value for value in blank.values())
    assert all("client@example.org" in value and DEFAULT not in value for value in padded.values())


def test_no_script_or_module_writes_its_own_address():
    """An address typed into one file is one RWPIPE_CONTACT does not reach."""
    pattern = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    found = []
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and path.name != "config.py":
                found.append(f"{path.relative_to(ROOT)}:{n}")
    assert found == []


@pytest.mark.parametrize("address", ["no-at-sign", "a@@example.org", "a@", "@example.org",
                                     "a\nb@example.org", "a\x00@example.org", "a b@example.org",
                                     "a@bad..org", "a@-bad.org", "\u00e9@example.org"])
def test_invalid_contact_fails_near_configuration_not_the_request(address):
    from rwpipe.config import contact_address
    with pytest.raises(ValueError, match="RWPIPE_CONTACT"):
        contact_address(address)
