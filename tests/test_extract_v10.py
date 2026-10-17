"""Stage 2 extraction, v10: telling the notice from the article.

Each case comes from a stored v9 row that was wrong, and each rule was
measured against the whole v9 table before it was written:

* 5,160 notice documents were graded `full`, so `full` meant "an article to
  analyse" for most of the table and "a notice with a long page around it"
  for those.
* 5,272 notices ran to the 3,000-character cap - most of them through an
  author list, a citation widget or a page's JSON.
* 14,649 notices taken from page text began mid-paragraph, because the page
  was split into lines at every link and italic.
* 1,786 documents stated a retraction in the active voice ("the Editors
  retract this article") and had no notice at all.

The counter-examples matter as much as the examples. Each rule has a
look-alike in the same table that it must not fire on, and those are here
too.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rwpipe import extract  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "extract_text", ROOT / "scripts" / "extract_text.py")
extract_text = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract_text)


def page(body_html: str) -> str:
    return f"<html><head><title>T</title></head><body>{body_html}</body></html>"


# --- grading -----------------------------------------------------------------
def test_a_notice_document_is_never_graded_full():
    """5,160 notice documents were `full`: a Spandidos notice is two
    paragraphs inside a page with a journal menu, an author list and a
    related-articles rail, and the grade was measuring the page."""
    e = extract.Extracted(
        body="Journal menu and related articles. " * 150,
        notice="This article has been retracted at the request of the authors.")
    assert extract_text.classify(e, "retraction") == "notice-only"
    assert extract_text.classify(e, "original") == "full"


def test_a_notice_document_with_no_statement_is_thin_not_full():
    e = extract.Extracted(body="A long notice page with no statement in it. " * 100)
    assert extract_text.classify(e, "retraction") == "thin"


def test_a_page_that_is_both_still_goes_by_proportion():
    notice = "This article has been retracted because the images were duplicated."
    long_page = extract.Extracted(body="x" * 5000, notice=notice)
    short_page = extract.Extracted(body="x" * 300, notice=notice)
    assert extract_text.classify(long_page, "both") == "full"
    assert extract_text.classify(short_page, "both") == "notice-only"


# --- the wording ---------------------------------------------------------------
@pytest.mark.parametrize("text", [
    # the active voice, which none of the v9 phrases matched
    "In light of these concerns, the PLOS ONE Editors retract this article.",
    "The Editor-in-Chief has retracted this article because of text overlap.",
    "The Editors-in-Chief are retracting this article due to similarities.",
    "The authors voluntarily retract their article from Preventive Medicine.",
    "Members of the editorial board have agreed to retract the article.",
    "In light of these new results, I am withdrawing the paper.",
    "The corresponding author requested withdrawal of the article.",
    # Spandidos, in the lower case it is published in
    "the Editor has decided that this paper should be retracted from the Journal.",
    "Notice of Violation of IEEE Publication Principles",
    "This article has been published OnlineFirst, but is withdrawn on request "
    "of the authors.",
    "This manuscript has been withdrawn as a duplicate.",
])
def test_missed_wordings_now_match(text):
    assert extract.NOTICE_CUE.search(text)


@pytest.mark.parametrize("text", [
    # a RETRACTED watermark interrupting a sentence in an Elsevier PDF
    "Availability of machines is another important aspect that should be "
    "RETRACTED M. Sheikhalishahi, N. Eskandari",
    # a methods section
    "To ensure that the stent is fully released, the microcatheter tip must be "
    "withdrawn until the proximal landmark.",
    # journal policy text
    "Authors may withdraw their manuscript at any time before acceptance.",
    # MDPI's standard footer
    "Disclaimer/Publisher's Note: The statements, opinions and data contained "
    "in all publications are solely those of the individual author(s).",
    # a title prefix is a marker, not a statement
    "RETRACTED: Effect of sperm morphology and number on success of "
    "intrauterine insemination",
])
def test_lookalikes_do_not_match(text):
    assert not extract.NOTICE_CUE.search(text)


# --- where a notice ends -------------------------------------------------------
WILEY_ARTICLE_PAGE = page(
    "<p>This article has been retracted</p>"
    "<p>Retraction(s) for this article</p>"
    "<p>Retracted: A Clinical Analysis of Six Cases of Primary Pulmonary Disease</p>"
    "<p>First Published online: July 12, 2023</p>"
    + "".join("<p>orcid.org/0000-0003-3037-11%02d</p>"
              "<p>Department of Respiratory Medicine, Taizhou Hospital of "
              "Zhejiang Province</p>"
              "<p>Search for more papers by this author</p>" % i for i in range(12))
    + "<p>" + "Lymphoepithelioma-like carcinoma is a rare subtype. " * 20 + "</p>")


def test_the_notice_ends_at_the_author_list_when_no_statement_follows():
    """1,638 of Wiley's 1,683 capped notices end at a marker like this: the
    walk went from the banner through every author's orcid, affiliation and
    search link to the 3,000-character cap."""
    e = extract.extract(WILEY_ARTICLE_PAGE, kind="original")
    assert e.notice.startswith("This article has been retracted")
    assert "Retracted: A Clinical Analysis" in e.notice
    assert "Taizhou Hospital" not in e.notice
    assert "Lymphoepithelioma" not in e.notice


def test_a_marker_above_a_statement_is_not_an_end():
    """The same lines sit above real statements - a cue follows "First
    Published online:" on 357 stored notices. Cutting there unconditionally
    would delete the text the notice exists for."""
    html = page(
        "<p>This article has been retracted</p>"
        "<p>First Published online: May 3, 2021</p>"
        "<p>Department of Surgery, First Hospital of a Medical University</p>"
        "<p>The above article, published online on 3 March 2020 in Wiley Online "
        "Library, has been retracted by agreement between the authors and the "
        "journal Editor in Chief.</p>")
    e = extract.extract(html, kind="retraction")
    assert "has been retracted by agreement between the authors" in e.notice


def test_reason_wording_without_a_cue_also_keeps_the_walk_going():
    """Wiley's second sentence carries the reason and no cue."""
    html = page(
        "<p>This article has been retracted by agreement between the authors "
        "and the journal.</p>"
        "<p>First Published online: May 3, 2021</p>"
        "<p>The retraction has been agreed due to unattributed overlap with a "
        "previously published article.</p>")
    e = extract.extract(html, kind="retraction")
    assert "unattributed overlap" in e.notice


