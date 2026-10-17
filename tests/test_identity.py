"""The checks that decide whether a capture is the paper it claims to be.

Every case here failed at least one version of the implementation.

The role check in particular went wrong twice in opposite directions. It first
treated "RETRACTED ARTICLE: X" as a notice, which would have accepted the
article whenever the notice was requested. Corrected, it then rejected genuine
Wiley and Hindawi notices, because those are titled "Retracted: <title>" while
Elsevier and Springer use the same word for the *article*. The same prefix
means opposite things at different publishers, so role is now advisory: DOI
presence and title similarity decide, and the naming convention is recorded
rather than trusted.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from rwpipe import fetch


def page(title: str) -> str:
    return f"<html><head><title>{title}</title></head><body>x</body></html>"


# --- role: is a notice actually a notice ---------------------------------
@pytest.mark.parametrize("title,kind,expected", [
    # "Retracted" is the adjective on the article; "Retraction" is the notice.
    ("RETRACTED ARTICLE: Silencing hsa_circ_PVT1 | Cancer Letters", "retraction", False),
    # Ambiguous on purpose: a bare "RETRACTED:" is the article at Elsevier and
    # the notice at Wiley. Role cannot separate them, so it does not try - the
    # DOI check is what rejects a wrong document.
    ("RETRACTED: Thymoquinone sensitizes hepatocarcinoma cells",    "retraction", True),
    ("Retraction Note: Correlation between body mass",              "retraction", True),
    ("Retraction: Downregulation of X-linked inhibitor",            "retraction", True),
    ("Retraction Notice to: Calcium and SOL Protease",              "retraction", True),
    ("Withdrawal: MicroRNA-34a modulates cytoskeleton",             "retraction", True),
    ("Withdrawn: Some paper",                                       "retraction", True),
    ("Expression of Concern: Curcumin improves age",                "retraction", True),
    ("Corrigendum to: An earlier paper",                            "retraction", True),
    ("Erratum: A small mistake",                                    "retraction", True),
    # A generic landing page is not a role failure - the DOI check is what
    # rejects it, and role must not be asked to do that job.
    ("Sage Journals: Discover world-class research",                "retraction", True),
    # Wiley and Hindawi title the NOTICE this way. Measured against a real
    # capture: title similarity 1.00, DOI present, and an earlier version of
    # this check called it wrong.
    ("Retracted: The Key Technologies of Marine Ship Monitoring",   "retraction", True),
    ("Retracted: Analysis of Anesthesia Effect of Dexmedetomidine", "retraction", True),
    # the article direction is not enforced - articles legitimately carry the flag
    ("RETRACTED ARTICLE: Silencing hsa_circ_PVT1",                  "original",   True),
    ("An ordinary paper title",                                     "original",   True),
])
def test_role(title, kind, expected):
    assert fetch.role_matches(page(title), kind) is expected


def test_role_without_title_is_not_a_failure():
    """No <title> means the DOI check stands alone rather than a rejection."""
    assert fetch.role_matches("<html><body>no title</body></html>", "retraction") is True


# --- DOI presence --------------------------------------------------------
def test_doi_found_plain():
    assert fetch.contains_doi("see 10.1016/j.foo.2020.01 here", "10.1016/j.foo.2020.01")


def test_doi_case_insensitive():
    assert fetch.contains_doi("10.1016/J.FOO.2020.01", "10.1016/j.foo.2020.01")


def test_doi_percent_encoded_slash():
    """Publishers percent-encode the slash inside URLs."""
    assert fetch.contains_doi("/doi/10.1016%2Fj.foo.2020.01/full", "10.1016/j.foo.2020.01")


def test_doi_split_by_quoted_printable_soft_break():
    """MHTML breaks long lines with '=\r\n', which is not part of the content.

    Missing this reports a DOI as absent when it is plainly there.
    """
    capture = "10.1016/j.f=\r\noo.2020.01"
    assert fetch.contains_doi(capture, "10.1016/j.foo.2020.01")


def test_doi_absent_is_absent():
    assert not fetch.contains_doi("a page about something else", "10.1016/j.foo.2020.01")


def test_elsevier_refusal_page_is_rejected():
    """The exact shape that passed every check for a month: HTTP 200, large,
    structurally valid, and containing no article."""
    shell = ("<html><head><title>ScienceDirect</title></head><body>"
             "There was a problem providing the content you requested"
             + "<div>padding</div>" * 5000 + "</body></html>")
    assert not fetch.contains_doi(shell, "10.1016/j.dnarep.2021.103117")


# --- title similarity ----------------------------------------------------
def test_title_matches_through_retraction_prefix_and_journal_suffix():
    ok, score = fetch.title_matches(
        page("RETRACTED ARTICLE: Silencing hsa_circ_PVT1 | Cancer Letters"),
        "Silencing hsa_circ_PVT1")
    assert ok and score > 0.8


def test_title_rejects_a_different_paper():
    ok, _ = fetch.title_matches(page("A study of frog populations"),
                                "Silencing hsa_circ_PVT1")
    assert not ok


def test_title_with_nothing_to_compare_passes():
    ok, _ = fetch.title_matches(page("Anything"), "")
    assert ok


def test_title_split_by_quoted_printable_soft_break():
    """The shape of a PLOS notice captured live on 2026-09-18: the soft break
    fell inside `</title>`, and the next title in the archive - an SVG logo's -
    was compared instead, rejecting the right page."""
    title = "Role of Activated Rac1/Cdc42 in Mediating Endothelial Cell Proliferation"
    capture = ("<html><head><title>Retraction: Role of Activated Rac1/Cdc42 in "
               "Mediating Endothelia=\r\nl Cell Proliferation | PLOS One</ti=\r\ntle>"
               # In the real capture the logo came 3.3 MB later: beyond the
               # 400-character reach of the title search, so it was the next
               # match rather than a tail on this one.
               "</head><body>" + "<p>article text</p>" * 100 +
               "<svg><title=\r\n>CROSSMARK_logo_4</title></svg></body></html>")
    assert fetch.page_title_of(capture).startswith("Retraction: Role of Activated")
    ok, score = fetch.title_matches(capture, title)
    assert ok and score > 0.9


def mhtml(*parts: str) -> str:
    """A minimal MHTML archive: one text/html part per argument, first is the page."""
    b = "----MultipartBoundary--abc"
    head = ('From: <Saved by Blink>\r\nContent-Type: multipart/related;\r\n'
            f'\ttype="text/html";\r\n\tboundary="{b}"\r\n\r\n')
    body = "".join(f"--{b}\r\nContent-Type: text/html\r\n"
                   "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
                   f"{p}\r\n" for p in parts)
    return head + body + f"--{b}--\r\n"


def test_an_svg_title_above_the_page_title_is_skipped():
    """IEEE's consent banner draws a close button whose <svg> carries
    <title>Close this consent banner</title> above the page's own title."""
    capture = mhtml('<html><body><svg width=3D"20px"><title>Close this consent banner'
                    '</title><line x1=3D"2"></line></svg><head><title>Notice of Retraction: '
                    'E-commerce security payment system research and implementation | '
                    'IEEE Conference Publication | IEEE Xplore</title></head></body></html>')
    assert fetch.page_title_of(capture).startswith("Notice of Retraction: E-commerce")
    ok, _ = fetch.title_matches(
        capture, "E-commerce security payment system research and implementation")
    assert ok


