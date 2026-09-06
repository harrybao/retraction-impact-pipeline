# Retraction Impact Pipeline

A data-engineering pipeline that measures what happens to a scientific paper
*after* it is retracted: who keeps citing it, who keeps writing about it, and
whether the retraction is ever acknowledged.

Built for a COMP5703 capstone at the University of Sydney, Semester 2 2026,
over the Crossref / Retraction Watch database.

## Scale

| | |
|---|---:|
| retraction records | 71,698 |
| pages to collect (article + notice, de-duplicated) | 109,888 |
| pages collected and verified | 87,239 (79.4%) |
| records holding both the paper and its retraction notice, as text | 48,389 |
| dated news, social and policy mentions | 1,437,966 |
| distinct publisher hosts encountered | 1,240 |

The archive itself is 31.5 GB compressed. It is not in this repository — see
*Data availability*.

## Two rules the whole thing rests on

**A capture is verified by identity, not by size.** A 200 OK response of the
right length is not evidence. ScienceDirect serves a full-size refusal page
reading *"There was a problem providing the content you requested"*, and 10,265
of those had been filed as successes on a size check alone — full size, correct
structure, no error in the logs. A capture now counts only if the file contains
the DOI it is filed under and its title matches the record. Every archive was
re-checked individually and the verdict stored per document, so the rule can be
disagreed with rather than taken on trust.

**Every reported number carries the sample it rests on.** "8,682 documents are
reachable" and "8,682 documents are reachable, from one request per host" are
different claims, and only the second one can be challenged before it is
believed. That distinction caught several wrong figures in this project, one of
them a headline result that had already been reported.

## The interesting problem: publishers refuse crawlers

About a third of the queue cannot be fetched with a browser. Six evasion-shaped
approaches were tried early and five did nothing, because the refusals arrive as
HTTP 403 *before a page renders* — they are decided on the connection, not on
browsing behaviour.

What worked was not beating the block but not needing the blocked page. Seven
routes, each measured rather than assumed:

| route | recovered | note |
|---|---:|---|
| browser capture of the publisher's page | 70,180 | the main path, MHTML via CDP |
| PubMed Central, via NCBI's `efetch` | 5,413 | the sanctioned bulk endpoint, not the web page PMC refuses |
| Elsevier Article Retrieval API | 10,854 | after they granted TDM access and issued an institutional token; 10,854 of 10,858 requested, zero errors |
| plain HTTP where the block is a browser fingerprint | 1,281 | cureus.com was 518/518 blocked, and returned 10 of 10 to `httpx` |
| WebKit where Chromium is refused | 1,031 | MDPI: 8/8 against 0 of 1,036 |
| open-access mirrors and long tail | 720 | repositories, SciELO, small publishers that answer a plain client |
| university network | 2,120 | five hosts that answer a campus address and refuse a residential one |

Routes that were tried and **did not** pay, recorded because a measured dead end
is worth more than an untried idea:

- **Common Crawl** holds article URLs for three of the blocked publishers — 191
  of the first 200 for one of them — and none of *our* DOIs: 0 of 18, in three
  URL forms each. A per-host statistic and a per-document question have
  different answers.
- **Internet Archive / CDX**: 1 of 10 blocked hosts has usable snapshots. The
  bot protection that refuses us refuses their crawler too.
- **Semantic Scholar** reports an open-access PDF for 9% of the hardest cases,
  but 9 of 11 URLs resolve back to the publisher page we cannot fetch. Real
  yield 1.7%.
- **Publisher-registered TDM endpoints** (Crossref `link[]`): 72 fetched across
  20 hosts, 6 articles. A registered endpoint is a lead, not a route.

## What this project would not do

Forging a TLS fingerprint, solving challenges, rotating residential proxies, or
running systematic collection through the university VPN.

