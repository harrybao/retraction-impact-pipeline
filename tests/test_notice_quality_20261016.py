"""Manual DOM/XML fixtures grounded in the fixed October 15 notice review."""
import html
import sys
import types

import pytest

from rwpipe import extract


def page(body):
    return "<html><body>" + body + "</body></html>"


def elsevier(description, body, doi="10.1016/j.ast.2019.105338"):
    return (
        '<?xml version="1.0"?><full-text-retrieval-response '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/" '
        'xmlns:xocs="http://www.elsevier.com/xml/xocs/dtd">'
        '<coredata><dc:title>WITHDRAWN: Aerodynamic optimization</dc:title>'
        f'<prism:doi>{doi}</prism:doi><dc:description>{html.escape(description)}</dc:description>'
        f'</coredata><originalText><xocs:doc><xocs:rawtext>{html.escape(body)}</xocs:rawtext>'
        '</xocs:doc></originalText></full-text-retrieval-response>'
    )


@pytest.mark.parametrize("kind", ["original", "both", "retraction"])
def test_thieme_reason_before_closing_decision_survives(kind):
    reason = (
        'The paper "Analysis of five-year experience with tubularized incised plate '
        'urethroplasty for anterior and midpenile hypospadias" has been found to contain '
        'approximately 18 paragraphs (approximately 112 lines) that are plagiarized '
        '(Verbatim Plagiarism) from 5 articles published before 2009.'
    )
    decision = (
        'For these reasons the European Journal of Pediatric Surgery hereby give notice '
        'that the paper has been retracted in accordance with COPE guidelines.'
    )
    result = extract.extract(page(
        '<section id="abstract"><h2>Retraction notice</h2>'
        f'<p>{reason}</p><p>{decision}</p></section>'
        '<div id="crossrefCitesDetails"><p>Other publications</p></div>'
    ), kind=kind)
    assert reason in result.notice and decision in result.notice
    assert "Other publications" not in result.notice


def test_text_route_retains_publication_reason_before_decision():
    reason = "The paper has been found to contain verbatim plagiarism from five earlier articles."
    decision = "For these reasons the paper has been retracted in accordance with COPE guidelines."
    notice, _ = extract._notice_from_text(reason + "\n" + decision, "retraction")
    assert reason in notice and decision in notice


def test_scoped_notice_keeps_short_reason_paragraph():
    result = extract.extract(page(
        '<section id="abstract"><h2>Retraction notice</h2><p>Raw data were missing.</p>'
        '<p>For these reasons this paper has been retracted by the Editor.</p></section>'
    ), kind="retraction")
    assert "Raw data were missing." in result.notice


def test_legacy_removal_notice_keeps_displaced_pregnancy_reason_without_reordering():
    raw = (
        'Complementary Therapies in Clinical Practice (2006) 12, 175 REMOVAL NOTICE '
        'Removal notice to "Acupuncture use for pre-birth treatment" Kate Duke and Michele Don '
        'their suitability for use during pregnancy, and the www.elsevierhealth.com '
        'the above paper be withdrawn due to concerns relating to the use of specific acupuncture points '
        'and clinical accuracy and academic excellence in all papers accepted for publication.'
    )
    result = extract.extract(elsevier("", raw), kind="retraction")
    assert "their suitability for use during pregnancy" in result.notice
    assert "withdrawn due to concerns" in result.notice
    assert raw[raw.index("REMOVAL NOTICE"):] == result.notice


@pytest.mark.parametrize("detail", [
    "This article showed substantial text overlap and peer review manipulation. None of the authors responded.",
    "Patient records could not be located and verified. The study information was fabricated, and the data are unreliable.",
])
def test_complete_additional_information_wins_over_short_change_history(detail):
    opening = "The Editor-in-Chief has retracted this article because its content cannot be verified."
    result = extract.extract(page(
        '<div id="change-history-content"><p>' + opening + '</p></div>'
        '<section><h2>Abbreviations</h2><p>ABI: ankle-brachial pressure index</p></section>'
        '<div id="additional-information-content"><p>Publisher\'s Note</p>'
        '<p>Springer Nature remains neutral with regard to jurisdictional claims.</p>'
        f'<p>{opening} {detail}</p></div>'
    ))
    assert opening in result.notice and detail in result.notice
    assert "ankle-brachial" not in result.notice
    assert "remains neutral" not in result.notice


def test_editorial_warning_and_later_pointer_are_both_preserved():
    warning = "Editor's Note: Readers are alerted that the reliability of data in this article is in question."
    pointer = "This article has been retracted. Please see the Retraction Notice: https://doi.org/10.1186/notice"
    result = extract.extract(page(
        f'<div id="change-history-content"><p>{warning}</p><p>{pointer}</p></div>'
        f'<div id="additional-information-content"><p>{pointer}</p></div>'
    ))
    assert warning in result.notice and pointer in result.notice


