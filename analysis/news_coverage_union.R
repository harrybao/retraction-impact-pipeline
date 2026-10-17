# Papers with news coverage over all four routes: Altmetric and PlumX, each on
# the paper's DOI and on its retraction notice's DOI.
#
#   Rscript analysis/news_coverage_union.R
#
# Run after scripts/export_notice_news.py, which writes the three notice
# tables this reads from data/clean (altmetric_notice.csv, plumx_notice.csv,
# notice_papers.csv). The paper-side tables come from the share bundle like
# every other analysis input.
#
# The table was first counted from the database by hand on 27 September
# (data/reports/news-coverage-union-2026-09-27.csv, project log 2026-09-27) and
# no script in the repository could rebuild it. This is that script. The
# counting rules - a paper once however many routes reach it, papers reached
# only through mass-retraction notices counted apart - are in R/news_union.R
# with their reasons.
#
# Writes
#   data/reports/news-coverage-union.csv               the table
#   data/reports/news-coverage-union-vs-2026-09-27.csv this against the hand
#       count, row by row, with the count recomputed the hand count's way
#       beside it, so a difference comes with its cause rather than a claim.
#       The 27 September file itself is left as it is.

source("R/analysis_data.R")
ensure_analysis_inputs(ANALYSIS_INPUTS[c("data/clean/altmetric.csv", "data/clean/plumx.csv",
                                         "data/clean/records_clean.csv")])
source("R/news_union.R")
options(width = 160)   # the measure labels are long; keep each row on one line

clean <- file.path("data", "clean")
OUT <- Sys.getenv("CS43_REPORTS", file.path("data", "reports"))
if (!nzchar(trimws(OUT))) stop("CS43_REPORTS must not be blank", call. = FALSE)
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)
# Not in the share bundle: export_notice_news.py writes them to data/clean,
# and the handover package carries data/clean whole.
need <- c("altmetric_notice.csv", "plumx_notice.csv", "notice_papers.csv")
missing <- need[!file.exists(file.path(clean, need))]
if (length(missing)) {
  stop("missing ", paste(missing, collapse = ", "),
       " - run scripts/export_notice_news.py (plumx_notice.csv needs a database ",
       "collected with collect_plumx.py --notices)", call. = FALSE)
}
rd <- function(f) withCallingHandlers(
  utils::read.csv(file.path(clean, f), colClasses = "character", fileEncoding = "UTF-8-BOM"),
  warning = function(w) stop("cannot safely read ", f, ": ", conditionMessage(w), call. = FALSE))
require_columns <- function(x, columns, name) {
  missing <- setdiff(columns, names(x))
  if (length(missing)) stop(name, " missing columns: ", paste(missing, collapse = ", "), call. = FALSE)
}
require_keys <- function(x, key, name) {
  d <- tolower(trimws(x[[key]]))
  if (anyNA(d) || any(!nzchar(d)) || anyDuplicated(d)) {
    stop(name, " must have unique nonempty DOI keys", call. = FALSE)
  }
}

alt <- rd("altmetric.csv")
px <- rd("plumx.csv")
alt_notice <- rd("altmetric_notice.csv")
px_notice <- rd("plumx_notice.csv")
map <- rd("notice_papers.csv")
for (name in c("alt", "px", "alt_notice", "px_notice")) {
  x <- get(name)
  key <- if (grepl("notice", name)) "retraction_doi" else "doi"
  count <- if (startsWith(name, "px")) "detail_json" else "news_count"
  require_columns(x, c(key, count), name)
  require_keys(x, key, name)
}
require_columns(map, c("retraction_doi", "original_doi", "n_papers"), "notice_papers.csv")
n_map <- suppressWarnings(as.numeric(map$n_papers))
if (anyNA(n_map) || any(!is.finite(n_map) | n_map < 1 | n_map != floor(n_map) |
                      n_map > .Machine$integer.max)) {
  stop("notice_papers.csv needs known positive integer notice sizes", call. = FALSE)
}
nd <- tolower(trimws(map$retraction_doi))
od <- tolower(trimws(map$original_doi))
if (anyNA(nd) || anyNA(od) || any(!nzchar(nd) | !nzchar(od) | nd == od) ||
    anyDuplicated(paste(nd, od, sep = "\t"))) {
  stop("notice_papers.csv needs unique nonempty nonself DOI pairs", call. = FALSE)
}
if (any(n_map != as.integer(table(nd)[nd]))) {
  stop("notice_papers.csv notice sizes disagree with the complete mapping", call. = FALSE)
}
rec <- rd("records_clean.csv")
require_columns(rec, c("original_doi", "retraction_doi", "retraction_nature"), "records_clean.csv")
rec_nd <- tolower(trimws(rec$retraction_doi))
rec_od <- tolower(trimws(rec$original_doi))
pair_rows <- !is.na(rec_nd) & !is.na(rec_od) & nzchar(rec_nd) & nzchar(rec_od) & rec_nd != rec_od
expected_pairs <- unique(paste(rec_nd[pair_rows], rec_od[pair_rows], sep = "\t"))
if (!setequal(paste(nd, od, sep = "\t"), expected_pairs)) {
  stop("notice_papers.csv does not match the complete original/notice mapping", call. = FALSE)
}
# Match the collectors: a notice DOI needs its own query when at least one
# record has a different original DOI (or has no original DOI). Self-only
# notices are already covered by the complete original-provider tables.
separate_notice <- !is.na(rec_nd) & nzchar(rec_nd) &
  (is.na(rec_od) | !nzchar(rec_od) | rec_nd != rec_od)
