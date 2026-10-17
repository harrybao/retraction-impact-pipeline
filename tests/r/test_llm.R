# Checks for R/llm.R and R/llm_eval.R. Run from the project root:
#   Rscript tests/r/test_llm.R
#
# No model server is needed: the runner is tested against a fake one, because
# what can go wrong in it - a file that cannot be read back, a resume that
# skips failures or mixes two models' answers - has nothing to do with the
# model.

source("R/labelling.R")
source("R/llm.R")
source("R/llm_eval.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
near <- function(label, got, want, tol = 1e-9) {
  if (!isTRUE(abs(got - want) < tol))
    cat("      got:", format(got, digits = 6), " want:", want, "\n")
  ok(label, isTRUE(abs(got - want) < tol))
}

# -- the request ------------------------------------------------------------
b <- llm_body("p", TASK_A_SCHEMA, "m")
ok("thinking is switched off in the request, not the prompt", identical(b$think, FALSE))
ok("the context window is set explicitly", identical(b$options$num_ctx, LLM_NUM_CTX))
ok("temperature 0 and a fixed seed", b$options$temperature == 0 && !is.null(b$options$seed))

p <- TASK_A_PROMPT(NA, NA, NA)
ok("a missing field is empty, not the letters NA",
   !grepl("TITLE: NA", p, fixed = TRUE) && !grepl("SUMMARY: NA", p, fixed = TRUE))
ok("text with % signs passes through sprintf untouched",
   grepl("50% of", TASK_A_PROMPT("t", "50% of cases", "s"), fixed = TRUE))
ok("task B prompt carries the notice", grepl("XYZZY", TASK_B_PROMPT("XYZZY")))

# The prompts must carry the protocol's own words. These are the phrases whose
# absence from an earlier paraphrase changed the answers: R6's "systematic
# manipulation" (62 notices), and the rule that R0 stands alone.
proto <- paste(readLines("docs/labelling-protocol.md", encoding = "UTF-8"), collapse = "\n")
pb <- TASK_B_PROMPT("N", "T")
for (phrase in c("systematic manipulation, citation manipulation",
                 "Use R0 alone, never with R1-R9",
                 "fake reviewer accounts", "failure to reproduce", "legal issues",
                 "self-plagiarised text", "submitted in parallel")) {
  ok(paste("task B prompt carries the protocol's", dQuote(phrase, FALSE)),
     grepl(phrase, pb, fixed = TRUE) && grepl(phrase, proto, fixed = TRUE))
}
ok("task B prompt shows the paper title, as the sheet did", grepl("PAPER TITLE: T", pb))
ok("task B prompt says how to decide R0", grepl("if you can copy words", pb, fixed = TRUE))

# -- the one task B rule that is a constraint, not a judgement ----------------
d <- data.frame(R1_fabrication = c("TRUE", "FALSE", "FALSE", "TRUE"),
                R5_peer_review = c("FALSE", "FALSE", "TRUE", "FALSE"),
                R0_no_reason = c("TRUE", "TRUE", "FALSE", "FALSE"),
                stringsAsFactors = FALSE)
for (r in setdiff(names(TASK_B_REASONS), names(d))) d[[r]] <- "FALSE"
f <- task_b_enforce(d)
ok("R0 marked beside a reason is corrected to FALSE", f$R0_no_reason[1] == "FALSE")
ok("R0 alone is left alone", f$R0_no_reason[2] == "TRUE")
ok("no reason at all becomes R0", f$R0_no_reason[3] == "FALSE" && f$R0_no_reason[4] == "FALSE")
ok("a correction is reported", f$fixed_R0[1] && !f$fixed_R0[2])
ok("the two directions are counted apart",
   f$fixed_R0_dropped[1] && !f$fixed_R0_added[1] &&
     task_b_enforce(within(d, R0_no_reason <- rep("FALSE", 4)))$fixed_R0_added[2])
d2 <- d; d2[1, ] <- "FALSE"
ok("a notice with nothing marked becomes R0",
   task_b_enforce(d2)$R0_no_reason[1] == "TRUE" && task_b_enforce(d2)$fixed_R0[1])
ok("the stated reasons are never changed",
   identical(f$R1_fabrication, d$R1_fabrication) &&
     identical(f$R5_peer_review, d$R5_peer_review))
pa2 <- TASK_A_PROMPT("", "", "")
for (phrase in c("a demand, not an act", "a correction is not a retraction",
                 "It must be about this paper, not only about the people who wrote it",
                 "No if the text only names the work",
                 "The item names the paper, lists it, or is about something else")) {
  ok(paste("task A v2 prompt carries", dQuote(phrase, FALSE)),
     grepl(phrase, pa2, fixed = TRUE) && grepl(phrase, proto, fixed = TRUE))
}
ok("task A v2 prompt lists the CJK retraction words",
   grepl("撤稿", pa2, fixed = TRUE) && grepl("勘误", pa2, fixed = TRUE))
ok("no placeholder or escape left unfilled in any prompt",
   !any(grepl("\\{[A-Z_]+\\}|\\\\u[0-9a-f]{4}", c(pa2, pb, TASK_A_PROMPT_V1("", "", "")))))
ok("task A v1 prompt has the round-1 precedence rule",
   grepl("A2 > A3 > A1 > A4", TASK_A_PROMPT_V1("", "", ""), fixed = TRUE))
ok("every v2 example marker is in the v2 prompt",
   all(vapply(TASK_A_V2_EXAMPLE_MARKERS, grepl, logical(1), x = pa2, fixed = TRUE)))

fp <- prompt_fingerprint(TASK_A_PROMPT, "", "", "")
ok("fingerprint is stable", identical(fp, prompt_fingerprint(TASK_A_PROMPT, "", "", "")))
ok("fingerprint changes when the wording does",
   !identical(fp, prompt_fingerprint(function(...) paste(TASK_A_PROMPT(...), "!"), "", "", "")))

# -- parsing ------------------------------------------------------------------
r <- llm_parse(list(response = '{"label":"A2","evidence":"was retracted"}',
                    prompt_eval_count = 812L, done_reason = "stop"))
ok("parsed answer keeps its fields", identical(r$label, "A2"))
ok("parsed answer carries the prompt size", identical(r$prompt_tokens, 812L))
r <- llm_parse(list(response = "not json", prompt_eval_count = 5L, done_reason = "length"))
ok("unparseable answer is an error, with the reason it stopped",
   identical(r$error, "unparseable") && identical(r$done_reason, "length"))

# -- the runner, against a fake server ------------------------------------------
# The fake fails every item whose prompt mentions FAILME, on the first attempt
# only, so a resume has something to retry.
attempts <- new.env()
# The runner checks for a server before asking; the fake is the server.
llm_available <- function(host = LLM_HOST) TRUE
llm_json_many <- function(prompts, schema, model = LLM_MODEL, ...) {
  lapply(prompts, function(p) {
    k <- sub("^.*ITEM TITLE: ([^\n]*)\n.*$", "\\1", p)
    n <- (if (exists(k, attempts)) get(k, attempts) else 0) + 1
    assign(k, n, attempts)
    if (grepl("FAILME", k) && n == 1) return(list(error = "request failed"))
    list(label = "A1", evidence = paste0('says "', k, '", then, a comma\nand a newline'),
         prompt_tokens = 700L, done_reason = "stop")
  })
}
items <- data.frame(id = sprintf("X%02d", 1:7), paper_title = "p",
                    title = c("one", "two", "FAILME three", "four", "five", "six", "seven"),
                    summary = "s", stringsAsFactors = FALSE)
out <- tempfile(fileext = ".csv")
suppressMessages(llm_classify(items, task_a_prompt_row, TASK_A_SCHEMA, out, "A-test",
                              model = "fake", batch = 3L))
d <- llm_read(out)
raw <- readLines(out, encoding = "UTF-8")
ok("header written once", sum(grepl('^"id","model"', raw)) == 1L)
ok("every item has a row", setequal(d$id, items$id))
ok("columns are the fixed set", identical(names(d), llm_columns(TASK_A_SCHEMA)))
ok("the failure is recorded as one", identical(d$error[d$id == "X03"], "request failed"))
ok("quotes, commas and newlines in the evidence survive a round trip",
   identical(d$evidence[d$id == "X01"], 'says "one", then, a comma\nand a newline'))
ok("the model and prompt version are on every row",
   all(d$model == "fake") && all(d$prompt_version == "A-test"))

suppressMessages(llm_classify(items, task_a_prompt_row, TASK_A_SCHEMA, out, "A-test",
                              model = "fake", batch = 3L))
d <- llm_read(out)
ok("a resume retries the failure", is.na(d$error[d$id == "X03"]) &&
     identical(d$label[d$id == "X03"], "A1"))
ok("... and asks nothing it already has",
   get("one", attempts) == 1 && get("FAILME three", attempts) == 2)
ok("reading back keeps only the latest answer per item", nrow(d) == 7L)

ok("resuming into another model's file is refused",
   inherits(tryCatch(suppressMessages(llm_classify(items, task_a_prompt_row, TASK_A_SCHEMA,
                                                   out, "A-test", model = "other")),
                     error = function(e) e), "error"))
ok("resuming into another prompt version's file is refused",
   inherits(tryCatch(suppressMessages(llm_classify(items, task_a_prompt_row, TASK_A_SCHEMA,
                                                   out, "A-other", model = "fake")),
                     error = function(e) e), "error"))
# -- one writer per file ------------------------------------------------------
local({
  f <- tempfile(fileext = ".csv")
  lk <- llm_lock(f)
  ok("a lock is written beside the output", file.exists(lk))
  ok("the same process may re-take its own lock",
     !inherits(tryCatch(llm_lock(f), error = function(e) e), "error"))
  # Use a child we own: operating-system pids may be hidden in a sandbox.
  child <- processx::process$new(
    file.path(R.home("bin"), if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript"),
    c("--vanilla", "-e", "Sys.sleep(30)"), windows_hide_window = TRUE)
  on.exit(child$kill(), add = TRUE)
  utils::write.csv(data.frame(pid = child$get_pid(),
                              started = "2026-01-01 00:00:00"), lk, row.names = FALSE)
  ok("a lock held by a live process blocks",
     inherits(tryCatch(llm_lock(f), error = function(e) e), "error"))
  # A pid that cannot be running is stale and must be taken over, not obeyed.
  utils::write.csv(data.frame(pid = 999999999L, started = "2026-01-01 00:00:00"),
                   lk, row.names = FALSE)
  ok("a stale lock is taken over",
     !inherits(tryCatch(suppressMessages(llm_lock(f)), error = function(e) e), "error"))
  child$kill()
  unlink(c(f, lk))
})

ok("the answer-length ceiling leaves room for a long quote", LLM_NUM_PREDICT >= 512L)
# A repetition penalty changes answers, so it must never be on by default.
ok("no repetition penalty is sent unless asked for",
   is.null(llm_body("p", TASK_A_SCHEMA, "m")$options$repeat_penalty))
ok("... and is sent when asked for",
   identical(llm_body("p", TASK_A_SCHEMA, "m", repeat_penalty = 1.05)$options$repeat_penalty,
             1.05))

ok("duplicate ids are refused",
   inherits(tryCatch(llm_classify(items[c(1, 1), ], task_a_prompt_row, TASK_A_SCHEMA,
                                  tempfile(), "A-test", model = "fake"),
                     error = function(e) e), "error"))
unlink(out)

# -- judging against the labellers ------------------------------------------------
near("Cohen kappa, worked by hand",
     cohen_kappa(c("a", "a", "b", "b"), c("a", "b", "b", "b")), 0.5)
ok("Cohen kappa is NA with nothing to compare", is.na(cohen_kappa(NA, NA)))

# Four labellers, four items. L4 dissents on items 1 and 2.
m <- rbind(c("A", "A", "A", "B"),
           c("B", "B", "B", "A"),
           c("A", "A", "A", "A"),
           c("A", "B", "A", "B"))    # tied: no majority
dimnames(m) <- list(paste0("i", 1:4), paste0("L", 1:4))
am <- agree_with_majority(m, c("A", "B", "A", "A"))
ok("a tied item is left out of the agreement", am[["n"]] == 3)
near("agreement with the majority", am[["agreement"]], 1)
am <- agree_with_majority(m, c("B", "B", NA, "A"))
ok("an item the model failed on is left out", am[["n"]] == 2)
near("... and the rest scored", am[["agreement"]], 0.5)

loo <- leave_one_out(m, c("A", "B", "A", "A"))
# L4 set aside: L1-L3 give A, B, A, A; L4 said B, A, A, B -> 1 of 4.
near("the dissenting labeller against the other three",
     loo$human[loo$held_out == "L4"], 1 / 4)
# L1 set aside: L2-L4 give A, B, A, B; L1 said A, B, A, A -> 3 of 4.
near("an agreeing labeller against the other three",
     loo$human[loo$held_out == "L1"], 3 / 4)
near("the model against the same panels as L1", loo$model[loo$held_out == "L1"], 3 / 4)

j <- judge_task(m, c("A", "B", "A", "A"), c("A", "B"))
v <- function(what) j$value[j$measure == what]
near("judge: model = majority", v("model = majority of all labellers"), 1)
near("judge: human ceiling is the leave-one-out mean",
     v("one labeller = majority of the other six (mean)"), mean(loo$human))
ok("judge: alpha with the model added is computed",
   is.finite(v("Krippendorff alpha, labellers + model")))

mo <- rbind(c("C0", "C0", "C1"), c("C3", "C3", "C3"))
dimnames(mo) <- list(c("i1", "i2"), c("L1", "L2", "L3"))
w <- agree_with_majority(mo, c("C1", "C2"), LABEL_C, within = 1L)
near("within one step counts a neighbouring answer", w[["agreement"]], 1)
w <- agree_with_majority(mo, c("C1", "C2"), LABEL_C)
near("... and exact does not", w[["agreement"]], 0)
jo <- judge_task(mo, c("C0", "C3"), LABEL_C, "ordinal")
ok("ordinal judgement reports within-one figures",
   any(jo$measure == "model within one step of majority"))

cf <- confusion(m, c("A", "A", "A", "A"), c("A", "B"))
ok("confusion counts model x majority over items with a majority",
   sum(cf$Freq) == 3 && cf$Freq[cf$model == "A" & cf$majority == "B"] == 1)

cat(if (fails) sprintf("\n%d FAILED\n", fails) else "\nall passed\n")
quit(status = if (fails) 1L else 0L)
