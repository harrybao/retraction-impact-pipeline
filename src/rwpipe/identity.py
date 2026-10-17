"""Is a stored page the document it is filed under? One rule for every route.

Two rules existed until 13 October 2026, and they disagreed.

`verify_archives.py` - which sets `documents.usable`, the flag every coverage
figure counts - accepted a page when the requested DOI appeared anywhere in
the file, or its title matched. `merge_wayback.py` was given a stricter rule
on 7 October after two adversarial reviews (the project log, 2026-10-07): a DOI
found anywhere proves what was *requested*, not what came back. An Internet
Archive copy carries the requested address 13-14 times in its toolbar and
MHTML headers, and a live capture carries it in its own headers too, so "DOI
on the page" was true of SAGE "Error | Sage" pages and of notices saved in
place of their article. Of the 28,087 stored pages `archive_checks` filed as
"doi-only", a 7 October review counted, among 10,133 with a label-only
title, 21 that declare another DOI and 10 error pages. The same capture
could therefore be refused by one script and counted by the other.

`judge()` is now the rule for both. In order, first decisive answer wins:

1. **Error pages.** A CDN error (`fetch.ORIGIN_ERROR`) or a short error title
   ("Error | Sage", "Page Unavailable", "DOI Not Found", ...) is refused,
   whatever DOI the page carries - the error page repeats the address it was
   served at.
2. **The DOI the document declares about itself.** An HTML page's <head>
   metadata (`fetch.page_doi()`: citation_doi, then Dublin Core, then PRISM;
   the head only), a JATS article's <article-meta> DOI, an Elsevier
   response's <coredata> DOI. The requested DOI, or a recorded alias of it
   (`doi_corrections`), is accepted even under a label title. Any other DOI
   is `other-doi`: refused, kept apart for review. See "What other-doi
   holds" below.
3. **Visible self-identification.** A first explicitly labelled DOI before
   references may identify a document without head metadata. Toolbars, request
   addresses and later references are excluded. A bare DOI URL needs an exact
   single-paper title match; an original target cannot be rescued by a notice.
4. **The page title**, for a DOI that stands for one paper and a title that
   names a paper (`fetch.informative_title()`): `fetch.compare_titles()` accepts
   similarity at 0.55 or more, or one normalised title wholly containing the
   other when the shorter has at least 15 characters. Otherwise the verdict
   is `wrong-article`. A DOI printed in a page whose
   title names another paper is the neighbouring-article failure (a page
   that cites ours in its reference list), so it does not rescue a mismatch.
5. Otherwise `wrong-article` (insufficient identity evidence, not proof that
   every rejected page is another document). A DOI that stands for several papers (a mass
   notice) needs a declared or explicitly labelled visible self DOI: some
   unrelated page always resembles one of several hundred titles.

Different declared DOIs can represent a notice saved in place of its paper,
a neighbouring article, or another version of a Cochrane review. Some are the
same paper under a publisher's alias (Elmer Press's volume-issue DOIs, a
journal renamed at Dove, a prefix moved at KJAN, De Gruyter, Ovid). Those
are refused too until recorded: no rule on the DOI string alone separates an
alias from a substitution - "Retraction: <title>" under another DOI is the
same title and a different document - so an alias becomes known the way the
two IOP corrections did, by being recorded in `doi_corrections`. Additional
reviewed captures use the directed, record/role/snapshot-scoped registry;
they do not establish reverse, transitive or resolver equivalence.
"""
from __future__ import annotations

import html
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from . import fetch, mhtml

MIN_TITLE_SIMILARITY = 0.55

# Titles of the error pages publishers serve at a DOI's address, found among
# the dataset's own captures (SAGE "Error | Sage", Springer "Page Unavailable",
# "DOI Not Found", Ingenta "Content Not Found", "Not Found | The Company of
# Biologists"). Short titles only: one paper in the CSV is titled "Error
# separation in on-site measurement of NC machine based on Bayesian network".
ERROR_TITLE = re.compile(r"^\s*(?:error\b|404\b|page not found|page unavailable|"
                         r"publication not found|doi not found|content not found|"
                         r"not found\b)", re.I)
ERROR_TITLE_MAX_WORDS = 8          # words, not '|' or '&'

VERDICTS = ("ok", "error-page", "other-doi", "wrong-article", "unreadable")


