"""Primary-source identity regressions from the independent pre-rebuild audit."""
import html
import pytest
from rwpipe import identity

DOI = "10.1234/own"
TITLE = "Measured changes in patient outcomes following a controlled intervention"


def page(body, title="Journal", head=""):
    return f"<html><head><title>{title}</title>{head}</head><body>{body}</body></html>"


def test_malformed_publisher_quote_does_not_hide_cpb_affected_title():
    own = "10.33594/000000421"
    body = (f'<nav id="topnav"><ul class="clear"><li><a href="https://doi.org/{own}">{own}</a></li></ul></nav>'
            f'<div id="Story">The article &amp;ldquo{TITLE}, DOI: 10.1234/original, '
            'has been retracted by the Publishers and Editor.</div>')
    assert identity.judge(page(body), own, [TITLE], kind="retraction").usable
    assert not identity.judge(page(body), own, ["An unrelated set of biological findings"], kind="retraction").usable
    assert not identity.judge(page(body), "10.1234/original", [TITLE], kind="original").usable


def test_annotation_article_doi_is_a_bibliographic_field_not_arbitrary_link():
    body = (f'<div class="novsu_block_content"><h4 class="center">{TITLE}</h4>'
            f'<p class="annotation">{"Substantive abstract text. " * 10}</p>'
            f'<p class="annotation">https://doi.org/{DOI}</p></div>')
    assert identity.judge(page(body), DOI, [TITLE], kind="original").usable
    assert not identity.judge(page(f'<div hidden>{body}</div>'), DOI, [TITLE]).usable
    assert not identity.judge(page(f'<div id="references">{body}</div>'), DOI, [TITLE]).usable
    assert not identity.judge(page(body), "10.1234/other", [TITLE]).usable


def test_cpb_authors_request_publication_retraction_with_affected_citation():
    own = "10.33594/000000411"
    body = (f'<nav id="topnav"><ul class="clear"><li><a href="https://doi.org/{own}">{own}</a></li></ul></nav>'
            f'<div id="Story">The authors of the article A. Author, entitled "{TITLE}" '
            '[Journal 2018;46:1455-1470, DOI: 10.1234/original] have requested retraction '
            'on the following basis: The experiments could not be reproduced.</div>')
    assert identity.judge(page(body), own, [TITLE], kind="retraction").usable
    assert not identity.judge(page(body.replace('have requested retraction on the following basis',
                             'withdrew patients from the trial')), own, [TITLE], kind="retraction").usable


def test_published_notice_sidebar_separates_notice_from_affected_doi():
    body = (f'<h1>Retraction: {TITLE}</h1><div><div>Published</div><div>'
            f'<img alt="with-doi"><a href="https://doi.org/{DOI}">{DOI}</a></div></div>'
            '<p>The affected article has DOI: 10.1234/original.</p>')
    v = identity.judge(page(body), DOI, [TITLE], kind="retraction")
    assert v.usable and v.matched_role == "retraction"
    assert not identity.judge(page(body), "10.1234/original", [TITLE], kind="retraction").usable


def test_ojs_own_citation_survives_embedded_notice_with_another_doi():
    body = (f'<article class="obj_article_details"><h1 class="page_title">Retracted: {TITLE}</h1>'
            '<section class="item abstract"><h2>Abstract</h2><p>RETRACTED ARTICLE: '
            'The article has fabricated data. The article was retracted. Notice DOI: 10.1234/notice.</p></section>'
            f'<section class="citation_display"><h2>How to Cite</h2><div id="citationOutput">'
            f'<div class="csl-entry">A. Author (2017). {TITLE}. Journal 6(4), 631-639. '
            f'<a href="https://doi.org/{DOI}">{DOI}</a></div></div></section></article>')
    v = identity.judge(page(body, f"Retracted: {TITLE}"), DOI, [TITLE], kind="original")
    assert v.usable and v.matched_role == "retraction"
    assert not identity.judge(page(body), "10.1234/notice", [TITLE], kind="retraction").usable
    foreign = '<meta name="citation_doi" content="10.1234/different">'
    assert not identity.judge(page(body, head=foreign), DOI, [TITLE], kind="original").usable


