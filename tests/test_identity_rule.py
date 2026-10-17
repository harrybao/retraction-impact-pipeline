"""Stored-page identity: shared rules, real MIME decoding and read-only audits."""
import email.policy
import gzip
import hashlib
import importlib.util
import sqlite3
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rwpipe import config, db, identity

DOI = "10.1234/paper"
OTHER = "10.1234/notice"
TITLE = "Effects of a controlled intervention on outcomes"


def page(title=TITLE, doi=DOI, body="", head=""):
    meta = "" if doi is None else f'<meta name="citation_doi" content="{doi}">'
    return f"<html><head><title>{title}</title>{meta}{head}</head><body>{body}</body></html>"


@pytest.mark.parametrize("title", ["Error | Sage", "Page Unavailable", "Access Denied", "Just a moment..."])
def test_error_title_wins_even_when_requested_doi_is_in_metadata(title):
    assert identity.judge(page(title), DOI, [TITLE]).verdict == "error-page"


def test_another_declared_doi_cannot_be_rescued_by_a_matching_title_or_reference():
    p = page(doi=OTHER, body=f"Retraction notice for {TITLE}: {DOI}")
    assert identity.judge(p, DOI, [TITLE]).verdict == "other-doi"


def test_a_doi_in_the_body_is_not_own_metadata():
    p = page("Unrelated article about instruments", None,
             body=f'<meta name="citation_doi" content="{DOI}">{TITLE} {DOI}')
    assert identity.judge(p, DOI, [TITLE]).verdict == "wrong-article"


@pytest.mark.parametrize("head", [
    f'<!-- <meta name="citation_doi" content="{DOI}"> -->',
    f'<script>const example = \'<meta name="citation_doi" content="{DOI}">\';</script>',
    f'<template><meta name="citation_doi" content="{DOI}"></template>',
    f'<title><meta name="citation_doi" content="{DOI}"></title>',
])
def test_inert_metadata_cannot_identify_a_page(head):
    assert not identity.judge(page("Unrelated gardening article", None, head=head), DOI, [TITLE]).usable


def test_implicit_head_end_still_excludes_body_metadata():
    p = (f'<html><head><title>Unrelated gardening article</title><body>'
         f'<meta name="citation_doi" content="{DOI}"></body></html>')
    assert not identity.judge(p, DOI, [TITLE]).usable


def test_head_end_literal_inside_title_does_not_hide_real_metadata():
    p = ('<html><head><title>Literal closing head </head> in an article title</title>'
         f'<meta name="citation_doi" content="{DOI}"></head><body>Article</body></html>')
    assert identity.judge(p, DOI, [TITLE]).usable


def test_printed_doi_and_cited_title_do_not_identify_a_label_title_page():
    p = page("View article", None, f"<h2>References</h2>{TITLE} https://doi.org/{DOI}")
    assert not identity.judge(p, DOI, [TITLE]).usable
    assert not identity.judge(p, DOI).usable


def test_known_alias_is_accepted_but_unknown_alias_is_kept_apart():
    p = page(doi=OTHER)
    assert identity.judge(p, DOI, [TITLE], aliases=[OTHER]).reason == "alias-doi"
    assert identity.judge(p, DOI, [TITLE]).verdict == "other-doi"


def test_reviewed_alias_needs_role_and_exact_reviewed_capture():
    p = page(doi=OTHER)
    alias = identity.ReviewedAlias(OTHER, "retraction",
              hashlib.sha256(p.encode()).hexdigest(), "same_document")
    good = identity.judge(p, DOI, [TITLE], aliases=[alias], kind="retraction")
    assert good.usable and good.matched_role == "retraction"
    assert not identity.judge(p, DOI, [TITLE], aliases=[alias], kind="original").usable
    assert not identity.judge(p, DOI, [TITLE], aliases=[alias]).usable
    assert not identity.judge(p + " ", DOI, [TITLE], aliases=[alias], kind="retraction").usable
    assert not identity.judge(page(doi=DOI), OTHER, [TITLE], kind="retraction").usable


