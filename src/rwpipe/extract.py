"""Turn a captured page into the text Stage 3 needs.

What is wanted is small: the title, the abstract, the body, and - for a
retraction notice - the sentence or two that says why the paper was withdrawn.
What is in the way is a publisher's website: navigation, cookie banners,
related-article panels, reference managers, advertising, and on some sites more
markup than prose.

Two decisions shape this module.

**Generic first, publisher rules second.** The corpus spans 1,348 hosts. Rules
per publisher do not scale to that and rot when a site is redesigned;
`trafilatura` handles the long tail by looking at text density rather than
class names. Targeted selectors are added only where the generic pass
demonstrably fails, and each one is justified by a measurement rather than a
guess.

**The notice is the point.** For most of this corpus the retraction notice is
two paragraphs, and a body-text extractor tuned for articles discards short
pages as boilerplate. The notice is extracted separately and is allowed to be
short - it is the document that says *why*, and Stage 3's central question
cannot be answered without it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

WS = re.compile(r"[ \t\u00a0]+")
BLANKS = re.compile(r"\n{3,}")

# v15 (2026-10-13). Until v14 "withdrawn" followed by at / by / following /
# due to / after / because was a cue by itself, and a methods section says
# exactly that: "samples were withdrawn at predetermined intervals", "blood
# was withdrawn by retro orbital puncture", "two patients were withdrawn due
# to the adverse effects".
#
# "Withdrawn" now counts only where the sentence says that a publication was
# withdrawn, or by whom or why in words a methods section never uses. Each
# alternative is covered by real publisher statements in test_extract_v15.py.
# The read-only full-corpus rehearsal checks both lost notices and changed ones.
#
# Every alternative contains v14's phrase, literal spaces included, so v15
# never finds a cue where v14 found none: a paragraph with a v15 match always
# had a v14 match. The difference in the other direction is the subject
# alternative, which starts the match at the subject ("this article be
# withdrawn because") a few words before v14's start.
_W_PREP = r"withdrawn (?:at|by|following|due to|after|because)"

WITHDRAWN_CUE = (
    # Verb tense alone does not identify a publication: experimental samples
    # and drugs can also "have been withdrawn".
    # A publication as the subject: "This article is withdrawn by the author",
    # "THIS ARTICLE WAS WITHDRAWN BY THE PUBLISHERS", "Ahead of Print article
    # withdrawn by publisher" (SAGE), "This review is temporarily withdrawn
    # following" (Cochrane), "requested that their article be withdrawn due
    # to", "The following three articles were withdrawn by". Samples, blood,
    # aliquots, patients and drugs are what a methods section withdraws.
    r"|(?:\b(?:this|the|that|their|our)\s+(?:above(?:-named)?\s+)?)?"
      r"\b(?:articles?|papers?|manuscripts?|chapters?|publications?|reviews?"
      r"|abstracts?|commentary|communications?|contribution|protocols?|overviews?|viewpoint\s+essays?)"
      r"(?:\s+(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/[^\s,;]+)?"
      r"(?:,\s*published\b[^;\n]{0,240}?,)?"
      r"(?:\s+by\s+[^\n]{1,240}?\))?"
      r"(?:\s*\([^)]{0,200}\))?\s+"
      r"(?:(?:is|was|were|are|be|to be|has to be|had to be|is being|was being|has been|had been|have been)\s+)?"
      r"(?:\w+ly\s+)?" + _W_PREP
    # "It was withdrawn due to internal investigations into the origin of the
    # data" (Wiley, 10.1002/cpt.2171). Only with a reason: "it was slowly
    # withdrawn at 1 mm/min" or "after 10 min" is a needle in a methods section.
    + r"|\bit\s+(?:is|was)\s+(?:\w+ly\s+)?withdrawn due to internal investigations"
    # Who asked for it or decided it, where the subject is a title, a DOI or a
    # citation too long to reach: "... 41(2), 132-138 was withdrawn by the
    # Editors on August 9, 2010" (J-STAGE), "...), is withdrawn by the authors
    # due to updated data" (OUP), "Withdrawn at Author's request" (Thieme),
    # and Elsevier's "This article has been\nwithdrawn at the request of the
    # author(s)" as its text layer cuts it ("le has been", "cle has been").
    # A methods section withdraws "by syringe", "by cutting the tail", "by the
    # staff", "by the manufacturers", and its participants "can be withdrawn
    # at any time". "By the author" needs "is" or "was" before it: Scientific
    # Reports' consent statement reads "unless rejected by the Journal, or
    # withdrawn by the author", and a reference list "(now withdrawn by the
    # authors)".
    + r"|withdrawn (?:at|after|following|by) (?:the |a )?request (?:of|from) "
      r"(?:the |its )?(?:authors?|editors?|publishers?)\b"
      r"|withdrawn at (?:the )?(?:authors?|editors?|publishers?)\b[^.\n]{0,40}?request"
      r"|withdrawn by (?:mutual )?agreement (?:between|among|with|of)\b"
      r"(?=[^.\n]{0,120}\b(?:editors?|publishers?)\b)"
      r"|withdrawn by (?:the |its )?(?:publishers?|editors?|editor-in-chief|editorial|journal)\b"
      r"|withdrawn by authors?\b"
      r"|(?:(?<=is\s)|(?<=was\s))withdrawn by (?:the |its |all (?:the )?)?(?:\w+\s+)?authors?\b"
    # Springer's OnlineFirst formula: "This article has been published
    # OnlineFirst, but is withdrawn due to duplicate publication."
    + r"|(?<=first,\sbut\sis\s)" + _W_PREP
    # A reason no methods section gives: "[DOI:10.1007/s12149-011-0501-3] is
    # withdrawn due to prior publication", "Withdrawn due to non-compliance
    # with The Cochrane Collaboration's Commercial Sponsorship Policy", "one of
    # the largest studies on which this analysis was based was withdrawn due
    # to fraudulent data" (Open Forum Infectious Diseases).
    + r"|withdrawn (?:due to|because of) (?:the |a )?(?:prior|previous|duplicate"
      r"|simultaneous|repetitive|redundant) publication"
      r"|withdrawn due to non.compliance with (?:the )?cochrane"
      r"|withdrawn (?:due to|because of) (?:\w+\s+){0,2}?(?:plagiarism|fraudulent"
      r"|fabricat|falsif|misconduct|copyright)"
    # arXiv comment for 10.1103/physrevd.84.084020.
    + r"|\bwithdrawn because of (?:a |the )?(?:crucial )?sign error\b"
    # European Review prints the full quoted publication/citation before the verb.
    + r"|(?:this|the)(?: above)? (?:article|paper)\s+[\"'“‘][^\n]{1,600}?[\"'”’]\s+"
      r"(?:has been|was|is)\s+" + _W_PREP
)

# Wording publishers use to state a withdrawal. Deliberately broad: a missed
# notice is a record that drops out of Stage 3 entirely.
NOTICE_CUE = re.compile(
    # Built from the wording found on real pages, not from what sounds likely.
    # Dovepress writes "At the author's request, we, the Editors and Publisher
    # of X, have retracted..." - which matched none of the first version's
    # patterns: the request clause is reversed, and "the publisher" is actually
    # "and Publisher". Two pages carrying an explicit reason came out with an
    # empty notice because of it. Apostrophes are matched in both the straight
    # and typographic forms; publishers use both, often on the same site.
    r"(this article has been (retracted|withdrawn)"
    # v12. IOP prints "This article (and all articles in the proceedings volume
    # relating to the same conference) has been retracted by IOP Publishing":
    # a 76-character parenthesis, past the 60 allowed further down, so the
    # leftmost match was the bare "has been retracted" below, and 839 notices
    # began mid-sentence. With this alternative the match starts at "This
    # article", since re.search takes the leftmost starting position.
    r"|(?:this|the) (?:article|paper|manuscript)"
    r"(?:\s*\([^)]{0,200}\))?\s+has been (?:retracted|withdrawn)"
    # Springer writes the statement in German first and English second: "Der
    # Beitrag wurde aufgrund einer Urheberrechtsverletzung vom
    # korrespondierenden Autor zurückgezogen. This article is retracted as
    # requested by the corresponding author". Neither half matched anything.
    r"|wurde[^.]{0,120}zur(?:ü|ue)ckgezogen"
    r"|(?:is|was) retracted as requested"
    r"|has been retracted"
    r"|retraction (note|notice|statement)"
    r"|notice of (retraction|concern)"
    r"|expression of concern"
    r"|withdrawal notice"
    r"|at the (author|editor|publisher)s?[’']?s? request"
    r"|at the request of the (author|editor|publisher)s?"
    r"|(editors?|publishers?|we)[^.]{0,80}have (retracted|withdrawn)"
    # v15: "withdrawn" followed by the same words is no longer a cue on its
    # own; WITHDRAWN_CUE above holds the wordings that replaced it.
    r"|retracted (at|by|following|due to|after|because)" + WITHDRAWN_CUE
    + r"|the (article|paper|following article) has been (retracted|withdrawn)"
    # Four wordings measured as missing on 2026-09-06, found because 182 XML
    # captures extracted to nothing at all and turned out to contain the
    # statement in words the list did not know. A phrase the cue misses is
    # missed everywhere, not only in the documents that fail visibly: 502
    # documents carried one of these and 395 of them had no notice.
    r"|(the )?publishers? regrets? that"
    r"|(article|paper|document|contribution) has been (temporarily )?removed"
    r"|has been archived because"
    r"|removed as a duplicate"
    # v10 (2026-09-11). Every phrase above is in the passive voice, and
    # publishers write the active one at least as often: "the PLOS ONE
    # Editors retract this article", "the Editor-in-Chief has retracted this
    # article", "the authors wish to retract their paper". Measured before
    # adding: 1,786 documents carried a statement in that voice and had no
    # notice at all - 1,147 PLOS and 459 Springer article pages that print
    # the retraction note on the page itself - and every sampled context was
    # a real statement. `withdraw` counts only as withdrew / withdrawn /
    # withdrawing: journal policy text says authors "may withdraw their
    # manuscript", which is not a withdrawal.
    r"|\b(?:retract(?:s|ed|ing)?|withdr(?:ew|awn|awing))\s+"
    r"(?:this|the|their|our)\s+(?:article|paper|manuscript|publication|chapter)\b"
    r"|withdrawal of (?:the|this) (?:paper|article|manuscript)"
    # Spandidos words every retraction as "the Editor has decided that this
    # paper should be retracted", which nothing matched, so its notices fell
    # back to "the whole page is the notice" - journal menu first - and 918
    # were cut at the cap. Case-sensitive on purpose: an Elsevier PDF carries
    # a RETRACTED watermark across its text, and "an aspect that should be
    # RETRACTED M. Sheikhalishahi" is the watermark interrupting a sentence.
    # A bare "must be withdrawn" is left out for the mirror-image reason: "the
    # microcatheter tip must be withdrawn" is a methods section.
    r"|(?-i:(?:should|must|will|would) be retracted)"
    r"|(?:paper|article|manuscript) (?:should|must|will|would) be withdrawn"
    # IEEE's plagiarism notice has a name of its own, and 'both' pages
    # carrying it were falling back to the whole page as their notice.
    r"|notice of violation of ieee publication principles"
    r"|(?:retracted|withdrawn) (?:up)?on (?:the )?request"
    r"|(?:manuscript|chapter|publication) has been (?:retracted|withdrawn)"
    r"|(?:withdrawn|retracted) as a duplicate"
    # Measured on the first v10 sample run: "Abstract: This article was
    # retracted" (J-STAGE), "This article was retracted as it was printed by
    # error" (Springer) and "the Accepted Manuscript version of this article
    # (published on 29 April 2020) was withdrawn on 8 June 2020 due to concerns
    # over authorship" (Portland Press) are statements no phrase above matched.
    # The subject has to be the article itself, with at most a parenthesis
    # between, because "the article describes how the drug was withdrawn" is
    # not one.
    #
    # **A bare date is not a statement.** The second sample run found Springer
    # and Nature printing "This article was retracted on 10 April 2026" as a
    # status line at the top of the article page, above the abstract. Matched,
    # it started 79 of 1,590 sampled notices there and walked into the
    # abstract, leaving out the banner that says what happened. A dated form
    # counts only when a reason follows the date.
    r"|(?:this|the) (?:article|paper|manuscript)(?:\s*\([^)]{0,60}\))?\s+was (?:retracted|withdrawn)(?! on \d)"
    r"|(?:article|paper|manuscript)(?:\s*\([^)]{0,60}\))?\s+was (?:retracted|withdrawn) on \d{1,2} \w+ \d{4},? (?:due to|because|following|after|at the request)"
    r"|is a duplicate (?:version|publication) of"
    r"|readers are alerted that"
    # Springer Nature's editor's note for an investigation still open, found by
    # the third sample run on article pages that are their own notice: "The
    # Editor-in-Chief is currently investigating this article as concerns have
    # been raised ... Further editorial action will be taken as appropriate".
    # 26 v9 documents carried that sentence and no other cue.
    r"|(?:is|are) currently investigating this article"
    r"|editor[’']?s[’']? note:?\s*concerns? (?:have|has) been raised"
    r"|further editorial action (?:will|may) be taken)",
    re.I)

# Where a notice stops. A reference list following it is not part of it.
REF_START = re.compile(r"^(references?|bibliography|literature cited|"
                       r"author information|about this article|"
                       r"rights and permissions|additional information|"
                       r"supplementary|abbreviations?\s*:?\s*$|"
                       r"authors[’']? original submitted files(?: for images)?\s*:?\s*$)", re.I)

# Where a notice ends although no reference list follows it: the first line
# of the site's own furniture. v9 walked on to the 3,000-character cap on
# 5,272 notices (6.4%) - through Wiley's author list, Spandidos's citation
# widget, a Cureus PDF-download form, and the JSON of a page that ships as a
# script.
#
# **A marker ends the notice only if nothing after it is a statement.** The
# same lines also sit above real statements: on 357 of 12,777 stored notices
# a cue follows "First Published online:", and cutting there unconditionally
# would delete exactly the text the notice exists for. So the rule asks the
# page rather than trusting the marker - if a cue or a reason phrase appears
# anywhere below it, including in the rest of its own line, the marker is
# ignored and the walk goes on as v9 did. By construction the rule cannot
# remove a sentence carrying a cue.
NOTICE_END = re.compile(
    r"^\s*(?:"
    # Wiley: the banner, the date, then the author list - orcid, affiliation,
    # "Search for more papers by this author" - once per author.
    r"first published online:"
    r"|search for more papers by this author"
    # Dovepress and Taylor & Francis: a date line under the banner. Only a
    # short line: "published online on 16th February 2016 in Wiley Online
    # Library, has been retracted" is a statement that a fragment began with.
    r"|published online on \d[^.]{0,30}$"
    r"|this work is licensed under a"                     # European Review
    r"|this is a preview of subscription content"         # Springer paywall
    r"|similar content being viewed by others"            # Springer rail
    r"|view (?:retraction|expression of concern|correction)\s*$"   # PLOS link
    r"|copy and paste a formatted citation"               # Spandidos
    r"|spandidos publications style"
    r"|enter your email address to receive your free pdf download"  # Cureus
    r"|resend confirmation instructions"
    r"|how to cite text copied to clipboard"              # Wiley citation tool
    r"|ris \(procite, reference manager\)"
    r"|explore this article.s citation statements on"
    r"|qr code copied to clipboard"
    r"|unable to load citation data"
    r")"
    # A JSON fragment: Cureus ships the article as a script, and what follows
    # the notice there is the page's data model.
    r"|\"\s*,\s*\"[a-z_]+\"\s*:", re.I)

# Furniture that ends a notice whatever follows it. NOTICE_END above is
# conditional by design - a marker with a cue below it is usually part of the
# statement rather than its end - but a navigation rail is never inside a
# statement, and on Taylor & Francis and Wiley the rail is followed by
# related-article links whose titles carry cues, so the conditional rule never
# fired: the v12 sample removed the rail from 10 of 200 Taylor & Francis
# notices and 0 of 150 Wiley ones until this was separated out.
HARD_END = re.compile(
    r"^\s*(?:previous article view issue table of contents next article"
    r"|(?:previous\s*)?figure\s*next figure"
    r"|related research recommended articles)", re.I)

CITED_BY = re.compile(r"^\s*this article has been cited by the following publications[.]?"
                      r"(?:\s+this list is generated based on data provided by crossref[.]?)?\s*$", re.I)

# Reason wording that is not a cue. It never starts a notice; it only stops a
# marker from ending one, so a false match here costs a cut, never text.
# "The retraction has been agreed due to unattributed overlap" is Wiley's
# second sentence, and on one stored page it sat below "First Published
# online:" while the first sentence, which carries the cue, sat above it.
REASON_HINT = re.compile(
    r"the retraction (?:has been|was) agreed"
    r"|concerns? (?:were|was|have been|had been) raised"
    r"|unattributed overlap"
    r"|compromised peer[- ]?review"
    r"|(?:peer[- ]?review|review process) (?:was|had been|has been) compromised"
    r"|(?:image|figure|data) (?:duplication|manipulation|fabrication)"
    r"|duplicat(?:ed|ion of) (?:images?|figures?|data)"
    r"|no longer (?:has|have) confidence"
    r"|(?:editors?|publishers?) (?:has|have) (?:decided|concluded)"
    r"|apologi[sz]es? (?:to the read|for any inconvenience)", re.I)

# How a statement opens when its cue comes at the end. The first v10 sample run
# found notices that began at their own last paragraph: PLOS and Dovepress write
# "Following the publication of this article, concerns were raised ..." and only
# paragraphs later "... the Editors retract this article", and Spandidos opens
# "it was drawn to the Editors' attention" before "should be retracted". v9 had
# kept those openings by accident - no cue matched, so the whole page became the
# notice - and the new cues moved the start to the end. These phrases never start
# a notice on their own; they only let a notice that has found its cue reach back
# to where the statement begins (see _opening).
START_HINT = re.compile(
    REASON_HINT.pattern
    + r"|following (?:the )?publication of (?:this|the)(?: above| aforementioned)? (?:article|paper)"
    + r"|after (?:this|the) (?:article|paper)(?: \[\s*\d+\s*\])? was published"
    + r"|(?:drawn|brought) to (?:the|our) (?:\w+[’']?s? )?attention"
    + r"|it (?:has|had) come to (?:the|our) attention"
    + r"|concerns? (?:has|have) been raised"
    + r"|(?:this|the) (?:paper|article)\b.{0,700}\b(?:plagiarized|plagiarised)"
    + r"|(?:this|the) (?:paper|article) has been found to contain"
    # Taylor & Francis states the finding a paragraph before the decision:
    # "Upon a thorough investigation, it has been determined that the paper
    # titled ... is substantially similar to ..." and then "As a result, the
    # article ... has been retracted". Without this the notice began at "As a
    # result" and lost the reason (10.1080/01441647.2012.664813 and three
    # more; review of 2026-10-17).
    + r"|upon (?:a |an )?(?:thorough|careful|full|detailed|further) (?:investigation|review|examination)"
    + r"|it (?:has|had) been determined that (?:the|this) (?:paper|article)", re.I)

# Structural noise that survives text extraction on some sites.
# An escaped line break, as opposed to the opening of a LaTeX command: see
# _clean. \nabla, \nu, \neq and \newline all begin with these two characters.
ESCAPED_BREAK = re.compile(r"\\n(?![a-z])")

JUNK_LINE = re.compile(
    r"^(cookie|we use cookies|accept all|manage (cookies|preferences)|"
    r"skip to (main )?content|sign in|log in|register|subscribe|"
    r"download pdf|view pdf|share|cite|export citation|"
    r"back to top|privacy policy|terms (and|of)|advertisement)\b", re.I)


@dataclass
class Extracted:
    title: str = ""
    abstract: str = ""
    body: str = ""
    notice: str = ""
    notice_method: str = "none"
    references: int = 0
    method: str = ""
    error: str | None = None

    @property
    def chars(self) -> int:
        return len(self.body)


# Publishers print the visiting client's own IP address into their page
# furniture, and the extractor was storing it as article text. 142 documents
# across 7 hosts captured this machine's address: Thieme writes "Your Current
# IP Address: ...", the PubFactory platform footers it as "[...] - ..." on
# Brill, thejns.org and Human Kinetics, Springer leaves it bare in the footer
# nav, and a Cloudflare 523 page on liebertonline.com printed it inside an
# error notice. 77 landed in a *retraction notice*, which is published in
# stage2_notices.csv.gz and was on its way to a shared repository.
#
# **Redacting every IPv4 would be wrong**, and measuring before writing the
# rule is what caught it. 564 documents carry an IPv4-shaped string; only 142
# are an address. The other 422 are Enzyme Commission numbers - `3.2.1.4` is
# cellulase, `1.15.1.1` is superoxide dismutase - in a corpus that is largely
# biomedical. A blanket rule would have silently deleted enzyme identifiers
# from 422 papers to solve a problem in 142.
#
# So the match has to be justified by its context: an IPv4 is redacted only
# where the surrounding text says it is an address, or where it is one of the
# addresses this project is known to go out from. An EC number never appears
# after the words "IP address".
_IPV4 = (r"(?<![\d.])"
         r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
         r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
         r"(?![\d.])")

IP_BARE = re.compile(_IPV4)

# Consent-management widgets, removed from the DOM before any text is taken.
#
# 77 Thieme notices ran to the 3,000-character cap, and what filled them was a
# OneTrust cookie dialogue: "We Care About Your Privacy", the list of
# processing purposes, the list of vendor partners. _narrow walks forward from
# the retraction cue and stops only at a reference list, so a consent dialogue
# sitting after the statement is swallowed whole.
#
# **Only the consent widgets.** The first version of this rule also dropped
# <footer>, <nav> and <ins>, which looked like the same kind of furniture and
# was measured before shipping: on 300 sampled captures it emptied 6 real
# notices outright and halved 2 more, every one of them Springer or Nature.
# The cause is <ins> - the element means "inserted into a published document",
# which is exactly what a retraction notice is, and Springer uses it correctly
# while Thieme uses it for a recommendation rail. Dropping it would have
# destroyed the notices to remove the rail. <footer> and <nav> changed nothing
# either way and were dropped from the rule for having risk and no measured
# benefit. Narrowed to consent widgets alone: 300/300 notices byte-identical,
# and the four Thieme documents fall from 3,000 characters to 409-1,550.
#
# v10: Springer Nature's consent dialog is a <dialog class="cc-banner"
# data-cc-banner>, which none of the selectors above matched. Its text sat inside
# 6,808 v9 notices, between the page title and the statement.
CONSENT_SEL = ('[id*="onetrust"], [class*="ot-sdk"], [class*="optanon"], '
               '[id*="CybotCookiebot"], [id*="cookie-banner"], '
               '[class*="cookie-banner"], [id*="cookieConsent"], '
               '[class*="cookie-consent"], [aria-label*="cookieconsent" i], '
               '[data-cc-banner], dialog.cc-banner')

# The cue that makes an IPv4 on this line an address rather than an enzyme:
# "Your Current IP Address: x" (Thieme), "Your IP address x" (Global Science,
# Cloudflare error pages), "IP-Adresse" on German sites, and PubFactory's
# "Powered by PubFactory - [x] - x" footer, which prints it twice with no
# words at all around the second copy - so the decision is made per line
# rather than per match.
IP_CUE = re.compile(r"ip[\s\-]*(?:address|adresse)|pubfactory", re.I)

# Addresses this project is known to exit from. Redacted wherever they appear,
# with or without a cue, because these are the ones that must never be
# published; an EC number cannot collide with them. They are read from the
# environment (RWPIPE_KNOWN_EXITS, comma-separated) and never written in code,
# which is shipped. Without it, an echoed address is still removed whenever
# its line names it as an IP address (IP_CUE below).
KNOWN_EXITS = tuple(a.strip() for a in os.environ.get("RWPIPE_KNOWN_EXITS", "").split(",")
                    if a.strip())

REDACTED = "[ip removed]"


def _redact_ips(line: str) -> str:
    """Remove echoed client IPs without touching Enzyme Commission numbers."""
    for addr in KNOWN_EXITS:
        line = line.replace(addr, REDACTED)
    if not IP_CUE.search(line):
        return line
    # The line is talking about an IP address, so any IPv4 on it is one.
    return IP_BARE.sub(REDACTED, line)


def _clean(text: str, keep_stops: bool = False) -> str:
    """Drop chrome. `keep_stops` preserves the headings _narrow() stops at.

    `References` was in the junk list and was therefore deleted before
    _narrow() could use it as a boundary - the stop condition existed and
    could never fire, so a notice ran on into the reference list exactly as it
    had before the fix.
    """
    # v12. Cureus ships the article inside a JSON blob, so its page text
    # arrives with the two characters backslash-n where the line breaks are:
    # 222 notices were one unbroken line beginning with a literal escape.
    #
    # v13. Not every backslash-n is an escape. A LaTeX command begins the same
    # way, and v12 turned \nabla into "\ abla", \nu into "\ u" and \neq into
    # "\ eq" in the mathematics papers among the 322 bodies it rewrote. A
    # command carries on in lowercase letters and an escaped break does not,
    # so backslash-n is a break only when a lowercase letter does not follow.
    # "\nthe" therefore keeps its escape, which is the safe direction to err.
    #
    # v14. v13 also turned a lone backslash-r into a break, and \rho, \right
    # and \rangle begin with those two characters: 256 bodies, 171 of them
    # Springer and 70 Nature mathematics, came out with " ho" and " ight". A
    # carriage return is an escape only as the first half of \r\n.
    text = (text or "").replace("\\r\\n", "\n")
    text = ESCAPED_BREAK.sub("\n", text)
    lines = []
    for line in text.splitlines():
        line = WS.sub(" ", line).strip()
        if not line:
            continue
        if JUNK_LINE.match(line) and not (keep_stops and REF_START.match(line)):
            continue
        lines.append(_redact_ips(line))
    return BLANKS.sub("\n\n", "\n".join(lines)).strip()


def _title(soup: BeautifulSoup) -> str:
    for sel in ("h1.citation__title", "h1.article-title", "h1.c-article-title",
                "h1.document-title", "meta[name='citation_title']", "h1", "title"):
        el = soup.select_one(sel)
        if not el:
            continue
        t = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
        if t and len(t) > 8:
            return WS.sub(" ", t).strip()
    return ""


def _abstract(soup: BeautifulSoup) -> str:
    for sel in ("meta[name='citation_abstract']", "meta[name='description']",
                "section.abstract", "div.abstract", "div#Abs1-content",
                "div.article-section__abstract", "section#abstract"):
        el = soup.select_one(sel)
        if not el:
            continue
        t = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
        if t and len(t) > 80:
            return WS.sub(" ", t).strip()
    return ""


def _blocks(node) -> list[str]:
    """The page's own paragraphs, not arbitrary text lines.

    Splitting on newlines counted DOM fragments: a heading, a byline and a
    date each consumed one of the eight allowed, so on Dovepress the sentence
    giving the actual reason sat past the limit and never made it into the
    notice. Walking <p>/<div>/<section>/<li> asks the page where its
    paragraphs are instead of guessing.
    """
    if node is None:
        return []
    out = []
    for el in node.find_all(["p", "li", "section", "div"], recursive=True):
        if el.find(["p", "li"], recursive=False):
            continue                      # container; its children carry the text
        t = WS.sub(" ", el.get_text(" ", strip=True)).strip()
        if t and len(t) > 25:
            out.append(t)
    if not out:
        out = [x.strip() for x in node.get_text(chr(10), strip=True).split(chr(10))
               if len(x.strip()) > 25]
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


# A cue says the page is about a retraction. A DECISION says the withdrawal
# actually happened. v11 kept whatever the page route found even when that was
# a heading and an author list, and the audit of 2026-09-16 measured the cost:
# IEEE prints its statement in the body, so 7,629 IEEE documents - a tenth of
# the corpus - carried "Notice of Retraction: <title>" in `notice` while "After
# careful and considered review ... in violation of IEEE's Publication
# Principles" sat only in `body`, with 618 more on AIP, PMC and Elsevier.
# Heading wording is deliberately absent. "Notice of Retraction:" and
# "Retraction note" are cues - they say the page concerns a retraction - and
# IEEE's page route returns exactly that and nothing else. Counting them here
# would mean the fallback never fires for the 7,629 documents it exists for.
DECISION = re.compile(
    r"(?:has|have|is|are|was|were)\s+(?:been\s+)?(?:retracted|withdrawn|removed)"
    r"|(?:retract(?:s|ed|ing)?|withdr(?:ew|awn|awing))\s+"
    r"(?:this|the|their|our)\s+(?:article|paper|manuscript|publication|chapter)"
    r"|hereby retract|we retract|(?:editors?|publishers?) retract"
    # The subject has to be the article: "the microcatheter tip must be
    # withdrawn slowly" is a methods section, and "the flap must be
    # retracted" is surgery. The cue list learned this in v10.
    r"|(?:article|paper|manuscript|publication|chapter)\s+"
    r"(?:should|must|will|would) be (?:retracted|withdrawn)"
    # IEEE states the finding rather than the act: "this paper has been found
    # to be in violation of IEEE's Publication Principles".
    r"|in violation of ieee[’']?s? publication principles"
    r"|zur(?:ü|ue)ckgezogen", re.I)

# What a Springer or Nature change-history entry says when it is not the notice
# (review of 2026-10-17, 169 entries without a decision read). Most such
# entries are the notice for an expression of concern - "Editor's Note: Readers
# are alerted that ..." - and stay. Two kinds are not: a log line about the
# retraction note itself ("The Retraction Note was updated to reflect reception
# of statements of disagreement from all authors", 10.1038/s41586-022-05294-9;
# "The original online version of this article was revised due to updates in
# the retraction note"), and a chapter's bare heading ("Retraction Note to:
# Chapter ..." in place of "The original version of this chapter was
# retracted: ..."). Taken as the notice, both replaced the statement and its
# reason, on 16 pages. The page's other sections still hold the statement.
_NOTE_AMENDMENT = re.compile(
    r"\bretraction (?:note|notice)\b.{0,200}?\b(?:updated|amended|corrected|revised|fixed"
    r"|modified|inaccurate|referred to)\b"
    r"|\b(?:updated|amended|corrected|revised|fixed)\b.{0,80}?\bretraction (?:note|notice)\b"
    r"|^(?:the )?original (?:online )?version of this (?:article|chapter) was revised\b", re.I | re.S)
_CHAPTER_NOTE_HEADING = re.compile(r"^retraction note to\s*:?\s*chapter\b", re.I)

# Wiley's chat widget was the whole of 37 v11 notices - "AI Companion feature
# unavailable for retraction notices" and nothing else - and those rows were
# graded notice-only, so the pipeline asserted a statement where there is none.
_WIDGET_ONLY = re.compile(r"^\s*ai companion feature unavailable", re.I)

_END_PUNCT = re.compile(r"""[.!?]['")\]]?\s*$""")


def _unwrap(text: str) -> str:
    """Join lines a PDF broke mid-sentence, for the cue search only.

    A PDF carries the page's layout, not its sentences: IOP's statement arrives
    as "This article ... has been" / "retracted by IOP Publishing following an
    extensive investigation", and a cue spanning the break cannot match. Only
    the PDF route uses this. HTML and JATS keep one line per block, where a
    break is a real boundary.
    """
    out: list[str] = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if out and not _END_PUNCT.search(out[-1]):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return "\n".join(out)


def _narrow(paras: list[str], budget: int = 3000, reach: int = 8) -> str:
    """From the first cue, forward, stopping at a reference list.

    5,560 notices sit at exactly the budget, which means the walk ran past the
    end of the statement and was cut by the limit rather than by a boundary.
    What it runs into is a related-articles rail - and on Spandidos every entry
    in that rail is prefixed "[Retracted]", so NOTICE_CUE matches each one and
    the walk has no reason to stop.

    A shape-based stop was tried and reverted: "three consecutive lines that
    look like titles" ends a real notice too, because notices routinely carry a
    heading, a journal name and an author line in a row. Measured on twelve
    capped notices it emptied three of them completely and cut a fourth from
    3,000 characters to 32, while still leaving six at the cap. It removed more
    signal than noise, which is the one thing a cleaning rule must not do.

    The cap therefore stands as the stop for these, and the truncation is
    visible - LENGTH(notice) = 3000 finds every one of them - rather than being
    hidden behind a rule that looked principled and was not.

    v10 adds the stop that rule was reaching for, with the condition it
    lacked (NOTICE_END, applied in _walk). The shape rule guessed that a run
    of short lines was not the notice. v10 ends the walk at a known piece of
    site furniture, and only when nothing below it could be the notice.
    """
    cutoff = next((i for i, para in enumerate(paras) if CITED_BY.match(para)), len(paras))
    paras = paras[:cutoff]
    hits = [i for i, x in enumerate(paras) if NOTICE_CUE.search(x)]
    if not hits:
        return ""
    return _walk(paras, _opening(paras, hits[0], reach, budget), budget)


def _opening(paras: list[str], cue: int, reach: int = 8, budget: int = 3000) -> int:
    """Where the statement holding paragraph `cue` begins.

    The cue phrase is often the conclusion ("... the Editors retract this
    article") with the reason in the paragraphs above it. Look back at most
    `reach` paragraphs, stopping at a reference list or a piece of furniture,
    and start at the earliest one that reads like the opening of a statement
    (START_HINT). Without such a paragraph the cue stays the start, so this
    never moves a notice back onto text that does not look like a statement.

    Never so far back that the cue itself would fall outside the budget. The
    third sample run found PLOS notices whose opening sits more than eight
    paragraphs above the decision, with a figure-by-figure list between. A
    wider look-back recovered the opening on 5 of 8 of them; on the other 3 the
    list is longer than the budget, and the notice keeps the decision rather
    than trading it for the opening.
    """
    start, size = cue, len(paras[cue])
    for j in range(cue - 1, max(-1, cue - 1 - reach), -1):
        para = paras[j]
        if REF_START.match(para) or NOTICE_END.search(para):
            break
        size += len(para)
        if size > budget:
            break
        if START_HINT.search(para):
            start = j
    return start


def _walk(paras: list[str], start: int, budget: int = 3000) -> str:
    """Paragraphs from `start` until a boundary or the budget.

    Two boundaries: a reference list (REF_START), and site furniture
    (NOTICE_END) with no cue or reason wording anywhere below it - the rest of
    the marker's own line included, since a fragment can open with marker
    words and still be the statement.

    Neither fires on the first paragraph. That is the cue line or the top of a
    notice page, and a heading that opens with "Additional information" is
    still where the notice starts; v9 returned nothing for it.
    """
    signal = [i for i, x in enumerate(paras)
              if NOTICE_CUE.search(x) or REASON_HINT.search(x)]
    last = signal[-1] if signal else -1
    out: list[str] = []
    size = 0
    for j in range(start, len(paras)):
        para = paras[j]
        if out:
            if REF_START.match(para) or HARD_END.match(para) or CITED_BY.match(para):
                break
            m = NOTICE_END.search(para)
            if m and last <= j:
                rest = para[:m.start()] + " " + para[m.end():]
                if not (NOTICE_CUE.search(rest) or REASON_HINT.search(rest)):
                    break
        out.append(para)
        size += len(para)
        if size > budget:
            break
    return chr(10).join(out)[:budget]


def _keep_line(line: str) -> bool:
    """Long enough to be text, or short and a boundary the walk must see.

    "References" is ten characters, so a >25 filter deleted the very heading
    _narrow stops at - the same mistake as the junk list, one layer further
    down, and the symptom was identical: notices running to the
    3,000-character budget. v10's markers need the same exemption: "View
    retraction" is fifteen.
    """
    return len(line) > 25 or bool(REF_START.match(line) or NOTICE_END.search(line))


def _body_as_notice(body: str) -> str:
    """A notice page's own text as its notice, within the same boundaries."""
    paras = [x for x in body.split(chr(10)) if x.strip() and _keep_line(x.strip())]
    text = _walk(paras, 0)
    return text if len(text) > 80 else ""


# Documents whose own text may stand as their notice when no statement is
# found in it. v9 also allowed 'both' - a page that is the article and is
# recorded as its own notice - and 1,489 such pages had their whole text
# stored as the notice. Against the v10 cues, 883 of them carry a statement
# after all. Of the 606 that do not, a sample of 136 was mostly not a notice
# by any reading: a login form, a paywall, a citation widget, a title and
# nothing else, or the article itself - a 50,907-character Cochrane review
# and a 24,682-character PMC paper were each stored as their own retraction
# notice. About one in eight was a real statement in wording no cue knows.
# For a 'both' page with no statement found, the honest notice is none.
_WHOLE_PAGE_ROLES = ("retraction",)

# Elements a browser starts on a new line. Everything else - a link, an
# italic journal name, a superscript citation - belongs to the sentence it
# sits in.
_BLOCK_TAGS = frozenset((
    "address", "article", "aside", "blockquote", "br", "caption", "dd",
    "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
    "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td",
    "tfoot", "th", "thead", "title", "tr", "ul"))
_CLOSE = object()


def _page_text(soup) -> str:
    """The page as text, one line per block, with sentences left whole.

    `get_text("\\n")` breaks the line at every string boundary, including the
    ones inside a sentence. Wiley's statement reads "The above article,
    published online on 12 March 2020 in Wiley Online Library
    (<a>wileyonlinelibrary.com</a>), has been retracted by agreement ..." -
    three strings, so three lines, and the notice began at the one holding the
    cue: "), has been retracted by agreement". Measured on the stored v9
    notices: 14,649 of the 59,309 taken from page text (24.7%) began in the
    middle of a paragraph - 9,809 of 12,199 on Springer, 1,507 of 1,631 on
    Nature - and the short fragments between, a journal name or a reference
    number, were dropped by the 25-character filter on top.

    Iterative, not recursive: publisher pages nest deeply enough to reach the
    recursion limit. Only the string types get_text() reads are kept, so a
    comment or a doctype does not become text here either.

    The <head> is left out. Its <title> repeats the page heading and began
    17,807 of the 59,309 v9 notices taken from page text (30.0%) - IEEE 9,496,
    Springer 5,884 - carrying whatever the page prints between the <title> and
    its heading, a consent dialog on Springer and breadcrumbs on IEEE, into the
    notice.
    """
    from bs4.element import CData, NavigableString, Tag
    text_types = (NavigableString, CData)
    out: list[str] = []
    stack: list = [soup]
    while stack:
        node = stack.pop()
        if node is _CLOSE:
            out.append("\x00")
        elif type(node) in text_types:
            out.append(str(node))
        elif isinstance(node, Tag):
            # A dropdown is never content. v10 read European Review's topic
            # menu - one <select> whose options include "Expression of
            # concern" - as a 1,605-character line carrying a cue, and 215 of
            # 589 notices there began at it (v11).
            if node.name in ("head", "title", "select"):
                continue
            if node.name in _BLOCK_TAGS:
                out.append("\x00")
                stack.append(_CLOSE)
            stack.extend(reversed(node.contents))
    text = re.sub(r"\s+", " ", "".join(out))
    return chr(10).join(x.strip() for x in text.split("\x00") if x.strip())


def _section_notice(node, reach: int = 8) -> str:
    text = _clean(_page_text(node), keep_stops=True)
    return _narrow([p for p in text.splitlines() if p.strip()], reach=reach)


def _editorial_decision(text: str) -> bool:
    return bool(DECISION.search(text) or re.search(
        r"\b(?:are retracting|have retracted) the following (?:article|paper)\b|"
        r"\b(?:is|are) being retracted\b|"
        r"\beditors? and (?:the )?publishers? wish to retract the following (?:article|paper)\b|"
        r"\bhas been administratively retracted\b", text, re.I))


def _editorial_opening(text: str) -> bool:
    """An actual own-publication decision, not a cited withdrawal or method."""
    if re.match(r"(?:this|the)(?: above-mentioned)? (?:article|paper) is being retracted\b", text, re.I):
        return True
    own = re.search(r"\bThe above(?:-mentioned)? (?:article|abstract),?\s+(?:first\s+)?published online\b", text, re.I)
    if own and own.start() and not NOTICE_CUE.search(text[:own.start()]):
        prefix = text[:own.start()]
        if (re.match(r"^(?:we|this (?:study|article|paper))\b", prefix, re.I)
                and re.search(r"\b(?:another|cited|other) (?:article|paper|study)\b", prefix, re.I)):
            return False
        text = text[own.start():]
    if _own_editorial_text(text):
        return True
    cue = NOTICE_CUE.search(text)
    if not cue or not _editorial_decision(text):
        return False
    if re.match(r"(?:this|the)(?: above)? (?:article|paper)\s+(?:https?://(?:dx\.)?doi[.]org/)?10[.]\d{4,9}/", text, re.I):
        return False
    if re.search(r"\b(?:another|cited|other article|other paper|discuss(?:es|ed)?)\b", text[:cue.start()], re.I):
        return False
    if re.search(r"\b(?:if|unless|might|could|can)\b|\bmay(?:\s+be)?\s*$",
                 text[:cue.start()], re.I):
        return False
    return bool(re.match(
        r"(?:(?:this|the)(?: (?:above(?:-mentioned|-named)?|following))? "
        r"(?:article|paper|abstract|communication)\b|"
        r"(?:the )?editors?\b|retraction\s*:|"
        r"we,?\s+(?=[^.]{0,180}\b(?:editors?|publishers?)\b)|"
        r"at (?:the )?(?:authors?[’']?s? request|request of (?:the )?(?:authors?|editors?)))", text, re.I))


def _scoped_notice(soup, reach: int) -> tuple[str, str] | None:
    """Use the publisher's notice boundary before searching the whole article."""
    additional = soup.select_one("#additional-information-content")
    if additional:
        notice = _section_notice(additional, reach)
        # A richer on-page statement can follow an abbreviated change-history
        # entry. A plain link must not displace an earlier editorial warning.
        if (DECISION.search(notice) and len(notice) > 40
                and not re.match(r"(?:this article has been retracted[.]?\s*)?"
                                 r"(?:please see|a retraction note)", notice, re.I)):
            history = soup.select_one("#change-history-content")
            update = _section_notice(history, reach) if history else ""
            # A later, separately labelled update may add reasons or author responses.
            if (DECISION.search(update) and len(update) > 80
                    and "".join(update.split()) not in "".join(notice.split())
                    and not re.match(r"this article has been retracted[.]?\s*please see", update, re.I)):
                combined = notice + "\nChange history:\n" + update
                if len(combined) <= 3000:
                    return combined, "section:additional-information+change-history"
            return notice, "section:additional-information"

    for section in soup.select(".article-section__abstract .article-section__content, section#abstract"):
        # Thieme nests publication metadata and a recommendation rail in its abstract.
        if section.get("id") == "abstract" and section.select_one("#publication-history-container"):
            section = BeautifulSoup(str(section), "lxml")
            boundary = section.select_one("#publication-history-container")
            for sibling in list(boundary.next_siblings):
                sibling.extract()
            boundary.decompose()
        paragraphs = section.find_all("p")
        first = next((_clean(p.get_text(" ", strip=True)) for p in paragraphs
                      if not re.match(r"^DOI\s*:|^Retraction\s*:(?!.*\b(?:retracted|withdrawn)\b)",
                                      p.get_text(" ", strip=True), re.I)), "")
        heading = section.find(["h2", "h3"])
        labelled = heading and re.match(r"(?:retraction|withdrawal) notice", heading.get_text(" ", strip=True), re.I)
        editorial = any(_editorial_opening(_clean(p.get_text(" ", strip=True))) for p in paragraphs[:10])
        if (editorial or labelled) and _editorial_decision(section.get_text(" ", strip=True)):
            notice = _section_notice(section, reach)
            if notice:
                method = "abstract-statement" if first.lower().startswith("this communication") else "section:notice-abstract"
                return notice, method

    # Wiley also places standalone and appended decisions in full-text sections.
    for section in soup.select(".article-section__full .article-section__content, .header-note-content, .citation .pb-rich-text"):
        paragraphs = section.find_all("p")
        first = next((_clean(p.get_text(" ", strip=True)) for p in paragraphs
                      if not re.match(r"^DOI\s*:", p.get_text(" ", strip=True), re.I)), "")
        if first and any(_editorial_opening(_clean(p.get_text(" ", strip=True))) for p in paragraphs[:10]):
            notice = _section_notice(section, reach)
            if _editorial_decision(notice):
                return notice, "section:editorial-statement"

    # Taylor & Francis keeps inline decisions in an abstract or a labelled section.
    for section in soup.select(".hlFld-Abstract, .NLM_sec"):
        heading = section.find(["h2", "h3"])
        heading_text = heading.get_text(" ", strip=True) if heading else ""
        if re.match(r"^(?:references?|bibliography|literature cited)\b", heading_text, re.I) or CITED_BY.match(heading_text):
            continue
        labelled = heading and re.match(r"(?:statement of retraction|retraction(?: notice| statement)?|withdrawal notice)\b",
                                       heading_text, re.I)
        first = section.find("p")
        first_text = _clean(first.get_text(" ", strip=True)) if first else ""
        labelled = labelled or re.match(r"^(?:statement of retraction\b|retraction\s*:)", first_text, re.I)
        editorial = any(_editorial_opening(_clean(p.get_text(" ", strip=True))) for p in section.find_all("p")[:10])
        if labelled or editorial:
            fragment = BeautifulSoup(str(section), "lxml")
            # Some statements quote the original abstract before giving the reason.
            # Remove only an exact duplicate also published as this page's abstract.
            abstracts = {" ".join(p.get_text(" ", strip=True).split())
                         for p in soup.select(".hlFld-Abstract > p")}
            for p in fragment.find_all("p"):
                text = p.get_text(" ", strip=True)
                if re.match(r"^Abstract\s*:", text, re.I):
                    quoted = re.sub(r"^Abstract\s*:\s*", "", text, flags=re.I)
                    if " ".join(quoted.split()) in abstracts and not NOTICE_CUE.search(quoted):
                        p.decompose()
            notice = _section_notice(fragment, reach)
            if re.match(r"(?:this|the)(?: above-mentioned)? (?:article|paper) is being retracted\b", first_text, re.I):
                notice = _walk(_clean(_page_text(fragment), keep_stops=True).splitlines(), 0)
            if not notice and labelled and _editorial_decision(fragment.get_text(" ", strip=True)):
                notice = _walk(_clean(_page_text(fragment), keep_stops=True).splitlines(), 0)
            if _editorial_decision(notice):
                return notice, "section:editorial-statement"

    for entry in soup.select("article.tag-retraction-notice .entry-content"):
        fragment = BeautifulSoup(str(entry), "lxml")
        for tag in fragment.select(".cite-article, .post-categories, .related-posts-div, blockquote.wp-embedded-content, iframe.wp-embedded-content"):
            tag.decompose()
        for tag in fragment.find_all(["a", "button"]):
            if tag.get_text(" ", strip=True).lower() == "free pdf download":
                tag.decompose()
        notice = _section_notice(fragment, reach)
        if DECISION.search(notice):
            return notice, "section:notice-entry"

    history = soup.select_one("#change-history-content")
    if history:
        notice = _section_notice(history, reach)
        if notice and (DECISION.search(notice)
                       or not (_NOTE_AMENDMENT.search(notice) or _CHAPTER_NOTE_HEADING.match(notice))):
            return notice, "section:change-history"

    # Explicit editorial containers take priority over a separate status banner.
    for section in soup.select("div.article-notice, section.retraction, div.alert-retraction, div.c-article-retraction"):
        notice = _section_notice(section, reach)
        if DECISION.search(notice):
            return notice, "section:notice"

    for section in soup.select('article[data-is="retraction"] section#bodymatter'):
        notice = _section_notice(section, reach)
        if DECISION.search(notice):
            return notice, "section:editorial-body"

    for selector in (".article-retraction-banner__content", ".component-errata-widget .content-box-body",
                     ".article-relations-box .alert-content", ".notices-box"):
        for banner in soup.select(selector):
            notice = _clean(_page_text(banner), keep_stops=True)
            if NOTICE_CUE.search(notice) and DECISION.search(notice):
                return notice[:3000], "section:status"
    return None


def _notice(soup, body: str, kind: str = "original") -> tuple[str, str]:
    """The statement of withdrawal, and how it was found.

    Three routes, tried in order of how much they prove:

    1. A container the publisher marked as a retraction notice.
    2. The cue wording anywhere in the page.
    3. For a page that IS the notice, the body itself - a standalone notice
       has no banner to find, and requiring one returned an empty notice for a
       4,244-character page whose text said "unable to explain the image
       duplication, provide original data or verify the validity". Since v10
       only for a document whose sole role is the notice: _WHOLE_PAGE_ROLES.

    The method is stored, so a rule that starts misfiring can be found by
    querying for it rather than by re-reading 69,657 files.
    """
    # A page that is only the notice has no article above its statement for a
    # look-back to wander into, so it may look much further back. On an article
    # page a discussion section can say "concerns have been raised about
    # safety", and the look-back stays short.
    reach = 60 if kind == "retraction" else 8

    scoped = _scoped_notice(soup, reach)
    if scoped is not None:
        return scoped

    best, how = "", "none"

    for sel in ("div.retraction", "section.retraction", "div.article-notice",
                "div.alert-retraction", "div.c-article-retraction",
                "div[class*='retract']", "section[class*='retract']"):
        el = soup.select_one(sel)
        if not el:
            continue
        narrowed = _narrow(_blocks(el), reach=reach)
        if 40 < len(narrowed) < 3000:
            best, how = narrowed, f"selector:{sel}"
            break

    # Page-wide, work from the cleaned text rather than raw DOM blocks. A
    # navigation <div> has no <p> children, so it qualifies as a "block" and
    # arrives first - the notice then began "Skip to main content Journals Why
    # Publish With Us?". _clean strips that; keep_stops leaves the headings
    # _narrow needs as boundaries. Since v10 the text has one line per block
    # rather than one per string; see _page_text.
    if not best:
        page = _clean(_page_text(soup), keep_stops=True)
        lines = [x.strip() for x in page.split(chr(10)) if x.strip()]
        lines = [x for x in lines if _keep_line(x)]
        narrowed = _narrow(lines, reach=reach)
        if narrowed:
            best, how = narrowed, "cue"

    if best and _WIDGET_ONLY.search(best):
        best, how = "", "none"

    # The same search over the extracted body. Page text and body are two
    # readings of one page and they can disagree: on a Scientific Research page
    # the parsed markup yields one line while the body extractor recovers the
    # article and its statement. v9 reached that statement only by storing the
    # whole body as the notice, which v10 no longer does for a page that is also
    # the article.
    # v12: also when the page route found something that never says the paper
    # was withdrawn. A heading carrying the word "Retraction" is not the
    # statement, and on IEEE it is all the page route ever returns.
    if body and (not best or (not DECISION.search(best) and DECISION.search(body))):
        narrowed = _narrow([x.strip() for x in body.split(chr(10))
                            if x.strip() and _keep_line(x.strip())], reach=reach)
        if narrowed and (not best or DECISION.search(narrowed)):
            return narrowed, ("body-cue" if not best else "body-decision")

    if best:
        return best, how

    if kind in _WHOLE_PAGE_ROLES and body:
        # The whole page is the notice. Keep it, bounded, rather than record
        # "no reason found" for a document that is nothing but the reason.
        text = _body_as_notice(body)
        if text:
            return text, "body-is-notice"
    return "", "none"


def _references(soup: BeautifulSoup) -> int:
    for sel in ("ol.references li", "ul.references li", "div.references li",
                "section#references li", "li.c-article-references__item",
                "div[class*='reference'] li"):
        n = len(soup.select(sel))
        if n:
            return n
    return 0


_ESCAPED_LT = re.compile(r"\\u([0-9a-fA-F]{4})")
_SCRIPT_OPEN = re.compile(r"<script[^>]*>", re.I)
_SCRIPT_CLOSE = re.compile(r"</script\s*>", re.I)


def _unwrap_escaped_markup(html: str) -> str:
    """Some sites ship the article as a JavaScript string, not as markup.

    Cureus serves 334 KB in which the paper - Introduction, Discussion,
    References, all of it - is a JS string with its angle brackets written
    `\\u003cp\\u003e`. Stripping scripts, which every extractor must do, throws
    the article away with them, and what remains is 781 characters of navigation
    menu. That is indistinguishable from a refusal by every measure we have:
    HTTP 200, six figures of bytes, the right DOI, the right title.

    Measured on the 223 Cureus captures:

        as delivered                    781 chars of body
        after \\uXXXX decoding          781      (the markup is real now, but
                                                  it is still inside a script)
        after decoding AND entities  23,035 - 62,272

    Decoding is not sufficient on its own: the markup is real afterwards but
    still sits inside a <script>, which the extractor removes. An early version
    of this function relied on lxml recovering the subtree by accident - it did
    on Cureus, and not at all on a clean synthetic page with the same escaping,
    which is how a fix that works for one publisher and no other gets shipped.
    So the script *tags* are deleted while their contents are kept: the article
    becomes part of the document deterministically rather than by parser quirk.

    Applied only when the signal is unmistakable. Forty random captures from the
    rest of the archive contain zero escaped angle brackets, and documents that
    already extract cleanly produce byte-identical output with this in place.
    Genuine JavaScript left behind as text is not a problem the body extractors
    have: they select prose, and minified source is not prose.

    The distinction this protects is the one that nearly cost 223 files:
    "the publisher sent no article" and "our reader cannot see the article it
    sent" look the same from outside and are not the same thing.
    """
    if "data-react-props" in html and "articleHtml" in html:
        import json
        soup = BeautifulSoup(html, "lxml")
        decoded = False
        for host in soup.select("[data-react-props]"):
            try:
                data = json.loads(host["data-react-props"])
            except (ValueError, TypeError):
                continue
            sections = data.get("articleHtml") if isinstance(data, dict) else None
            if not isinstance(sections, dict):
                continue
            fragments = []
            for name in ("hero", "article", "figures", "authors"):
                value = sections.get(name)
                markup = value.get("html") if isinstance(value, dict) else value
                if isinstance(markup, str) and "<" in markup:
                    fragment = BeautifulSoup(markup, "lxml")
                    fragments.extend(list((fragment.body or fragment).contents))
            if fragments:
                host.clear()
                del host["data-react-props"]
                for fragment in fragments:
                    host.append(fragment)
                decoded = True
        if decoded:
            return str(soup)
    if html.count("\\u003c") < 20:
        return html
    import html as _html
    out = _html.unescape(_ESCAPED_LT.sub(
        lambda m: chr(int(m.group(1), 16)), html))
    out = _SCRIPT_OPEN.sub(" ", out)
    return _SCRIPT_CLOSE.sub(" ", out)



# --- publisher furniture ---------------------------------------------------
#
# 16,433 rows (23.4%) carried site chrome inside the body or the notice: cookie
# consent panels, "related articles" rails, IEEE's customer service numbers,
# copyright footers. It reads as prose to anything downstream, and Stage 3 has
# to compare a retraction notice against what the news said about it - a notice
# with two thousand characters of cookie policy in it is not a notice.
#
# The rule is line-based because the furniture is not all at the end. Measured:
# "Similar content being viewed by others" sits at a median of 14% into the
# text, between the abstract and the article body on Springer and Nature pages.
# Truncating there would have emptied 7,204 documents while reporting them
# clean - the same shape as every other failure in this project, a cut made on
# a marker whose position was assumed rather than checked.

# A line that is entirely furniture. Matched against the whole line, so a
# sentence that merely mentions cookies in an article about cookies survives.
_JUNK_LINE = re.compile(
    r"^\s*(?:"
    r"similar content being viewed by others"
    r"|skip to (?:main )?content"
    r"|main(?:\s*content)?"
    r"|(?:accept|manage|reject)\s+(?:all\s+)?cookies?"
    r"|manage consent preferences"
    r"|(?:strictly necessary|functional|performance|targeting|advertising) cookies"
    r"|we use cookies[^.]*\.?"
    r"|this website utili[sz]es technologies such as cookies.*"
    r"|communications preferences"
    r"|privacy (?:&|and) opting out of cookies"
    r"|access through your institution"
    r"|(?:us|worldwide)\s*(?:&|and)?\s*canada:\s*\+?[\d\s]+"
    # "Copyright (c) 2026 X" and "Copyright 2026 X" are both footers, and the
    # first was missed: the year was required immediately after the word.
    r"|copyright\s*(?:©|\(c\))?\s*\d{4}.*"
    r"|(?:©|\(c\))\s*\d{4}.*"
    r"|all rights (?:are )?reserved.*"
    r"|show (?:more|less)"
    r"|download (?:conference paper |chapter )?pdf"
    r"|view all subscription options.*"
    r"|the content on this site is intended for healthcare professionals\.?"
    r"|forgot your (?:username|password)\??"
    r"|(?:sign|log) in(?: to your account)?\.?"
    r"|register(?: now)?\.?"
    r"|institutional (?:login|access)\.?"
    r"|change password"
    r"|(?:old|new|confirm(?:ed)?) password"
    r")\s*$", re.I)

# A line from which everything after it is furniture. Only phrases measured to
# sit in the last quarter of the text qualify; a marker that appears mid-article
# belongs in _JUNK_LINE instead.
_TAIL_START = re.compile(
    r"^\s*(?:"
    r"discover the latest articles, books and news"
    r"|you may choose not to allow some types of cookies"
    r"|a (?:public charity|not-for-profit organization), ieee"
    r"|about this (?:journal|site)"
    r"|explore content"
    # A login form, a password-reset flow, or the rights footer that
    # precedes them. Wiley prints all three after the notice, and
    # nothing following "your password has been changed" is part of a
    # retraction statement.
    r"|password changed successfully"
    r"|your password has been changed"
    r"|please check your email for instructions on resetting"
    r"|enter your email address below"
    r"|can.?t sign in\??\s*forgot"
    r"|forgot your (?:username|password)"
    # Wiley writes "rights for", Wolters Kluwer writes "those for". One word
    # was the whole difference between a stripped footer and 1,753 documents
    # carrying one.
    r"|all rights reserved, including (?:rights|those) for text and data mining"
    r"|advertisers (?:&|and) corporate partners"
    r"|(?:log|sign) in to (?:wiley online library|your account)"
    r"|institutional (?:login|access)\b"
    # Ovid/Wolters Kluwer print the site footer and a consent panel after the
    # article: 283 of the 285 documents still carrying "we use cookies" are
    # one host with one banner shape, and it opens with these headings.
    r"|manage cookie preferences"
    r"|privacy preference center"
    r"|to give you the best possible experience we use cookies"
    r"|your california privacy choices"
    r")", re.I)

# Bodies that ARE the interface rather than merely ending with it. These
# cannot be handled by the truncation rule above: its marker sits at position
# 0, and the guard that stops a cut from emptying the text - correctly -
# refuses it, so the boilerplate survives as the whole body. Adding them there
# made the Wiley case worse, taking a 766-character body to 2,130.
#
# Measured 2026-09-08 across all 88,839 extracted documents: 70 Springer
# bodies are the cookie notice and nothing else (every one exactly 631
# characters) and 78 Wiley bodies open with the citation-manager panel
# (766-1074). For these the honest value is no body at all - the page was
# fetched, but no article was extracted from it.
INTERFACE_ONLY = re.compile(
    r"^\s*(?:"
    r"we use essential cookies to make sure the site can function"
    r"|if you have the appropriate software installed"
    r"|this website utilises technologies such as cookies"
    r")", re.I)

# What has to survive for a cut to be allowed. A guard against the patterns
# matching nearly everything - not a quality bar. Set low on purpose: a real
# retraction notice can be one sentence ("This article has been retracted."),
# and a floor high enough to protect a long notice would hand back the
# uncleaned version of every short one. Below these lengths the text is not
# usable either way, so keeping the original costs nothing and catches the
# case where a pattern has eaten the document.
_KEEP_BODY = 120
_KEEP_NOTICE = 40


def _strip_boilerplate(text: str, floor: int) -> str:
    """Remove site chrome, keeping the original if the result is implausible."""
    if not text:
        return text
    kept = []
    for line in text.split("\n"):
        if _TAIL_START.match(line):
            break
        if _JUNK_LINE.match(line):
            continue
        kept.append(line)
    out = "\n".join(kept).strip()
    # Two ways this can go wrong, and both mean "leave it alone": the patterns
    # ate nearly everything, or they ate the part that mattered.
    if len(out) < floor and len(text) >= floor:
        return text
    return out or text


def _notice_from_text(text: str, kind: str = "original") -> tuple[str, str]:
    """The retraction statement inside already-extracted text.

    `_notice` walks a parsed page; this walks prose. Same cues, so a notice is
    recognised identically whether it arrived as HTML or as JATS - two
    definitions of "this is the withdrawal statement" would be two datasets.
    """
    m = NOTICE_CUE.search(text)
    if not m:
        return "", "none"
    # Back to the start of the SENTENCE, not the line. A single-paragraph XML
    # abstract has no line breaks at all, so "the start of the line" was the
    # start of the document: 24 notices in 1,551 arrived carrying up to 1,410
    # characters of unrelated text in front of the statement - in one case an
    # entire erratum about a misspelled surname, sitting above a real
    # withdrawal notice on the same page.
    # Start at the cue. Measured on 730 notices: a lookback window of any size
    # finds sentence boundaries inside author affiliation blocks and running
    # headers, so 80 characters of window prepended junk to 11 notices, 120 to
    # 46, and 400 to 72. Zero prepends it to none. The cost is a lead-in clause
    # ("Following an investigation, ...") on the rare notice that has one, and
    # the cue phrases are themselves the opening of the statement in almost
    # every case.
    start = m.start()
    paras = text.splitlines()
    cue = text[:start].count("\n")
    opening = _opening(paras, cue, 60 if kind == "retraction" else 8)
    if opening < cue:
        return _walk(paras, opening), "cue"
    if START_HINT.match(text[:start]):
        start = 0
    if kind == "retraction":
        heading = re.search(r"\bremoval notice(?: to)?\b", text[:min(start, 180)], re.I)
        if heading and start - heading.start() < 1500:
            # Some legacy removal notices have only a disordered PDF text
            # layer. Retain its leading reason fragments without reordering.
            start = heading.start()
    out = _strip_boilerplate(text[start:start + 3000], _KEEP_NOTICE)
    return out.strip(), "cue"



# --- JATS ------------------------------------------------------------------
# PubMed Central's efetch endpoint returns JATS XML, and for Stages 2 and 3 it
# is a better artefact than a rendered page: title, abstract, body and
# references arrive already separated, so none of the six versions of guesswork
# above is needed. What it needs instead is to be recognised - 5,425 verified
# documents extracted as "no text/html part" because the extractor knew two
# containers and this is a third.
_JATS_MARK = re.compile(r"<\?xml|<!DOCTYPE\s+pmc-articleset|<article[ >]", re.I)


def _jats_text(node) -> str:
    """Readable text from a JATS element, one block per paragraph."""
    if node is None:
        return ""
    out = []
    for p in node.find_all(["p", "title", "label"]):
        t = p.get_text(" ", strip=True)
        if t:
            out.append(t)
    if not out:
        t = node.get_text(" ", strip=True)
        return t
    return "\n".join(out)


def _extract_jats(xml: str, kind: str) -> Extracted:
    try:
        soup = BeautifulSoup(xml, "lxml-xml")
    except Exception:                                      # noqa: BLE001
        soup = BeautifulSoup(xml, "html.parser")

    art = soup.find("article") or soup
    tg = art.find("title-group")
    title = ""
    if tg and tg.find("article-title"):
        title = tg.find("article-title").get_text(" ", strip=True)

    abstract = _jats_text(art.find("abstract"))
    body = _jats_text(art.find("body"))
    refs = len(art.find_all("ref"))

    # A retraction notice in PMC is a short article whose whole body is the
    # statement. Everything else goes through the same cue search as HTML, so
    # one rule decides what a notice is regardless of where the text came from.
    notice, method = "", "none"
    if body:
        notice, method = _notice_from_text(body, kind)
    if not notice and abstract:
        # A withdrawn article in PMC frequently has no <body> at all: the
        # statement is the whole of the abstract. Searching only the body left
        # those documents with no notice and, having neither, classified as
        # failures - 106 of them.
        notice, method = _notice_from_text(abstract, kind)
        if notice:
            method = "jats-abstract"
    if not notice and kind in _WHOLE_PAGE_ROLES and body:
        notice, method = body, "jats-body"

    return Extracted(title=title, abstract=abstract, body=body, notice=notice,
                     notice_method=method, references=refs, method="jats")



# --- Elsevier ---------------------------------------------------------------
# The Article Retrieval API returns Elsevier's own XML, which is neither HTML
# nor JATS. Two shapes come back and both are real full text: `<ce:sections>`
# with structured paragraphs, or `<xocs:rawtext>` as a plain dump - the API
# chooses, we do not. Without this path they would have failed exactly as the
# 5,425 PubMed Central captures did: recognised by no reader, filed as
# unreadable, and quietly absent from Stage 2.
_ELS_MARK = "full-text-retrieval-response"


_ELS_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_ELS_ONLINE_DATE = re.compile(
    r"^Available online\s*:?\s*(?:(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" +
    _ELS_MONTH + r"\s+\d{4}|" + _ELS_MONTH + r"\s+\d{4}|\d{4})\s*)?[.:]?\s*", re.I
)
_AUTHOR_DECISION = re.compile(
    r"^The authors\s+(?:have\s+)?(?:now\s+)?(?:decided|agreed|elected)\b"
    r"[^.!?]{0,100}?\bto\s+(?:retract|withdraw)\s+(?:this|the)(?: above)?\s+"
    r"(?:article|paper|publication)\b", re.I
)


def _own_editorial_text(text: str, doi: str = "", title: str = "") -> bool:
    """A metadata description must describe this publication's actual notice."""
    text = _ELS_ONLINE_DATE.sub("", text.strip())
    text = re.sub(r"^Reason\s*:\s*", "", text, flags=re.I)
    authors = _AUTHOR_DECISION.match(text)
    article = re.match(r"(?:(?:this|the|our)(?: above)? (?:article|paper|abstract|communication|review|"
                       r"manuscript|chapter|book/chapter|document|corrigendum|erratum|obituary)|"
                       r"these (?:articles|papers))\b", text, re.I)
    opening = authors or article
    publisher = re.match(r"(?:the )?publishers? regrets? that\b", text, re.I)
    cue = NOTICE_CUE.search(text)
    editorial_title = re.match(r"^(?:(?:chapter|appendix)\s+\w+\s+|\d+\s+)?"
                               r"(?:retracted|withdrawn|removed|retired|temporary removal|"
                               r"retraction|notice of retraction)\b", title.strip(), re.I)
    decision = DECISION.search(text) or re.search(
        r"\bhas been \w+ly (?:retracted|withdrawn)\b|"
        r"\b(?:has|have) therefore been (?:retracted|withdrawn|removed)\b|"
        r"\bthis document has been archived because\b", text, re.I)
    if not cue:
        return False
    if opening:
        subject = re.match(r"\s+(?:https?://(?:dx\.)?doi[.]org/)?(10[.]\d{4,9}/[^\s,;]+)",
                           text[opening.end():], re.I)
        if subject and (not doi or subject[1].rstrip(".").lower() != doi.lower()):
            return False
    foreign_context = re.search(
        r"\b(?:another|cited|other paper|other article|discuss(?:es|ed)?|reviews?)\b", text[:cue.start()], re.I)
    if foreign_context:
        return False
    if decision and (publisher or authors or (article and cue.start() <= 40)):
        return True
    # A status in the publication's own metadata corroborates older editorial
    # openings, including bibliographic leads and reasons before the decision.
    # A title alone is not enough: an actual cue and no foreign-paper or
    # hypothetical context are still required.
    if editorial_title and not re.search(r"\b(?:if|unless|may|might|could)\b", text[:cue.start()], re.I):
        return True
    if decision and re.match(r"For legal reasons this article\b", text, re.I) and cue.start() < 100:
        return True
    return False


def _extract_elsevier(xml: str, kind: str) -> Extracted:
    try:
        soup = BeautifulSoup(xml, "lxml-xml")
    except Exception:                                      # noqa: BLE001
        soup = BeautifulSoup(xml, "html.parser")

    def first(*names) -> str:
        for n in names:
            el = soup.find(n)
            if el:
                t = el.get_text(" ", strip=True)
                if t:
                    return t
        return ""

    title = first("dc:title", "title")
    abstract = first("ce:abstract", "dc:description")
    core = soup.find("coredata")
    description_el = core.find("dc:description") if core else None
    description = _ELS_ONLINE_DATE.sub("", _clean(description_el.get_text(" ", strip=True))) if description_el else ""
    doi = first("prism:doi")

    body = ""
    orig = soup.find("originalText")
    if orig:
        paras = [p.get_text(" ", strip=True) for p in orig.find_all("ce:para")]
        paras = [p for p in paras if p]
        if paras:
            body = "\n".join(paras)
        else:
            raw_el = orig.find("xocs:rawtext")
            if raw_el:
                body = raw_el.get_text("\n", strip=True)
    if not body:
        raw_el = soup.find("xocs:rawtext")
        if raw_el:
            body = raw_el.get_text("\n", strip=True)

    refs = len(soup.find_all("ce:bib-reference"))

    notice, method = "", "none"
    if body:
        notice, method = _notice_from_text(body, kind)
    complete_description = (description and _own_editorial_text(description, doi, title)
                            and not description.rstrip().endswith(("...", "\u2026"))
                            and bool(re.search(r"[.!?]\s*$", description)))
    if complete_description and (not notice or not orig or
                                 (not orig.find("ce:para") and not _own_editorial_text(notice, doi))):
        notice, method = _notice_from_text(description, kind)
        if notice:
            # The metadata is a clean target notice, whereas rawtext can carry
            # column fragments, mutilated requester names and running footers.
            notice, method = description[:3000].strip(), "els-description"
    if not notice and abstract and _own_editorial_text(abstract, doi, title):
        # Elsevier puts the withdrawal statement in dc:description for a
        # withdrawn article, where there is no body to search.
        notice, method = _notice_from_text(_ELS_ONLINE_DATE.sub("", _clean(abstract)), kind)
        if notice:
            method = "els-description"
    if not notice and kind in _WHOLE_PAGE_ROLES and body:
        # v10: a notice page with no cue is its own notice here as well. The
        # HTML route has done this since v5 and JATS since v7; this route
        # never did, so 352 Elsevier notice documents had no notice at all
        # and 277 of them were graded `full` on the length of the notice
        # itself. Some are corrigenda rather than retractions - that is what
        # the record's notice DOI resolves to, and the text says so.
        notice = _body_as_notice(body)
        if notice:
            method = "body-is-notice"

    return Extracted(title=title, abstract=abstract, body=body, notice=notice,
                     notice_method=method, references=refs, method="elsevier")


def extract(html: str, *, kind: str = "original") -> Extracted:
    """Pull the useful text out of one captured page."""
    if not html:
        return Extracted(error="empty html")
    if re.match(r"\s*<html\b", html, re.I) and "data-rwpipe-format" in html[:512]:
        root = BeautifulSoup(html[:512], "html.parser").find("html")
        if root and root.get("data-rwpipe-format") == "pdf-text":
            from .pdftext import extract_capture
            return extract_capture(html, kind)
    if _ELS_MARK in html[:2_000]:
        return _extract_elsevier(html, kind)
    if _JATS_MARK.match(html.lstrip()[:120]) and "<article-title" in html[:400_000]:
        return _extract_jats(html, kind)
    html = _unwrap_escaped_markup(html)
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as exc:                               # noqa: BLE001
        return Extracted(error=f"soup: {type(exc).__name__}")

    for tag in soup(["script", "style", "noscript", "svg", "form"]):
        tag.decompose()
    for tag in soup.select(CONSENT_SEL):
        tag.decompose()

    removed_pointer = False
    removed_embed = False
    for tag in soup.select("div#corrections-tab, div#retractions-tab"):
        label = tag.get_text(" ", strip=True).lower()
        if (tag.select_one(".article-card, .corrections__articles-box") or
                "the following corrections have been issued for this article" in label):
            tag.decompose()
            removed_pointer = True
    for tag in soup.select(".product-citations-modal, #kindleModal, #dropboxModal, #googleDriveModal"):
        tag.decompose()
        removed_pointer = True
    for tag in soup.select(".linked-content-item"):
        if re.match(r"please note a (?:retraction|correction) has been issued", tag.get_text(" ", strip=True), re.I):
            tag.decompose()
            removed_pointer = True
    for tag in soup.select(".core-relations .relation.HAS_ERRATUM"):
        if NOTICE_CUE.search(tag.get_text(" ", strip=True)) and not DECISION.search(tag.get_text(" ", strip=True)):
            tag.decompose()
            removed_pointer = True
    for entry in soup.select("article.tag-retraction-notice .entry-content"):
        for tag in entry.select("blockquote.wp-embedded-content, iframe.wp-embedded-content"):
            tag.decompose()
            removed_embed = True

    out = Extracted(title=_title(soup), abstract=_abstract(soup))

    body, method = "", ""
    try:
        import trafilatura
        body = trafilatura.extract(str(soup) if removed_pointer or removed_embed else html, include_comments=False,
                                   include_tables=True, favor_recall=True) or ""
        method = "trafilatura"
    except Exception:                                      # noqa: BLE001
        body = ""
    if len(body) < 400:
        # Short pages are where trafilatura gives up, and retraction notices
        # are short by nature. Fall back to the page's own text rather than
        # discarding the document.
        body = soup.get_text("\n", strip=True)
        method = "soup" if not method else method + "+soup"

    # Chrome comes off before the notice is looked for, not after: `_notice`
    # searches the body for its cue and walks forward from it, so a cookie panel
    # sitting between the cue and the end of the statement would be carried into
    # the notice no matter how well the notice itself were cleaned afterwards.
    out.body = _strip_boilerplate(_clean(body), _KEEP_BODY)
    # A body that opens with the site's own interface is not a short article,
    # it is no article. Emptying it here rather than in the truncation rule
    # keeps the guard that stops a cut from deleting real text: this is a
    # judgement about what the whole body is, not about where to cut it.
    if INTERFACE_ONLY.match(out.body):
        out.body = ""
    out.method = method
    out.references = _references(soup)
    out.notice, out.notice_method = _notice(soup, out.body, kind)
    # Wiley's description metadata can be truncated. Use the actual editorial
    # paragraph, not _abstract()'s preferred metadata, to retain the full reason.
    section = soup.select_one(".article-section__abstract .article-section__content")
    if section:
        blocks = _blocks(section)
        if blocks and blocks[0].lower().startswith("this communication") and NOTICE_CUE.match(blocks[0]):
            statement = _section_notice(section)
            if 80 < len(statement) < 3000:
                out.notice, out.notice_method = statement, "abstract-statement"
    # The notice can also come from a selector, which never passed through the
    # body, so it is stripped on its own account as well.
    out.notice = _strip_boilerplate(out.notice, _KEEP_NOTICE)
    if removed_pointer and not out.notice:
        out.notice_method = "pointer-only"
    return out
