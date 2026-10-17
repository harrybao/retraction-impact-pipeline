# Media after retraction, with the posts filed under retraction-notice DOIs
# added to those filed under paper DOIs.
#
#   Rscript analysis/media_with_notices.R
#
# Run after analysis/llm_classify_corpus.R A and AN. Both halves were asked the
# same question with the same prompt; they differ in what they are filed
# under. Joining them follows R/notice_news.R:
#
#   * an ARTICLE is one piece, identified by its normalised URL (or its post id
#     when it has none) - one filed under a paper and under its notice is one
#     article, and it says the paper was retracted if any of its answers does;
#   * a PAPER counts once, whether it was mentioned directly or through its
#     notice, and papers reached only through a notice retracting 10 or more
#     papers are counted apart, because one article can be the whole reason.
#
# "After retraction" is the paper's own retraction date for posts filed under
# the paper, and the notice's date for posts filed under the notice.
#
# Writes data/reports/media-with-notices.csv

source("R/llm.R")
source("R/llm_text.R")
source("R/notice_news.R")

CORPUS <- file.path("data", "llm", "corpus")
clean <- file.path("data", "clean")
slug <- gsub("[^A-Za-z0-9]+", "-", LLM_MODEL)
MEDIA_KINDS <- c("news", "blogs", "reddit", "facebook", "bluesky", "googleplus",
                 "q&a", "linkedin", "pinterest", "video", "podcasts")

answers <- function(tag) {
  f <- list.files(CORPUS, sprintf("^%s_%s_A-v2-.*[.]csv$", slug, tag), full.names = TRUE)
  a <- llm_read(f[1])[, c("id", "label", "error")]
  a <- a[is.na(a$error), c("id", "label")]
  r <- utils::read.csv(file.path(CORPUS, sprintf("answers_%s_by_rule.csv", tag)),
                       colClasses = "character", encoding = "UTF-8")
  rbind(a, r[, c("id", "label")])
}
lab_p <- answers("A")
lab_n <- answers("AN")
cat("answers: paper side", nrow(lab_p), " notice side", nrow(lab_n), "\n")

# Posts, prepared exactly as the corpus script prepared them, so each post
# finds the item it was asked as.
prep_posts <- function(p, key) {
  p$title[is.na(p$title)] <- ""
  p$summary[is.na(p$summary)] <- ""
  p <- p[nchar(trimws(paste(p$title, p$summary))) >= 30 & p$kind %in% MEDIA_KINDS, ]
  pr <- prepare_item_text(p$title, p$summary)
  p$id <- hash_id(paste(p[[key]], pr$title, pr$summary, sep = "\t"))
  p$article <- article_key(p$url, p$post_id)
  p
}
pp <- utils::read.csv(gzfile(file.path(clean, "altmetric_posts.csv.gz")),
                      colClasses = "character", encoding = "UTF-8")
pp <- prep_posts(pp, "doi")
pp$label <- lab_p$label[match(pp$id, lab_p$id)]
pp$period <- pp$relative_to_retraction
np <- utils::read.csv(gzfile(file.path(clean, "altmetric_notice_posts.csv.gz")),
                      colClasses = "character", encoding = "UTF-8")
np <- prep_posts(np, "retraction_doi")
np$label <- lab_n$label[match(np$id, lab_n$id)]

rec <- utils::read.csv(file.path(clean, "records_clean.csv"), colClasses = "character",
                       encoding = "UTF-8")
rec <- rec[!is.na(rec$retraction_doi) & nzchar(rec$retraction_doi), ]
rdate <- tapply(substr(rec$retraction_date, 1, 10), rec$retraction_doi, min)
nd <- unname(rdate[np$retraction_doi])
day <- substr(np$posted_on, 1, 10)
np$period <- ifelse(is.na(nd) | !nzchar(day), NA,
                    ifelse(day < nd, "before", ifelse(day == nd, "same-day", "after")))
cat("posts with an answer: paper side", sum(!is.na(pp$label)), "of", nrow(pp),
    "; notice side", sum(!is.na(np$label)), "of", nrow(np), "\n")

# --- articles after retraction ------------------------------------------------
all_periods <- rbind(
  data.frame(side = "paper", article = pp$article, kind = pp$kind, period = pp$period,
             label = pp$label, stringsAsFactors = FALSE),
  data.frame(side = "notice", article = np$article, kind = np$kind, period = np$period,
             label = np$label, stringsAsFactors = FALSE))
all_periods <- all_periods[!is.na(all_periods$label), ]

