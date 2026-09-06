# Smarter ways around publisher blocking

Client feedback on Status Check 1: *"come up with smarter ways to avoid
publisher blocking."*

Measured 2026-09-04 against the live database. Every number below is a query,
not an estimate.

## The reframe

39,459 documents are refused. Six ways of beating the refusal were tested in
Week 4 and five of them did nothing:

| tried | result |
|---|---|
| browser-stealth JS patches | no effect, identical to baseline |
| arrival-pattern warming (home page, cookie banner, session, scrolling) | no effect; on cell.com it *triggered* a challenge a direct request did not |
| open-access direct links | no effect — same host, same refusal |
| Internet Archive snapshots | 2 of 7 |
| a different browser engine (WebKit) | **works** — 8/8 on MDPI where Chromium got 0 of 1,036 |
| official APIs | metadata and abstracts, no notice text |

The refusals arrive as HTTP 403 before a page renders, so they are decided on
the connection, not on browser behaviour. Beating that would mean forging a TLS
fingerprint — bypassing an access control, not avoiding a nuisance — and it
would land on the University's IP range, so it is out.

**The smarter question is not how to beat the block. It is how not to need the
blocked page.**

## What the blocked pile actually contains

```
39,459  refused documents
        19,700  retraction notices
        19,759  original articles

18,133  of them (46%) have a free copy on a host that is not the
        publisher who refused us
         9,631  notices
         9,370  articles
```

And the pages are not evenly costly. Per **record**:

```
27,972  records have neither document — these are what a recovery is worth
 3,199  records have the notice but not the article
 1,564  records have the article but not the notice
```

A blocked page belonging to a record we already hold the notice for is worth
less than one belonging to a record we hold nothing for. Recovery should be
ordered by that, not by page count.

## Where the free copies are

| host | blocked docs it could supply |
|---|---:|
| PubMed Central (`ncbi.nlm.nih.gov` + `pmc.`) | **7,634** |
| doaj.org | 4,146 |
| e3s-conferences.org | 618 |
| downloads.hindawi.com | 363 |
| hdl.handle.net (institutional repositories) | 271 |
| oncotarget.com | 243 |
| ajol.info | 185 |
| bio-conferences.org | 157 |
| europepmc.org | 132 |
| scielo.br | 123 |
| jci.org | 119 |

(`doi.org`, `tandfonline.com`, `sciencedirect.com`, `iopscience.iop.org`
also appear in OpenAlex's location list but are the publisher's own page —
the one we cannot fetch — so they are excluded from the 18,133.)

Some blocked hosts are almost entirely mirrored elsewhere:

| blocked host | pages | with a free copy |
|---|---:|---:|
| jbc.org | 857 | 856 (99.9%) |
| e3s-conferences.org | 623 | 623 (100%) |
| cell.com | 1,679 | 1,627 (97%) |
| IOP (behind `validate.perfdrive.com`) | 2,458 | 2,348 (96%) |
| portlandpress.com | 588 | 563 (96%) |
| tandfonline.com | 2,855 | 1,847 (65%) |

## Seven routes, in order of expected yield

**1. PubMed Central through NCBI E-utilities — ~7,600 documents.**
The sanctioned bulk endpoint (`efetch.fcgi?db=pmc`), not the web page PMC
refuses to render for us. Measured on a sample: 28% return full text, 40%
carry *"The publisher of this article does not allow downloading of the full
text"*, and a large share of the rest are notices, for which two paragraphs is
the complete document. Realistic yield **3,000-3,500**. No negotiation, no
blocking, no permission needed. Returns JATS XML — see *The format question*.

**2. Non-PMC open-access mirrors — several thousand.**
DOAJ, Hindawi's download host, institutional repositories via `hdl.handle.net`,
EuropePMC, SciELO, and journal sites that mirror their own content.
`collect_oa.py` and `probe_oa_hosts.py` are written; one document has been
captured so far, so this is almost entirely untapped. Each host is probed
before being trusted — an earlier inference wrote off 1,203 documents because
`ncbi.nlm.nih.gov` and `pmc.ncbi.nlm.nih.gov` are different strings.

**3. Plain HTTP where the block is a browser fingerprint — 1,281 recovered.**
The archive is captured with Playwright, and bot management fingerprints
precisely that. "Blocked" in this database has often meant *blocked while
driving a browser* — a claim about our client recorded as a claim about the
publisher. cureus.com was 518/518 blocked and returned 10 of 10 articles to a
plain HTTP client with an honest User-Agent. 150 hosts probed, 20 answered,
1,281 documents behind them.

**4. A different browser engine — 1,036 documents on MDPI alone.**
WebKit gets 8/8 where Chromium gets 0. Not yet swept across the other blocked
hosts. Costs one probe per host to find out.