def test_a_statement_that_opens_with_marker_words_is_not_a_marker():
    """A fragment began "published online on 16th February 2016 in Wiley
    Online Library, has been retracted" - the date-line marker matched it."""
    html = page(
        "<p>This article has been retracted.</p>"
        "<p>published online on 16th February 2016 in Wiley Online Library, has "
        "been retracted by agreement between the journal Editor and the "
        "publisher.</p>")
    e = extract.extract(html, kind="retraction")
    assert "by agreement between the journal Editor" in e.notice


def test_a_plos_notice_ends_at_its_link_and_keeps_its_first_clause():
    """PLOS prints the notice on the article page, then "View retraction",
    then the article. v9 also started it mid-paragraph, at the fragment
    after the italic journal name."""
    html = page(
        "<p>After this article was published, concerns were raised about Figure 2 "
        "<a href='#r1'>[1]</a>. In light of these concerns, the <em>PLOS ONE</em> "
        "Editors retract this article.</p>"
        "<p>21 Jun 2018: The PLOS ONE Editors (2018) Retraction: Gene expression in "
        "a zebrafish model. PLOS ONE 13(6): e0199.</p>"
        "<p>View retraction</p>"
        "<p>" + "Abstract. The regulation of gene expression is complex. " * 20
        + "</p>")
    e = extract.extract(html, kind="original")
    assert e.notice.startswith("After this article was published, concerns were raised")
    assert "the PLOS ONE Editors retract this article" in e.notice
    assert "View retraction" not in e.notice
    assert "regulation of gene expression" not in e.notice


def test_a_whole_page_notice_ends_at_the_citation_widget():
    body = ("Following the publication of this paper, it was drawn to the Editors' "
            "attention that the Western blot data in Figure 3 had already appeared "
            "in another article.\n"
            "Copy and paste a formatted citation\n"
            "Spandidos Publications style\n"
            "Zhang L, et al. A related article title that is long enough to keep.")
    out = extract._body_as_notice(body)
    assert "Western blot data" in out
    assert "formatted citation" not in out
    assert "Zhang L" not in out


