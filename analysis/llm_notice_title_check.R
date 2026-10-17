# Does the missing paper title change the notice answers?
#
#   Rscript analysis/llm_notice_title_check.R [n]
#
# The corpus run (analysis/llm_classify_corpus.R, B-v2-d0f96a83) put each
# notice under the title of its paper, looked up by retraction DOI. But 32,124
# of the 85,896 notice DOIs are the ORIGINAL article's DOI - the notice text
# was taken from the retraction banner on the article page - so the lookup
# missed them and 29,820 of the 60,605 texts went to the model with no title,
# although the validated prompt always had one.
#
# This asks the same model the same prompt for a random sample of those texts,
# this time with the title (looked up by original DOI as well), and compares.
# The model runs at temperature 0 with a fixed seed, so any difference is the
# title's doing. Answers go to data/llm/checks/, the comparison to
# data/reports/llm-notice-title-check.csv.

source("R/llm.R")
source("R/llm_text.R")

args <- commandArgs(trailingOnly = TRUE)
n_sample <- if (length(args)) as.integer(args[1]) else 400L
rd <- function(f) utils::read.csv(f, colClasses = "character", encoding = "UTF-8")
blank <- function(x) is.na(x) | !nzchar(trimws(x))
NOTICE_CAP <- 3000L

# The corpus items, rebuilt exactly as llm_classify_corpus.R builds them.
n <- utils::read.csv(gzfile("data/clean/stage2_notices.csv.gz"),
                     colClasses = "character", encoding = "UTF-8")
n <- n[!blank(n$notice) & nchar(n$notice) >= 50, ]
n$notice <- decode_entities(n$notice)
n <- n[nchar(n$notice) >= 50, ]
n$notice <- substr(n$notice, 1, NOTICE_CAP)
n$id <- hash_id(n$notice)
rec <- rd("data/clean/records_clean.csv")
rec <- rec[order(rec$retraction_doi, rec$original_doi), ]
r <- rec[!blank(rec$retraction_doi), ]
by_notice <- tapply(r$title, r$retraction_doi, `[`, 1)
n <- n[order(n$doi), ]
items <- n[!duplicated(n$id), c("id", "doi", "notice")]
items$title_used <- unname(by_notice[items$doi])
items$title_used[is.na(items$title_used)] <- ""
# The lookup the corpus run should have made: by notice DOI, else by article DOI.
by_article <- rec$title[match(items$doi, rec$original_doi)]
items$paper_title <- ifelse(nzchar(items$title_used), items$title_used,
                            ifelse(is.na(by_article), "", by_article))
cat("texts:", nrow(items), " sent without a title:", sum(!nzchar(items$title_used)),
    " of which a title exists by article DOI:",
    sum(!nzchar(items$title_used) & nzchar(items$paper_title)), "\n")

pool <- items[!nzchar(items$title_used) & nzchar(items$paper_title), ]
set.seed(20260926)
s <- pool[sort(sample(nrow(pool), min(n_sample, nrow(pool)))), ]

version <- paste0("B-v2-", prompt_fingerprint(TASK_B_PROMPT, "", ""))
f <- file.path("data", "llm", "checks",
               sprintf("%s_B_title-check_%s.csv", gsub("[^A-Za-z0-9]+", "-", LLM_MODEL), version))
llm_classify(s[, c("id", "notice", "paper_title")], task_b_prompt_row, TASK_B_SCHEMA,
             f, version, report_every = 100L)

old <- task_b_enforce(llm_read(sprintf("data/llm/corpus/%s_B_%s.csv",
                                       gsub("[^A-Za-z0-9]+", "-", LLM_MODEL), version)))
new <- task_b_enforce(llm_read(f))
ok <- function(d) d[is.na(d$error) | !nzchar(d$error), ]
old <- ok(old); new <- ok(new)
ids <- intersect(s$id, intersect(old$id, new$id))
old <- old[match(ids, old$id), ]; new <- new[match(ids, new$id), ]
reasons <- grep("^R[0-9]_", names(new), value = TRUE)
as_l <- function(x) as.logical(x)
rows <- lapply(reasons, function(k) {
  a <- as_l(old[[k]]); b <- as_l(new[[k]])
  data.frame(measure = k, compared = length(ids), same = sum(a == b),
             agreement = round(mean(a == b), 4),
             without_title = sum(a), with_title = sum(b))
})
set_a <- apply(sapply(reasons, function(k) as_l(old[[k]])), 1, paste, collapse = "")
set_b <- apply(sapply(reasons, function(k) as_l(new[[k]])), 1, paste, collapse = "")
rows <- c(rows, list(
  data.frame(measure = "reason set identical", compared = length(ids),
             same = sum(set_a == set_b), agreement = round(mean(set_a == set_b), 4),
             without_title = NA, with_title = NA),
  data.frame(measure = "clarity", compared = length(ids),
             same = sum(old$clarity == new$clarity),
             agreement = round(mean(old$clarity == new$clarity), 4),
             without_title = NA, with_title = NA)))
res <- do.call(rbind, rows)
print(res, row.names = FALSE)
cat("\nclarity, without title (rows) x with title (columns):\n")
print(table(without = old$clarity, with = new$clarity))
dir.create("data/reports", showWarnings = FALSE)
utils::write.csv(res, "data/reports/llm-notice-title-check.csv", row.names = FALSE,
                 fileEncoding = "UTF-8")
cat("written: data/reports/llm-notice-title-check.csv\n")
