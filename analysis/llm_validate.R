# Is the local model good enough to do what the group did by hand - and which one?
#
#   Rscript analysis/llm_validate.R [model ...]     default: qwen3:14b qwen3:8b
#
# Stage 3 classifies about 175,000 media items and notices with an open-weight
# model on this machine, because the client has no budget for a commercial one.
# This script is what says whether that is acceptable, and which model to use.
#
# Each candidate labels the 200 media items and 200 notices the seven labellers
# did in round 1 (data/labelling/main), asked the same questions, and is judged
# by R/llm_eval.R: against the labellers' majority, and against what one
# labeller achieves against the other six. The model is chosen on those numbers
# and on speed, not on size.
#
# Task A is asked twice. The labellers worked from protocol v1, so the model is
# scored on v1's wording for the like-for-like figure. It is also asked v2, the
# rewrite round 1 led to and what the corpus is classified with - but scored
# against people who answered v1, where v2 moved a boundary the model and the
# majority can disagree with neither being wrong. v2's real validation is
# round 2; re-run this against it when it comes back.
#
# The model's answers go to data/llm/validation/ (not version-controlled: the
# evidence column quotes licensed text). Only numbers go to data/reports/.

source("R/labelling.R")
source("R/llm.R")
source("R/llm_eval.R")

models <- commandArgs(trailingOnly = TRUE)
if (!length(models)) models <- c("qwen3:14b", "qwen3:8b")
# Every answer is saved as it arrives (data/llm/validation), and llm_classify()
# asks only for what is missing. So a machine with no model server - the
# client's, reproducing the tables - scores the saved answers and needs no
# model at all. The server is required only when an answer is missing, and
# llm_classify() says so then.
if (llm_available()) {
  absent <- setdiff(models, llm_models())
  if (length(absent)) {
    stop("not installed: ", paste(absent, collapse = ", "),
         "\n  install with: ollama pull <name>", call. = FALSE)
  }
} else {
  message("no model server at ", LLM_HOST, ": scoring the saved answers only")
}

OUT_PRED <- file.path("data", "llm", "validation")
OUT_REP <- file.path("data", "reports")
dir.create(OUT_REP, showWarnings = FALSE, recursive = TRUE)

read_key <- function(f) {
  k <- utils::read.csv(file.path("data", "labelling", f), stringsAsFactors = FALSE,
                       fileEncoding = "UTF-8-BOM")
  k <- k[k$set == "main", ]
  k$id <- k$item_id
  k
}
key_a <- read_key("key_A.csv")
key_b <- read_key("key_B.csv")

# The labellers' answers, read by the same code that scored them. The folder is
# the RETURNED sheets; data/labelling/main holds the blank ones as issued, and
# reading those gives seven labellers who answered nothing - which the check
# below turns into an error instead of a table of NA agreement.
RETURNED <- Sys.getenv("CS43_LABELS_ROUND1", file.path("data", "labelling", "returned", "round1"))
got <- read_returned_labels(RETURNED)
ha <- score_task_a(got$A)$matrix
hb <- task_b_matrices(got$B)
if (mean(!is.na(ha)) < 0.9 || mean(!is.na(hb$clarity)) < 0.9) {
  stop(RETURNED, " is mostly unlabelled - is it the folder of returned sheets?",
       call. = FALSE)
}
stopifnot(all(key_a$id %in% rownames(ha)), all(key_b$id %in% rownames(hb$clarity)))
ha <- ha[key_a$id, , drop = FALSE]
hb$clarity <- hb$clarity[key_b$id, , drop = FALSE]
hb$reasons <- lapply(hb$reasons, function(m) m[key_b$id, , drop = FALSE])
cat("labellers:", ncol(ha), " task A items:", nrow(ha), " task B items:", nrow(hb$clarity), "\n")

# The version carries a fingerprint of the wording, so an edited prompt starts
# a new file instead of resuming into answers the old one produced.
VERSION_A1 <- paste0("A-v1-", prompt_fingerprint(TASK_A_PROMPT_V1, "", "", ""))
VERSION_A <- paste0("A-v2-", prompt_fingerprint(TASK_A_PROMPT, "", "", ""))
VERSION_B <- paste0("B-v2-", prompt_fingerprint(TASK_B_PROMPT, "", ""))
cat("prompts:", VERSION_A1, VERSION_A, VERSION_B, " context:", LLM_NUM_CTX,
    "tokens  batch:", LLM_BATCH, "\n\n")

# Items that v2's own examples are taken from. The v2 prompt shows the model
# their answers, so they are left out when v2 is scored.
shown <- Reduce(`|`, lapply(TASK_A_V2_EXAMPLE_MARKERS, function(k) {
  grepl(k, paste(key_a$title, key_a$summary), fixed = TRUE)
}))
cat("task A items quoted in the v2 examples, not scored for v2:",
    paste(key_a$id[shown], collapse = " "), "\n\n")

rows <- list()
confusions <- list()
add <- function(model, task, d) {
  d$model <- model
  d$task <- task
  rows[[length(rows) + 1L]] <<- d[, c("model", "task", "measure", "value")]
}
told <- function(x) ifelse(is.na(x), NA, ifelse(x == "A2", "told", "not told"))