notice_targets <- norm_doi(rec_nd[separate_notice])
for (name in c("alt_notice", "px_notice")) {
  keys <- tolower(trimws(get(name)$retraction_doi))
  if (!setequal(keys, notice_targets)) {
    absent <- setdiff(notice_targets, keys)
    stop(name, " does not cover the complete notice DOI list; missing ",
         length(absent), " (first: ", if (length(absent)) absent[1] else "none",
         "). Incomplete attention data cannot be treated as zero news.", call. = FALSE)
  }
}
cat("Population: original DOIs in the full Retraction Watch corpus, all record natures.\n")
cat("Tracked news mentions do not establish that a story discussed withdrawal.\n")

# The denominator is the corpus: every paper DOI both trackers were asked
# about. They were asked about the same list; if that ever stops being true
# the shares would rest on two different denominators, so stop instead.
universe <- tolower(trimws(alt$doi))
if (!setequal(universe, tolower(trimws(px$doi)))) {
  stop("altmetric.csv and plumx.csv do not cover the same papers (",
       length(unique(universe)), " against ", length(unique(px$doi)),
       "); the shares need one denominator", call. = FALSE)
}
if (!setequal(universe, norm_doi(rec$original_doi))) {
  stop("provider DOI ledgers do not cover the complete original-DOI corpus", call. = FALSE)
}

has_news <- function(x) {
  n <- suppressWarnings(as.numeric(x))
  known <- !is.na(x) & nzchar(trimws(x))
  if (any(known & (is.na(n) | !is.finite(n) | n < 0 | n != floor(n)))) {
    stop("invalid Altmetric news_count", call. = FALSE)
  }
  !is.na(n) & n > 0
}
routes <- list(
  universe = universe,
  alt_paper = alt$doi[has_news(alt$news_count)],
  plumx_paper = px$doi[plumx_news_count(px$detail_json) > 0],
  alt_notice = alt_notice$retraction_doi[has_news(alt_notice$news_count)],
  plumx_notice = px_notice$retraction_doi[plumx_news_count(px_notice$detail_json) > 0])

res <- do.call(news_coverage_union, c(routes, list(map = map)))
cat(sprintf("original DOIs with tracked news coverage, %s original DOIs in the corpus:\n\n",
            format(length(unique(universe)), big.mark = ",")))
print(res, row.names = FALSE, right = FALSE)
f <- file.path(OUT, "news-coverage-union.csv")
utils::write.csv(res, f, row.names = FALSE, fileEncoding = "UTF-8")
cat("\nwritten:", f, "\n")

# A work can have several record natures. Count an original DOI once when it
# has any Retraction record; this still does not classify the news topic.
ret <- norm_doi(rec$original_doi[!is.na(rec$retraction_nature) & rec$retraction_nature == "Retraction"])
ret_routes <- routes
ret_routes$universe <- ret
ret_res <- do.call(news_coverage_union, c(ret_routes, list(map = map)))
all_covered <- res$papers[res$measure == "ALL FOUR"]
ret_covered <- ret_res$papers[ret_res$measure == "ALL FOUR"]
population <- data.frame(
  measure = c("all original DOIs, all record natures", "original DOIs with any Retraction record",
              "covered original DOIs, all record natures", "covered original DOIs with any Retraction record",
              "covered original DOIs without a Retraction record"),
  original_dois = c(length(unique(universe)), length(ret), all_covered, ret_covered, all_covered - ret_covered))
