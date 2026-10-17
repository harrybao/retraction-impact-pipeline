"""The Internet Archive merge: a capture reaches `documents` only once checked.

Run as a subprocess against a scratch data directory, as the pipeline runs it,
because `rwpipe.config` fixes the data directory at import.

Two cases the hand-run merge of 3 September got wrong: a notice retracting 107
papers compared with one of their titles picked arbitrarily, and a notice page
whose title is only a label ("Retraction Notice, 2025"), which matches no paper
title at all. And the cases a review on 7 October found the first fix accepting:
an error page or a substituted notice, passed because the Archive's toolbar
carries the requested DOI. `judge()` is tested directly as well.
"""
from __future__ import annotations

import gzip
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import importlib.util  # noqa: E402

from rwpipe import db  # noqa: E402

_spec = importlib.util.spec_from_file_location("merge_wayback",
                                               ROOT / "scripts" / "merge_wayback.py")
mw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mw)

NOTICE = "10.1007/s13277-017-5487-6"
LABELLED = "10.1177/10641246251331509"
PAPER = "10.1038/cmi.2012.76"
WRONG = "10.1080/0000000.2020.1"
ZTITLE = "Cellular immunology of Z in dendritic cells after ultraviolet exposure"


TOOLBAR = ("<!-- BEGIN WAYBACK TOOLBAR INSERT -->{u} {u} {u}"
           "<!-- END WAYBACK TOOLBAR INSERT -->")


def mhtml(doi: str, title: str, own: str | None = None, toolbar_for: str | None = None) -> str:
    """A capture titled `title` whose text cites `doi`; `own` is the DOI its
    metadata declares (none if None); `toolbar_for` adds an Archive toolbar
    carrying that DOI's address, as every real Archive copy has."""
    meta = f'<meta name="citation_doi" content="{own}">' if own else ""
    bar = TOOLBAR.format(u=f"https://journals.example.org/doi/{toolbar_for}") if toolbar_for else ""
    return ("From: <Saved by Blink>\r\nSubject: " + title
            + '\r\nMIME-Version: 1.0\r\nContent-Type: text/html; charset="utf-8"\r\n\r\n'
            f"<html><head><title>{title}</title>{meta}</head><body>{bar}<h1>{title}</h1>"
            f"<p>https://doi.org/{doi}</p>" + "text " * 400 + "</body></html>")


