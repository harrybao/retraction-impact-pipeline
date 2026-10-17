"""Self-citation fields differ from request URLs and cited-paper identifiers."""
import pytest
from rwpipe import identity

DOI = "10.1234/own"
TITLE = "A controlled intervention and clinical outcomes"


def page(body, head=""):
    return f"<html><head><title>Journal</title>{head}</head><body>{body}</body></html>"


@pytest.mark.parametrize("body", [
    f'<article class="obj_article_details"><section class="item doi"><div class="value"><a href="https://doi.org/{DOI}">{DOI}</a></div></section></article>',
    f'<div id="article-wrapper"><aside id="smj-sidebar"><div class="sidebar-widgets"><p>DOI {DOI}<br>Pages 169</p></div></aside></div>',
    f'<div class="detail-right"><p class="subinfo">Volume: 10 Issue: 5 Pages: 1-2 DOI: {DOI}</p></div>',
    f'<div id="TabArticulo" class="tab-pane active"><div>DOI: {DOI}</div></div>',
    f'<ds-show-metadata><div class="simple-view-element"><b>Identificador DOI</b><a href="https://www.doi.org/{DOI}">{DOI}</a></div></ds-show-metadata>',
])
def test_explicit_bibliographic_fields_work_under_a_label_title(body):
    assert identity.judge(page(body), DOI, [TITLE]).usable


def test_mass_notice_collation_is_not_its_affected_article_list():
    body = (f'<div class="right_articles"><p><span id="x_labelArticleCollation">'
            f'<p align="right">Genetika 2023 Volume 55 Issue 2 Pages 791-791 '
            f'<a href="https://doi.org/{DOI}">{DOI}</a></p></span>'
            '<p>The following papers were retracted. DOI: 10.1234/other</p></p></div>')
    assert identity.judge(page(body), DOI, [TITLE], papers=31, kind="retraction").usable


def test_cpb_notice_control_is_distinct_from_the_affected_original():
    doi = "10.33594/000000448"
    body = (f'<nav id="topnav"><ul class="clear"><li><a href="https://doi.org/{doi}">{doi}</a></li></ul></nav>'
            f'<div id="Story">Retraction Statement. The article {TITLE}, DOI: 10.1234/original, has been retracted.</div>')
    result = identity.judge(page(body), doi, [TITLE], kind="retraction")
    assert result.usable and result.matched_role == "retraction"
    assert not identity.judge(page(body), doi, ["Unrelated genetic research paper"], kind="retraction").usable


def test_cpb_methods_withdrawal_is_not_a_publication_notice_field():
    doi = "10.33594/000000448"
    body = (f'<nav id="topnav"><ul class="clear"><li><a href="https://doi.org/{doi}">{doi}</a></li></ul></nav>'
            f'<div id="Story">{TITLE}. Samples were withdrawn at regular intervals.</div>')
    assert identity.self_field(page(body), [TITLE]) is None


def test_cpb_publisher_expression_of_concern_is_a_notice_not_a_methods_cue():
    doi = "10.33594/000000551"
    body = (f'<nav id="topnav"><ul class="clear"><li><a href="https://doi.org/{doi}">{doi}</a></li></ul></nav>'
            f'<div id="Story">The current and former Publishers and Editor are issuing an Expression of Concern '
            f'on the article entitled "{TITLE}". DOI: 10.1234/affected.</div>')
    result = identity.judge(page(body), doi, [TITLE], kind="retraction")
    assert result.usable and result.matched_role == "retraction"


def test_cpb_related_retraction_link_is_not_a_conflicting_self_identifier():
    doi = "10.33594/000000502"
    body = (f'<nav id="topnav"><ul class="clear"><li><a href="https://doi.org/{doi}">{doi}</a></li>'
            '<li><a href="https://doi.org/10.33594/000000545">Retraction Statement</a></li></ul></nav>'
            f'<div id="Story">Expression of Concern. The article {TITLE}, DOI: 10.1234/original.</div>')
    result = identity.judge(page(body), doi, [TITLE], kind="retraction")
    assert result.usable and result.declared == doi and result.matched_role == "retraction"
    assert not identity.judge(page(body), "10.33594/000000545", [TITLE], kind="retraction").usable


