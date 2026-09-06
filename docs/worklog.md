# Work log

Running record of work done, kept for the individual report. COMP5703 scores
each component as `group mark x individual mark`, and the individual report has
to evidence "your individual contribution in technical details" - so every
entry names what was done, what it produced, and how to verify it.

Format: date, what, evidence (commit / command / measured result).

---

## 2026-08-25 (Week 4)

### Set up the project and toolchain
Nothing existed at the start of the day - empty directory, no Python installed.

- Installed Python 3.12.10, created `.venv`, installed Playwright 1.49.1 +
  Chromium 131, pandas, httpx, tenacity, rich, pytest.
- Created the repository `retraction-pipeline/`, initialised git.

**Evidence:** [`requirements.txt`](../requirements.txt), commit `6ca40c0`.

### Read and reconciled the project brief
- Located the project in the PG project list: **CS-43, Automated Extraction and
  Impact Tracking Pipeline for Retracted Scientific Literature**, client
  an academic client at the University of Sydney.
- Transcribed the Week 3 client meeting into concrete requirements, including
  constraints stated only verbally: one-time collection (not monitoring), all
  data local, whole CSV (not a sample), Week 10 completion, three mandatory
  stages, client meetings weeks 5/7/9/13.
- Noted an inconsistency to raise with the client: he said "four columns are
  relevant" but named only two (`OriginalPaperDOI`, `RetractionDOI`).

**Evidence:** [`README.md`](../README.md), [`findings-week4.md`](findings-week4.md).

### Stage 0 - built the crawl frontier
- Wrote DOI validation/normalisation: handles bare DOIs, `https://doi.org/...`
  URLs, `unavailable`, `0`, blanks.
- Loaded the CSV into a resumable SQLite frontier.

**Measured:** 71,944 raw rows -> 246 blank trailing rows dropped -> **71,698
records**; 65,752 valid original DOIs (91.4%), 67,029 valid retraction DOIs
(93.2%); 3,283 records with neither DOI, excluded; **132,781 pages** queued.

Cross-checked with an independent `awk` pass over the raw CSV - both
implementations agree exactly on 132,781.

**Evidence:** `python scripts/build_queue.py`, commit `6ca40c0`.

### Established that publisher routing must key on the DOI prefix
The free-text `Publisher` column has **1,713 distinct spellings**; the DOI
registrant prefix has 970 and is stable. Coverage is concentrated:
top 5 prefixes = 57.7% of pages, top 20 = 83.1%, top 50 = 93.0%.

**Consequence:** ~20 publisher adapters plus a generic fallback, rather than a
universal crawler for 970 prefixes.

### Feasibility probe - measured what publishers do to a crawler
Wrote `scripts/probe.py` and ran 36 live samples across 12 prefixes.

**Measured:** 20/36 succeeded (56%), 16 blocked (44%).

**Key finding - blocks arrive as HTTP 200.** Springer and Nature return a
complete-looking page, status 200, titled `Client Challenge`, body
*"Enter the characters seen in the image below"*. A crawler trusting HTTP
status would file tens of thousands of CAPTCHA pages as valid articles with no
error in the logs, silently poisoning the dataset.

Rewrote success detection to be content-based: bot-vendor hostnames
(`validate.perfdrive.com`, `idp.*`), challenge titles/markup, and a 50 KB floor
on rendered HTML. Unit-tested against 5 captured cases.

**Evidence:** `rwpipe.fetch.classify`, `data/reports/probe-*.json`, commit `6ca40c0`.

### Stage 1a - built the crawler
`scripts/crawl.py`: per-publisher rate limiting with cross-publisher
concurrency, retry accounting, gzipped MHTML archival via CDP
`Page.captureSnapshot`, resume after interruption, safe Ctrl+C.

**Verified the archives are genuine**, not shells: a matched pair for one
record - `RETRACTED: Downregulation of SUMO2...` and
`RETRACTION: Downregulation of SUMO2...` - each with 13-14 images inlined,
3.2 MB uncompressed / 751 KB on disk.

**Measured on 100 mixed pages:** 56% first-attempt success - independently
matching the probe's 56%.

**Evidence:** commit `44fa7ed`.

### Disproved the retry hypothesis
I had estimated that if blocks were independent events, three attempts would
lift coverage to ~70%, and flagged that the independence assumption needed
testing. Added `--retry-only` and tested it.

**Measured:** of 44 failed pages, retries recovered **9 (10%)**, not the
predicted 70%. Blocks are stateful, not independent - the site remembers the
client. **Total coverage 65%.**

Conclusion recorded: retries alone will not close the gap; stealth and slower
crawling matter more than previously assumed.

**Evidence:** commit `44fa7ed`.

### Fixed a data-model error found by a client-side question
Prompted by the question "why not clean the data before crawling", audited the
frontier for redundancy.

**Measured:** 132,781 page rows resolve to only **109,888 distinct DOIs** -
**22,893 redundant fetches, 17% of the work**. Two causes:
- **Mass retraction notices.** One notice (`10.1177/10641246251331509`) covers
  659 papers from *Journal of Intelligent & Fuzzy Systems*, all retracted
  2025-04-17 - a paper-mill batch. Fetching it 659 times is waste, and 659
  extra chances of being rate-limited.
- **13,655 records carry the same DOI in both columns** - one page, not two.

Restructured: `documents` table keyed on DOI is the unit of fetching; `pages`
becomes the record-to-document mapping. Completed fetches carried across, so
nothing was re-downloaded.

**Evidence:** commit `d37a92e`.

### Stage 0b - cleaned the dataset
Audited data quality first, then wrote `rwpipe/clean.py` + `scripts/clean_data.py`.

**Found and fixed:**
- Two dates were unparseable only because the file mixes `4/10/2026 0:00` with
  `6/24/1756 12:00:00 AM`. The 18th-century dates are genuine - *Treatise upon
  Electricity*, published 1753, retracted 1756. Flexible parsing now handles
  both; **0 unparsed dates remain**.
- Every Subject and Reason cell carries a trailing `;` - all 71,698 rows - so a
  naive split leaves an empty tail on every record.
- PubMed IDs use `0` and blank interchangeably for absent.
- Subject codes separated from labels: `(BLS) Biochemistry` -> `BLS` +
  `Biochemistry`.

**Deliberately not done:** 5,245 records share an `OriginalPaperDOI` and were
**not** merged. The dominant pattern is `Expression of concern` later followed
by `Retraction` on the same paper - the update lifecycle this project exists to
study. Merging would destroy the signal, so they are flagged instead.

**Validated:** 0 records where a notice predates the paper it retracts;
retraction years span 1756-2026; median time to retraction 499 days;
top reason "Investigation by Journal/Publisher" (31,704), with
"Paper Mill" at 11,797.

**Outputs:** `data/clean/records_clean.csv` (41 columns incl. 10 quality flags),
plus long-format `subjects.csv` (195,350 rows), `reasons.csv` (278,029),
`authors.csv` (304,185), `countries.csv` (90,565), and
[`data_quality.md`](../data/clean/data_quality.md).

**Evidence:** `python scripts/clean_data.py`.

---

## Open items

- [ ] Apply for Altmetric and PlumX API keys - Stage 2 blocks on them, and
      institutional approval takes time. Free fallbacks to name in the
      proposal: Crossref Event Data, OpenAlex, Semantic Scholar.
- [ ] Test `playwright-stealth` and a slower crawl rate; re-measure coverage
      against the 65% baseline.
- [ ] Pre-validate DOIs against the Crossref API before spending browser time
      on dead ones.
- [ ] Ask the client which four columns he meant.
- [ ] Decide IOP (2,531 pages) and MDPI: mitigate, or document as excluded.
- [ ] Confirm mid-semester break dates on Canvas.

---

## 2026-08-25 (Week 4) - Stage 1a full crawl launched

### Hardened the runner for a multi-day run
Two changes made before launching, both required by the run length rather than
by correctness:

- `--max-attempts`: caps attempts per page. A default run makes three passes
  (pending -> failed -> failed), but retries were measured to recover only 10%,
  so passes two and three would cost ~16 hours to gain ~4,000 pages. First pass
  runs with `--max-attempts 1`; the failure set is kept for a later, better-
  equipped pass.
- `--recycle-every`: rebuilds each browser context every N pages. One context
  held across 110,000 pages accumulates memory and cookie state.

Verified both on an 8-page run: failures went straight to `blocked` with no
retry pass, and recycling every 3 pages did not disturb the crawl.

### Launched the full first pass
    python -u scripts/crawl.py --workers 8 --max-attempts 1

**Frontier at launch:** 109,797 pending documents.
**Measured in the first 130 pages:** 64% success, ~5,200 pages/hour.
**Projection:** ~21 hours, ~70,000 archived pages, ~15-20 GB on disk.

Log: `data/reports/crawl-stage1a.log`. The run is resumable - an interruption
loses nothing, the same command continues where it stopped.

**Deliverable framing:** the output is two things, not one - the archive, and a
complete failure register with a specific cause per page (403 / CAPTCHA /
bot-management vendor). The register is what makes the coverage gap
quantifiable, and it is the baseline against which any anti-blocking
improvement will be measured.

### Restarted the crawl after mid-run code changes
Caught by the question "the background job is running the old code - but you
changed it since". Correct: Python loads modules at start, so edits on disk do
not reach a running process.

**Checked before assuming harm:** all 1,984 archives already captured resolved
correctly and none were missing. No corruption.

**But a real inconsistency was forming.** The running process wrote
`data\mhtml\...` (repo-relative, Windows backslashes) while the new code writes
`mhtml/...` (data-relative, forward slashes). Continuing would leave two path
formats in one database - readable here only because `storage.resolve()`
accepts both, but a trap for teammates, and backslashes break on macOS/Linux.

Normalised all 1,984 existing paths to the portable form, verified every
archive still resolves, and restarted on the current code. Nothing re-fetched.

### Hard-blocked publishers: tested, not assumed
Six publishers return zero successes: IOP, RSC, MDPI, ACS, JAMA, APA -
**5,875 pages, 5.3% of the frontier**.

Ran a controlled experiment (`scripts/experiment_stealth.py`), same DOIs across
browser variants: headless baseline, headless + stealth JS patches, headed.

**Result: stealth changed nothing - 0/2 on every publisher, identical to
baseline.** The reason is structural: the failures are `http 403` on the first
response, so the block lands at the network edge *before* any page loads.
Stealth patches `navigator.webdriver` and similar, which only exist after load.
Disguising something never observed cannot work. Defeating edge-level blocking
would mean TLS-fingerprint impersonation - circumventing access control, not a
call to make unilaterally on a university project.

**Alternative routes measured instead:** Crossref covers 100% of the 5,875
pages (all have valid DOIs); PubMed Central covers 2,135 (36%), and highest
exactly where blocking is worst - JAMA 97%, APA 90%, ACS 75%. IOP is the
exception at 2% (physics rarely indexed in PubMed).

### Fixed silent dtype corruption in our own cleaned output
`original_doi_prefix` was written bare to CSV and read back as float, dropping
trailing zeros: `10.3390` (MDPI) -> `10.339`, `10.1080` (T&F) -> `10.108`.
**82 prefixes covering 4,781 pages affected; three pairs collide outright**
(`10.1113`/`10.11130`, `10.1556`/`10.15560`, `10.1557`/`10.15570`).

This class of bug surfaces only in Stage 3, as a publisher count that is quietly
wrong. Added `rwpipe.clean.read_clean()` and a `dtypes.json` sidecar.

### Team tooling for splitting the crawl
- `--shard I/N` on the crawler: deterministic CRC32 split, verified balanced
  (13,470-13,753 per shard across 8).
- `scripts/merge_shard.py`: merges a teammate's database and archives.
  Tested on four cases - new page promoted and file copied, a teammate's
  diagnosis absorbed into our undiagnosed failure, an existing archive never
  overwritten by stale data, no file written outside the data root.
- `RWPIPE_DATA` env var to relocate the data directory.

**Recommendation recorded:** split by *publisher*, not by hash. A hash split
balances load but points all 8 machines at every publisher simultaneously -
individually polite, collectively 8x. Per-publisher assignment means each site
faces one crawler at a time. Also: crawl from home networks, never the
university network, where a block could hit the whole campus IP range.

---

## 2026-08-26 (Week 4) - Circuit breaker: the crawl was dying

### Symptom
24 hours in, only 16.2% complete. Throughput had collapsed from 5,200 pages/hour
at launch to **351/hour**, and success had fallen from 78% to 55%. Projected
completion: **10.9 days**.

