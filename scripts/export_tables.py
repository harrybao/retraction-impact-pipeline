"""Export the two tables that only lived in SQLite: crawl progress and engagement.

Both are re-generated from scratch on every run, so the CSVs never drift from
the database. Written for reporting to the team - the numbers in a status
update should come from here, not from a hand-typed summary.
"""
import csv
import gzip
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rwpipe import config  # noqa: E402

# Through config, like every other script, so RWPIPE_DATA is honoured. The
# paths were written out from ROOT, which made an R run against a scratch
# directory read the collected database and overwrite its exports.
DB = config.DB_PATH
OUT = config.DATA / "clean"


def dump(conn, sql, path):
    cur = conn.execute(sql)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow([d[0] for d in cur.description])
        n = 0
        for row in cur:
            w.writerow(row)
            n += 1
    print(f"{path.name:<28} {n:>7,} rows")


def dump_gz(conn, sql, path):
    """Same, gzipped. For the tables that are too large to hand anyone as a
    plain CSV - altmetric_posts alone is 1.4 million rows."""
    cur = conn.execute(sql)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([d[0] for d in cur.description])
        n = 0
        for row in cur:
            w.writerow(row)
            n += 1
    print(f"{path.name:<28} {n:>7,} rows  ({path.stat().st_size/1e6:.0f} MB gz)")


# Per-publisher crawl progress - the one small enough to paste into a slide.
CRAWL_BY_PREFIX = """
select doi_prefix                                              as doi_prefix,
       count(*)                                                as total,
       sum(status = 'done')                                    as done,
       sum(status = 'blocked')                                 as blocked,
       sum(status = 'failed')                                  as failed,
       sum(status = 'pending')                                 as pending,
       round(100.0 * sum(status = 'done') / count(*), 1)       as done_pct,
       round(sum(case when status = 'done' then gz_bytes end)
             / 1048576.0, 1)                                   as archived_mb
from documents
group by doi_prefix
order by total desc
"""

# Per-DOI crawl outcome - the audit trail behind the summary above.
CRAWL_BY_DOI = """
select doi, doi_prefix, status, attempts, final_host,
       http_status, mhtml_path, gz_bytes, error, fetched_at
from documents
order by doi_prefix, doi
"""

# The Stage 3 input: citation counts split around the retraction year.
ENGAGEMENT = """
select e.doi, e.record_id, r.title, r.journal, r.publisher,
       e.retraction_year, e.work_type,
       e.cited_by_count, e.citations_before, e.citations_during,
       e.citations_after, e.referenced_works_count,
       e.is_oa, e.wikipedia_count, e.counts_by_year, e.openalex_id
from engagement e
left join records r on r.record_id = e.record_id
where e.error is null
order by e.cited_by_count desc
"""

# Publishers proven unreachable. The threshold encodes the rule adopted after
# an earlier requeue was justified by 2 successes out of 6: no success-rate
# claim from fewer than several dozen attempts. A prefix qualifies only once it
# has been tried >= 30 times and answered under 2% of them.
BLOCKED = """
select doi_prefix, count(*) total,
       sum(status = 'done')    done,
       sum(status = 'blocked') blocked,
       sum(status = 'failed')  failed,
       sum(status = 'pending') pending,
       sum(status in ('done','blocked','failed')) tried,
       round(100.0 * sum(status = 'done')
             / sum(status in ('done','blocked','failed')), 2) done_pct
from documents
group by doi_prefix
having tried >= 30 and done_pct < 2.0
order by total desc
"""


# Altmetric, in full. `found = 0` is a result and is kept: it means Altmetric
# has no record of this paper, i.e. it attracted no traceable public attention.
# Dropping those rows would leave a file describing only the papers that
# happened to be noticed, and every percentage computed from it would be wrong.
ALTMETRIC = """
select a.doi, r.title, r.journal, r.publisher, e.retraction_year,
       a.found, a.score, a.news_count, a.social_count,
       json_extract(a.counts_json, '$.cited_by_wikipedia_count') as wikipedia_mentions,
       json_extract(a.counts_json, '$.cited_by_feeds_count')     as blog_mentions,
       json_extract(a.counts_json, '$.cited_by_policies_count')  as policy_mentions,
       json_extract(a.counts_json, '$.cited_by_patents_count')   as patent_mentions,
       json_extract(a.counts_json, '$.readers_count')            as readers,
       e.cited_by_count, e.citations_after,
       a.details_url, a.counts_json
from altmetric a
left join engagement e on e.doi = a.doi
left join records    r on r.record_id = e.record_id
order by a.news_count desc, a.score desc
"""