**5. The publishers themselves — 2,594 agreed, more outstanding.**
Twelve approached. IOP agreed to deliver 2,594 articles as files over SFTP.
ASM treated the block as a fault and escalated it to their platform vendor and
allowlisted our IP (measured: 3 of 39 requests now succeed — partly effective,
not usable). SAGE agreed and then required a Library letter the Library
declined to write; AIP and Taylor & Francis required the same. ACS and ACM are
closed. OUP and RSC have not replied.

**6. The campus network — 2,120 articles collected, not yet merged.**
Some publishers answer a University IP that refuse a residential one. Measured
and used within the sanctioned kit; not a general-purpose route, and the campus
egress is a shared NAT address, which is why we have declined every offer to
allowlist it.

**7. The Internet Archive — 35 documents.**
2 of 7 on the first test. Cheap, small, already run.

## What we will not do

Forging a TLS fingerprint, solving challenges, rotating residential proxies, or
running systematic collection through the University VPN. The first three
bypass an access control rather than avoid an inconvenience; all four put the
University's address range at risk of a block that would affect everyone behind
it, not this project. This position was taken in Week 4 and has not changed.

## The format question this forces

Routes 1 and 5 — the two largest — deliver **JATS XML**, not MHTML. The client's
written feedback is *"you need to get the exact mhtmls from publisher's page."*

Today that requirement is met: 68,923 of 70,216 verified captures are MHTML,
and 70,180 come from the publisher's own page. But for the pages that are
refused, the publisher's own MHTML is the one thing that cannot be obtained —
it is the format the block exists to prevent.

For Stages 2 and 3, XML is *better* than a rendered page: title, abstract,
sections and references arrive already separated, and the extractor — six
versions of which were needed to find a retraction notice inside publisher HTML
— is not needed at all.

**One question for the client:** for articles the publishers refuse to serve to
a browser, would you rather have the publisher's own structured full text (XML,
supplied through their sanctioned channel) or nothing? Those are the two
options; the publisher's page as MHTML is not among them.

## What else exists, and what it is worth (measured 2026-09-04)

The client asked for smarter methods, so the obvious next question is whether
somebody has already built one. Two things were checked: what is on GitHub, and
what the big aggregators actually return for *our* hardest documents.

### GitHub

Most "download any paper" tools resolve through **Sci-Hub or LibGen** —
`PyPaperBot`, `SoPaper` and most of the `sci-hub` topic. They redistribute
copyrighted work without licence. Using one would make the dataset unpublishable
in a capstone report, so they are out, and no amount of convenience changes that.

What is left is narrower than it looks:

| project | what it actually does | use to us |
|---|---|---|
| `jannisborn/paperscraper` | arXiv, PubMed, bioRxiv, medRxiv, chemRxiv | preprint servers only — our corpus is published journals |
| `andrenarchy/libfulltext` | bulk-downloads open-access papers by DOI | the same idea as our OA route, no new source |
| `internetarchive/fatcat` | the catalogue behind scholar.archive.org, 25M+ preserved papers | promising, but see below |

Nothing on GitHub knows a way past a publisher block that we do not. The tools
that appear to are the ones using Sci-Hub.

### The aggregators, asked about our own documents

120 documents drawn from the hardest end - refused, no PMC record, no
open-access location OpenAlex knows of - asked of four keyless services:

| service | result |
|---|---|
| **Semantic Scholar** | 9% report an open-access PDF. But **9 of the 11 URLs point at `doi.org`**, which resolves straight back to the publisher page we cannot fetch. Real yield **2 of 120 (1.7%)** |
| **Europe PMC** | 60% not indexed, 20% metadata only, **0 full text** in the sample |
| **OpenAIRE** | indexes essentially all of them, and returns metadata, not full text |
| **fatcat / Internet Archive** | `api.fatcat.wiki` did not answer at all (connect timeout), though `scholar.archive.org` itself is up |

The Semantic Scholar result is the same trap as OpenAlex's `best_oa_location`:
a field named "open access PDF" that contains the publisher's own landing page.
A route has to be checked by fetching, not by counting rows that have a URL in
them.

### The one candidate left untested

**CORE** (`api.core.ac.uk/v3`) aggregates 300M+ records and **40M+ full texts**
harvested from institutional repositories - which is a different population
from PMC and from OpenAlex's location list, and the population most likely to
hold a copy of a paper whose publisher refuses us. It needs a free API key,
issued instantly for academic use; the free tier allows five lookups per ten
seconds, which is enough for 11,506 documents in about six hours.

It is the only remaining lead with a plausible claim to hold what nobody else
does, and it costs one registration to find out.

### The honest conclusion

