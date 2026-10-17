# Retraction-notice coverage, brought back to the paper level.
#
#   Rscript analysis/notice_news_merge.R
#
# Run after scripts/collect_altmetric_notices.py, its detail pass, and
# scripts/export_notice_news.py.
#
# The rule - one article about a mass retraction is one article, not 659
# pieces of coverage - lives in R/notice_news.R, with the reasoning and its
# tests. This file reads the exports, calls it, and writes the tables.

source("R/notice_news.R")

clean <- file.path("data", "clean")
OUT <- file.path("data", "reports")
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)
need <- c("altmetric_notice.csv", "notice_papers.csv")
missing <- need[!file.exists(file.path(clean, need))]
if (length(missing)) {
  stop("missing ", paste(missing, collapse = ", "),
       " - run scripts/export_notice_news.py (and the collectors before it)",
       call. = FALSE)
}
rd <- function(f, ...) utils::read.csv(file.path(clean, f), stringsAsFactors = FALSE,
                                       fileEncoding = "UTF-8-BOM", ...)

notice <- rd("altmetric_notice.csv", colClasses = "character")
map <- rd("notice_papers.csv", colClasses = "character")

all_notices <- length(unique(map$retraction_doi))
cat("notices collected:", format(nrow(notice), big.mark = ","),
    "of", format(all_notices, big.mark = ","), "\n")
if (nrow(notice) < all_notices) {
  cat("NOTE: the collection is not finished. Everything below is a partial\n",
      "     count and will grow.\n", sep = "")
}

# The individual articles, when the detail pass has produced any.
posts_file <- file.path(clean, "altmetric_notice_posts.csv.gz")
news <- NULL
if (file.exists(posts_file) &&
    length(readLines(gzfile(posts_file), n = 2, warn = FALSE)) > 1) {
  posts <- utils::read.csv(gzfile(posts_file), stringsAsFactors = FALSE)
  news <- posts[posts$kind == "news", ]
}

paper_news <- NULL
if (file.exists(file.path(clean, "altmetric.csv"))) {
  alt <- rd("altmetric.csv", colClasses = "character")
  n <- suppressWarnings(as.integer(alt$news_count))
  paper_news <- alt$doi[!is.na(n) & n > 0]
}

# The paper side's news articles, so the two can be deduplicated by URL. Most
# pieces about a retraction name the paper and the notice, and Altmetric files
# them under both DOIs - adding the two totals counts every such piece twice.
paper_posts <- NULL
pp <- file.path(clean, "altmetric_posts.csv.gz")
if (file.exists(pp)) {
  cat("reading the paper-side mentions (1.4 M rows)...\n")
  a <- utils::read.csv(gzfile(pp), stringsAsFactors = FALSE,
                       colClasses = "character")
  paper_posts <- a[a$kind == "news", intersect(c("doi", "post_id", "url"), names(a))]
  rm(a)
}

res <- notice_news_tables(notice, map, news = news, paper_news = paper_news,
                          paper_news_posts = paper_posts)
print(res$summary, row.names = FALSE, right = FALSE)

w <- function(d, name, what) {
  f <- file.path(OUT, paste0("notice-news-", name, ".csv"))
  utils::write.csv(d, f, row.names = FALSE, fileEncoding = "UTF-8")
  cat("written:", f, "-", nrow(d), what, "\n")
}
w(res$summary, "summary", "rows")
w(res$by_notice[order(-as.integer(res$by_notice$n_papers)), ], "by-notice",
  "notices with news")
w(res$by_paper, "by-paper", "papers")

# A check the data should pass: a mention of a notice is post-retraction by
# construction, because the notice does not exist until the retraction does.
# Anything dated earlier is worth seeing rather than assuming away.
if (!is.null(news) && nrow(news) && file.exists(file.path(clean, "records_clean.csv"))) {
  rec <- rd("records_clean.csv", colClasses = "character")
  when <- tapply(rec$retraction_date, tolower(trimws(rec$retraction_doi)),
                 function(x) sort(x)[1])
  d <- news[!is.na(news$posted_on), ]
  d$retracted_on <- unname(when[tolower(d$retraction_doi)])
  d <- d[!is.na(d$retracted_on), ]
  if (nrow(d)) {
    before <- sum(substr(d$posted_on, 1, 10) < substr(d$retracted_on, 1, 10))
    cat("\nnotice mentions dated before their own retraction:", before, "of",
        nrow(d), "- should be 0 or near it\n")
  }
}