# Citation data on its own, with the retraction timeline attached. This file
# and `api_recovered.csv` below were shipped in the bundle for two weeks with
# no generator in the repository - they were produced once by a throwaway
# script, so a rebuild from the database silently reproduced every other file
# and left these two frozen at their August sizes. `api_recovered.csv` went out
# holding 31,237 rows when the table had grown to 42,806.
#
# The two derived columns carry the guard that the shipped version did not.
# `cited_after_retraction` computed as `citations_after > 0` over the whole
# table is the headline error this project keeps re-finding: OpenAlex reports
# citations for a rolling recent window, so a paper retracted before that
# window opens has citations_before = 0 by construction and any later citation
# makes the retraction look like it attracted attention. NULL where the
# comparison cannot be made; a missing measurement is not a negative result.
OPENALEX_ENGAGEMENT = """
select e.doi, e.record_id, r.title, r.journal, r.publisher,
       r.retraction_nature,
       -- Retraction Watch writes '5/19/2011 0:00', so the year is the four
       -- characters nine from the end. One record of the 71,698 has a date
       -- that does not parse and yields 0; a publication year of 0 would
       -- otherwise travel into years_to_retraction as 2011, which reads as a
       -- real and extraordinary finding. Bound it and let it be NULL.
       case when cast(substr(r.original_date, -9, 4) as integer)
                 between 1700 and 2027
            then cast(substr(r.original_date, -9, 4) as integer)
            end                                          as original_year,
       e.retraction_year,
       case when cast(substr(r.original_date, -9, 4) as integer)
                 between 1700 and 2027
            then e.retraction_year
                 - cast(substr(r.original_date, -9, 4) as integer)
            end                                          as years_to_retraction,
       e.cited_by_count, e.citations_before, e.citations_during,
       e.citations_after,
       e.window_covers_retraction                        as citation_window_ok,
       case when e.window_covers_retraction = 1
            then (e.citations_after > 0) else null end   as cited_after_retraction,
       case when e.window_covers_retraction = 1
             and e.citations_before + e.citations_during
                 + e.citations_after > 0
            then round(100.0 * e.citations_after
                       / (e.citations_before + e.citations_during
                          + e.citations_after), 1)
            else null end                                as pct_citations_after,
       e.referenced_works_count, e.is_oa, e.work_type,
       e.counts_by_year, e.openalex_id, e.error
from engagement e
left join records r on r.record_id = e.record_id
order by e.cited_by_count desc
"""

# What Crossref / Europe PMC / OpenAlex returned for pages the crawler could
# not fetch. `abstract` is kept in full and `abstract_chars` added beside it,
# because "did the API actually recover anything" is the question this file
# exists to answer and a length is the fastest way to ask it across 42,806
# rows. `fetched_at` is included: these were collected over several weeks and
# a row's age is part of reading it.
API_RECOVERED = """
select doi, sources, title, publisher, journal, issued, work_type,
       abstract, abstract_source, length(coalesce(abstract, '')) as abstract_chars,
       reference_count, update_types, related_dois,
       is_oa, oa_url, pmid, pmcid, tdm_links, error, fetched_at
from api_records
order by doi
"""