### Diagnosis
The adaptive limiter slows down under refusal but **never gives up**. A host
that refuses everything keeps consuming one request every 90 s (the ceiling),
forever. Measured across hosts with zero successes:

    pubs.rsc.org            1,001 tries, 0 ok
    pubs.acs.org              761 tries, 0 ok
    www.e3s-conferences.org   623 tries, 0 ok
    jbc.org                   605 tries, 0 ok
    portlandpress.com         588 tries, 0 ok
    pubs.aip.org              529 tries, 0 ok
    validate.perfdrive.com    410 tries, 0 ok   (IOP)
    academic.oup.com          339 tries, 0 ok
    ... 18 hosts in total

**6,191 attempts spent confirming a result already certain after the first
fifty** - and tens of thousands more pages were queued behind the same walls.
At 90 s each that is several hundred hours of pure waste.

This is a design flaw in my own limiter, not a publisher problem: backing off
without ever stopping is an infinite loop with extra steps.

### Fix
Added `rwpipe.limiter.Breaker`. Once a host reaches `--breaker-after` attempts
(default 40) with **zero** successes, its remaining pages are archived as
`blocked` with the cause recorded, without issuing a request. The outcome for
those pages is identical - they were going to fail - but it costs a millisecond
instead of 90 seconds.

State is seeded from the database at startup, so the breaker carries knowledge
across restarts rather than relearning it.

**Measured on a 40-page test:** 37 pages archived by the breaker, throughput
**351 -> 13,870 pages/hour**.

### Note for the report
Final coverage is unchanged by this fix. What changes is that the crawl now
*terminates*. The blocked pages still carry a specific, per-page cause, which is
what makes the coverage gap quantifiable.

### Second defect: recovery was 350x slower than backoff
Found while re-estimating completion time after the breaker fix. The limiter
showed Springer parked at the 90s ceiling **despite a 94% success rate**, with
13,394 pages left - a 335-hour projection for one publisher.

Cause: backoff was multiplicative (x2) but recovery was **additive** (-0.25s).
From the ceiling that needs 356 successes to return to 1s, and each of those
successes costs a 90s wait, so recovery alone takes ~9 hours. One block
effectively crippled a host permanently.

Changed recovery to multiplicative (x0.85). Verified: 90s -> 2s in **24
successes instead of 352**. Trajectory 90 -> 76 -> 65 -> 55 -> 47 -> 40 -> ...

Lesson worth keeping for the report: in an AIMD scheme the two directions have
to be *comparable in magnitude*. Pairing a doubling increase with a fixed small
decrement is not conservative tuning - it is a one-way ratchet.

### Silent corruption: verifying the wrong observation
Health check found 362 documents marked `done` with anomalous files. Two
distinct causes.

**65 rows** had a NULL `gz_bytes` - captured before that column existed. Files
were intact on disk; backfilled from disk.

**297 rows** were 1.6 KB archives from `linkinghub.elsevier.com` whose entire
content was `Subject: Redirecting`. Elsevier's DOI waypoint, stored as if it
were an article.

**Root cause.** `page.content()` and the CDP snapshot are two separate
observations of the page, taken moments apart. The 50 KB floor was applied to
the first; the archive kept is the second. Elsevier's interstitial renders at
almost exactly 50 KB - right at the threshold - so it passed, while the
snapshot taken afterwards still held the waypoint. **The size check ran against
the observation we were not keeping.**

**Fix.** Wait for the URL to leave a known waypoint before snapshotting, and
apply a 40 KB floor to the captured MHTML itself. The 297 pages were requeued.

**Follow-on finding.** After the fix those pages fail honestly rather than
succeeding falsely - the redirect no longer completes at all, because
ScienceDirect is now rate-limiting us (the limiter has it pinned at the 90 s
ceiling, having been 4,505/4,505 earlier). So the interstitial was never an
article we were losing; it was an artefact of a redirect that had already
stopped working. The fix costs no real coverage, it only makes the count honest.

**Pattern worth naming for the report:** every silent-data bug in this project
has had the same shape - a check that passes while the thing being checked is
not the thing being kept. Status code vs page content. Uncompressed size vs
on-disk size. Rate limit vs missing record. HTML observation vs MHTML artefact.

### Fixed a navigation race that discarded good pages
55 Elsevier fetches failed with `Page.title: Execution context was destroyed`.
Not a publisher problem - a redirect landing between `goto` and the title/HTML
reads destroys the execution context mid-call, so the page was thrown away
even though nothing was wrong with it. The navigation that interrupted us is
usually the one arriving at the article.

Reads now retry after waiting for the new document, and the final URL is
recorded *after* settling, so a page that redirects during the read is filed
under where it ended up rather than where it started.

### Noted: two crawler instances on one database interfere
A test run started while the background crawler was live printed
"recovered 7 interrupted pages" - its startup reset of stale `running` rows
also reset the other instance's genuinely in-flight ones. Harmless here (those
pages are simply re-fetched) but worth knowing before the team runs shards in
parallel: **one crawler process per database file**. Teammates should each work
against their own copy and merge afterwards, which is what merge_shard.py is
for.

### Testing the client's "smarter ways to avoid captchas"
Client's reply to the access question: *"Find smarter ways to avoid captchas,
there is no alternative source. Do your best."* Note *avoid*, not bypass -
nothing tested here solves a challenge or forges a fingerprint.

**Tested: arrival pattern.** Hypothesis was that some walls fire on the *shape*
of the request - a deep article URL with no prior session, no cookies, no
referrer - rather than on the client itself. Condition B landed on the
publisher's home page first, accepted the cookie banner, kept the session,
paused, then followed to the article and scrolled before snapshotting.

**Result: no help, and on cell.com actively harmful** - warming tripped a
Cloudflare challenge that a direct request did not.

### My own sampling error, recorded
The control arm showed three hosts previously recorded as 0-for-1200-plus
answering **2 of 6** requests. I read that as a 33% recovery rate and requeued
3,366 pages.

**That was wrong.** The 95% interval on 2/6 runs from about 4% to 78% - a hint,
not a measurement. After the requeue those hosts have now been genuinely
attempted 152 times with **zero** successes. They are dead.

I had criticised a teammate's 3-sample conclusion earlier the same week and
then drew one from 6. Rule for the rest of this project: **no success-rate
claim from fewer than several dozen attempts**, mine included.

The round was not wasted - it exposed two real breaker defects (a verdict that
never reconsidered, and a pardon that did not clear the record it pardoned),
both fixed, and it answers the client's question with evidence rather than
assertion.

---

## 2026-08-28 (Week 4)

### Found and fixed a circuit-breaker defect that wrote off a live publisher

The breaker keys on `final_host`. A DOI that never resolves past the resolver
records `doi.org` as its host — and that host can only ever accumulate
failures, because a successful fetch has by definition redirected away from it.
So `doi.org` reached 1,264 attempts with zero successes, was condemned, and
from then on every DOI whose resolution stalled there was archived as `blocked`
**without a request being made**.

Damage measured before the fix:

| | |
|---|---|
| Pages written off by the `doi.org` verdict | **1,135** |
| Distinct DOI prefixes affected | 53 |
| Prefixes whose learned host had become `doi.org` | 53 |
| Largest victims | 10.26355 (575), 10.4314 (408) |

**Fix:** `RESOLVER_HOSTS` is exempt from both the breaker and the prefix→host
map (`src/rwpipe/limiter.py`). A failure at the resolver is a fact about one
DOI, not about the publisher behind it.

**The fix recovered a real publisher.** After requeueing, `www.europeanreview.org`
returned **73/73 = 100%**. Those 575 pages were never blocked by anyone; our own
breaker had written them off.

**Not everything recovered.** `10.4314` (AJOL, 410 pages) still fails at the
resolver — 10 attempts, 0 successes. Those DOIs genuinely do not resolve. That
is a third failure class, distinct from bot-walls, and it belongs in its own
bucket in the report alongside FUNPEC (10.4238) and TJPRC (10.24247), whose
publishers return real 404s.

**Evidence:** `src/rwpipe/limiter.py` (RESOLVER_HOSTS), the requeue of 1,135
rows, and `data/reports/crawl-today.log`.

### Quantified the unreachable set, using the project's own sampling rule

Applied the rule set after the earlier 2-of-6 error: no success-rate claim from
fewer than several dozen attempts. Filtering on ≥30 attempts and <2% success:

**51 DOI prefixes are confirmed unreachable — 18,059 records, 16.4% of the
frontier.** Largest: T&F (2,990), IOP (2,531), SAGE (1,914), RSC (1,015),
EDP (837), ACS (766). Exported to `data/clean/blocked_publishers.csv` with the
host and block mechanism for each.

A second tier — **412 prefixes, 2,982 records** — has zero successes but fewer
than 30 attempts, so under the rule nothing may be concluded about it. The
largest single unknown is **MDPI (10.3390), 1,035 records tried only 23 times**.

Two corrections to earlier readings, both from checking the host rather than
the prefix:

- **IOS Press (10.3233, 1,791 pages) is not "untested" — it is served from
  `journals.sagepub.com`**, already confirmed dead. It looked promising only
  because it had never been attempted.
- 10.3109 (211 pages) likewise resolves to `tandfonline.com`.

Neither was queued, saving the run from ~2,000 certain failures.

### Exported the tables that existed only inside SQLite

`scripts/export_tables.py` regenerates three CSVs from the database on every
run, so a status report can never drift from the data:
`crawl_by_publisher.csv` (970), `crawl_by_doi.csv` (109,888),
`engagement.csv` (62,489), plus `blocked_publishers.csv` (51).

### State at end of day

| status | count |
|---|---|
| done | 68,895 |
| blocked | 25,111 |
| pending | 15,399 |
| failed | 483 |

Coverage 62.7% of 109,888 distinct DOIs; 30 GB of MHTML; 62,489 DOIs with
pre/post-retraction citation splits.

**Open question for the client (Week 5):** 16.4% of the corpus is provably
unreachable to automated collection, and that figure is itself a finding —
which publishers close their doors to independent verification of retracted
work, and by what mechanism. Worth asking whether he wants that written up as a
result rather than treated as a shortfall.

### WebKit reaches publishers that refuse Chromium
Client asked for smarter ways to avoid captchas. Browser *engine* turned out to
be the lever that arrival-pattern tricks were not.

Playwright drives real Firefox and WebKit builds alongside Chromium. Running
WebKit is not forging a fingerprint - it *is* WebKit, with its own TLS
handshake and header order. Bot-management products are tuned hardest against
headless Chromium because that is what nearly every crawler uses.

**Swept all 18 chromium-blocked hosts, 8 DOIs each:**

    www.mdpi.com          8/8   median 534 KB   1,036 pages
    cell.com              4/8   median  51 KB   1,205 pages
    doi.org               6/8                     148 pages
    11 other hosts        1/8 each              10,788 pages
    IOP / EDP / Cureus    0/8                   ~3,600 pages

Restricted the fallback to MDPI and cell.com. At 1/8 the others cost eight
requests per recovered page, each failure deepening the block - a bad trade for
~1,300 pages.

**Constraint discovered: WebKit cannot produce MHTML.** Capture goes through
the Chrome DevTools Protocol, which is Chromium-only, so these pages are stored
as gzipped rendered HTML instead. Verified on a real capture: 74,153 characters
of text with abstract, introduction, references and the RETRACTED marker all
present. What is lost is the inlined images - relevant because image
duplication is a common retraction reason, so those pages can be revisited if
the analysis needs figures.

Added `documents.capture_format` so no downstream stage can mistake an HTML
capture for an MHTML one, and existing rows were backfilled as `mhtml`.

**To raise with the client:** he said the goal is MHTML. For these ~2,200 pages
the choice was HTML or nothing, and HTML carries the body text the analysis
needs. Worth confirming he accepts the mixed format.

---

## 2026-08-29 — Two crawler processes were competing, and one was destroying recoverable pages

Checked the running jobs and found **two crawler processes alive at once**:

    PID 91736  started 8/28 16:45  --prefix 10.1016 10.26355 10.4314 --workers 6
    PID 205444 started 8/29 14:22  --workers 4 --max-attempts 1 --floor 1.0 --delay 2

Two separate defects, one of which was silently losing data.

### The rate limiter is per-process, so its guarantee did not hold

`Limiter` serialises requests per host inside one process (`async with
lim.lock(key)`). Two processes each computed their own delay for
`www.sciencedirect.com` and neither could see the other, so the host received
roughly double the rate that either process believed was safe. This is the same
class of bug as the earlier one where the limiter was keyed on DOI prefix
instead of resolved host.

**Not acted on.** Measurement contradicts the theory: ScienceDirect returned
2,408 pages today and 7,274 in total, at 100% success and a 3.1 s median. The
doubled rate is evidently within what Elsevier tolerates, and 3,453 of the
3,713 remaining pages are Elsevier. Halving throughput to remove a risk that is
not materialising would cost ~6 hours against no measured benefit. Recorded
here because the guarantee is real even where the harm is not.

