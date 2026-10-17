# Classify every media item and every retraction notice with the local model.
#
#   Rscript analysis/llm_classify_corpus.R [A|B|both] [limit]
#
# Run analysis/llm_validate.R first: it is what says the model is good enough
# and which one to use. The model comes from CS43_LLM_MODEL (default in
# R/llm.R). Resumable - stop it at any point and run it again, and it carries
# on from the last answer written.
#
# What an "item" is decides what gets counted, so it is fixed here:
#
# **Task A, media.** One item per (paper, text). The same words attached to two
# papers are two items, because question Q3 asks whether the text states what
# THIS paper found, and the answer can differ by paper. The same words posted
# twice about one paper are one item - asking twice would only cost time.
#
# Items with no readable text are not asked at all: they are A0 by the
# protocol's first question, and R/llm_text.R explains what they are and why
# the rule is trusted. They are still coverage and still counted as coverage;
# it is only their text that is missing.
#
# **Task B, notices.** One item per distinct notice text. The prompt contains
# the notice and nothing else, so the same words get the same answer, and
# publisher boilerplate is very repetitive: 85,896 notices are 61,169 texts. A
# map file carries each answer back to every notice DOI with that text.
#
# Notices are cut at 3,000 characters. The labellers never saw a longer one -
# every notice in the validation sample is at most 3,000 - so the model is
# validated only on that length, and the few much longer ones (up to 37,341
# characters) would not fit the context window anyway. How many were cut is
# reported.
#
# Media kinds run in order: news and blogs first, because those are what the
# first validation measured; the social sources after. Round 2 now labels five
# social sources; analysis/social_media.R separates validated and unvalidated
# sources and the limits of the five-category result;
# nothing else. Patents, policy documents, Wikipedia and peer-review sites cite
# papers, but they are not coverage of a paper in the sense the client asked
# about, and none were labelled.

source("R/llm.R")
source("R/llm_text.R")

args <- commandArgs(trailingOnly = TRUE)
task <- if (length(args)) toupper(args[1]) else "BOTH"
limit <- if (length(args) > 1) as.integer(args[2]) else NULL
stopifnot(task %in% c("A", "AN", "B", "BOTH"))

# The sources classified. "q&a" belongs here and was missing: the social
# round-2 supplement sampled Q&A as a social source, and
# analysis/social_media.R counts it as one, but this list did not - so 197
# distinct Q&A items over 133 papers were never put to the model, and a
# breakdown by source would have shown Q&A as empty rather than as unasked.
MEDIA_KINDS <- c("news", "blogs",                               # validated
                 "reddit", "facebook", "bluesky", "googleplus",  # social supplement
                 "q&a", "linkedin", "pinterest", "video", "podcasts")
NOTICE_CAP <- 3000L
clean <- file.path("data", "clean")
out <- file.path("data", "llm", "corpus")
dir.create(out, showWarnings = FALSE, recursive = TRUE)
blank <- function(x) is.na(x) | !nzchar(trimws(x))

# A content hash as the id: the same item gets the same id on every run,
# whatever order the source rows arrive in, so a resume never asks twice.
hash_id <- function(x) {
  vapply(x, function(s) substr(unname(tools::md5sum(bytes = charToRaw(enc2utf8(s)))), 1, 16),
         character(1), USE.NAMES = FALSE)
}

if (!llm_available()) stop("no model server at ", LLM_HOST, call. = FALSE)
if (!LLM_MODEL %in% llm_models()) stop(LLM_MODEL, " is not installed", call. = FALSE)
slug <- gsub("[^A-Za-z0-9]+", "-", LLM_MODEL)
cat("model:", LLM_MODEL, " context:", LLM_NUM_CTX, " batch:", LLM_BATCH, "\n")