def test_the_notice_ends_where_the_page_turns_into_json():
    paras = ["This article has been retracted. See the retraction notice.",
             "Montesdeoca A, et al. (May 18, 2026) Retraction: Cleft Lip and Palate.",
             '","created_at":"2026-05-18T08:31:25.580-07:00","updated_at":"2026"},',
             "PRISMA flow chart illustrating the study identification process"]
    out = extract._narrow(paras)
    assert "Retraction: Cleft Lip" in out
    assert "created_at" not in out
    assert "PRISMA" not in out


def test_a_boundary_on_the_first_line_does_not_empty_the_notice():
    paras = ["Additional information The Authors have retracted this article "
             "because the data are unreliable.",
             "Rights and permissions"]
    out = extract._narrow(paras)
    assert out.startswith("Additional information The Authors have retracted")
    assert "Rights and permissions" not in out


# --- lines are paragraphs, not strings -----------------------------------------
def test_page_text_keeps_inline_markup_inside_its_line():
    soup = BeautifulSoup("<div><p>One <em>two</em> three.</p><p>Four<br>five</p>"
                         "<!-- a comment --></div>", "lxml")
    assert extract._page_text(soup) == "One two three.\nFour\nfive"


def test_a_link_inside_the_statement_does_not_split_it():
    """v9 began this notice at "), has been retracted by agreement"."""
    html = page(
        "<p>The above article, published online on 12 March 2020 in Wiley Online "
        "Library (<a href='https://onlinelibrary.wiley.com'>wileyonlinelibrary.com"
        "</a>), has been retracted by agreement between the journal Editor in "
        "Chief and John Wiley and Sons Ltd.</p>")
    e = extract.extract(html, kind="retraction")
    assert e.notice.startswith("The above article, published online on 12 March 2020")
    assert "(wileyonlinelibrary.com), has been retracted" in e.notice


# --- whose text may stand as its own notice ---------------------------------------
def test_a_page_that_is_also_the_article_is_not_its_own_notice():
    """v9 stored a 24,682-character PMC paper and a 50,907-character Cochrane
    review as their own retraction notices."""
    html = page("<p>" + "Colorectal cancer ranks third among malignant tumours. "
                * 30 + "</p>")
    e = extract.extract(html, kind="both")
    assert e.notice == ""
    assert e.notice_method == "none"


def test_a_notice_page_still_is_its_own_notice():
    html = page("<p>Following publication, the authors could not provide the "
                "original data for the western blots in Figure 4 when asked by "
                "the journal, and the figure could not be verified.</p>")
    e = extract.extract(html, kind="retraction")
    assert "original data for the western blots" in e.notice
    assert e.notice_method == "body-is-notice"


def _jats(body: str) -> str:
    return ('<?xml version="1.0"?><!DOCTYPE pmc-articleset><pmc-articleset><article>'
            "<front><article-meta><title-group><article-title>A paper</article-title>"
            "</title-group></article-meta></front>"
            f"<body><p>{body}</p></body></article></pmc-articleset>")


def test_jats_follows_the_same_rule_about_whose_text_is_a_notice():
    body = ("Colorectal cancer ranks third among malignant tumours in men and "
            "second in women, and its incidence is rising.")
    assert extract.extract(_jats(body), kind="both").notice == ""
    assert extract.extract(_jats(body), kind="retraction").notice_method == "jats-body"


def _elsevier(para: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<full-text-retrieval-response '
            'xmlns="http://www.elsevier.com/xml/svapi/article/dtd" '
            'xmlns:ce="http://www.elsevier.com/xml/common/dtd" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:xocs="http://www.elsevier.com/xml/xocs/dtd">'
            "<coredata><dc:title>A notice</dc:title></coredata>"
            f"<originalText><xocs:doc><ce:para>{para}</ce:para></xocs:doc>"
            "</originalText></full-text-retrieval-response>")


