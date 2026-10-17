"""PDF captures: page integrity, editorial covers, and four read-only regressions."""
from __future__ import annotations

import gzip
import hashlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rwpipe import mhtml, pdftext


NOTICE = (
    "Retraction Notice\n"
    "DOI: 10.1234/notice\n"
    "The following article has been retracted at the request of the Editor.\n"
    "Doe J. Tested study. DOI: 10.1234/original\n"
    "The authors could not provide the original data.\n"
)
RESEARCH = (
    "Original Research\nTested study\nJane Doe\nAbstract\n"
    "We measured a response in controlled experiments.\n"
    "Journal 2020; 12: 1-9. DOI: 10.1234/original\n"
    "Introduction\nThe measured response is discussed in detail.\n"
)


def mock_pdf(monkeypatch, pages, raw=b"%PDF-1.7\nmock"):
    class Page:
        def __init__(self, value):
            self.value = value

        def extract_text(self):
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    import pypdf

    def reader(stream, strict=False):
        assert stream.read() == raw
        assert strict is False
        return SimpleNamespace(pages=[Page(value) for value in pages])

    monkeypatch.setattr(pypdf, "PdfReader", reader)
    return mhtml.parse(raw)


def test_sniffs_pdf_before_mime_and_keeps_page_order(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("PDF bytes reached MIME parsing")

    monkeypatch.setattr(mhtml.email, "message_from_bytes", forbidden)
    doc = mock_pdf(monkeypatch, [NOTICE, RESEARCH])
    soup = BeautifulSoup(doc.html, "html.parser")
    assert doc.ok and doc.parts == 1 and doc.charset == "pdf-text/recovered"
    assert soup.html["data-rwpipe-format"] == "pdf-text"
    assert soup.html["data-rwpipe-graphics"] == "unverified"
    assert soup.html["data-rwpipe-page-count"] == "2"
    sections = soup.select("section.rwpipe-pdf-page")
    assert [p["data-page"] for p in sections] == ["1", "2"]
    assert [p.pre.get_text() for p in sections] == [NOTICE, RESEARCH]
    assert not soup.find("meta") and not soup.find("title")


def test_pdf_text_is_escaped_and_never_interpreted_as_markup(monkeypatch):
    text = '<script>alert("x")</script> & <meta name="citation_doi" content="10.1/fake">'
    doc = mock_pdf(monkeypatch, [text])
    soup = BeautifulSoup(doc.html, "html.parser")
    assert soup.pre.get_text() == text
    assert not soup.find("script") and not soup.find("meta")
    out = pdftext.extract_capture(doc.html, "original")
    assert text in out.body


def test_lossy_and_failed_or_empty_pages_are_retained(monkeypatch):
    raw = b"%PDF-1.7\n\xef\xbf\xbd"
    doc = mock_pdf(monkeypatch, [RESEARCH, "", ValueError("bad stream")], raw)
    soup = BeautifulSoup(doc.html, "html.parser")
    assert doc.ok and doc.parts == 1 and doc.charset == "pdf-text/lossy"
    assert soup.html["data-rwpipe-integrity"] == "lossy"
    assert soup.html["data-rwpipe-page-count"] == "3"
    sections = soup.select("section.rwpipe-pdf-page")
    assert [p["data-status"] for p in sections] == ["ok", "empty", "error"]
    assert sections[2]["data-error"] == "ValueError"
    assert len(sections) == 3


def test_surrogate_text_is_utf8_safe_and_qualifies_loss(monkeypatch):
    doc = mock_pdf(monkeypatch, ["paired \ud835\udc65 lone \ud835"])
    doc.html.encode("utf-8")
    assert "\U0001d465" in doc.html and "\ufffd" in doc.html
    assert doc.charset == "pdf-text/lossy"


@pytest.mark.parametrize("pages", [[], ["", "  "], [RuntimeError("broken"), ""]])
def test_all_blank_or_failed_pages_stay_unreadable(monkeypatch, pages):
    doc = mock_pdf(monkeypatch, pages)
    assert not doc.ok and doc.html == "" and doc.parts == 1
    assert "no extractable text" in doc.error
    assert "pages=" in doc.error


def test_corrupt_pdf_does_not_fall_back_to_html_or_mime():
    doc = mhtml.parse(b"%PDF-1.7\nnot a valid PDF <html>not article text</html>")
    assert not doc.ok and not doc.html and doc.error.startswith("pdf-text:")


def test_plain_html_route_is_unchanged():
    text = "<html><head><title>Real HTML</title></head><body>Article</body></html>"
    doc = mhtml.parse(text.encode())
    assert doc.ok and doc.html == text and doc.charset == "utf-8" and doc.parts == 1


@pytest.mark.parametrize("kind", ["original", "both"])
def test_research_body_excludes_notice_cover_and_notice_excludes_article(monkeypatch, kind):
    doc = mock_pdf(monkeypatch, [NOTICE, RESEARCH, "Methods\nResearch continuation."])
    out = pdftext.extract_capture(doc.html, kind)
    assert out.error is None and out.method == "pdf-text/recovered"
    assert "could not provide the original data" in out.notice
    assert "Research continuation" not in out.notice and "Abstract" not in out.notice
    assert "controlled experiments" in out.body and "Research continuation" in out.body
    assert "could not provide the original data" not in out.body
    assert "10.1234/notice" not in out.body


def test_retraction_kind_never_emits_research_body(monkeypatch):
    doc = mock_pdf(monkeypatch, [NOTICE, RESEARCH])
    out = pdftext.extract_capture(doc.html, "retraction")
    assert out.body == "" and out.notice
    assert "controlled experiments" not in out.notice


def test_standalone_notice_has_no_original_research_body(monkeypatch):
    doc = mock_pdf(monkeypatch, [NOTICE])
    out = pdftext.extract_capture(doc.html, "original")
    assert out.body == "" and "retracted" in out.notice


def test_unknown_retraction_wording_uses_first_notice_page_only(monkeypatch):
    notice = "Publishers' Note\nWithdrawal of the paper owing to an error in the analysis."
    doc = mock_pdf(monkeypatch, [notice, RESEARCH])
    out = pdftext.extract_capture(doc.html, "retraction")
    assert out.body == "" and "Withdrawal of the paper" in out.notice
    assert "controlled experiments" not in out.notice


def test_generic_pdf_retains_plain_body_without_adding_notice(monkeypatch):
    tail = "References\nDoe, Retraction Notice to an unrelated prior paper."
    doc = mock_pdf(monkeypatch, [RESEARCH, tail])
    out = pdftext.extract_capture(doc.html, "original")
    assert "controlled experiments" in out.body and tail in out.body
    assert out.notice == ""


def test_discussion_of_notice_terminology_is_not_a_notice_cover(monkeypatch):
    text = ("A bibliometric study of Retraction Notice terminology.\n"
            "We compare publication labels in a collection of journals.")
    doc = mock_pdf(monkeypatch, [text])
    out = pdftext.extract_capture(doc.html, "original")
    assert out.body == text and out.notice == ""


def test_headerless_explicit_retraction_is_recognised(monkeypatch):
    text = ("Journal of Experiments 12: 9. DOI: 10.1234/notice\n"
            "This article has been retracted by the publisher for data fabrication.")
    doc = mock_pdf(monkeypatch, [text, RESEARCH])
    out = pdftext.extract_capture(doc.html, "original")
    assert "data fabrication" in out.notice and "data fabrication" not in out.body
    assert "controlled experiments" in out.body


def test_empty_leading_page_does_not_obscure_notice_cover(monkeypatch):
    doc = mock_pdf(monkeypatch, ["", NOTICE, RESEARCH])
    out = pdftext.extract_capture(doc.html, "both")
    assert out.notice and "controlled experiments" in out.body
    assert "could not provide the original data" not in out.body


def test_two_leading_notice_pages_use_only_first_for_notice(monkeypatch):
    second = "Retraction Statement\nThis article has been retracted.\nDifferent-page-only reason."
    doc = mock_pdf(monkeypatch, [NOTICE, second, RESEARCH])
    out = pdftext.extract_capture(doc.html, "both")
    assert "Different-page-only" not in out.notice and "Different-page-only" not in out.body
    assert "controlled experiments" in out.body


def test_cleaning_keeps_math_and_redacts_explicit_client_ip(monkeypatch):
    doc = mock_pdf(monkeypatch, [RESEARCH + "\nYour IP address: 192.0.2.44\nEC 3.2.1.4 and \\nabla x"])
    out = pdftext.extract_capture(doc.html, "original")
    assert "192.0.2.44" not in out.body and "[ip removed]" in out.body
    assert "3.2.1.4" in out.body and "\\nabla" in out.body


@pytest.mark.parametrize("markup", ["<html><body>not wrapper</body></html>",
    '<html data-rwpipe-format="pdf-text"><body></body></html>'])
def test_non_wrapper_or_empty_wrapper_is_not_success(markup):
    assert pdftext.extract_capture(markup).error


def test_extract_dispatch_preserves_cover_and_body_separation(monkeypatch):
    from rwpipe import extract

    doc = mock_pdf(monkeypatch, [NOTICE, RESEARCH])
    out = extract.extract(doc.html, kind="both")
    assert out.method == "pdf-text/recovered"
    assert "controlled experiments" in out.body
    assert "could not provide the original data" in out.notice
    assert "could not provide the original data" not in out.body
    assert extract.extract(doc.html, kind="retraction").body == ""


def test_extract_dispatch_does_not_unescape_pdf_text_as_markup(monkeypatch):
    from rwpipe import extract

    text = '<meta name="citation_doi" content="10.1234/fake"> and ordinary text'
    doc = mock_pdf(monkeypatch, [text])
    out = extract.extract(doc.html)
    assert out.method == "pdf-text/recovered" and out.body == text
    assert out.title == ""


def test_gzipped_pdf_read_keeps_the_original_bytes(monkeypatch, tmp_path):
    raw = b"%PDF-1.7\nmock"
    mock_pdf(monkeypatch, [RESEARCH], raw)
    path = tmp_path / "mislabeled.html.gz"
    path.write_bytes(gzip.compress(raw))
    before = path.read_bytes()
    assert mhtml.read(path).ok
    assert path.read_bytes() == before


def test_actual_text_pdf_without_mocks():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Ordinary original article text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    doc = mhtml.parse(stream.getvalue())
    assert doc.ok and doc.parts == 1
    assert "Ordinary original article text" in pdftext.extract_capture(doc.html).body


REAL_CASES = [
    (8, "10.1177/33/33b8368ab5edb5fc4fc8a17145358046a5a1bfa9.html.gz", False),
    (11, "10.3181/10/10e3ac7035ba78c6bf2b8bf92474c34613ebedc3.html.gz", False),
    (32, "10.1177/96/96d5833f2016254f073f4918fe9caceb78d22991.html.gz", False),
    (50, "10.1177/ad/ad2ea3d65929d3bafd6bfae44c9e40a4b03d9f3b.html.gz", True),
]


@pytest.mark.parametrize("case_id,relative,readable", REAL_CASES)
def test_four_real_pdf_captures_read_only(case_id, relative, readable):
    path = REPO / "data/mhtml" / relative
    if not path.is_file():
        pytest.skip("Local audit capture is not distributed with the tests")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = mhtml.read(path)
    assert doc.ok is readable
    assert doc.charset.startswith("pdf-text/")
    if readable:
        assert doc.parts == 1 and doc.charset == "pdf-text/lossy"
        assert BeautifulSoup(doc.html, "html.parser").html["data-rwpipe-page-count"] == "12"
        out = pdftext.extract_capture(doc.html, "original")
        assert "Upregulation of microRNA-205" in out.body
        assert "Gang An" in out.body and "10.1177/1535370216669839" in out.body
        assert "The corresponding author provided new figures" not in out.body
        assert "uncropped" in out.notice and "1144939" not in out.body
        assert "Methods" in out.body or "Materials and methods" in out.body
        notice_only = pdftext.extract_capture(doc.html, "retraction")
        assert notice_only.body == "" and notice_only.notice == out.notice
    else:
        assert doc.error and not doc.html
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