@dataclass(frozen=True)
class Verdict:
    verdict: str                    # one of VERDICTS
    reason: str                     # which evidence decided
    declared: str | None = None     # the DOI the document declares, if any
    similarity: float | None = None  # title similarity, when the title decided
    matched_role: str | None = None

    @property
    def usable(self) -> bool:
        return self.verdict == "ok"


@dataclass(frozen=True)
class ReviewedAlias:
    declared: str
    role: str
    html_sha256: str
    relation: str


def _reviewed_aliases():
    path = Path(__file__).with_name("identity_aliases.json")
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("schema") != 1 or registry.get("scope") != "reviewed-capture-only":
        raise ValueError("unsupported reviewed identity registry")
    return registry["rules"]


# --- reading the document ----------------------------------------------------

def read_capture(path) -> str:
    """Decode the main document with Stage 2's MIME and charset parser."""
    doc = mhtml.read(path)
    return doc.html if doc.ok else ""


def _flat(s: str) -> str:
    """Quoted-printable soft line breaks removed: "=" at the end of a line
    means "no break here"."""
    return s.replace("=\r\n", "").replace("=\n", "")


_DOUBLED_PREFIX = re.compile(r"^(10\.\d{4,9}/)\1")
_DOI_LEAD = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.I)


def norm_doi(doi: str | None) -> str:
    """One spelling of a DOI: lower case, entities and percent-escapes
    decoded, a doubled prefix collapsed. Retraction Watch holds
    "10.18287/10.18287/2782-2966-2024-4-4-124" for a paper whose page declares
    "10.18287/2782-2966-2024-4-4-124"; lungca.org declares the doubled form of
    a DOI the CSV holds correctly. Both are the same string mistyped, not two
    documents."""
    d = unquote(html.unescape(doi or "")).strip().lower()
    d = _DOI_LEAD.sub("", d)
    m = _DOUBLED_PREFIX.match(d)
    return d[len(m.group(1)):] if m else d


_JATS_META = re.compile(r"<article-meta\b.*?</article-meta>", re.S | re.I)
_JATS_DOI = re.compile(r"<article-id\b[^>]*pub-id-type\s*=\s*[\"']doi[\"'][^>]*>"
                       r"\s*([^<]+?)\s*</article-id>", re.I)
_JATS_TITLE = re.compile(r"<article-title\b[^>]*>(.*?)</article-title>", re.S | re.I)
_ELS_CORE = re.compile(r"<coredata\b.*?</coredata>", re.S | re.I)
_ELS_DOI = re.compile(r"<prism:doi>\s*([^<]+?)\s*</prism:doi>", re.I)
_ELS_TITLE = re.compile(r"<dc:title>(.*?)</dc:title>", re.S | re.I)
_TAG = re.compile(r"<[^>]*>", re.S)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _structured(capture: str) -> tuple[ET.Element, str] | None:
    """The document's own XML metadata, excluding references and subarticles."""
    start = capture[:300].lstrip()
    if not re.match(r"^(?:<\?xml|<!DOCTYPE(?!\s+html)|<(?:[\w.-]+:)?(?:article|pmc-articleset|full-text-retrieval-response)\b)",
                    start, re.I):
        return None
    try:
        root = ET.fromstring(capture)
    except ET.ParseError:
        return None
    if _local(root.tag) == "pmc-articleset":
        articles = [e for e in root if _local(e.tag) == "article"]
        if len(articles) != 1:
            return None
        root = articles[0]
    if _local(root.tag) == "full-text-retrieval-response":
        block = next((e for e in root if _local(e.tag) == "coredata"), None)
        return (block, "elsevier") if block is not None else None
    if _local(root.tag) == "article":
        front = next((e for e in root if _local(e.tag) == "front"), root)
        block = next((e for e in front if _local(e.tag) == "article-meta"), None)
        return (block, "jats") if block is not None else None
    return None


def declared_dois(capture: str) -> list[str]:
    """The DOIs the document declares as its own, at the most preferred kind
    of declaration it uses, normalised and in order of appearance.

    Usually one. Reumatismo's pages put three other articles' citation_doi in
    the head above their own (the "latest articles" box), so a page can
    declare several - which then identifies nothing by itself."""
    s = _structured(capture)
    if s:
        block, kind = s
        values = [norm_doi("".join(e.itertext())) for e in block.iter()
                  if (_local(e.tag) == "doi" if kind == "elsevier" else
                      _local(e.tag) == "article-id" and e.get("pub-id-type") == "doi")]
        return list(dict.fromkeys(d for d in values if d))
    found = fetch._declared_dois(capture)
    if not found:
        return []
    best = min(rank for rank, _ in found)
    out: list[str] = []
    for rank, d in found:
        d = norm_doi(d)
        if rank == best and d not in out:
            out.append(d)
    return out


