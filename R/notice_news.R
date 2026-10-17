# Bringing retraction-notice coverage back to the paper level.
#
# ## The rule this file exists to enforce
#
# A news article about a retraction is ONE article. Most notices retract one
# paper and nothing is at stake, but ten notices in this corpus retract more
# than a hundred papers each and the largest retracts 659. A plain join from
# notices to papers turns one article about a mass retraction into 659 pieces
# of news coverage, and the resulting figure is wrong by a factor nobody can
# see in the output.
#
# So two counts are kept apart, and both are reported:
#
#   * **articles** - distinct mentions of notices, counted once each however
#     many papers the notice retracts. This is how much coverage exists.
#   * **papers whose retraction was covered** - a flag per paper, counted once
#     per paper. True for every paper under a covered notice, which is correct:
#     that paper's retraction was in the news. Split by whether the notice was
#     a mass retraction, because "500 papers' retractions were covered" by a
#     single article is true and misleading in the same breath.
#
# Nothing here multiplies one by the other.

# A notice retracting this many papers or more is a mass retraction. 10 is not
# a natural constant - it is the threshold the media diagnosis already used, so
# the two answers given to the client rest on the same definition.
MASS_NOTICE_AT <- 10L

# --- one article, however many DOIs it mentions -----------------------------
#
# A piece about a retraction usually names both the paper and the notice, and
# Altmetric records it against both DOIs. Those arrive here in two tables -
# altmetric_posts keyed on the paper, altmetric_notice_posts keyed on the
# notice - so a total taken by adding the two counts the same article twice.
# The article's identity is its URL, not the DOI it was filed under.
#
# The normalisation is deliberately shallow. Scheme, a leading www, a trailing
# slash, the fragment and the usual tracking parameters differ between two
# records of one article and mean nothing; the path does not, and is left
# alone, because two articles on one site differ only there.
normalise_url <- function(u) {
  u <- tolower(trimws(as.character(u)))
  u[is.na(u)] <- ""
  u <- sub("^https?://", "", u)
  u <- sub("^www\\.", "", u)
  u <- sub("#.*$", "", u)
  u <- gsub("([?&])(utm_[^&]*|fbclid=[^&]*|gclid=[^&]*|mc_[ce]id=[^&]*)", "\\1", u)
  u <- gsub("([?&])&+", "\\1", u)
  u <- gsub("[?&]+$", "", u)
  sub("/+$", "", u)
}

# What identifies one article. A post with no URL - Altmetric returns some
# under a licence that withholds it - falls back to its own id, so it is never
# merged with anything else. Two unrelated posts both lacking a URL must not
# collapse into one.
article_key <- function(url, post_id) {
  k <- normalise_url(url)
  ifelse(nzchar(k), k, paste0("id:", as.character(post_id)))
}

# Distinct articles across both tables.
distinct_articles <- function(...) {
  sets <- Filter(function(d) !is.null(d) && nrow(d), list(...))
  if (!length(sets)) return(0L)
  keys <- unlist(lapply(sets, function(d) {
    u <- if ("url" %in% names(d)) d$url else rep(NA_character_, nrow(d))
    id <- if ("post_id" %in% names(d)) d$post_id else seq_len(nrow(d))
    article_key(u, id)
  }), use.names = FALSE)
  length(unique(keys))
}

