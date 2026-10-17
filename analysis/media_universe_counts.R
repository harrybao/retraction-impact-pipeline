# How many papers have media text we could analyse and label, by source.
#
#   Rscript analysis/media_universe_counts.R
#
# Sources with text (title/summary/link) and a date:
#   Altmetric news, Altmetric blogs, and Retraction Watch posts written about a
#   specific paper (the CSV's URLS column; a post cited for 5 papers or fewer).
# PlumX news is counts only - no text - so it cannot be classified or labelled.

clean <- file.path("data", "clean")
posts <- utils::read.csv(gzfile(file.path(clean, "altmetric_posts.csv.gz")),
                         colClasses = "character", encoding = "UTF-8")[c("doi", "kind", "posted_on")]
rec <- utils::read.csv(file.path(clean, "records_clean.csv"), colClasses = "character",
                       encoding = "UTF-8")[c("original_doi", "urls")]
rec <- rec[nzchar(rec$original_doi), ]

news <- unique(posts$doi[posts$kind == "news"])
blogs <- unique(posts$doi[posts$kind == "blogs"])

# Retraction Watch posts: /yyyy/mm/dd/slug, cited for 5 papers or fewer
pat <- "retractionwatch\\.(com|wordpress\\.com)/(\\d{4}/\\d{2}/\\d{2}/[^/?#; ]+)"
hits <- regmatches(rec$urls, gregexpr(pat, rec$urls, perl = TRUE))
pairs <- data.frame(doi = rep(rec$original_doi, lengths(hits)),
                    post = tolower(sub(".*?/(\\d{4}/.*)$", "\\1", unlist(hits), perl = TRUE)))
pairs <- unique(pairs)
per_post <- table(pairs$post)
rw <- unique(pairs$doi[pairs$post %in% names(per_post)[per_post <= 5]])

papers <- length(unique(rec$original_doi))
count <- function(x) c(papers = length(x), share = round(100 * length(x) / papers, 2))
out <- rbind(
  "Altmetric news" = count(news),
  "Altmetric blogs" = count(blogs),
  "Retraction Watch post about the paper" = count(rw),
  "news or blogs" = count(union(news, blogs)),
  "news or RW post" = count(union(news, rw)),
  "news or blogs or RW post" = count(Reduce(union, list(news, blogs, rw))))
posts_n <- c(news = sum(posts$kind == "news"), blogs = sum(posts$kind == "blogs"),
             rw_posts = length(unique(pairs$post[pairs$doi %in% rw])))

res <- data.frame(metric = rownames(out), papers = out[, "papers"], share_pct = out[, "share"],
                  denominator = paste(papers, "papers"), row.names = NULL)
res <- rbind(res, data.frame(metric = paste("items:", names(posts_n)), papers = posts_n,
                             share_pct = NA, denominator = "posts", row.names = NULL))
utils::write.csv(res, file.path("data", "reports", "media-universe-2026-09-18.csv"),
                 row.names = FALSE)
print(res, row.names = FALSE)