@pytest.mark.parametrize("banner", [
    '<div class="article-retraction-banner__content"><span>This article has been retracted.</span></div>',
    '<div class="component-errata-widget"><div class="content-box-body"><a><span>THIS ARTICLE HAS BEEN RETRACTED</span></a></div></div>',
    '<div class="article-relations-box"><div class="alert-content"><h2>This article has been retracted</h2><p>Read the retraction statement published online on 01 May 2015</p></div></div>',
])
def test_status_banner_does_not_swallow_original_abstract(banner):
    result = extract.extract(page(
        banner + '<section class="abstract"><p>Chordomas are rare tumors that often recur.</p>'
        '<p>We trained decision tree and random forest classifiers on original study results.</p></section>'
        '<h2>Abbreviations</h2><p>RF: random forest</p>'
    ))
    assert "RETRACTED" in result.notice.upper()
    assert "Chordomas" not in result.notice and "classifiers" not in result.notice


def test_real_notice_beside_banner_keeps_its_reason():
    statement = "This article has been retracted because duplicated images invalidate its conclusions."
    result = extract.extract(page(
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
        f'<div class="article-notice"><p>{statement}</p></div>'
        '<section class="abstract"><p>Original scientific abstract and experimental results.</p></section>'
    ))
    assert result.notice == statement


def test_ibro_relation_title_is_only_a_pointer():
    result = extract.extract(page(
        '<div class="core-relations"><div class="relation HAS_ERRATUM">'
        '<div class="content--title">RETRACTION NOTICE TO "Intranasal delivery of insulin"</div>'
        '<div>J.A. Martinez, G.J. Francis, W.Q. Liu ...</div></div></div>'
        '<div id="abstracts"><section id="author-abstract"><div>'
        'Amyotrophic lateral sclerosis is a fatal neuromuscular disorder. '
        'We studied nitrergic stress and the impact of intranasal insulin.'
        '</div></section></div>'
    ))
    assert not result.notice
    assert result.notice_method == "pointer-only"


@pytest.mark.parametrize("real_statement", ["", "This article has been retracted because the data are unreliable."])
def test_cambridge_cards_citation_modal_and_kindle_are_not_notice(real_statement):
    result = extract.extract(page(
        '<div id="corrections-tab"><div class="article-card"><p>Study - Expression of Concern</p></div></div>'
        '<div id="retractions-tab"><div class="article-card"><p>Study - RETRACTION</p></div></div>'
        f'<p>{real_statement}</p>'
        '<div class="product-citations-modal"><div class="reference-display">'
        'Study - Expression of Concern. Another citing publication.</div></div>'
        '<div id="kindleModal"><p>To send this article to your Kindle, first ensure your email is approved.</p></div>'
    ))
    assert result.notice == real_statement
    if not real_statement:
        assert result.notice_method == "pointer-only"


def test_european_review_entry_ends_before_citations_and_sidebar_notices():
    reason = "The article has been retracted because its figures duplicate previously published images."
    result = extract.extract(page(
        '<article class="tag-retraction-notice"><div class="entry-content">'
        '<ul class="post-categories"><li>Oncology</li></ul>'
        f'<p>{reason}</p><h5>The Publisher apologizes for any inconvenience.</h5>'
        '<a href="notice.pdf">Free PDF Download</a>'
        '<blockquote class="wp-embedded-content"><p>Embedded original title</p></blockquote>'
        '<div>This work is licensed under a Creative Commons license</div>'
        '<div class="cite-article"><p>Retraction Note: Citation details</p></div>'
        '</div></article><div class="related-posts-div"><p>Retraction Note: Other cancer study</p></div>'
        '<aside><p>New Frontiers in Medicine</p></aside>'
    ), kind="retraction")
    assert reason in result.notice
    assert all(s not in result.notice for s in ("Oncology", "Free PDF", "Embedded", "Creative Commons", "Citation details", "Other cancer", "Frontiers"))


def test_supplementary_file_description_does_not_follow_real_reason():
    statement = "This article has been retracted because incorrect statistics and missing data invalidate it."
    result = extract.extract(page(
        f'<div id="additional-information-content"><p>{statement}</p></div>'
        '<section><h2>Electronic supplementary material</h2><p>Additional file 1: biological processes.</p></section>'
    ))
    assert result.notice == statement


@pytest.mark.parametrize("description", [
    "This article has been withdrawn at the request of the author(s) and/or editor. The Publisher apologizes for any inconvenience this may cause.",
    "This article has been withdrawn at the request of the author. The full Elsevier Policy on Article Withdrawal can be found at http://www.elsevier.com/locate/withdrawalpolicy.",
    "This article has been erroneously withdrawn due to a unique error at Production. The Publisher apologizes for any inconvenience this may cause.",
])
def test_complete_target_description_beats_broken_rawtext(description):
    broken = "Irkutsk his article has been withdrawn at the request of the aut convenience. Corresponding author: an email and journal footer."
    result = extract.extract(elsevier(description, broken), kind="both")
    assert result.notice == description
    assert result.notice_method == "els-description"


def test_truncated_description_cannot_replace_complete_body_reason():
    statement = "This article has been withdrawn at the request of the editors because the published data were fabricated."
    result = extract.extract(elsevier("This article has been withdrawn at the request of...", statement), kind="both")
    assert "fabricated" in result.notice


