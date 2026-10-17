# Social media: where most of the discussion is, and what the labellers have
# checked of it.
#
#   Rscript analysis/social_media.R [model]
#
# Run analysis/llm_classify_corpus.R A first. Reads only data/clean (the
# Altmetric posts) and data/llm/corpus (the model's saved answers): no model
# server and no labelling sheets. The validation figures below are not
# recomputed here: that needs the returned sheets in data/labelling, which are
# not version-controlled because they carry Altmetric text
# (analysis/labelling_agreement.R). They are copied, with the report and row
# each comes from, into VALIDATION, and tests/r/test_social_media.R checks every
# one against that report whenever it is present.
#
# This is separated from analysis/media_classification.R because the sources
# are not all checked to the same standard, and the standard each figure rests
# on has to travel with the figure: buried in a combined table, it is the first
# thing dropped from a slide. Every row below says which labelled sample covers
# its source and what that sample established.
#
# **How the model is checked.** It is held to a labeller's standard: set one
# person aside, let the other six vote, and score that person and the model
# against the same vote (R/llm_eval.R). "Validated" below means the model has
# been scored that way on a sample drawn from those sources, and came within a
# few points of a labeller. The second word is how far the labellers agree with
# each other, on Krippendorff's reading as analysis/labelling_agreement.R applies
# it: 0.80 and above reliable, 0.667 to 0.80 tentative conclusions only, below
# 0.667 not a basis for conclusions.
#
#   news and blogs  Round 1: 200 items, 50 from each of news and blogs, before
#                   and after, drawn at random (analysis/labelling_sample.R).
#                   "Says retracted or not": seven labellers alpha 0.776
#                   (labelling-agreement.csv) - tentative. The prompt the
#                   corpus was classified with (v2), on the 197 items its
#                   examples do not quote, matches the majority 93.1% of the
#                   time, a labeller 94.1% (llm-validation.csv). Those labels
#                   were given under protocol v1; v2's own test is round 2.
#                   Round 2, protocol v2: 127 of the 200 - the 87 round 1 split
#                   on and 40 checks, so harder than average by construction -
#                   alpha 0.648, model 90.5% against a labeller's 94.1%
#                   (labelling-round2.csv, labelling-round2-model.csv). The
#                   127 include A001, A002 and A005, the three items v2's own
#                   examples quote (TASK_A_V2_EXAMPLE_MARKERS, R/llm.R):
#                   analysis/llm_validate.R leaves them out of v2's score,
#                   analysis/labelling_round2_score.R does not.
#   social media    Round 2's social supplement: 100 items, 50 before and 50
#                   after, drawn at random by analysis/labelling_sample.R from
#                   the reddit, facebook, bluesky, googleplus and q&a posts with
#                   30 or more characters of text, and from nothing else (42,
#                   39, 12, 5 and 2 of the 100, data/labelling/key_A_social.csv).
#                   Seven labellers, "says retracted or not": alpha 0.860,
#                   pairwise 97.8% - reliable. The model's saved answers - the
#                   same file this script reads - match the majority 97.1% of
#                   the time, a labeller 98.6% (labelling-round2-model.csv).
#
# Both samples drew papers at random and one post from each, and only posts
# from before or after the retraction (pick_a() in analysis/labelling_sample.R).
# So a paper with many posts weighs no more in a sample than a paper with one,
# though it weighs more in the item counts below; and the 1,166 items posted on
# the retraction day (data/llm/corpus/items_A.csv) were in neither sample. They
# carry their source's group like every other item - the after tables leave
# them out anyway - and `same_day` in validated-vs-not says how many there are.
#
# The project log, 2026-10-06, draws the conclusion: media and social results
# are reported on "says retracted or not"; social media is reliable on it, and
# the news and blog figures carry the caveat that the labellers themselves fall
# short of 0.667 on round 2's harder items. Before round 2 this script called
# every social figure provisional. On "says retracted" that is no longer true:
# the labellers agree more on the social items than on news and blogs.
#
# Two limits on the social figure. It rests mostly on items nobody calls
# retracted: A2 is 8.6% of the social ratings (the project log, 2026-10-06),
# so most of the 97.1% is earned on the other nine items in ten. The model's
# Cohen kappa against the majority, which takes out the agreement that base
# rate gives for free, is 0.778, against 0.820 on round 1's news and blogs
# (labelling-round2-model.csv, llm-validation.csv). And on the whole corpus
# the A2 flag below is raised on 32.8% of the social A2 answers against 20.1%
# of the news and blog ones (social-media-a2-support-by-group.csv, 2026-10-13
# run) - a gap a sample with so few retracted items can neither confirm nor
# rule out. "Reliable" says how far the labellers agree with each other; how
# often the model is right on the retracted items themselves is measured on
# few of them.
#
# The supplement was also scored as one sample of 100, not source by source;
# five googleplus items or two q&a items would say nothing on their own. So a
# social source carries the group's status, and `labelled_items` says how much
# of the sample it supplied.
#
# **The five categories are provisional everywhere.** Labellers alpha 0.594 in
# round 1, 0.457 on round 2's media items and 0.490 on the social items - not a
# basis for conclusions on any of them - and the model is below a labeller on
# each: 61.0% against 77.3%, 71.1% against 74.8%, 63.5% against 75.9%.
# "Repeats the finding" (A1) is one of the five, so its columns are marked
# provisional for every labelled source. A1 alone, as a yes-or-no, was never
# scored.
#
# **Video, podcasts, LinkedIn and Pinterest were never labelled by anybody.**
# Round 1 drew from news and blogs only and the supplement from the five social
# sources above, so these four are still extended to on the assumption that the
# task is the same, and every figure for them says NOT validated. They are
# 2,259 of the 95,193 classified items (2.4%), and after the retraction 1,233
# of 36,219 (3.4%) - video 820, podcasts 413, LinkedIn and Pinterest none
# (social-media-validated-vs-not.csv, social-media-after-by-kind.csv,
# 2026-10-13 run).
#
# **Twitter is the largest blind spot in the project.** 1,281,535 posts over
# 14,028 papers - more papers than any other source - and in this collected
# snapshot every one carries an identifier and a date and no text at all. It cannot be
# classified, and it is excluded from every content table here. Its volume is
# reported because a reader who sees "social media" and thinks of Twitter should
# be told it is missing, not left to assume it is included. Weibo is the same.
#
# The items are those filed under paper DOIs, which is where both labelled
# samples were drawn from; posts filed under retraction-notice DOIs are joined
# in by analysis/media_with_notices.R.
#
# Writes data/reports/social-media-{source-universe,validation-basis,
# validated-vs-not,after-by-kind,after-by-group,papers-only-social,
# text-length-by-kind,a2-support-by-group}.csv

