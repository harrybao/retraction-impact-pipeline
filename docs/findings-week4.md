# Feasibility findings — Week 4

Evidence gathered before committing to an architecture. All numbers are
reproducible: `python scripts/build_queue.py` then `python scripts/probe.py`.

## 1. The frontier

| | |
|---|---|
| Rows in `retraction_watch.csv` | 71,944 |
| — of which blank trailing rows | 246 (dropped) |
| Usable records | **71,698** |
| Valid `OriginalPaperDOI` | 65,752 (91.4%) |
| Valid `RetractionDOI` | 67,029 (93.2%) |
| Records with neither DOI (skipped entirely) | 3,529 |
| **Pages to render** | **132,781** |

`RetractionNature`: Retraction 66,445 · Expression of concern 3,587 ·
Correction 1,506 · Reinstatement 160.

## 2. Route on the DOI prefix, not the Publisher column

The `Publisher` column has **1,713 distinct spellings** ("Elsevier",
"Elsevier - Cell Press", "IOS Press (bought by Sage November 2023)"…). The DOI
registrant prefix is the stable routing key — 970 distinct values, and heavily
concentrated:

```
top  5 prefixes →  76,677 pages (57.7%)
top 20 prefixes → 110,349 pages (83.1%)
top 50 prefixes → 123,552 pages (93.0%)
```

**Design consequence:** ~20 publisher adapters cover 83% of the work. A generic
"smart crawler" for all 970 prefixes is not achievable in one semester, and is
not needed. Build 20 adapters + one generic fallback.

## 3. Publisher probe — 36 live samples, 12 prefixes

**Overall: 20/36 succeeded (56%), 16 blocked (44%) on first attempt.**

| prefix | publisher | ok | resolves to |
|---|---|---|---|
| 10.1016 | Elsevier | 3/3 | sciencedirect.com |
| 10.1002 | Wiley | 3/3 | onlinelibrary.wiley.com |
| 10.1371 | PLOS | 3/3 | journals.plos.org |
| 10.1155 | Hindawi | 2/3 | **wiley.com** *and* hindawi.com |
| 10.1109 | IEEE | 2/3 | ieeexplore.ieee.org |
| 10.1111 | Wiley | 2/3 | onlinelibrary.wiley.com |
| 10.1007 | Springer | 1/3 | link.springer.com, idp.springer.com |
| 10.1038 | Nature | 1/3 | nature.com, idp.nature.com |
| 10.1177 | SAGE | 1/3 | journals.sagepub.com |
| 10.3233 | IOS/SAGE | 1/3 | journals.sagepub.com |
| 10.1080 | Taylor & Francis | 1/3 | tandfonline.com |
| 10.1088 | IOP | **0/3** | **validate.perfdrive.com** |

Block causes: HTTP 403 ×7 · bot-management host ×4 · challenge title ×2 ·
interstitial too small ×2 · challenge markup ×1.

### 3.1 The critical finding: blocks arrive as HTTP 200

Springer and Nature return **200 OK** with a complete-looking page whose title
is `Client Challenge` and whose body reads *"Enter the characters seen in the
image below … Download audio CAPTCHA"*. Status code, response size and absence
of exceptions all look healthy.

> A crawler that trusts HTTP status would file tens of thousands of CAPTCHA
> pages as valid articles, with **no error anywhere in the logs**. The dataset
> would be silently poisoned and the Stage 3 analysis meaningless.

Detection must therefore be content-based (`rwpipe.fetch.classify`): bot-vendor
hostnames (`validate.perfdrive.com`, `idp.*`), challenge titles/markup, and a
50 KB floor on rendered HTML — a real article page is 250–500 KB.

### 3.2 IOP is hard-blocked

`10.1088` (2,564 pages) fails 3/3, handed to Radware Bot Manager. Needs its own
mitigation and is a candidate for scope negotiation with the client.

### 3.3 Hindawi is partially migrated

`10.1155` is the single largest prefix (23,669 pages, 18%). Some DOIs now
resolve to `onlinelibrary.wiley.com`, others still to `www.hindawi.com`, which
blocks. The split must be measured across a larger sample before Stage 1 runs.

### 3.4 Blocks are intermittent, not deterministic

Every partially-blocked publisher succeeded on some samples and failed on
others with identical code and headers. So retry-with-backoff genuinely
recovers pages — but the frontier must persist attempt counts to distinguish
"transient" from "permanently blocked".

## 4. Projections

| | |
|---|---|
| MHTML per page (min/median/max) | 0.55 / **2.35** / 4.29 MB |
| Raw storage, 132,781 pages | **≈ 310 GB** |
| Gzipped (~25%) | **≈ 80 GB** |
| Median page load | 3.2 s |
| One worker, 5 s politeness delay | ≈ 14 days |
| 16 parallel workers | ≈ 1 day (excl. retries) |

Free space: E: 506 GB, F: 771 GB — sufficient, but only if MHTML is stored
gzipped. Uncompressed at the 90th percentile it does not comfortably fit.

## 5. Open items

- [ ] Apply for Altmetric and PlumX API keys **now** — Stage 2 blocks on them.
      Free fallbacks to write into the proposal: Crossref Event Data (free,
      DOI-indexed, covers news/blogs/Wikipedia/social), OpenAlex, Semantic Scholar.
- [ ] Larger Hindawi sample to size the wiley/hindawi split.
- [ ] Decide IOP mitigation vs. documented exclusion.
- [ ] Confirm mid-semester break dates on Canvas — affects the week→date mapping.
