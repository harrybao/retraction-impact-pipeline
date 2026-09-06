"""Export the two tables that only lived in SQLite: crawl progress and engagement.

Both are re-generated from scratch on every run, so the CSVs never drift from
the database. Written for reporting to the team - the numbers in a status
update should come from here, not from a hand-typed summary.
"""
import csv
import gzip
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "interim" / "pipeline.sqlite"
OUT = ROOT / "data" / "clean"


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
       e.is_oa, e.work_type, e.openalex_id
from engagement e
left join records   r  on r.record_id = e.record_id
left join altmetric a  on a.doi = e.doi
left join documents d1 on d1.doi = e.doi
left join documents d2 on d2.doi = r.retraction_doi
where e.error is null
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
with rd as (
  -- Retraction Watch writes '3/15/2012 0:00'. Compared by year, 795,229 of
  -- the 1.4 million posts land in the retraction year itself and become
  -- unusable for a before/after split - more than half the dataset thrown
  -- into a bucket that answers nothing. The full date is in the CSV; it just
  -- has to be parsed, so parse it.
  select record_id,
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
)
select p.doi, p.kind, substr(p.posted_on, 1, 10) as posted_on,
       p.title, p.summary, p.url, p.author, p.author_url,
       p.country, p.license,
       rd.retracted_on, e.retraction_year,
       case when rd.retracted_on is null or p.posted_on is null then null
            when substr(p.posted_on, 1, 10) < rd.retracted_on then 'before'
            when substr(p.posted_on, 1, 10) = rd.retracted_on then 'same-day'
            else 'after' end                             as relative_to_retraction,
       case when rd.retracted_on is null or p.posted_on is null then null
            else cast(julianday(substr(p.posted_on, 1, 10))
                      - julianday(rd.retracted_on) as integer)
       end                                               as days_from_retraction
from altmetric_posts p
left join engagement e on e.doi = p.doi
left join rd on rd.record_id = e.record_id
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


with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
    dump(conn, CRAWL_BY_PREFIX, OUT / "crawl_by_publisher.csv")
    dump(conn, CRAWL_BY_DOI, OUT / "crawl_by_doi.csv")
    dump(conn, ENGAGEMENT, OUT / "engagement.csv")
    dump(conn, BLOCKED, OUT / "blocked_publishers.csv")
    dump(conn, ALTMETRIC, OUT / "altmetric.csv")
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