def repository(target=DOI, title=TITLE):
    return (f'<article id="item-details"><h1>{title}</h1><div id="item-sidebar">'
            '<section><h3>Persistent Identifier</h3><div><a href="https://doi.org/10.23668/repository.1">'
            '10.23668/repository.1</a></div></section>'
            f'<section><h3>Is version of</h3><div><a href="https://doi.org/{target}">{target}</a></div></section>'
            '<section><h3>Journal title</h3><div>Journal</div></section>'
            '<section><h3>Publication status</h3><div>publishedVersion</div></section>'
            '<section id="item-citation"><div class="csl-entry">Author. Citation.</div></section>'
            '</div></article>')


def test_repository_version_is_directed_role_scoped_and_title_corroborated():
    v = identity.judge(page(repository()), DOI, [TITLE], kind="original")
    assert v.usable and v.reason == "repository-version-of"
    assert v.declared == "10.23668/repository.1" and v.matched_role == "original"
    assert not identity.judge(page(repository()), DOI, [TITLE], kind="retraction").usable
    assert not identity.judge(page(repository()), DOI, ["Completely unrelated chemistry research"], kind="original").usable
    assert not identity.judge(page(repository("10.1234/reference")), DOI, [TITLE], kind="original").usable
    assert not identity.judge(page(f'<div hidden>{repository()}</div>'), DOI, [TITLE], kind="original").usable


def test_repository_relation_does_not_override_unrelated_head_identifier():
    own = '<meta name="citation_doi" content="10.23668/repository.1">'
    foreign = '<meta name="citation_doi" content="10.1234/different">'
    assert identity.judge(page(repository(), head=own), DOI, [TITLE], kind="original").usable
    assert not identity.judge(page(repository(), head=foreign), DOI, [TITLE], kind="original").usable
    assert not identity.judge(page(repository().replace("publishedVersion", "unreviewedDraft")),
                              DOI, [TITLE], kind="original").usable


def test_full_article_heading_can_replace_truncated_browser_title():
    body = (f'<div class="lww-article__header"><h1 id="omni-article-title">{TITLE}<sup>1</sup> [RETRACTED]</h1>'
            '<ul class="lww-article__contributors-list"><li>A. Author</li></ul>'
            '<div class="lww-article__citation-info">Journal 78(4), 317-322 (1999)</div></div>'
            '<div id="omni-article-abstract"><div class="lww-text-abstract">Substantive abstract.</div></div>')
    v = identity.judge(page(body, "Measured changes... : Journal"), DOI, [TITLE], kind="original")
    assert v.usable and v.reason == "article-heading-title"
    assert not identity.judge(page(body.replace('class="lww-article__contributors-list"', 'class="unrelated"')),
                              DOI, [TITLE], kind="original").usable
    assert not identity.judge(page(f'<div hidden>{body}</div>'), DOI, [TITLE], kind="original").usable


def pdf_page(text):
    return '<section class="rwpipe-pdf-page"><pre>' + html.escape(text) + '</pre></section>'


def pdf_capture(pages):
    return '<html data-rwpipe-format="pdf-text" data-rwpipe-integrity="lossy"><body>' + ''.join(map(pdf_page, pages)) + '</body></html>'


def test_recovered_pdf_uses_the_article_page_not_the_cover_reference():
    cover = (f'Journal 2022; X:1\nRetraction Notice\nThe article {TITLE}, DOI: {DOI}, was retracted.\n'
             'Journal 2022; X:1. DOI: 10.1234/notice')
    article = f'Original Research\n{TITLE}\nA. Author\nAbstract\nResults.\nJournal 2017; 242:275-285. DOI: {DOI}'
    v = identity.judge(pdf_capture([cover, article]), DOI, [TITLE], kind="original")
    assert v.usable and v.matched_role == "original"
    assert not identity.judge(pdf_capture([cover]), DOI, [TITLE], kind="original").usable
    assert not identity.judge(pdf_capture([article]), DOI, [TITLE], kind="retraction").usable
    v = identity.judge(pdf_capture([cover]), "10.1234/notice", [TITLE], kind="retraction")
    assert v.usable and v.matched_role == "retraction"


def test_recovered_pdf_reference_doi_is_not_an_own_citation():
    article = f'Original Research\n{TITLE}\nAbstract\nJournal 2017; 242:275-285. DOI: 10.1234/actual\nReferences\nDOI: {DOI}'
    assert not identity.judge(pdf_capture([article]), DOI, [TITLE], kind="original").usable