# PlumX, the third attention source. Collected for all 62,867 papers and then
# never exported, so it appeared in the README table and in no file anyone
# could open - the one source of the three nobody outside this machine could
# check.
#
# `found = 0` is kept for the same reason it is kept in ALTMETRIC: PlumX
# answers HTTP 200 with an empty envelope rather than 404, so "no record" is a
# measurement, not a failure, and dropping those rows would leave a file
# describing only the papers PlumX happens to know about.
#
# Two caveats a reader needs and cannot get from the numbers alone:
#   * usage_count is populated on 451 of the 59,172 records that exist (0.8%).
#     PlumX reports usage only where the publisher feeds it; a 0 here means
#     "not reported", not "never read". Do not average it.
#   * citation_count is PlumX's own count and disagrees with OpenAlex's. Two
#     sources counting differently is a finding to report, not a column to
#     silently pick a winner from - both are shipped, joined on doi.
PLUMX = """
select p.doi, r.title, r.journal, r.publisher, e.retraction_year,
       p.found, p.plumx_id,
       p.usage_count, p.capture_count, p.mention_count,
       p.social_count, p.citation_count,
       e.cited_by_count                          as openalex_cited_by_count,
       e.citations_after,
       a.score                                   as altmetric_score,
       p.totals_json, p.detail_json, p.error, p.fetched_at
from plumx p
left join engagement e on e.doi = p.doi
left join altmetric  a on a.doi = p.doi
left join records    r on r.record_id = e.record_id
order by p.capture_count desc, p.citation_count desc
"""

# One row per (paper, Wikipedia edition, article). English-only counts live in
# engagement.wikipedia_count; this is the multilingual sweep.
WIKIPEDIA = """
select w.doi, w.lang, w.title as wikipedia_article,
       r.title as paper_title, e.retraction_year,
       e.citations_after, a.news_count
from wikipedia_cites w
left join engagement e on e.doi = w.doi
left join records    r on r.record_id = e.record_id
left join altmetric  a on a.doi = w.doi
order by w.doi, w.lang, w.title
"""

# The join Stage 3 actually needs: every signal per paper, one row each.
# Left joins throughout - a paper missing from Altmetric or never swept for
# Wikipedia keeps its citation data rather than dropping out of the file.
MASTER = """
select e.doi,
       r.title, r.journal, r.publisher, e.retraction_year,
       e.cited_by_count, e.citations_before, e.citations_during, e.citations_after,
       -- Only where the yearly data actually covers the retraction. OpenAlex
       -- reports a rolling recent window, so for a paper retracted before the
       -- window opens citations_before is 0 by construction and any later
       -- citation makes it look like the retraction attracted attention.
       -- 8,220 papers are in that position. NULL, not 0, where the comparison
       -- cannot be made - a missing measurement is not a negative result.
       e.window_covers_retraction                      as citation_window_ok,
       case when e.window_covers_retraction = 1
             and e.citations_after > e.citations_before
            then 1
            when e.window_covers_retraction = 1 then 0
            else null end                              as cited_more_after_retraction,
       a.found                                         as in_altmetric,
       a.score                                         as altmetric_score,
       a.news_count, a.social_count,
       e.wikipedia_count                               as wikipedia_en,
       (select count(*) from wikipedia_cites w where w.doi = e.doi)
                                                       as wikipedia_all_langs,
       (select count(distinct w.lang) from wikipedia_cites w where w.doi = e.doi)
                                                       as wikipedia_editions,
       d1.status as article_page, d2.status as notice_page,
       e.is_oa, e.work_type, e.openalex_id,
       -- Every paper, including the 378 OpenAlex does not know. Filtering on
       -- e.error dropped them, and their Altmetric data with them: 4 had news
       -- (found 2026-09-18). Their citation columns are NULL; filter here.
       case when e.error is null then 1 else 0 end    as in_openalex
from engagement e
left join records   r  on r.record_id = e.record_id
left join altmetric a  on a.doi = e.doi
left join documents d1 on d1.doi = e.doi
left join documents d2 on d2.doi = r.retraction_doi
order by e.citations_after desc
"""


# Per resolved host, not per DOI prefix. The prefix says who registered the
# DOI; the host says who actually answered the request, and one publisher's
# prefix fans out across several sites with very different behaviour -
# 10.1016 reaches sciencedirect.com at 100% and cell.com at 0%. Success has to
# be charged to where the request landed.
CRAWL_BY_HOST = """
select final_host                                          as host,
       count(*)                                            as pages,
       sum(status = 'done')                                as done,
       sum(status = 'blocked')                             as blocked,
       sum(status = 'failed')                              as failed,
       round(100.0 * sum(status = 'done') / count(*), 1)   as done_pct,
       round(sum(case when status = 'done' then gz_bytes end)
             / 1048576.0, 1)                               as archived_mb,
       sum(capture_format = 'html')                        as html_captures
from documents
where final_host is not null
group by final_host
order by pages desc
"""

