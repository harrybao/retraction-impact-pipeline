"""Reduced primary DOM fixtures from the October 16 fixed-seed audit sample."""
import pytest

from rwpipe import extract


def page(content):
    return "<html><body>" + content + "</body></html>"


@pytest.mark.parametrize("kind", ["original", "both", "retraction"])
def test_thieme_abstract_stops_at_publication_metadata_before_recommendations(kind):
    pointer = "This article has been retracted. For details, see:"
    note = "Please note that this article is retracted, refer to the retraction note."
    result = extract.extract(page(
        '<section id="abstract"><div class="articleBox cave">'
        f'<p>{pointer}</p><p>Retraction Note: The Spiritual Dimension of Death: A Mini Review</p>'
        '<p>DOI: 10.1055/s-0043-1774713</p></div>'
        f'<p>{note}</p><h2 id="publication-history-container">Publication History</h2>'
        '<p>Article published online: 21 November 2022</p><p>Georg Thieme Verlag KG</p>'
        '<div id="trendmd-suggestions"><h2>We recommend</h2>'
        '<p>Retraction Note: A Spiritual Prescription to Bereaved Parents</p></div></section>'
    ), kind=kind)
    assert pointer in result.notice and note in result.notice
    assert "10.1055/s-0043-1774713" in result.notice
    assert all(noise not in result.notice for noise in (
        "Publication History", "published online", "Thieme Verlag", "We recommend", "Bereaved Parents"))


def test_publication_history_words_inside_reason_are_not_a_text_stop():
    reason = ("This article has been retracted because its Publication History was falsified. "
              "The authors supplied manipulated images.")
    result = extract.extract(page(f'<section id="abstract"><p>{reason}</p></section>'))
    assert result.notice == reason


@pytest.mark.parametrize("kind", ["original", "both", "retraction"])
def test_elsevier_declared_editorial_body_excludes_related_articles_and_footer(kind):
    reason = ("This article has been retracted at the request of the Editor-in-Chief and the authors. "
              "Sections of the text were taken without appropriate attribution from a doctoral thesis. "
              "The findings presented in this systematic review remain accurate as published.")
    result = extract.extract(page(
        '<article data-is="retraction"><section id="bodymatter" property="articleBody">'
        f'<div role="paragraph">{reason}</div></section>'
        '<section><h2>Related Articles</h2><p>Retraction notice to another study</p></section>'
        '<footer>Hide CaptionDownloadSee figure in Article Information for Advertisers</footer></article>'
    ), kind=kind)
    assert result.notice == reason
    assert result.notice_method == "section:editorial-body"


def test_editorial_body_requires_explicit_notice_role():
    reason = "This article has been retracted because its data were fabricated."
    soup = extract.BeautifulSoup(page(
        f'<article data-is="research"><section id="bodymatter"><p>{reason}</p></section></article>'
    ), "lxml")
    assert extract._scoped_notice(soup, 8) is None


def test_later_history_adds_reason_and_author_response_to_main_statement():
    main = "The Editor-in-Chief retracts this article per the COPE guidelines on plagiarism."
    update = ("The authors do not agree to the retraction of this article. "
              "In addition to the overlap with Lopes and Betrouni, this article was retracted "
              "due to overlap with a number of other sources.")
    result = extract.extract(page(
        f'<div id="additional-information-content"><p>{main}</p></div>'
        f'<div id="change-history-content"><p>{update}</p></div>'
    ))
    assert main in result.notice and update in result.notice
    assert "Change history:" in result.notice
    assert result.notice_method == "section:additional-information+change-history"


def test_shorter_duplicate_history_does_not_replace_or_duplicate_reason():
    opening = "The Editor-in-Chief has retracted this article because its data were fabricated."
    result = extract.extract(page(
        f'<div id="additional-information-content"><p>{opening} The authors agree.</p></div>'
        f'<div id="change-history-content"><p>{opening}</p></div>'
    ))
    assert result.notice == opening + " The authors agree."


def test_unrelated_history_without_an_actual_publication_decision_is_not_merged():
    main = "This article has been retracted because its conclusions cannot be verified."
    result = extract.extract(page(
        f'<div id="additional-information-content"><p>{main}</p></div>'
        '<div id="change-history-content"><p>Samples were withdrawn at ten-minute intervals.</p></div>'
    ))
    assert result.notice == main


