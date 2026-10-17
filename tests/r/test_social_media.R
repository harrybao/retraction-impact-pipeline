# Checks for analysis/social_media.R. Run from the project root:
#   Rscript tests/r/test_social_media.R
#
# What has to stay true: round 2's social supplement validated "says retracted
# or not" for exactly five social sources (reddit, facebook, bluesky,
# googleplus, q&a), with labellers in Krippendorff's reliable band; round 1
# validated it for news and blogs with labellers in the tentative band; the
# five categories were validated for none; and video, podcasts, LinkedIn and
# Pinterest were labelled by nobody. A table that calls an unlabelled source
# validated, or drops the warning from a figure, is wrong in a way the numbers
# themselves would not show. The figures the statuses rest on are copied into
# the script, so the last section checks each against the report it came from.

options(cs43.functions_only = TRUE)
source("analysis/social_media.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}

# -- which group, which status -----------------------------------------------
ok("the five round-2 social sources form one group",
   all(source_group(c("reddit", "facebook", "bluesky", "googleplus", "q&a")) == G_SOCIAL))
ok("news and blogs form another", all(source_group(c("news", "blogs")) == G_NEWS))
ok("video, podcasts, linkedin, pinterest are the unlabelled group",
   all(source_group(c("video", "podcasts", "linkedin", "pinterest")) == G_UNLAB))
ok("twitter and weibo have no text", all(source_group(c("twitter", "weibo")) == G_NOTEXT))
ok("a source the model never read is out of scope", source_group("patent") == G_OUT)
ok("an unknown source the model DID answer is NOT validated, never out of the tables",
   source_group("tiktok", classified = TRUE) == G_UNLAB)
ok("says retracted: reddit reliable, news tentative, video NOT validated",
   identical(group_status(source_group(c("reddit", "news", "video")), "says_retracted"),
             c("validated, reliable", "validated, tentative", "NOT validated")))
ok("the five categories are validated for no group",
   !any(grepl("^validated", GROUPS$five_categories)))
ok("the five categories are provisional where they were labelled",
   all(group_status(c(G_NEWS, G_SOCIAL), "five_categories") == "provisional"))
ok("every group name says its status, so it survives being copied alone",
   grepl("validated, tentative", G_NEWS) && grepl("validated, reliable", G_SOCIAL) &&
     grepl("NOT validated", G_UNLAB))
ok("labelled items: q&a supplied 2, news 100, video none",
   identical(labelled_items(c("q&a", "news", "video", "twitter")), c(2L, 100L, 0L, 0L)))

# -- Krippendorff's bands, as analysis/labelling_agreement.R reads them -------
ok("alpha 0.80 is reliable, just under is tentative",
   identical(alpha_band(c(0.80, 0.7999)), c("reliable", "tentative conclusions only")))
ok("alpha 0.667 is tentative, just under is not a basis",
   identical(alpha_band(c(0.667, 0.6669)),
             c("tentative conclusions only", "not a basis for conclusions")))
ok("no alpha, no band", alpha_band(NA_real_) == "")

# -- the statuses follow from the figures -------------------------------------
vb <- validation_basis()
random_told <- vb[vb$measure == "says retracted or not" & vb$drawn_at_random %in% TRUE, ]
ok("says retracted: one drawn-at-random sample for each labelled group",
   setequal(random_told$group, c(G_NEWS, G_SOCIAL)) && nrow(random_told) == 2)
ok("the social group's status word is its labellers' band (reliable)",
   random_told$labellers_band[random_told$group == G_SOCIAL] == "reliable" &&
     grepl("reliable$", group_status(G_SOCIAL, "says_retracted")))
ok("the news group's status word is its labellers' band (tentative)",
   random_told$labellers_band[random_told$group == G_NEWS] == "tentative conclusions only" &&
     grepl("tentative$", group_status(G_NEWS, "says_retracted")))
ok("says retracted: on each random sample the model is within 2 points of a labeller",
   all(abs(random_told$model_minus_labeller_points) < 2))
five <- vb[vb$measure == "five categories", ]
ok("five categories: no sample reaches 0.667, which is why they stay provisional",
   nrow(five) == 3 && all(five$labellers_band == "not a basis for conclusions"))
ok("five categories: the model is below a labeller on every sample",
   all(five$model_agrees < five$labeller_agrees))
ok("round 2's media items are not taken as a random sample",
   identical(vb$drawn_at_random[grepl("^round 2: 127", vb$sample)], c(FALSE, FALSE)))
un <- vb[vb$group == G_UNLAB, ]
ok("the unlabelled group has a row saying it has no sample",
   nrow(un) == 1 && un$items == 0 && is.na(un$labellers_alpha) && grepl("^none", un$sample))
ok("model minus labeller is in points: social says retracted 97.12 - 98.56 = -1.4",
   vb$model_minus_labeller_points[vb$group == G_SOCIAL &
                                    vb$measure == "says retracted or not"] == -1.4)

# -- shares -------------------------------------------------------------------
ok("a share against one total is computed per element",
   identical(pct(c(1, 3), 4), c(25, 75)))
ok("a share of nothing is NA", is.na(pct(1, 0)) && !is.nan(pct(1, 0)))
ok("per-row totals still work", identical(pct(c(1, 1), c(2, 4)), c(50, 25)))

# -- a small corpus -----------------------------------------------------------
#   P1  news (after, told) + reddit (after, repeats)
#   P2  reddit only: two after items, one told
#   P3  video only: after, not told
#   P4  video (before) + facebook (after, told)
d <- data.frame(
  id = sprintf("i%d", 1:7),
  doi = c("P1", "P1", "P2", "P2", "P3", "P4", "P4"),
  kind = c("news", "reddit", "reddit", "reddit", "video", "video", "facebook"),
  relative_to_retraction = c("after", "after", "after", "after", "after", "before", "after"),
  label = c("A2", "A1", "A2", "A4", "A1", "A4", "A2"),
  words = c(40, 20, 30, 10, 50, 60, 25),
  stringsAsFactors = FALSE)
d$group <- source_group(d$kind, classified = TRUE)
d$told <- d$label == "A2"
d$repeats <- d$label == "A1"
aft <- d[d$relative_to_retraction == "after", ]

k <- after_table(aft, "kind")
ok("by kind: largest first", k$kind[1] == "reddit")
ok("by kind: reddit has 3 items over 2 papers",
   k$items[k$kind == "reddit"] == 3 && k$papers[k$kind == "reddit"] == 2)
ok("by kind: reddit told 1 of 3 = 33.3%", k$told_pct[k$kind == "reddit"] == 33.3)
ok("by kind: reddit's told figure is validated, its repeats figure provisional",
   k$told_status[k$kind == "reddit"] == "validated, reliable" &&
     k$repeats_status[k$kind == "reddit"] == "provisional")
ok("by kind: news's told figure is validated with the tentative caveat",
   k$told_status[k$kind == "news"] == "validated, tentative")
ok("by kind: video's figures are both NOT validated",
   k$told_status[k$kind == "video"] == "NOT validated" &&
     k$repeats_status[k$kind == "video"] == "NOT validated")
ok("by kind: each source says how many labelled items it supplied",
   k$labelled_items[k$kind == "reddit"] == 42 && k$labelled_items[k$kind == "video"] == 0)
ok("by kind: the before-only video item is not counted", k$items[k$kind == "video"] == 1)

g <- after_table(aft, "group")
ok("by group: in the fixed order news, social, unlabelled",
   identical(g$group, c(G_NEWS, G_SOCIAL, G_UNLAB)))
ok("by group: social has reddit and facebook, 4 items, 2 told",
   g$items[g$group == G_SOCIAL] == 4 && g$told[g$group == G_SOCIAL] == 2)
ok("by group: the groups add up to every after item", sum(g$items) == nrow(aft))

v <- validated_vs_not(d)
ok("validated-vs-not: shares are per group and add to 100",
   abs(sum(v$share_of_items) - 100) < 0.2 && length(unique(v$share_of_items)) > 1)
ok("validated-vs-not: the unlabelled group has 2 items over 2 papers",
   v$items[v$group == G_UNLAB] == 2 && v$papers[v$group == G_UNLAB] == 2)
ans_flag <- c(TRUE, TRUE, FALSE, TRUE, TRUE, TRUE, TRUE)
v2 <- validated_vs_not(d, ans_flag)
ok("validated-vs-not: an unanswered item is counted apart, not as an item",
   v2$items[v2$group == G_SOCIAL] == 3 && v2$not_answered[v2$group == G_SOCIAL] == 1 &&
     sum(v2$not_answered) == 1)
ok("validated-vs-not: shares are over answered items only",
   v2$share_of_items[v2$group == G_NEWS] == pct(1, 6))
ok("validated-vs-not: no same-day item, none counted", all(v$same_day == 0))
d3 <- d; d3$relative_to_retraction[c(1, 3)] <- "same-day"
v3 <- validated_vs_not(d3, ans_flag)
ok("validated-vs-not: a same-day item is counted in its group, an unanswered one is not",
   identical(v3$same_day, c(1L, 0L, 0L)) && identical(v3$items, v2$items))

po <- papers_only_social(d)
val <- function(i) po$value[i]
ok("papers with any item: 4", val(1) == 4)
ok("papers with no news or blog item: P2, P3, P4", val(2) == 3)
ok("... all three covered after", val(3) == 3)
ok("their post-retraction items: 2 + 1 + 1", val(4) == 4)
ok("... telling the reader: P2's and P4's", val(5) == 2)
ok("papers with no item from a validated source: P3 only (P4 has facebook)", val(6) == 1)
ok("... covered after, 1 item, not told", val(7) == 1 && val(8) == 1 && val(9) == 0)
ok("A0-only DOI coverage is not treated as judged text", val(10) == 0 && val(11) == 0)

txt <- c("the paper was retracted", "a finding", "no retraction word here at all",
         "plain", "words", "more words", "Retracted by the journal")
a2 <- a2_support_by_group(d, txt)
ok("A2 support: news 1 A2, supported",
   a2$a2[a2$group == G_NEWS] == 1 && a2$a2_no_retraction_word[a2$group == G_NEWS] == 0)
ok("A2 support: social 2 A2, both with a retraction word",
   a2$a2[a2$group == G_SOCIAL] == 2 && a2$a2_no_retraction_word[a2$group == G_SOCIAL] == 0)
ok("A2 support: no A2 in the unlabelled group gives NA, not NaN",
   a2$a2[a2$group == G_UNLAB] == 0 && is.na(a2$pct[a2$group == G_UNLAB]) &&
     !is.nan(a2$pct[a2$group == G_UNLAB]))
d2 <- d; d2$label[3] <- "A2"
a2b <- a2_support_by_group(d2, replace(txt, 3, "nothing to see"))
ok("A2 support: an A2 with no retraction word is flagged",
   a2b$a2_no_retraction_word[a2b$group == G_SOCIAL] == 1)

len <- text_length_by_kind(d)
ok("text length: reddit median of 20, 30, 10 is 20", len$median_words[len$kind == "reddit"] == 20)
ok("text length: says which sample covers each source",
   len$labelled_sample[len$kind == "video"] == "none" &&
     grepl("social supplement", len$labelled_sample[len$kind == "reddit"]) &&
     len$labelled_items[len$kind == "news"] == 100)

# -- the whole run, on a dataset small enough to check by hand ---------------
make_run <- function(root, overlap = FALSE) {
  dir.create(file.path(root, "clean"), recursive = TRUE)
  dir.create(file.path(root, "corpus"))
  posts <- data.frame(
    doi = c("P1", "P1", "P2", "P2", "P2", "P3", "P9"),
    kind = c("news", "reddit", "reddit", "video", "facebook", "twitter", "patent"),
    title = c("Study retracted by the journal after an investigation into it",
              "This paper shows coffee cures colds in every single person tested",
              "Interesting discussion about something unrelated to the paper at all",
              "A video about the study and what it says about many things",
              "A long post that the model failed to answer in a usable way at all",
              "", "x"),
    summary = "", relative_to_retraction = "after",
    date_basis = c("earliest Retraction record", "earliest Retraction record",
                   rep("earliest notice, no Retraction record", 5)), stringsAsFactors = FALSE)
  gz <- gzfile(file.path(root, "clean", "altmetric_posts.csv.gz"), "w")
  utils::write.csv(posts, gz, row.names = FALSE)
  close(gz)
  pr <- prepare_item_text(posts$title, posts$summary)
  ids <- item_id_A(posts$doi, pr$title, pr$summary)
  it <- data.frame(id = ids[1:5], doi = posts$doi[1:5], kind = posts$kind[1:5],
                   relative_to_retraction = "after", stringsAsFactors = FALSE)
  utils::write.csv(it, file.path(root, "corpus", "items_A.csv"), row.names = FALSE)
  # The facebook item's reply did not parse: no label, an error.
  utils::write.csv(data.frame(id = ids[c(1:3, 5)], label = c("A2", "A1", "A4", ""),
                              evidence = "", error = c("", "", "", "unparseable")),
                   file.path(root, "corpus", "test-model_A_A-v2-0000.csv"), row.names = FALSE)
  rule_ids <- if (overlap) ids[c(4, 1)] else ids[4]
  utils::write.csv(data.frame(id = rule_ids, label = "A0", evidence = "", rule = "test"),
                   file.path(root, "corpus", "answers_A_by_rule.csv"), row.names = FALSE)
}
run_in <- function(root) {
  out <- file.path(root, "reports")
  warned <- character()
  invisible(utils::capture.output(
    written <- withCallingHandlers(
      run_social_media("test:model", clean = file.path(root, "clean"),
                       corpus = file.path(root, "corpus"), out = out),
      warning = function(w) {
        warned <<- c(warned, conditionMessage(w))
        invokeRestart("muffleWarning")
      })))
  attr(written, "warned") <- warned
  written
}

root <- tempfile("social-")
make_run(root)
written <- run_in(root)
out <- file.path(root, "reports")
rd <- function(n) utils::read.csv(file.path(out, sprintf("social-media-%s.csv", n)),
                                  stringsAsFactors = FALSE, check.names = FALSE)
ok("run: all ten tables written", length(written) == 10 && all(file.exists(written)))
ok("run: answers from a model nobody checked are warned about",
   any(grepl("do not apply to answers from test:model", attr(written, "warned"))))
u <- rd("source-universe")
ok("run: twitter is listed with no text",
   u$with_text[u$kind == "twitter"] == 0 && u$group[u$kind == "twitter"] == G_NOTEXT)
ok("run: an alternative model does not inherit the sampled model's status",
   u$says_retracted[u$kind == "reddit"] == "NOT validated for this model/prompt" &&
     u$labelled_items[u$kind == "reddit"] == 42)
gr <- rd("after-by-group")
ok("run: news told 1 of 1, social 0 of 2 answered, video 0 of 1",
   identical(gr$told, c(1L, 0L, 0L)) && identical(gr$items, c(1L, 2L, 1L)))
ok("run: actual model and prompt accompany every after rate",
   all(gr$answer_model == "test:model") && all(gr$answer_prompt == "A-v2-0000") &&
     !any(gr$validation_applicable) && !any(grepl("validated, reliable|validated, tentative", gr$group)))
ok("run: unvalidated sources cannot gain validation from metadata alone",
   !any(bs <- gr$validation_applicable[grepl("unlabelled", gr$group)]))
ok("run: A0 is separate from the judged denominator",
   identical(gr$a0_items, c(0L, 0L, 1L)) && identical(gr$judged_items, c(1L, 2L, 0L)) &&
     is.na(gr$told_pct_of_judged[3]))
db <- rd("date-basis")
ok("run: fallback event dates remain visible", sum(db$items) == 4 &&
     db$after_items[db$date_basis == "earliest notice, no Retraction record"] == 2)
rr <- rd("after-retraction-record-by-group")
ok("run: Retraction-record sensitivity excludes fallback events",
   sum(rr$items) == 2 && sum(rr$told) == 1)
vv <- rd("validated-vs-not")
ok("run: the unparseable facebook answer is counted as not answered",
   vv$not_answered[grepl("^round-2 social sources", vv$group)] == 1 && sum(vv$items) == 4)
bs <- rd("validation-basis")
ok("run: the validation basis is written with its unlabelled row",
   nrow(bs) == nrow(VALIDATION) + 1 && any(bs$group == G_UNLAB))
ab <- rd("a2-support-by-group")
ok("run: the A2 found its own text (has a retraction word)",
   ab$a2_no_retraction_word[grepl("^news and blogs", ab$group)] == 0)
tl <- rd("text-length-by-kind")
ok("run: every answered item found its text", sum(tl$items) == 4)
unlink(root, recursive = TRUE)

root <- tempfile("social-dup-")
make_run(root, overlap = TRUE)
err <- tryCatch({ run_in(root); "" }, error = conditionMessage)
ok("run: an item answered by the model and by rule stops the run",
   grepl("more than one answer", err))
unlink(root, recursive = TRUE)

# -- the constants against what was actually labelled ------------------------
# These read the project's own data and are skipped where it is absent: the
# keys are not version-controlled, the reports are.
ks <- file.path("data", "labelling", "key_A_social.csv")
ka <- file.path("data", "labelling", "key_A.csv")
if (file.exists(ks) && file.exists(ka)) {
  k1 <- utils::read.csv(ka, stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
  k2 <- utils::read.csv(ks, stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
  ok("the social supplement labelled exactly ROUND2_SOCIAL_KINDS",
     setequal(unique(k2$kind[k2$set == "main"]), ROUND2_SOCIAL_KINDS))
  ok("round 1 labelled exactly news and blogs",
     setequal(unique(k1$kind[k1$set == "main"]), NEWS_BLOG_KINDS))
  t <- table(c(k1$kind[k1$set == "main"], k2$kind[k2$set == "main"]))
  ok("LABELLED_ITEMS is what each source supplied to the two samples",
     identical(as.integer(t[names(LABELLED_ITEMS)]), unname(LABELLED_ITEMS)) &&
       setequal(names(t), names(LABELLED_ITEMS)))
  ok("neither sample drew a same-day item",
     !any(c(k1$relative_to_retraction, k2$relative_to_retraction) == SAME_DAY))
  # The caveat on round 2's media row: v2's examples quote three round-1
  # items (analysis/llm_validate.R leaves them out), and all three are in
  # round 2's 127. No social item is quoted.
  shown_in <- function(k) k$item_id[Reduce(`|`, lapply(TASK_A_V2_EXAMPLE_MARKERS, function(m)
    grepl(m, paste(k$title, k$summary), fixed = TRUE)))]
  k2r <- file.path("data", "labelling", "round2", "key_A_round2.csv")
  if (file.exists(k2r)) {
    r2 <- utils::read.csv(k2r, stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
    ok("the 3 items v2's examples quote are A001, A002, A005, all in round 2, none social",
       setequal(shown_in(k1), c("A001", "A002", "A005")) && all(shown_in(k1) %in% r2$item_id) &&
         !length(shown_in(k2)) && nrow(r2) == 127)
  } else cat("skip  ", k2r, "not present\n")
} else cat("skip   the labelling keys are not present\n")
ok("the round-2 media rows say they include the quoted items",
   all(grepl("includes the 3 items v2's examples quote", VALIDATION$sample[VALIDATION$items == 127])))
ia <- file.path("data", "llm", "corpus", "items_A.csv")
if (file.exists(ia)) {
  kinds <- unique(utils::read.csv(ia, colClasses = "character")$kind)
  ok("every classified source is named in one of the three lists",
     all(kinds %in% c(NEWS_BLOG_KINDS, ROUND2_SOCIAL_KINDS, UNLABELLED_KINDS)))
} else cat("skip  ", ia, "not present\n")

# Each copied figure against the row it was copied from.
report_value <- function(file, task, pattern) {
  x <- utils::read.csv(file.path("data", "reports", file), stringsAsFactors = FALSE)
  if (file == "llm-validation.csv") x <- x[x$model == VALIDATED_MODEL, ]
  v <- x$value[x$task == task & grepl(pattern, x$measure)]
  if (length(v) == 1) v else NA_real_
}
have <- file.exists(file.path("data", "reports", unique(VALIDATION$source_file)))
if (all(have)) {
  for (i in seq_len(nrow(VALIDATION))) {
    r <- VALIDATION[i, ]
    got <- c(items = report_value(r$source_file, r$source_task, "^items$"),
             alpha = report_value(r$source_file, r$source_task, "^Krippendorff alpha, labellers$"),
             model = report_value(r$source_file, r$source_task, "^model = majority of the same"),
             labeller = report_value(r$source_file, r$source_task,
                                     "^one labeller = majority of the other"),
             kappa = report_value(r$source_file, r$source_task,
                                  "^Cohen kappa, model vs majority$"))
    want <- c(r$items, r$labellers_alpha, r$model_agrees, r$labeller_agrees,
              r$kappa_model_vs_majority)
    ok(sprintf("VALIDATION matches %s: %s", r$source_file, r$source_task),
       all(!is.na(got)) && all(abs(got - want) < 1e-4))
  }
} else cat("skip   the validation reports are not present\n")

cat(if (fails) sprintf("\n%d FAILED\n", fails) else "\nall passed\n")
quit(status = if (fails) 1L else 0L)
