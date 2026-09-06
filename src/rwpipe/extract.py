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

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

WS = re.compile(r"[ \t\u00a0]+")
BLANKS = re.compile(r"\n{3,}")

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
    r"|has been retracted"
    r"|retraction (note|notice|statement)"
    r"|notice of (retraction|concern)"
    r"|expression of concern"
    r"|withdrawal notice"
    r"|at the (author|editor|publisher)s?[’']?s? request"
    r"|at the request of the (author|editor|publisher)s?"
    r"|(editors?|publishers?|we)[^.]{0,80}have (retracted|withdrawn)"
    r"|(retracted|withdrawn) (at|by|following|due to|after|because)"
    r"|the (article|paper|following article) has been (retracted|withdrawn))",
    re.I)

# Where a notice stops. A reference list following it is not part of it.
REF_START = re.compile(r"^(references?|bibliography|literature cited|"
                       r"author information|about this article|"
                       r"rights and permissions|additional information|"
                       r"supplementary)", re.I)

# Structural noise that survives text extraction on some sites.
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


def _clean(text: str, keep_stops: bool = False) -> str:
    """Drop chrome. `keep_stops` preserves the headings _narrow() stops at.

    `References` was in the junk list and was therefore deleted before
    _narrow() could use it as a boundary - the stop condition existed and
    could never fire, so a notice ran on into the reference list exactly as it
    had before the fix.
    """
    lines = []
    for line in (text or "").splitlines():
        line = WS.sub(" ", line).strip()
        if not line:
            continue
        if JUNK_LINE.match(line) and not (keep_stops and REF_START.match(line)):
            continue
        lines.append(line)
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


def _narrow(paras: list[str], budget: int = 3000) -> str:
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
    """
    hits = [i for i, x in enumerate(paras) if NOTICE_CUE.search(x)]
    if not hits:
        return ""
    out: list[str] = []
    for para in paras[hits[0]:]:
        if REF_START.match(para):
            break
        out.append(para)
        if sum(len(x) for x in out) > budget:
            break
    return chr(10).join(out)[:budget]


def _notice(soup, body: str, kind: str = "original") -> tuple[str, str]:
    """The statement of withdrawal, and how it was found.

    Three routes, tried in order of how much they prove:

    1. A container the publisher marked as a retraction notice.
    2. The cue wording anywhere in the page.
    3. For a page that IS the notice, the body itself - a standalone notice
       has no banner to find, and requiring one returned an empty notice for a
       4,244-character page whose text said "unable to explain the image
       duplication, provide original data or verify the validity".

    The method is stored, so a rule that starts misfiring can be found by
    querying for it rather than by re-reading 69,657 files.
    """
    for sel in ("div.retraction", "section.retraction", "div.article-notice",
                "div.alert-retraction", "div.c-article-retraction",
                "div[class*='retract']", "section[class*='retract']"):
        el = soup.select_one(sel)
        if not el:
            continue
        narrowed = _narrow(_blocks(el))
        if 40 < len(narrowed) < 3000:
            return narrowed, f"selector:{sel}"

    # Page-wide, work from the cleaned text rather than raw DOM blocks. A
    # navigation <div> has no <p> children, so it qualifies as a "block" and
    # arrives first - the notice then began "Skip to main content Journals Why
    # Publish With Us?". _clean strips that; keep_stops leaves the headings
    # _narrow needs as boundaries.
    page = _clean(soup.get_text(chr(10), strip=True), keep_stops=True)
    # Keep short lines only when they are the boundary. "References" is ten
    # characters, so a >25 filter deleted the very heading _narrow stops at -
    # the same mistake as the junk list, one layer further down, and the
    # symptom was identical: notices running to the 3,000-character budget.
    lines = [x.strip() for x in page.split(chr(10)) if x.strip()]
    lines = [x for x in lines if len(x) > 25 or REF_START.match(x)]
    narrowed = _narrow(lines)
    if narrowed:
        return narrowed, "cue"

    if kind in ("retraction", "both") and body:
        # The whole page is the notice. Keep it, capped, rather than record
        # "no reason found" for a document that is nothing but the reason.
        paras = [x for x in body.split(chr(10)) if len(x.strip()) > 25]
        text = chr(10).join(paras)[:3000]
        if len(text) > 80:
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
    start = max(text.rfind("\n", 0, m.start()) + 1, 0)
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
    if not notice and kind in ("retraction", "both") and body:
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
    if not notice and abstract:
        # Elsevier puts the withdrawal statement in dc:description for a
        # withdrawn article, where there is no body to search.
        notice, method = _notice_from_text(abstract, kind)
        if notice:
            method = "els-description"

    return Extracted(title=title, abstract=abstract, body=body, notice=notice,
                     notice_method=method, references=refs, method="elsevier")


def extract(html: str, *, kind: str = "original") -> Extracted:
    """Pull the useful text out of one captured page."""
    if not html:
        return Extracted(error="empty html")
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

    out = Extracted(title=_title(soup), abstract=_abstract(soup))

    body, method = "", ""
    try:
        import trafilatura
        body = trafilatura.extract(html, include_comments=False,
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
    out.method = method
    out.references = _references(soup)
    out.notice, out.notice_method = _notice(soup, out.body, kind)
    # The notice can also come from a selector, which never passed through the
    # body, so it is stripped on its own account as well.
    out.notice = _strip_boilerplate(out.notice, _KEEP_NOTICE)
    return out
