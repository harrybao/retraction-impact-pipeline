"""Real publisher wording and DOM shapes from the October 2026 review."""
import pytest

from rwpipe import extract


COMMUNICATION = (
    "This Communication, published online on 14 August 2019 in Wiley Online Library "
    "(onlinelibrary.wiley.com), has been withdrawn by agreement between the corresponding "
    "authors, the journal's Executive Committee, and Wiley-VCH GmbH, Weinheim.")


@pytest.mark.parametrize("statement", [
    COMMUNICATION,
    "The above article published online on July 25, 2013 has been withdrawn by the request from authors.",
    "The article published at Clinical Endoscopy Vol. 44 No. 1, was withdrawn after request from authors.",
    'The article "SOCS3 overexpression enhances ADM resistance" has been withdrawn due to misunderstandings among some authors.',
    'The article "Lycopene prevents experimental priapism" has been withdrawn due to problems concerning authorship.',
])
def test_real_editorial_statement_is_recognised_without_whole_page_fallback(statement):
    assert extract.NOTICE_CUE.search(statement)
    result = extract.extract(f"<html><body><p>{statement}</p></body></html>", kind="both")
    assert result.notice and result.notice_method != "body-is-notice"


def test_wiley_communication_below_author_widgets_keeps_the_reason():
    widgets = "<div>Search for more papers by this author</div>" * 12
    reason = "The withdrawal is necessary because of problems with a microscopy image."
    result = extract.extract(
        f"<html><body><h1>THIS ARTICLE HAS BEEN WITHDRAWN</h1>{widgets}"
        f"<p>{COMMUNICATION}</p><p>{reason}</p></body></html>", kind="both")
    assert "Wiley-VCH" in result.notice and "microscopy image" in result.notice


@pytest.mark.parametrize("kind", ["original", "both", "retraction"])
def test_wiley_truncated_metadata_cannot_replace_complete_actual_editorial_paragraph(kind):
    reason = (" The withdrawal has been agreed upon following the explanation by the "
              "authors that problems with a microscopy image presented in Figure 4 "
              "of the article cannot be fully clarified.")
    complete = COMMUNICATION + reason
    short = COMMUNICATION[:200] + "..."
    result = extract.extract(
        f'<html><head><meta name="description" content="{short}"></head><body>'
        '<h1>THIS ARTICLE HAS BEEN WITHDRAWN</h1>' +
        '<div>Search for more papers by this author</div>' * 12 +
        f'<div class="article-section__abstract"><div class="article-section__content">'
        f'<p>{complete}</p></div></div></body></html>', kind=kind)
    assert result.notice == complete
    assert result.notice_method == "abstract-statement"


@pytest.mark.parametrize("tail", [
    "<h2>Abbreviations</h2><p>APTES: Aminopropyltriethoxysilane</p>",
    "<h2>Authors' original submitted files for images</h2><p>Download GIF</p>",
    "<h5>This article has been cited by the following publications.</h5><p>Cited publication data</p>",
])
def test_notice_stops_before_unrelated_article_sections(tail):
    statement = "This article has been retracted because the peer review was compromised."
    result = extract.extract(f"<html><body><p>{statement}</p>{tail}</body></html>")
    assert result.notice == statement


def test_cambridge_link_cards_are_not_editorial_statements():
    pointer = ("<div id='corrections-tab' class='corrections tab-pane'>"
               "<h2>The following corrections have been issued for this article:</h2>"
               "<p>Synbiotic supplementation - Expression of concern</p></div>")
    cited = ("<h5>This article has been cited by the following publications.</h5>"
             "<p>Unrelated Expression of concern cited by another paper.</p>")
    result = extract.extract(f"<html><body>{pointer}{cited}</body></html>")
    assert not result.notice
    assert result.notice_method == "pointer-only"


def test_removing_pointer_cards_preserves_a_real_on_page_editorial_statement():
    pointer = ("<div id='corrections-tab'><h2>The following corrections have been issued "
               "for this article:</h2><p>Related Expression of concern</p></div>")
    statement = "This article has been retracted because the authors fabricated data."
    result = extract.extract(f"<html><body>{pointer}<p>{statement}</p></body></html>")
    assert result.notice == statement


def test_original_file_evidence_sentence_is_not_mistaken_for_a_download_heading():
    statement = "This article has been retracted because its data were unreliable."
    evidence = "Authors' original submitted files for images contained duplicated panels."
    result = extract.extract(f"<html><body><p>{statement}</p><p>{evidence}</p></body></html>")
    assert evidence in result.notice


def test_citation_evidence_sentence_is_not_mistaken_for_a_cited_by_widget():
    statement = "This article has been retracted because its data were unreliable."
    evidence = "This article has been cited by the following publications as evidence of fabricated data."
    result = extract.extract(f"<html><body><p>{statement}</p><p>{evidence}</p></body></html>")
    assert evidence in result.notice


@pytest.mark.parametrize("statement", [
    "Samples were withdrawn by agreement with the journal club protocol at 10 min.",
    "Two patients were withdrawn at the request from their legal guardians.",
    "The participant was withdrawn by agreement among the guardians and clinicians.",
])
def test_editorial_expansion_does_not_accept_method_or_participant_sentences(statement):
    assert not extract.NOTICE_CUE.search(statement)