The 11,506 documents with `route = none-known` are close to a real limit, and
it is not a technical one. SAGE (1,696), Taylor & Francis (1,069) and ACM (430)
are not unreachable - they were negotiated, and the negotiation failed at the
institutional step. RSC (570) and ACS (558) refused outright. That is 4,323
documents, more than a third of the remainder, blocked by licensing rather than
by anything a crawler could solve.

## Two routes that were claimed but never run, now measured (2026-09-05)

### Alternate publisher HTML paths

`experiment_engine.py` defines `ALT_PATHS` at line 40 - six publishers' URL
templates - and never references it again. Every navigation in that file goes
to `https://doi.org/{doi}`. So "alternate paths tested - no effect" has been in
the status report on the strength of code that does not execute. That is worse
than an untested idea, because it closed the question.

`probe_alt_paths.py` runs it properly, taking the address from the publisher
rather than a template: Crossref's `resource.primary.URL` and the registered
`link[]` entries whose `content-type` is `text/html`. PDF and XML links are
excluded - packaging one as MHTML would be dressing up a different artefact -
and doi.org, dx.doi.org and linkinghub are excluded as candidates because they
are redirect stops, not destinations.

| sample | attempts | articles |
|---|---:|---:|
| 12 hosts, 2 each | 24 | 2 |
| www.tandfonline.com | 20 | 1 |
| dl.acm.org | 20 | 0 |
| **total** | **64** | **3 (4.7%)** |

Both of the first two successes looked promising - one on Taylor & Francis,
which is otherwise 100% refused, and one on ACM, a 920 KB article page. Neither
survived a bigger sample: they were intermittent passes, not a working path.
The route is real but marginal, and it is not what closes the gap.

Worth keeping for one reason: `resource.primary.URL` skips `linkinghub` and the
other JS interstitials, and 6 of the 24 in the first sample had **no registered
HTML address at all** - Cell Press and JBC register only PDFs. That is a fact
about those publishers worth recording rather than re-discovering.

### Internet Archive via CDX

`collect_wayback.py` asks `archive.org/wayback/available` for the closest
snapshot of one URL string. 4,772 documents asked, 4,727 came back
`no-snapshot`, 35 were kept, 10 were the wrong article: 0.73%. The obvious
diagnosis is the single query - CDX would allow http/https and www/non-www
variants, `statuscode:200`, `mimetype:text/html` and `collapse=digest`.

Tested before rewriting anything, one DOI per host across the ten hosts with
the most `no-snapshot` rows:

| host | usable snapshots in CDX |
|---|---:|
| pmc.ncbi.nlm.nih.gov | 2 |
| tandfonline · sagepub · acs · acm · oup · sciencedirect · e3s · rsc · lww | **0** |

The premise does not hold. The bot protection that refuses us also refuses the
Internet Archive's crawler, so there is nothing in CDX for a better query to
find - and the one host that does have snapshots is PubMed Central, which
`collect_pmc.py` already covers through NCBI's own endpoint. The single-snapshot
query was not the limiting factor.

### A route not taken, and why

An extension driving a normal Chrome profile through a logged-in institutional
session would not forge a fingerprint or bypass an access control - the session
is real and the subscription is the University's. But fetching thousands of
articles through it is *systematic downloading under a subscription licence*,
which is the activity publisher terms prohibit and the reason these blocks
exist at all. SAGE, Taylor & Francis and AIP each asked for institutional
authorisation for exactly this, and the Library declined to provide it on
2026-09-04. A consequence would land on the University's account, not on this
project's.

Saving a page you are reading is a save button and is fine. A queue driving
that button is the thing that was declined. It is a decision for the client and
the University, not one to take quietly inside a script.

## Independent web archives, and a host-level number that misled me (2026-09-05)

Common Crawl and Arquivo.pt had never been tried. Both are free, need no
account, and are independent of the Internet Archive, so they are a genuinely
different population rather than another way of asking the same question.

**Arquivo.pt**: one DOI per host across the ten hosts with the most missing
documents. One host had snapshots - e3s-conferences.org, 4 - and that host is
already 100% covered by open-access mirrors. Nine had none.

**Common Crawl** looked much better, and then did not survive the next
question. Asked per host, it holds article-shaped URLs for three publishers we
cannot reach:

| host | first 200 indexed URLs | article-shaped |
|---|---:|---:|
| academic.oup.com | 200 | 191 |
| pubs.rsc.org | 200 | 179 |
| pubs.acs.org | 200 | 164 |
| www.nature.com (control, not blocked) | 200 | 192 |
| tandfonline · sagepub · cell.com · dl.acm.org | 9-200 | **0** |

That reads like 2,763 documents behind an open, public archive. It is not.
Asked about **our** DOIs - the question that matters - Common Crawl returned
nothing for 18 of 18 across those same three hosts, in three URL forms each,
and the index holds two blocks for the whole of pubs.acs.org against three for
nature.com. Two hundred article URLs out of a very small slice of a publisher
with millions of pages is not coverage; it is the first page of a short list.