def test_cpb_two_printed_self_identifiers_remain_ambiguous():
    body = ('<nav id="topnav"><ul class="clear">'
            '<li><a href="https://doi.org/10.33594/000000502">10.33594/000000502</a></li>'
            '<li><a href="https://doi.org/10.33594/000000545">10.33594/000000545</a></li></ul></nav>'
            f'<div id="Story">Expression of Concern. The article {TITLE}.</div>')
    assert identity.self_field(page(body), [TITLE])[1] == "ambiguous-self-field"


def test_ieee_own_doi_field_precedes_plagiarized_paper_identifier_in_notes():
    body = (f'<h1 class="document-title">Notice of Retraction: {TITLE}</h1>'
            '<section class="document-abstract"><div class="document-article-notes">'
            'This paper copied text from another paper, DOI: 10.1234/copied.</div>'
            f'<div class="stats-document-abstract-doi"><a href="https://doi.org/{DOI}">{DOI}</a></div></section>')
    result = identity.judge(page(body), DOI, [TITLE], kind="both")
    assert result.usable and result.reason == "article-abstract-doi" and result.declared == DOI
    assert not identity.judge(page(body), "10.1234/copied", [TITLE], kind="both").usable


def test_ieee_self_doi_duplicates_agree_but_conflicts_and_hidden_fields_do_not():
    field = f'<div class="stats-document-abstract-doi"><a href="https://doi.org/{DOI}">{DOI}</a></div>'
    body = f'<h1 class="document-title">{TITLE}</h1><section class="document-abstract">{field}{field}</section>'
    assert identity.judge(page(body), DOI, [TITLE]).usable
    conflict = field.replace(DOI, "10.1234/other")
    assert identity.self_field(page(body.replace(field + field, field + conflict)))[1] == "ambiguous-self-field"
    assert identity.self_field(page(body.replace('<section ', '<section hidden '))) is None


def test_foreign_head_doi_still_beats_a_self_field():
    body = f'<div id="TabArticulo" class="active"><div>DOI: {DOI}</div></div>'
    head = '<meta name="citation_doi" content="10.1234/other">'
    assert identity.judge(page(body, head), DOI, [TITLE]).verdict == "other-doi"


def test_ambiguous_fields_cannot_be_rescued_by_the_first_printed_doi():
    body = f'<div id="TabArticulo" class="active"><div>DOI: {DOI}</div><div>DOI: 10.1234/other</div></div>'
    assert identity.judge(page(body), DOI, [TITLE]).reason == "ambiguous-self-field"


def test_reference_widgets_are_not_self_citation_metadata():
    body = f'<section id="references"><div id="TabArticulo" class="active"><div>DOI: {DOI}</div></div></section>'
    assert not identity.judge(page(body), DOI, [TITLE]).usable


def test_ordinary_hidden_bibliographic_fields_are_not_active_evidence():
    body = f'<div hidden><div id="TabArticulo" class="active"><div>DOI: {DOI}</div></div></div>'
    assert not identity.judge(page(body), DOI, [TITLE]).usable


def test_hidden_whole_scholarly_article_does_not_gain_a_trusted_identifier_exception():
    body = (f'<article hidden itemscope itemtype="https://schema.org/ScholarlyArticle"><h1>{TITLE}</h1>'
            '<header class="article-meta"><div id="article-information"><dl class="article-identifiers">'
            f'<a itemprop="sameAs" href="https://doi.org/{DOI}">{DOI}</a></dl></div></header></article>')
    assert not identity.judge(page(body), DOI, [TITLE]).usable


@pytest.mark.parametrize("wrapper", ["noscript", "template", "hidden", "display:none", "visibility:hidden"])
def test_inert_notice_text_cannot_override_an_original_doi_content_role(wrapper):
    statement = ('<div class="article-section__abstract"><div class="article-section__content">'
                 '<p>Retraction Statement: This article has been retracted.</p></div></div>')
    if wrapper in ("noscript", "template"):
        inert = f"<{wrapper}>{statement}</{wrapper}>"
    else:
        attr = "hidden" if wrapper == "hidden" else f'style="{wrapper}"'
        inert = f"<div {attr}>{statement}</div>"
    capture = page(inert + '<h2>Methods</h2><p>Original study content.</p>',
                   f'<meta name="citation_doi" content="{DOI}">').replace(
                       '<title>Journal</title>', '<title>WITHDRAWN: ' + TITLE + '</title>')
    result = identity.judge(capture, DOI, [TITLE], kind="original")
    assert result.usable and result.matched_role is None