def test_an_elsevier_notice_without_a_cue_is_its_own_notice():
    """The HTML and JATS routes let a notice page stand as its own notice and
    the Elsevier route never did: 352 Elsevier notice documents had no notice
    at all, 277 of them graded `full`."""
    text = ("The authors regret that the second affiliation was incomplete. The "
            "correct affiliation is given below, and the authors would like to "
            "apologise for any inconvenience caused.")
    e = extract.extract(_elsevier(text), kind="retraction")
    assert e.notice == text
    assert e.notice_method == "body-is-notice"
    assert extract.extract(_elsevier(text), kind="original").notice == ""


# --- found by the first v10 sample run against the stored v9 rows ---------------
@pytest.mark.parametrize("text", [
    "Abstract This article was retracted. See the Notification.",
    "This article was retracted as it was printed by error in this issue.",
    "The Accepted Manuscript version of this article (published on 29 April 2020) "
    "was withdrawn on 8 June 2020 due to concerns over authorship.",
    "This article is a duplicate version of another article entitled "
    "People Say There Are No Accidents.",
    "Editor's Note: Readers are alerted that the reliability of data presented in "
    "this manuscript is currently in question.",
])
def test_wordings_found_on_the_first_sample_run_match(text):
    assert extract.NOTICE_CUE.search(text)


def test_an_article_about_a_withdrawn_drug_is_not_a_notice():
    assert not extract.NOTICE_CUE.search(
        "The article describes how the drug was withdrawn from the market in 2004.")


def test_a_dated_status_line_is_not_a_notice():
    """Springer and Nature print this above the abstract. Matched as a cue it
    started 79 of 1,590 sampled notices at the status line and walked into the
    abstract, past the banner that says what happened."""
    assert not extract.NOTICE_CUE.search(
        "This article was retracted on 10 April 2026 An Author Response (Invitation) "
        "to this article was published on 25 October 2025")
    assert extract.NOTICE_CUE.search(
        "This article was withdrawn on 8 June 2020 due to concerns over data validity.")


def test_a_statement_is_kept_from_its_opening_not_from_its_cue():
    """PLOS gives the reason first and the cue last. The first v10 run began
    these notices at the final paragraph and lost the reason, which v9 had kept
    only because no cue matched and the whole page became the notice."""
    html = page(
        "<p>Reader Comments</p>"
        "<p>Following the publication of this article [1], concerns were raised "
        "regarding the results presented in Figs 1, 4 and 5.</p>"
        "<p>In Fig 1B, the FACS results for two different conditions appear more "
        "similar than expected.</p>"
        "<p>In light of the concerns affecting multiple figures, the PLOS ONE Editors "
        "retract this article.</p>"
        "<p>References</p>"
        "<p>1. Smith J. A reference line that is long enough to be kept as text.</p>")
    e = extract.extract(html, kind="retraction")
    assert e.notice.startswith("Following the publication of this article")
    assert "FACS results" in e.notice
    assert "retract this article" in e.notice
    assert "Smith J." not in e.notice


def test_a_spandidos_opening_is_kept_and_the_citation_above_it_is_not():
    html = page(
        "<p>Exp Ther Med 13: 2279-2285, 2017; DOI: 10.3892/etm.2017.4244</p>"
        "<p>Following the publication of the above article, it was drawn to the "
        "Editors’ attention by a concerned reader that the western blots in Fig. 3 "
        "had already appeared elsewhere.</p>"
        "<p>Owing to the fact that the contentious data had already been published, "
        "the Editor has decided that this paper should be retracted from the Journal.</p>")
    e = extract.extract(html, kind="retraction")
    assert e.notice.startswith("Following the publication of the above article")
    assert "should be retracted" in e.notice


def test_an_opening_phrase_far_above_the_cue_is_not_pulled_in():
    """The look-back is bounded. A sentence about concerns in the article's own
    text, ten paragraphs above a retraction banner, is not the statement."""
    far = ("<p>Concerns were raised about vaccine safety in several national surveys "
           "of parents.</p>")
    filler = "".join(f"<p>Methods paragraph {i} describing the survey design in "
                     "enough detail to be kept.</p>" for i in range(10))
    html = page(far + filler +
                "<p>This article has been retracted at the request of the authors.</p>")
    e = extract.extract(html, kind="original")
    assert e.notice.startswith("This article has been retracted")
    assert "vaccine safety" not in e.notice


