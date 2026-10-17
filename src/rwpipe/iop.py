"""IOP's SFTP delivery: how to read a file's identity.

IOP names each zip `<n>_<ISSN>_<vol>_<issue>_<article>_<escaped DOI>.zip`,
escaping the DOI as `.` -> `__` and `/` -> `_`. That name is a hint. The DOI
the article actually carries is in the XML inside, and that is the one the
pipeline files it under.

Shared by check_iop_delivery.py and ingest_iop.py so that "which DOI is this
file" has one definition, not two that can drift apart.
"""
from __future__ import annotations

import re

# The DOI segment of the filename: the last "_10__<registrant>_..." run.
# Greedy on the left so a volume or issue of 10 earlier in the name is not
# taken for the DOI's "10." - the filename is ISSN_vol_issue_article_DOI.
NAME_DOI = re.compile(r".*_(10__\d{4,9}_.+)\.zip$", re.I)

# JATS carries the DOI as <article-id pub-id-type="doi">. The fallbacks are
# for whatever IOP uses in metadata-only records, and the one that matched is
# recorded so an unexpected schema is visible in the report.
XML_DOI = [
    ("jats-article-id", re.compile(
        r'<article-id[^>]*pub-id-type=["\']doi["\'][^>]*>\s*([^<\s]+)\s*<', re.I)),
    ("doi-element", re.compile(r"<doi>\s*([^<\s]+)\s*</doi>", re.I)),
    ("doi-attribute", re.compile(r'\bdoi=["\'](10\.[^"\']+)["\']', re.I)),
]
DOCTYPE = re.compile(r"<!DOCTYPE\s+([^\s>]+)[^>]*?(?:\"([^\"]*)\")?", re.I)
ROOT = re.compile(r"<([A-Za-z][\w:.-]*)[\s>]")


def unescape(seg: str) -> str:
    """`10__1088_2631-8695_ae3e7d` -> `10.1088/2631-8695/ae3e7d`."""
    return seg.replace("__", "\x00").replace("_", "/").replace("\x00", ".").lower()


def xml_doi(xml: str) -> tuple[str, str]:
    """(DOI, which pattern found it), or ("", "none")."""
    head = xml[:60_000]
    for how, rx in XML_DOI:
        m = rx.search(head)
        if m:
            return m.group(1).strip().lower(), how
    return "", "none"


def root_of(xml: str) -> tuple[str, str]:
    """(doctype or root element, public id if any) - the schema, roughly."""
    head = xml.lstrip()[:4_000]
    m = DOCTYPE.search(head)
    if m:
        return f"!DOCTYPE {m.group(1)}", (m.group(2) or "")[:80]
    body = re.sub(r"<\?.*?\?>", "", head, flags=re.S)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    m = ROOT.search(body)
    return (f"<{m.group(1)}>" if m else "?"), ""
