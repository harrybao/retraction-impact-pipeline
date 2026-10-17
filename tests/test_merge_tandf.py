"""The Taylor & Francis merge's identity checks, and the mistakes they encode.

Each case is a failure already met in this project: doi.org error pages that
contain the DOI they could not resolve (SAGE hand-off, 57 files), CAPTCHA
scripts read as challenges (15,245 Wiley articles, 7 of 10 OUP articles), CDN
error pages vouched for by the DOI in their URL (9 liebertonline captures), and
DOIs that cannot be recovered from a flattened filename.

The fixtures are real MHTML: a MIME envelope with a quoted-printable HTML part,
decoded by the same reader the pipeline uses.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "merge_tandf",
    Path(__file__).resolve().parents[1] / "scripts" / "merge_tandf.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

DOI = "10.1080/13642529.2014.915496"
ARTICLE_URL = f"https://www.tandfonline.com/doi/full/{DOI}"


def mhtml_file(tmp_path: Path, url: str, title: str, body: str, name: str = "page.mhtml") -> Path:
    html = (f"<html><head><title>{title}</title>"
            '<script src=3D"https://www.google.com/recaptcha/api.js"></script>'
            f"</head><body>{body}</body></html>")
    text = ("From: <Saved by Blink>\r\n"
            f"Snapshot-Content-Location: {url}\r\n"
            f"Subject: {title}\r\n"
            "MIME-Version: 1.0\r\n"
            'Content-Type: multipart/related; type="text/html"; boundary="----B"\r\n'
            "\r\n"
            "------B\r\n"
            "Content-Type: text/html\r\n"
            "Content-Transfer-Encoding: quoted-printable\r\n"
            f"Content-Location: {url}\r\n"
            "\r\n"
            f"{html}\r\n"
            "------B--\r\n")
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_an_article_with_a_captcha_script_is_accepted(tmp_path):
    p = mhtml_file(tmp_path, ARTICLE_URL, "Full article: Crystal defects in olivine",
                   f"<p>https://doi.org/{DOI}</p><p>Abstract text.</p>")
    assert _mod.judge(p, DOI)[0] == "ok"


def test_a_challenge_title_is_refused(tmp_path):
    p = mhtml_file(tmp_path, ARTICLE_URL, "Just a moment...", f"<p>{DOI}</p>")
    assert _mod.judge(p, DOI)[0] == "refusal-title"


def test_a_doi_org_error_page_is_refused_even_though_it_contains_the_doi(tmp_path):
    p = mhtml_file(tmp_path, f"https://doi.org/{DOI}", "Error: DOI Not Found",
                   f"<p>Error: DOI Not Found</p><p>{DOI}</p>")
    assert _mod.judge(p, DOI)[0] == "never-left-doi.org"


def test_a_page_for_another_doi_is_refused(tmp_path):
    other = "https://www.tandfonline.com/doi/full/10.1080/00207540902865746"
    p = mhtml_file(tmp_path, other, "Full article: Something else", f"<p>{DOI}</p>")
    assert _mod.judge(p, DOI)[0] == "url-is-a-different-doi"


def test_a_cdn_error_page_is_refused_even_with_the_doi(tmp_path):
    p = mhtml_file(tmp_path, ARTICLE_URL, "Please Return Soon",
                   f"<p>Origin is unreachable. Error reference number: 523</p><p>{DOI}</p>")
    assert _mod.judge(p, DOI)[0] == "cdn-error-page"


def test_an_article_title_that_says_not_found_is_not_a_refusal(tmp_path):
    p = mhtml_file(tmp_path, ARTICLE_URL,
                   "Full article: Species not found in the 2012 survey of the Murray basin",
                   f"<p>{DOI}</p>")
    assert _mod.judge(p, DOI)[0] == "ok"


def test_the_doi_comes_from_the_log_not_the_filename(tmp_path):
    log = tmp_path / "log.csv"
    log.write_text(
        "timestamp,doi,status,file,size,url\n"
        "2026-09-10 00:12:36,10.1080/1234_5678,ok,"
        "E:\\OneDrive\\cs43_tandf\\mhtml\\10.1080_1234_5678.mhtml,1500000,"
        "https://www.tandfonline.com/doi/full/10.1080/1234_5678\n",
        encoding="utf-8")
    got = {r["name"]: r for r in _mod.load_log(log)}
    assert got["10.1080_1234_5678.mhtml"]["doi"] == "10.1080/1234_5678"


def test_a_log_row_with_no_file_is_kept(tmp_path):
    """The 67 pages that answered 404 have no file. Keyed on the file name, the
    first version dropped all of them from the report silently."""
    log = tmp_path / "log.csv"
    log.write_text(
        "timestamp,doi,status,file,size,url\n"
        "2026-09-10 00:00:00,10.1080/00000000.2020.0000000,http404,,,"
        "https://www.tandfonline.com/doi/full/10.1080/00000000.2020.0000000\n",
        encoding="utf-8")
    rows = _mod.load_log(log)
    assert len(rows) == 1
    assert rows[0]["status"] == "http404" and rows[0]["name"] == ""


def test_a_file_written_through_windows_text_mode_is_repaired(tmp_path):
    """The hand-off's collector wrote Chrome's CRLF text in Windows text mode, so
    every line ends CR CR LF. The MIME parser then takes the first CR as the end
    of the headers and finds no parts: before the repair, every one of the 2,799
    files read as "no text/html part"."""
    good = mhtml_file(tmp_path, ARTICLE_URL, "Full article: Crystal defects in olivine",
                      f"<p>https://doi.org/{DOI}</p><p>Abstract text.</p>")
    bad = tmp_path / "text-mode.mhtml"
    bad.write_bytes(good.read_bytes().replace(b"\r\n", b"\r\r\n"))
    assert _mod.mhtml.read(bad).error == "no text/html part"
    assert _mod.judge(bad, DOI)[0] == "ok"


def test_the_repair_is_exact_and_touches_nothing_else(tmp_path):
    good = mhtml_file(tmp_path, ARTICLE_URL, "Full article: x", f"<p>{DOI}</p>").read_bytes()
    damaged = good.replace(b"\r\n", b"\r\r\n")
    assert _mod.undo_text_mode(damaged) == (good, True)
    assert _mod.undo_text_mode(good) == (good, False)
    # Both endings in one file is not the text-mode pattern: left as it is.
    mixed = damaged + b"\r\n"
    assert _mod.undo_text_mode(mixed) == (mixed, False)