# Every distinct failure cause with its count, so a reader can see what
# "blocked" actually consisted of rather than taking the word for it.
FAILURE_REASONS = """
select error                                  as reason,
       count(*)                               as pages,
       count(distinct final_host)             as hosts,
       count(distinct doi_prefix)             as publishers
from documents
where status in ('blocked', 'failed') and error is not null
group by error
order by pages desc
"""



# ---------------------------------------------------------------------------
# Tables that existed only inside the 4 GB SQLite file. Everything below was
# collected, counted in a status update, and then not shipped - which means
# nobody outside this machine could check any of it.

# The timestamped mentions themselves, not the totals. This is the Stage 3
# input: 1.4 million news articles, blog posts and social posts with a date,
# which is what makes a before/after comparison against the retraction date
# possible at all. `raw` is dropped - it is the full API response per post and
# would multiply the file size for data already in the other columns.
ALTMETRIC_POSTS = """
with notices as (
  -- Retraction Watch writes '3/15/2012 0:00'. Compared by year, 795,229 of
  -- the 1.4 million posts land in the retraction year itself and become
  -- unusable for a before/after split - more than half the dataset thrown
  -- into a bucket that answers nothing. The full date is in the CSV; it just
  -- has to be parsed, so parse it.
  select original_doi, retraction_nature,
         printf('%04d-%02d-%02d',
           cast(substr(substr(retraction_date,
                 instr(retraction_date, '/') + 1),
                 instr(substr(retraction_date, instr(retraction_date, '/') + 1),
                       '/') + 1, 4) as integer),
           cast(substr(retraction_date, 1,
                 instr(retraction_date, '/') - 1) as integer),
           cast(substr(substr(retraction_date, instr(retraction_date, '/') + 1),
                 1,
                 instr(substr(retraction_date, instr(retraction_date, '/') + 1),
                       '/') - 1) as integer)
         ) as retracted_on
  from records
  where retraction_date like '%/%/%'
    and original_doi is not null and original_doi <> ''
),
rd as (
  -- One date per paper, and the same one for every post about it. Until
  -- 2026-09-16 the date came from whichever record `engagement.record_id`
  -- happened to point at, so for 1,126 papers it was an expression of concern
  -- or a correction rather than the retraction, and a second definition (the
  -- earliest notice of any kind) was circulating in the group: two answers to
  -- "how much attention came after retraction". The rule now: the earliest
  -- record of nature 'Retraction'; for a paper with no Retraction record, the
  -- earliest notice of any kind - and `date_basis` says which, so the 2,462
  -- such papers can be analysed apart or dropped.
  select original_doi as doi,
         coalesce(min(case when retraction_nature = 'Retraction' then retracted_on end),
                  min(retracted_on))                                   as retracted_on,
         case when max(retraction_nature = 'Retraction') = 1
              then 'earliest Retraction record'
              else 'earliest notice, no Retraction record' end         as date_basis
  from notices
  group by original_doi
)
select p.doi, p.kind, substr(p.posted_on, 1, 10) as posted_on,
       p.title, p.summary, p.url, p.author, p.author_url,
       p.country, p.license,
       -- the post's own identity, so an article filed under two DOIs, or one
       -- with no URL, can be counted once (R/notice_news.R article_key)
       p.post_id,
       rd.retracted_on, rd.date_basis,
       cast(substr(rd.retracted_on, 1, 4) as integer)    as retraction_year,
       case when rd.retracted_on is null or p.posted_on is null then null
            when substr(p.posted_on, 1, 10) < rd.retracted_on then 'before'
            when substr(p.posted_on, 1, 10) = rd.retracted_on then 'same-day'
            else 'after' end                             as relative_to_retraction,
       case when rd.retracted_on is null or p.posted_on is null then null
            else cast(julianday(substr(p.posted_on, 1, 10))
                      - julianday(rd.retracted_on) as integer)
       end                                               as days_from_retraction
from altmetric_posts p
left join rd on rd.doi = p.doi
order by p.doi, p.posted_on
"""

