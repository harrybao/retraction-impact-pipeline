# Why do only about 2,500 retracted papers have news coverage?
#
#   Rscript analysis/media_coverage_answer.R
#
# The client expected a higher figure, and the answer we gave was a set of
# measurements. Those were computed once, by hand, and written straight into
# a CSV. Anyone who wanted to check them had to take our word for it, which is
# the opposite of what a reproducible deliverable is for. This script
# recomputes every figure that is ours, from the shipped data, in R.
#
# The rows attributed to the literature are quotations from published papers
# and are carried through unchanged, with their citation.
#
# The short answer: 4.00% is not low. It is the rate the literature reports for
# all indexed papers, and this corpus is dominated by mass retractions that
# were never covered by anyone.

source("R/analysis_data.R")
ensure_analysis_inputs()

clean <- file.path("data", "clean")
rd <- function(f, ...) utils::read.csv(file.path(clean, f), stringsAsFactors = FALSE,
                                       fileEncoding = "UTF-8-BOM", ...)

alt <- rd("altmetric.csv", colClasses = "character")
alt$news_count <- suppressWarnings(as.integer(alt$news_count))
alt$found <- suppressWarnings(as.integer(alt$found))
rec <- rd("records_clean.csv", colClasses = "character")

N <- nrow(alt)
has_news <- !is.na(alt$news_count) & alt$news_count > 0
news_dois <- tolower(alt$doi[has_news])

rows <- list()
add <- function(source, metric, value, denominator, note = "") {
  rows[[length(rows) + 1L]] <<- data.frame(
    source = source, metric = metric, value = value,
    denominator = denominator, note = note, stringsAsFactors = FALSE)
}
pct <- function(n, d) sprintf("%s (%.2f%%)", format(n, big.mark = ","), 100 * n / d)

# ---- the headline ---------------------------------------------------------
add("ours", "papers with Altmetric news", pct(sum(has_news), N),
    paste(format(N, big.mark = ","), "papers"), "recomputed by this script")

add("literature", "WoS papers 2012-2018 with >=1 Altmetric news mention", "4.01%",
    "12,271,991 WoS papers",
    paste("Fang, Costas, Tian, Wang & Wouters (2020) Scientometrics 124:2519-2549,",
          "Table 3; read from the arXiv PDF 2006.12630"))
add("literature",
    "individually retracted RW articles published 2012-2021 with >=1 news mention",
    "1,013 (6.6%)", "15,461 articles, bulk retractions excluded",
    paste("Zheng, Fu, Jiang, Fang & Thelwall (2025) arXiv:2503.18215, Table 3;",
          "article DOI AND notice DOI, Altmetric to Nov 2022"))
add("literature",
    "share of post-retraction news mentions that were mentions of the notice",
    "114/144 (79.2%)", "post-retraction news mentions of highly mentioned articles",
    "Zheng et al. 2025 Table 11 - notice DOIs carry most post-retraction news")

# ---- what pulls the rate down --------------------------------------------
# A retraction is "mass" when its notice covers many papers at once, or when a
# publisher retracted a great many on one day. Either way no newsroom covered
# them one by one, and they are most of this corpus.
rec$original_doi <- tolower(rec$original_doi)
rec$retraction_doi <- tolower(rec$retraction_doi)
shared <- table(rec$retraction_doi[nzchar(rec$retraction_doi)])
mass_by_notice <- names(shared)[shared >= 10]
day_key <- paste(rec$publisher, substr(rec$retraction_date, 1, 10))
big_day <- table(day_key)
mass_by_day <- names(big_day)[big_day >= 50]
rec$is_mass <- rec$retraction_doi %in% mass_by_notice | day_key %in% mass_by_day
mass_dois <- unique(rec$original_doi[rec$is_mass & nzchar(rec$original_doi)])

in_mass <- tolower(alt$doi) %in% mass_dois
rate <- function(sel) {
  n <- sum(sel); k <- sum(sel & has_news)
  sprintf("%.2f%% (%s/%s)", 100 * k / n, format(k, big.mark = ","), format(n, big.mark = ","))
}
add("ours", "news rate in mass retractions vs the rest",
    paste(rate(in_mass), "vs", rate(!in_mass)),
    paste(format(N, big.mark = ","), "papers"),
    "mass = notice DOI shared by >=10 records, or publisher+retraction day >=50 records")

# Three groups that are almost entirely mass retractions.
doi_of <- function(sel) unique(rec$original_doi[sel & nzchar(rec$original_doi)])
groups <- list(
  IEEE = grepl("^IEEE", rec$publisher),
  Hindawi = rec$publisher == "Hindawi",
  `conference papers` = grepl("Conference Abstract/Paper", rec$article_type_list, fixed = TRUE))
for (g in names(groups)) {
  sel <- tolower(alt$doi) %in% doi_of(groups[[g]])
  add("ours", paste("news rate,", g), rate(sel),
      paste(format(sum(sel), big.mark = ","), "papers"), "")
}