### `--max-attempts 1` was parking recoverable pages as permanent failures

With `max_attempts=1`, any failure satisfies `attempts >= frontier.max_attempts`
on the first try, so the page is written off with no second chance. Failure
breakdown at the time (1,124 rows):

| count | error | verdict |
|---|---|---|
| 547 | `challenge title: Just a moment...` | refusal, not retryable (mostly cell.com) |
| 93 | `Execution context was destroyed` | our own navigation race, since fixed |
| 69 | `TimeoutError: Page.goto` | transient |
| 42 | `capture too small (1 KB)` | transient |
| 38 | `body too small - interstitial` | Elsevier waypoint, transient |
| 27 | CDP `Failed to generate MHTML` | transient |
| 17 | `http 403` | refusal, not retryable |

1,098 of 1,124 had been attempted exactly once.

The interstitial failures are ongoing rather than historical — 41 on 08-29,
the same day ScienceDirect succeeded 2,408 times — so the same DOI stuck at
`linkinghub.elsevier.com` will usually resolve on a second attempt. Under
`--max-attempts 1` every one of those was being discarded.

**Fix:** stopped PID 205444 and restarted with the same pacing but default
retries:

```
python scripts/crawl.py --workers 4 --floor 1.0 --delay 2 --breaker-after 40
```

No manual requeue was needed. `Frontier.claim` already selects
`status IN ('pending','failed') AND attempts < max_attempts`, so restoring the
default of 3 makes the 272 retryable rows eligible again on their own. The 547
Cloudflare challenges are on breaker-dead hosts and are archived without a
request, which costs nothing.

Restart was clean: `recovered 4 interrupted pages`, 887 prefix→host routes,
circuit breaker open on 42 hosts with 15,379 failed attempts already spent.

### Method note

The candidate retry set was checked host by host and dated before acting,
rather than inferred from the error text. On 08-27 a 2-of-6 sample was read as
a 33% success rate and 3,366 pages were requeued on it; those hosts then failed
152 consecutive attempts. The rule from that mistake — no success-rate claim
from fewer than several dozen attempts — is what turned up the fact that
`linkinghub.elsevier.com` sits at 0.4% lifetime (2/520) while
`www.sciencedirect.com` sits at 100%, which is why the waypoint failures are
worth retrying and the challenge pages are not.

### State at this point

| status | count |
|---|---|
| done | 75,604 |
| blocked | 29,443 |
| pending | 3,713 (3,453 Elsevier, 98 other) |
| failed | 1,124 |

Stage 1b: OpenAlex complete at 62,867 (99.4%). Altmetric 7,826 collected,
5,634 with attention (72%), 1,122 with news coverage; ~21 h remaining at the
observed 2,600/h. Wikipedia pass covered 174 DOIs and must be re-run once
Altmetric finishes, because it is gated on `altmetric.counts_json`.

---

## 2026-08-30 — PlumX: the half of Stage 1b that was never started

Re-read the Week 3 transcript against what has actually been collected. The
client named **two** sources, not one:

    The client, in the week 3 meeting: "Social media news media: Altmetrics,
    PlumX metrics API"

Altmetric is running. **PlumX had never been attempted.** OpenAlex and
Wikipedia, which are done, are additions of mine — useful, but neither is one
of the two he asked for.

**Correction to my own reporting.** Stage 1b was written up as COMPLETE in
`docs/status-check-1-draft.md` and in the Week 4 status form on the strength of
the OpenAlex citation coverage (62,867 DOIs, 99.4%). That figure is right but
the label is not: measured against the client's own definition, Stage 1b is one
source running, one source not started, and two extra sources delivered.

### PlumX is not a duplicate of Altmetric

Altmetric counts *attention* — news articles, tweets, blog posts. PlumX
additionally reports **usage**: abstract views, full-text downloads, library
holdings. For this project the distinction is substantive. A retracted paper
still being *downloaded* is a more direct form of continued life than one still
being tweeted about, and it is the closer analogue to "was this bad science
actually used". Neither source substitutes for the other, which is presumably
why he named both.

### Access, measured rather than assumed

    GET https://api.elsevier.com/analytics/plumx/doi/10.1016/j.jhep.2014.10.009
    401 {"statusCode":"AUTHENTICATION_ERROR","statusText":"No APIKey provided"}

Two undocumented `plu.mx` widget paths were also tried on the chance the public
badge endpoint was usable; both 404. So the official route is the only route:
an Elsevier API key from https://dev.elsevier.com/, self-service, sent as
`X-ELS-APIKey`.

Entitlement is the open question. A key can authenticate for one Elsevier API
and be refused (403) on this one, because PlumX Metrics is licensed per
institution. That is why `plumx.probe()` exists and why the collector refuses
to start without it — discovering a 403 on request 40,000 instead of request 1
is the difference between a minute and a wasted evening.

### Written, ready to run the moment a key exists

- `src/rwpipe/plumx.py` — client. 404 is stored as "no measurable footprint",
  a result about the paper, not an error; 401/403 raise `NotEntitled` and
  429 raises `QuotaExhausted`, both of which stop the run rather than grind
  through an institution's allowance. Same discipline as the Altmetric
  collector, for the same reason: the credential is not ours.
- `scripts/collect_plumx.py` — resumable, most-cited DOIs first, `--probe`.
- `plumx` table: the five category totals as columns, plus every `count_type`
  verbatim in `detail_json`, so no downstream question is foreclosed by what I
  chose to extract today.

**Blocked on the user:** the key must be created under their own Elsevier /
Scopus login. Not something I can or should do on their behalf.

### Elsevier key obtained — and PlumX is licensed separately

Key created at dev.elsevier.com under label `COMP5703-CS43-retraction-impact`,
stored in `.env` (gitignored, untracked — verified with
`git ls-files --error-unmatch`).

Probed before running anything, which was the right call. Matrix of one DOI
against four endpoints, from a home connection:

| endpoint | result |
|---|---|
| Scopus abstract retrieval | **200**, real metadata returned |
| PlumX metrics (`X-ELS-APIKey` header) | 401 `not authorized to access this resource` |
| PlumX metrics (`apiKey` query param) | 401, identical |
| ScienceDirect full-text article | 403 `Requestor configuration settings insufficient` |

**The key is valid.** Scopus answering 200 proves it. So the 401 from PlumX is
an entitlement result, not an authentication one: PlumX Metrics is licensed as
a separate subscription, and this account does not hold it. Corrected
`plumx.probe()`, which had reported 401 as "wrong or inactive key" — that
wording would have sent the next person off to regenerate a key that already
works.

The 403 on ScienceDirect full text is the same shape of answer for the TDM
route, and may be IP-conditional; worth one retest from the university network
before drawing a conclusion, since Elsevier resolves some entitlements by IP.
Using the publisher's own API from campus is the sanctioned channel and is not
the same activity as crawling, which stays off the university network.

**Next move is the client, not more engineering.** He supplied an Altmetric key
unprompted when the free endpoint closed; PlumX is the other source he named,
so he is the right person to ask whether he has access or can arrange it. If
the answer is no, that is a finding to report rather than a gap to hide: two of
the sources he specified, one is reachable and one is behind a subscription
neither the group nor the university appears to hold.

### Correction: the PlumX 401 is an IP problem, not a licensing one

The conclusion recorded above — "PlumX is licensed as a separate subscription
and this account does not hold it" — is **wrong**, and was reached by inferring
a cause from a status code instead of checking the documentation. Checked it:

> "This API is available to all API users with an active Scopus subscription."
> — Elsevier, PlumX Metrics API documentation

The University of Sydney holds a Scopus subscription; it is in the library's
database list and available to students. So the entitlement almost certainly
exists. What is missing is proof that the request comes from inside it:
Elsevier resolves API entitlement from the **requesting IP**, matched against
the subscribing institution's registered range. Every probe so far was sent
from a home connection, which is outside that range.

That also explains the shape of the evidence, which I had misread. The Scopus
*abstract* endpoint answered 200 from the same connection — I took that as
proof the key was fully entitled and therefore that PlumX must be unlicensed.
Abstract retrieval is simply available at a lower entitlement level than PlumX,
so the two results are consistent with a key that is valid and a network that
is unrecognised.

**Two ways forward, both cheap:**

1. Re-run `--probe` on the university network or VPN. Costs 30 seconds. This is
   a call to Elsevier's own documented API, not crawling, so the rule about
   keeping the crawler off the university network does not apply to it.
2. If the collection has to run from home — 62,867 DOIs is a long job to hold a
   campus connection for — ask the library for an **institutional token**.
   `X-ELS-Insttoken` is Elsevier's own mechanism for carrying entitlement
   off-campus, and is issued to the institution on request.

Added `load_insttoken()` and wired `X-ELS-Insttoken` into `headers()`, so
option 2 needs only a line in `.env`. Rewrote the 401 message to state the IP
diagnosis and both remedies, replacing wording that would have sent the next
person off to regenerate a key that already works.

**Lesson, and it is the same one as the OpenAlex 429 mistake:** a status code
names the refusal, not the reason. That 429 was recorded as "not in OpenAlex"
and wrote off 23,917 papers when only 365 were genuinely absent. Here a 401 was
recorded as "not licensed" and nearly produced an email telling the client an
entire data source was unobtainable — when it may be one VPN connection away.
Read the vendor's own documentation before reporting a cause upward.

### VPN ruled out, with the reason

Tested the university VPN rather than assuming it would carry entitlement.
Cisco Secure Client against `vpn.sydney.edu.au` offers exactly one connection
group, `cloud_idp`, with no full-tunnel alternative. Connected, the exit IP
measured as:

    <home IP redacted>   AS135126 <consumer ISP>   Sydney, AU

Occom is a consumer ISP, not the University. The tunnel is split, so requests
to `api.elsevier.com` leave via the home connection and the earlier 401 was
never actually retested from a university address.

Two useful consequences:

**The crawler was never exposed.** The VPN was connected for a period while the
crawler was still running, which is the thing the "never crawl from the
university network" rule exists to prevent. Split tunnelling means the crawler's
traffic went out over the home ISP throughout, so no publisher ever saw a
university address. Stopped and restarted the crawler anyway once this was
noticed, before confirming the exit IP.

**The VPN is not the wrong setting, it is the wrong tool.** The University
provides off-campus access to licensed resources through OpenAthens and
EZproxy, not VPN — which is why the VPN profile only tunnels internal traffic.
None of the three can carry an API key:

| mechanism | how it identifies you | usable by a script |
|---|---|---|
| VPN (`cloud_idp`) | IP, internal traffic only | no — API calls exit via home ISP |
| EZproxy | rewrites URLs to appear on-campus | no — browser web pages only |
| OpenAthens | identity, not IP; browser login | no |

All three are built for a person reading a page in a browser. That is why
`X-ELS-Insttoken` exists, and why it is the correct route here rather than a
workaround: Elsevier's own mechanism for a program calling the API from off
campus.

One measurement complicates the IP theory and should be stated: **Scopus Search
returns 200 from the home connection**, and that endpoint does require a real
subscription. So the key already carries Scopus entitlement regardless of
network. If the documentation's "available to all API users with an active
Scopus subscription" were the whole rule, PlumX would already work. It does
not, which suggests PlumX needs an entitlement flag of its own. The library
request is therefore worded to ask both questions — whether the subscription
covers the PlumX *API* as distinct from PlumX badges inside the Scopus
interface, and whether an institutional token can be issued.

Remaining routes: campus wifi for a genuine IP test, and the library request.
Neither is engineering work.

### How other people solve this, and a quota problem found before it bit us

Searched for prior art rather than continuing to reason from status codes.
`pybliometrics`, the main Python client for these APIs, documents this exact
situation: off-campus access is VPN, **InstToken**, or being on campus, and the
InstToken is described as issued by Elsevier specifically for remote access in
the absence of a VPN, **coupled directly to one API key**.

The request route is documented and is not the library:

    datasupportRD@elsevier.com

and it must be sent **from an institutional email address** — that is how they
verify affiliation. The draft written earlier used the personal Gmail address
on file, which would have been rejected; corrected to the university address.

**Quota, which nobody had checked.** Elsevier applies weekly quotas per API,
5,000–50,000 requests for the Scopus family. PlumX is not listed in the public
table. If it sits at the low end:

    62,867 DOIs / 5,000 per week ~= 13 weeks

The project ends in Week 10. So entitlement alone would not have been enough —
a token could have arrived, collection started, and the corpus still been
unfinishable, discovered several weeks in. Elsevier's documented remedy is to
request a quota increase in the same message, naming the API and the volume, so
both asks now go in one email.