source("R/labelling.R")   # LABEL_A
source("R/llm.R")
source("R/llm_text.R")
source("R/llm_evidence.R")

# --------------------------------------------- which sample covers what
NEWS_BLOG_KINDS <- c("news", "blogs")
# The supplement's sampling frame: SOCIAL_KINDS in analysis/labelling_sample.R.
ROUND2_SOCIAL_KINDS <- c("reddit", "facebook", "bluesky", "googleplus", "q&a")
# Put to the model (MEDIA_KINDS in analysis/llm_classify_corpus.R) but in no
# labelled sample.
UNLABELLED_KINDS <- c("video", "podcasts", "linkedin", "pinterest")
NO_TEXT_KINDS <- c("twitter", "weibo")

# Labelled items each source supplied: the main sets of data/labelling/key_A.csv
# and key_A_social.csv. Round 2's media items are 127 of round 1's 200, so they
# add none. tests/r/test_social_media.R checks these against the keys.
LABELLED_ITEMS <- c(news = 100L, blogs = 100L, reddit = 42L, facebook = 39L,
                    bluesky = 12L, googleplus = 5L, "q&a" = 2L)
labelled_items <- function(kind) {
  unname(ifelse(kind %in% names(LABELLED_ITEMS), LABELLED_ITEMS[kind], 0L))
}

G_NEWS <- "news and blogs (says retracted: validated, tentative)"
G_SOCIAL <- "social media labelled in round 2 (says retracted: validated, reliable)"
G_UNLAB <- "video, podcasts and other unlabelled sources (NOT validated)"
G_NOTEXT <- "no text in collected snapshot"
G_OUT <- "other, out of scope"

