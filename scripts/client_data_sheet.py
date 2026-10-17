"""Every figure reported to the client, with its unit and its source. Read-only.

    python scripts/client_data_sheet.py

Written because a bare figure is easily misread: "2.5K" papers with news is not
2.5K news items, and a citation count without its source cannot be checked.
Each row here says what is counted (pages, papers,
notices, news items, mentions, posts), where it comes from, and how it was
obtained, so no figure goes to the client without its unit.

Output: data/reports/client-data-sheet.csv
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "clean"
REPORTS = ROOT / "data" / "reports"
csv.field_size_limit(10**9)

rows: list[list] = []


def add(section: str, measure: str, value, unit: str, source: str) -> None:
    rows.append([section, measure, value, unit, source])
    v = f"{value:,}" if isinstance(value, int) else value
    print(f"  {measure:<66} {v:>12}  {unit}")


def read(name: str) -> list[dict]:
    with open(CLEAN / name, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


con = sqlite3.connect(f"file:{ROOT / 'data' / 'interim' / 'pipeline.sqlite'}?mode=ro", uri=True)
one = lambda s: con.execute(s).fetchone()[0]

# ------------------------------------------------------------------ scope --
print("SCOPE (Retraction Watch CSV)")
rec = read("records_clean.csv")
orig = {r["original_doi"] for r in rec if r["original_doi"]}
notice = {r["retraction_doi"] for r in rec
          if r["retraction_doi"] and r["retraction_doi"] != r["original_doi"]}
queue = read("crawl_by_doi.csv")
add("scope", "usable records", len(rec), "records", "Retraction Watch CSV")
add("scope", "retracted papers (distinct original DOIs)", len(orig), "papers", "Retraction Watch CSV")
add("scope", "retraction-notice DOIs (distinct, not the paper's own)", len(notice), "notices", "Retraction Watch CSV")
add("scope", "pages to collect (distinct DOIs, paper + notice)", len(queue), "pages", "Retraction Watch CSV")

# ---------------------------------------------------------------- stage 1 --
print("STAGE 1 - PAGES")
s2 = read("stage2_index.csv")
kind = Counter()
for r in s2:
    sk = r["source_kind"] or "crawler"
    f = r["capture_format"]
    if f == "mhtml" and sk in ("crawler", "teammate-sage", "teammate-tandf"):
        kind["publisher page saved as MHTML"] += 1
    elif f == "html" and sk != "wayback":
        kind["publisher page saved as HTML"] += 1
    elif f in ("elsxml", "jats"):
        kind["article text from publisher / PubMed Central API (XML/PDF)"] += 1
    else:
        kind["copy of the page from another website"] += 1
add("stage1", "pages collected", len(s2), "pages", "publisher websites + APIs")
for k, n in kind.most_common():
    add("stage1", k, n, "pages", "publisher websites + APIs")
add("stage1", "pages not collected", len(queue) - len(s2), "pages", "publisher refused")
role = Counter(r["doc_role"] for r in s2)
add("stage1", "collected: article pages", role["original"], "pages", "")
add("stage1", "collected: notice pages", role["retraction"], "pages", "")
add("stage1", "collected: DOI that is both article and notice", role["both"], "pages", "")
ai = read("archive_index.csv")
kb = sorted(int(r["gz_bytes"]) / 1024 for r in ai
            if r["capture_format"] == "mhtml" and r["usable"] == "1" and r["gz_bytes"])
add("stage1", "MHTML files kept: median size (compressed)", round(kb[len(kb) // 2]), "KB", "archive")
add("stage1", "MHTML files kept: under 50 KB (compressed)", sum(k < 50 for k in kb), "files", "archive")
# Stored gzip-compressed; the uncompressed size is the gzip trailer's ISIZE
# field (exact for files under 4 GB), so no file has to be inflated.
import struct


def isize(path: Path) -> int:
    with open(path, "rb") as f:
        f.seek(-4, 2)
        return struct.unpack("<I", f.read(4))[0]


raw = sorted(isize(ROOT / "data" / r["path"]) / 1024 for r in ai
             if r["capture_format"] == "mhtml" and r["usable"] == "1")
add("stage1", "MHTML files kept: median size (uncompressed)", round(raw[len(raw) // 2]), "KB", "archive")
add("stage1", "MHTML files kept: 10th / 90th percentile (uncompressed)",
    f"{raw[len(raw) // 10]:.0f} / {raw[len(raw) * 9 // 10]:.0f}", "KB", "archive")
add("stage1", "MHTML files kept: total (compressed / uncompressed)",
    f"{sum(kb) / 1024**2:.1f} / {sum(raw) / 1024**2:.1f}", "GB", "archive")
st = Counter(r["http_status"] or "no response" for r in queue)
for code in ("200", "403", "404", "no response"):
    add("stage1", f"last HTTP status {code}", st[code], "pages", "crawler log")

# ---------------------------------------------------- stage 1 - Altmetric --
print("STAGE 1 - ALTMETRIC, PAPER DOIs")
add("altmetric", "paper DOIs searched", one("select count(*) from altmetric"), "papers", "Altmetric API (client key)")
add("altmetric", "papers with any Altmetric record", one("select count(*) from altmetric where found=1"), "papers", "Altmetric")
add("altmetric", "papers with no Altmetric record", one("select count(*) from altmetric where found=0"), "papers", "Altmetric")
add("altmetric", "post-paper links downloaded (a post about 2 papers counts twice)", one("select count(*) from altmetric_posts"), "links", "Altmetric detail endpoint")
add("altmetric", "distinct posts downloaded", one("select count(*) from (select distinct kind, post_id from altmetric_posts)"), "posts", "Altmetric detail endpoint")
for k, n, p in con.execute("select kind, count(distinct post_id), count(distinct doi) from altmetric_posts "
                           "group by kind order by count(*) desc"):
    add("altmetric", f"{k}: distinct posts", n, "posts", "Altmetric detail endpoint")
    add("altmetric", f"{k}: papers", p, "papers", "Altmetric detail endpoint")
add("altmetric", "news: distinct stories (a story about 2 papers counted once)",
    one("select count(distinct post_id) from altmetric_posts where kind='news'"), "news stories", "Altmetric")
add("altmetric", "news: distinct news outlets per paper, summed (Altmetric summary)",
    one("select sum(cast(news_count as int)) from altmetric"), "outlet-paper pairs", "Altmetric summary endpoint")

print("STAGE 1 - ALTMETRIC, NOTICE DOIs")
add("altmetric_notice", "notice DOIs searched", one("select count(*) from altmetric_notice"), "notices", "Altmetric")
add("altmetric_notice", "notices with any record", one("select count(*) from altmetric_notice where found=1"), "notices", "Altmetric")
add("altmetric_notice", "notices with news", one("select count(*) from altmetric_notice where cast(coalesce(news_count,0) as int)>0"), "notices", "Altmetric")
add("altmetric_notice", "news outlets per notice, summed (Altmetric summary)",
    one("select sum(cast(coalesce(news_count,0) as int)) from altmetric_notice"), "outlet-notice pairs", "Altmetric summary endpoint")
add("altmetric_notice", "notice posts downloaded, distinct (X left out: the key's X allowance is spent)",
    one("select count(*) from (select distinct kind, post_id from altmetric_notice_posts)"), "posts", "Altmetric detail endpoint")
add("altmetric_notice", "news stories on notice DOIs, distinct",
    one("select count(distinct post_id) from altmetric_notice_posts where kind='news'"), "news stories", "Altmetric detail endpoint")
# One article is one article: identified by its normalised URL, or by its post
# id when it has no URL - the rule in R/notice_news.R, reproduced here so the
# sheet and analysis/notice_news_merge.R agree. Counting by raw post id instead
# splits one article whose URL is written two ways (38,288 against 38,239).
def _norm(u):
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www[.]", "", u)
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"([?&])(utm_[^&]*|fbclid=[^&]*|gclid=[^&]*|mc_[ce]id=[^&]*)", r"\1", u)
    u = re.sub(r"([?&])&+", r"\1", u)
    u = re.sub(r"[?&]+$", "", u)
    return re.sub(r"/+$", "", u)


def _key(url, pid):
    k = _norm(url)
    return k if k else "id:" + str(pid)


paper_news = {_key(u, p) for u, p in con.execute("select url, post_id from altmetric_posts where kind='news'")}
notice_news = {_key(u, p) for u, p in con.execute("select url, post_id from altmetric_notice_posts where kind='news'")}
add("altmetric_total", "news articles on paper DOIs, distinct by URL", len(paper_news), "news articles", "Altmetric")
add("altmetric_total", "news articles on notice DOIs, distinct by URL", len(notice_news), "news articles", "Altmetric")
add("altmetric_total", "news articles citing both a paper and its notice (counted once)",
    len(paper_news & notice_news), "news articles", "Altmetric")
add("altmetric_total", "news articles in total, paper and notice DOIs, distinct",
    len(paper_news | notice_news), "news articles", "Altmetric")
papers_of: dict[str, set] = {}
for rd, od in con.execute("select retraction_doi, original_doi from records "
                          "where retraction_doi<>'' and original_doi<>''"):
    papers_of.setdefault(rd, set()).add(od)
art_news = {r[0] for r in con.execute("select distinct doi from altmetric_posts where kind='news'")}
via_notice = set()
for (rd,) in con.execute("select retraction_doi from altmetric_notice where cast(coalesce(news_count,0) as int)>0"):
    via_notice |= papers_of.get(rd, set())
add("altmetric_notice", "papers behind notices with news", len(via_notice), "papers", "Altmetric + CSV")
add("altmetric_notice", "papers with news via notice only (new)", len(via_notice - art_news), "papers", "Altmetric + CSV")
add("altmetric_total", "papers with Altmetric news, paper or notice DOI", len(art_news | via_notice), "papers", "Altmetric")
# No summed news total: the paper side counts stories, the notice side counts
# outlets, and the two overlap. They are reported separately.

# ------------------------------------------------------------- PlumX --
print("STAGE 1 - PLUMX")
pat = re.compile(r'"mention\.NEWS_COUNT"\s*:\s*"?([0-9]+)')
plx = read("plumx.csv")
news_p = [int(m.group(1)) for r in plx if (m := pat.search(r.get("detail_json") or "")) and int(m.group(1)) > 0]
add("plumx", "paper DOIs searched", len(plx), "papers", "PlumX API")
add("plumx", "papers found in PlumX", sum(r["found"] == "1" for r in plx), "papers", "PlumX")
add("plumx", "papers with news", len(news_p), "papers", "PlumX (count only)")
add("plumx", "news mentions on papers", sum(news_p), "mentions", "PlumX (count only)")
add("plumx_notice", "notice DOIs searched", one("select count(*) from plumx_notice"), "notices", "PlumX")
add("plumx_notice", "notices found in PlumX", one("select count(*) from plumx_notice where found=1"), "notices", "PlumX")
nn = [int(m.group(1)) for (d,) in con.execute("select detail_json from plumx_notice")
      if (m := pat.search(d or "")) and int(m.group(1)) > 0]
add("plumx_notice", "notices with news", len(nn), "notices", "PlumX (count only)")
add("plumx_notice", "news mentions on notices", sum(nn), "mentions", "PlumX (count only)")

# ------------------------------------------------------------ OpenAlex --
print("STAGE 1 - OPENALEX")
oa = read("openalex_engagement.csv")
add("openalex", "papers found in OpenAlex", sum(1 for r in oa if not r.get("error")), "papers", "OpenAlex API")
with open(ROOT / "data" / "controls" / "controls_v1.csv", encoding="utf-8-sig", newline="") as fh:
    ctl = list(csv.DictReader(fh))
# Counted by DOI: one control DOI resolves to two OpenAlex works, so counting
# OpenAlex ids gives one more than there are papers.
add("openalex", "comparison papers (distinct OpenAlex works, not in Retraction Watch)", len({r["control_openalex_id"] for r in ctl}), "papers", "OpenAlex API")
add("openalex", "of which have a DOI", len({r["control_doi"] for r in ctl if r["control_doi"]}), "papers", "OpenAlex API")
add("openalex", "comparison links (retracted paper -> comparison paper)", len(ctl), "links", "OpenAlex API")

# ---------------------------------------------------------------- stage 2 --
print("STAGE 2")
add("stage2", "pages with extracted text (article or notice)",
    sum(1 for r in s2 if int(r["body_chars"] or 0) > 0), "pages", "Stage 2")
add("stage2", "pages with full article text", sum(1 for r in s2 if r["quality"] == "full"), "pages", "Stage 2")
add("stage2", "pages with notice text", sum(1 for r in s2 if int(r["notice_chars"] or 0) > 0), "pages", "Stage 2")

# ---------------------------------------------------------------- stage 3 --
# Read from the analysis reports, so the sheet repeats what the R code wrote.
print("STAGE 3")


def report(name: str) -> list[dict]:
    with open(REPORTS / name, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


h = {r["group"]: r for r in report("citation-impact-headline.csv")}
# The ratio compares two CHANGES - retracted papers' citations after against
# before, over the same change for their comparison papers - in a window of k
# years on EACH side of the retraction. It is not "x% of the comparison
# papers' citations", and k = 2 is not "the second year" (project log 2026-10-08).
for g, label in (("k = 1 years each side", "1 year"), ("k = 2 years each side", "2 years"),
                 ("k = 3 years each side", "3 years")):
    r = h[g]
    add("stage3_citations",
        f"citation change after retraction relative to comparison papers, {label} each side",
        f"{float(r['ratio_mh']):.3f}",
        f"Mantel-Haenszel ratio of changes across matched groups ({int(r['papers']):,} papers); "
        f"pooled: retracted x{float(r['treated_change']):.2f} / comparison "
        f"x{float(r['control_change']):.2f} = {float(r['ratio_pooled']):.3f}",
        "OpenAlex")
pl = report("citation-impact-placebo.csv")[0]
add("stage3_citations", "check on two periods before retraction (1.0 = no gap)",
    f"{float(pl['ratio_mh']):.3f}", "ratio", "OpenAlex")
per = {(r["period"], r["told"]): int(r["Freq"]) for r in report("media-classification-told-by-period.csv")}
after = per[("after", "told")] + per[("after", "not told")]
add("stage3_media", "media items published after retraction", after, "items", "Altmetric, classified by local model")
add("stage3_media", "of which say the paper was retracted", f"{100 * per[('after', 'told')] / after:.1f}%", "of items", "")
lab = {(r["period"], r["label"]): r for r in report("media-classification-items-by-period.csv")}
add("stage3_media", "of which repeat the finding without saying it was retracted",
    f"{lab[('after', 'A1')]['share_of_period']}%", "of items", "")
add("stage3_media", "of which challenge the finding", f"{lab[('after', 'A3')]['share_of_period']}%", "of items", "")
kinds = {(r["kind"], r["told"]): r for r in report("media-classification-told-by-kind-after.csv")}
add("stage3_media", "news items after retraction that say it was retracted",
    f"{kinds[('news', 'told')]['share']}%", "of news items", "")
pa = {r["measure"]: r["value"] for r in report("media-classification-papers-after.csv")}
add("stage3_media", "papers covered after retraction", int(pa["papers covered after retraction"]), "papers", "")
add("stage3_media", "of which never get an item saying it was retracted", int(pa["no item acknowledges it"]), "papers", "")
v = {(r[1], r[2]): r[3] for r in csv.reader(open(REPORTS / "llm-validation.csv", encoding="utf-8-sig"))
     if r and r[0] == "qwen3:14b"}
q = "A media v2, is the reader told it was retracted?"
add("stage3_media", "model agrees with the students' majority (says retracted or not)",
    f"{100 * float(v[(q, 'model = majority of the same six (mean)')]):.1f}%", "of items", "7 student labellers")
add("stage3_media", "a single student agrees with the others' majority",
    f"{100 * float(v[(q, 'one labeller = majority of the other six (mean)')]):.1f}%", "of items", "7 student labellers")
ro = {r["reason"]: r for r in report("notice-classification-reasons-overall.csv")}
log = (ROOT / "logs" / "notice_classification-final.log").read_text(encoding="utf-8", errors="replace")
m = re.search(r"retracted papers with a classified notice:\s*([0-9]+)", log)
add("stage3_notices", "papers with a classified notice", int(m.group(1)), "papers", "notice text, local model")
add("stage3_notices", "notices that give no reason", f"{ro['R0_no_reason']['share_of_papers']}%", "of papers", "notice text, local model")

REPORTS.mkdir(parents=True, exist_ok=True)
with open(REPORTS / "client-data-sheet.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["section", "measure", "value", "unit", "source"])
    w.writerows(rows)
print("\nwritten: data/reports/client-data-sheet.csv")