@pytest.mark.parametrize("container", [
    '<section class="article-section__full"><section class="article-section__content">{}</section></section>',
    '<section class="article-section__abstract"><div class="article-section__content">{}</div></section>',
    '<div class="header-note-content">{}</div>',
])
def test_complete_wiley_decision_beats_short_status_even_with_long_bibliographic_lead(container):
    reason = ("The above article from FEBS Letters, published online on 19 April 2017 "
              "in Wiley Online Library, has been withdrawn by agreement between the authors, "
              "the Journal Managing Editor and John Wiley & Sons Ltd. The withdrawal has been "
              "agreed following a communication from the Corresponding Author who found signs "
              "of data manipulation, and requested the retraction of the article.")
    result = extract.extract(page(
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
        + container.format(f'<p>DOI: 10.1002/1873-3468.12657</p><p>{reason}</p>')
    ))
    assert result.notice == reason
    assert result.notice_method != "section:status"


def test_retracted_abstract_proceedings_keep_actual_reason():
    reason = ("The above abstract from the THSNA 2016 Summit Abstract Proceedings, "
              "first published online in the American Journal of Hematology on 20 July 2016, "
              "has been retracted by agreement between the authors and Wiley. The retraction "
              "has been agreed due to concerns that the abstract was inadvertently submitted "
              "prior to receiving approval from all authors and proper review of data analytics.")
    result = extract.extract(page(
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
        '<section class="article-section__abstract"><div class="article-section__content">'
        f'<p>{reason}</p><h3>Reference</h3><p>Unrelated bibliographic reference</p></div></section>'
    ))
    assert reason in result.notice and "Unrelated bibliographic" not in result.notice


@pytest.mark.parametrize("research", [
    "Samples were withdrawn at ten-minute intervals. The article reports our measurements.",
    "This article discusses how another paper has been retracted because data were fabricated.",
])
def test_wiley_research_and_foreign_paper_do_not_displace_own_status(research):
    result = extract.extract(page(
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
        '<section class="article-section__full"><section class="article-section__content">'
        f'<p>{research}</p></section></section>'
    ))
    assert result.notice == "This article has been retracted."


def test_tandf_inline_editorial_abstract_keeps_reason_instead_of_link_banner():
    opening = "We, the Editor and Publisher of Alexandria Journal of Medicine, have retracted the following article:"
    reason = ("The authors of the above article requested that it be retracted due to issues "
              "with the research methodology which call the reliability of the data into question.")
    result = extract.extract(page(
        '<div class="article-relations-box"><div class="alert-content">This article has been retracted</div></div>'
        f'<div class="hlFld-Abstract"><p>{opening}</p><p>{reason}</p></div>'
        '<aside><p>Retraction notice to another study</p></aside>'
    ))
    assert opening in result.notice and reason in result.notice
    assert "another study" not in result.notice


def test_tandf_labelled_statement_keeps_reason_after_duplicated_research_abstract():
    abstract = "Structure and quantum effects of a molecule are investigated. " * 70
    reason = ("We have agreed to the authors' request for this article to be withdrawn after "
              "they had ascertained that their calculations included serious errors.")
    result = extract.extract(page(
        '<div class="article-relations-box"><div class="alert-content">This article has been retracted</div></div>'
        f'<div class="hlFld-Abstract"><h2>Abstract</h2><p>{abstract}</p></div>'
        '<div class="NLM_sec"><h2>Statement of Retraction</h2>'
        '<p>We, the Editors of Molecular Physics, are retracting the following article at the request of the authors.</p>'
        f'<p>Abstract: {abstract}</p><p>{reason}</p></div>'
    ))
    assert reason in result.notice and "quantum effects" not in result.notice


@pytest.mark.parametrize("prefix", [
    "The following article from the Journal of Applied Polymer Science, published online on 7 April 2010, ",
    "Retraction: Huang J, Ma L. Study on measurement reliability. The above article, published online on 31 May 2018, ",
    "Chen, Z, Liu, W. Geniposide exhibits anticancer activity. The above article, published online on 14 February 2020, ",
])
def test_bibliographic_notice_leads_preserve_subsequent_complete_reason(prefix):
    reason = prefix + "has been retracted by agreement between the authors and Wiley. The retraction was agreed due to significant overlap."
    result = extract.extract(page(
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
        '<section class="article-section__abstract"><div class="article-section__content">'
        f'<p>{reason}</p></div></section>'
    ))
    assert reason in result.notice


