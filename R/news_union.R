# Papers with news coverage over all four routes the two trackers allow.
#
# The client named two sources, Altmetric and PlumX, and each can be asked
# about original and notice DOIs. This is tracked DOI coverage within the
# full Retraction Watch corpus (all record natures), not a count of news
# stories about withdrawal. The coverage question is a union over four routes:
#
#   Altmetric, paper DOI    summary news_count; post text is checked separately
#   Altmetric, notice DOI   the same count, filed under the notice
#   PlumX, paper DOI        a count only - no date, no text
#   PlumX, notice DOI       a count only
#
# The table this builds was first counted from the database by hand on
# 27 September (data/reports/news-coverage-union-2026-09-27.csv, project log
# 2026-09-27 "PlumX on the notice DOIs is complete") and had no producer: the
# PlumX notice counts never reached data/clean. scripts/export_notice_news.py
# now exports them, and this file is the definition the counting follows.
#
# Two rules, both from R/notice_news.R and the collectors, not new here:
#
#   * A paper counts once, however many routes reach it. The routes overlap
#     heavily - a story about a retraction usually names the paper and the
#     notice - so route counts are never added.
#   * A notice reaches every paper it retracts, which is true and misleading
#     for a mass retraction: one story about a notice retracting 408 papers
#     covers all 408. Papers reached ONLY through notices retracting
#     MASS_NOTICE_AT or more papers are counted apart, and a total without
#     them is reported beside the total with them.
#
# And one that the hand count did not follow. A record whose notice DOI is the
# paper's own DOI is not a notice route: there is nothing separate to ask
# about. Both collectors leave such pairs out when they list notice DOIs
# (collect_altmetric_notices.py, collect_plumx.py) and
# scripts/export_notice_news.py leaves them out of notice_papers.csv, so they
# are dropped here too. The hand count joined every record to the notice
# tables, these pairs included, and one of them reaches a notice with news.
# Of the DOIs that are their own notice in some record, 12 are also the
# notice DOI of other papers; one of those 12 notices has news:
# 10.1093/ecco-jcc/jjab227, an expression of concern over 38 papers (one
# Altmetric news mention, no PlumX news), which Retraction Watch record 35171
# also gives as its own paper's DOI. That DOI has Altmetric news as a paper,
# so it is covered either way; kept as a notice pair it adds one paper to the
# Altmetric notice route and nothing to any union. The 27 September table has
# 1,970 on that route where this gives 1,969, and every other row the same -
# compared paper by paper, not only as totals, with the database opened
# read-only (data/reports/news-coverage-union-checks-2026-10-13.csv, which
# also shows that every count the union reads was collected before the hand
# count was written: the difference is the definition, not the data).
# `keep_self_pairs = TRUE` counts with those pairs kept, so
# analysis/news_coverage_union.R can show that this pair is the whole
# difference rather than assert it.

source("R/notice_news.R")

# PlumX has no news column: its per-source counts live in detail_json, as
# "mention.NEWS_COUNT". Parse the actual JSON object and require a nonnegative
# integer count, rather than interpreting malformed text as zero coverage.
# Absent key, empty or missing JSON: 0 in this collected ledger, not proof
# that the work had no news anywhere.
plumx_news_count <- function(detail_json) {
  j <- as.character(detail_json)
  j[is.na(j)] <- ""
  vapply(seq_along(j), function(i) {
    if (!nzchar(trimws(j[i]))) return(0L)
    if (!startsWith(trimws(j[i]), "{")) stop("PlumX detail_json must be an object at row ", i, call. = FALSE)
    x <- tryCatch(jsonlite::fromJSON(j[i], simplifyVector = FALSE),
                  error = function(e) stop("invalid PlumX detail_json at row ", i, ": ", conditionMessage(e), call. = FALSE))
    if (anyDuplicated(names(x))) stop("duplicate PlumX JSON keys at row ", i, call. = FALSE)
    if (!"mention.NEWS_COUNT" %in% names(x)) return(0L)
    n <- x[["mention.NEWS_COUNT"]]
    if (length(n) != 1L || !is.numeric(n) || !is.finite(n) || n < 0 ||
        n != floor(n) || n > .Machine$integer.max) {
      stop("invalid PlumX NEWS_COUNT at row ", i, call. = FALSE)
    }
    as.integer(n)
  }, integer(1), USE.NAMES = FALSE)
}