def page_title(capture: str) -> str:
    """The document's own title: the article title of a JATS or Elsevier
    document (whose first <title> is a section heading - "Keywords",
    "Abstract"), the <title> of a page."""
    s = _structured(capture)
    if s:
        block, kind = s
        tag = "title" if kind == "elsevier" else "article-title"
        title = next((e for e in block.iter() if _local(e.tag) == tag), None)
        if title is None:
            return ""
        return re.sub(r"\s+", " ", "".join(title.itertext())).strip()
    return fetch.page_title_of(capture)


def is_error_title(title: str) -> bool:
    return bool((ERROR_TITLE.search(title or "") or fetch._BLOCK_TITLE.search(title or ""))
                and len(re.findall(r"[^\W_]+", title)) <= ERROR_TITLE_MAX_WORDS)


_TOOLBAR = re.compile(r"<!--\s*BEGIN WAYBACK TOOLBAR INSERT\s*-->.*?"
                      r"<!--\s*END WAYBACK TOOLBAR INSERT\s*-->", re.S | re.I)
_INVISIBLE = re.compile(r"<(script|style|template|noscript)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
# A URL printed on the page is an address, and an address is what every error
# page and login wall repeats. Only a doi.org link names a DOI as such.
_ADDRESS = re.compile(r"(?:https?://(?!(?:www\.|dx\.)?doi\.org/)|\bwww\.(?!doi\.org/))\S+",
                      re.I)
_WS = re.compile(r"\s+")


def printed_text(capture: str) -> str:
    """What the page itself prints, lower-cased: the main document's text
    without its MIME headers, the Archive's toolbar, scripts, styles,
    comments, markup, or any address other than a doi.org link."""
    qp = "quoted-printable" in capture[:5000].lower()
    flat = capture.replace("=\r\n", "").replace("=\n", "")
    doc = fetch._main_document(flat)
    if doc is not flat:
        # The part's own headers: Content-Location is the requested address.
        cut = re.search(r"\r?\n\r?\n", doc)
        doc = doc[cut.end():] if cut else doc
    if qp:
        doc = fetch._unqp(doc)
    for rx in (_TOOLBAR, _INVISIBLE, _COMMENT, _TAG):
        doc = rx.sub(" ", doc)
    text = html.unescape(doc).lower()
    return _WS.sub(" ", _ADDRESS.sub(" ", text))


def prints_doi(text: str, doi: str) -> bool:
    return bool(doi) and norm_doi(doi) in text


_BODY_DOI = re.compile(r"(?<![\w])10\.\d{4,9}/[^\s\"'<>]+", re.I)
_DOI_LABEL = re.compile(r"(?:\bdoi|digital object identifier)\s*:?\s*(?:https?://(?:dx\.)?doi\.org/)?$", re.I)
_CITATION_END = re.compile(r"^(?:references?|bibliography|literature cited|"
                            r"this article has been cited by|related articles?)\b", re.I)


def _identifier_value(value: str) -> str:
    value = value.rstrip(".,;:!?")
    for left, right in (("(", ")"), ("[", "]"), ("{", "}")):
        while value.endswith(right) and value.count(right) > value.count(left):
            value = value[:-1]
    return norm_doi(value)


def body_identifier(capture: str, allow_url: bool = False) -> str | None:
    """First visible DOI before references, only when labelled or a DOI URL.

    Never use request headers, Archive furniture, scripts, metadata in the
    body, or a later reference to rescue a page. Absence is not a rejection.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(mhtml.strip_wayback_toolbar(capture), "lxml")
    for node in soup.select("head,script,style,template,noscript,svg,math,iframe,object,nav,footer,aside,"
                            "section.references,div.references,#references,#References,#TabBibliografia"):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.attrs is not None and (node.has_attr("hidden") or re.search(
                r"(?:display\s*:\s*none\b|visibility\s*:\s*(?:hidden|collapse)\b)",
                node.get("style", ""), re.I)):
            node.decompose()
    text = (soup.body or soup).get_text("\n", strip=True)
    lines = []
    for line in text.splitlines():
        if _CITATION_END.match(line.strip()):
            break
        lines.append(_ADDRESS.sub(" ", line))
    text = " ".join(lines)
    match = _BODY_DOI.search(text)
    if not match:
        return None
    prefix = text[max(0, match.start() - 100):match.start()]
    if not (_DOI_LABEL.search(prefix) or
            (allow_url and re.search(r"https?://(?:dx\.)?doi\.org/$", prefix, re.I))):
        return None
    return _identifier_value(match.group(0))


def _field_dois(nodes) -> set[str]:
    values = set()
    for node in nodes:
        if node is None or node.attrs is None:
            continue
        for found in _BODY_DOI.finditer(node.get_text(" ", strip=True)):
            values.add(_identifier_value(found.group(0)))
        anchors = [node] if node.name == "a" else node.select("a[href]")
        for anchor in anchors:
            try:
                url = urlsplit(anchor.get("href", ""))
            except ValueError:
                continue
            if url.hostname in ("doi.org", "dx.doi.org", "www.doi.org") and url.path.startswith("/10."):
                values.add(norm_doi(unquote(url.path[1:])))
    return values


def _visible_soup(capture: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(mhtml.strip_wayback_toolbar(capture), "lxml")
    for node in soup.select("head,script,style,template,noscript,svg,math,iframe,object,"
                            "section.references,div.references,#references,#References"):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.attrs is not None and (node.has_attr("hidden") or re.search(
                r"display\s*:\s*none\b|visibility\s*:\s*(?:hidden|collapse)\b",
                node.get("style", ""), re.I)):
            node.decompose()
    return soup


def self_field(capture: str, titles: Iterable[str] = ()) -> tuple[str, str, str | None] | None:
    """Explicit article bibliographic fields, not DOI links in arbitrary prose.

    Read candidates before comparing with the requested DOI. Each selector is
    an observed self-citation field; multiple different values are ambiguous.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(mhtml.strip_wayback_toolbar(capture), "lxml")
    peerj = soup.select("article[itemscope][itemtype$='ScholarlyArticle'] header.article-meta "
                        "#article-information dl.article-identifiers a[itemprop='sameAs']")
    for node in soup.select("script,style,template,noscript,svg,math,iframe,object,"
                            "section.references,div.references,#references,#References,#TabBibliografia"):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.attrs is not None and (node.has_attr("hidden") or re.search(
                r"display\s*:\s*none\b|visibility\s*:\s*(?:hidden|collapse)\b",
                node.get("style", ""), re.I)):
            # This one collapsed structured identifier belongs to a visible
            # ScholarlyArticle header. Ordinary hidden DOI text remains inert.
            if ((node.get("id") in ("article-information", "article-item-article-information-container") or
                 node.find_parent(id="article-item-article-information-container")) and
                    any(node in a.parents for a in peerj)):
                continue
            node.decompose()

    values = _field_dois
    fields = []
    def add(nodes, method, role=None):
        candidates = values(nodes)
        if len(candidates) == 1:
            fields.append((next(iter(candidates)), method, role))
        elif candidates:
            fields.append(("", "ambiguous-self-field", None))

    add(soup.select("article.obj_article_details section.item.doi .value"), "article-doi-field")
    if soup.select_one(".obj_article_details h1.page_title"):
        add(soup.select(".obj_article_details .citation_display #citationOutput .csl-entry"),
            "article-self-citation")
    if soup.select_one("h1.document-title"):
        add(soup.select("section.document-abstract .stats-document-abstract-doi a[href]"),
            "article-abstract-doi")
    if peerj and soup.select_one("article[itemscope][itemtype$='ScholarlyArticle'] h1"):
        add(peerj, "structured-article-identifier")
    add([p for p in soup.select("#article-wrapper #smj-sidebar .sidebar-widgets > p")
         if re.match(r"\s*(?:article\s+)?doi\b", p.get_text(" ", strip=True), re.I)], "article-sidebar-doi")
    add([p for p in soup.select(".detail-right p.subinfo")
         if all(re.search(r"\b" + word + r"\b", p.get_text(" ", strip=True), re.I)
                for word in ("volume", "issue", "pages", "doi"))], "article-collation-doi")
    add([p for p in soup.select(".thn_post_wrap > h4")
         if re.match(r"\s*doi\s*:", p.get_text(" ", strip=True), re.I)], "article-heading-doi",
        "retraction" if re.search(r"retraction notification", soup.get_text(" ", strip=True), re.I) else None)
    dg = soup.select("#citations #citationContent #MLA.citation-text.show.active")
    add(dg, "active-self-citation")
    add(soup.select("[id$='labelArticleCollation'] a[href]"), "journal-collation-doi")
    add([p for p in soup.select(".right_articles p[align='right']")
         if re.search(r"\b(?:volume|vol\.?|issue|pages)\b", p.get_text(" ", strip=True), re.I)
         and re.search(r"\b(?:19|20)\d{2}\b", p.get_text(" ", strip=True))], "journal-collation-doi")
    add([p for p in soup.select("#TabArticulo.active div")
         if not p.find(["div", "p"]) and re.match(r"\s*doi\s*:", p.get_text(" ", strip=True), re.I)], "active-article-doi")
    add([p for p in soup.select("ds-show-metadata .simple-view-element")
         if re.search(r"identificador doi", p.get_text(" ", strip=True), re.I)], "repository-doi-field")
    for heading in soup.select("h2"):
        if heading.get_text(" ", strip=True).lower() == "citation":
            block = heading.find_next_sibling()
            if block and any(d.startswith("10.17912/") for d in values([block])):
                add([block], "publication-self-citation")

    for block in soup.select(".novsu_block_content"):
        if (block.select_one("h4.center") and
                any(len(p.get_text(" ", strip=True)) >= 150 for p in block.select("p.annotation"))):
            add([p for p in block.select("p.annotation") if re.fullmatch(
                r"\s*https?://(?:dx\.)?doi\.org/10\.\S+\s*", p.get_text(" ", strip=True), re.I)],
                "article-annotation-doi")
    if soup.select_one("h1"):
        for icon in soup.select("img[alt='with-doi']"):
            block = icon.parent.parent
            if re.match(r"\s*Published\b", block.get_text(" ", strip=True), re.I):
                add(icon.parent.select("a[href]"), "publication-sidebar-doi",
                    "retraction" if fetch.NOTICE_TITLE.match(soup.h1.get_text(" ", strip=True)) else None)

    story = soup.select_one("div#Story")
    notice_clause = (r"^\s*(?:(?:the|this)\s+(?:above\s+)?(?:article|paper)\b.{0,1600}?"
                     r"\b(?:has\s+been|was|is)\s+(?:retracted|withdrawn)\b|"
                     r"expression of concern\b|retraction (?:statement|notice)\b|"
                     r"the authors of (?:the|this) article\b.{0,1600}?"
                     r"\bhave requested retraction on the following basis\b|"
                     r"the\b.{0,100}\b(?:publishers?|editors?)\b.{0,100}"
                     r"\bissuing an expression of concern\b)")
    if (story and re.search(notice_clause, story.get_text(" ", strip=True), re.I | re.S)
            and titles and prints_title(story.get_text(" ", strip=True).lower(), titles)):
        # Status links name separate documents. Only the printed DOI names
        # this page; choose that field without consulting the requested DOI.
        nodes = [a for a in soup.select("nav#topnav > ul.clear > li > a[href]")
                 if _BODY_DOI.search(a.get_text(" ", strip=True))]
        candidates = values(nodes)
        if candidates and all(d.startswith("10.33594/") for d in candidates):
            add(nodes, "notice-control-doi", "retraction")

    unique = {d for d, _, _ in fields}
    if not unique:
        return None
    if len(unique) != 1 or "" in unique:
        return "", "ambiguous-self-field", None
    value, method, role = fields[0]
    role = next((r for _, _, r in fields if r), role)
    if method == "active-self-citation" and value.endswith("-fm"):
        role = "original"
    return value, method, role


def notice_landing_role(capture: str, title: str, kind: str | None) -> str | None:
    """A correct original DOI can now serve only a publication-status notice.

    This records content availability, not an alias to an independent notice.
    A status prefix alone never rejects a retained original article.
    """
    if kind not in ("original", "both") or not (fetch.NOTICE_TITLE.match(title or "") or
                                               re.match(r"\s*retracted\b", title or "", re.I)):
        return None
    if _structured(capture):
        return None
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(capture, "lxml")
    for node in soup.select("script,style,template,noscript,svg,math,iframe,object"):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.attrs is not None and (node.has_attr("hidden") or re.search(
                r"display\s*:\s*none\b|visibility\s*:\s*(?:hidden|collapse)\b",
                node.get("style", ""), re.I)):
            node.decompose()
    original_abstract = soup.select_one(".obj_article_details section.item.abstract")
    if original_abstract:
        text = original_abstract.get_text(" ", strip=True)
        body = soup.select_one(".obj_article_details .item.full_text")
        if (re.match(r"\s*Abstract\s+RETRACTED ARTICLE\s*:", text, re.I) and
                re.search(r"\b(?:article was retracted|article has fabricated data)\b", text, re.I) and
                not (body and len(body.get_text(" ", strip=True)) >= 300)):
            return "retraction"
    abstract = soup.select_one(".article-section__abstract .article-section__content")
    text = abstract.get_text(" ", strip=True) if abstract else ""
    if text and re.match(r"\s*(?:retraction statement|this article has been retracted)", text, re.I):
        return "retraction"
    return None


_BRACKETED = re.compile(r"\[([^\]]*)\]")


TITLE_PREFIX_CHARS = 60


def _title_forms(title: str) -> list[str]:
    """Retraction Watch appends a translation in brackets ("Einführung
    [Introduction]"); the page prints one language. Each form is compared on
    its first 60 characters, normalised as fetch does, and a form that short
    or shorter must be printed as whole words."""
    forms = [title, _BRACKETED.sub(" ", title)] + _BRACKETED.findall(title)
    out = []
    for f in forms:
        n = _identity_words(f)
        n = f" {n} " if len(n) <= TITLE_PREFIX_CHARS else f" {n[:TITLE_PREFIX_CHARS]}"
        if n.strip() and n not in out:
            out.append(n)
    return out


def prints_title(text: str, titles: Iterable[str]) -> bool:
    flat = " " + _identity_words(text) + " "
    return any(f in flat for t in titles for f in _title_forms(t))


def _identity_words(text: str) -> str:
    # Some captured notices print a malformed &ldquoTitle without a semicolon.
    # Normalize that quote only for identity, never rewrite publication text.
    text = re.sub(r"&(?:[lr]dquo|[lr]squo);?", " ", html.unescape(text), flags=re.I)
    return _WS.sub(" ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def repository_version(capture: str, want: str, titles, kind, declared) -> Verdict | None:
    """A directed, explicit repository relationship; never a DOI alias."""
    if kind not in ("original", "both") or "item-details" not in capture or "version of" not in capture.lower():
        return None
    soup = _visible_soup(capture)
    article = soup.select_one("article#item-details")
    heading = article.find("h1") if article else None
    if not heading or not prints_title(heading.get_text(" ", strip=True), titles):
        return None
    fields = {}
    for label in article.select("#item-sidebar h3"):
        value = label.find_next_sibling()
        fields.setdefault(label.get_text(" ", strip=True).lower(), []).append(value)
    targets = _field_dois(fields.get("is version of", []))
    own = _field_dois(fields.get("persistent identifier", []))
    if targets != {want} or len(own) != 1 or (declared and set(declared) != own):
        return None
    # The own citation and bibliographic fields corroborate a publication
    # version, not an arbitrary related-resource link in a reference list.
    if not (article.select_one("#item-citation .csl-entry") and
            fields.get("journal title") and fields.get("publication status")):
        return None
    if " ".join(n.get_text(" ", strip=True) for n in fields["publication status"] if n).lower() != "publishedversion":
        return None
    return Verdict("ok", "repository-version-of", next(iter(own)), matched_role="original")


def article_heading(capture: str, titles, journal) -> Verdict | None:
    if "omni-article-title" not in capture:
        return None
    soup = _visible_soup(capture)
    header = soup.select_one(".lww-article__header")
    heading = header.select_one("h1#omni-article-title") if header else None
    if not (heading and header.select_one(".lww-article__contributors-list") and
            header.select_one(".lww-article__citation-info") and
            soup.select_one("#omni-article-abstract .lww-text-abstract")):
        return None
    for footnote in heading.select("sup"):
        footnote.decompose()
    title = re.sub(r"\s*\[retracted\]\s*$", "", heading.get_text(" ", strip=True), flags=re.I)
    best = max((fetch.compare_titles(title, t, journal=journal) for t in titles),
               key=lambda x: (x[0], x[1]), default=(False, None))
    return Verdict("ok", "article-heading-title", similarity=best[1], matched_role="original") if best[0] else None


def pdf_identity(capture: str, want: str, titles, kind) -> Verdict | None:
    if 'data-rwpipe-format="pdf-text"' not in capture[:300]:
        return None
    soup = _visible_soup(capture)
    for page in soup.select("section.rwpipe-pdf-page"):
        text = page.get_text("\n", strip=True)
        notice = bool(re.search(r"\b(?:Retraction Notice|Retraction Statement|Withdrawal Notice)\b", text[:350], re.I))
        if notice and kind == "original":
            continue
        if not notice and kind == "retraction":
            continue
        if not prints_title(text[:2200], titles):
            continue
        if not notice and not re.search(r"\babstract\b", text[:2200], re.I):
            continue
        lines = []
        for line in text.splitlines():
            if _CITATION_END.match(line.strip()):
                break
            if re.search(r"\b(?:19|20)\d{2}\s*;", line) and re.search(r"\bdoi\s*:", line, re.I):
                lines.append(line)
        if notice:
            year = re.search(r"\b(?:19|20)\d{2}\b", text[:200])
            if year:
                lines = [line for line in lines if year.group(0) in line]
        own = {_identifier_value(m.group(0)) for line in lines for m in _BODY_DOI.finditer(line)}
        if own == {want}:
            role = "retraction" if notice else "original"
            return Verdict("ok", "pdf-self-citation-recovered", want, matched_role=role)
    return Verdict("wrong-article", "pdf-identity-insufficient")


# --- the rule ------------------------------------------------------------------

def judge(capture: str, doi: str, titles: Iterable[str] = (),
          journal: str | None = None, papers: int = 1,
          aliases: Iterable[str] = (), kind: str | None = None) -> Verdict:
    """Is `capture` the document filed under `doi`?

    `titles` are the titles the DOI stands for in `records` (a notice stands
    for the papers it retracts), `papers` how many papers, `aliases` DOIs
    recorded as naming the same document (`doi_corrections`, either way)."""
    if not capture:
        return Verdict("unreadable", "unreadable")
    if not capture.lstrip().startswith("<"):
        doc = mhtml.parse(capture.encode("utf-8"))
        if not doc.ok:
            return Verdict("unreadable", "mime-unreadable")
        capture = doc.html
    titles = [t for t in titles if t and fetch._norm_title(t).strip()]
    # On the text with soft breaks removed, as the verifier always matched
    # it: in raw MHTML "origin is unreachable" can be split across two lines.
    # 220,000 raw characters hold the first 200,000 flattened ones (a soft
    # break is 3 characters per 76-character line).
    if fetch.ORIGIN_ERROR.search(_flat(capture[:220_000])[:200_000]):
        return Verdict("error-page", "origin-error")
    title = page_title(capture)
    if is_error_title(title):
        return Verdict("error-page", "error-title")

    want = norm_doi(doi)
    pdf = pdf_identity(capture, want, titles, kind)
    if pdf is not None:
        return pdf
    aliases = tuple(aliases)
    snapshot = hashlib.sha256(capture.encode("utf-8")).hexdigest() if any(
        isinstance(a, ReviewedAlias) for a in aliases) else None
    scoped = {a.declared: a for a in aliases if isinstance(a, ReviewedAlias)
              and (kind == a.role or kind == "both") and a.html_sha256 == snapshot}
    known = {want} | set(scoped) | {norm_doi(a) for a in aliases if isinstance(a, str)}
    declared = declared_dois(capture)
    version = repository_version(capture, want, titles, kind, declared)
    if version is not None:
        return version
    notice_for_article = (kind == "original" and fetch.NOTICE_TITLE.match(title or "") and
                          not fetch.ARTICLE_FLAG.match(title or ""))
    if len(declared) == 1:
        d = declared[0]
        if d == want:
            return Verdict("ok", "own-doi", d, matched_role=notice_landing_role(capture, title, kind))
        if d in known:
            if d in scoped:
                rule = scoped[d]
                reason = ("corrected-notice-target" if rule.relation == "notice_target_correction"
                          else f"{rule.role}-alias-doi")
                return Verdict("ok", reason, d, matched_role=rule.role)
            return Verdict("ok", "alias-doi", d)
        return Verdict("other-doi", "declares-other-doi", d)
    if declared:
        # Several declared at once: the head does not say which page this
        # is. Without the requested DOI among them it is another page; with
        # it, only the page's own title can say which of them it is.
        shown = " ".join(declared)[:200]
        if not known & set(declared):
            return Verdict("other-doi", "declares-other-dois", shown)
        own_field = self_field(capture, titles)
        if own_field:
            if not own_field[0]:
                return Verdict("wrong-article", "ambiguous-self-field")
            if own_field[0] in known & set(declared):
                return Verdict("ok", own_field[1], own_field[0], matched_role=own_field[2])
            return Verdict("other-doi", "self-field-declares-other-doi", own_field[0])
        if papers > 1 or not fetch.informative_title(title, journal):
            return Verdict("wrong-article", "declares-several", shown)
        best = max((fetch.compare_titles(title, t, MIN_TITLE_SIMILARITY, journal=journal) for t in titles),
                   key=lambda m: (m[0], m[1]), default=(False, None))
        if title and best[0]:
            return Verdict("ok", "declares-several-title", shown, best[1])
        return Verdict("wrong-article", "declares-several", shown, best[1])

    own_field = self_field(capture, titles)
    if own_field:
        value, method, role = own_field
        if not value:
            return Verdict("wrong-article", "ambiguous-self-field")
        return (Verdict("ok", method, value, matched_role=role or notice_landing_role(capture, title, kind)) if value in known else
                Verdict("other-doi", "self-field-declares-other-doi", value))
    body_doi = body_identifier(capture)
    if not body_doi and papers == 1 and any(
            fetch._norm_title(title, site_suffix=True, journal=journal) == fetch._norm_title(t)
            for t in titles):
        body_doi = body_identifier(capture, allow_url=True)
    if notice_for_article:
        return Verdict("wrong-article", "notice-substitution", body_doi)
    if body_doi in known:
        return Verdict("ok", "body-doi" if body_doi == want else "body-alias-doi", body_doi)
    if body_doi:
        return Verdict("other-doi", "body-declares-other-doi", body_doi)

    if papers == 1:
        heading = article_heading(capture, titles, journal)
        if heading is not None:
            return heading
    if papers > 1:
        return Verdict("wrong-article", "mass-notice-undeclared")
    if titles and fetch.informative_title(title, journal):
        ok, sim = max((fetch.compare_titles(title, t, MIN_TITLE_SIMILARITY, journal=journal) for t in titles),
                      key=lambda m: (m[0], m[1]))
        return Verdict("ok", "title", None, sim) if ok else \
            Verdict("wrong-article", "title-mismatch", None, sim)
    return Verdict("wrong-article", "no-evidence")


def load_context(conn) -> tuple[dict, dict]:
    """(records by DOI, aliases by DOI), lower-cased keys, read once.

    Per DOI: (titles, journal, papers) - the same three things
    `merge_wayback.py` asked per capture (TITLES, JOURNAL, PAPERS), read in
    one pass so a run over 96,514 documents is not 290,000 queries."""
    titles: dict[str, list[str]] = {}
    journal: dict[str, str | None] = {}
    papers: dict[str, set[str]] = {}
    memberships: dict[tuple[str, str], set[int]] = {}
    for rid, title, jour, orig, notice in conn.execute(
            "SELECT record_id, title, journal, original_doi, retraction_doi FROM records"):
        for d in {x.strip().lower() for x in (orig, notice) if x and x.strip()}:
            if title and title.strip() and title not in titles.setdefault(d, []):
                titles[d].append(title)
            journal.setdefault(d, jour)
            papers.setdefault(d, set()).add(orig.strip().lower() if orig and orig.strip()
                                            else f"record:{rid}")
        for role, value in (("original", orig), ("retraction", notice)):
            if value and value.strip():
                memberships.setdefault((norm_doi(value), role), set()).add(rid)
    records = {d: (titles.get(d, []), journal.get(d), len(papers[d])) for d in papers}
    aliases: dict[str, set] = {}
    try:
        for a, b in conn.execute("SELECT doi, corrected_doi FROM doi_corrections"):
            aliases.setdefault(a.lower(), set()).add(b.lower())
            aliases.setdefault(b.lower(), set()).add(a.lower())
    except sqlite3.OperationalError as error:
        if "no such table: doi_corrections" not in str(error):
            raise
    for rule in _reviewed_aliases():
        requested = norm_doi(rule["requested"])
        role = rule["role"]
        if role not in ("original", "retraction"):
            raise ValueError("invalid document role in reviewed identity registry")
        if set(rule["records"]) & memberships.get((requested, role), set()):
            aliases.setdefault(requested, set()).add(ReviewedAlias(
                norm_doi(rule["declared"]), role, rule["html_sha256"], rule["relation"]))
    return records, aliases


def load_roles(conn) -> dict[str, str]:
    roles: dict[str, set[str]] = {}
    for original, notice in conn.execute("SELECT original_doi, retraction_doi FROM records"):
        for role, value in (("original", original), ("retraction", notice)):
            if value and value.strip():
                roles.setdefault(norm_doi(value), set()).add(role)
    return {d: "both" if len(kinds) == 2 else next(iter(kinds)) for d, kinds in roles.items()}
