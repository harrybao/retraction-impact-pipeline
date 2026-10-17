# What each collected Stage 1 document is, in the client's terms.
#
# The client's recurring question was whether we hold the publisher's
# page as it renders in a browser - the exact MHTML of the publisher's page.
# The answer differs by how a document was
# obtained, and stage2_index.csv records that per document in three columns:
# capture_format (what the file is), source_kind (which route produced it;
# empty for the crawler) and host (where the page came from).
#
# Until 13 October data/reports/stage1-rendered-pages.csv called all 3,970
# HTML documents other than the 35 Wayback copies "publisher page, rendered".
# They are four different things. Counted on 13 October in stage2_index.csv as
# shipped; the dates are fetched_at in the documents table, read-only:
#
#   1,035  crawler, html          The crawler's WebKit fallback (scripts/crawl.py,
#          www.mdpi.com 1,031,    WEBKIT_HOSTS). A browser rendered them, but
#          sciforum.net 4         MHTML is saved through the Chrome DevTools
#                                 Protocol, which WebKit does not have, so
#                                 src/rwpipe/fetch.py keeps WebKit's rendered
#                                 page.content() as "html" (project log
#                                 2026-08-28, "WebKit reaches publishers that
#                                 refuse Chromium"). The four sciforum.net
#                                 pages are MDPI DOIs (10.3390) fetched on
#                                 28 August between 10:32 and 10:43, like
#                                 1,027 of the 1,031 MDPI pages (4 on the
#                                 29th) - before 1 September, the date of
#                                 every page collect_plain_http.py (below)
#                                 saved.
#     223  crawler, html          Not the crawler and not a browser:
#          www.cureus.com         scripts/collect_plain_http.py fetched them
#                                 with httpx, all 223 on 1 September (project log
#                                 2026-09-01 evening, "223 were collected
#                                 before it began challenging"). That script
#                                 sets no source_kind, so they look like
#                                 crawler pages. The only other writers of an
#                                 HTML capture without one are crawl.py's
#                                 WebKit path and scripts/merge_shard.py,
#                                 which copies a teammate's crawler rows
#                                 (capture_format is in its FIELDS,
#                                 source_kind is not).
#   2,385  campus-network, html   Saved on the University network by
#                                 COLLECT-CAMPUS.command, a bash script kept
#                                 outside this repository (the copy returned
#                                 with the files is in "CAMPUS-KIT 2/
#                                 CAMPUS-KIT"). It fetches each page with one
#                                 call, curl -sS -L -A <UA> -o <file>, and
#                                 keeps the body the server returned after
#                                 redirects; nothing in the script starts a
#                                 browser (project log 2026-09-11 evening: "which
#                                 fetches with curl"). scripts/merge_campus.py
#                                 stored those files as capture_format
#                                 'html' (data/reports/campus-merge.csv,
#                                 2,385 "merged"). curl does not run a page's
#                                 scripts or fetch its images, so these are
#                                 the server's HTML, not a rendered page; for
#                                 these five publishers the article text is
#                                 in that HTML (merge_campus.py).
#     327  tail-plain-http (283), scripts/collect_remainder.py, with httpx:
#          tail-crossref-url (44) one GET of the page each, of
#                                 https://doi.org/<doi> (plain-http) or of the
#                                 address the publisher registered with
#                                 Crossref, looked up first through the
#                                 Crossref API (crossref-url). No browser
#                                 either.
#
# So 1,035 of the 3,970 were rendered by a browser and 2,935 were not. The
# split needs the host as well as the route, because the Cureus pages carry
# the crawler's empty source_kind.
#
# A combination not listed here is reported as itself ("other: ...") rather
# than folded into the nearest label - which is how the 2,935 unrendered pages
# came to be called rendered.

PAGE_KIND <- c(
  rendered_mhtml = "browser-created MHTML, crawler or teammate route (publisher origin and completeness not established)",
  webkit_html    = "browser-rendered HTML, crawler's WebKit route (not MHTML; origin and completeness not established)",
  campus_html    = "HTML from the University-network route (curl, not browser-rendered)",
  direct_html    = "HTML from a plain-request route (not browser-rendered)",
  text           = "publisher/PMC text supplied as XML or PDF, not a page",
  copy           = "explicitly tagged open-access mirror or Internet Archive copy (not an exhaustive archive-host census)"
)

# The routes whose MHTML a browser wrote: our crawler (Chromium, through the
# DevTools Protocol, src/rwpipe/fetch.py), and the SAGE and Taylor & Francis
# pages two teammates handed over as MHTML files. Those are Blink's own
# archives - merge_sage.py checks the Snapshot-Content-Location header Blink
# writes, merge_tandf.py undoes a line-ending change to Chrome's MHTML - so a
# Chromium-based browser saved them; how the teammates drove it is not
# recorded (merge_tandf.py says so).
RENDERED_MHTML_SOURCES <- c("crawler", "teammate-sage", "teammate-tandf")

# Where the crawler's WebKit fallback ran: scripts/crawl.py WEBKIT_HOSTS, plus
# sciforum.net, where four MDPI DOIs landed (see above).
WEBKIT_HOSTS <- c("www.mdpi.com", "cell.com", "www.cell.com", "sciforum.net")