def test_the_page_title_and_the_consent_dialog_do_not_start_the_notice():
    """30% of v9 page-text notices began with the <title>; on Springer the
    consent dialog followed it into the notice."""
    html = ("<html><head><title>Retraction Note: A trial of combination therapy | "
            "BMC Cardiovascular Disorders</title></head><body>"
            "<dialog class='cc-banner' data-cc-banner><p>We use essential cookies to "
            "make sure the site can function. We also use optional cookies.</p></dialog>"
            "<h1>Retraction Note: A trial of combination therapy in hospital patients</h1>"
            "<p>The Editors have retracted this article because the trial registration "
            "could not be verified.</p></body></html>")
    e = extract.extract(html, kind="retraction")
    assert e.notice.startswith("Retraction Note: A trial of combination therapy in hospital")
    assert "BMC Cardiovascular Disorders" not in e.notice
    assert "cookies" not in e.notice


@pytest.mark.parametrize("text", [
    "Editor's Note: The Editor-in-Chief is currently investigating this article as "
    "concerns have been raised about integrity of the clinical trial reported here.",
    "Further editorial action will be taken as appropriate once the investigation into "
    "the concerns is complete.",
    "Editor’s Note: Concerns have been raised about the integrity of the data reported "
    "in this article.",
])
def test_springer_nature_editors_notes_match(text):
    """26 v9 documents carried this note and no other cue; on a page that is also
    the article, v10 would otherwise leave them with no notice."""
    assert extract.NOTICE_CUE.search(text)


def test_concerns_in_an_article_s_own_discussion_are_not_a_notice():
    assert not extract.NOTICE_CUE.search(
        "Concerns have been raised about vaccine safety in several national surveys.")


def _plos_notice(issue_paragraphs: int, width: int) -> str:
    issues = "".join(f"<p>Figure panel {i}: the lanes in this blot appear to overlap with "
                     f"{'those in another panel ' * width}of the same figure.</p>"
                     for i in range(issue_paragraphs))
    return page("<p>Reader Comments</p>"
                "<p>After this article [1] was published, concerns were raised regarding "
                "results presented in Figs 1-3.</p>" + issues +
                "<p>In light of the above concerns, the PLOS ONE Editors retract this "
                "article.</p>")


def test_a_long_list_between_opening_and_decision_keeps_both_when_they_fit():
    """Twelve issue paragraphs: more than the eight an article page may look
    back over, well inside the budget, and a notice page may look further."""
    e = extract.extract(_plos_notice(12, 1), kind="retraction")
    assert e.notice.startswith("After this article [1] was published")
    assert "retract this article" in e.notice


def test_a_list_longer_than_the_budget_keeps_the_decision_not_the_opening():
    """When opening, list and decision cannot all fit, the decision stays."""
    e = extract.extract(_plos_notice(12, 12), kind="retraction")
    assert "retract this article" in e.notice
    assert not e.notice.startswith("After this article [1] was published")


def test_a_statement_only_the_body_carries_is_still_found():
    """Page text and body can disagree; the statement is searched for in both
    before a page that is also the article is left with no notice."""
    soup = BeautifulSoup("<html><body><p>RETRACTED: Our Elderly's Mental Health in "
                         "Times of COVID-19</p></body></html>", "lxml")
    body = ("RETRACTED: Our Elderly's Mental Health in Times of COVID-19\n"
            "This article has been retracted to straighten the academic record, at the "
            "request of the authors.")
    notice, method = extract._notice(soup, body, kind="both")
    assert method == "body-cue"
    assert "has been retracted" in notice


def test_a_dropdown_menu_is_not_read_as_a_notice():
    """European Review's category dropdown lists "Expression of concern" as a
    topic. Merged into one line it carried a cue, and 215 of 589 v10 notices
    there began at the menu instead of the statement."""
    html = page(
        "<div class='sidebar'><select class='postform'><option>Select a topic</option>"
        "<option>Allergology</option><option>Erratum</option>"
        "<option>Expression of concern</option><option>Gastroenterology</option>"
        "</select></div>"
        "<p>This article has been withdrawn. The Publisher apologizes for any "
        "inconvenience this may cause.</p>")
    e = extract.extract(html, kind="retraction")
    assert e.notice.startswith("This article has been withdrawn")
    assert "Allergology" not in e.notice