Also noted: dev.elsevier.com keys expire after 6 months.

Two messages prepared — Elsevier for the token and quota, the library in
parallel in case Elsevier asks the institution to confirm entitlement.

### Re-tested the key across eight endpoints — and had to withdraw an inference

Ran the whole Elsevier surface rather than PlumX alone. From home, 2 of 8:

    200  Scopus Search
    200  Scopus Abstract
    401  PlumX Metrics          not authorized to access this resource
    401  Author Search          not authorized to access the requested view or fields
    401  ScienceDirect Search   not authorized to access the requested view or fields
    403  ScienceDirect full text  requestor configuration settings insufficient
    403  Scopus Citations         requestor configuration settings insufficient
    403  SciVal                   not entitled to the resource specified

**This withdraws the argument made earlier today.** I had reasoned: Scopus
Search returns 200, that endpoint requires a subscription, therefore the key
carries institutional entitlement, therefore the PlumX 401 cannot be about IP,
therefore testing from campus is probably a waste of time.

Author Search is the same API family and returns 401. So the Scopus Search 200
is much more likely an open tier available to any registered key than proof of
a subscription. The key looks like a bare registration with no institutional
entitlement at all — and institutional entitlement is precisely what is
resolved by IP.

So the conclusion inverts: **the campus test matters more, not less.** It is
now the single measurement that separates "the University does not license
these" from "the University licenses them and we have never once asked from an
address it recognises". Six endpoints could flip, including ScienceDirect full
text — which is the sanctioned route to the ~30,000 pages the crawler cannot
reach.

Same error as the 401-means-unlicensed call earlier and the OpenAlex 429 before
it: one response was read as evidence for a general claim it did not support.
The fix is the same one each time — measure the whole surface, not the one
endpoint that happens to be in front of you.

Wrote `scripts/probe_elsevier.py` so the comparison is a single command run in
two places rather than an ad-hoc script retyped from memory. It reports the
exit IP, says plainly whether the address is a university one, and picks up
`ELSEVIER_INSTTOKEN` automatically if the token request succeeds.

### UniConnect Cloud ruled out — it egresses from AWS, not the University

Tested the university's virtual desktop (UniConnect Cloud, Citrix, at
`uniconnect.cloud.com`) on the theory that an app running inside the
university's environment would present a university address. Checked the exit
IP from inside the session before touching the API:

    "ip": "54.79.53.98"
    "hostname": "ec2-54-79-53-98.ap-southeast-2.compute.amazonaws.com"
    "org": "AS16509 Amazon.com, Inc."

It is hosted on AWS in ap-southeast-2. Elsevier sees a commercial cloud
address, which is if anything worse than a residential one — cloud ranges are
the first thing an abuse filter looks at. Ruled out.

Cost of ruling it out: two minutes, and the API key was never exposed. Checking
the IP before the credential was deliberate — a virtual desktop is a shared
managed environment, and a key passed as a URL parameter would have persisted
in its browser history and proxy logs for no gain.

**Ruled out so far, with the measurement each time:**

| route | why not |
|---|---|
| VPN `cloud_idp` | split tunnel; exit IP stayed Occom (home ISP) |
| UniConnect Cloud | exit IP is AWS ap-southeast-2 |
| EZproxy / OpenAthens | authenticate browser sessions, not API calls |
| Individual subscription | PlumX is not sold to individuals |
| Archived pages | PlumX badge present on 0 of 20 largest publishers |
| Crossref Event Data | duplicates Altmetric; carries no usage metrics |
| University HPC | Artemis retired Aug 2025; successors are project-application only, too slow for a Week 10 deadline |
| Local route manipulation | server-side policy decides the split; also circumventing IT configuration |

Outstanding: campus wifi (the only remaining self-service test), and three
requests awaiting reply — Elsevier for the InstToken and quota, the library for
entitlement confirmation, ICT for whether a full-tunnel profile exists.

### The breaker could not see what a retry already knew

Checked crawler health rather than assuming the tail was fine. ScienceDirect
was healthy — 477 pages in 90 minutes at a 3.07 s median, no refusals in five
hours — but alongside it:

    36  failed  2.96s  cell.com
    19  failed 10.69s  linkinghub.elsevier.com

cell.com stands at **0 successes in 1,552 attempts**. The circuit breaker has
it marked dead, so those requests should never have been made.

**Why the guard missed them.** The pre-check is keyed on the host *guessed*
from the DOI prefix. For `10.1016` that guess is `www.sciencedirect.com`,
because that is where most Elsevier DOIs land. A page that resolved to
cell.com on its first attempt therefore passes the guard, spends a full request
and ~3 s, and fails again — proving something already measured 1,552 times.

The row itself carried the answer the whole time: `documents.final_host` holds
where the previous attempt actually landed. The guard simply never read it.

**Fix.** `Frontier.claim` now selects `final_host`, and a retry whose
last-known host is breaker-dead is archived without a request, with that reason
recorded. Restricted to rows that carry a result — requeued pages have their
host cleared, so this cannot repeat the earlier bug where jbc.org's 837
requeued pages were archived without a single request being made.

**Measured effect before restarting**, by running the breaker's own verdicts
over the queue:

    459 rows  cell.com              992 requests saved in total
     20 rows  jbc.org
     11 rows  www.auajournals.org
      6 rows  four other dead hosts
    ---
    496 rows skipped, ~50 minutes of crawler time

446 rows still retry normally, including the 164 on
`linkinghub.elsevier.com`. That host reads as 0.5% lifetime, which looks
skippable, but retries there genuinely convert: 147 pages were recovered on
attempts 2 and 3 in a single six-hour window, and the Elsevier waypoint is the
reason. Lifetime rate was the wrong statistic for that host; what matters is
that a second attempt resolves somewhere else.

The saving lands at the tail rather than immediately — the queue is ordered by
`attempts`, so the 1,875 never-tried pages go first and the retries follow.

Both crawler processes restarted on the patched code; each recovered its
in-flight pages and reloaded 43 dead-host verdicts covering 15,528 spent
attempts.

---

## 2026-08-30 (morning) — Altmetric complete; Stage 1a queue empty

### Altmetric: 62,867 / 62,867, seven errors

Ran to completion on the client's key overnight, finishing 08:21. Seven errors
in 62,867 requests, and the key was never shared, logged, or committed.

    any recorded attention   22,362   (36%)
    covered by news           2,513   (4%)
    shared on social media   14,886
    no record at all         40,505   (64%)

**Two thirds of retracted papers never attracted public attention at all.** The
denominator matters here: had 404s been discarded as errors rather than stored
as "no footprint", the news figure would have read 11% instead of 4%, and every
downstream claim about media reach would have been inflated by the same factor.

Attention is extremely concentrated. The top ten by news coverage:

    719 news outlets   Lancet 10.1016/s0140-6736(20)31180-6   (Surgisphere hydroxychloroquine)
    570               Lancet 10.1016/s0140-6736(20)32656-8
    395               Lancet 10.1016/s0140-6736(97)11096-0    (Wakefield MMR)
    353               Int J Antimicrob Agents 10.1016/j.ijantimicag.2020.105949
    296               Nature 10.1038/nature23305

Four of the top five are COVID-era or Wakefield. The pattern to carry into
Stage 3: a handful of retractions reach the public, and the rest reach nobody —
so a mean across the corpus would describe a paper that does not exist.

3,174 papers carry Wikipedia citations per Altmetric, against the 174 checked
in the first pass. The Wikipedia collector is now running over the remaining
3,000; that pass was always gated on this table being complete.

### The retry guard fired as designed

Crawl queue reached zero pending overnight and moved into its retry tail, which
is where yesterday's patch takes effect. Measured:

    621 rows archived without a request
        551  cell.com
         24  jbc.org
         18  heartlungcirc.org
         14  www.auajournals.org
         13  three other dead hosts

Predicted 496 rows and ~992 requests; actual is 621, higher because more rows
acquired a `final_host` overnight and so became eligible. Roughly an hour of
crawler time not spent re-proving that cell.com refuses us.

343 rows still retry normally, 184 of them on `linkinghub.elsevier.com` —
deliberately, for the reason recorded yesterday.

State: done 79,047, blocked 30,193, failed 645, pending 0.

### Wikipedia is searched in eleven languages now — and the first sweep was wrong

**The gap that started this.** Altmetric flags 3,174 papers in the corpus as
Wikipedia-cited. Our own search found 430. The collector searches
`en.wikipedia.org` and nothing else, while Altmetric tracks every language
edition.

Sampled 60 of the 2,744 apparent misses across ten editions before writing any
code. 19 of 60 (32%) were cited somewhere other than English:

    zh 11 · pt 10 · ja 9 · es 8 · it 6 · ru 5 · fr 4 · nl 4 · de 3 · pl 3

So English-only understates Wikipedia reach by roughly a factor of three, and
the remaining ~68% are missed for reasons a broader language sweep cannot fix:
articles citing by PMID or title rather than DOI, and citations Altmetric
recorded once that have since been edited away. Both stated as limitations.

New `wikipedia_cites` table, one row per (paper, edition, article).
`engagement.wikipedia_count` deliberately left alone — English-only, comparable
across the whole corpus. Redefining a column mid-project is how one number ends
up meaning two things.

### Then the sweep produced a result that could not be true

First run reported **56** papers cited on Wikipedia. The English-only pass had
found **430**. Adding ten languages cannot reduce the count.

Wakefield's paper, checked by hand, returns 18 English articles, 3 Chinese, 5
Japanese — and the stored sweep had it right, 45 across editions. The first
~56 papers were correct and everything after was zero.

**Cause.** The sweep issued eleven parallel requests per DOI at 0.4 s
intervals — about 27 requests a second. Wikipedia throttled it. And `search()`
returned `[]` for both "searched, found nothing" and "the request was
refused":

    if r.status_code != 200:
        return []
    except Exception:
        return []

So 3,118 papers were written to the database as having no Wikipedia presence
without one successful search behind the claim.

**This is the third time the same mistake has appeared in this project**, after
the OpenAlex 429 recorded as "not in the index" (23,917 papers written off) and
the PlumX 401 read as "not licensed". A refusal is not an answer. The three
share one fix: never let a failure and an empty result share a return value.

**Corrected.** `search()` now returns `None` on failure and `[]` only on a
genuine empty result, retries 429/503 with backoff, and treats an API-level
`error` object as a refusal rather than as no data. A paper is written to
`wikipedia_swept` only when every edition answered — otherwise it is left
unswept and picked up next run, and the count of those is printed rather than
hidden. Editions are queried three at a time instead of eleven, and the
interval is 1.0 s.

Deleted the 3,118 false zeros and re-swept; the 56 rows that carried real
results were kept. ~52 minutes.

### Corrected sweep: Wikipedia reach is more than double what we would have reported

Re-ran with the fixed collector. Clean this time — no papers left unswept,
meaning every one of the 3,118 got a complete answer from all eleven editions.

    swept 3,118 (+56 already good)
      cited somewhere on Wikipedia :   849
      articles citing them         : 1,738

      en 507 · ja 146 · fr 126 · de 120 · es 110 · zh 102
      ru 102 · it  87 · pt  73 · pl  39 · nl  18

Final, across the whole table: **905 papers, 2,154 citing articles, 549 of them
in English.**

**The English figure was wrong too.** The sweep found 549 English citations
where `engagement.wikipedia_count` held 430. Checked directly: **121 papers
were recorded as having no Wikipedia presence when a working search finds
them** — a fifth of the English total. `engagement.wikipedia_citations()`
carried the identical `return 0, None` on failure, and its caller wrote that
zero into the column as a finding.

So the number we would have reported for Wikipedia reach was 430. It is 905.

Fixed the library function to return `None` on failure with retry/backoff on
429/503, and the caller now leaves the column NULL rather than writing a zero,
so a failed search stays in the queue instead of becoming a fact. Backfilled
`engagement.wikipedia_count` from `wikipedia_cites`, which is the measurement
that actually succeeded.

**Fourth occurrence of one bug.** OpenAlex 429 as "not indexed" (23,917
papers), PlumX 401 as "not licensed", the multilingual sweep's throttle as "no
citations" (3,118 papers), and now the English pass (121 papers). Every one is
a failure and an empty result sharing a return value. The rule going into
Stage 2: any function that fetches must be able to say "I don't know", and no
caller may store a zero it did not measure.

### Stage 1a closed

    done      79,081   72.0%
    blocked   30,554   27.8%
    failed       253    0.2%
    total    109,888

    36.6 GB compressed, 146 GB raw, 79,081 files
    mhtml 78,044 · html 1,037 (backfilled 2,360 rows whose format predated the column)

**44,205 records have both the article and its retraction notice archived** —
62% of the 71,698 usable records, and the real unit of analysis for Stage 3,
since comparing what a notice says against who kept citing it needs both.