That is not only a policy position — it is also measured. Real Chrome with a
window on screen retrieves full articles from publishers that refuse both
headless Chrome and the bundled Chromium. But eight documents per host later,
the pattern is *one article per browsing session*, at any request spacing: it is
a session gate, not a rate gate. Using it at scale means resetting the session
for every document, which is defeating the control rather than obeying it. So
the honest sentence in the report is not "these were unreachable" but "these
were reachable by a method we declined to use", which is a stronger and more
checkable claim.

## Things that were silently wrong

Most of the engineering time went here rather than into features. A selection,
because the failure modes are the transferable part:

- **A re-run made the data worse than not running.** Four collectors rebuilt
  rows without columns that a later pass had filled — one would have NULLed
  62,867 flags. The fix that mattered was a *generic* test: every column a
  script computes must appear in the statement that writes it. It immediately
  found a second instance the specific test could not have.
- **A flag that answered a narrower question than its name.**
  `cited_after_retraction` was `citations_after > 0`, ignoring whether the
  citation window reaches the retraction at all. For 33,292 papers there is no
  "before" in the data, so every later citation looked like post-retraction
  attention. It is now blank where the window cannot answer, not 0.
- **A reader that knew two containers.** Archive decoding recognised MHTML and
  rendered HTML. JATS XML from PubMed Central opens with an XML declaration, and
  Elsevier's API response opens with a bare `<full-text-retrieval-response` —
  so 5,425 verified documents were filed as "no text/html part", twice. Fixed
  the third time with a rule instead of a list: an envelope begins with headers,
  a document begins with a tag.
- **A cleaning rule measured before it was trusted.** Truncating extracted text
  at the first publisher-furniture marker looks obviously right. Measured, that
  marker sits at a *median of 14%* into Springer and Nature articles — between
  the abstract and the body. It would have emptied 7,204 article bodies while
  reporting them clean.
- **An error message about our own parser.** Two Crossref failures were reported
  as `JSONDecodeError` on every host, which reads exactly like "this route does
  not work". Both were an `Accept: text/html` header we had set ourselves.

## Layout

```
src/rwpipe/
  fetch.py      Playwright renderer, CDP MHTML capture, bot-wall classifier,
                and the identity check that decides what counts
  extract.py    article body, abstract, retraction notice; HTML, JATS and
                Elsevier XML paths
  db.py         SQLite schema — resumable frontier, engagement, extracted text
  storage.py    content-addressed archive paths, identical on every machine
  limiter.py    per-host rate limiting and circuit breaking
  altmetric.py / plumx.py / engagement.py / apis.py
scripts/
  crawl.py             the Stage 1 crawler
  verify_archives.py   identity verification
  extract_text.py      Stage 2
  collect_*.py         one per access route
  probe_*.py           feasibility probes — run before committing to a route
  export_missing.py    every page we do not have, why, and what could reach it
tests/                 54 tests
docs/
  worklog.md              dated record of every decision and measurement
  blocking-strategy.md    the access problem, with the numbers behind each route
  findings-week4.md       evidence behind the architecture
```

## Running it

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m playwright install chromium
cp .env.example .env      # API keys — never committed
```

```bash
python scripts/build_queue.py        # CSV -> SQLite frontier
python scripts/crawl.py              # Stage 1, resumable
python scripts/verify_archives.py    # decides what counts as collected
python scripts/collect_engagement.py # citations, attention, Wikipedia
python scripts/extract_text.py       # Stage 2
python scripts/export_master.py      # the joined table
```

Every step is resumable and safe to re-run. That was not true until an audit
made it true, which is the subject of half the fixes above.

## Data availability

The collected corpus is **not** published here, for three separate reasons:

- the article text is publisher copyright, obtained under a text-and-data-mining
  grant for one-time academic use with no redistribution;
- the attention data was retrieved under a licensed API key belonging to the
  project's client;
- it is 31.3 GB.

The Retraction Watch source data is openly licensed (CC BY 4.0) and available
from Crossref. Everything in this repository is code and documentation.

## Tests

```bash
python -m pytest -q
```

54 tests. The ones worth reading are in `tests/test_persistence.py` — they check
that re-running a collector reproduces the dataset rather than damaging it,
which is the class of bug that cost this project the most.
