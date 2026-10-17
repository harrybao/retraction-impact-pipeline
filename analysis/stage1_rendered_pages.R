# Captured-page routes, sizes, extracted lengths and recorded HTTP statuses.
#
#   Rscript analysis/stage1_rendered_pages.R
#
# The client's recurring question is how many collected pages are the
# publisher's own page, completely rendered, judged by sizes, contents and
# status codes. This answers it in those terms: what each
# collected document is, how big the MHTML files are, and what the pages
# contain as extraction fields, without inferring completeness, paywalls or
# notice replacement from a length threshold. Written to the report CSV.
#
# What each document is comes from page_kind() in R/stage1_pages.R, which
# carries the evidence for each label. Until 13 October the 3,970 HTML
# documents other than the Wayback copies were all reported here as rendered.
# 1,035 were: the crawler's WebKit fallback rendered them, and WebKit cannot
# save MHTML. The other 2,935 were not rendered by a browser: 2,385 fetched
# with curl on the University network, 327 by the tail routes' plain requests,
# and 223 Cureus pages by collect_plain_http.py.
#
# The last four sections are the status codes the client asked for: the last
# HTTP status of every page in the queue and of the pages not collected, and,
# for the two statuses that do not mean what they seem to (a 200 on a page we
# do not have, and "no response" on pages mostly never asked), why those
# pages were not collected. Until 13 October the status codes were only in
# the Python data sheet (scripts/client_data_sheet.py). It lists four of the
# 21 values - 200, 403, 404 and no response, 109,085 pages - under the source
# "crawler log"; the other 803 are here (202: 522, 420: 232, 15 rarer codes:
# 49), and the statuses are every route's, not the crawler's alone (see
# R/stage1_pages.R). The four counts it lists are the same here.
#
# Each input gets its own name. The queue was once called q, and the size loop
# below reused q for the percentiles, so the status section read
# q$http_status from a number and the script stopped ("$ operator is invalid
# for atomic vectors"; audit of 13 October). tests/r/test_stage1_pages.R runs
# the whole script on a small fixture so that cannot come back.

source("R/stage1_pages.R")
rd <- function(f) withCallingHandlers(
  utils::read.csv(f, colClasses = "character", fileEncoding = "UTF-8-BOM"),
  warning = function(w) stop("cannot safely read ", f, ": ", conditionMessage(w), call. = FALSE))
unique_dois <- function(x, name) {
  if (!"doi" %in% names(x)) stop(name, " has no DOI column", call. = FALSE)
  d <- tolower(trimws(x$doi))
  if (anyNA(d) || any(!nzchar(d)) || anyDuplicated(d)) {
    stop(name, " must contain one nonempty row per distinct DOI", call. = FALSE)
  }
  d
}
lengths_of <- function(x, name) {
  n <- suppressWarnings(as.numeric(x))
  present <- !is.na(x) & nzchar(trimws(x)) & x != "NA"
  if (any(present & is.na(n)) || any(!is.na(n) & (!is.finite(n) | n < 0))) {
    stop("invalid character lengths in ", name, call. = FALSE)
  }
  n
}
s2 <- rd("data/clean/stage2_index.csv")
ai <- rd("data/clean/archive_index.csv")
queue <- rd("data/clean/crawl_by_doi.csv")   # one row per page in the queue
queue_dois <- unique_dois(queue, "crawl_by_doi.csv")
collected_dois <- unique_dois(s2, "stage2_index.csv")
if (!all(collected_dois %in% queue_dois)) stop("a collected DOI is absent from the queue", call. = FALSE)
QUEUE <- nrow(queue)                         # 109,888 in the shipped data
out <- list()
add <- function(section, measure, n, of = NA) {
  out[[length(out) + 1L]] <<- data.frame(section = section, measure = measure, n = n,
                                         share_pct = if (is.na(of)) NA else round(100 * n / of, 1))
}

s2$kind <- page_kind(s2$capture_format, s2$source_kind, s2$host)
sec <- sprintf("what the %s collected documents are", format(nrow(s2), big.mark = ","))
kinds <- c(intersect(PAGE_KIND, s2$kind), sort(setdiff(s2$kind, PAGE_KIND)))
for (k in kinds) add(sec, k, sum(s2$kind == k), QUEUE)
add(sec, "not collected", QUEUE - nrow(s2), QUEUE)

# Sizes of the MHTML files kept, and of those rejected. gz_bytes is the stored,
# compressed size.
m <- ai[ai$capture_format == "mhtml" & !is.na(ai$gz_bytes) & nzchar(ai$gz_bytes), ]
m$kb <- as.numeric(m$gz_bytes) / 1024
bins <- c(0, 50, 200, 500, 1024, 5120, Inf)
lab <- c("under 50 KiB", "50-200 KiB", "200-500 KiB", "500 KiB-1 MiB", "1-5 MiB", "5 MiB and above")
# Rejected here means not usable after the content check: 175 "suspect", 9
# "error-page" and 4 pages recorded as failed or blocked (archive_index.csv,
# 13 October). The 10,380 archives with verdict "not-in-database" are left
# out: they are earlier captures that verification rejected and that no row
# of the documents table points to any more (scripts/export_archive_index.py
# writes them as "capture rejected at verification"; 10,106 of them are
# 10.1016, project log 2026-09-16). Every page is counted here through the
# capture its row points to, so these would count some pages a second time:
# the 45 whose file names its DOI are all pages already collected.
# Kept MHTML is every route's: the 73,468 include the 393 open-access mirror
# copies, which the first section counts as copies, not as the 73,075
# publisher pages.
m <- m[m$verdict != "not-in-database", ]
for (u in c("1", "0")) {
  s <- m[m$usable == u, ]
  sec <- if (u == "1") "MHTML size, pages kept (compressed)" else "MHTML size, pages rejected by the content check (compressed)"
  tb <- table(cut(s$kb, bins, lab, right = FALSE))
  for (b in lab) add(sec, b, as.integer(tb[b]), nrow(s))
  pct <- stats::quantile(s$kb, c(.1, .5, .9))
  add(sec, sprintf("10th / median / 90th percentile KiB: %.0f / %.0f / %.0f", pct[1], pct[2], pct[3]), nrow(s))
}

