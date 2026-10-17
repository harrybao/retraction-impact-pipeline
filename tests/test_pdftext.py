"""The two ways the PDF route was wrong on its first run, kept as tests.

1. pypdf hands back UTF-16 surrogates for characters outside the BMP, and
   SQLite refuses them: the first full run died 384 documents in.
2. A retraction notice's own PDF was read into `body`, and 177 of 1,272 notice
   documents were then graded `full` - counted as articles.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rwpipe import extract, pdftext


def test_surrogate_pair_becomes_the_real_character():
    # U+1D465 MATHEMATICAL ITALIC SMALL X, as pypdf delivers it.
    assert pdftext._fix_surrogates("𝑥") == "\U0001d465"


def test_lone_surrogate_is_replaced_not_kept():
    out = pdftext._fix_surrogates("a\ud835b")
    assert out == "a�b"
    out.encode("utf-8")          # the thing SQLite needs to be able to do


NOTICE_PAGE = ("Journal of Physics: Conference Series 1114 (2018) 012172 "
               "doi:10.1088/1742-6596/1114/1/012172\n"
               "This article has been retracted by IOP Publishing following an "
               "investigation that found evidence of systematic manipulation of "
               "the publication process.\n"
               "Published under licence by IOP Publishing Ltd")
ARTICLE_PAGE = ("1. Introduction\n" + "The measured response of the sample "
                "is described in detail here. " * 40)


def _fake(monkeypatch, pages):
    monkeypatch.setattr(pdftext, "_INDEX", {"10.1/x": "pdf/x.pdf"})
    monkeypatch.setattr(pdftext, "pages_of", lambda path: pages)


def test_a_notice_documents_pdf_feeds_the_notice_never_the_body(monkeypatch):
    _fake(monkeypatch, [NOTICE_PAGE])
    e = extract.Extracted(method="jats")
    assert pdftext.fill_from_pdf(None, e, "10.1/x", "retraction")
    assert e.body == ""
    assert "has been retracted" in e.notice
    assert e.notice_method.startswith("pdf-")


def test_an_unrecognised_statement_is_still_the_notice(monkeypatch):
    # "Withdrawal of the paper" - phrasing the cue matcher may not know. The
    # whole page is the notice rather than nothing being recorded.
    # No cue phrase anywhere on the page. The first version of this fixture
    # said "at the request of the authors" - which IS a cue, so the matcher
    # rightly started the notice there and the test failed on its own data.
    page = ("EPL 96 (2011) 40000 Publishers' Note\n"
            "Withdrawal of the paper on strain-induced ferroelectricity in "
            "KTaO3, owing to an error in the analysis.")
    _fake(monkeypatch, [page])
    e = extract.Extracted(method="jats")
    pdftext.fill_from_pdf(None, e, "10.1/x", "retraction")
    assert e.body == ""
    assert "Withdrawal of the paper" in e.notice


def test_an_articles_pdf_feeds_the_body(monkeypatch):
    _fake(monkeypatch, [NOTICE_PAGE, ARTICLE_PAGE])
    e = extract.Extracted(method="jats")
    assert pdftext.fill_from_pdf(None, e, "10.1/x", "original")
    assert "measured response" in e.body
    assert e.method == "jats+pdf"


def test_a_fuller_xml_notice_is_kept(monkeypatch):
    _fake(monkeypatch, ["This article has been retracted."])
    e = extract.Extracted(method="jats", notice="This article has been retracted "
                          "by IOP Publishing following a detailed investigation.")
    before = e.notice
    assert not pdftext.fill_from_pdf(None, e, "10.1/x", "retraction")
    assert e.notice == before