Measured coverage where it was reachable: Wiley 99.9% (27,871), ScienceDirect
100% (10,049), IEEE 97.6% (9,846), Springer 97.2% (13,087), PLOS 100% (3,108),
Nature 86.4% (1,760), MDPI 99.9% (1,032, via WebKit).

Seven tables exported, including a new `impact_master.csv` — one row per paper
joining citations, Altmetric, Wikipedia across editions, and page-capture
status, with a `cited_more_after_retraction` flag.

---

## 2026-08-31 — Re-opening the 25,071 pages that were refused

25,071 of the 30,554 unreachable pages are refused by anti-bot measures rather
than by a paywall: 13,840 HTTP 403 at the connection layer, 6,951 archived by
the circuit breaker after a host went 40+ attempts with zero successes, 3,801
Cloudflare or captcha challenges, 479 bot-management products. For most of
them we have never seen the page at all, so "is the full text behind a
paywall" has never been a question we could reach.

### The verdicts on eleven of those hosts were never evidence

The WebKit sweep that decided which hosts were worth a second engine tried
**8 DOIs per host**. Eleven hosts covering 10,788 pages were written off on a
1-in-8 result, and the fallback was restricted to MDPI and cell.com.

That is precisely the evidence standard this project later disavowed. On 08-27
a 2-of-6 sample was read as a 33% success rate, 3,366 pages were requeued on
it, and those hosts then failed 152 consecutive attempts. The rule adopted
from that mistake — no success-rate claim from fewer than several dozen
attempts — postdates the sweep, and the sweep has never been redone under it.

So those eleven verdicts are guesses with a number attached, and 10,788 pages
are sitting behind them. `scripts/sweep_engines.py` re-runs the question
properly: 40 pages per host, serial, one host at a time, 2 s apart. These
hosts already refuse us and a sweep that provokes a harder block would cost
more than it could discover.

It also fixes a classification error the first sweep would have inherited.
Judging a capture by length alone marks **retraction notices as failures** —
some are two paragraphs, and they are the document that says why a paper was
withdrawn, so they are the pages we least want to discard. The sweep now
recognises a notice by title and accepts it at a lower length threshold.

### Firefox: a third engine, unavailable for a reason that had nothing to do with the web

Firefox has never been tested. Every attempt to launch it died with:

    BrowserType.launch: spawn UNKNOWN
    <launching> C:\Users\<non-ASCII username>\AppData\Local\ms-playwright\firefox-1466\...

The binary itself runs fine from a shell — `firefox.exe --version` reports
132.0. The failure is the **non-ASCII path**: this machine's Windows username
is in Chinese characters, and Playwright's Firefox launcher cannot spawn from
a path containing them. Chromium and WebKit tolerate it, which is why nobody
noticed.

Fixed by copying the browser to an ASCII path and pointing
`PLAYWRIGHT_BROWSERS_PATH` at it:

    C:\pwbrowsers\firefox-1466        launches, loads pages normally

Worth recording as an environment fact rather than a bug: a teammate on an
ASCII username would never reproduce it, and "Firefox doesn't work" would have
stayed in the notes as a property of the site rather than of this laptop.
Three engines are now available to test against the blocked hosts instead of
two.

### WebKit re-tested at 40 per host: the original verdict holds

480 attempts across the twelve largest blocked hosts, serial, 2 s apart.

     2%   1/40   www.tandfonline.com      39 x http403
     2%   1/40   validate.perfdrive.com   39 x thin
     2%   1/40   journals.sagepub.com     39 x http403
     0%   0/40   cell.com                 40 x thin
     2%   1/40   academic.oup.com         36 x http403
     2%   1/40   pubs.rsc.org             38 x http403
     0%   0/40   jbc.org                  38 x thin
     2%   1/40   pubs.acs.org             39 x http403
     0%   0/40   www.e3s-conferences.org  40 x http403
     2%   1/40   portlandpress.com        39 x http403
    10%   4/40   journals.lww.com         36 x http403
     2%   1/40   pubs.aip.org             39 x http403

    worth pursuing: 0 hosts

**The 8-sample verdict was right.** Re-testing it changed nothing about the
conclusion and everything about its standing: it now rests on 480 attempts
instead of 96, and can be stated to the client as measured rather than assumed.
Checking a result that turns out to be correct is not wasted work - the
alternative was carrying 10,788 pages behind a number nobody had earned.

**The shape of the refusals is the finding.** HTTP 403 dominates, arriving
before any page renders. On MDPI, WebKit succeeded 8/8 where Chromium managed
0 of 1,036, because MDPI's protection inspects *browser behaviour* - and a
different engine genuinely is a different browser. These twelve hosts are not
looking at the browser at all. They refuse the connection, which means the
engine cannot matter, and no amount of rendering-layer work will change it.

That is worth stating plainly in the report: for ~17,000 of the unreachable
pages the decision is taken before a browser is involved, so the remaining
options are not technical ones.

`cell.com`, `jbc.org` and `validate.perfdrive.com` return `thin` rather than
403 - a page loads carrying almost no text. Those are challenge or interstitial
pages that the sweep's pattern list does not name yet; they are refusals in a
different costume, not near-misses.

### Firefox: same answer, different refusal

480 attempts, same twelve hosts, same pacing.

     2%  www.tandfonline.com      39 x challenge
     5%  validate.perfdrive.com   38 x thin
     2%  journals.sagepub.com     39 x challenge
     0%  cell.com                 39 x challenge
     0%  academic.oup.com         38 x challenge
     0%  pubs.rsc.org             40 x challenge
     0%  pubs.acs.org             40 x challenge
     0%  portlandpress.com        40 x challenge
    10%  journals.lww.com         36 x challenge
     0%  pubs.aip.org             40 x challenge

**The engine route is now closed on 960 measured attempts across three
engines.** Worth noting the difference in how the refusal arrives: WebKit is
mostly turned away with 403 at the connection, Firefox is mostly allowed to
connect and then served a challenge page. Different treatment, identical
outcome - which is itself evidence that these hosts fingerprint the client
rather than watch what it does.

### Publisher TDM endpoints: 0 of 210

Crossref records text-and-data-mining links for 7,799 of the unreachable
pages - the publisher's own sanctioned channel, registered by the publisher.
Never tried until now.

Aggregate on a mixed sample of 45: 17.8% succeeded, but the successes were
Springer and Nature, which the crawler already reaches at 97%+. So the
aggregate flattered the route. Tested against the hosts that actually matter,
30 each:

    0/30  validate.perfdrive.com   challenge
    0/30  jbc.org                  tiny
    0/30  journals.sagepub.com     http403
    0/30  cell.com                 tiny
    0/30  dl.acm.org               http403
    0/30  journals.asm.org         http403
    0/30  karger.com               http403

Zero of 210. The lesson repeats: an aggregate rate across mixed hosts says
nothing about the host you need. Elsevier's TDM links are the exception still
open - they point at `api.elsevier.com`, which returns 403 for the same
entitlement reason as PlumX, so the campus test decides both at once.

### The Internet Archive was nearly dismissed on a broken query

First measurement: **0 of 240**. It was wrong. I had asked the Wayback API
about `doi.org/{doi}` - the resolver URL - when what is archived is the page
the resolver leads to. The real URL was sitting in `documents.final_url` for
21,814 of these rows, unused.

Re-asked correctly:

     76%  23/30  academic.oup.com       (1,636 blocked)
     50%  15/30  dl.acm.org             (  437)
     43%  13/30  pubs.acs.org           (  776)
     30%   9/30  www.tandfonline.com    (2,867)
     10%   3/30  journals.sagepub.com   (2,440)
      0%   0/30  cell.com / pubs.rsc.org / jbc.org
     ---
     26%  63/240

**Fifth instance of one failure mode.** OpenAlex 429 as "not indexed", PlumX
401 as "not licensed", Wikipedia throttling as "no citations", the English
Wikipedia pass, and now a malformed query as "no archive exists". Every one is
a question that failed being recorded as an answer that was measured. A
negative result deserves the same scepticism as a positive one, and the check
is always the same: confirm the request was well-formed before believing what
it returned.

This route also has a property the others lack: `web.archive.org` does not
refuse crawlers, so Chromium can render it and produce **real MHTML** - the
format the client asked for. Verification of that is in progress and is not
yet settled: a first pass rendered only 1 of 6 snapshots into usable content
(1.19 MB MHTML, 35,092 characters), while five returned an identical 218-
character stub that has still to be identified.

### Correction: "TDM is closed" was wrong, and the test that produced it was unauthenticated

I recorded TDM as ruled out on 0 of 210. That test sent nothing but a
User-Agent. Knocking on a paywalled endpoint with no credential and receiving
403 establishes that the link is not anonymous; it establishes nothing about
whether the route works.

Checked the publishers' own published policies rather than inferring from
status codes:

* **SAGE** — TDM is "expressly permitted in standard license agreements for no
  extra fee", provided the articles are ones "to which you have legitimate
  access", and SAGE publishes the TDM full-text URLs through Crossref. We
  already hold those links for 7,799 unreachable pages. Legitimate access is
  decided by the requesting IP.
* **Taylor & Francis** — non-commercial TDM allowed at no cost, but they must
  be **notified in writing in advance**. One email, no library needed.
* **Oxford University Press** — non-commercial TDM offered; contact required.
* **Wiley** — self-service click-through licence, token issued against an
  ORCID. Not needed: Wiley is already at 99.9%.

So the 403s from SAGE, OUP and Elsevier are consistent with a licence we
already hold and an address the publisher does not recognise. **The route has
never actually been tested.**

This also reframes the campus visit. It is not a PlumX test with extras - it is
the only test of the entire text-and-data-mining route, and TDM bypasses the
website altogether, so it is immune to the 403s, Cloudflare and bot-management
products that closed every rendering-layer option. `scripts/probe_campus.py`
runs the whole thing in one command: six Elsevier endpoints plus TDM links for
eight publishers, with the home baseline recorded for comparison.

### The same blindness, inverted: a refusal scored as a success

The first version of that probe classified a TDM response by status code and
size. IOP's links return **HTTP 200 with exactly 14,371 bytes** of

    <head><title>Radware Bot Manager Captcha</title>

and it scored them 6/6 OK - turning a total refusal into the best result on the
board. An earlier run of the same host with a content check had reported 30/30
challenge; the contradiction is what exposed it.

Seven instances now of one root cause, and this is the first running the other
way. The other six read a failed request as a negative answer; this read a
refusal as a positive one. Same fix in every case: **decide what a response is
by looking at what it contains, never by its status code or its size.** That is
the identical lesson as the Springer/Nature captchas served with HTTP 200 at
the very start of this project, and it had to be learned again in a new place.

`verdict()` in `probe_campus.py` now checks content first and status last, and
the corrected baseline reads 0/6 on every host - which is the honest number to
compare the campus run against.

### curl reached content three other clients could not — and I destroyed the evidence

Building a portable probe for a laptop, the macOS version uses `curl` because
every Mac has it. Run from the same machine, same network, same URLs, same
minute as the Python and PowerShell versions, it returned something they did
not:

    Taylor & Francis   200  3,064,750 bytes  %PDF-1.4   <- a real article PDF
    SAGE               200     55,883 bytes  text/html
    ACM                200    118,064 bytes  text/html

Python `httpx` and PowerShell `Invoke-WebRequest` had both received 403 for
those same URLs. So these hosts do discriminate between HTTP clients, and curl
was being let through where the other two were not.

Checked what arrived rather than trusting the status code, which is the only
reason the picture is honest: the SAGE and ACM responses were **generic landing
pages** — SAGE's titled "Sage Journals: Discover world-class research" — not
articles. Only the T&F response was real, and it was unambiguous: a 3 MB PDF
with a `%PDF-1.4` header.

**Then I lost it.** The next step should have been one more request, slowly, to
see whether it reproduced. Instead I ran a 27-URL batch, and when that failed
on an unwritable temp path I ran another 25 — and a failed local write does not
prevent the request from being sent. So roughly **52 requests went to Taylor &
Francis inside two minutes**, at a host whose bot protection had already
refused us 2,867 times. It now returns 403 to the same URL that had served the
PDF.

One real result, and the first thing I did with it was scale it up. That is the
opposite of how every other finding in this project was handled, and it is the
one host where the mistake costs the most: T&F is the single largest block at
2,867 pages.

**Consequences and what changes:**

* T&F is off limits for the rest of the day. Retesting means a **single**
  request after a long pause, never a batch.
* The claim "curl reaches T&F" rests on one sample and is now unverifiable
  until the block lifts. It is recorded as unconfirmed, not as a finding.