# What the kept pages contain.
art <- s2[s2$doc_role %in% c("original", "both") & s2$kind == PAGE_KIND[["rendered_mhtml"]], ]
# Not named body and abs: those are base R functions, and this script has
# already been broken once by a name meaning two things.
body_chars <- lengths_of(art$body_chars, "body_chars")
abstract_chars <- lengths_of(art$abstract_chars, "abstract_chars")
sec <- "content of article pages kept as MHTML"
long <- !is.na(body_chars) & body_chars >= 5000
short <- !is.na(body_chars) & body_chars < 5000
with_abstract <- short & !is.na(abstract_chars) & abstract_chars > 0
without_abstract <- short & !is.na(abstract_chars) & abstract_chars == 0
unknown <- !(long | with_abstract | without_abstract)
add(sec, "extracted body 5,000+ characters (length alone does not prove completeness)", sum(long), nrow(art))
add(sec, "body under 5,000 characters with extracted abstract (does not prove a paywall)", sum(with_abstract), nrow(art))
add(sec, "body under 5,000 characters and no extracted abstract (page content not determined)", sum(without_abstract), nrow(art))
add(sec, "unknown body or abstract length", sum(unknown), nrow(art))
stopifnot(sum(long, with_abstract, without_abstract, unknown) == nrow(art))

# Notice-field presence is separate evidence, not proof that a notice replaced
# the article; these exports still contain v14's unadjudicated extraction.
if ("notice_chars" %in% names(art)) {
  notice_chars <- lengths_of(art$notice_chars, "notice_chars")
  n <- notice_chars[without_abstract]
  sec <- "extracted notice field among short/no-abstract article pages (not adjudicated)"
  add(sec, "nonempty extracted notice field", sum(!is.na(n) & n > 0), length(n))
  add(sec, "empty extracted notice field", sum(!is.na(n) & n == 0), length(n))
  add(sec, "unknown extracted notice length", sum(is.na(n)), length(n))
}

# The last HTTP status recorded for each page (what "recorded" means per route
# is in R/stage1_pages.R). For the collected pages it is mostly 200.
sec <- sprintf("last HTTP status, all %s pages", format(QUEUE, big.mark = ","))
status_all <- http_status_counts(queue$http_status)
for (i in seq_len(nrow(status_all))) add(sec, status_all$status[i], status_all$n[i], QUEUE)
missed <- queue[!queue_dois %in% collected_dois, ]
# The first section counts the pages not collected as the queue minus the
# collected documents; this one lists them. The two agree only if every
# collected DOI is in the queue once, so check it rather than print two
# different "not collected" numbers in one table.
stopifnot(nrow(missed) == QUEUE - nrow(s2))
sec <- sprintf("last HTTP status, the %s pages not collected", format(nrow(missed), big.mark = ","))
status_missed <- http_status_counts(missed$http_status)
for (i in seq_len(nrow(status_missed))) add(sec, status_missed$status[i], status_missed$n[i], nrow(missed))

# For the pages not collected a 200 is not a success, and "no response" is
# mostly a page never asked for, so those two are broken down by the cause in
# the register of missing pages. In the shipped data (13 October) the 2,200
# that end on 200 are 749 pmc-opted-out (PubMed Central holds the record, the
# publisher forbids the full text), 747 human-verification (a challenge page,
# served with a 200), 359 interstitial, 139 never-asked and 206 other - 178 of
# those failed the identity check, the page was not this paper (error column,
# crawl_by_doi.csv). The 3,871 with no response are 2,946 never-asked, 673
# pmc-opted-out, 214 other and 38 human-verification - pages the circuit
# breaker skipped at validate.perfdrive.com, a bot-management host, which
# export_missing.py files under human-verification for the host's name.
register <- rd("data/clean/not_collected.csv")
register_dois <- unique_dois(register, "not_collected.csv")
if (!all(register_dois %in% queue_dois[!queue_dois %in% collected_dois])) {
  stop("not_collected.csv contains a collected or out-of-queue DOI", call. = FALSE)
}
# New columns, not the queue's own: its status column is done/blocked/failed.
missed$cause <- not_collected_cause(missed$doi, register)
missed$http_label <- status_label(missed$http_status)
for (bucket in c("HTTP 200", "no response")) {
  in_bucket <- missed[missed$http_label == bucket, ]
  if (!nrow(in_bucket)) next
  sec <- sprintf("pages not collected: the %s with %s, by cause (not_collected.csv)",
                 format(nrow(in_bucket), big.mark = ","), bucket)
  causes <- count_desc(in_bucket$cause)
  for (i in seq_len(nrow(causes))) add(sec, causes$value[i], causes$n[i], nrow(in_bucket))
}

res <- do.call(rbind, out)
print(res, row.names = FALSE)
report_dir <- Sys.getenv("CS43_REPORTS", file.path("data", "reports"))
if (!nzchar(trimws(report_dir))) stop("CS43_REPORTS must not be blank", call. = FALSE)
dir.create(report_dir, showWarnings = FALSE, recursive = TRUE)
report <- file.path(report_dir, "stage1-rendered-pages.csv")
utils::write.csv(res, report, row.names = FALSE, fileEncoding = "UTF-8")
cat("written:", report, "\n")
