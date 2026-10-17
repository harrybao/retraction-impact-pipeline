# How far to trust the model's "no reason given" on retraction notices.
#
#   Rscript analysis/notice_r0_check.R
#
# On the 200 validation notices the model agrees with the labellers' majority
# on R0 ("no reason given") 86.9% of the time, where one labeller agrees with
# the other six 96.9% - the weakest of the ten notice columns. This asks where
# the two part: on which notices the model says "no reason" and the labellers
# name one, and which reason they name. It then turns the model's errors into a
# corrected corpus share, so the figure that reaches the client is not the
# model's raw one.
#
# Writes data/reports/notice-r0-check.csv (measures) and
# data/reports/notice-r0-disagreements.csv (item ids and the reasons named, no
# notice text).

source("R/labelling.R")
source("R/llm.R")

read_key <- function(f) {
  k <- utils::read.csv(file.path("data", "labelling", f), stringsAsFactors = FALSE,
                       fileEncoding = "UTF-8-BOM")
  k[k$set == "main", ]
}
key <- read_key("key_B.csv")
got <- read_returned_labels(file.path("data", "labelling", "returned", "round1"))
hb <- task_b_matrices(got$B)
hb$reasons <- lapply(hb$reasons, function(m) m[key$item_id, , drop = FALSE])

majority <- function(m) {
  y <- rowSums(m == "Y", na.rm = TRUE)
  n <- rowSums(!is.na(m))
  ifelse(y * 2 > n, "Y", "N")
}
maj <- vapply(hb$reasons, majority, character(nrow(key)))
rownames(maj) <- key$item_id

pb <- llm_read(file.path("data", "llm", "validation", "qwen3-14b_B_B-v2-d0f96a83.csv"))
pb <- task_b_enforce(pb[is.na(pb$error), ])
pb <- pb[match(key$item_id, pb$id), ]
model_r0 <- toupper(pb$R0_no_reason) == "TRUE"
human_r0 <- maj[, "R0_no_reason"] == "Y"
ok <- !is.na(model_r0)

tp <- sum(model_r0 & human_r0 & ok); fp <- sum(model_r0 & !human_r0 & ok)
fn <- sum(!model_r0 & human_r0 & ok); tn <- sum(!model_r0 & !human_r0 & ok)
ppv <- tp / (tp + fp)          # when the model says "no reason", how often the labellers agree
npv <- tn / (tn + fn)          # when it names a reason, how often the labellers name one too
cat(sprintf("validation (200 notices): model R0 %d, labellers' majority R0 %d\n",
            tp + fp, tp + fn))
cat(sprintf("  both R0 %d | model only %d | labellers only %d | neither %d\n", tp, fp, fn, tn))
cat(sprintf("  when the model says 'no reason' the labellers agree %.1f%% of the time\n", 100 * ppv))

# The model-only cases: which reasons did the labellers name instead?
dis <- key$item_id[model_r0 & !human_r0 & ok]
named <- maj[dis, setdiff(colnames(maj), "R0_no_reason"), drop = FALSE]
named_list <- apply(named, 1, function(r) paste(names(r)[r == "Y"], collapse = "; "))
tab <- sort(table(unlist(strsplit(named_list[nzchar(named_list)], "; "))), decreasing = TRUE)
cat("\nreasons the labellers named where the model said 'no reason':\n")
print(tab)
cat("  with no reason reaching a majority either:", sum(!nzchar(named_list)), "\n")
utils::write.csv(data.frame(item_id = dis, labellers_named = named_list,
                            model_clarity = pb$clarity[match(dis, pb$id)]),
                 file.path("data", "reports", "notice-r0-disagreements.csv"),
                 row.names = FALSE, fileEncoding = "UTF-8")

# The validation measures only. An earlier version also turned these error
# rates into a "corrected corpus share" by reading
# notice-classification-reasons-overall.csv - but that file is written by
# notice_classification.R, which now applies the rule this script measures, so
# the figure depended on which script ran first. The corpus share is reported by
# notice_classification.R alone.
out <- data.frame(
  measure = c("validation notices", "model R0", "labellers' majority R0", "both",
              "model only", "labellers only", "neither",
              "labellers agree when model says R0 (PPV)",
              "labellers name a reason when model does (NPV)"),
  value = c(sum(ok), tp + fp, tp + fn, tp, fp, fn, tn, round(ppv, 4), round(npv, 4)))

# The protocol's definition applied to the answers (R/notice_rules.R): a notice
# citing a violated policy states a reason. Re-scored on the same 200 notices,
# for every reason column, so a gain on R0 cannot hide a loss elsewhere.
source("R/notice_rules.R")
pr <- notice_policy_rule(pb, key$notice)
cat("\nwith the policy-violation rule:", sum(pr$fixed_policy, na.rm = TRUE), "answers changed\n")
agree <- function(d, r) {
  x <- toupper(d[[r]]) == "TRUE"
  mean(ifelse(x, "Y", "N")[ok] == maj[ok, r])
}
cmp <- data.frame(reason = colnames(maj),
                  before = vapply(colnames(maj), function(r) round(agree(pb, r), 4), numeric(1)),
                  after = vapply(colnames(maj), function(r) round(agree(pr, r), 4), numeric(1)))
print(cmp, row.names = FALSE)
m2 <- toupper(pr$R0_no_reason) == "TRUE"
tp2 <- sum(m2 & human_r0 & ok); fp2 <- sum(m2 & !human_r0 & ok)
fn2 <- sum(!m2 & human_r0 & ok)
cat(sprintf("  R0 after the rule: model %d, labellers %d, both %d, model only %d, labellers only %d\n",
            tp2 + fp2, tp2 + fn2, tp2, fp2, fn2))
out <- rbind(out, data.frame(
  measure = c("answers changed by the policy-violation rule", "model R0 after the rule",
              "model only, after the rule", "labellers only, after the rule",
              "R0 agreement with majority, before", "R0 agreement with majority, after",
              "R9 agreement with majority, before", "R9 agreement with majority, after"),
  value = c(sum(pr$fixed_policy, na.rm = TRUE), tp2 + fp2, fp2, fn2,
            cmp$before[cmp$reason == "R0_no_reason"], cmp$after[cmp$reason == "R0_no_reason"],
            cmp$before[cmp$reason == "R9_other"], cmp$after[cmp$reason == "R9_other"])))
utils::write.csv(out, file.path("data", "reports", "notice-r0-check.csv"), row.names = FALSE,
                 fileEncoding = "UTF-8")
cat("written: data/reports/notice-r0-check.csv, notice-r0-disagreements.csv\n")
