# Round 2 of the labelling, scored.
#
#   Rscript analysis/labelling_round2_score.R
#
# Round 2 asked the same people to label, under protocol v2 (four questions in
# a fixed order), 127 media items - the 87 round 1 split on, plus 40 it had
# agreed on, mixed in unmarked as a check - and 100 social media items the
# model has never been checked on (analysis/labelling_round2.R and
# analysis/labelling_package.R built the sheets).
#
# Four questions, in this order:
#   1. Did v2 raise agreement on the items round 1 split on? Compared with
#      round 1 on the same items and the same labellers.
#   2. Did v2 move what round 1 had settled? The 40 check items: does the
#      round 2 majority match round 1's?
#   3. How was A0 used? The sheets' dropdown read "A0 Cannot tell", while v2
#      defines A0 as "no readable text" and says uncertainty is not A0 - so A0
#      is counted per labeller, round 1 against round 2.
#   4. Is the model as good on social media as on news and blogs? Its saved
#      answers against the labellers' majority, the way a labeller is judged.
#
# Writes data/reports/labelling-round2.csv, labelling-round2-a0.csv and
# labelling-round2-model.csv.

source("R/labelling.R")
source("R/llm.R")
source("R/llm_text.R")
source("R/llm_eval.R")

R2 <- Sys.getenv("CS43_LABELS_ROUND2", file.path("data", "labelling", "returned", "round2"))
R1 <- file.path("data", "labelling", "returned", "round1")
OUT <- file.path("data", "reports")
rd <- function(f) utils::read.csv(f, stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")

got <- read_returned_labels(R2)
if (!is.null(got$problems)) print(got$problems, row.names = FALSE)
a <- got$A
a$code <- label_code(a$label, LABEL_A)
who <- sort(unique(a$labeller))
cat("round 2 labellers:", length(who), "-", paste(who, collapse = ", "), "\n")
med <- a[a$sheet == "A_media", ]
soc <- a[a$sheet == "A_social", ]
m2 <- rating_matrix(med$item_id, med$labeller, med$code)
s2 <- rating_matrix(soc$item_id, soc$labeller, soc$code)
told <- function(m) { x <- ifelse(m == "A2", "Y", "N"); x[is.na(m)] <- NA; x }

rows <- list()
add <- function(part, scope, measure, value) {
  rows[[length(rows) + 1L]] <<- data.frame(part = part, scope = scope, measure = measure,
                                           value = round(value, 4), stringsAsFactors = FALSE)
}
describe <- function(m, part, scope) {
  add(part, scope, "items", nrow(m))
  add(part, scope, "labellers", ncol(m))
  add(part, scope, "alpha, five categories", krippendorff_alpha(m, "nominal", LABEL_A))
  add(part, scope, "pairwise agreement, five categories", pairwise_agreement(m))
  add(part, scope, "alpha, says retracted or not", krippendorff_alpha(told(m), "nominal", c("N", "Y")))
  add(part, scope, "pairwise agreement, says retracted or not", pairwise_agreement(told(m)))
}

# --- 1. round 1 against round 2, same items, same people ---------------------
key2 <- rd(file.path("data", "labelling", "round2", "key_A_round2.csv"))
contested <- key2$item_id[key2$why == "contested in round 1"]
checks <- key2$item_id[key2$why != "contested in round 1"]
g1 <- read_returned_labels(R1)
a1 <- g1$A[g1$A$sheet == "A_media", ]
a1$code <- label_code(a1$label, LABEL_A)
m1 <- rating_matrix(a1$item_id, a1$labeller, a1$code)
same <- intersect(colnames(m1), colnames(m2))
cat("labellers in both rounds:", length(same), "\n")
for (scope in c("contested in round 1", "all 127")) {
  ids <- if (scope == "all 127") rownames(m2) else contested
  describe(m1[ids, same, drop = FALSE], "media, round 1 (same items, same people)", scope)
  describe(m2[ids, same, drop = FALSE], "media, round 2 (protocol v2)", scope)
}

# --- 2. did v2 move what round 1 had settled? --------------------------------
v2 <- majority_vote(m2[checks, , drop = FALSE], LABEL_A_PRECEDENCE)
was <- key2$round1_majority[match(checks, key2$item_id)]
held <- !v2$tied & v2$majority == was
add("media, the 40 check items", "round 2 majority = round 1 majority", "items", sum(held, na.rm = TRUE))
add("media, the 40 check items", "round 2 majority = round 1 majority", "share", mean(held, na.rm = TRUE))
for (w in unique(key2$why[key2$why != "contested in round 1"])) {
  ids <- key2$item_id[key2$why == w]
  h <- held[match(ids, checks)]
  add("media, the 40 check items", w, "share held", mean(h, na.rm = TRUE))
}

# --- 3. A0, per labeller -----------------------------------------------------
a0 <- do.call(rbind, lapply(who, function(p) data.frame(
  labeller = p,
  round1_media = sum(m1[, p] %in% "A0") / 200,
  round2_media = sum(m2[, p] %in% "A0") / nrow(m2),
  round2_social = sum(s2[, p] %in% "A0") / nrow(s2))))
a0[-1] <- lapply(a0[-1], function(x) round(100 * x, 1))
cat("\nA0 share by labeller (%):\n"); print(a0, row.names = FALSE)

# --- social media: agreement, and the model ----------------------------------
describe(s2, "social media (new)", "all 100")

ks <- rd(file.path("data", "labelling", "key_A_social.csv"))
ks <- ks[match(rownames(s2), ks$item_id), ]
prep <- prepare_item_text(ks$title, ks$summary)
ids <- item_id_A(ks$doi, prep$title, prep$summary)
slug <- gsub("[^A-Za-z0-9]+", "-", LLM_MODEL)
f <- list.files(file.path("data", "llm", "corpus"), sprintf("^%s_A_A-v2-.*[.]csv$", slug),
                full.names = TRUE)
ans <- llm_read(f[1])
ans <- ans[is.na(ans$error), c("id", "label")]
byrule <- rd(file.path("data", "llm", "corpus", "answers_A_by_rule.csv"))
ans <- rbind(ans, byrule[, c("id", "label")])
x <- ans$label[match(ids, ans$id)]
cat("\nsocial items with a saved model answer:", sum(!is.na(x)), "of", length(x), "\n")
jm <- rbind(
  cbind(task = "social, five categories", judge_task(s2, x, LABEL_A)),
  cbind(task = "social, says retracted or not",
        judge_task(told(s2), ifelse(is.na(x), NA, ifelse(x == "A2", "Y", "N")), c("N", "Y"))))
# The model on the round-2 media items too: its validation answers, against
# the round-2 majority.
pv <- llm_read(file.path("data", "llm", "validation", sprintf("%s_A_A-v2-77581c5c.csv", slug)))
xv <- pv$label[match(rownames(m2), pv$id)]
jm <- rbind(jm,
  cbind(task = "round-2 media, five categories", judge_task(m2, xv, LABEL_A)),
  cbind(task = "round-2 media, says retracted or not",
        judge_task(told(m2), ifelse(is.na(xv), NA, ifelse(xv == "A2", "Y", "N")), c("N", "Y"))))
jm$value <- round(jm$value, 4)
jm$measure <- sub("other six", sprintf("other %d", ncol(m2) - 1L), jm$measure)
jm$measure <- sub("same six", sprintf("same %d", ncol(m2) - 1L), jm$measure)

res <- do.call(rbind, rows)
cat("\n"); print(res, row.names = FALSE)
k <- ncol(m2) - 1L
cat("\n"); print(jm[jm$measure %in% c(sprintf("model = majority of the same %d (mean)", k),
                                     sprintf("one labeller = majority of the other %d (mean)", k),
                                     "Krippendorff alpha, labellers"), ], row.names = FALSE)
utils::write.csv(res, file.path(OUT, "labelling-round2.csv"), row.names = FALSE, fileEncoding = "UTF-8")
utils::write.csv(a0, file.path(OUT, "labelling-round2-a0.csv"), row.names = FALSE, fileEncoding = "UTF-8")
utils::write.csv(jm, file.path(OUT, "labelling-round2-model.csv"), row.names = FALSE, fileEncoding = "UTF-8")
cat("written: data/reports/labelling-round2.csv, labelling-round2-a0.csv, labelling-round2-model.csv\n")