for (model in models) {
  slug <- gsub("[^A-Za-z0-9]+", "-", model)
  fa1 <- file.path(OUT_PRED, sprintf("%s_A_%s.csv", slug, VERSION_A1))
  fa <- file.path(OUT_PRED, sprintf("%s_A_%s.csv", slug, VERSION_A))
  fb <- file.path(OUT_PRED, sprintf("%s_B_%s.csv", slug, VERSION_B))
  cat("==", model, "\n")
  llm_classify(key_a, task_a_v1_prompt_row, TASK_A_SCHEMA, fa1, VERSION_A1, model = model)
  llm_classify(key_a, task_a_prompt_row, TASK_A_SCHEMA, fa, VERSION_A, model = model)
  llm_classify(key_b, task_b_prompt_row, TASK_B_SCHEMA, fb, VERSION_B, model = model)
  pa1 <- llm_read(fa1)
  pa <- llm_read(fa)
  pb <- llm_read(fb)
  pa1 <- pa1[match(key_a$id, pa1$id), ]
  pa <- pa[match(key_a$id, pa$id), ]
  pb <- pb[match(key_b$id, pb$id), ]

  # -- task A: the five categories, and the client's own question.
  # v1 is the question round 1 answered, so it is the like-for-like score. v2
  # is scored too, without the items its examples quote, but against people
  # who answered v1; its own validation is round 2.
  for (v in list(list("v1", pa1, rep(TRUE, nrow(ha))), list("v2", pa, !shown))) {
    keep <- v[[3]]
    xa <- label_code(v[[2]]$label, LABEL_A)
    tag <- sprintf("A media %s", v[[1]])
    add(model, paste0(tag, ", 5 categories"),
        judge_task(ha[keep, , drop = FALSE], xa[keep], LABEL_A))
    add(model, paste0(tag, ", is the reader told it was retracted?"),
        judge_task(told(ha[keep, , drop = FALSE]), told(xa[keep]), c("told", "not told")))
    cf <- confusion(ha[keep, , drop = FALSE], xa[keep], LABEL_A)
    cf$model_name <- model
    cf$prompt <- v[[1]]
    confusions[[length(confusions) + 1L]] <- cf
  }

  # -- task B: each reason is its own yes/no question, clarity is ordinal.
  # The protocol's "use R0 alone" is enforced first; see task_b_enforce().
  failed <- !is.na(pb$error)
  pb <- task_b_enforce(pb)
  pb$R0_no_reason[failed] <- NA
  cat("  R0 corrected by the protocol's 'use R0 alone' rule:",
      sum(pb$fixed_R0_dropped & !failed, na.rm = TRUE), "dropped beside stated reasons,",
      sum(pb$fixed_R0_added & !failed, na.rm = TRUE), "added where nothing was marked,",
      "of", nrow(pb), "\n")
  for (r in names(hb$reasons)) {
    xr <- ifelse(is.na(pb[[r]]), NA, ifelse(toupper(pb[[r]]) == "TRUE", "Y", "N"))
    add(model, paste("B reason", r), judge_task(hb$reasons[[r]], xr, c("N", "Y")))
  }
  add(model, "B clarity",
      judge_task(hb$clarity, label_code(pb$clarity, LABEL_C), LABEL_C, "ordinal"))

  # -- running cost, and anything that says an answer cannot be trusted
  cols <- c("error", "prompt_tokens", "done_reason", "seconds")
  both <- rbind(pa1[, cols], pa[, cols], pb[, cols])
  tok <- as.numeric(both$prompt_tokens)
  add(model, "running", data.frame(
    measure = c("answers", "failed", "cut off at the answer limit",
                "prompt within 300 tokens of the context limit",
                "largest prompt (tokens)", "seconds per item, A v2", "seconds per item, B"),
    value = c(nrow(both), sum(!is.na(both$error)), sum(both$done_reason %in% "length"),
              sum(tok + LLM_NUM_PREDICT > LLM_NUM_CTX, na.rm = TRUE),
              max(tok, na.rm = TRUE),
              mean(as.numeric(pa$seconds), na.rm = TRUE),
              mean(as.numeric(pb$seconds), na.rm = TRUE)),
    stringsAsFactors = FALSE))
}

res <- do.call(rbind, rows)
res$value <- round(res$value, 4)
f <- file.path(OUT_REP, "llm-validation.csv")
utils::write.csv(res, f, row.names = FALSE, fileEncoding = "UTF-8")
cf <- do.call(rbind, confusions)
fc <- file.path(OUT_REP, "llm-validation-confusion-A.csv")
utils::write.csv(cf[cf$Freq > 0, c("model_name", "prompt", "model", "majority", "Freq")], fc,
                 row.names = FALSE, fileEncoding = "UTF-8")

# Side by side: one column per model.
wide <- reshape(res, idvar = c("task", "measure"), timevar = "model", direction = "wide")
names(wide) <- sub("^value\\.", "", names(wide))
old <- options(width = 200)
print(wide, row.names = FALSE, right = FALSE)
options(old)
cat("\nwritten:", f, "\n         ", fc, "\n")