# How many posts each paper has, and of what kind. The index to the file above.
ALTMETRIC_DETAIL = """
select d.doi, r.title, r.journal, e.retraction_year,
       d.posts_total, d.kinds, d.error, d.fetched_at
from altmetric_detail d
left join engagement e on e.doi = d.doi
left join records    r on r.record_id = e.record_id
order by d.posts_total desc
"""

# Where a free copy exists, and on whose server. The route that fills the gap
# left by the publishers who refuse us.
OA_LOCATIONS = """
select o.doi, o.is_oa, o.oa_status, o.host, o.version, o.license,
       o.n_locations, o.landing_url, o.pdf_url, o.error, o.fetched_at
from oa_locations o
order by o.doi
"""

OA_ALL_LOCATIONS = """
select doi, idx, is_oa, host, version, license, source_name,
       landing_url, pdf_url
from oa_all_locations
order by doi, idx
"""

# Why each archive was accepted or rejected. The evidence behind
# `documents.usable`, so a reader can disagree with the rule rather than
# having to take the flag on trust.
ARCHIVE_CHECKS = """
select doi, host, doc_role, gz_bytes, doi_found, title_similarity,
       role_ok, verdict, page_title, checked_at
from archive_checks
order by verdict, doi
"""

# Pages recovered from the Internet Archive when the publisher refused us.
WAYBACK = """
select doi, snapshot_date, status, bytes, snapshot_url, fetched_at
from wayback_captures
order by doi
"""

# Every feasibility probe, with the IP it went out from. Kept because a probe
# result is only meaningful together with where it was run: the same host
# answers differently from a home connection and from the campus network.
PROBES = """
select run, target, item, verdict, http_status, bytes,
       -- Which network the probe went out from is the analytical point; the
       -- address itself is a home connection tied to a named person, and it
       -- was being shipped in 42 rows of a file the whole team and the client
       -- can read. "Home or campus" answers every question the column was
       -- added for.
       case
         when exit_ip like '%Sydney%'  then 'University of Sydney network'
         when exit_ip like '%AARNet%'  then 'University of Sydney network'
         when exit_ip is null or exit_ip = '' then ''
         when exit_ip = 'home'         then 'residential (AU)'
         else 'residential (AU)'
       end                                     as exit_network,
       detail, probed_at
from probe_results
order by run, target, item
"""

# `outreach` is deliberately NOT exported. It holds the names and email
# addresses of the publisher staff who replied to us. Those are third parties'
# contact details, and a shared bundle is the wrong place for them.


OUT.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
    dump(conn, CRAWL_BY_PREFIX, OUT / "crawl_by_publisher.csv")
    dump(conn, CRAWL_BY_DOI, OUT / "crawl_by_doi.csv")
    dump(conn, ENGAGEMENT, OUT / "engagement.csv")
    dump(conn, OPENALEX_ENGAGEMENT, OUT / "openalex_engagement.csv")
    dump(conn, API_RECOVERED, OUT / "api_recovered.csv")
    dump(conn, BLOCKED, OUT / "blocked_publishers.csv")
    dump(conn, ALTMETRIC, OUT / "altmetric.csv")
    dump(conn, PLUMX, OUT / "plumx.csv")
    dump(conn, WIKIPEDIA, OUT / "wikipedia_citations.csv")
    dump(conn, MASTER, OUT / "impact_master.csv")
    dump(conn, CRAWL_BY_HOST, OUT / "crawl_by_host.csv")
    dump(conn, FAILURE_REASONS, OUT / "failure_reasons.csv")
    dump(conn, ALTMETRIC_DETAIL, OUT / "altmetric_detail.csv")
    dump(conn, OA_LOCATIONS, OUT / "oa_locations.csv")
    dump(conn, OA_ALL_LOCATIONS, OUT / "oa_all_locations.csv")
    dump(conn, ARCHIVE_CHECKS, OUT / "archive_checks.csv")
    dump(conn, WAYBACK, OUT / "wayback_captures.csv")
    dump(conn, PROBES, OUT / "probe_results.csv")
    dump_gz(conn, ALTMETRIC_POSTS, OUT / "altmetric_posts.csv.gz")
