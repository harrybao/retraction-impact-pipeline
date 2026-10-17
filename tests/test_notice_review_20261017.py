"""Two notice regressions found by the pre-rerun review of 17 October 2026.

Both were measured on stored pages before the fix (21,385 changed notices
sampled, then every Springer, Nature, BMC and Taylor & Francis document
replayed): a Springer/Nature change-history entry that only logs an amendment
replaced the statement on 11 pages and a chapter heading on 3, and Taylor &
Francis notices began one paragraph after their reason on 9.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rwpipe import extract  # noqa: E402


STATEMENT = ("The editors have retracted this article because questions were raised regarding "
             "the manner in which the data in this paper have been processed and analysed.")


def page(history: str, body: str) -> str:
    return (f'<html><body><div id="change-history-content"><p>{history}</p></div>'
            f"{body}</body></html>")


def test_an_amendment_logged_in_change_history_does_not_replace_the_statement():
    # 10.1038/s41586-022-05294-9: the entry was the whole notice before the fix.
    result = extract.extract(page(
        "The Retraction Note was updated to reflect reception of statements of "
        "disagreement from all authors.",
        f"<p>Retraction Note: Room-temperature superconductivity</p><p>{STATEMENT}</p>"),
        kind="retraction")
    assert "have retracted this article" in result.notice
    assert result.notice_method != "section:change-history"


def test_each_amendment_wording_from_the_review_is_recognised():
    for line in [
        "The original online version of this article was revised due to updates in the retraction note.",
        "The initial version of this retraction note did not carry the editor's approval and was "
        "therefore corrected to the current version.",
        "This retraction note has been updated because the wrong retraction note was published.",
        "The Retraction Note was amended to clarify authors that disagree with the Retraction.",
        "This Retraction Note was corrected to update the first author's affiliation.",
        "The original retraction notice was inaccurate and has been updated.",
        "The mistake of retraction note formatting has been fixed.",
        "Editor's Note: Readers are informed that the revised manuscript referred to in the "
        "retraction notice for this article is now published.",
    ]:
        assert extract._NOTE_AMENDMENT.search(line), line


def test_an_expression_of_concern_in_change_history_is_still_the_notice():
    # 120 of the 169 decision-less entries read are editor's notes of this kind.
    note = ("Editor's Note: Readers are alerted that the reliability of data presented in this "
            "article is currently in question. Appropriate editorial action will be taken once "
            "this matter is resolved.")
    result = extract.extract(page(note, "<p>Original research article text.</p>"), kind="original")
    assert result.notice == note
    assert result.notice_method == "section:change-history"
    assert not extract._NOTE_AMENDMENT.search(note)


def test_a_chapter_heading_in_change_history_gives_way_to_the_statement():
    # 10.1007/978-3-030-18854-2_6
    result = extract.extract(page(
        "Retraction Note to: Chapter “Investigation on Spark Erosion Machining” in: "
        "K. Gupta (ed.), Materials Forming, Machining and Post Processing",
        "<p>The original version of this chapter was retracted: The retraction note to this "
        "chapter is available at https://doi.org/10.1007/978-3-030-18854-2_12</p>"),
        kind="original")
    # The page cue now finds the statement; the heading may stay above it.
    assert "The original version of this chapter was retracted" in result.notice
    assert result.notice_method != "section:change-history"


def test_a_statement_with_its_decision_in_change_history_is_kept():
    result = extract.extract(page(STATEMENT, "<p>Body.</p>"), kind="retraction")
    assert result.notice == STATEMENT
    assert result.notice_method == "section:change-history"


def test_taylor_and_francis_notice_starts_at_its_finding():
    # 10.1080/01441647.2012.664813: the notice began at "As a result".
    finding = ("Upon a thorough investigation, it has been determined that the paper titled "
               "“Inaccuracy in Traffic Forecasts” published in Transport Reviews, 2006, is "
               "substantially similar to that of an already published article.")
    decision = ("As a result, the article published in Transport Reviews has been retracted and "
                "should not be cited in the electronic or print version of the journal.")
    notice = extract._narrow([finding, decision])
    assert notice.startswith("Upon a thorough investigation")
    assert decision in notice


def test_the_finding_wording_alone_does_not_start_a_notice():
    # START_HINT only moves a found cue back; it is never a cue itself.
    assert extract._narrow(["Upon a thorough investigation, it has been determined that the "
                            "paper titled X used a flawed method."]) == ""
