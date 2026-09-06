"""SQLite-backed crawl frontier.

A resumable queue is not optional here: ~133k pages at a polite crawl rate
means the job runs for days across multiple machines and will be interrupted.
Every page row carries its own status, attempt count and error, so a restart
picks up exactly where it stopped.
"""
from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS records (
    record_id         INTEGER PRIMARY KEY,
    title             TEXT,
    subject           TEXT,
    journal           TEXT,
    publisher         TEXT,
    country           TEXT,
    article_type      TEXT,
    retraction_nature TEXT,
    reason            TEXT,
    paywalled         TEXT,
    retraction_date   TEXT,
    original_date     TEXT,
    original_doi      TEXT,
    retraction_doi    TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    page_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   INTEGER NOT NULL REFERENCES records(record_id),
    kind        TEXT NOT NULL CHECK (kind IN ('original','retraction')),
    doi         TEXT NOT NULL,
    doi_prefix  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','done','failed','blocked','skipped')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    final_url   TEXT,
    final_host  TEXT,
    http_status INTEGER,
    mhtml_path  TEXT,
    mhtml_bytes INTEGER,   -- uncompressed capture size
    gz_bytes    INTEGER,   -- bytes actually on disk
    elapsed_ms  INTEGER,
    error       TEXT,
    fetched_at  TEXT,
    UNIQUE (record_id, kind)
);