GROUPS <- data.frame(
  group = c(G_NEWS, G_SOCIAL, G_UNLAB, G_NOTEXT, G_OUT),
  labelled_sample = c("round 1 media (200 items), round 2 media (127 of them)",
                      "round 2 social supplement (100 items)",
                      "none", "none", "none"),
  says_retracted = c("validated, tentative", "validated, reliable", "NOT validated",
                     "no text to classify", "not classified"),
  five_categories = c("provisional", "provisional", "NOT validated",
                      "no text to classify", "not classified"),
  stringsAsFactors = FALSE)

# Krippendorff's reading, with analysis/labelling_agreement.R's words.
alpha_band <- function(a) {
  unname(ifelse(is.na(a), "",
         ifelse(a >= 0.80, "reliable",
         ifelse(a >= 0.667, "tentative conclusions only", "not a basis for conclusions"))))
}

# What each group's status rests on, copied from the reports that computed it
# from the sheets. `source_file` and `source_task` name the rows; agreement is
# the leave-one-out mean of R/llm_eval.R. Every figure is for one model, the
# one chosen on 2026-09-25 and run over the corpus (the project log): answers
# from any other have been checked against nobody.
VALIDATED_MODEL <- "qwen3:14b"
VALIDATED_PROMPT <- "A-v2-77581c5c"
VALIDATION <- data.frame(
  group = rep(c(G_NEWS, G_NEWS, G_SOCIAL), 2),
  measure = rep(c("says retracted or not", "five categories"), each = 3),
  sample = rep(c("round 1: 197 of the 200 news and blog items, labelled under protocol v1",
                 paste("round 2: 127 of the 200, the 87 round 1 split on and 40 checks,",
                       "protocol v2; includes the 3 items v2's examples quote"),
                 "round 2 social supplement: 100 items, protocol v2"), 2),
  drawn_at_random = rep(c(TRUE, FALSE, TRUE), 2),
  items = rep(c(197L, 127L, 100L), 2),
  labellers = 7L,
  labellers_alpha = c(0.7743, 0.6480, 0.8604, 0.5976, 0.4571, 0.4901),
  model_agrees = c(0.9308, 0.9049, 0.9712, 0.6097, 0.7107, 0.6352),
  labeller_agrees = c(0.9414, 0.9405, 0.9856, 0.7731, 0.7485, 0.7587),
  kappa_model_vs_majority = c(0.8195, 0.6623, 0.7778, 0.4709, 0.4848, 0.4922),
  source_file = rep(c("llm-validation.csv", "labelling-round2-model.csv",
                      "labelling-round2-model.csv"), 2),
  source_task = c("A media v2, is the reader told it was retracted?",
                  "round-2 media, says retracted or not", "social, says retracted or not",
                  "A media v2, 5 categories", "round-2 media, five categories",
                  "social, five categories"),
  stringsAsFactors = FALSE)

# The table as written: one row per group, measure and sample, the labellers'
# band beside their alpha, the model's distance from a labeller in points, and
# a row saying outright that the unlabelled sources have no sample at all.
validation_basis <- function(v = VALIDATION) {
  v$model <- VALIDATED_MODEL
  v$labellers_band <- alpha_band(v$labellers_alpha)
  v$model_minus_labeller_points <- round(100 * (v$model_agrees - v$labeller_agrees), 1)
  none <- v[1, ]
  none[] <- NA
  none$group <- G_UNLAB
  none$measure <- "says retracted or not; five categories"
  none$sample <- "none: no labeller saw a video, podcast, LinkedIn or Pinterest item"
  none$items <- 0L
  none$labellers <- 0L
  none$labellers_band <- ""
  none$model <- ""
  none$source_file <- ""
  none$source_task <- ""
  out <- rbind(v, none)
  out[, c("group", "measure", "sample", "drawn_at_random", "items", "labellers",
          "labellers_alpha", "labellers_band", "model", "model_agrees", "labeller_agrees",
          "model_minus_labeller_points", "kappa_model_vs_majority", "source_file",
          "source_task")]
}

# The group of an Altmetric source. `classified = TRUE` is for items the model
# answered: a source in neither labelled sample is NOT validated whatever its
# name, so a kind added to the corpus later cannot slip into a validated group
# or out of the tables.
source_group <- function(kind, classified = FALSE) {
  unname(ifelse(kind %in% NEWS_BLOG_KINDS, G_NEWS,
         ifelse(kind %in% ROUND2_SOCIAL_KINDS, G_SOCIAL,
         ifelse(kind %in% UNLABELLED_KINDS | classified, G_UNLAB,
         ifelse(kind %in% NO_TEXT_KINDS, G_NOTEXT, G_OUT)))))
}
group_status <- function(group, what) GROUPS[[what]][match(group, GROUPS$group)]