norm_doi <- function(x) {
  x <- tolower(trimws(as.character(x)))
  unique(x[!is.na(x) & nzchar(x)])
}

# universe       every paper DOI in the corpus: the denominator
# alt_paper      paper DOIs with Altmetric news
# plumx_paper    paper DOIs with PlumX news
# alt_notice     notice DOIs with Altmetric news
# plumx_notice   notice DOIs with PlumX news
# map            retraction_doi, original_doi, n_papers (notice_papers.csv)
#
# Returns one row per measure, with the labels of the 27 September table so
# the two can be compared line by line.
news_coverage_union <- function(universe, alt_paper, plumx_paper, alt_notice,
                                plumx_notice, map, mass_at = MASS_NOTICE_AT,
                                keep_self_pairs = FALSE) {
  universe <- norm_doi(universe)
  N <- length(universe)
  m <- data.frame(retraction_doi = tolower(trimws(map$retraction_doi)),
                  original_doi = tolower(trimws(map$original_doi)),
                  n_papers = suppressWarnings(as.integer(map$n_papers)),
                  stringsAsFactors = FALSE)
  m <- m[!is.na(m$original_doi) & nzchar(m$original_doi), ]
  if (!keep_self_pairs) m <- m[m$retraction_doi != m$original_doi, ]
  # n_papers is carried on the mapping by the exporter, so a notice's size is
  # the same number here as in the collectors and R/notice_news.R. A notice
  # whose size is unknown is not treated as small: crediting it as individual
  # coverage is the error the split exists to prevent.
  small <- !is.na(m$n_papers) & m$n_papers < mass_at

  in_u <- function(x) intersect(norm_doi(x), universe)
  via <- function(notices, only_small = FALSE) {
    sel <- m$retraction_doi %in% norm_doi(notices)
    if (only_small) sel <- sel & small
    in_u(m$original_doi[sel])
  }

  A1 <- in_u(alt_paper)
  P1 <- in_u(plumx_paper)
  A2 <- via(alt_notice)
  P2 <- via(plumx_notice)
  alt_any <- union(A1, A2)
  previous <- union(alt_any, P1)
  all4 <- union(previous, P2)
  added_p2 <- setdiff(all4, previous)
  # Papers a reader can trust as individual coverage: reached on a paper DOI,
  # or through at least one notice retracting fewer than mass_at papers.
  individual <- Reduce(union, list(A1, P1, via(alt_notice, TRUE), via(plumx_notice, TRUE)))
  mass_only <- setdiff(all4, individual)

  rows <- list(
    "Altmetric, article DOI" = A1,
    "Altmetric, notice DOI" = A2,
    "PlumX, article DOI (count only)" = P1,
    "PlumX, notice DOI (count only)" = P2,
    "Altmetric article or notice" = alt_any,
    "PlumX article or notice" = union(P1, P2),
    "Altmetric article or notice, or PlumX article (previous total)" = previous,
    "ALL FOUR" = all4,
    "  added by PlumX notice DOI alone" = added_p2,
    "    of which via notices retracting <%d papers" =
      intersect(added_p2, via(plumx_notice, TRUE)),
    # These are summary-count routes, not a check of the retrieved posts.
    # Altmetric can supply dates and text, but placeholder-only or missing
    # text must not be certified as readable by this coverage table.
    "  papers reached through Altmetric news counts" = intersect(all4, alt_any),
    "  PlumX only (counts, no date, no text)" = setdiff(all4, alt_any),
    "ALL FOUR, mass-retraction-only papers left out" = setdiff(all4, mass_only),
    "  papers reached only through mass-retraction notices" = mass_only)
  n <- vapply(rows, length, 0L)
  data.frame(measure = sub("%d", mass_at, names(rows), fixed = TRUE), papers = unname(n),
             share_pct = round(100 * unname(n) / N, 1), stringsAsFactors = FALSE)
}