* `CAMPUS-TEST.command` fires two requests per host back to back. On hosts this
  sensitive that is still too aggressive for a probe whose entire purpose is to
  ask one question without changing the answer. Reduced to one request per
  host with a pause between.

The rule this project already had — never claim a rate from a handful of
attempts — has a second half that was missing: **a single positive result is a
reason to slow down, not to scale up.** Amplifying an unverified success is how
you convert a lead into a block.

---

## 2026-08-31 (evening) — Every ScienceDirect capture is an empty shell

A teammate shared results from their own crawler: 11,704 records, 21,426 pages,
21.1 GB. Their coverage is narrower than ours - Springer, Nature, PLoS, BMC and
Dove Press, all publishers we already reach at 97-100% - so their 97% success
rate and our 72% are not comparable, and I will say so when it is reported.

Two things in their file are worth more than the pages.

### 1,357 pages they hold that we recorded as blocked

All on Springer, Nature, BMC and Cureus, and 1,303 of ours on those hosts fail
with `challenge title: Client Challenge`. Springer stands at 97.2% and Nature
at 86.4% for us, so the refusals are intermittent rather than structural, and
their run proves a retry at another hour succeeds. Requeued 1,900 such pages
with `final_host` cleared, so the dead-host precheck cannot dismiss them on the
strength of the very attempt being retried.

### They verify identity; we verified size — and it hid a total failure

Their crawler records `article_doi_found`, `article_title_similarity`,
`article_valid_mhtml`. Ours accepted a capture on **bulk**: over 50 KB of HTML
or 40 KB of MHTML, and not a recognised challenge page. Bulk cannot tell one
article from another.

Applied the same test to our archive. On a 300-page sample, 12.3% matched
neither DOI nor title, and 36 of those 37 were ScienceDirect. Opened one:

    10.1016/j.dnarep.2021.103117
    MHTML 875,910 bytes          <- passes every size check comfortably
    DOI present in file          NO
    "Abstract"                   NO
    "References"                 NO
    "RETRACTED"                  NO
    Subject: ScienceDirect       <- the page title at capture time

875 KB of JavaScript and CSS with no article in it. ScienceDirect builds the
body client-side and the snapshot was taken before it arrived.

Swept 60 pages on each of the six largest hosts:

    www.sciencedirect.com     SHELL 60/60  (100%)
    onlinelibrary.wiley.com   full  60/60
    link.springer.com         full  60/60
    ieeexplore.ieee.org       full  60/60
    journals.plos.org         full  60/60
    www.nature.com            full  60/60

The defect is confined to ScienceDirect and total there. **All 10,049 pages we
recorded as ScienceDirect successes are empty.**

Corrected coverage:

    reported    79,081 / 109,888   72.0%
    actual      69,032 / 109,888   62.8%

That 100% ScienceDirect figure has been in every status report, in the Week 4
form, in the GitHub share README and in the numbers prepared for the client. It
was wrong, and it was wrong in the most flattering possible direction - the
single publisher we cited as perfect.

**The failure mode is the project's oldest one, one level deeper.** Springer
served captchas with HTTP 200, so we stopped trusting status codes and started
checking content. But "checking content" meant checking *how much* content,
and a loading shell is bulky. The rule that survives is narrower: a capture is
verified only when it can be shown to be **this paper** - its DOI in the file,
its title on the page. Nothing about size establishes that.

Recoverable rather than lost: the pages were never refused, only captured too
early. Testing whether waiting for the article container before snapshotting
fixes it; if so, 10,049 pages need re-crawling at roughly eight hours.

`scripts/verify_archives.py` now runs this check over the whole archive, and it
should have existed from the first day.

---

## 2026-09-01 — An external audit found eleven defects; ten were real

A full audit of the repository arrived (`gpt/capstone_audit_report_zh.md`).
Its headline is correct and worth quoting: the project had been treating
"a file was fetched" as "the right content was fetched", and treating
"the code contains this" as "this happened".

Every finding was checked rather than accepted. Ten stood, one did not.

### P0-1 — the Elsevier API key was hardcoded in six scripts and shipped in a ZIP

Mine. I embedded the key in the campus kit so the scripts would run on a
laptop, noted it in passing as "⚠️ the key is in here, delete it when done",
and sent that ZIP to the user four times. A credential exposure treated as a
footnote.

    CAMPUS-TEST.ps1, CAMPUS-TEST.command, and four files inside CAMPUS-KIT.zip
    probe_elsevier.py also printed the last six characters

Checked what was *not* affected: git history has never contained it
(`git log -S` finds nothing), and the GitHub push does not include these
scripts - they live outside the repository. Exposure was the local files and
whatever copies of the ZIP exist.

Removed from all six. They now read `ELSEVIER_API_KEY`, then
`elsevier-key.txt`, then prompt with echo disabled. The key fragment is no
longer printed anywhere.

### P1-2 — `counts_by_year` is a rolling window, not the citation history

The most damaging finding, because the number was published. OpenAlex reports
citations per year over a recent window; this project summed it and called the
result before/during/after the retraction.

Verified directly rather than from the documentation. Across 40,000 papers the
widest span is **15 years** and the earliest year clusters hard at **2012** - a
floor, not a distribution. 6,687 papers have `cited_by_count` exceeding the sum
of their yearly figures, 185,672 citations unaccounted for. And decisively:

    8,220 papers are retracted BEFORE their window opens, so citations_before
    is 0 by construction and any post-retraction citation makes them look like
    they gained attention after being retracted.

Recomputed on the 37,070 papers whose window actually covers their retraction:

    cited after retraction          25,749  (69%)
    cited more after than before    14,660  (40%)

The published figures were 37,888 (61%) and 6,692 over the whole corpus. They
mixed complete and truncated histories and are withdrawn, with a correction
note left in `data/share/README.md` and `docs/status-check-1-draft.md` rather
than a silent edit. `engagement.window_covers_retraction` now marks which rows
may be compared, and `Engagement.window_covers` carries the reasoning.

### P1-3 — the Wayback collector would have crashed on its first success

`snap["data"].encode("utf-8")` produced bytes, `storage.write_capture` then
called `.encode()` on them again. Reproduced: `'bytes' object has no attribute
'encode'`. The call sits outside the surrounding `try`, so the first genuine
capture would have ended the run rather than skipping a page.

It was invisible because the trial run reached **zero** successes - every
snapshot hit an Internet Archive outage. A collector that had never saved
anything looked like a collector that worked.

### P1-4 — a teammate's successes could never be merged

`merge_shard.py` omitted `capture_format`, and `PROMOTABLE` excluded
`blocked` - so a page we had recorded as refused could not be superseded by
someone else's successful fetch. A teammate's run holds 1,357 pages in exactly
that state.

Fixed, and with an addition the audit did not ask for: **their `done` is now
verified before it overwrites anything.** Ours was not evidence either - 10,049
refusal pages were filed as successes here - and a merge that trusts a status
code imports someone else's version of that mistake.

### P1-6 — recording a failure made it permanent

Altmetric, PlumX and the API enrichment all selected DOIs *absent from the
table*. A row written with an error therefore never came back: a transient
timeout became a final answer by the act of being recorded. 383 rows were in
that state.

### P2-1 — the breaker's own report changed the breaker

`dead_hosts()` called `is_dead()`, which increments the parole counter. So
listing dead hosts for the startup log consumed one parole slot per host, real
parole requests happened less often than the intended one in fifty, and a host
could be reported alive because the log's own call landed on the boundary.
Observation altered the thing observed, in the one place least likely to be
noticed. `is_dead(host, consume=False)` now exists for reporting.

### P2-3 — an experiment that was never run, reported as a result

`experiment_engine.py` defines `ALT_PATHS` for six publishers and never reads
it; AST confirms zero loads and a single `page.goto`. The status report
recorded "Open-access direct links — no effect, same host, same refusal".

That conclusion had no experiment behind it. Withdrawn.

### The one that did not stand

The audit reported 21 percent-encoded DOIs of which 20 decode to something
findable, implying 20 recoverable pages. Measured: **20 of the 21 are already
`done`** - the encoded form resolved fine. One is blocked. The normalisation
gap is real; the recoverable-page count is 1, not 20.

### What the audit could not have found, because it needed two processes

Restarting the crawler alongside the verifier produced
`sqlite3.OperationalError: database is locked`, killing a crawl that had
already fetched 575 pages. SQLite's default journal allows one writer and fails
the second immediately rather than waiting. `db.connect()` now sets WAL,
`busy_timeout=60000` and `synchronous=NORMAL`. Earlier unexplained crawler
exits were most likely this.

---

## 2026-09-01 — Overnight: verification finished, and a recovery route that did not pay

### Every archive now carries proof it is the right paper

70,168 archives checked individually against three tests - the DOI appears in
the file, the title matches the record, and the document role is recorded.
Results persisted to `archive_checks`, so this never has to be recomputed
again; the first full run kept nothing and had to be repeated.

    47,923  ok                  DOI and title both match
    20,972  doi-only            DOI present, publisher words the title differently
       995  ok-role-differs     both match; only the naming convention differs
        78  title-only
       200  suspect             neither matches

99.7% carry their own DOI. The 200 that do not were already marked `usable = 0`
by the earlier DOI-only pass - two independent checks, run days apart with
different code, converging on the same set.

**The 995 are the interesting ones.** Wiley and Hindawi title a retraction
*notice* "Retracted: <title>"; Elsevier and Springer use the same word for the
retracted *article*. An earlier version of the role check treated the prefix as
decisive and rejected genuine Wiley notices at a title similarity of 1.00 with
the DOI present. Role is now recorded and never decisive: a naming convention
cannot outvote two pieces of direct evidence.

### The Wayback estimate was wrong, and the mistake is worth naming

I told the user roughly 6,700 pages were recoverable from the Internet
Archive. Measured over 4,718 pages:

    4,674  no snapshot
       44  captured        15.3 MB, real MHTML

**0.9%, against a predicted 22%.**

The sampling measured *whether a snapshot exists*. The collector measures
*whether the snapshot renders into an article*. Those are different questions
and I reported the first as though it answered the second - many snapshots are
redirect records, consent walls, or pages the archive captured before the
content loaded.

Stopped it: 26,000 pages remaining at that rate is eleven hours for perhaps 230
more pages. The 44 are kept and are genuinely useful - real MHTML, the format
the client asked for - but they are labelled as Internet Archive captures, not
publisher pages.

### Exports corrected: `status` is not `usable`

`cs43_master.csv` counted a record as captured when the request finished. That
included 10,245 refusal pages. The master now counts `usable = 1` and carries
`orig_usable` / `notice_usable` so nobody downstream repeats it:

    both pages captured   38,958  (54%)   was 39,077 under the old rule

### Final Stage 1 position

    archived and verified   69,957 pages   31.2 GB
    on disk, unusable       10,245 pages    5.7 GB   (kept as evidence)
    refused by publishers   39,459 pages
    records with both pages 38,958  (54%)

Four requests outstanding, covering 41,525 pages: the University copyright team
(escalated by the library, who confirmed TDM via API is permitted where the
licence allows and that our process was correct), Elsevier, and four publishers
directly.

---

## 2026-09-01 — Stage 2, and four versions of one function

Built the Stage 2 pipeline: `mhtml.py` decodes the MIME archive back into the
captured document, `extract.py` pulls title, abstract, body and the withdrawal
statement out of it, `scripts/extract_text.py` writes the result to
`document_text`.

The parsing was straightforward. The notice extractor took four versions, and
every one of them looked right until it was measured against a real page.

    v1  ran from the first cue to the last. A Nature notice began correctly and
        continued 8,000 characters into the reference list, because the word
        "retracted" appears there too.
    v2  narrowed the fallback but let the structural selectors return whatever
        the matched element held. `div[class*='retract']` matches a container
        that on Dovepress wraps the whole article: a 4,531-character "notice"
        beginning "Authors ... Received ... Published ...".
    v3  stopped at "References" - except `_clean` deleted that heading as junk
        before `_narrow` could reach it, so the stop condition existed and
        could never fire.
    v4  kept the heading through the junk filter, then dropped it again with a
        `len > 25` line filter, because "References" is ten characters.

Three of those four are the same shape: a rule that reads a marker, and another
rule upstream that removes it first. The symptom was identical each time -
notices running to the length cap - which is why v3 and v4 both looked like
progress.

### The cue list was written from imagination

Dovepress states a retraction as:

    At the author's request, we, the Editors and Publisher of OncoTargets and
    Therapy, have retracted the following article.

It matched **nothing** in the original pattern list. The request clause is
reversed - `at the request of the author` versus `at the author's request` -
and it is "and Publisher", never "the publisher". So two pages whose text says
plainly *"unable to explain the image duplication, provide original data or
verify the validity of the reported findings"* came out with an empty notice.