def test_reviewed_alias_is_bound_to_the_registered_record(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE records(record_id, title, journal, original_doi, retraction_doi)")
    c.execute("INSERT INTO records VALUES (2, 'Test', '', ?, ?)", (DOI, DOI))
    rule = dict(requested=DOI, declared=OTHER, role="retraction", records=[1],
                html_sha256="a" * 64, relation="same_document")
    monkeypatch.setattr(identity, "_reviewed_aliases", lambda: [rule])
    assert not identity.load_context(c)[1]
    c.execute("UPDATE records SET record_id=1")
    aliases = identity.load_context(c)[1]
    assert DOI in aliases and OTHER not in aliases
    assert identity.load_roles(c)[DOI] == "both"
    c.close()


def test_title_can_identify_one_paper_without_declared_doi():
    assert identity.judge(page(doi=None), DOI, [TITLE]).reason == "title"
    assert not identity.judge(page(doi=None), DOI, [TITLE], papers=100).usable


def test_multiple_dois_need_an_informative_title_and_cannot_identify_a_mass_notice():
    p = page(head=f'<meta name="citation_doi" content="{OTHER}">')
    assert identity.judge(p, DOI, [TITLE]).reason == "declares-several-title"
    assert not identity.judge(p, DOI, [TITLE], papers=100).usable
    assert not identity.judge(page("Retraction notice", head=f'<meta name="citation_doi" content="{OTHER}">'),
                              DOI, [TITLE]).usable


def test_elsevier_xml_declaration_and_namespaces_are_read_from_coredata_only():
    xml = ('<?xml version="1.0"?><full-text-retrieval-response xmlns="urn:els" '
           'xmlns:prism="urn:prism" xmlns:dc="urn:dc"><coredata>'
           f"<prism:doi>{DOI}</prism:doi><dc:title>{TITLE}</dc:title></coredata>"
           f"<originalText><prism:doi>{OTHER}</prism:doi></originalText>"
           "</full-text-retrieval-response>")
    assert identity.declared_dois(xml) == [DOI]
    assert identity.page_title(xml) == TITLE
    assert identity.judge(xml, DOI, [TITLE]).usable
    assert not identity.judge(xml, OTHER, [TITLE]).usable


def test_jats_reference_doi_does_not_override_article_metadata():
    xml = (f'<article><front><article-meta><article-id pub-id-type="doi">{DOI}</article-id>'
           f"<title-group><article-title>{TITLE}</article-title></title-group>"
           f'</article-meta></front><back><article-id pub-id-type="doi">{OTHER}</article-id>'
           "</back></article>")
    assert identity.declared_dois(xml) == [DOI]
    assert identity.judge(xml, DOI, [TITLE]).usable
    assert not identity.judge(xml, OTHER, [TITLE]).usable


def test_pmc_articleset_wrapper_contains_exactly_one_document():
    article = (f'<article><front><article-meta><article-id pub-id-type="doi">{DOI}</article-id>'
               f"<title-group><article-title>{TITLE}</article-title></title-group>"
               "</article-meta></front></article>")
    assert identity.judge("<pmc-articleset>" + article + "</pmc-articleset>", DOI, [TITLE]).usable
    assert not identity.judge("<pmc-articleset>" + article + article + "</pmc-articleset>",
                              DOI, [TITLE]).usable


def mime(main, *, charset="utf-8", cte="base64", main_type="text/html"):
    msg = EmailMessage()
    msg["Snapshot-Content-Location"] = "https://example.org/main"
    msg.make_related()
    for location, content in [("https://example.org/frame", page(doi=OTHER)),
                              ("https://example.org/main", main)]:
        part = EmailMessage()
        part.set_content(content, subtype="html", charset=charset, cte=cte)
        if location.endswith("/main"):
            part.replace_header("Content-Type", f'{main_type}; charset="{charset}"')
        part["Content-Location"] = location
        msg.attach(part)
    return msg.as_bytes(policy=email.policy.SMTP)


@pytest.mark.parametrize("cte", ["base64", "quoted-printable"])
def test_main_mime_part_is_selected_and_decoded(tmp_path, cte):
    raw = mime(page(), cte=cte)
    path = tmp_path / "capture.mhtml.gz"
    path.write_bytes(gzip.compress(raw))
    assert identity.judge(identity.read_capture(path), DOI, [TITLE]).usable
    assert identity.judge(raw.decode("ascii"), DOI, [TITLE]).usable


def test_xhtml_main_part_is_not_discarded_as_unreadable(tmp_path):
    path = tmp_path / "capture.mhtml.gz"
    path.write_bytes(gzip.compress(mime(page(), main_type="application/xhtml+xml")))
    assert identity.judge(identity.read_capture(path), DOI, [TITLE]).usable


def test_part_charset_is_honoured_for_title_only_identity(tmp_path):
    title = "\u0422\u0435\u0441\u0442\u043e\u0432\u043e\u0435 \u0438\u0441\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u043d\u0438\u0435 of intervention outcomes"
    path = tmp_path / "capture.mhtml.gz"
    path.write_bytes(gzip.compress(mime(page(title, None), charset="windows-1251")))
    assert identity.judge(identity.read_capture(path), DOI, [title]).usable


def test_missing_capture_is_unreadable(tmp_path):
    assert identity.judge(identity.read_capture(tmp_path / "absent"), DOI).verdict == "unreadable"


@pytest.mark.parametrize("wrapper", ["<!-- {} -->", "<script>const x = '{}';</script>",
                                   "<style>/* {} */</style>", "<template>{}</template>"])
def test_inert_title_cannot_verify_the_requested_paper(wrapper):
    fake = wrapper.format(f"<title>{TITLE}</title>")
    capture = f"<html><head>{fake}<title>Growing vegetables in a garden</title></head><body>Unrelated article</body></html>"
    assert identity.page_title(capture) == "Growing vegetables in a garden"
    assert not identity.judge(capture, DOI, [TITLE]).usable


@pytest.mark.parametrize("prefix", ["<body><div>Consent banner</div>", "<body><p>Journal navigation</p>"])
def test_real_document_title_after_publisher_body_prefix_is_preserved(prefix):
    capture = f"<html>{prefix}<head><title>{TITLE}</title></head></body></html>"
    assert identity.page_title(capture) == TITLE
    assert identity.judge(capture, DOI, [TITLE]).usable


@pytest.mark.parametrize("widget", [
    '<div id="yui3-css-stamp" style="position:absolute;visibility:hidden !important"></div>',
    '<iframe height="0" width="0"></iframe>',
])
def test_publisher_hidden_head_widgets_do_not_hide_own_doi(widget):
    capture = page(head=widget).replace('<title>', widget + '<title>', 1)
    assert identity.declared_dois(capture) == [DOI]
    assert identity.judge(capture, DOI, [TITLE]).reason == "own-doi"


def test_hidden_widget_metadata_is_not_the_documents_own_declaration():
    widget = f'<iframe height="0" width="0"><meta name="citation_doi" content="{DOI}"></iframe>'
    capture = page("Unrelated gardening article", None, head=widget)
    assert not identity.judge(capture, DOI, [TITLE]).usable


def test_hidden_widget_exposes_wrong_cochrane_version_instead_of_title_rescue():
    widget = '<div id="yui3-css-stamp" style="visibility:hidden"></div>'
    capture = page(head=widget, doi=OTHER).replace('<title>', widget + '<title>', 1)
    assert identity.judge(capture, DOI, [TITLE]).verdict == "other-doi"


@pytest.mark.parametrize("widget", [
    '<div id="yui3-css-stamp" style="position: absolute !important; visibility: hidden !important"></div>',
    '<iframe height="0" width="0"></iframe>',
])
def test_actual_chrome_widgets_before_head_do_not_hide_later_doi(widget):
    capture = page(doi=OTHER).replace("<html><head>", "<html>" + widget + "<head>")
    assert identity.declared_dois(capture) == [OTHER]
    assert identity.judge(capture, DOI, [TITLE]).verdict == "other-doi"


@pytest.mark.parametrize("body", [f"DOI: {DOI}", f"DOI: https://doi.org/{DOI}",
                                  f'<p>Digital Object Identifier:</p><a>{DOI}</a>'])
def test_visible_self_identifier_supports_a_label_title_or_mass_notice(body):
    capture = page("Journal citation", None, body=body)
    assert identity.judge(capture, DOI, [TITLE]).reason == "body-doi"
    assert identity.judge(capture, DOI, [TITLE], papers=50).usable


def test_short_exact_title_and_first_visible_doi_url_agree():
    capture = page("Retracted: M", None, body=f"https://doi.org/{DOI}")
    assert identity.judge(capture, DOI, ["M"]).reason == "body-doi"


def test_first_visible_doi_cannot_be_replaced_with_a_later_reference():
    capture = page("Journal citation", None,
                   body=f"DOI: {OTHER}<h2>References</h2><p>DOI: {DOI}</p>")
    assert not identity.judge(capture, DOI, [TITLE]).usable


def test_notice_citing_an_article_is_not_the_article_identity():
    capture = page("Retraction Notice", None, body=f"Article DOI: {DOI}")
    assert not identity.judge(capture, DOI, [TITLE], kind="original").usable


@pytest.mark.parametrize("attrs", ["hidden", 'style="display:none"', 'style="visibility:hidden"'])
def test_hidden_doi_is_not_visible_identity(attrs):
    capture = page("Unrelated gardening article", None, f"<div {attrs}>DOI: {DOI}</div>")
    assert not identity.judge(capture, DOI, [TITLE]).usable


def test_conflicting_visible_self_doi_cannot_be_rescued_by_the_same_title():
    capture = page(TITLE, None, f"<p>DOI: {OTHER}</p>")
    assert identity.judge(capture, DOI, [TITLE]).verdict == "other-doi"


def test_status_prefix_cannot_reject_a_document_with_its_own_original_doi():
    assert identity.judge(page("WITHDRAWN: " + TITLE), DOI, [TITLE], kind="original").usable


def test_original_doi_notice_only_landing_records_content_role_not_an_alias():
    capture = page("Retraction: " + TITLE, DOI, '<div class="article-section__abstract">'
                   '<div class="article-section__content"><p>Retraction Statement: This article has been retracted.</p></div></div>')
    result = identity.judge(capture, DOI, [TITLE], kind="original")
    assert result.usable and result.matched_role == "retraction" and result.reason == "own-doi"


@pytest.mark.parametrize("doi", ["10.1016/s0140-6736(96)91545-7", "10.1234/a(b)"])
def test_parentheses_belonging_to_a_doi_are_preserved(doi):
    assert identity.body_identifier(page("Journal", None, f"<p>DOI: {doi}</p>")) == doi


def test_article_subtitle_is_not_removed_as_a_website_suffix():
    title = "Clinical interventions - a systematic review of treatment outcomes"
    assert identity.judge(page(title + " | IEEE Xplore", None), DOI, [title]).usable
    assert identity.fetch._norm_title(title, site_suffix=True) == identity.fetch._norm_title(title)


def test_long_complete_document_title_is_not_discarded():
    title = TITLE + " " + "a detailed controlled intervention study " * 12
    assert identity.page_title(page(title, None)) == title.strip()
    assert identity.judge(page(title, None), DOI, [title]).usable


def test_verifier_dry_run_preserves_database_bytes(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("verify_audit_test", ROOT / "scripts/verify_archives.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    path = tmp_path / "pipeline.sqlite"
    conn = sqlite3.connect(path)
    db.init(conn)
    archive = tmp_path / "page.html.gz"
    archive.write_bytes(gzip.compress(page(doi=OTHER).encode()))
    conn.execute("INSERT INTO documents (doi,doi_prefix,shard,status,mhtml_path,usable) VALUES (?,?,?,?,?,?)",
                 (DOI, "10.1234", 0, "done", str(archive), 1))
    conn.commit()
    conn.close()
    before = path.read_bytes()
    monkeypatch.setattr(config, "DB_PATH", path)
    out = tmp_path / "audit.csv"
    monkeypatch.setattr(sys, "argv", ["verify_archives", "--dry-run", "--out", str(out)])
    assert verifier.main() == 0
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    assert "other-doi" in out.read_text(encoding="utf-8")