failed_keys <- function(x, key) {
  if (!"error" %in% names(x)) return(character())
  norm_doi(x[[key]][!is.na(x$error) & nzchar(trimws(x$error))])
}
failed_notices <- union(failed_keys(alt_notice, "retraction_doi"),
                       failed_keys(px_notice, "retraction_doi"))
unknown <- Reduce(union, list(failed_keys(alt, "doi"), failed_keys(px, "doi"),
                             od[nd %in% failed_notices]))
population <- rbind(population, data.frame(
  measure = "original DOIs with an unknown news lookup (failed request)",
  original_dois = length(intersect(unknown, universe))))
cat("Failed news lookups are unknown, not evidence of no attention:\n")
utils::write.csv(population, file.path(OUT, "news-coverage-population.csv"), row.names = FALSE)
print(population, row.names = FALSE)

# --- against the 27 September hand count ---------------------------------------
hand_file <- file.path("data", "reports", "news-coverage-union-2026-09-27.csv")
if (file.exists(hand_file)) {
  hand <- utils::read.csv(hand_file, stringsAsFactors = FALSE, strip.white = FALSE)
  legacy_text_label <- "  papers with dated, text-bearing Altmetric news"
  count_label <- "  papers reached through Altmetric news counts"
  hand$measure[hand$measure == legacy_text_label] <- count_label
  # The same count with the records whose notice DOI is the paper's own DOI
  # put back as notice pairs - the one place the hand count is known to have
  # counted differently (R/news_union.R). They come from the records, because
  # notice_papers.csv leaves them out on purpose. A pair keeps its notice's
  # size from the mapping, so the mass split is the same as the hand count's.
  rn <- tolower(trimws(rec$retraction_doi))
  self <- unique(rn[nzchar(rn) & rn == tolower(trimws(rec$original_doi))])
  size <- map$n_papers[match(self, map$retraction_doi)]
  map_kept <- rbind(map[, c("retraction_doi", "original_doi", "n_papers")],
                    data.frame(retraction_doi = self, original_doi = self,
                               n_papers = size, stringsAsFactors = FALSE))
  kept <- do.call(news_coverage_union, c(routes, list(map = map_kept,
                                                      keep_self_pairs = TRUE)))

  cmp <- data.frame(measure = res$measure,
                    papers_2026_09_27 = hand$papers[match(res$measure, hand$measure)],
                    papers_now = res$papers, stringsAsFactors = FALSE)
  cmp$difference <- cmp$papers_now - cmp$papers_2026_09_27
  cmp$papers_self_pairs_kept <- kept$papers
  cmp$note <- ifelse(is.na(cmp$papers_2026_09_27), "not in the 27 September table",
              ifelse(cmp$difference == 0, "same",
              ifelse(cmp$papers_self_pairs_kept == cmp$papers_2026_09_27,
                     paste("with records whose notice DOI is the paper's own DOI kept",
                           "as notice pairs, this script gives the 27 September figure;",
                           "the project's rule leaves them out (R/news_union.R)"),
                     "NOT explained by the self-pair rule - investigate")))
  clarified <- cmp$measure == count_label
  cmp$note[clarified] <- paste(cmp$note[clarified],
    "Label clarified: summary news counts do not certify post text/date availability.")
  # A row of the hand count with no counterpart here would be a measure this
  # script dropped.
  gone <- setdiff(hand$measure, res$measure)
  if (length(gone)) {
    cmp <- rbind(cmp, data.frame(measure = gone,
                                 papers_2026_09_27 = hand$papers[match(gone, hand$measure)],
                                 papers_now = NA, difference = NA,
                                 papers_self_pairs_kept = NA,
                                 note = "in the 27 September table, not produced here"))
  }
  cat("\n-- against the 27 September hand count --\n")
  print(cmp[, c("measure", "papers_2026_09_27", "papers_now", "difference",
                "papers_self_pairs_kept")], row.names = FALSE, right = FALSE)
  f <- file.path(OUT, "news-coverage-union-vs-2026-09-27.csv")
  utils::write.csv(cmp, f, row.names = FALSE, fileEncoding = "UTF-8")
  cat("written:", f, "\n")
}