# The same, one row per (source, period), for the figures: each article once
# within its source and period, "says retracted" if any of its answers does.
by_kp <- do.call(rbind, lapply(split(all_periods, list(all_periods$kind, all_periods$period),
                                     drop = TRUE), function(d) {
  told <- tapply(d$label == "A2", d$article, any)
  data.frame(kind = d$kind[1], period = d$period[1], articles = length(told),
             say_retracted = sum(told), share_pct = round(100 * mean(told), 1))
}))
by_kp <- by_kp[order(by_kp$kind, by_kp$period), ]
utils::write.csv(by_kp, file.path("data", "reports", "media-with-notices-by-kind.csv"),
                 row.names = FALSE, fileEncoding = "UTF-8")
by_p <- do.call(rbind, lapply(split(all_periods, all_periods$period), function(d) {
  told <- tapply(d$label == "A2", d$article, any)
  data.frame(period = d$period[1], articles = length(told), say_retracted = sum(told),
             share_pct = round(100 * mean(told), 1))
}))
utils::write.csv(by_p, file.path("data", "reports", "media-with-notices-by-period.csv"),
                 row.names = FALSE, fileEncoding = "UTF-8")

both <- all_periods[all_periods$period %in% "after", ]
art <- function(d) {
  told <- tapply(d$label == "A2", d$article, any)
  c(articles = length(told), told = sum(told), share = round(100 * mean(told), 1))
}
rows <- list()
add <- function(scope, sub, d) {
  a <- art(d)
  rows[[length(rows) + 1L]] <<- data.frame(scope = scope, subset = sub,
                                            articles_after = a[["articles"]],
                                            say_retracted = a[["told"]],
                                            share_pct = a[["share"]])
}
add("paper DOIs only", "all sources", both[both$side == "paper", ])
add("paper DOIs only", "news", both[both$side == "paper" & both$kind == "news", ])
add("notice DOIs only", "all sources", both[both$side == "notice", ])
add("notice DOIs only", "news", both[both$side == "notice" & both$kind == "news", ])
add("paper and notice DOIs, each article once", "all sources", both)
add("paper and notice DOIs, each article once", "news", both[both$kind == "news", ])
add("paper and notice DOIs, each article once", "news and blogs",
    both[both$kind %in% c("news", "blogs"), ])
res <- do.call(rbind, rows)
cat("\n-- articles published after retraction --\n")
print(res, row.names = FALSE)

# --- papers ---------------------------------------------------------------------
map <- unique(rec[!is.na(rec$original_doi) & nzchar(rec$original_doi),
                  c("retraction_doi", "original_doi")])
n_papers <- table(map$retraction_doi)
pa <- pp[!is.na(pp$label) & pp$period %in% "after", c("doi", "label")]
na <- np[!is.na(np$label) & np$period %in% "after", c("retraction_doi", "label")]
na <- merge(na, map, by = "retraction_doi")
na$mass <- as.integer(n_papers[na$retraction_doi]) >= MASS_NOTICE_AT
direct <- unique(pa$doi)
via_small <- setdiff(unique(na$original_doi[!na$mass]), direct)
via_mass <- setdiff(unique(na$original_doi[na$mass]), c(direct, via_small))
told_papers <- unique(c(pa$doi[pa$label == "A2"], na$original_doi[na$label == "A2" & !na$mass]))
told_mass <- setdiff(unique(na$original_doi[na$label == "A2" & na$mass]), told_papers)
cover <- c(direct, via_small)
pap <- data.frame(
  measure = c("papers mentioned after retraction, paper DOIs",
              "  added through notices retracting fewer than 10 papers",
              "  added only through mass-retraction notices (reported apart)",
              "papers mentioned after retraction, without mass-only",
              "  ever described as retracted",
              "  never described as retracted",
              "mass-only papers described as retracted"),
  value = c(length(direct), length(via_small), length(via_mass), length(cover),
            length(intersect(told_papers, cover)), length(setdiff(cover, told_papers)),
            length(intersect(told_mass, via_mass))))
cat("\n-- papers --\n")
print(pap, row.names = FALSE)

out <- rbind(
  data.frame(section = "articles after retraction",
             measure = paste(res$scope, res$subset, sep = " / "),
             articles = res$articles_after, say_retracted = res$say_retracted,
             share_pct = res$share_pct),
  data.frame(section = "papers", measure = pap$measure, articles = pap$value,
             say_retracted = NA, share_pct = NA))
utils::write.csv(out, file.path("data", "reports", "media-with-notices.csv"), row.names = FALSE,
                 fileEncoding = "UTF-8")
cat("written: data/reports/media-with-notices.csv\n")