# A share of nothing is NA, not NaN. ifelse() goes on the denominator only:
# around the whole quotient it returns as many values as `b` has, so counts
# over one total would come back as a single share.
pct <- function(a, b) round(100 * a / ifelse(b > 0, b, NA_real_), 1)
n_papers <- function(doi, by) vapply(split(doi, by), function(x) length(unique(x)), integer(1))

# ------------------------------------------------------------- the tables

# Every Altmetric source, with or without text.
source_universe <- function(posts, text) {
  u <- data.frame(kind = sort(unique(posts$kind)), stringsAsFactors = FALSE)
  u$posts <- as.integer(table(posts$kind)[u$kind])
  u$papers <- unname(n_papers(posts$doi, posts$kind)[u$kind])
  u$with_text <- unname(vapply(split(nchar(text) >= 30, posts$kind), sum, integer(1))[u$kind])
  u$group <- source_group(u$kind)
  u$labelled_sample <- group_status(u$group, "labelled_sample")
  u$labelled_items <- labelled_items(u$kind)
  u$says_retracted <- group_status(u$group, "says_retracted")
  u$five_categories <- group_status(u$group, "five_categories")
  u <- u[order(-u$posts, u$kind), ]
  rownames(u) <- NULL
  u
}

# Corpus items by group: how much of the classification rests on which sample.
# `answered` is FALSE for an item with no usable answer (the model's reply did
# not parse); those are counted, not silently dropped. `same_day` counts the
# answered items posted on the retraction day, a period neither labelled
# sample drew from.
SAME_DAY <- "same-day"    # relative_to_retraction in data/llm/corpus/items_A.csv
validated_vs_not <- function(d, answered = rep(TRUE, nrow(d))) {
  g <- data.frame(group = GROUPS$group[GROUPS$group %in% d$group], stringsAsFactors = FALSE)
  a <- d[answered, ]
  g$items <- as.integer(table(factor(a$group, g$group)))
  g$share_of_items <- pct(g$items, sum(g$items))
  g$papers <- unname(n_papers(a$doi, factor(a$group, g$group))[g$group])
  g$same_day <- as.integer(table(factor(a$group[a$relative_to_retraction %in% SAME_DAY],
                                        g$group)))
  g$not_answered <- as.integer(table(factor(d$group[!answered], g$group)))
  g$says_retracted <- group_status(g$group, "says_retracted")
  g$five_categories <- group_status(g$group, "five_categories")
  g
}

# Post-retraction items by `by` ("kind" or "group"). Each figure carries the
# status of the measure it rests on: told is "says retracted or not", repeats
# is A1, one of the five categories.
after_table <- function(aft, by) {
  key <- aft[[by]]
  k <- data.frame(x = sort(unique(key)), stringsAsFactors = FALSE)
  names(k) <- by
  if (by == "kind") k$group <- source_group(k$kind, classified = TRUE)
  k$labelled_sample <- group_status(k$group, "labelled_sample")
  if (by == "kind") k$labelled_items <- labelled_items(k$kind)
  k$items <- as.integer(table(key)[k[[by]]])
  k$a0_items <- unname(vapply(split(aft$label == "A0", key), sum, integer(1))[k[[by]]])
  k$judged_items <- k$items - k$a0_items
  k$papers <- unname(n_papers(aft$doi, key)[k[[by]]])
  k$told <- unname(vapply(split(aft$told, key), sum, integer(1))[k[[by]]])
  k$told_pct <- pct(k$told, k$items)
  k$told_pct_of_judged <- pct(k$told, k$judged_items)
  k$told_status <- group_status(k$group, "says_retracted")
  k$repeats_finding <- unname(vapply(split(aft$repeats, key), sum, integer(1))[k[[by]]])
  k$repeats_pct <- pct(k$repeats_finding, k$items)
  k$repeats_status <- group_status(k$group, "five_categories")
  if (by == "group") k <- k[order(match(k$group, GROUPS$group)), ]
  else k <- k[order(-k$items, k$kind), ]
  rownames(k) <- NULL
  k
}