def test_wiley_bibliography_paragraph_before_actual_statement_is_not_a_blocker():
    statement = "The above article has been retracted by agreement with the authors because its analyses were incorrect."
    result = extract.extract(page(
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
        '<section class="article-section__abstract"><div class="article-section__content">'
        '<p>Uppal V, et al. Anaesthesia. 2020. https://doi.org/10.1111/anae.14939.</p>'
        f'<p>{statement}</p></div></section>'
    ))
    assert statement in result.notice


def test_citation_area_publisher_widget_preserves_duplicate_publication_reason():
    statement = ("This article has been retracted due to a duplicate publication resulting from a system error "
                 "during data transfer from Water Environment Federation to John Wiley & Sons, Inc.")
    result = extract.extract(page(
        f'<div class="citation"><div class="pb-rich-text"><p>{statement}</p></div></div>'
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
    ))
    assert result.notice == statement


def test_performative_decision_in_labelled_tandf_statement_preserves_reasons():
    opening = "The Editors and Publisher wish to retract the following article published in Cancer Biology & Therapy:"
    reason = "The authors could not provide the raw data or explain the duplicated images."
    result = extract.extract(page(
        '<div class="article-relations-box"><div class="alert-content">This article has been retracted</div></div>'
        f'<div class="hlFld-Abstract"><h2>Statement of Retraction</h2><p>{opening}</p><p>{reason}</p></div>'
    ))
    assert opening in result.notice and reason in result.notice


@pytest.mark.parametrize("text", [
    "The article 10.1000/other was retracted because its images were manipulated.",
    "The editors may retract this article if the allegations are substantiated.",
    "This article discusses how another paper has been retracted because its data were fabricated.",
])
def test_editorial_opening_rejects_foreign_subject_and_hypothetical_withdrawal(text):
    assert not extract._editorial_opening(text)


def test_generic_dated_being_retracted_banner_does_not_start_a_walk_into_research():
    result = extract.extract(page(
        '<div>This article is being retracted effective 8 November 2011.</div>'
        '<p>Topics Signal processing, Optical imaging, Dielectric properties, Holography.</p>'
        '<p>The similarity between nuclear magnetic resonance and optical holography is investigated.</p>'
    ))
    assert not result.notice


def test_being_retracted_in_owned_editorial_region_retains_the_full_reason():
    opening = "The above-mentioned article is being retracted from publication in the journal."
    reason = ("We were made aware of unexplained image manipulations and duplications. "
              "Following an investigation, we have taken the decision to retract this paper.")
    result = extract.extract(page(
        '<div class="article-relations-box"><div class="alert-content">This article has been retracted</div></div>'
        f'<div class="NLM_sec"><p>{opening}</p><p>{reason}</p></div>'
    ))
    assert opening in result.notice and reason in result.notice


@pytest.mark.parametrize("lead", ["We discuss another paper. ", "This study reviews a cited article. "])
def test_bibliographic_lead_detection_cannot_erase_a_foreign_paper_subject(lead):
    text = lead + ("The above article, published online on 31 May 2018, has been retracted "
                   "by the editors because its data were fabricated.")
    assert not extract._editorial_opening(text)
    result = extract.extract(page(
        '<div class="article-retraction-banner__content">This article has been retracted.</div>'
        '<section class="article-section__abstract"><div class="article-section__content">'
        f'<p>{text}</p></div></section>'
    ))
    assert result.notice == "This article has been retracted."


@pytest.mark.parametrize("heading", ["References", "This article has been cited by the following publications."])
def test_tandf_reference_or_cited_by_section_cannot_become_the_target_notice(heading):
    result = extract.extract(page(
        '<div class="article-relations-box"><div class="alert-content">This article has been retracted</div></div>'
        f'<div class="NLM_sec"><h2>{heading}</h2><p>Retraction: A different study</p>'
        '<p>This article has been retracted because its data were fabricated.</p></div>'
    ))
    assert result.notice == "This article has been retracted"