def build(data: Path) -> None:
    (data / "interim").mkdir(parents=True)
    conn = db.connect(data / "interim" / "pipeline.sqlite")
    db.init(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS wayback_captures (
        doi TEXT PRIMARY KEY, snapshot_url TEXT, snapshot_date TEXT,
        status TEXT, path TEXT, bytes INTEGER, fetched_at TEXT)""")
    rid = 0
    # The Springer notice retracts three papers here (107 in life); its page
    # declares its own DOI, as the real one does.
    for t in ("Expression of gene X in tumour cells", "A study of Y in mice",
              "Survivin expression in tumour biopsies"):
        rid += 1
        conn.execute("INSERT INTO records (record_id, title, original_doi, retraction_doi) "
                     "VALUES (?,?,?,?)", (rid, t, f"10.1007/paper{rid}", NOTICE))
    conn.execute("INSERT INTO records (record_id, title, original_doi, retraction_doi) "
                 "VALUES (?,?,?,?)", (10, ZTITLE, None, PAPER))
    conn.execute("INSERT INTO records (record_id, title, original_doi, retraction_doi) "
                 "VALUES (?,?,?,?)", (11, "Quantum chromodynamics at scale", WRONG, None))
    # A SAGE mass notice: its page title is a label, matching none of its papers.
    for i, t in enumerate(("Fuzzy decision making under regret theory",
                           "Neutrosophic factor analysis of variance"), start=20):
        conn.execute("INSERT INTO records (record_id, title, journal, original_doi, "
                     "retraction_doi) VALUES (?,?,?,?,?)",
                     (i, t, "Journal of Intelligent & Fuzzy Systems", f"10.3233/p{i}", LABELLED))
    pages = {NOTICE: mhtml(NOTICE, "Retraction Note to multiple articles in Tumor Biology",
                           own=NOTICE),
             LABELLED: mhtml(LABELLED, "Retraction Notice, 2025", own=LABELLED),
             PAPER: mhtml(PAPER, "Retraction: " + ZTITLE),
             WRONG: mhtml(WRONG, "An entirely different article about cooking")}
    for doi, text in pages.items():
        prefix = doi.split("/")[0]
        conn.execute("INSERT INTO documents (doi, doi_prefix, status, usable) "
                     "VALUES (?,?, 'blocked', 0)", (doi, prefix))
        p = data / "mhtml" / f"{abs(hash(doi))}.mhtml.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wb") as fh:
            fh.write(text.encode("utf-8"))
        conn.execute("INSERT INTO wayback_captures VALUES (?,?,?,?,?,?, datetime('now'))",
                     (doi, f"http://web.archive.org/web/2024/{doi}", "20241125", "ok",
                      str(p.relative_to(data)).replace("\\", "/"), 1))
    conn.commit()
    conn.close()


def run(data: Path, *args: str) -> str:
    env = dict(os.environ, RWPIPE_DATA=str(data), PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "merge_wayback.py"), *args],
                         env=env, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return out.stdout


def state(data: Path) -> dict:
    conn = sqlite3.connect(data / "interim" / "pipeline.sqlite")
    got = {d: (u, sk, fmt, ver) for d, u, sk, fmt, ver in conn.execute(
        "SELECT doi, usable, source_kind, capture_format, oa_version FROM documents")}
    got["captures"] = dict(conn.execute("SELECT doi, status FROM wayback_captures"))
    conn.close()
    return got


def test_dry_run_changes_nothing(tmp_path):
    build(tmp_path)
    path = tmp_path / "interim" / "pipeline.sqlite"
    before = path.read_bytes()
    assert "would merge 3" in run(tmp_path, "--dry-run")
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    s = state(tmp_path)
    assert s[NOTICE][0] == 0 and s["captures"][WRONG] == "ok"


def test_a_recorded_alias_rechecks_only_its_prior_other_doi_rejection(tmp_path):
    build(tmp_path)
    path = tmp_path / "interim" / "pipeline.sqlite"
    conn = sqlite3.connect(path)
    rel = conn.execute("SELECT path FROM wayback_captures WHERE doi=?", (LABELLED,)).fetchone()[0]
    capture = tmp_path / rel
    with gzip.open(capture, "rb") as f:
        raw = f.read()
    alias = "10.1234/reviewed-alias"
    raw = raw.replace(f'content="{LABELLED}"'.encode(), f'content="{alias}"'.encode())
    with gzip.open(capture, "wb") as f:
        f.write(raw)
    conn.close()
    run(tmp_path)
    assert state(tmp_path)[LABELLED][0] == 0
    assert state(tmp_path)["captures"][LABELLED] == "other-doi"
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO doi_corrections(doi,corrected_doi,source,reason) VALUES (?,?,?,?)",
                 (LABELLED, alias, "reviewed publisher evidence", "same document"))
    conn.commit()
    conn.close()
    run(tmp_path)
    s = state(tmp_path)
    assert s[LABELLED][:2] == (1, "wayback")
    assert s[WRONG][0] == 0
    assert s["captures"][LABELLED] == "other-doi"  # historical rejection retained


def test_checked_captures_are_merged_and_the_wrong_one_recorded(tmp_path):
    build(tmp_path)
    run(tmp_path)
    s = state(tmp_path)
    assert s[NOTICE] == (1, "wayback", "html", "snapshot 20241125")   # mass notice kept
    assert s[LABELLED][:2] == (1, "wayback")                           # label title: DOI decides
    assert s[PAPER][:2] == (1, "wayback")
    assert s[WRONG][0] == 0
    assert s["captures"][WRONG] == "wrong-article"
    conn = sqlite3.connect(tmp_path / "interim" / "pipeline.sqlite")
    same = conn.execute("SELECT COUNT(*) FROM documents d JOIN wayback_captures w "
                        "ON w.doi = d.doi WHERE d.usable = 1 AND d.mhtml_path = w.path").fetchone()
    conn.close()
    assert same[0] == 3                                                # no second copy
    assert "nothing to merge" in run(tmp_path)                         # idempotent


def test_judge_a_label_title_is_decided_by_the_pages_own_doi():
    page = mhtml(LABELLED, "Retraction Notice, 2025", own=LABELLED, toolbar_for=LABELLED)
    assert mw.judge(page, LABELLED, ["Fuzzy decision making under regret theory"]) == ("ok", None)


def test_judge_the_doi_in_the_archive_toolbar_is_no_evidence():
    # A login wall with a label title: the requested DOI is all over the
    # toolbar and the text, but the page declares nothing and names no paper.
    page = mhtml(LABELLED, "Sign in", toolbar_for=LABELLED)
    verdict = mw.judge(page, LABELLED, ["Fuzzy decision making under regret theory"])[0]
    assert verdict == "wrong-article"


def test_judge_a_notice_saved_in_place_of_its_article_is_kept_apart():
    # The notice cites the original's DOI in its text and declares its own.
    page = mhtml("10.1002/dvg.22383", "Retraction", own="10.1002/dvg.22394",
                 toolbar_for="10.1002/dvg.22383")
    assert mw.judge(page, "10.1002/dvg.22383", ["A study of gene X"])[0] == "other-doi"


def test_judge_only_the_pages_own_doi_counts_not_one_in_its_body():
    # AME article pages carry their retraction notice's DOI after </head>.
    page = mhtml("10.21037/tp-22-68", "Article title here", own="10.21037/tp-22-68").replace(
        "</head><body>", '</head><body><meta name="dc.identifier" content="doi: 10.21037/tp-22-260">')
    assert mw.judge(page, "10.21037/tp-22-260", ["Retraction note"])[0] == "other-doi"


def test_judge_an_error_page_is_refused_by_its_title():
    page = mhtml(LABELLED, "Error | Sage", toolbar_for=LABELLED).replace(
        "<h1>", "<p>Error! Publication not found: " + LABELLED + "</p><h1>")
    assert mw.judge(page, LABELLED, ["anything"])[0] == "error-page"


def test_judge_a_paper_titled_error_is_not_an_error_page():
    t = "Error separation in on-site measurement of NC machine based on Bayesian network"
    page = mhtml("10.1109/aimsec.2011.6009900", t, toolbar_for="10.1109/aimsec.2011.6009900")
    assert mw.judge(page, "10.1109/aimsec.2011.6009900", [t])[0] == "ok"


def test_judge_without_metadata_an_informative_title_must_match():
    ok = mhtml(PAPER, "Retraction: " + ZTITLE, toolbar_for=PAPER)
    assert mw.judge(ok, PAPER, [ZTITLE])[0] == "ok"
    other = mhtml(WRONG, "An entirely different article about cooking methods", toolbar_for=WRONG)
    verdict, sim = mw.judge(other, WRONG, ["Quantum chromodynamics at scale"])
    assert verdict == "wrong-article" and sim < mw.MIN_TITLE_SIMILARITY


def test_judge_a_mass_notice_without_metadata_is_not_judged_by_title():
    # With hundreds of titles to choose from, some unrelated page resembles
    # one of them; only metadata can identify a mass notice's page.
    page = mhtml(NOTICE, "Survivin expression in tumour biopsies", toolbar_for=NOTICE)
    titles = ["Expression of gene X in tumour cells", "Survivin expression in tumour biopsies"]
    assert mw.judge(page, NOTICE, titles, papers=107)[0] == "wrong-article"
    assert mw.judge(page, NOTICE, titles[1:], papers=1)[0] == "ok"


def test_judge_an_origin_error_page_is_refused_even_with_its_doi():
    page = mhtml(PAPER, "Retraction: " + ZTITLE, own=PAPER).replace(
        "<h1>", "<p>Error reference number: 523. Origin is unreachable.</p><h1>")
    assert mw.judge(page, PAPER, [ZTITLE])[0] == "error-page"


def test_archive_files_never_take_the_documents_own_path(tmp_path, monkeypatch):
    from rwpipe import storage
    monkeypatch.setattr(storage.config, "MHTML_DIR", tmp_path)
    own = storage.page_path(PAPER, "10.1038", "mhtml")
    assert storage.page_path(PAPER, "10.1038", "mhtml", tag="wayback") != own
    assert storage.page_path(PAPER, "10.1038", "html", tag="wayback").name.endswith(
        ".wayback.html.gz")


def test_the_archive_is_asked_about_the_article_not_the_login_page():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cw", ROOT / "scripts" / "collect_wayback.py")
    cw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cw)
    idp = ("https://idp.springer.com/authorize?response_type=cookie&client_id=springerlink"
           "&redirect_uri=https%3A%2F%2Flink.springer.com%2Farticle%2F10.1007%2Fs13277-017-5487-6")
    assert cw.lookup_urls(idp, NOTICE) == [
        "https://link.springer.com/article/10.1007/s13277-017-5487-6",
        f"https://doi.org/{NOTICE}"]
    page = "https://journals.sagepub.com/doi/10.1177/10641246251331509"
    assert cw.lookup_urls(page, LABELLED) == [page]


def test_judge_counts_words_not_separators_in_an_error_title():
    page = mhtml("10.1242/dev.135533", "Not Found | The Company of Biologists",
                 toolbar_for="10.1242/dev.135533")
    assert mw.judge(page, "10.1242/dev.135533", ["A developmental biology paper"])[0] \
        == "error-page"


def test_reading_an_archive_page_drops_the_archive_toolbar():
    from rwpipe import mhtml as mh
    page = ("<html><head><title>T</title></head><body>"
            "<!-- BEGIN WAYBACK TOOLBAR INSERT -->Formed in 2009, the Archive Team"
            "<!-- END WAYBACK TOOLBAR INSERT --><p>The article itself.</p></body></html>")
    html = mh.parse(page.encode("utf-8")).html
    assert "Archive Team" not in html and "The article itself." in html