# Papers whose selected classified items have no news or blog item - social media, or
# the unlabelled sources - and, within those, the part round 2 did not cover:
# papers whose discussion comes only from sources nobody labelled. The second
# set is part of the first. For those papers the classification is the only
# evidence there is.
papers_only_social <- function(d) {
  aft <- d[d$relative_to_retraction == "after", ]
  by_paper <- split(d$group, d$doi)
  no_news <- names(by_paper)[vapply(by_paper, function(g) !any(g == G_NEWS), logical(1))]
  no_valid <- names(by_paper)[vapply(by_paper, function(g) all(g == G_UNLAB), logical(1))]
  a0_only <- names(Filter(function(x) all(x == "A0"), split(d$label, d$doi)))
  os <- aft[aft$doi %in% no_news, ]
  ou <- aft[aft$doi %in% no_valid, ]
  data.frame(measure = c("papers with any classified item",
                         "papers with no news or blog item",
                         "... represented after the selected event",
                         "their representative after-event items (including A0)",
                         "... labelled A2 under the broader Q1 definition",
                         "papers with no item from a validated source",
                         "... represented after the selected event",
                         "their representative after-event items (including A0)",
                         "... labelled A2 under the broader Q1 definition",
                         "papers whose selected items are all A0 (unjudgeable)",
                         "... with no news or blog item in the selected corpus"),
             value = c(length(by_paper), length(no_news),
                       length(intersect(no_news, aft$doi)), nrow(os), sum(os$told),
                       length(no_valid), length(intersect(no_valid, aft$doi)),
                       nrow(ou), sum(ou$told), length(a0_only), length(intersect(a0_only, no_news))),
             stringsAsFactors = FALSE)
}

# How long the texts are, by source. The labellers read items of each source's
# own length only where a labelled sample drew from that source; for the four
# unlabelled sources this is the only check on whether the task is the same.
text_length_by_kind <- function(d) {
  s <- split(d$words, d$kind)
  len <- do.call(rbind, lapply(names(s), function(k) {
    x <- s[[k]][!is.na(s[[k]])]
    if (!length(x)) return(NULL)
    data.frame(kind = k, items = length(x), median_words = stats::median(x),
               q10 = unname(stats::quantile(x, .1)), q90 = unname(stats::quantile(x, .9)),
               stringsAsFactors = FALSE)
  }))
  len$labelled_sample <- group_status(source_group(len$kind, classified = TRUE), "labelled_sample")
  len$labelled_items <- labelled_items(len$kind)
  len <- len[order(-len$items, len$kind), c("kind", "labelled_sample", "labelled_items",
                                            "items", "median_words", "q10", "q90")]
  rownames(len) <- NULL
  len
}

# A2 answers with no listed retraction-word match: a lexical review flag,
# not proof of an unsupported answer and not an estimated error rate.
a2_support_by_group <- function(d, text) {
  sup <- a2_support(d$label, text)
  g <- GROUPS$group[GROUPS$group %in% d$group]
  x <- data.frame(group = g,
                  a2 = vapply(g, function(k) sum(d$label[d$group == k] == "A2"), integer(1)),
                  a2_no_retraction_word = vapply(g, function(k) sum(sup$suspect[d$group == k]),
                                                 integer(1)),
                  row.names = NULL, stringsAsFactors = FALSE)
  x$pct <- pct(x$a2_no_retraction_word, x$a2)
  x
}