# Hosts whose crawler-looking HTML came from scripts/collect_plain_http.py.
# Only Cureus in the shipped data; any other host is reported as "other: ...".
PLAIN_HTTP_HOSTS <- c("www.cureus.com")

page_kind <- function(capture_format, source_kind, host) {
  stopifnot(length(source_kind) == length(capture_format),
            length(host) == length(capture_format))
  fmt <- ifelse(is.na(capture_format), "", capture_format)
  sk <- ifelse(is.na(source_kind) | !nzchar(source_kind), "crawler", source_kind)
  h <- ifelse(is.na(host), "", tolower(host))
  crawler_html <- fmt == "html" & sk == "crawler"
  out <- rep(NA_character_, length(fmt))
  # label is one string, or one per document (the "other: ..." fallback).
  set <- function(cond, label) {
    i <- is.na(out) & cond
    out[i] <<- rep_len(label, length(out))[i]
  }
  set(sk %in% c("oa-mirror", "wayback"), PAGE_KIND[["copy"]])
  set(fmt %in% c("elsxml", "jats"), PAGE_KIND[["text"]])
  set(fmt == "mhtml" & sk %in% RENDERED_MHTML_SOURCES, PAGE_KIND[["rendered_mhtml"]])
  set(crawler_html & h %in% WEBKIT_HOSTS, PAGE_KIND[["webkit_html"]])
  set(crawler_html & h %in% PLAIN_HTTP_HOSTS, PAGE_KIND[["direct_html"]])
  set(fmt == "html" & sk == "campus-network", PAGE_KIND[["campus_html"]])
  set(fmt == "html" & sk %in% c("tail-plain-http", "tail-crossref-url"), PAGE_KIND[["direct_html"]])
  # Crawler HTML from an unlisted host names the host: it is the one case
  # where the route alone does not say what the page is.
  other <- sprintf("other: %s from %s", ifelse(nzchar(fmt), fmt, "no format"), sk)
  other <- ifelse(crawler_html, sprintf("%s at %s", other, ifelse(nzchar(h), h, "no host")), other)
  set(rep(TRUE, length(out)), other)
  out
}

# The last HTTP status recorded for each page in the queue (crawl_by_doi.csv,
# an export of the documents table): "HTTP <code>", or "no response" where
# none is recorded - the label scripts/client_data_sheet.py gives the same
# 3,924 pages.
#
# Recorded is not the same as what the last attempt got. The crawler writes
# the status of every request it makes (scripts/crawl.py). The other routes
# write one only when they save the page - 200 in merge_campus.py,
# merge_sage.py, merge_tandf.py, collect_elsevier.py, ingest_iop.py and
# collect_remainder.py, their own response's in collect_pmc.py, collect_oa.py
# and collect_plain_http.py - and when they fail they write their reason to
# the error column and leave the status as it was. So a page's status and its
# error can come from different attempts: 749 pages not collected end on 200
# with the error "pmc-no-fulltext: publisher opted out", which collect_pmc.py
# wrote after an earlier attempt's 200. merge_wayback.py writes no status, so
# its 35 pages keep the crawler's earlier 403.
#
# And "no response" is mostly not a request that went unanswered. Of the
# 3,871 pages not collected without a status, 2,946 have the cause
# "never-asked" in not_collected.csv: the last thing recorded is that their
# host had been written off - by the circuit breaker, or as "archived: host
# under 5% over 200+ attempts" - and no request was made (2,089 of them show
# no attempt at all). Most of the 214 with cause "other" are browser errors
# that got no HTTP answer - timeouts, names that did not resolve, aborted
# loads. Counted 13 October in crawl_by_doi.csv and not_collected.csv; the
# report lists the 200s and the "no response" pages by cause for this reason.
status_label <- function(status) {
  out <- sprintf("HTTP %s", status)   # sprintf, not paste: none in, none out
  out[is.na(status) | !nzchar(status)] <- "no response"
  out
}

# How often each value occurs, largest first. Ties are broken by the value in
# C-locale order (radix), so the report's row order depends on neither the
# input's order nor the machine's language settings.
count_desc <- function(x) {
  stopifnot(!anyNA(x))
  if (!length(x)) return(data.frame(value = character(), n = integer(), stringsAsFactors = FALSE))
  tb <- table(x)
  o <- order(-as.integer(tb), names(tb), method = "radix")
  data.frame(value = names(tb)[o], n = as.integer(tb)[o], stringsAsFactors = FALSE)
}

http_status_counts <- function(status) {
  d <- count_desc(status_label(status))
  names(d)[1] <- "status"
  d
}

# Why a page was not collected, from the project's register of missing pages
# (not_collected.csv, written by scripts/export_missing.py, whose cause_of()
# defines each cause). Matched on the DOI without regard to case, as the
# report matches the queue. A page the register does not have, or has without
# a cause, is counted under its own name rather than dropped: in the shipped
# data the register and the queue's uncollected pages are the same 13,559
# DOIs, with the same status and error on every row (13 October).
NO_CAUSE <- "no cause in not_collected.csv"
not_collected_cause <- function(doi, register) {
  cause <- as.character(register$cause)[match(tolower(doi), tolower(register$doi))]
  cause[is.na(cause) | !nzchar(cause)] <- NO_CAUSE
  cause
}