def test_a_quoted_printable_title_is_decoded():
    capture = mhtml("<html><head><title>=E8=84=82=E8=B3=AA=E8=86=9C</title></head></html>")
    assert fetch.page_title_of(capture) == "脂質膜"


def test_a_page_title_inside_the_bracketed_original_title_matches():
    """Retraction Watch holds 'English title [original title]'; J-STAGE shows
    only the Japanese one. Measured at 0.14 before this rule."""
    capture = mhtml("<html><head><title>=E8=84=82=E8=B3=AA=E8=86=9C=E3=81=8B=E3=82=89=E6=9C"
                    "=89=E6=A9=9F=E3=83=8A=E3=83=8E=E3=83=81=E3=83=A5=E3=83=BC=E3=83=96=E6"
                    "=9D=90=E6=96=99</title></head></html>")
    ok, score = fetch.title_matches(
        capture, "Organic Nanotube Materials Consisting of Lipid Membranes: Architectures "
                 "and Functions [脂質膜から有機ナノチューブ材料]")
    assert ok and score < 0.55


def test_an_iframe_title_is_not_borrowed():
    capture = mhtml("<html><head></head><body>article</body></html>",
                    "<html><head><title>Usabilla Feedback Button</title></head></html>")
    assert fetch.page_title_of(capture) == ""