The per-host statistic and the per-document question have different answers,
and only the second one can be acted on. This is the same shape as "8,682
reachable, from one request per host" and as a citation flag that ignores its
own window, and it is worth recording that it caught me again here rather than
quietly correcting the number.

What both archives do confirm: for tandfonline, sagepub, cell.com and ACM the
index holds robots.txt and a homepage and nothing else. The crawlers were
refused exactly as we are. There is no archive route because there is nothing
in the archives.

## WebKit captures and the MHTML question

1,031 MDPI documents plus 262 others are stored as rendered HTML rather than
MHTML, because MHTML capture runs through the Chrome DevTools Protocol and
WebKit has none.

"WebKit cannot export MHTML" is true; "therefore MHTML is impossible" is not -
MHTML is MIME packaging (RFC 2557) and a capture plus its subresources can be
assembled into one. Two things follow, and they point in opposite directions:

* it would not add a single document. All 1,293 are already `usable = 1` and
  already through Stage 2 - 794 full, 499 notice-only. Repackaging changes the
  container, not the corpus.
* the subresources were never captured. Only the rendered HTML was stored, so
  producing a faithful MHTML means re-fetching images and stylesheets from the
  publisher, which is more requests to hosts we are negotiating with, in order
  to change a file extension.

So this is a presentation question about the client's "exact MHTML" requirement,
not a data question, and it should be settled by asking him rather than by
building a converter. Renaming the files without the resources would be the one
genuinely dishonest option: an MHTML that does not contain what MHTML promises.

## Headed Chrome: a real route, and the reason it is not being used (2026-09-05)

The proposal was that only Playwright's own browsers had ever been tested. That
was worth checking, and checking it produced the largest technical finding of
the week - and then a second measurement that changed what it means.

**Test 1, one DOI per publisher, three clients:**

| host | bundled Chromium headless | real Chrome headless | real Chrome, window on screen |
|---|---|---|---|
| journals.sagepub.com | http 403 | http 403 | **ARTICLE 450,837 B** |
| www.tandfonline.com | http 403 | http 403 | **ARTICLE 352,969 B** |
| pubs.acs.org | http 403 | http 403 | **ARTICLE 1,029,931 B** |
| dl.acm.org | http 403 | http 403 | **ARTICLE 919,520 B** |
| pubs.rsc.org | http 403 | http 403 | timeout |

The variable is not the browser binary - real Chrome headless is refused
exactly like the bundled one. It is whether the browser has a window. And these
are four of the five publishers written off as `none-known`.

**Test 2, eight DOIs per publisher, same headed Chrome:**

    journals.sagepub.com   1/8      pubs.rsc.org   1/8
    www.tandfonline.com    1/8      dl.acm.org     1/8
    pubs.acs.org           1/8

Exactly the first request per host succeeds. Test 1 had one DOI per host, so
all five were first requests - the sample structure produced the result, which
is the third time that has happened in this project and the reason the second
test exists.

**Test 3, four requests per host, 30 seconds apart, one session.** If the gate
were a rate limit, slowing down would obey it, and SAGE publishes the number
they want (1 request per 6 seconds on weekday mornings US Pacific, 1 per 2
seconds otherwise). Result: request 1 returns the article, requests 2, 3 and 4
return HTTP 403 at any spacing.

So it is a **session gate, not a rate gate**. One document per session,
irrespective of how politely the session behaves.

### What comes back is real

Not abstract stubs: 287-910 KB, 13-49 paragraphs, 3,700-9,200 characters of
article text, each carrying its own DOI. The content is there and it is
readable by an ordinary visitor with no login.

### Why this is not being turned into a collector

The only way to use a one-document-per-session gate at scale is to reset the
session for every document. That is not a slow crawl or a polite one - it is
deliberately defeating the control the publisher put there, and it would look
from their side exactly like the evasion this project has refused all along.
Rotating session identity is the same category as rotating an IP; the fact that
it is achieved with a real browser rather than a forged fingerprint does not
change what it is for.

So this changes what we can say, not what we are doing. **The 4,323 documents
behind SAGE, Taylor & Francis, ACM, RSC and ACS are not technically
unreachable.** They are reachable by a method that requires defeating a session
control, and the project has declined to use it without the client and the
University deciding that it should. That is a stronger and more honest sentence
for the report than "we could not get them", and it is a real question to put
to the client rather than a limitation to write around.

The legitimate versions of the same access remain what they always were: the
publisher supplying files against a DOI list, as IOP agreed to; or an
institutional TDM agreement, which three of these five publishers offered and
the Library declined to sign on 2026-09-04.
