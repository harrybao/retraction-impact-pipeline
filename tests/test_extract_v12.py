"""Extractor v12: the six defect classes the 2026-09-16 audit of v11 measured.

Every fixture is wording taken from a stored page, and every rule has a
counter-example beside it - the counter-examples are the reason the rules are
shaped the way they are.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bs4 import BeautifulSoup  # noqa: E402

from rwpipe import extract  # noqa: E402


def page(body: str, head: str = "") -> str:
    return f"<html><head><title>t</title>{head}</head><body>{body}</body></html>"


# --- the decision test ------------------------------------------------------

def test_a_heading_is_not_a_decision_but_the_statement_under_it_is():
    """IEEE's page route returns "Notice of Retraction: <title>" and nothing
    else. If that counted as a decision, the body fallback below would never
    fire for the 7,629 IEEE documents it exists for."""
    assert not extract.DECISION.search("Notice of Retraction: E-commerce security payment")
    assert not extract.DECISION.search("Retraction note")
    assert extract.DECISION.search(
        "this paper has been found to be in violation of IEEE's Publication Principles")
    assert extract.DECISION.search("We hereby retract the content of this paper")


def test_a_decision_needs_the_article_as_its_subject():
    """"the microcatheter tip must be withdrawn slowly" is a methods section."""
    assert not extract.DECISION.search("the microcatheter tip must be withdrawn slowly")
    assert not extract.DECISION.search("the skin flap must be retracted to expose the joint")
    assert extract.DECISION.search("the Editor has decided that this paper should be retracted")


def test_the_body_statement_wins_when_the_page_offers_only_a_heading():
    """IEEE: heading in the page, statement in the body."""
    html = page("<h1>Notice of Retraction: Tribological properties of AISI 4140</h1>"
                "<p>Abstract: Retracted.</p>")
    body = ("Notice of Retraction: After careful and considered review of the content of this "
            "paper by a duly constituted expert committee, this paper has been found to be in "
            "violation of IEEE's Publication Principles. We hereby retract the content of this paper.")
    notice, how = extract._notice(BeautifulSoup(html, "lxml"), body, kind="both")
    assert "in violation of IEEE's Publication Principles" in notice
    assert how == "body-decision"


def test_a_page_notice_that_already_decides_is_kept():
    """The fallback must not overwrite a real statement with body text."""
    html = page("<div class='retraction'><p>The above article, published online on 11 July 2023 "
                "in Wiley Online Library, has been retracted by agreement between the authors and "
                "the journal Editor in Chief.</p></div>")
    body = "This article has been retracted. Some other wording entirely, from the article page."
    notice, how = extract._notice(BeautifulSoup(html, "lxml"), body, kind="both")
    assert notice.startswith("The above article")
    assert how.startswith("selector:")


# --- the PDF route ----------------------------------------------------------

def test_a_pdf_statement_broken_across_lines_is_joined_before_the_cue_search():
    """IOP lays the statement out across the page, so the cue spanned the break
    and 839 notices began at "retracted by IOP Publishing"."""
    pdf = ("Retraction\n"
           "This article (and all articles in the proceedings volume relating to the same "
           "conference) has been\n"
           "retracted by IOP Publishing following an extensive investigation in line with the "
           "COPE guidelines.\n"
           "This investigation has uncovered evidence of systematic manipulation.")
    notice, how = extract._notice_from_text(extract._unwrap(pdf), kind="retraction")
    assert notice.startswith("This article (and all articles")
    assert how == "cue"


def test_unwrap_leaves_a_finished_sentence_alone():
    text = "This article has been retracted.\nThe publisher apologises to readers."
    assert extract._unwrap(text) == text


# --- stop markers -----------------------------------------------------------

def test_the_taylor_and_francis_article_rail_ends_a_notice():
    lines = ["This article has been retracted by the publisher.",
             "Previous article View issue table of contents Next article",
             "Related research Recommended articles Cited by 3"]
    out = extract._narrow(lines)
    assert "has been retracted" in out
    assert "View issue table of contents" not in out


def test_the_wiley_figure_rail_ends_a_notice():
    lines = ["The above article has been retracted by agreement between the authors.",
             "FigureNext Figure",
             "Volume 12, Issue 4 April 2024 Pages 1-9"]
    out = extract._narrow(lines)
    assert "has been retracted" in out
    assert "FigureNext" not in out


# --- page text --------------------------------------------------------------

def test_a_literal_backslash_n_becomes_a_line_break():
    """Cureus ships the notice inside a JSON blob: 222 v11 notices were one
    unbroken line that began with a literal escape."""
    out = extract._clean("This article has been retracted.\\nThe authors apologise.")
    assert "\\n" not in out
    assert out.splitlines()[0] == "This article has been retracted."


def test_the_chat_widget_alone_is_not_a_notice():
    """37 v11 notices were only this, and were graded notice-only."""
    html = page("<div class='ai-companion'><p>AI Companion feature unavailable for retraction "
                "notices, as they pertain to retracted contents</p><p>Your conversation history "
                "is available during this session only</p></div>")
    notice, how = extract._notice(BeautifulSoup(html, "lxml"), "", kind="original")
    assert notice == ""
    assert how == "none"


# --- cues -------------------------------------------------------------------

def test_the_german_statement_is_a_cue():
    """Springer prints German first: neither half matched anything in v11."""
    text = ("Additional information Der Beitrag wurde aufgrund einer Urheberrechtsverletzung vom "
            "korrespondierenden Autor zurückgezogen. This article is retracted as requested by the "
            "corresponding author because of copyright violation.")
    notice, how = extract._notice_from_text(text, kind="retraction")
    assert "zurückgezogen" in notice
    assert how == "cue"


def test_a_long_parenthesis_between_the_article_and_the_verb_still_matches():
    m = extract.NOTICE_CUE.search(
        "This article (and all articles in the proceedings volume relating to the same conference) "
        "has been retracted by IOP Publishing")
    assert m and m.group(0).startswith("This article")


def test_a_bare_status_line_is_still_not_a_cue():
    """v10's rule, re-checked: a date without a reason is not a statement."""
    assert not extract.NOTICE_CUE.search("This article was retracted on 10 April 2026")


def test_a_latex_command_is_not_an_escaped_line_break():
    r"""v12 turned \nabla into "\ abla" in the mathematics papers among the 322
    bodies it rewrote: a LaTeX command begins with a backslash and a letter,
    exactly as an escaped break does. A break is one only when no lowercase
    letter follows."""
    out = extract._clean(r"the gradient \nabla\Phi, with \nu and \neq holding")
    assert r"\nabla" in out and r"\nu" in out and r"\neq" in out
    assert extract._clean("It has been retracted.\nThe authors apologise.").splitlines()[0] \
        == "It has been retracted."


def test_a_latex_command_starting_with_r_is_not_an_escape_either():
    r"""v13 broke \rho into " ho" and \right into " ight" in 256 mathematics
    bodies by treating a lone backslash-r as a line break. Only the pair
    backslash-r backslash-n is one."""
    src = r"density \(\rho\) and \left( x \right) on \(\langle a \rangle\)"
    out = extract._clean(src)
    assert r"\rho" in out and r"\right" in out and r"\rangle" in out
    assert extract._clean(r"One line.\r\nTwo lines.").splitlines() == ["One line.", "Two lines."]