def test_short_complete_description_cannot_erase_more_detailed_clean_body():
    description = "This article has been retracted. The Publisher apologizes for any inconvenience."
    body = "This article has been retracted because its author fabricated the raw data and supplied manipulated images."
    result = extract.extract(elsevier(description, body), kind="both")
    assert "fabricated the raw data" in result.notice and "manipulated images" in result.notice


def test_foreign_subject_doi_in_metadata_cannot_replace_target_notice():
    description = "The article 10.1016/other was withdrawn by the editors because of fabricated data."
    statement = "This article has been withdrawn at the request of the author because it was published prematurely."
    result = extract.extract(elsevier(description, statement), kind="both")
    assert "prematurely" in result.notice and "fabricated" not in result.notice


def test_scientific_metadata_cannot_assign_cited_paper_reason_to_target():
    description = "We review evidence after another paper was withdrawn by the editors because of image manipulation."
    result = extract.extract(elsevier(description, "Blood samples were withdrawn at five-minute intervals."))
    assert not result.notice


@pytest.mark.parametrize("statement", [
    "Samples were withdrawn at predetermined intervals and filtered for HPLC analysis.",
    "The blood was withdrawn following standard procedure by retro-orbital plexus.",
    "Two patients were withdrawn due to adverse effects.",
    "The vaccine was withdrawn following reports of adverse events.",
    "The manuscript remains under review unless rejected by the journal or withdrawn by the author.",
    "The participant was withdrawn by agreement among the guardians and clinicians.",
])
def test_methods_and_hypothetical_withdrawals_still_are_not_cues(statement):
    assert not extract.NOTICE_CUE.search(statement)
    assert not extract.extract(page(f"<p>{statement}</p>")).notice


def test_v14_latex_escapes_are_preserved():
    text = r"The field is $\nabla f$, $\nu$, $\neq$, $\rho$, $\right\rangle$ and $\newline$."
    assert extract._clean(text) == text


def test_wiley_communication_full_reason_still_wins_over_metadata():
    statement = (
        "This Communication, published online on 14 August 2019 in Wiley Online Library, "
        "has been withdrawn by agreement between the corresponding authors, the journal's "
        "Executive Committee, and Wiley-VCH GmbH. The withdrawal has been agreed upon "
        "following the explanation by the authors that problems with a microscopy image "
        "presented in Figure 4 of the article cannot be fully clarified."
    )
    result = extract.extract(page(
        '<h1>THIS ARTICLE HAS BEEN WITHDRAWN</h1>'
        '<div>Search for more papers by this author</div>' * 2 +
        '<section class="article-section__abstract"><div class="article-section__content">'
        f'<p>{statement}</p></div></section>'
    ), kind="both")
    assert result.notice == statement
    assert result.notice_method == "abstract-statement"


def test_wiley_short_following_reason_is_not_filtered_out():
    opening = "This Communication has been withdrawn by agreement between the authors and publisher."
    result = extract.extract(page(
        '<section class="article-section__abstract"><div class="article-section__content">'
        f'<p>{opening}</p><p>Images were fabricated.</p></div></section>'
    ), kind="both")
    assert "Images were fabricated." in result.notice


def test_cureus_encoded_notice_box_does_not_include_self_citation():
    import json

    hero = (
        '<div class="notices-box"><span>This article has been retracted. See '
        '<a href="https://doi.org/10.7759/cureus.r137">Retraction notice, doi:10.7759/cureus.r137.</a>'
        '</span></div><div class="citation-container"><p>Original article self-citation, January 24, 2024.</p></div>'
    )
    article = "<p>Original scientific discussion of drugs and vaccines.</p>" * 24
    data = json.dumps({"articleHtml":{"hero":{"html":hero},"article":{"html":article}}})
    encoded = data.replace("<", r"\u003c").replace(">", r"\u003e")
    result = extract.extract(page(f'<div data-react-props="{html.escape(encoded, quote=True)}"></div>'))
    assert "10.7759/cureus.r137" in result.notice
    assert "self-citation" not in result.notice and "scientific discussion" not in result.notice


def test_pdf_dispatch_is_lazy_and_preserves_kind(monkeypatch):
    stub = types.ModuleType("rwpipe.pdftext")
    calls = []

    def capture(markup, kind):
        calls.append((markup, kind))
        return extract.Extracted(notice="PDF notice", method="pdf-text")

    stub.extract_capture = capture
    monkeypatch.setitem(sys.modules, "rwpipe.pdftext", stub)
    wrapped = '<html data-rwpipe-format="pdf-text" data-rwpipe-integrity="recovered"><section class="rwpipe-pdf-page"><pre>Text</pre></section></html>'
    assert extract.extract(wrapped, kind="retraction").notice == "PDF notice"
    assert calls == [(wrapped, "retraction")]
    extract.extract(page('<p>Literal data-rwpipe-format="pdf-text" mentioned in an article.</p>'))
    assert len(calls) == 1
