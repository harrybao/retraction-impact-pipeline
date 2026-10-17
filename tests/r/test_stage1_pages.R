# Checks for the Stage 1 report. Run from the project root:
#   Rscript tests/r/test_stage1_pages.R
#
# Three things went wrong here before 13 October, and each has a check below:
#   * every HTML document but the Wayback copies was called "rendered", and
#     2,935 of the 3,970 were not (R/stage1_pages.R has the evidence);
#   * among them, 223 Cureus pages fetched with httpx carry the crawler's
#     empty source_kind, so the route alone cannot tell them from WebKit;
#   * analysis/stage1_rendered_pages.R reused the queue's name (q) for the
#     size percentiles, and stopped at the status section. The last part runs
#     the whole script on a small fixture, so any reuse of an input's name
#     fails here rather than in reproduce_results().
# And one thing the status table did not say: a 200 on a page not collected
# is not a success, and "no response" is mostly a page never asked for, so
# the report breaks both down by the cause in not_collected.csv.

source("R/stage1_pages.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
K <- PAGE_KIND

# ---- page_kind(): every route, format and host the shipped data has ------
cases <- read.csv(text = '
fmt,sk,host,want
mhtml,,www.wiley.com,rendered_mhtml
mhtml,NA,www.wiley.com,rendered_mhtml
mhtml,teammate-sage,journals.sagepub.com,rendered_mhtml
mhtml,teammate-tandf,www.tandfonline.com,rendered_mhtml
mhtml,oa-mirror,europepmc.org,copy
html,oa-mirror,europepmc.org,copy
html,wayback,web.archive.org,copy
mhtml,wayback,web.archive.org,copy
elsxml,elsevier-api,api.elsevier.com,text
jats,pmc-efetch,pmc.ncbi.nlm.nih.gov,text
jats,iop-sftp,iopscience.iop.org,text
html,,www.mdpi.com,webkit_html
html,,WWW.MDPI.COM,webkit_html
html,,sciforum.net,webkit_html
html,,cell.com,webkit_html
html,,www.cell.com,webkit_html
html,NA,www.mdpi.com,webkit_html
html,,www.cureus.com,direct_html
html,campus-network,academic.oup.com,campus_html
html,campus-network,karger.com,campus_html
html,campus-network,,campus_html
html,tail-plain-http,doi.org,direct_html
html,tail-crossref-url,www.scielo.br,direct_html
mhtml,,,rendered_mhtml
mhtml,,NA,rendered_mhtml
mhtml,teammate-sage,NA,rendered_mhtml
html,wayback,NA,copy
,oa-mirror,europepmc.org,copy
NA,wayback,web.archive.org,copy
elsxml,,api.elsevier.com,text
jats,NA,,text
html,campus-network,NA,campus_html
html,tail-plain-http,,direct_html
html,NA,www.cureus.com,direct_html
html,,Www.Cureus.Com,direct_html
html,,SCIFORUM.NET,webkit_html
', colClasses = "character", na.strings = "NA", strip.white = TRUE)
got <- page_kind(cases$fmt, cases$sk, cases$host)
for (i in seq_len(nrow(cases))) {
  ok(sprintf("page_kind(%s, %s, %s) is %s", cases$fmt[i],
             ifelse(is.na(cases$sk[i]), "NA", dQuote(cases$sk[i], FALSE)), cases$host[i], cases$want[i]),
     identical(got[i], K[[cases$want[i]]]))
}
ok("every label in PAGE_KIND is reachable", setequal(unique(got), unname(K)))

# The two hosts whose HTML carries the crawler's empty source_kind are not the
# same thing: MDPI was rendered by WebKit, Cureus was a plain request.
rendered <- c(K[["rendered_mhtml"]], K[["webkit_html"]])
ok("Cureus HTML is not called rendered",
   !page_kind("html", "", "www.cureus.com") %in% rendered)
ok("campus HTML is not called rendered",
   !page_kind("html", "campus-network", "academic.oup.com") %in% rendered)
ok("tail HTML is not called rendered",
   !any(page_kind(c("html", "html"), c("tail-plain-http", "tail-crossref-url"), c("doi.org", "x")) %in% rendered))

# The fallback label is per document: a batch mixing unlisted rows with
# listed ones must give each unlisted row its own name, in its own place.
mixed <- page_kind(c("html", "mhtml", "pdf", "html"), c("", "", "", ""),
                   c("a.example.org", "www.wiley.com", "b.example.org", "www.mdpi.com"))
ok("unlisted rows in a mixed batch each keep their own label",
   identical(mixed, c("other: html from crawler at a.example.org", K[["rendered_mhtml"]],
                      "other: pdf from crawler", K[["webkit_html"]])))

# Anything else is reported as itself, never folded into a label.
other <- read.csv(text = '
fmt,sk,host,want
html,,www.example.org,other: html from crawler at www.example.org
html,,,other: html from crawler at no host
html,,NA,other: html from crawler at no host
html,alt-path,www.example.org,other: html from alt-path
mhtml,campus-network,academic.oup.com,other: mhtml from campus-network
mhtml,tail-plain-http,doi.org,other: mhtml from tail-plain-http
pdf,,www.example.org,other: pdf from crawler
NA,,www.example.org,other: no format from crawler
,NA,www.example.org,other: no format from crawler
NA,NA,NA,other: no format from crawler
,,,other: no format from crawler
xml,elsevier-api,api.elsevier.com,other: xml from elsevier-api
,campus-network,academic.oup.com,other: no format from campus-network
NA,tail-crossref-url,www.scielo.br,other: no format from tail-crossref-url
html,teammate-sage,journals.sagepub.com,other: html from teammate-sage
html,pmc-efetch,pmc.ncbi.nlm.nih.gov,other: html from pmc-efetch
mhtml,alt-path,www.example.org,other: mhtml from alt-path
HTML,,www.mdpi.com,other: HTML from crawler
', colClasses = "character", na.strings = "NA", strip.white = TRUE)
got <- page_kind(other$fmt, other$sk, other$host)
for (i in seq_len(nrow(other))) {
  ok(sprintf("unlisted combination reported as %s", dQuote(other$want[i], FALSE)),
     identical(got[i], other$want[i]))
}
# The host decides only for crawler HTML: on any other route it is ignored.
ok("the host does not change a non-crawler label",
   identical(page_kind(rep("html", 3), rep("campus-network", 3), c("www.mdpi.com", "www.cureus.com", "x.org")),
             rep(K[["campus_html"]], 3)) &&
     identical(page_kind(c("mhtml", "mhtml"), c("", ""), c("www.mdpi.com", "www.cureus.com")),
               rep(K[["rendered_mhtml"]], 2)))
ok("page_kind() of nothing is nothing", identical(page_kind(character(), character(), character()), character()))
ok("page_kind() refuses columns of different lengths",
   inherits(tryCatch(page_kind(c("html", "mhtml"), c("", ""), "www.mdpi.com"), error = identity), "error"))

# ---- http_status_counts() -------------------------------------------------
st <- http_status_counts(c("200", "403", "", NA, "200", "404", "200", "403", "404", "202"))
ok("statuses are counted, largest first, ties in name order",
   identical(st$status, c("HTTP 200", "HTTP 403", "HTTP 404", "no response", "HTTP 202")) &&
     identical(st$n, c(3L, 2L, 2L, 2L, 1L)))
ok("an empty status and NA are both 'no response'",
   identical(http_status_counts(c("", NA))$status, "no response") &&
     identical(http_status_counts(c("", NA))$n, 2L))
ok("numeric statuses work too", identical(http_status_counts(c(200, NA, 200))$status, c("HTTP 200", "no response")))
ok("no statuses give an empty table, not an error",
   nrow(http_status_counts(character())) == 0L && identical(names(http_status_counts(character())), c("status", "n")))
ok("the counts add up to the input", sum(st$n) == 10L)
ok("status_label(): a code, NA and empty",
   identical(status_label(c("403", NA, "", "200")), c("HTTP 403", "no response", "no response", "HTTP 200")))
ok("status_label() of nothing is nothing, of a number is its code",
   identical(status_label(character()), character()) && identical(status_label(c(404, NA)), c("HTTP 404", "no response")))
# Ties are ordered by the value in C-locale order, whatever the input order:
# upper case sorts before lower case there, so "HTTP ..." before "no ...".
ok("count_desc(): largest first, ties in C-locale order, input order ignored",
   identical(count_desc(c("b", "a", "B", "a", "b", "B", "c"))$value, c("B", "a", "b", "c")) &&
     identical(count_desc(c("c", "B", "b", "a", "B", "b", "a"))$value, c("B", "a", "b", "c")) &&
     identical(count_desc(c("c", "B", "b", "a", "B", "b", "a"))$n, c(2L, 2L, 2L, 1L)))
ok("count_desc() of nothing is an empty table", nrow(count_desc(character())) == 0L)
ok("count_desc() refuses NA rather than drop it",
   inherits(tryCatch(count_desc(c("a", NA)), error = identity), "error"))

# ---- not_collected_cause() --------------------------------------------------
reg <- data.frame(doi = c("10.1/A", "10.1/b", "10.1/c"), cause = c("never-asked", "403-refused", ""))
ok("cause is matched on the DOI without regard to case",
   identical(not_collected_cause(c("10.1/a", "10.1/B"), reg), c("never-asked", "403-refused")))
ok("a page missing from the register, or without a cause, is named as such",
   identical(not_collected_cause(c("10.1/c", "10.1/zz", NA), reg), rep(NO_CAUSE, 3)))
ok("not_collected_cause() of nothing is nothing", identical(not_collected_cause(character(), reg), character()))

# ---- the whole script, on a fixture ----------------------------------------
# 11 collected documents (one of each kind, plus an unlisted host) out of a
# queue of 15. Every number below is worked out by hand from these rows.
script <- normalizePath("analysis/stage1_rendered_pages.R")
helper <- normalizePath("R/stage1_pages.R")

make_fixture <- function(extra_s2 = NULL, drop_from_register = character()) {
  d <- tempfile("stage1-")
  for (p in c("R", "data/clean", "data/reports")) dir.create(file.path(d, p), recursive = TRUE)
  file.copy(helper, file.path(d, "R", "stage1_pages.R"))
  s2 <- read.csv(text = '
doi,doc_role,host,capture_format,source_kind,abstract_chars,body_chars
10.1/a1,original,onlinelibrary.wiley.com,mhtml,,500,8000
10.1/a2,original,journals.sagepub.com,mhtml,teammate-sage,300,1000
10.1/a3,both,www.tandfonline.com,mhtml,teammate-tandf,0,200
10.1/n1,retraction,onlinelibrary.wiley.com,mhtml,,0,900
10.1/w1,original,www.mdpi.com,html,,400,9000
10.1/c1,original,www.cureus.com,html,,400,9000
10.1/u1,original,academic.oup.com,html,campus-network,400,9000
10.1/t1,original,doi.org,html,tail-plain-http,400,9000
10.1/x1,original,api.elsevier.com,elsxml,elsevier-api,400,9000
10.1/o1,original,europepmc.org,mhtml,oa-mirror,400,9000
10.1/z1,original,odd.example.org,html,,400,9000
', colClasses = "character", strip.white = TRUE)
  s2 <- rbind(s2, extra_s2)
  # The queue writes one collected DOI in capitals: the match is case-blind.
  q <- read.csv(text = '
doi,http_status
10.1/A1,200
10.1/a2,202
10.1/a3,200
10.1/n1,
10.1/w1,200
10.1/c1,200
10.1/u1,200
10.1/t1,200
10.1/x1,200
10.1/o1,200
10.1/z1,200
10.1/m1,403
10.1/m2,
10.1/m3,200
10.1/m4,403
', colClasses = "character", strip.white = TRUE)
  # The register of missing pages, for the four not collected; one is left
  # out when the caller asks, to show it is reported rather than dropped.
  reg <- read.csv(text = '
doi,http_status,cause
10.1/m1,403,403-refused
10.1/m2,,never-asked
10.1/m3,200,human-verification
10.1/m4,403,403-refused
', colClasses = "character", strip.white = TRUE)
  reg <- reg[!reg$doi %in% drop_from_register, ]
  KB <- 1024
  ai <- data.frame(
    capture_format = c(rep("mhtml", 6), "mhtml", "mhtml", "mhtml", "html", "mhtml"),
    gz_bytes = c(10, 100, 300, 700, 2048, 6144, 40, 60, 100, 100, NA) * KB,
    verdict = c(rep("ok", 6), "suspect", "suspect", "not-in-database", "ok", "ok"),
    usable = c(rep("1", 6), "0", "0", "0", "1", "1"))
  ai$gz_bytes <- ifelse(is.na(ai$gz_bytes), "", format(ai$gz_bytes, scientific = FALSE, trim = TRUE))
  w <- function(x, f) utils::write.csv(x, file.path(d, "data/clean", f), row.names = FALSE)
  w(s2, "stage2_index.csv"); w(q, "crawl_by_doi.csv"); w(ai, "archive_index.csv")
  w(reg, "not_collected.csv")
  d
}

# Run in this process, in the fixture folder, the way reproduce_results() runs
# it in the project root. Warnings are collected: a recycled vector is a
# warning, not an error, and it once put wrong labels in the table.
run_script <- function(d, report_dir = NULL) {
  old <- setwd(d)
  old_reports <- Sys.getenv("CS43_REPORTS", unset = NA)
  Sys.unsetenv("CS43_REPORTS")
  if (!is.null(report_dir)) Sys.setenv(CS43_REPORTS = report_dir)
  on.exit({
    setwd(old)
    if (is.na(old_reports)) Sys.unsetenv("CS43_REPORTS") else Sys.setenv(CS43_REPORTS = old_reports)
  })
  warns <- character()
  err <- tryCatch(withCallingHandlers({
    utils::capture.output(sys.source(script, envir = new.env()))
    NULL
  }, warning = function(w) {
    warns <<- c(warns, conditionMessage(w))
    invokeRestart("muffleWarning")
  }), error = function(e) conditionMessage(e))
  list(error = err, warnings = warns,
       out = file.path(if (is.null(report_dir)) file.path(d, "data/reports") else report_dir,
                       "stage1-rendered-pages.csv"))
}

d <- make_fixture()
r <- run_script(d)
ok("the script runs to the end on the fixture", is.null(r$error))
if (!is.null(r$error)) cat("      error:", r$error, "\n")
ok("the script gives no warnings", !length(r$warnings))
if (length(r$warnings)) cat("      warning:", r$warnings, sep = "\n      ")
res <- if (file.exists(r$out)) utils::read.csv(r$out, stringsAsFactors = FALSE) else
  data.frame(section = character(), measure = character(), n = integer(), share_pct = numeric())
n_of <- function(section, measure) {
  x <- res$n[res$section == section & res$measure == measure]
  if (length(x) == 1L) x else NA
}
S <- "what the 11 collected documents are"
ok("collected: 4 rendered MHTML (crawler, SAGE, T&F, a notice)", identical(n_of(S, K[["rendered_mhtml"]]), 4L))
ok("collected: 1 WebKit HTML (MDPI)", identical(n_of(S, K[["webkit_html"]]), 1L))
ok("collected: 1 campus HTML", identical(n_of(S, K[["campus_html"]]), 1L))
ok("collected: 2 plain-request HTML (Cureus, tail)", identical(n_of(S, K[["direct_html"]]), 2L))
ok("collected: 1 text, 1 copy", identical(n_of(S, K[["text"]]), 1L) && identical(n_of(S, K[["copy"]]), 1L))
ok("collected: the unlisted host is reported as itself",
   identical(n_of(S, "other: html from crawler at odd.example.org"), 1L))
ok("not collected: 15 in the queue - 11 collected = 4", identical(n_of(S, "not collected"), 4L))
ok("shares are of the whole queue", isTRUE(res$share_pct[res$section == S & res$measure == "not collected"] == 26.7))
ok("the kinds add up to the collected documents",
   sum(res$n[res$section == S & res$measure != "not collected"]) == 11L)

S <- "MHTML size, pages kept (compressed)"
ok("kept MHTML: one file in each size bin, html and missing sizes left out",
   all(vapply(c("under 50 KiB", "50-200 KiB", "200-500 KiB", "500 KiB-1 MiB", "1-5 MiB", "5 MiB and above"),
              function(b) identical(n_of(S, b), 1L), TRUE)))
# quantile type 7 of 10, 100, 300, 700, 2048, 6144 KB
ok("kept MHTML: percentiles 55 / 500 / 4096 KB of 6 files",
   identical(n_of(S, "10th / median / 90th percentile KiB: 55 / 500 / 4096"), 6L))
S <- "MHTML size, pages rejected by the content check (compressed)"
ok("rejected MHTML: 2 files, the not-in-database archive left out",
   identical(n_of(S, "under 50 KiB"), 1L) && identical(n_of(S, "50-200 KiB"), 1L) &&
     identical(n_of(S, "10th / median / 90th percentile KiB: 42 / 50 / 58"), 2L))

S <- "content of article pages kept as MHTML"
ok("content: length/abstract bins and unknowns partition the article pages without inferring notices",
   identical(res$n[res$section == S], c(1L, 1L, 1L, 0L)))
ok("content labels do not assert completeness, paywalls or notice replacement",
   !any(grepl("shows the retraction notice|abstract only [(]paywall[)]|full article text on", res$measure)))

S <- "last HTTP status, all 15 pages"
ok("status, all pages: 200 x10, 403 x2, no response x2, 202 x1, largest first",
   identical(res$measure[res$section == S], c("HTTP 200", "HTTP 403", "no response", "HTTP 202")) &&
     identical(res$n[res$section == S], c(10L, 2L, 2L, 1L)))
S <- "last HTTP status, the 4 pages not collected"
ok("status, pages not collected: 403 x2, 200 x1, no response x1",
   identical(res$measure[res$section == S], c("HTTP 403", "HTTP 200", "no response")) &&
     identical(res$n[res$section == S], c(2L, 1L, 1L)))
S <- "pages not collected: the 1 with HTTP 200, by cause (not_collected.csv)"
ok("not collected, ending on 200: the cause is the register's (human-verification)",
   identical(res$measure[res$section == S], "human-verification") && identical(res$n[res$section == S], 1L))
S <- "pages not collected: the 1 with no response, by cause (not_collected.csv)"
ok("not collected, no response: the cause is the register's (never-asked)",
   identical(res$measure[res$section == S], "never-asked") && identical(res$n[res$section == S], 1L) &&
     isTRUE(res$share_pct[res$section == S] == 100))
ok("nothing but these sections is written, in this order",
   identical(unique(res$section), c("what the 11 collected documents are",
                                    "MHTML size, pages kept (compressed)",
                                    "MHTML size, pages rejected by the content check (compressed)",
                                    "content of article pages kept as MHTML",
                                    "last HTTP status, all 15 pages",
                                    "last HTTP status, the 4 pages not collected",
                                    "pages not collected: the 1 with HTTP 200, by cause (not_collected.csv)",
                                    "pages not collected: the 1 with no response, by cause (not_collected.csv)")))
unlink(d, recursive = TRUE)

# A collected DOI missing from the queue would make the two "not collected"
# numbers in the report disagree; the script stops instead.
d <- make_fixture(extra_s2 = data.frame(doi = "10.1/stray", doc_role = "original", host = "www.mdpi.com",
                                        capture_format = "html", source_kind = "", abstract_chars = "0",
                                        body_chars = "0"))
r <- run_script(d)
ok("a collected DOI that is not in the queue stops the script", !is.null(r$error) && !file.exists(r$out))
unlink(d, recursive = TRUE)

# A page not collected that the register does not have is counted under its
# own name, not dropped and not given a cause.
d <- make_fixture(drop_from_register = "10.1/m3")
r <- run_script(d)
res <- if (file.exists(r$out)) utils::read.csv(r$out, stringsAsFactors = FALSE) else res[0, ]
S <- "pages not collected: the 1 with HTTP 200, by cause (not_collected.csv)"
ok("a page missing from the register is reported as such",
   is.null(r$error) && identical(res$measure[res$section == S], NO_CAUSE) && identical(res$n[res$section == S], 1L))
unlink(d, recursive = TRUE)

# A caller's global report destination must not leak into fixture runs.
d <- make_fixture()
redirect <- file.path(d, "redirected")
r <- run_script(d, redirect)
ok("explicitly redirected output is written and read from that destination",
   is.null(r$error) && file.exists(r$out) &&
     !file.exists(file.path(d, "data/reports/stage1-rendered-pages.csv")))
unlink(d, recursive = TRUE)

d <- make_fixture()
p <- file.path(d, "data/clean/stage2_index.csv")
x <- read.csv(p, colClasses = "character")
x$body_chars[x$doi == "10.1/a3"] <- NA
write.csv(x, p, row.names = FALSE)
r <- run_script(d)
x <- if (file.exists(r$out)) read.csv(r$out) else data.frame()
ok("unknown content lengths remain an explicit partition, not a silently dropped row",
   is.null(r$error) && identical(x$n[x$measure == "unknown body or abstract length"], 1L) &&
     sum(x$n[x$section == "content of article pages kept as MHTML"]) == 3L)
unlink(d, recursive = TRUE)

d <- make_fixture()
for (name in c("stage2_index.csv", "crawl_by_doi.csv")) {
  p <- file.path(d, "data/clean", name)
  x <- read.csv(p, colClasses = "character")
  write.csv(rbind(x, x[1, ]), p, row.names = FALSE)
}
r <- run_script(d)
ok("duplicating the same DOI in both inputs still fails validation",
   !is.null(r$error) && grepl("distinct DOI", r$error) && !file.exists(r$out))
unlink(d, recursive = TRUE)

d <- make_fixture()
writeLines(c("doi,http_status", '"unterminated'), file.path(d, "data/clean/crawl_by_doi.csv"))
r <- run_script(d)
ok("a CSV read warning aborts instead of publishing partial counts",
   !is.null(r$error) && grepl("cannot safely read", r$error) && !file.exists(r$out))
unlink(d, recursive = TRUE)

for (doi in c("10.1/a1", "10.1/outside")) {
  d <- make_fixture()
  p <- file.path(d, "data/clean/not_collected.csv")
  x <- read.csv(p, colClasses = "character")
  extra <- x[1, ]
  extra$doi <- doi
  write.csv(rbind(x, extra), p, row.names = FALSE)
  r <- run_script(d)
  ok("the missing register rejects collected and out-of-queue DOIs",
     !is.null(r$error) && grepl("collected or out-of-queue", r$error) && !file.exists(r$out))
  unlink(d, recursive = TRUE)
}

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