# ------------------------------------------------------------------ task A --
if (task %in% c("A", "BOTH")) {
  rec <- utils::read.csv(file.path(clean, "records_clean.csv"), colClasses = "character",
                         encoding = "UTF-8")
  paper_title <- tapply(rec$title, rec$original_doi, `[`, 1)
  p <- utils::read.csv(gzfile(file.path(clean, "altmetric_posts.csv.gz")),
                       colClasses = "character", encoding = "UTF-8")
  p$title[is.na(p$title)] <- ""
  p$summary[is.na(p$summary)] <- ""
  # Same threshold as the labelling sample: at least 30 characters of text.
  p <- p[nchar(trimws(paste(p$title, p$summary))) >= 30, ]
  cat("\ntask A: posts with text", nrow(p), "\n")
  print(table(ifelse(p$kind %in% MEDIA_KINDS, "in scope", "out of scope")))
  p <- p[p$kind %in% MEDIA_KINDS, ]

  # The text as the model will read it: entities decoded, markup gone, and the
  # items that carry no text at all identified. See R/llm_text.R.
  prep <- prepare_item_text(p$title, p$summary)
  p$title <- prep$title
  p$summary <- prep$summary
  p$unreadable <- prep$unreadable
  p$placeholder <- prep$placeholder

  p$id <- hash_id(paste(p$doi, p$title, p$summary, sep = "\t"))
  n_posts <- table(p$id)
  p <- p[order(match(p$kind, MEDIA_KINDS), p$doi, p$posted_on), ]
  items <- p[!duplicated(p$id), c("id", "doi", "kind", "relative_to_retraction",
                                  "title", "summary", "unreadable", "placeholder")]
  items$n_posts <- as.integer(n_posts[items$id])
  items$paper_title <- unname(paper_title[items$doi])
  cat("items (paper, text):", nrow(items), "\n")
  print(table(factor(items$kind, MEDIA_KINDS)))
  utils::write.csv(items[, c("id", "doi", "kind", "relative_to_retraction", "n_posts",
                             "unreadable", "placeholder")],
                   file.path(out, "items_A.csv"), row.names = FALSE, fileEncoding = "UTF-8")

  # An item with no readable text is A0 by the protocol's own first question,
  # so it is answered here rather than asked. On the 200 validated items this
  # rule and the seven labellers agree on every one: 12 unanimous A0 and one
  # 6-of-7. Asking the model instead would cost 10,556 calls and, in round 1,
  # got them wrong - it invented evidence rather than answer A0.
  cat("no readable text (answered A0 by rule, not asked):", sum(items$unreadable),
      "of which Altmetric placeholders:", sum(items$placeholder), "\n")
  fixed <- items[items$unreadable, ]
  if (nrow(fixed)) {
    utils::write.csv(data.frame(id = fixed$id, label = "A0",
                                evidence = "", rule = ifelse(fixed$placeholder,
                                  "no title or summary recorded", "under 10 words"),
                                stringsAsFactors = FALSE),
                     file.path(out, "answers_A_by_rule.csv"), row.names = FALSE,
                     fileEncoding = "UTF-8")
  }
  items <- items[!items$unreadable, ]
  cat("to classify:", nrow(items), "\n")

  version <- paste0("A-v2-", prompt_fingerprint(TASK_A_PROMPT, "", "", ""))
  llm_classify(items, task_a_prompt_row, TASK_A_SCHEMA,
               file.path(out, sprintf("%s_A_%s.csv", slug, version)), version,
               limit = limit, report_every = 1000L)
}

# ------------------------------------------------------ task A, notice DOIs --
# The media posts Altmetric files under a retraction NOTICE's DOI
# (scripts/collect_altmetric_notice_detail.py, exported by
# scripts/export_notice_news.py). Same question, same prompt, same rules as
# task A - only the key differs: an item is one (notice, text), and the title
# the model reads above it is the retracted paper's, taken from the first paper
# (by original DOI) that the notice retracts, as task B does. The answers go to
# their own file, so the paper-side results are untouched and the two can be
# merged under R/notice_news.R's one-article-once rule.
if (task == "AN") {
  rec <- utils::read.csv(file.path(clean, "records_clean.csv"), colClasses = "character",
                         encoding = "UTF-8")
  rec <- rec[order(rec$retraction_doi, rec$original_doi), ]
  r <- rec[!blank(rec$retraction_doi), ]
  by_notice <- tapply(r$title, r$retraction_doi, `[`, 1)
  by_article <- tapply(rec$title, rec$original_doi, `[`, 1)
  p <- utils::read.csv(gzfile(file.path(clean, "altmetric_notice_posts.csv.gz")),
                       colClasses = "character", encoding = "UTF-8")
  p$title[is.na(p$title)] <- ""
  p$summary[is.na(p$summary)] <- ""
  p <- p[nchar(trimws(paste(p$title, p$summary))) >= 30, ]
  cat("\ntask AN: notice posts with text", nrow(p), "\n")
  p <- p[p$kind %in% MEDIA_KINDS, ]
  prep <- prepare_item_text(p$title, p$summary)
  p$title <- prep$title
  p$summary <- prep$summary
  p$unreadable <- prep$unreadable
  p$placeholder <- prep$placeholder
  p$id <- hash_id(paste(p$retraction_doi, p$title, p$summary, sep = "\t"))
  n_posts <- table(p$id)
  p <- p[order(match(p$kind, MEDIA_KINDS), p$retraction_doi, p$posted_on), ]
  items <- p[!duplicated(p$id), c("id", "retraction_doi", "kind", "posted_on",
                                  "title", "summary", "unreadable", "placeholder")]
  items$n_posts <- as.integer(n_posts[items$id])
  t1 <- unname(by_notice[items$retraction_doi])
  t2 <- unname(by_article[items$retraction_doi])
  items$paper_title <- ifelse(!is.na(t1), t1, ifelse(is.na(t2), "", t2))
  cat("items (notice, text):", nrow(items), " without a paper title:",
      sum(!nzchar(items$paper_title)), "\n")
  print(table(factor(items$kind, MEDIA_KINDS)))
  utils::write.csv(items[, c("id", "retraction_doi", "kind", "posted_on", "n_posts",
                             "unreadable", "placeholder")],
                   file.path(out, "items_AN.csv"), row.names = FALSE, fileEncoding = "UTF-8")
  fixed <- items[items$unreadable, ]
  if (nrow(fixed)) {
    utils::write.csv(data.frame(id = fixed$id, label = "A0",
                                evidence = "", rule = ifelse(fixed$placeholder,
                                  "no title or summary recorded", "under 10 words"),
                                stringsAsFactors = FALSE),
                     file.path(out, "answers_AN_by_rule.csv"), row.names = FALSE,
                     fileEncoding = "UTF-8")
  }
  items <- items[!items$unreadable, ]
  cat("to classify:", nrow(items), "\n")
  version <- paste0("A-v2-", prompt_fingerprint(TASK_A_PROMPT, "", "", ""))
  llm_classify(items, task_a_prompt_row, TASK_A_SCHEMA,
               file.path(out, sprintf("%s_AN_%s.csv", slug, version)), version,
               limit = limit, report_every = 1000L)
}