# --------------------------------------------------------------- the run
run_social_media <- function(model = LLM_MODEL, clean = file.path("data", "clean"),
                             corpus = file.path("data", "llm", "corpus"),
                             out = Sys.getenv("CS43_REPORTS", file.path("data", "reports"))) {
  slug <- gsub("[^A-Za-z0-9]+", "-", model)
  f <- list.files(corpus, sprintf("^%s_A_A-v2-.*\\.csv$", slug), full.names = TRUE)
  if (length(f) != 1L) stop("need exactly one task A answer file for ", model,
                           " in ", corpus, call. = FALSE)
  answer_prompt <- sub("^.*_A_(A-v2-.*)\\.csv$", "\\1", basename(f))
  applicable <- model == VALIDATED_MODEL && answer_prompt == VALIDATED_PROMPT
  if (model != VALIDATED_MODEL) {
    warning("the validation figures are for ", VALIDATED_MODEL, "; the statuses in these ",
            "tables do not apply to answers from ", model, call. = FALSE)
  }
  if (model == VALIDATED_MODEL && !applicable) {
    warning("validation does not apply to prompt ", answer_prompt, call. = FALSE)
  }
  if (!nzchar(trimws(out))) stop("output directory must not be blank", call. = FALSE)
  dir.create(out, showWarnings = FALSE, recursive = TRUE)
  written <- character()
  w <- function(x, name) {
    x$answer_model <- rep(model, nrow(x))
    x$answer_prompt <- rep(answer_prompt, nrow(x))
    x$sampled_model_prompt_match <- rep(applicable, nrow(x))
    row_group <- if ("group" %in% names(x)) x$group else if ("kind" %in% names(x)) source_group(x$kind) else NULL
    x$validation_applicable <- if (is.null(row_group)) rep(NA, nrow(x)) else
      applicable & row_group %in% c(G_NEWS, G_SOCIAL)
    x$validation_scope <- rep("pooled sampled sources only; five-category labels remain provisional; mixed aggregates have no single status", nrow(x))
    x$population_scope <- rep("original-DOI corpus, all RW record natures", nrow(x))
    x$item_basis <- rep("one DOI/text; source priority then earliest date within that source", nrow(x))
    x$q1_scope <- rep("A2 includes withdrawal/removal/expression of concern, not just retraction", nrow(x))
    if (!applicable) {
      for (col in intersect(c("says_retracted", "five_categories", "told_status", "repeats_status"), names(x))) {
        x[[col]] <- rep("NOT validated for this model/prompt", nrow(x))
      }
      if ("group" %in% names(x)) {
        x$group[x$group == G_NEWS] <- "news and blogs (answers NOT validated for this model/prompt)"
        x$group[x$group == G_SOCIAL] <- "round-2 social sources (answers NOT validated for this model/prompt)"
      }
    }
    p <- file.path(out, sprintf("social-media-%s.csv", name))
    utils::write.csv(x, p, row.names = FALSE, fileEncoding = "UTF-8")
    cat("written:", p, "\n")
    written <<- c(written, p)
  }

  posts <- utils::read.csv(gzfile(file.path(clean, "altmetric_posts.csv.gz")),
                           colClasses = "character", encoding = "UTF-8")
  if (!"date_basis" %in% names(posts)) stop("posts need date_basis; rebuild the post export", call. = FALSE)
  prep <- prepare_item_text(posts$title, posts$summary)

  universe <- source_universe(posts, prep$text)
  cat("-- every Altmetric source --\n")
  print(universe[, c("kind", "group", "posts", "papers", "with_text")], row.names = FALSE)
  w(universe, "source-universe")
  cat("\nposts with no text at all in this collected snapshot:",
      sum(universe$posts[universe$group == G_NOTEXT]),
      "over", length(unique(posts$doi[posts$kind %in% NO_TEXT_KINDS])), "papers\n")

  basis <- validation_basis()
  cat("\n-- what each group's status rests on --\n")
  print(basis[, c("group", "measure", "items", "labellers_alpha", "labellers_band",
                  "model_agrees", "labeller_agrees")], row.names = FALSE)
  w(basis, "validation-basis")

  items <- utils::read.csv(file.path(corpus, "items_A.csv"), colClasses = "character",
                           encoding = "UTF-8")
  saved <- llm_read(f[1])
  if (("model" %in% names(saved) && anyNA(saved$model)) ||
      ("model" %in% names(saved) && any(saved$model != model)) ||
      ("prompt_version" %in% names(saved) &&
       (anyNA(saved$prompt_version) || any(saved$prompt_version != answer_prompt)))) {
    stop("answer metadata disagrees with the selected model/prompt", call. = FALSE)
  }
  ans <- saved[, c("id", "label", "evidence", "error")]
  byrule <- file.path(corpus, "answers_A_by_rule.csv")
  if (file.exists(byrule)) {
    r <- utils::read.csv(byrule, colClasses = "character", encoding = "UTF-8")
    ans <- rbind(ans, data.frame(id = r$id, label = r$label, evidence = r$evidence,
                                 error = NA_character_, stringsAsFactors = FALSE))
  }
  # One answer per item. llm_read() keeps the last of the model's own retries,
  # but an item answered both by the model and by rule would be counted twice
  # by the merge below.
  if (anyDuplicated(ans$id)) stop("more than one answer for an item in ", corpus, call. = FALSE)
  all_items <- merge(items, ans, by = "id", all.x = TRUE)
  all_items$group <- source_group(all_items$kind, classified = TRUE)
  answered <- !is.na(all_items$label) & is.na(all_items$error)
  d <- all_items[answered, ]
  stopifnot(all(d$label %in% LABEL_A))
  d$told <- d$label == "A2"
  d$repeats <- d$label == "A1"
  # Each item's text, found by the id the corpus script gave it.
  keep <- posts$kind %in% unique(d$kind)
  m <- match(d$id, item_id_A(posts$doi[keep], prep$title[keep], prep$summary[keep]))
  if (anyNA(m)) stop("an answered corpus item has no matching prepared text", call. = FALSE)
  d$date_basis <- posts$date_basis[keep][m]
  if (anyNA(d$date_basis) || any(!d$date_basis %in%
      c("earliest Retraction record", "earliest notice, no Retraction record"))) {
    stop("unknown selected-event date basis", call. = FALSE)
  }
  d$words <- prep$words[keep][m]
  text <- ifelse(is.na(m), "", prep$text[keep][m])

  cat("\n-- how much of the classification rests on which labelled sample --\n")
  g <- validated_vs_not(all_items, answered)
  print(g[, c("group", "items", "share_of_items", "papers", "same_day", "not_answered")],
        row.names = FALSE)
  w(g, "validated-vs-not")

  aft <- d[d$relative_to_retraction == "after", ]
  ka <- after_table(aft, "kind")
  cat("\n-- representative items AFTER the selected event, by source (A0 included) --\n")
  print(ka[, c("kind", "items", "papers", "told_pct", "told_status", "repeats_pct",
               "repeats_status")], row.names = FALSE)
  w(ka, "after-by-kind")

  ga <- after_table(aft, "group")
  cat("\n-- representative after-event items, by group --\n")
  print(ga[, c("group", "items", "told", "told_pct", "repeats_finding", "repeats_pct")],
        row.names = FALSE)
  w(ga, "after-by-group")
  date_rows <- do.call(rbind, lapply(split(d, d$date_basis), function(x) {
    data.frame(date_basis = x$date_basis[1], items = nrow(x), papers = length(unique(x$doi)),
               after_items = sum(x$relative_to_retraction == "after"),
               after_a0_items = sum(x$relative_to_retraction == "after" & x$label == "A0"))
  }))
  w(date_rows, "date-basis")
  ret_aft <- aft[aft$date_basis == "earliest Retraction record", ]
  w(after_table(ret_aft, "group"), "after-retraction-record-by-group")

  cat("\n-- DOIs with no news/blog item in this selected classified corpus (A0 included) --\n")
  po <- papers_only_social(d)
  print(po, row.names = FALSE)
  w(po, "papers-only-social")

  cat("\n-- length of what the model was asked to judge --\n")
  len <- text_length_by_kind(d)
  print(len, row.names = FALSE)
  w(len, "text-length-by-kind")

  cat("\n-- A2 answers flagged for no listed retraction-word match (not an error rate) --\n")
  chk <- a2_support_by_group(d, text)
  print(chk, row.names = FALSE)
  w(chk, "a2-support-by-group")

  if (applicable) {
  cat("\n\"Says retracted or not\" is validated for news and blogs (labellers tentative)\n")
  cat("and for the five social sources round 2 labelled (labellers reliable). The\n")
  cat("five categories, and so \"repeats the finding\", are provisional for every\n")
  cat("source. Video, podcasts, LinkedIn and Pinterest were never labelled: every\n")
  cat("figure for them is NOT validated.\n")
  } else cat("\nThese model/prompt answers have NOT been validated.\n")
  invisible(written)
}

# Sourced by tests/r/test_social_media.R for its functions only.
if (!isTRUE(getOption("cs43.functions_only"))) {
  args <- commandArgs(trailingOnly = TRUE)
  run_social_media(model = if (length(args)) args[1] else LLM_MODEL)
}