flags <- c("Paper Mill", "Compromised Peer Review",
           "Concerns/Issues about Peer Review",
           "Computer-Aided Content or Computer-Generated Content")
flagged <- Reduce(`|`, lapply(flags, function(f) grepl(f, rec$reason_list, fixed = TRUE)))
sel <- tolower(alt$doi) %in% doi_of(flagged)
add("ours", "news rate, papers flagged paper mill / compromised peer review / computer-generated",
    rate(sel), paste(format(sum(sel), big.mark = ","), "papers"),
    paste(flags, collapse = "; "))

# ---- what we have not asked ----------------------------------------------
# Most post-retraction news is about the notice, and the notice has its own
# DOI, which the production collector never queried. This is a coverage
# boundary, not a collection failure, and it is the largest one we know of.
notice <- unique(rec$retraction_doi[nzchar(rec$retraction_doi) &
                                    rec$retraction_doi != rec$original_doi])
never_asked <- setdiff(notice, tolower(alt$doi))
add("ours", "retraction notice DOIs never sent to Altmetric",
    format(length(never_asked), big.mark = ","),
    paste(format(length(notice), big.mark = ","), "notice DOIs differing from the original"),
    "where Zheng et al. found most post-retraction news")

pmid <- rec$original_pmid[nzchar(rec$original_pmid)]
no_record <- tolower(alt$doi[!is.na(alt$found) & alt$found == 0])
with_pmid <- sum(no_record %in% tolower(rec$original_doi[nzchar(rec$original_pmid)]))
add("ours", "papers with no Altmetric record that have a PubMed ID never queried",
    format(with_pmid, big.mark = ","),
    paste(format(length(no_record), big.mark = ","), "papers with no Altmetric record"),
    "Altmetric merges PMID and DOI when linked; a live sample of 50 added 0")

# ---- the media universe we can actually analyse --------------------------
# Retraction Watch's own posts are dated and carry a link, so they can be read
# and placed before or after the retraction. Two filters, and both matter:
#
#   * only a dated post, retractionwatch.com/YYYY/MM/DD/... . The URLS column
#     also holds search links (retractionwatch.com/?s=<author name>), which
#     are not coverage of anything - counting those adds 518 papers that have
#     no article behind them.
#   * only a post cited against at most five papers. Beyond that it is a
#     roundup of a mass retraction, not coverage of one paper.
urls <- strsplit(rec$urls, "[;,[:space:]]+")
flat <- data.frame(doi = rep(rec$original_doi, lengths(urls)),
                   url = unlist(urls), stringsAsFactors = FALSE)
flat <- flat[nzchar(flat$doi) &
             grepl("^https?://retractionwatch\\.com/[0-9]{4}/[0-9]{2}/[0-9]{2}/", flat$url), ]
flat <- unique(flat)
per_url <- table(flat$url)
specific <- names(per_url)[per_url <= 5]
rw_dois <- unique(flat$doi[flat$url %in% specific])
add("ours", "papers with a paper-specific dated Retraction Watch post",
    format(length(rw_dois), big.mark = ","),
    paste(format(N, big.mark = ","), "papers"),
    paste("a dated post cited for at most 5 papers.", length(per_url),
          "dated posts, of which", length(specific), "are specific.",
          "An earlier hand count gave 4,385; this script is the reproducible one"))

usable <- union(news_dois, rw_dois)
add("ours", "papers with Altmetric news OR a paper-specific RW post",
    format(length(intersect(usable, tolower(alt$doi))), big.mark = ","),
    paste(format(N, big.mark = ","), "papers"),
    "dated, with a link: the set that can carry a before/after text analysis")

if (file.exists(file.path(clean, "plumx.csv"))) {
  px <- rd("plumx.csv", colClasses = "character")
  # PlumX has no news column: its per-source figures live inside detail_json,
  # as "mention.NEWS_COUNT". Pulled out by pattern rather than parsed, because
  # this is one number out of 62,867 small objects.
  n <- as.integer(sub('.*"mention\\.NEWS_COUNT"\\s*:\\s*([0-9]+).*', "\\1",
                      px$detail_json))
  n[!grepl("mention\\.NEWS_COUNT", px$detail_json)] <- 0L
  n[is.na(n)] <- 0L
  px_news <- tolower(px$doi[n > 0])
  add("ours", "papers with news in either tracker, or a paper-specific RW post",
      format(length(union(usable, px_news)), big.mark = ","),
      paste(format(N, big.mark = ","), "papers"),
      paste("PlumX finds news for", format(length(px_news), big.mark = ","),
            "papers,", format(length(setdiff(px_news, news_dois)), big.mark = ","),
            "of them not in Altmetric - but gives counts only, no dates and no text,",
            "so they cannot carry a before/after analysis"))
}

out <- do.call(rbind, rows)
print(out[c("source", "metric", "value")], row.names = FALSE, right = FALSE)
f <- file.path("data", "reports", "media-coverage-answer.csv")
utils::write.csv(out, f, row.names = FALSE, fileEncoding = "UTF-8")
cat("\nwritten:", f, "\n")
