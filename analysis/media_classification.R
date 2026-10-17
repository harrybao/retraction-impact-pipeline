# What does the coverage of a retracted paper actually say, before and after?
#
#   Rscript analysis/media_classification.R [model]
#
# This is the central question of the Stage 3 brief: does the media repeat the
# finding, acknowledge the retraction, or
# correct it - and does that change once the paper is retracted. Run
# analysis/llm_classify_corpus.R first; this reads its answers.
#
# Three things decide whether the tables below mean anything.
#
# **The unit is the item, and then the paper.** An item is one (paper, text):
# the same words posted twice about one paper are one item. Counting posts
# instead would let a single story syndicated across forty outlets outvote
# forty separate stories.
#
# **"After" is not "because".** An item posted after the retraction date may
# have been written without knowing of it, and the citation analysis already
# showed retracted papers drifting from their controls beforehand. These tables
# describe what the coverage says, in each period; they do not claim the
# retraction caused it.
#
# **The headline is the binary, not the five categories.** Seven labellers
# reached Krippendorff's alpha 0.594 on the five-way scheme, below the level
# any conclusion rests on, but 0.776 on the one distinction the client asked
# about: is the reader told the paper was retracted. The five-way breakdown is
# reported as indicative, with its agreement figure beside it.
#
# Items whose evidence quote is not in the text are counted separately, because
# on the validation sample those answers matched the labellers only about half
# as often as grounded ones (R/llm_evidence.R).

source("R/labelling.R")   # LABEL_A, and the label vocabulary the humans used
source("R/llm.R")
source("R/llm_text.R")
source("R/llm_evidence.R")

args <- commandArgs(trailingOnly = TRUE)
model <- if (length(args)) args[1] else LLM_MODEL
slug <- gsub("[^A-Za-z0-9]+", "-", model)
CORPUS <- file.path("data", "llm", "corpus")
OUT <- file.path("data", "reports")
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)

items <- utils::read.csv(file.path(CORPUS, "items_A.csv"), colClasses = "character",
                         encoding = "UTF-8")
items$n_posts <- as.integer(items$n_posts)
f <- list.files(CORPUS, sprintf("^%s_A_A-v2-.*\\.csv$", slug), full.names = TRUE)
if (!length(f)) stop("no task A answers for ", model, " in ", CORPUS, call. = FALSE)
ans <- llm_read(f[1])
cat("model:", model, " answers:", nrow(ans), "\n")

# The items answered by rule, if any, join the model's answers as A0.
byrule <- file.path(CORPUS, "answers_A_by_rule.csv")
if (file.exists(byrule)) {
  r <- utils::read.csv(byrule, colClasses = "character", encoding = "UTF-8")
  cat("answered A0 by rule (no readable text):", nrow(r), "\n")
  ans <- rbind(ans[, c("id", "label", "evidence", "error")],
               data.frame(id = r$id, label = r$label, evidence = r$evidence,
                          error = NA_character_, stringsAsFactors = FALSE))
} else {
  ans <- ans[, c("id", "label", "evidence", "error")]
}

d <- merge(items, ans, by = "id", all.x = TRUE)
missing <- sum(is.na(d$label))
if (missing) cat("NOT YET CLASSIFIED:", missing, "items - tables below cover the rest\n")
d <- d[!is.na(d$label) & is.na(d$error), ]
d$told <- ifelse(d$label == "A2", "told", "not told")
d$period <- factor(d$relative_to_retraction, c("before", "same-day", "after"))
stopifnot(all(d$label %in% LABEL_A))

w <- function(x, name) {
  p <- file.path(OUT, sprintf("media-classification-%s.csv", name))
  utils::write.csv(x, p, row.names = FALSE, fileEncoding = "UTF-8")
  cat("written:", p, "\n")
}
pct <- function(x) round(100 * x, 1)

# ------------------------------------------------------------------ items --
tab <- as.data.frame(table(period = d$period, label = factor(d$label, LABEL_A)))
tab$share_of_period <- pct(tab$Freq / ave(tab$Freq, tab$period, FUN = sum))
cat("\n-- items by period and label --\n")
print(stats::xtabs(Freq ~ label + period, tab))
w(tab, "items-by-period")

bin <- as.data.frame(table(period = d$period, told = d$told))
bin$share_of_period <- pct(bin$Freq / ave(bin$Freq, bin$period, FUN = sum))
cat("\n-- is the reader told the paper was retracted? --\n")
print(bin, row.names = FALSE)
w(bin, "told-by-period")