# notice: retraction_doi, n_papers, news_count, found
# map:    retraction_doi, original_doi, n_papers
# news:   retraction_doi, post_id  (optional; the detail pass)
notice_news_tables <- function(notice, map, news = NULL, paper_news = NULL,
                               paper_news_posts = NULL,
                               mass_at = MASS_NOTICE_AT) {
  notice$n_papers <- as.integer(notice$n_papers)
  notice$news_count <- as.integer(notice$news_count)
  notice$is_mass <- !is.na(notice$n_papers) & notice$n_papers >= mass_at
  notice$has_news <- !is.na(notice$news_count) & notice$news_count > 0

  if (!is.null(news) && nrow(news)) {
    # One article can mention several notices of the same mass retraction. Its
    # identity is the post, not the pairing, so it is counted once.
    n_articles <- length(unique(news$post_id))
    n_covered <- length(unique(news$retraction_doi))
    articles_note <- "distinct posts, counted once each"
  } else {
    n_articles <- sum(notice$news_count[notice$has_news], na.rm = TRUE)
    n_covered <- sum(notice$has_news)
    articles_note <- "sum of summary news counts; the detail pass has not run"
  }

  covered <- notice$retraction_doi[notice$has_news]
  pairs <- map[map$retraction_doi %in% covered, ]
  pairs$is_mass <- as.integer(pairs$n_papers) >= mass_at
  # A paper can be retracted by more than one notice. Flag it once, and record
  # whether ANY of its notices was an individual one - that is the version a
  # reader should trust.
  by_paper <- data.frame(original_doi = unique(pairs$original_doi),
                         stringsAsFactors = FALSE)
  by_paper$via_individual <- by_paper$original_doi %in%
    pairs$original_doi[!pairs$is_mass]
  by_paper$via_mass_only <- !by_paper$via_individual

  rows <- list()
  add <- function(metric, value, note = "") {
    rows[[length(rows) + 1L]] <<- data.frame(metric = metric, value = value,
                                             note = note, stringsAsFactors = FALSE)
  }
  add("notices collected so far", nrow(notice))
  add("notices with any Altmetric record", sum(as.integer(notice$found) == 1, na.rm = TRUE))
  add("notices covered by news", n_covered)
  add("news ARTICLES about retraction notices", n_articles, articles_note)
  add("papers whose retraction was covered by news", nrow(by_paper),
      "each paper counted once, however many articles")
  add(sprintf("  ... via a notice retracting fewer than %d papers", mass_at),
      sum(by_paper$via_individual), "the figure to lead with")
  add("  ... only via a mass-retraction notice", sum(by_paper$via_mass_only),
      "true, but one article can be the whole reason")

  if (!is.null(paper_news)) {
    both <- union(tolower(paper_news), tolower(by_paper$original_doi))
    add("papers with news about the PAPER", length(unique(tolower(paper_news))),
        "the figure already reported")
    add("papers with news about the paper OR its notice", length(both))
    add("  ... added by asking about notices",
        length(setdiff(tolower(by_paper$original_doi), tolower(paper_news))),
        "papers no paper-level query could have found")
  }

  # Articles across both tables, deduplicated by URL. Adding the two counts
  # would double every piece that named the paper and the notice, which most
  # of them do.
  if (!is.null(paper_news_posts) && nrow(paper_news_posts)) {
    n_paper_articles <- distinct_articles(paper_news_posts)
    add("news articles about the papers", n_paper_articles,
        sprintf("distinct by URL; %s post rows carry a URL already seen",
                format(nrow(paper_news_posts) - n_paper_articles, big.mark = ",")))
    # Only meaningful once the notice detail pass has produced articles with
    # URLs to match against. Until then there is nothing to deduplicate, and a
    # line reading "either identifier" beside a paper-only figure would be read
    # as the total.
    if (!is.null(news) && nrow(news) && "url" %in% names(news)) {
      n_notice_articles <- distinct_articles(news)
      n_all <- distinct_articles(paper_news_posts, news)
      add("news articles about the notices", n_notice_articles,
          "distinct by URL")
      add("distinct news articles about retracted work, either identifier", n_all,
          "an article filed under a paper and under its notice is one article")
      add("  ... double counting avoided by matching on URL",
          n_paper_articles + n_notice_articles - n_all)
    } else {
      add("distinct news articles, either identifier", NA_integer_,
          "needs the notice detail pass; the notice side has no URLs yet")
    }
  }

  list(summary = do.call(rbind, rows),
       by_notice = notice[notice$has_news,
                          intersect(c("retraction_doi", "n_papers", "news_count",
                                      "social_count", "is_mass"), names(notice))],
       by_paper = by_paper)
}
