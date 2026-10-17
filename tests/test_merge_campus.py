"""The campus merge's identity checks, and the mistake they were written against.

Matched against raw HTML, a challenge pattern flagged 7 of 10 sampled OUP
articles, because OUP embeds a CAPTCHA script for its login form. The merge
therefore judges challenges by the page title; these tests keep it that way.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "merge_campus",
    Path(__file__).resolve().parents[1] / "scripts" / "merge_campus.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

DOI = "10.1093/nar/gkab123"


def page(title: str, body: str = "") -> str:
    return (f"<html><head><title>{title}</title>"
            '<script src="https://www.google.com/recaptcha/api.js"></script>'
            f"</head><body><p>https://doi.org/{DOI}</p>{body}</body></html>")


def test_a_captcha_script_in_the_page_is_not_a_challenge():
    verdict, _ = _mod.judge(page("Retracted: Structure of X | Nucleic Acids "
                                 "Research | Oxford Academic"), DOI)
    assert verdict == "ok"


def test_a_challenge_title_is_refused():
    assert _mod.judge(page("Just a moment..."), DOI)[0] == "refusal-title"


def test_a_cdn_error_page_is_refused_even_with_the_doi():
    body = "Origin is unreachable. Error reference number: 523"
    assert _mod.judge(page("Please Return Soon", body), DOI)[0] == "cdn-error-page"


def test_a_page_without_its_doi_is_refused():
    html = "<html><head><title>Some article</title></head><body>no id</body></html>"
    assert _mod.judge(html, DOI)[0] == "no-doi-in-file"


def test_the_doi_comes_from_the_log_not_the_filename(tmp_path):
    # The filename flattens both "/" and ":" to "_", which cannot be undone.
    logs = tmp_path / "campus-logs"
    logs.mkdir()
    (logs / "academic.oup.com.tsv").write_text(
        "doi\thost\tcode\tbytes\tkind\tfile\n"
        "10.1093/jn/nxab_01:x\tacademic.oup.com\t200\t150000\thtml\t10.1093_jn_nxab_01_x.html\n",
        encoding="utf-8")
    got = _mod.load_logs(tmp_path)
    assert got["10.1093_jn_nxab_01_x.html"][0] == "10.1093/jn/nxab_01:x"