Across ~5,000 documents, **364 pure retraction pages had no notice extracted**
and Stage 3 would have had no way to distinguish "this notice gives no reason"
from "the extractor missed the reason".

Rebuilt the patterns from wording found on actual pages, straight and
typographic apostrophes both, and added a third route: a page that *is* the
notice can use its own body, because a standalone notice has no banner to find
and requiring one returned nothing for a 4,244-character page that was entirely
the reason.

After: **0 of 221 retraction documents with an empty notice.**

### `notice_method` exists so the next bad rule is findable

Each row records which route produced its notice - `selector:<css>`, `cue`,
`body-is-notice`, or `none`. When a selector starts misfiring it can be found
with a query instead of by re-reading 69,657 archives, which is how the
Dovepress container was found in the first place, by hand.

### Tests, at last

`tests/test_extract.py`, 19 cases, each one a defect that shipped rather than a
hypothetical: the Dovepress wording, the navigation bar leading a notice, the
reason sitting past a fixed window, the reference list being swallowed, a
standalone notice page, and an ordinary article that must *not* acquire a
notice. 44 tests total now; before today all 25 were Stage 1.

The audit was right that "syntax passes" is not "works". Four versions of
`_notice` passed every syntax check and three of them were wrong.

## 2026-09-01 (afternoon) — Restart after a reboot, and a redirect that swallowed both jobs

Machine rebooted with two long jobs in flight. Both are resumable by design, so
the only real question was whether they came back where they left off.

Before the reboot, everything was stopped deliberately and the state recorded:

| | before reboot | after restart |
|---|---|---|
| Stage 2 extracted (`extractor_version >= 3`) | 3,100 | 3,100 |
| Altmetric detail rows | 161 | 161 |
| Altmetric posts stored | 551,505 | 551,505 |
| `journal_mode` | wal | wal |
| `PRAGMA quick_check` | ok | ok |

Nothing lost. Resume works because neither job trusts a counter: extraction
skips rows whose `quality IN ('full','notice-only') AND extractor_version >= 3`,
and the detail collector's target query left-joins `altmetric_detail` and takes
only the DOIs with no row there. Both re-derive the remaining work from the
database on every start, so an interrupted run costs at most the one item in
flight.

### The first restart failed silently, twice

Both processes exited with code 1 having done nothing. The cause was not in
either script: `logs/` did not exist. The reboot removed it - it had been
created ad hoc during an earlier session and never committed - and

    python -u scripts/extract_text.py > logs/extract_text.log 2>&1

fails in the shell *before* Python is executed, because the redirect cannot open
its target. The failure therefore had no traceback and no log file, since the
log file is exactly what could not be created. Two jobs reported "failed" with
no output anywhere.

Worth keeping in mind as a shape: **a job whose only diagnostic channel is the
thing that failed reports nothing at all.** The check that caught it was not
reading a log - it was noticing that `tail` could not find the log either.

`logs/` recreated. It should be a `mkdir -p` inside the runner rather than an
assumption about the working tree; noted for the next pass over the scripts.

### Running again

    extracting 66,864 documents
      1,000/66,864  full 557    notice 434  thin 9   failed 0  | 29,798/h
      2,000/66,864  full 1,123  notice 865  thin 12  failed 0  | 29,492/h

    7 DOIs errored during the summary pass and will be re-asked first
    Altmetric detail: 22,215 papers at 1.2s apart - about 7.4 h

Stage 2 at ~29,500 documents/hour, so roughly 2.2 hours for the remaining
66,864. Quality split holds at the earlier ratio and `failed` is still 0.

Altmetric detail is ordered by `cited_by_msm_count` descending, which means the
heaviest papers come back first: 29 papers into the run, stored posts went from
551,505 to 662,129. That is 110,624 posts from 29 papers - an average near
3,800 each - and it is a property of the ordering, not a surprise. The tail will
be far thinner, so the posts-per-paper figure seen early in this run must not be
extrapolated to the remaining 22,000.

## 2026-09-01 (evening) — "Blocked" was often a fact about our client, not the publisher

Three findings today, none of which came from crawling harder.

### The campus network answers differently for every publisher

A second campus test, from 129.78.56.152 (AS23719, University of Sydney), on
sixteen hosts the first test never asked about:

| documents | publisher | home | campus |
|---|---|---|---|
| 10,053 | ScienceDirect | http403 | **http 403** |
| 1,636 | Oxford University Press | challenge | **OK 151 KB** |
| 1,001 | Royal Society of Chemistry | challenge | tiny 2 KB |
| 588 | Portland Press | challenge | **OK 147 KB** |
| 547 | AIP Publishing | challenge | **OK 160 KB** |
| 431 | AACR | challenge | **OK 262 KB** |
| 331 | Karger | challenge | **OK 132 KB** |
| 361 | Cureus (control) | **ok** | challenge |

Five hosts, 3,533 documents, that a University address unlocks and a home
connection does not. And Cureus the other way round.

The first campus test concluded that the university network was worse than home
because Elsevier blocked it at the Cloudflare edge. That generalised one
publisher's configuration to all of them. A Cloudflare rule belongs to the
customer, not to Cloudflare: elsevier.com refusing an address says nothing about
karger.com. IOP had already contradicted the general claim on the same page of
results and it was not noticed.

**The control was designed wrong.** Cureus was chosen as "a host that answers
from home, so if it fails the harness is broken" - but whether a host answers is
exactly the variable under test, and Cureus turned out to block the university
address. The harness was fine; five hosts returned articles that passed a DOI
identity check. A control has to be something the measurement cannot move: an
IP-neutral endpoint, not a publisher.

### The browser was the problem, not the network

The archive is captured with Playwright, because MHTML needs a browser, and
bot-management products fingerprint exactly that. So `blocked` in this database
has always meant *blocked while driving a browser* - a fact about our client,
recorded as a fact about the publisher.

`scripts/probe_plain_http.py` asks each blocked host once with httpx and an
honest User-Agent. 150 hosts, 36,186 documents behind them:

    20 hosts returned the article    1,281 documents
    109 hosts still challenge

Cureus is 518/518 blocked in the database and answered ten of ten. 223 were
collected before it began challenging - the block is behavioural, and the
remaining 295 need a slower pass.

### An article that arrives as a JavaScript string

The Cureus captures then looked like shells. Stripping scripts left 781
characters of specialty menu; five files in a row measured 8,5xx characters and
exactly 19 `<p>` tags, which is the uniformity of a template, not of papers.

They were not shells. The whole article - Introduction, Discussion, References -
is in the page as a JavaScript string with its angle brackets written
`\u003cp\u003e`. Decoding recovers 23,035 to 62,272 characters of body.

Two versions of the fix. The first decoded the escapes and relied on lxml
recovering the subtree; it worked on Cureus and produced nothing at all on a
clean synthetic page with identical escaping. A fix that works for one
publisher by accident is worse than no fix, because it will be trusted. The
second deletes the script *tags* while keeping their contents, so the markup
becomes part of the document deterministically.

Gated on the signal - 20 or more escaped angle brackets. Forty random captures
from the rest of the archive carry zero. Documents that already extract cleanly
produce byte-identical output.

`EXTRACTOR_VERSION` is 4 and everything is being re-extracted. Nothing is
expected to change for the other 69,957, which is the reason to run it: forty
sampled captures are not evidence about seventy thousand, and any other host
shipping an escaped body can only be found by looking at all of them.

### Two bugs in the collector, both of the same kind

**Tab collapse.** `while IFS=$'\t' read -r doi host tdm url` looks correct and
is not: tab is an IFS *whitespace* character, so bash collapses a run of tabs
and an empty column disappears. Every row with no TDM link had its page URL
slide one column left, the fallback fetch ran against an empty string, and the
log filled with `none / 0 bytes` - which reads as a publisher refusing and was
the script asking for nothing. It emptied a 2,531-page IOP run and would have
hit ~17,000 rows of the main worklist.

**Counters in a subshell.** `tail ... | while` puts the loop in a subshell, so
every count incremented inside it is discarded at the end and the summary line
reports zero regardless of what was collected. A run that worked, reporting that
it did nothing.

Both are the same failure as the original ScienceDirect disaster: a number that
had come loose from the thing it was counting.

### What a refusal looks like, measured

|  | bytes | visible text | `<p>` tags |
|---|---|---|---|
| ScienceDirect refusal | 122,456 | **801** | **2** |
| six real articles | 122k–342k | 9,337–28,813 | 51–84 |

The campus collector now checks, in order: challenge markup, soft-refusal
wording, its own DOI, then paragraph count. Pages under ten paragraphs are kept
but saved as `.thin.html` and counted apart - the mistake in the ScienceDirect
case was never keeping the files, it was counting them as articles.

The threshold is too generous: the Cureus shell has 19 paragraphs and would pass
it. The counts a campus run prints are therefore optimistic, and the real
classification happens here, against the decoded body.

### ScienceDirect, settled

10,050 of 10,053 are HTTP 200 with a full-size page and no article. Opening one:

    "There was a problem providing the content"   present
    "not have access" / "Sign in" / "Purchase PDF"   absent

A paywall offers a way to pay. This page offers nothing, which makes it bot
detection rather than entitlement - and campus returning 403 as well settles it.
Those 10,053 depend on the Elsevier Article Retrieval API, which their support
confirmed today is *disabled on our key* rather than refused to us.

### v4 re-extraction, finished

70,180 documents.

| quality | n | share |
|---|---|---|
| full | 40,277 | 57.4% |
| notice-only | 29,763 | 42.4% |
| thin | 139 | 0.2% |
| failed | 1 | — |

**Cureus: all 223 now `full`**, body 2,974–184,553 characters, mean 21,055. The
escaped-markup fix works end to end, not just in the extractor.

The re-run existed to answer one question - whether any other host ships its
article as a JavaScript string - and it answered it. Of the 140 thin and failed
documents, opened individually:

    escaped markup still hidden   0
    the page is a refusal         0
    genuinely short page        139
    archive unreadable            1

So the trick was unique to Cureus in this archive. That is worth having as a
measurement rather than as a belief: forty sampled captures had said the same
thing, and forty captures cannot say anything about seventy thousand.

The three thin rows with the most visible text were checked in case the
classifier was wrong about them - 11,200, 8,202 and 4,165 characters on the page
against 169, 387 and 350 characters of extracted body. Opening the largest: the
11,200 characters are "Skip to main content / For Authors / Submit A Book
Proposal / Publishing Ethics", a menu wrapped around an abstract. trafilatura
was right and `thin` is the right label.

One cost worth recording: v4 overwrote the v3 rows, so the two versions can no
longer be compared document by document. The quality shares are unchanged within
rounding, which is the expected result, but "unchanged" is now an inference from
totals rather than a diff. A version bump should write alongside, not over.

## 2026-09-02 — Stage 1b Altmetric detail: complete

22,215 papers fetched in this run, 0 remaining, no rate limit reached - the
quota headers were still at their ceilings when it finished (3,600 hourly,
86,400 daily). 22,369 papers now carry a detail row and **1,437,967 posts** are
stored.

### The two things the summary endpoint could not do

**Attention can now be placed in time.** 1,437,966 of 1,437,967 posts carry a
timestamp - one does not. For all 22,368 papers with posts, both a post
timestamp and a retraction date exist, so every mention can be put before or
after the retraction. The earlier collection recorded that split as impossible;
it was a property of the endpoint chosen, not of Altmetric.

**A mention can be read, not just counted.** 140,800 posts carry a title,
139,527 a summary, 152,828 a URL.

### Where the mentions are

| source | posts | papers |
|---|---|---|
| twitter | 1,281,535 | 14,028 |
| reddit | 43,238 | 1,859 |
| news | 36,888 | 2,513 |
| patent | 20,155 | 3,028 |
| blogs | 14,250 | 6,403 |
| facebook | 10,867 | 2,028 |
| peer_reviews | 8,085 | 1,627 |
| wikipedia | 7,647 | 3,174 |
| bluesky | 6,286 | 509 |
| policy | 2,123 | 904 |
| googleplus | 1,800 | 329 |
| guideline | 1,771 | 700 |
| video | 1,445 | 501 |
| podcasts | 858 | 309 |

Removing the channel whitelist was worth it: bluesky, patents, guidelines,
video, podcasts and peer reviews are 38,600 posts across sources the earlier
seven-channel list would have dropped, and patents in particular - 3,028 papers
cited by a patent after retraction - are a form of downstream use nobody on this
project would have thought to enumerate.

### One figure that must not be reported carelessly

**1.4 million posts is not 1.4 million readable items.** 1,281,535 of them are
X/Twitter, which Full Access returns as post IDs rather than text. The readable
set is roughly 140,000, and the part Stage 3 has to judge for accuracy is the
news: **36,888 articles across 2,513 papers**, 36,884 with a headline and 33,033
with a summary.

