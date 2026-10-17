"""Actual metadata openings that exposed a full-corpus v15 regression."""
import html

import pytest

from rwpipe import extract


def response(description, raw="", doi="10.1006/example", title="Target publication"):
    return (f'<full-text-retrieval-response xmlns:dc="urn:dc" xmlns:prism="urn:prism" xmlns:xocs="urn:xocs">'
            f'<coredata><prism:doi>{doi}</prism:doi><dc:title>{html.escape(title)}</dc:title>'
            f'<dc:description>{html.escape(description)}</dc:description></coredata>'
            f'<originalText><xocs:rawtext>{html.escape(raw)}</xocs:rawtext></originalText>'
            '</full-text-retrieval-response>')


@pytest.mark.parametrize("date,reason", [
    ("8 September 1997", "The experiments were manipulated and/or fabricated by one of the co-authors."),
    ("14 September 2001", "The authors consider the binding data erroneous; the synthesis procedure remains valid."),
])
def test_available_online_date_does_not_hide_real_metadata_statement(date, reason):
    statement = "This article has been retracted at the request of the authors. " + reason
    result = extract.extract(response(f"Available online {date}\n{statement}", "Original research methods and results."))
    assert result.notice == statement
    assert result.notice_method == "els-description"
    assert "Available online" not in result.notice


def test_authors_decision_keeps_complete_retraction_reason():
    statement = ("The authors have decided, regretfully, to retract the article listed above. "
                 "Because of contamination occuring during the isolation procedure, the cDNA clone "
                 "was actually from pig liver, not human lens.")
    result = extract.extract(response(statement), kind="retraction")
    assert result.notice == statement
    assert result.notice_method == "els-description"


@pytest.mark.parametrize("description", [
    "Available online 8 September 1997 This article 10.1006/other was retracted because of fabricated data.",
    "The authors have decided to retract the article 10.1006/other because of fabricated data.",
    "The above article 10.1006/other was retracted because of fabricated data.",
    "This review 10.1006/other was retracted because of fabricated data.",
    "The authors have decided to withdraw the blood samples at five-minute intervals.",
    "If this article has been retracted, the authors may submit a correction.",
    "The authors have decided to study experiments described in another retracted article.",
    "Available online 8 September 1997 We review evidence after another paper was withdrawn by the editors.",
])
def test_metadata_openings_do_not_relax_subject_or_foreign_doi_guards(description):
    result = extract.extract(response(description, "Original research methods and results."))
    assert not result.notice


@pytest.mark.parametrize("prefix", [
    "Available online: 2010", "Available online : 21 July 2005", "Available online: 14th of August 2009",
    "Available online", "Available online October 1997", "Available online 8 September 1997.",
])
def test_legacy_online_datelines_are_not_part_of_the_notice(prefix):
    statement = "This article has been retracted at the request of the editor because data were fabricated."
    result = extract.extract(response(prefix + "\n" + statement))
    assert result.notice == statement


@pytest.mark.parametrize("description,title,passage", [
    ("The Publisher regrets that this article is an accidental duplication of an article already published. "
     "The duplicate article has therefore been withdrawn.", "Target publication", "accidental duplication"),
    ("This chapter has been retracted. This chapter has been removed as the rights have been returned to the author.",
     "3 REMOVED: Exponents and Polynomials", "rights have been returned"),
    ("This Book/Chapter has been withdrawn as the rights have been returned to the author.",
     "4 WITHDRAWN: Rational Expressions", "rights have been returned"),
    ("This document has been archived because it contains outdated information. It should not be consulted for clinical use.",
     "RETIRED: Technical Update", "outdated information"),
    ("This corrigendum has been retracted. Three instances of figure panel duplication exist in its corrigendum as well.",
     "RETRACTED: Corrigendum", "figure panel duplication"),
    ("Reason: This article has been retracted at the request of the editor as the authors have plagiarised part of several papers.",
     "Retraction notice", "plagiarised"),
    ("Our paper reported transcriptional activation of MAP65. We recently identified mistakes affecting the figures. "
     "We believe that the most responsible course of action is to retract the paper. We sincerely apologize.",
     "RETRACTED: A PLETHORA-Auxin Transcription Module", "identified mistakes"),
    ("BBRC has been targeted by a scheme to defraud our editors, reviewers and readers with falsified author information. "
     "The manuscript has been withdrawn. We consider such abuse unethical.",
     "WITHDRAWN: Identification of meteorin", "falsified author information"),
    ("For legal reasons this article has been removed by the publisher.", "A scientific applications study", "legal reasons"),
    ("This article was originally part of a print supplement. To avoid duplicate publication, this article has been withdrawn. "
     "This action in no way reflects any error or fault on the part of the authors.",
     "WITHDRAWN: A Comparison of Internet Monitoring", "no way reflects"),
])
def test_editorial_metadata_families_keep_their_primary_reason(description, title, passage):
    result = extract.extract(response(description, title=title), kind="both")
    assert passage in result.notice
    assert result.notice_method == "els-description"


def test_bibliographic_re_opening_is_corroborated_by_the_own_title():
    title = "Characterization of long-term survivors of human immunodeficiency virus type 1 infection"
    description = (f"Re: Cao et al., {title}, Immunology Letters, 51 (1996) 7-13. "
                   "The authors, editors and Publisher apologise for a publication error and inform you that "
                   "the paper referred to above has been retracted by the authors, due to an earlier publication.")
    result = extract.extract(response(description, title="RETRACTED: " + title), kind="both")
    assert "publication error" in result.notice and "earlier publication" in result.notice


@pytest.mark.parametrize("description", [
    "This article reviews why another paper was withdrawn by the editors because of fabricated data.",
    "This article discusses another study that has been retracted after an investigation.",
    "The article 10.1006/other was retracted because of fabricated data.",
    "The authors have decided to withdraw the blood samples at five-minute intervals.",
])
def test_status_title_alone_cannot_make_scientific_metadata_a_notice(description):
    assert not extract.extract(response(description, title="RETRACTED: Target publication")).notice


def test_status_title_does_not_override_a_hypothetical_statement():
    description = "If this article has been retracted, the authors may submit a correction."
    assert not extract.extract(response(description, title="RETRACTED: Target publication")).notice
