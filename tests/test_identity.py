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