-- One row per DISTINCT DOI - the actual unit of fetching.
-- Many records can share one page: a single mass-retraction notice can cover
-- hundreds of papers (one in this dataset covers 659), and 13,655 records
-- carry the same DOI in both columns. Fetching per record would repeat 22,883
-- requests for pages already on disk, and burn 22,883 extra chances of being
-- rate-limited for nothing.
CREATE TABLE IF NOT EXISTS documents (
    doi         TEXT PRIMARY KEY,
    doi_prefix  TEXT NOT NULL,
    -- Stable hash of the DOI. `shard % N` splits the work N ways for N team
    -- members: deterministic, needs no coordination, and gives everyone the
    -- same publisher mix so no one person inherits a slow publisher.
    shard       INTEGER,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','done','failed','blocked','skipped')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    final_url   TEXT,
    final_host  TEXT,
    http_status INTEGER,
    mhtml_path  TEXT,
    mhtml_bytes INTEGER,
    gz_bytes    INTEGER,
    -- 'mhtml' (Chromium, the client's requested format) or 'html'
    -- (WebKit, for publishers that refuse Chromium - see storage.write_capture)
    capture_format TEXT,
    elapsed_ms  INTEGER,
    error       TEXT,
    fetched_at  TEXT
);

-- What the official APIs could recover for pages the crawler cannot reach.
-- Kept in its own table rather than merged into `documents`: this is a
-- different kind of evidence, from a different source, and the report has to
-- be able to say exactly which fields came from a rendered page and which
-- came from an API.
CREATE TABLE IF NOT EXISTS api_records (
    doi             TEXT PRIMARY KEY,
    sources         TEXT,
    title           TEXT,
    publisher       TEXT,
    journal         TEXT,
    issued          TEXT,
    work_type       TEXT,
    abstract        TEXT,
    abstract_source TEXT,
    reference_count INTEGER,
    update_types    TEXT,     -- 'retraction', 'correction', ...
    related_dois    TEXT,     -- the DOI on the other side of that relationship
    is_oa           INTEGER,
    oa_url          TEXT,
    pmid            TEXT,
    pmcid           TEXT,
    tdm_links       TEXT,     -- publisher-registered text-mining endpoints
    error           TEXT,
    fetched_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_abstract ON api_records(abstract_source);

-- Stage 1b. Keyed on the ORIGINAL paper's DOI: the question is whether the
-- retracted work keeps being cited, so engagement attaches to the paper, not
-- to its retraction notice.
CREATE TABLE IF NOT EXISTS engagement (
    doi                    TEXT PRIMARY KEY,
    record_id              INTEGER,
    openalex_id            TEXT,
    work_type              TEXT,
    cited_by_count         INTEGER,
    counts_by_year         TEXT,    -- JSON {year: count}
    citations_before       INTEGER, -- strictly before the retraction year
    citations_during       INTEGER, -- the retraction year itself: unassignable
    citations_after        INTEGER, -- strictly after - the project's core signal
    referenced_works_count INTEGER,
    is_oa                  INTEGER,
    wikipedia_count        INTEGER,
    wikipedia_pages        TEXT,
    retraction_year        INTEGER,
    error                  TEXT,
    fetched_at             TEXT,
    -- OpenAlex reports citations for a rolling recent window, not the full
    -- history. Where the window opens after the retraction, citations_before
    -- is 0 by construction and the paper looks like it gained citations by
    -- being retracted. 1 only when a before/after comparison is meaningful.
    window_covers_retraction INTEGER
);

-- Stage 1b, the news and social half. Separate from `engagement` because the
-- two answer different questions and have different shapes: OpenAlex gives a
-- per-year series that can be split at the retraction date, Altmetric gives
-- running totals with no time axis. Merging them would invite reporting a
-- lifetime total as if it were post-retraction attention.
CREATE TABLE IF NOT EXISTS altmetric (
    doi          TEXT PRIMARY KEY,
    found        INTEGER,   -- 0 = Altmetric has no record: real result, not an error
    altmetric_id TEXT,
    score        REAL,
    news_count   INTEGER,   -- mainstream media outlets
    social_count INTEGER,   -- twitter, facebook, reddit, bluesky, weibo, video
    counts_json  TEXT,      -- every per-source count, verbatim
    details_url  TEXT,
    added_on     INTEGER,
    error        TEXT,
    fetched_at   TEXT
);

CREATE TABLE IF NOT EXISTS plumx (
    doi          TEXT PRIMARY KEY,
    found        INTEGER,   -- 0 = PlumX has no record: real result, not an error
    plumx_id     TEXT,
    usage_count  INTEGER,   -- views, downloads, holdings: NOT available from Altmetric
    capture_count INTEGER,  -- saves into reference managers
    mention_count INTEGER,  -- news, blogs, comments, reviews
    social_count INTEGER,
    citation_count INTEGER,
    totals_json  TEXT,      -- the five category totals, verbatim
    detail_json  TEXT,      -- every count_type inside them, verbatim
    error        TEXT,
    fetched_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_plumx_found ON plumx(found);
CREATE INDEX IF NOT EXISTS idx_plumx_usage ON plumx(usage_count);

-- One row per (paper, language edition, article). Kept separate from
-- engagement.wikipedia_count, which is English-only and stays as it is: a
-- clean, comparable measurement rather than one silently redefined halfway
-- through. Altmetric flags 3,174 papers as Wikipedia-cited where an
-- English-only search finds 430; a 60-DOI sample showed 32% of the difference
-- sitting in other language editions, which is what this table captures.
CREATE TABLE IF NOT EXISTS wikipedia_cites (
    doi        TEXT NOT NULL,
    lang       TEXT NOT NULL,   -- wikipedia edition, e.g. 'en', 'zh', 'pt'
    title      TEXT NOT NULL,   -- article citing the paper
    fetched_at TEXT,
    PRIMARY KEY (doi, lang, title)
);

CREATE INDEX IF NOT EXISTS idx_wc_doi  ON wikipedia_cites(doi);
CREATE INDEX IF NOT EXISTS idx_wc_lang ON wikipedia_cites(lang);

-- Which DOIs have been swept, so an interrupted run resumes instead of
-- restarting. A paper with no citations anywhere must be recorded too,
-- otherwise "checked and found nothing" is indistinguishable from "not yet
-- checked" - the same distinction that made 404s worth storing for Altmetric.
CREATE TABLE IF NOT EXISTS wikipedia_swept (
    doi        TEXT PRIMARY KEY,
    langs      TEXT,            -- editions searched, comma separated
    total      INTEGER,         -- articles found across all of them
    fetched_at TEXT
);

-- Every probe, sweep and experiment writes here, one row per attempt.
-- Rates get quoted in reports and to the client, and a rate whose underlying
-- observations were only printed to a terminal cannot be rechecked, rescoped,
-- or defended when someone asks "how many did you try?". The 960 engine
-- attempts and 210 TDM attempts behind this project's central claim existed
-- only as screen output until 2026-08-31.
CREATE TABLE IF NOT EXISTS probe_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run        TEXT NOT NULL,     -- which experiment, e.g. 'sweep:webkit'
    target     TEXT,              -- host, endpoint or DOI probed
    item       TEXT,              -- the specific DOI/URL, when there is one
    verdict    TEXT,              -- ok / http403 / challenge / thin / error
    http_status INTEGER,
    bytes      INTEGER,
    detail     TEXT,
    exit_ip    TEXT,              -- entitlement results depend on where we asked
    probed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_probe_run    ON probe_results(run);
CREATE INDEX IF NOT EXISTS idx_probe_target ON probe_results(target);

-- Stage 2 output: the text pulled out of each archived page.
-- EXTRACTOR_VERSION is stored per row so a change to the extraction rules can
-- be re-run selectively instead of all 69,657 documents or none of them.
CREATE TABLE IF NOT EXISTS document_text (
    doi            TEXT PRIMARY KEY,
    doc_role       TEXT,        -- original | retraction | both
    host           TEXT,
    capture_format TEXT,
    source_path    TEXT,
    page_title     TEXT,        -- <title> at capture time
    title          TEXT,        -- extracted article title
    abstract       TEXT,
    body           TEXT,
    notice         TEXT,        -- the statement of withdrawal
    notice_method  TEXT,        -- selector | cue | none, so a bad rule is traceable
    references_n   INTEGER,
    body_chars     INTEGER,
    method         TEXT,
    mime_parts     INTEGER,
    charset        TEXT,        -- the encoding that actually decoded it
    quality        TEXT,        -- full | notice-only | thin | failed
    extractor_version INTEGER,
    error          TEXT,
    extracted_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_dt_quality ON document_text(quality);
CREATE INDEX IF NOT EXISTS idx_dt_role    ON document_text(doc_role);
CREATE INDEX IF NOT EXISTS idx_dt_version ON document_text(extractor_version);

CREATE INDEX IF NOT EXISTS idx_alt_news  ON altmetric(news_count);
CREATE INDEX IF NOT EXISTS idx_alt_found ON altmetric(found);

CREATE INDEX IF NOT EXISTS idx_eng_after ON engagement(citations_after);
CREATE INDEX IF NOT EXISTS idx_eng_wiki  ON engagement(wikipedia_count);

CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_docs_prefix ON documents(doi_prefix, status);
CREATE INDEX IF NOT EXISTS idx_docs_shard  ON documents(shard, status);

CREATE INDEX IF NOT EXISTS idx_pages_status  ON pages(status);
CREATE INDEX IF NOT EXISTS idx_pages_prefix  ON pages(doi_prefix, status);
CREATE INDEX IF NOT EXISTS idx_pages_host    ON pages(final_host);

-- Where a paper is legally readable for free, if anywhere.
--
-- 10,388 of the documents we cannot get from a publisher are flagged open
-- access by OpenAlex, and that flag was already in `engagement` - but only the
-- flag, not the address, so it could not be acted on. This table holds the
-- address, its host, and whether it is a PDF or a page, because those decide
-- two different things: whether we can fetch it at all, and whether the result
-- can be MHTML. MHTML needs a browser rendering a page; a PDF has no such form
-- and pretending otherwise would put a mislabelled format in the deliverable.
CREATE TABLE IF NOT EXISTS oa_locations (
    doi           TEXT PRIMARY KEY,
    is_oa         INTEGER,
    oa_status     TEXT,       -- gold, green, hybrid, bronze, closed
    landing_url   TEXT,       -- an HTML page: capturable as MHTML
    pdf_url       TEXT,       -- a PDF: capturable only as a PDF
    host          TEXT,       -- pmc, arxiv, a repository - not the publisher
    version       TEXT,       -- submittedVersion / acceptedVersion / publishedVersion
    license       TEXT,
    n_locations   INTEGER,
    error         TEXT,
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_oa_host ON oa_locations(host);
CREATE INDEX IF NOT EXISTS idx_oa_isoa ON oa_locations(is_oa);

-- Every free copy, not only the one OpenAlex ranks first.
--
-- `best_oa_location` is OpenAlex's judgement of which copy is best, and its
-- idea of best is the publisher's version of record. That is exactly the copy
-- we cannot fetch: 2,362 of these "open" papers point at iopscience.iop.org,
-- 1,682 at tandfonline.com, 855 at sciencedirect.com. Open access describes the
-- licence, not whether the door is guarded.
--
-- 15,262 of the 20,921 open papers list more than one location, and a copy in
-- PubMed Central or an institutional repository is fetchable where the
-- publisher's is not. Storing only the best one threw those away.
CREATE TABLE IF NOT EXISTS oa_all_locations (
    doi          TEXT NOT NULL,
    idx          INTEGER NOT NULL,   -- position in OpenAlex's list
    is_oa        INTEGER,
    host         TEXT,
    landing_url  TEXT,
    pdf_url      TEXT,
    version      TEXT,
    license      TEXT,
    source_name  TEXT,               -- the repository or journal name
    PRIMARY KEY (doi, idx)
);
CREATE INDEX IF NOT EXISTS idx_oaall_host ON oa_all_locations(host);
CREATE INDEX IF NOT EXISTS idx_oaall_doi  ON oa_all_locations(doi);

-- DOI -> PubMed Central, resolved through NCBI's own ID converter rather than
-- parsed out of a URL. OpenAlex knows of an NCBI copy for 7,833 refused
-- documents, but writes the landing page three different ways
-- (www.ncbi.nlm.nih.gov/pmc/articles/8129940, pmc.ncbi.nlm.nih.gov/...,
-- pubmed.ncbi.nlm.nih.gov/42614536 - the last a PMID, not a PMCID), and a
-- regex over those recovered 1,646 of them. The converter answers for a DOI
-- whether or not OpenAlex ever mentioned PMC, so it is asked about every
-- refused document, not only the ones we already suspected.
--
-- `status` records the converter's answer including its refusals, so a DOI
-- that PMC genuinely does not hold is not re-asked on every run.
CREATE TABLE IF NOT EXISTS pmc_ids (
    doi         TEXT PRIMARY KEY,
    pmcid       TEXT,
    pmid        TEXT,
    status      TEXT,          -- 'found' | 'not-found' | converter error text
    checked_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pmc_pmcid ON pmc_ids(pmcid);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    """One database, several long-running processes.

    The crawler, the enrichment pass and the archive verifier all write here at
    once, and with SQLite's defaults that means a rollback journal, one writer
    at a time, and an immediate `database is locked` for anyone who arrives
    second. A verification run doing bulk writes killed a crawler that had
    already fetched 575 pages - not a slow query, an outright crash.

    WAL lets readers proceed while a writer holds the file, and `busy_timeout`
    makes a competing writer wait its turn instead of failing. Both are
    connection-level settings, so every process that opens the database has to
    ask for them.
    """
    conn = sqlite3.connect(path or config.DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Five minutes, not one. WAL lets readers run during a write, but two
    # writers still take turns - and the archive verifier holds a transaction
    # open while it decompresses hundreds of files, which can exceed a minute
    # easily. A 60s timeout meant the crawler gave up and died mid-run; the
    # waiter has to outlast the slowest writer, not the average one.
    conn.execute("PRAGMA busy_timeout=300000")
    conn.execute("PRAGMA synchronous=NORMAL")     # safe under WAL, much faster
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Add columns that were introduced after a database was first created.

    `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists,
    so a column added to the schema never reaches an existing file. These three
    were added by hand during development, which meant the code ran here and
    would have failed immediately on a fresh checkout - `crawl.py` writes
    `claimed_at` unconditionally.
    """
    wanted = {
        # Where a capture came from, recorded per document rather than assumed.
        # The client asked for "publisher pages from the CSV"; an open-access
        # copy in PubMed Central is the same paper from a different host, and
        # some of those are the author's accepted manuscript rather than the
        # published version of record. Both differences are real, neither is
        # visible in the file, and a dataset that cannot tell you which is
        # which cannot be corrected later - so the columns exist before the
        # first such capture is written, not after someone asks.
        "documents":  [("claimed_at", "TEXT"), ("usable", "INTEGER"),
                       ("source_kind", "TEXT"),    # publisher | oa-mirror
                       ("oa_host", "TEXT"),
                       ("oa_version", "TEXT")],    # read from the page itself
        "document_text": [("notice_method", "TEXT"),
                          ("extractor_version", "INTEGER")],
        "engagement": [("window_covers_retraction", "INTEGER")],
    }
    for table, cols in wanted.items():
        try:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if not have:
            continue
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def init(conn: sqlite3.Connection) -> None:
    # Migrate first. The schema script ends with CREATE INDEX statements, and
    # an index on a column that an existing table has not been given yet fails
    # the whole script - `CREATE TABLE IF NOT EXISTS` silently does nothing for
    # a table that is already there, so the column never arrives on its own.
    migrate(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)      # again, for tables the script has just created


def counts_by_status(conn: sqlite3.Connection, table: str = "documents") -> dict[str, int]:
    rows = conn.execute(
        f"SELECT status, COUNT(*) n FROM {table} GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def _normalise_path(p: str) -> str:
    """Archive paths: data-relative, forward slashes, portable across OSes."""
    p = p.replace("\\", "/")
    return p[len("data/"):] if p.startswith("data/") else p


def sync_documents(conn: sqlite3.Connection) -> int:
    """Collapse the record-level page list into distinct DOIs to fetch.

    Carries over any fetch already completed against the old per-record rows so
    a migration never re-downloads work that is on disk.
    """
    conn.execute(
        """INSERT OR IGNORE INTO documents (doi, doi_prefix)
           SELECT DISTINCT doi, doi_prefix FROM pages"""
    )
    conn.execute(
        """UPDATE documents SET
               status = COALESCE((SELECT p.status FROM pages p
                    WHERE p.doi = documents.doi AND p.status='done' LIMIT 1), status),
               mhtml_path = COALESCE((SELECT p.mhtml_path FROM pages p
                    WHERE p.doi = documents.doi AND p.status='done' LIMIT 1), mhtml_path),
               mhtml_bytes = COALESCE((SELECT p.mhtml_bytes FROM pages p
                    WHERE p.doi = documents.doi AND p.status='done' LIMIT 1), mhtml_bytes),
               gz_bytes = COALESCE((SELECT p.gz_bytes FROM pages p
                    WHERE p.doi = documents.doi AND p.status='done' LIMIT 1), gz_bytes),
               final_host = COALESCE((SELECT p.final_host FROM pages p
                    WHERE p.doi = documents.doi AND p.status='done' LIMIT 1), final_host)
           WHERE EXISTS (SELECT 1 FROM pages p
                         WHERE p.doi = documents.doi AND p.status='done')"""
    )
    # Self-heal legacy archive paths. Early runs stored them repo-relative with
    # Windows separators ("data\mhtml\..."); they are now data-relative with
    # forward slashes so a teammate can resolve them against their own data
    # root on any OS. Normalising here as well as in `pages` matters because
    # this function copies paths *out* of `pages` - fixing only the destination
    # let the old format reappear on every sync.
    for table, key in (("pages", "page_id"), ("documents", "doi")):
        rows = conn.execute(
            f"SELECT {key} k, mhtml_path FROM {table} "
            "WHERE mhtml_path IS NOT NULL AND "
            "(instr(mhtml_path, char(92)) > 0 OR mhtml_path LIKE 'data%')"
        ).fetchall()
        if rows:
            conn.executemany(
                f"UPDATE {table} SET mhtml_path=? WHERE {key}=?",
                [(_normalise_path(r["mhtml_path"]), r["k"]) for r in rows])

    # Assign shards to any row that lacks one.
    todo = conn.execute(
        "SELECT doi FROM documents WHERE shard IS NULL").fetchall()
    if todo:
        conn.executemany(
            "UPDATE documents SET shard=? WHERE doi=?",
            [(zlib.crc32(r["doi"].encode("utf-8")), r["doi"]) for r in todo])
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def record_probe(conn, run: str, target: str, verdict: str, *, item: str | None = None,
                 http_status: int | None = None, nbytes: int | None = None,
                 detail: str | None = None, exit_ip: str | None = None) -> None:
    """One observation from a probe or sweep.

    Called per attempt, not per summary. A summary answers "what was the
    rate"; the rows answer "on what evidence", which is the question that
    actually gets asked in a report or a client meeting.
    """
    conn.execute(
        """INSERT INTO probe_results
           (run, target, item, verdict, http_status, bytes, detail, exit_ip, probed_at)
           VALUES (?,?,?,?,?,?,?,?, datetime('now'))""",
        (run, target, item, verdict, http_status, nbytes, detail, exit_ip))