cat("\n-- by source kind, after the retraction only --\n")
aft <- d[d$period == "after", ]
k <- as.data.frame(table(kind = aft$kind, told = aft$told))
k$share <- pct(k$Freq / ave(k$Freq, k$kind, FUN = sum))
print(k[k$told == "told", c("kind", "Freq", "share")], row.names = FALSE)
w(k, "told-by-kind-after")

# ----------------------------------------------------------------- papers --
# A paper is covered "after" if any post-retraction item exists; it is told-of
# if any of those items acknowledges the retraction. Any, not all: the client's
# question is whether the reader could have learnt of it, and one story that
# says so is enough for that, while a paper covered ten times with no mention
# is the case he is worried about.
pap <- stats::aggregate(
  list(items_after = rep(1L, nrow(aft)),
       told_after = as.integer(aft$told == "told")),
  by = list(doi = aft$doi), FUN = sum)
pap$any_told <- pap$told_after > 0
cat("\n-- papers with coverage after the retraction --\n")
cat("papers:", nrow(pap), "\n")
cat("at least one item acknowledging the retraction:", sum(pap$any_told),
    sprintf("(%.1f%%)\n", 100 * mean(pap$any_told)))
cat("every post-retraction item silent on it:", sum(!pap$any_told),
    sprintf("(%.1f%%)\n", 100 * mean(!pap$any_told)))
w(data.frame(measure = c("papers covered after retraction",
                         "at least one item acknowledges it",
                         "no item acknowledges it"),
             value = c(nrow(pap), sum(pap$any_told), sum(!pap$any_told))),
  "papers-after")

# ---------------------------------------------------------------- quality --
# How much of this rests on answers whose quoted reason is not in the text.
it <- utils::read.csv(gzfile(file.path("data", "clean", "altmetric_posts.csv.gz")),
                      colClasses = "character", encoding = "UTF-8")
prep <- prepare_item_text(it$title, it$summary)
# The same identity the classifier used: a hash of the PREPARED text.
ids <- item_id_A(it$doi, prep$title, prep$summary)
m <- match(d$id, ids)
d$text <- ifelse(is.na(m), "", prep$text[m])

# Does the text support an A2 at all? Every A2 in the corpus is checked, not a
# sample, because A2 is the label the client's question turns on.
sup <- a2_support(d$label, d$text)
d$a2_suspect <- sup$suspect
n2 <- sum(d$label == "A2")
cat("\n-- can the text support the A2 answers? --\n")
cat("A2 answers:", n2, "  of those with no retraction word in the text:",
    sum(d$a2_suspect), sprintf("(%.1f%%)\n", 100 * sum(d$a2_suspect) / n2))
cat("A3 answers in the whole corpus:", sum(d$label == "A3"),
    "- the labellers' majority chose A3 on 15% of the 200 validated items,\n",
    "  so A3 is under-used here and some of it is sitting in A2.\n")
w(data.frame(measure = c("A2 answers", "A2 with no retraction word in the text",
                         "A3 answers"),
             value = c(n2, sum(d$a2_suspect), sum(d$label == "A3"))),
  "a2-support")

# The headline, with that uncertainty carried through rather than hidden: the
# lower bound drops every A2 the text cannot support.
aft2 <- d[d$period == "after", ]
strict <- aft2$label == "A2" & !aft2$a2_suspect
pa <- unique(aft2$doi)
told_any <- unique(aft2$doi[aft2$told == "told"])
told_strict <- unique(aft2$doi[strict])
cat("\n-- papers covered after the retraction, told-of, as a range --\n")
cat("papers:", length(pa), "\n")
cat("  as labelled:                     ", length(told_any),
    sprintf("(%.1f%%)\n", 100 * length(told_any) / length(pa)))
cat("  counting only A2 the text backs: ", length(told_strict),
    sprintf("(%.1f%%)\n", 100 * length(told_strict) / length(pa)))
w(data.frame(measure = c("papers covered after retraction",
                         "at least one item acknowledges it, as labelled",
                         "at least one item acknowledges it, A2 backed by the text"),
             value = c(length(pa), length(told_any), length(told_strict))),
  "papers-after-range")

if (nrow(d)) {
  set.seed(20260925)
  s <- d[sample(nrow(d), min(4000L, nrow(d))), ]
  keep <- nzchar(s$text)
  cat("\n-- evidence check, on a sample of", sum(keep), "answers --\n")
  chk <- evidence_check(s$evidence[keep], s$text[keep])
  print(evidence_summary(chk), row.names = FALSE)
  w(evidence_summary(chk), "evidence-check-sample")
}