That is the number for the report: 2,513 retracted papers were covered by news
outlets in a way we can actually read and assess. Not 22,369, and not 62,867.

## 2026-09-02 (evening) — Three numbers for the same thing, and only the third was measured

The open-access route was investigated as a way to reach documents no publisher
will hand over. It produced three figures in one afternoon. Two of them were
wrong, in the same way, and the way is worth writing down because it is the same
shape as the ScienceDirect failure that cost this project a month.

| figure | what it rested on | what it was worth |
|---|---|---|
| **20,926** open access | OpenAlex's `is_oa` flag | a licence, not an open door |
| **8,682** reachable | one probe per host | wrong by roughly five times |
| **~3,000–3,500** usable from PMC | 40 sampled articles through the sanctioned API | measured |

### 20,926: open access is a licence, not a door

53% of the 39,708 missing documents are open access. Then the hosts were
counted: 2,362 of those "open" copies are on iopscience.iop.org, 1,627 on
tandfonline.com, 854 on sciencedirect.com. Bronze open access - 5,073 of them -
is *defined* as free to read on the publisher's own site and nowhere else.

Being allowed to read something says nothing about whether the door is guarded.

### 8,682: one sample per host, treated as a property of the host

150 hosts were asked one question each and 38 answered, so the documents behind
those 38 were counted as reachable. 7,638 of the 8,682 - 88% - sat behind two
hostnames, `ncbi.nlm.nih.gov` and `pmc.ncbi.nlm.nih.gov`, both PubMed Central.
**One request decided 88% of the queue.**

Sampling twenty PMC articles instead of one: **3 of 20 returned the article.**
The other 17 returned 21,340 bytes of JavaScript shell with no DOI in it. NCBI
has moved PMC to a new host and put a bot check in front of it; headless
Chromium gets HTTP 203 and a page whose body is `visibility: hidden`.

This morning's entry, about the extractor re-run, says: *"forty sampled captures
are not evidence about seventy thousand."* That was written six hours before one
sample was allowed to speak for 7,638 documents.

### What the sanctioned route actually gives

NCBI publishes E-utilities for exactly this, and it does not fight bot
protection. Forty articles fetched through it:

    FULL TEXT                    11   28%
    partial / notice             11   28%
    publisher blocks full text   16   40%
    metadata only                 2    5%

The 40% come back carrying an explicit comment: *"The publisher of this article
does not allow downloading of the full text."* PMC holds the record; the
publisher still controls the body.

The 28% "partial" needs care in the other direction. **3,984 of the 7,638 PMC
documents are retraction notices**, and a retraction notice is one or two
paragraphs - for those, "partial" is the complete document. Counting them as
failures would understate the route as badly as counting the 40% as successes
would overstate it.

Realistic yield: **3,000-3,500 usable documents** of the 7,638.

### The format question this forces

E-utilities returns JATS XML. The client asked for *"HTML ... a rendered page,
not a raw page. Rendered means the page is fully loaded"* and typed `mhtml`.
XML is not that.

The browser route, which would produce MHTML, is the one PMC refuses: 15%.

This is not a new question introduced by the open-access route. **IOP is already
supplying 2,594 documents as JATS XML** - IOP offered PDF or JATS
and there is no third option, their TDM API does not exist yet and their bot
protection cannot be disabled per user. So roughly 5,600 documents across two
sources arrive as structured XML or not at all.

For Stage 2 and Stage 3 the XML is better than a rendered page: title, abstract,
sections and references arrive already separated, and the extractor - four
versions of which were needed to find a retraction notice inside publisher HTML -
is not required at all.

That is a decision for the client, not for us. It goes to him on Sunday as one
question about both sources, not as two.

### What was actually built today

* `oa_locations` and `oa_all_locations` - every free copy OpenAlex knows of, not
  just the one it ranks first. `best_oa_location` prefers the publisher's version
  of record, which is the single copy we cannot fetch; 15,262 of the open papers
  list more than one location and the alternates were being discarded.
* `probe_oa_hosts.py` - asks each OA host directly instead of inferring
  reachability from stale evidence. An earlier inference had written off 1,203
  documents because `ncbi.nlm.nih.gov` and `pmc.ncbi.nlm.nih.gov` are different
  strings, and counted 485 SAGE documents as reachable on captures from months
  ago, before SAGE started refusing us.
* `collect_oa.py` - captures through the same browser path as the rest of the
  archive, and records `source_kind`, `oa_host` and `oa_version` per document.
  The version is read from the page ("Author manuscript ... Published in final
  edited form as") rather than from OpenAlex, which labels 7,948 of these
  `submittedVersion` and is wrong about nearly all of them.
* `fetch_page` takes an optional `url`, so an open-access copy can be fetched
  without duplicating the capture path. Identity checks are unchanged: a capture
  must carry the DOI it is filed under, wherever it came from.
* The quota bug: OpenAlex allows 1,000 requests a day and says so in
  `Retry-After`. The collector slept against a 17.5-hour wall for ninety minutes
  because it read the status code and not the headers, and printed progress once
  every twenty batches, so a stalled run and a healthy one looked identical.
  Third instance today of silence being mistaken for progress.

### The rule this needs

A number that will be reported gets a sample proportional to what it claims, and
the sample size travels with the number. "8,682 reachable" and "8,682 reachable,
from one request per host" are different statements, and only the second one
could have been challenged before it was believed.

## 2026-09-03 — Stage 2 was finished, and a quarter of it was cookie banners

Stage 2 reported 70,216 documents extracted and was treated as done. The
question that reopened it was not a bug report, it was "are you sure this is
good" — so instead of re-reading the code I counted strings that cannot belong
to a scientific article, across the whole table rather than a sample.

| marker | v4 (as shipped) | v5 |
|---|---:|---:|
| `similar content being viewed by others` | 7,204 | 0 |
| `all rights reserved, including rights for text and data mining` | 3,015 | 1,753 |
| password-reset flow | 1,615 | 1,615 |
| `we use cookies` | 285 | 285 |

Publisher furniture — cookie consent panels, related-article rails, IEEE
customer-service numbers, copyright footers — was sitting inside the extracted
text of roughly a quarter of the rows. Stage 3 has to compare a retraction
notice against what the press said about the paper, and a notice with two
thousand characters of cookie policy in it is not a notice.

### The measurement that changed the design

The obvious fix is to truncate from the first furniture marker. Before writing
that, I measured where `similar content being viewed by others` actually falls
in Springer and Nature text: **median 14% of the way in** — between the
abstract and the body, not after it. Truncating from it would have emptied
**7,204 bodies while reporting them clean**, which is worse than the problem,
because a body that is gone is not visibly wrong.

So the rule is split in two. `_JUNK_LINE` drops the line and keeps reading;
`_TAIL_START` cuts to the end, and a phrase only qualifies for the second list
once it has been measured to sit in the last quarter of the text. Both are
guarded: if stripping leaves less than 120 characters of body or 40 of notice,
the original is kept, because a rule that has eaten the document has matched
something it should not have.

Cost of the whole pass: **0.25% of the body characters** across 70,216
documents (739,537,589 → 737,718,118).

### What v5 did not fix

`document_text_v4` was overwritten in place by an earlier run, so v4 and v5
could no longer be compared row by row until a snapshot table was added. From
v5 onward each version is snapshotted before the next one runs — the only
honest way to answer "did this change make it better" is to still have the
previous answer.

## 2026-09-04 — Seven findings, five fixes, one refusal, and one the audit missed

A second external audit of the shared bundle listed seven defects. All seven
were real. Each was checked against the database before being fixed, and the
check changed the fix twice.

### Four ways a re-run made the data worse than not running it

These are the dangerous ones, because nothing about them looks wrong until
somebody rebuilds the dataset and quietly gets a different one.

**`collect_engagement.py`** rebuilt each row with `INSERT OR REPLACE` and left
`window_covers_retraction` out of the column list, so a re-run would have set
all **62,867** flags to NULL — the flag that decides whether a before/after
citation comparison is meaningful at all. I wrote a test for that specific bug
and then wrote a generic one: *every column the script computes must appear in
the statement that writes it*. The generic test found a second instance in the
same statement — `wikipedia_count` and `wikipedia_pages` are filled by a later
pass of the same script, and the rebuild would have wiped **3,174** rows of it.
The specific test would never have found that. Both columns are carried forward
by subquery now.

**`verify_archives.py`** did not set `documents.usable`, and no committed
script did. Stage 2 reads only `usable = 1`, so a rebuild from an empty
database produced 70,000 captures, zero usable rows and an empty Stage 2 —
the pipeline was not reproducible from its own code. The rule was recovered
from the data rather than invented: `doi_found` alone gives 69,890,
`doi_found OR title_similarity >= 0.55` gives **69,957**, which is exactly what
is in the database. Re-running flips 11 rows to usable and **zero** rows away.

**`build_queue.py`** refreshed five columns on conflict — title, publisher,
nature, reason, paywalled — and left the rest at their first-import values. An
upstream correction to a DOI, a retraction date or a subject could not reach
the database no matter how often the loader ran. All non-key columns are
refreshed now. DOI changes are **reported, not applied silently**: a changed
DOI files an already-archived capture under a paper it is not, and that is a
decision, not a merge. Records that disappear upstream are kept — a withdrawn
retraction record is itself a finding.

**`merge_shard.py`** skipped the file copy when a file of that name was already
present, then ran the `UPDATE` anyway — so the database took the teammate's
byte count, `fetched_at` and `final_url` while the file on disk stayed ours.
One such row is already in the dataset (`10.1245/s10434-024-16763-y`, recorded
142,503 bytes, actually 141,816). Fixed and the row corrected; same-name
different-bytes collisions are now reported and left alone, because neither
copy can be assumed to be the better one.

### Two columns in the shared export that said something untrue

`cited_after_retraction` was `citations_after > 0`, which ignores whether
OpenAlex's rolling citation window reaches back to the retraction at all. For a
paper retracted before the window opens, `citations_before` is 0 by
construction and every later citation looks like post-retraction attention.
That is how the 37,888 figure was produced, corrected in the docs to 25,749 —
and the ZIP still shipped the withdrawn version, so anyone reading the CSV
reproduced it. Now:

| value | rows | meaning |
|---|---:|---|
| 1 | 26,969 | cited after the retraction |
| 0 | 11,437 | not cited after the retraction |
| *(blank)* | 33,292 | the window does not reach the retraction — no answer |

Blank rather than 0, because a missing measurement is not a negative result.
`citation_window_ok` is exported beside it so a reader can filter instead of
trusting the flag.

`days_to_retraction` was declared in the header, computed by an `if` that
assigned `""` in both branches, and shipped blank for all 71,698 rows. It is
now populated for all 71,698; **median 499 days** from publication to
retraction.

### The refusal

The audit also asked for the **5,560 notices sitting at exactly 3,000
characters** — cut by the budget rather than by a boundary. I wrote the fix: a
shape-based stop for the related-article rails that fill them, "three
consecutive lines that look like titles". Measured on twelve capped notices, it
**emptied three completely and cut a fourth from 3,000 characters to 32**,
while still leaving six at the cap. Reverted.

Truncation that is visible in the data (`LENGTH(notice) = 3000` finds every
affected row) beats a rule that removes more signal than noise. The 5,560 go to
a manual QA queue instead, and the reason is recorded next to the constant so
nobody rediscovers the same idea in three weeks.

### The one the audit missed

While measuring the fixes I ran the same string count as yesterday and found
**285 documents still carrying "we use cookies" — unchanged across v4, v5 and
v6**. 283 of them are one host, `www.ovid.com`, with one banner shape. It had
survived two existing rules by one word each:

* the copyright rule required the year immediately after the word, and Ovid
  writes `Copyright © 2026 Wolters Kluwer`;
* the rights rule read `all rights reserved, including **rights** for text and
  data mining`, and Ovid writes `including **those** for`.

On the 285 affected documents the fix removes **49% of the stored body** — the
footer is half of these short pages. On 300 controls it removes **0.24%** and
empties nothing. Measurements in `data/reports/ovid-footer-fix.csv`.

The v6 run was already 9,850 documents in when this was found. It was stopped
and restarted with `--redo`, because 9,850 rows extracted under different rules
from the other 60,366, all labelled version 6, is the same class of defect as
the four above: a dataset that cannot be reproduced from the code that claims
to produce it.

### The rule this needs

The audit found what a reader of the code could find. The 285 were found by
counting one unambiguous string across the whole table — the question "does a
sample look clean" and the question "how many rows contain this exact phrase"
have different answers, and only the second one is checkable.

54 tests pass.