# ------------------------------------------------------------------ task B --
if (task %in% c("B", "BOTH")) {
  n <- utils::read.csv(gzfile(file.path(clean, "stage2_notices.csv.gz")),
                       colClasses = "character", encoding = "UTF-8")
  # Same threshold as the labelling sample: at least 50 characters.
  n <- n[!blank(n$notice) & nchar(n$notice) >= 50, ]
  # Notices come from extracted publisher HTML, so they carry entities too.
  n$notice <- decode_entities(n$notice)
  n <- n[nchar(n$notice) >= 50, ]
  n$cut <- nchar(n$notice) > NOTICE_CAP
  n$notice <- substr(n$notice, 1, NOTICE_CAP)
  n$id <- hash_id(n$notice)
  cat("\ntask B: notices", nrow(n), " cut at", NOTICE_CAP, "characters:", sum(n$cut), "\n")
  utils::write.csv(data.frame(retraction_doi = n$doi, id = n$id, cut = n$cut),
                   file.path(out, "items_B_map.csv"), row.names = FALSE, fileEncoding = "UTF-8")
  # The labellers saw each notice under its paper's title, and the validated
  # prompt does the same, so the corpus prompt carries one too: the title of
  # the first paper (by notice DOI) that the text belongs to. The reasons and
  # the clarity are read from the notice, and the id is the notice text alone,
  # so which paper's title stands above a shared text does not change what is
  # being asked.
  #
  # The title is looked up by the notice's DOI and, failing that, by the
  # article's: 32,124 of the notice DOIs are the retracted article's own - the
  # text is the retraction banner on the article page. The first corpus run
  # looked up by notice DOI only, and 29,820 texts went to the model with no
  # title. analysis/llm_notice_title_check.R measured what that cost on 400 of
  # them: the same reason set on 97.0%, the same clarity on 95.3%. Small, but
  # not the validated prompt, so those texts were asked again.
  rec <- utils::read.csv(file.path(clean, "records_clean.csv"), colClasses = "character",
                         encoding = "UTF-8")
  rec <- rec[order(rec$retraction_doi, rec$original_doi), ]
  r <- rec[!blank(rec$retraction_doi), ]
  title_of_notice <- tapply(r$title, r$retraction_doi, `[`, 1)
  n <- n[order(n$doi), ]
  items <- n[!duplicated(n$id), c("id", "doi", "notice")]
  by_notice <- unname(title_of_notice[items$doi])
  by_notice[is.na(by_notice)] <- ""
  by_article <- rec$title[match(items$doi, rec$original_doi)]
  items$paper_title <- ifelse(nzchar(by_notice), by_notice,
                              ifelse(is.na(by_article), "", by_article))
  cat("distinct texts:", nrow(items), " without a paper title:",
      sum(!nzchar(items$paper_title)), "\n")
  # Texts the paper-level results rest on go first - a paper's notice page, or
  # the article-page banner when the paper has no notice page - so a run that
  # stops early leaves the paper tables complete and only notice counts short.
  has_page <- r$retraction_doi %in% n$doi
  used <- c(r$retraction_doi[has_page], r$original_doi[!has_page & !blank(r$original_doi)])
  items <- items[order(!items$id %in% n$id[n$doi %in% used]), ]

  version <- paste0("B-v2-", prompt_fingerprint(TASK_B_PROMPT, "", ""))
  f <- file.path(out, sprintf("%s_B_%s.csv", slug, version))
  llm_classify(items, task_b_prompt_row, TASK_B_SCHEMA, f, version,
               limit = limit, report_every = 1000L)
  # The protocol's "use R0 alone" is a constraint, not a judgement, so it is
  # applied to the answers rather than left to the model. Written beside the
  # raw answers, never over them.
  if (file.exists(f)) {
    d <- task_b_enforce(llm_read(f))
    cat("R0 corrected by the protocol's 'use R0 alone' rule:",
        sum(d$fixed_R0 & is.na(d$error), na.rm = TRUE), "of", sum(is.na(d$error)), "\n")
    utils::write.csv(d, sub("\\.csv$", "_enforced.csv", f), row.names = FALSE,
                     fileEncoding = "UTF-8")
  }
}