@pytest.mark.parametrize("record,page_title", [
    ("E-commerce security payment system research and implementation",
     "E-commerce security payment system research and implementation - IEEE Xplore"),
    ("miR-21 promotes proliferation of gastric cancer cells",
     "miR-21 promotes proliferation of gastric cancer cells | Oncology Reports"),
    ("COVID-19 and long-term outcomes", "COVID-19 and long-term outcomes"),
])
def test_a_hyphen_inside_a_title_is_not_a_journal_separator(record, page_title):
    ok, score = fetch.title_matches(page(page_title), record)
    assert ok and score > 0.9


@pytest.mark.parametrize("title,journal,expected", [
    ("Molecular Medicine Reports", "Molecular Medicine Reports", False),
    ("International Journal of Oncology", "International Journal of Oncology", False),
    ("Journal of Cellular Physiology | Cell Biology Journal | Wiley Online Library",
     "Journal of Cellular Physiology", False),
    ("Full article: Statement of Retraction", None, False),
    ("Retraction - 2012 - Pediatric Transplantation - Wiley Online Library", None, False),
    ("Background", None, False),
    # a paper's own title, notice or not, is informative
    ("Retraction: Role of Activated Rac1/Cdc42 in Mediating Endothelial Cell "
     "Proliferation | PLOS One", "PLoS ONE", True),
    ("MicroRNA-200b inhibits the growth and metastasis of glioma cells via targeting ZEB2",
     "International Journal of Oncology", True),
])
def test_informative_title(title, journal, expected):
    assert fetch.informative_title(title, journal) is expected


# --- page_dois: the DOI a page declares as its own --------------------------------

def test_page_dois_reads_the_indexing_metadata():
    page = ('<html><head><meta name="citation_doi" content="10.1007/S13277-017-5487-6">'
            '<meta name="prism.doi" content="doi:10.1007/s13277-017-5487-6"></head></html>')
    assert fetch.page_dois(page) == {"10.1007/s13277-017-5487-6"}


def test_page_dois_skips_og_url_and_other_identifier_schemes():
    page = ('<html><head>'
            '<meta property="og:url" content="http://web.archive.org/web/2025/https://x.org/doi/10.1177/1">'
            '<meta name="dc.Identifier" scheme="publisher-id" content="10.1177_1064">'
            '<meta name="dc.Identifier" scheme="doi" content="10.1177/10641246251331509">'
            '</head></html>')
    assert fetch.page_dois(page) == {"10.1177/10641246251331509"}


def test_page_dois_decodes_quoted_printable_mhtml():
    page = ('From: <Saved by Blink>\r\nContent-Type: multipart/related; boundary="B"\r\n\r\n'
            '--B\r\nContent-Type: text/html\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n'
            '<html><head><meta name=3D"citation_doi" content=3D"10.1038/cmi.2=\r\n012.76">'
            '</head></html>\r\n--B--')
    assert fetch.page_dois(page) == {"10.1038/cmi.2012.76"}


def test_page_dois_is_empty_when_the_page_declares_none():
    assert fetch.page_dois("<html><body>https://doi.org/10.1/x</body></html>") == set()


def test_page_doi_prefers_citation_doi_and_reads_the_head_only():
    page = ('<html><head><meta name="prism.doi" content="doi:10.4081/r.2024.1686">'
            '<meta name="citation_doi" content="10.4081/r.2018.1171"></head>'
            '<body><meta name="citation_doi" content="10.4081/r.2024.1709"></body></html>')
    assert fetch.page_doi(page) == "10.4081/r.2018.1171"
    assert fetch.page_dois(page) == {"10.4081/r.2018.1171", "10.4081/r.2024.1686"}


def test_page_dois_is_not_fooled_by_data_attributes_or_trailing_dots():
    page = ('<html><head><meta name="citation_doi" data-name="x" '
            'content="10.1177/10641246251331509." data-content="y"></head></html>')
    assert fetch.page_dois(page) == {"10.1177/10641246251331509"}
