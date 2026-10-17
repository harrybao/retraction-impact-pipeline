# The local model, and the prompts that ask it what the labellers were asked.
#
# The project has no budget for a commercial API, so the classification runs
# on an open-weight model on this machine. That constraint decides two things.
#
# **It must be reproducible by him.** So the model, its quantisation and the
# exact prompt are recorded with every answer, and the runner is resumable:
# 175,000 calls will be interrupted.
#
# **It must be comparable with the humans.** The prompts below are the v2
# decision procedure from docs/labelling-protocol.md, in the same order, with
# the same stopping rule. Asking the model a differently-worded question and
# then comparing its answers with the labellers' would measure the difference
# between two questions, not between a model and a person.
#
# Every answer carries its evidence, because the client asked for a reason
# with every label.

LLM_HOST <- Sys.getenv("CS43_LLM_HOST", "http://127.0.0.1:11434")
LLM_MODEL <- Sys.getenv("CS43_LLM_MODEL", "qwen3:14b")

# The context window is set, not left to the server. Ollama's default depends on
# its version and the GPU it finds, and when a prompt is longer than the window
# the server drops the START of it - which is where the instructions are - and
# still answers. A notice that silently lost its instructions would come back
# as confident, well-formed nonsense. Every answer records how many prompt
# tokens the server read, so a truncated one can be found afterwards.
LLM_NUM_CTX <- as.integer(Sys.getenv("CS43_LLM_NUM_CTX", "4096"))

# The answer-length ceiling. Raised from 300 to 512 after an answer came back
# with done_reason "length". A ceiling is not a tuning knob: decoding is greedy
# at temperature 0, so the token sequence is identical whatever the cap and
# raising it only stops a long answer being cut. Answers that already completed
# are unaffected, which is why this could change mid-corpus.
#
# It does not rescue every truncated answer, and the one it does not is worth
# recording. Item 1ab7b6a90169b4c8 is a Portuguese Reddit comment on which the
# model enters a repetition loop inside the `evidence` string - "que foi usado
# por varios politicos... que foi usado..." - and never closes the JSON. 2,000
# tokens does not finish it. `repeat_penalty` does: 1.05 terminates and gives
# A2. But a penalty is NOT answer-neutral - at 1.20 the label itself moves to
# A3, and at 1.10 an item that was already fine loses its evidence quote - so
# answering one item under settings the other 86,000 did not use would buy a
# label by making it incomparable. It is left unclassified and counted as such.
LLM_NUM_PREDICT <- as.integer(Sys.getenv("CS43_LLM_NUM_PREDICT", "512"))

# Is a local model server answering?
llm_available <- function(host = LLM_HOST) {
  ok <- tryCatch({
    r <- httr2::request(paste0(host, "/api/tags"))
    r <- httr2::req_timeout(r, 5)
    r <- httr2::req_error(r, is_error = function(x) FALSE)
    httr2::resp_status(httr2::req_perform(r)) == 200
  }, error = function(e) FALSE)
  isTRUE(ok)
}

llm_models <- function(host = LLM_HOST) {
  r <- httr2::request(paste0(host, "/api/tags"))
  d <- httr2::resp_body_json(httr2::req_perform(r))
  vapply(d$models, function(m) m$name, character(1))
}

# `format` is a JSON schema the server constrains the output to, so the answer
# parses or the call fails - it never arrives as prose a regex has to guess at.
#
# `think = FALSE` is not a tuning knob, it is required. Qwen3 reasons before
# answering by default, and that reasoning goes to a separate `thinking` field
# while `response` stays empty until it finishes. With a 300-token budget the
# budget is spent thinking, `response` comes back empty, and every answer looks
# unparseable. Measured on one item: thinking on, 4.9 s and nothing usable;
# thinking off, 1.8 s and 22 tokens of clean JSON. Note that writing /no_think
# into the prompt does NOT do this - the API parameter does.
llm_body <- function(prompt, schema, model, temperature = 0, seed = 20260925,
                     num_predict = LLM_NUM_PREDICT, num_ctx = LLM_NUM_CTX,
                     repeat_penalty = NULL) {
  opts <- list(temperature = temperature, seed = seed,
               num_predict = num_predict, num_ctx = num_ctx)
  if (!is.null(repeat_penalty)) opts$repeat_penalty <- repeat_penalty
  list(model = model, prompt = prompt, stream = FALSE, think = FALSE,
       format = schema, keep_alive = "30m", options = opts)
}

`%||%` <- function(x, y) if (is.null(x)) y else x

# The parsed answer, plus what the server says about how it produced it:
# `prompt_tokens` (to find a prompt that did not fit) and `done_reason`
# ("length" means the answer was cut off at num_predict).
llm_parse <- function(out) {
  meta <- list(prompt_tokens = out$prompt_eval_count %||% NA_integer_,
               done_reason = out$done_reason %||% NA_character_)
  if (is.null(out) || is.null(out$response)) return(c(list(error = "no response"), meta))
  parsed <- tryCatch(jsonlite::fromJSON(out$response), error = function(e) NULL)
  if (is.null(parsed) || !length(parsed)) {
    return(c(list(error = "unparseable", raw = substr(out$response %||% "", 1, 200)), meta))
  }
  c(parsed, meta)
}

llm_json <- function(prompt, schema, model = LLM_MODEL, host = LLM_HOST,
                     temperature = 0, seed = 20260925, timeout = 180,
                     num_predict = LLM_NUM_PREDICT, repeat_penalty = NULL) {
  r <- httr2::request(paste0(host, "/api/generate"))
  r <- httr2::req_body_json(r, llm_body(prompt, schema, model, temperature, seed,
                                        num_predict = num_predict,
                                        repeat_penalty = repeat_penalty),
                            auto_unbox = TRUE)
  r <- httr2::req_timeout(r, timeout)
  r <- httr2::req_error(r, is_error = function(x) FALSE)
  resp <- httr2::req_perform(r)
  if (httr2::resp_status(resp) != 200) {
    return(list(error = paste("http", httr2::resp_status(resp))))
  }
  llm_parse(httr2::resp_body_json(resp))
}

# Several prompts at once. One request at a time leaves the GPU idle between
# them: the model answers in under two seconds but 175,000 of those in series
# is 87 hours. Ollama serves OLLAMA_NUM_PARALLEL requests concurrently, so the
# work is sent in groups of that size.
llm_json_many <- function(prompts, schema, model = LLM_MODEL, host = LLM_HOST,
                          temperature = 0, seed = 20260925, timeout = 300) {
  reqs <- lapply(prompts, function(p) {
    r <- httr2::request(paste0(host, "/api/generate"))
    r <- httr2::req_body_json(r, llm_body(p, schema, model, temperature, seed),
                              auto_unbox = TRUE)
    r <- httr2::req_timeout(r, timeout)
    httr2::req_error(r, is_error = function(x) FALSE)
  })
  resps <- httr2::req_perform_parallel(reqs, on_error = "continue")
  lapply(resps, function(resp) {
    if (inherits(resp, "error") || is.null(resp)) return(list(error = "request failed"))
    if (httr2::resp_status(resp) != 200) {
      return(list(error = paste("http", httr2::resp_status(resp))))
    }
    llm_parse(httr2::resp_body_json(resp))
  })
}

# ------------------------------------------------------------------ task A --

TASK_A_SCHEMA <- list(
  type = "object",
  properties = list(
    label = list(type = "string", enum = c("A0", "A1", "A2", "A3", "A4")),
    evidence = list(type = "string")),
  required = list("label", "evidence"))

# The prompts are the protocol, not a summary of it.
#
# The first prompts here paraphrased docs/labelling-protocol.md, and the
# paraphrase dropped words that mattered. The labellers' definition of R6 reads
# "paper mill, computer-generated or nonsensical text, systematic manipulation,
# citation manipulation"; the prompt's read "paper mill, or computer-generated
# content". Hindawi's standard notice says "systematic manipulation of the
# publication process", so the humans marked R6 on 62 of 200 notices and both
# models on 7, filing the rest under "other" - which made the model look unable
# to read a notice when it had been asked a different question. The prompt also
# never said "Use R0 alone, never with R1-R9", and one model broke that rule 12
# times. So the text below is the protocol's, table rows and examples included,
# and nothing is reworded except where the humans were told to use a tool the
# model does not have.
#
# The CJK retraction words are written as escapes so this file reads the same
# in any locale; they are filled in below.

# Task A as round 1 labelled it: five descriptions and an order of precedence
# (protocol v1, the version in CS43_labelling_protocol.docx when the sheets went
# out). Used only to validate against round 1 - asking the model v2 and
# comparing it with people who answered v1 measures the protocol change as much
# as the model.
TASK_A_V1_TEMPLATE <- r"---(You are labelling a media item about a scientific paper that was retracted.

Read the paper's title (what the paper claims), then the media item's title and summary. Choose one category.

A1 Repeats or supports the original finding - presents the paper's finding as valid, reports it as news, or uses it as evidence. There is no mention of problems or of a retraction.
A2 Acknowledges the retraction - says the paper has been, or is being, retracted, withdrawn, or placed under an expression of concern. This applies whatever else the item says.
A3 Questions or corrects the finding - raises doubt about the finding or the paper: criticism, errors, misconduct allegations, an investigation, a failed replication, a correction. It does not mention a formal retraction or expression of concern.
A4 Unrelated - does not engage with the finding at all. Examples: the paper appears only in a list or a citation, the item is about something else, or only the journal or authors are mentioned.
A0 Cannot tell - the text is empty, too short to judge, or in a language you cannot read even with a translation tool.

Precedence when several apply: A2 > A3 > A1 > A4. For example, an item that criticises the paper and mentions its retraction is A2.

Examples, for a paper titled "Hydroxychloroquine... multinational registry analysis":
  "Malaria drug linked to higher death risk in COVID patients, study finds" -> A1
  "Lancet retracts hydroxychloroquine study after data questioned" -> A2
  "Scientists raise concerns over data behind hydroxychloroquine study" -> A3
  "Weekly roundup: 20 papers on COVID treatments" (no discussion of this one) -> A4

Judge only the text shown. Do not use what you know about the paper from elsewhere. A summary that is cut off mid-sentence is still judged on what is there.

PAPER TITLE: %s

ITEM TITLE: %s

ITEM SUMMARY: %s

In "evidence" copy the words that decided the label (up to 15), for example "the study, which has since been retracted". It is required for A1-A3 and may be empty for A4 and A0.)---"

# Task A as it stands: protocol v2, four questions in a fixed order. This is
# what round 2 labels and what the corpus is classified with.
TASK_A_V2_TEMPLATE <- r"---(You are labelling a media item about a scientific paper that was retracted.

Read the paper's title (what the paper claims), then the item's title and summary. Then work down these four questions in a fixed order and stop at the first "yes". You are not choosing which description fits best; you are answering yes or no, in order.

Q0. Can you read it at all?
Yes -> A0. The item text (title and summary together) is empty, is fewer than 10 words, or is in a script you cannot read even after translating it.
"I am not sure which label to give" is not A0. If you can read the text you must answer Q1-Q4. A0 is about the text being unavailable, not about being undecided.

Q1. Does the text say the retraction HAS HAPPENED?
Yes -> A2. The text states that the paper has been, or has been decided to be, retracted, withdrawn, removed by the journal, or placed under an expression of concern - retracted, retraction, withdrawn, pulled, removed by the journal, expression of concern, {RETRACT_WORDS}.
It must have happened or been decided. These are NOT A2 - go to Q2:
  "Scientists are calling for the paper to be retracted" - a demand, not an act
  "The committee recommended retracting 10 papers" - a recommendation, not an act
  "There is a petition to retract" - a demand
  "The journal issued an erratum" / {ERRATUM_WORD} - a correction is not a retraction
  "The journal is investigating" - an investigation is not a retraction
This applies whatever else the item says. An item that praises the finding and reports that it was retracted is A2.

Q2. Does the text raise doubt about THIS paper?
Yes -> A3. It questions or corrects this paper - its data, methods, images, results or integrity. This includes an investigation into it, a failed replication, allegations about it, an erratum or correction to it, and a demand that it be retracted.
It must be about this paper, not only about the people who wrote it. An item about a researcher's misconduct, a prison sentence or an institution's record is not A3 unless the text also says something about this paper.
  "Figure 3 of the study appears duplicated" -> A3
  "Hundreds of scholars demand the study be retracted" -> A3
  "Chinese scientist who edited embryos sentenced to prison", this paper not discussed -> not A3, continue

Q3. Does the text say what THIS paper found or claimed?
Yes -> A1. The text reports a result, a conclusion or a recommendation - whether in the item's own words or by reproducing the paper's own title or abstract.
No if the text only names the work. A citation string, a reference-list entry, an author list, or an item on the same subject that never says what this paper found - none of those state a finding.
  "A poop transplant could boost the effects of immunotherapy" -> A1, a finding is stated
  "Abstract: The novel tetraaza-macrobicyclic chelator..." -> A1, the paper's own claim
  "Woodworth, M. H., et al. Fecal microbiota transplantation..." -> not A1, a citation
  "Memrise launches immersive language learning music lessons" -> not A1, a product, no finding

Q4. Otherwise -> A4. The item names the paper, lists it, or is about something else.

Judge only the text shown. Do not use what you know about the paper from elsewhere. A summary that is cut off mid-sentence is still judged on what is there.

PAPER TITLE: %s

ITEM TITLE: %s

ITEM SUMMARY: %s

In "evidence" copy the words that decided your answer (up to 15). If you answered A2 they should be the words saying it happened; if A1, the words stating the finding. It may be empty for A4 and A0.)---"
TASK_A_V2_TEMPLATE <- sub("{RETRACT_WORDS}",
                          "\u64a4\u7a3f, \u64a4\u56de, \u64a4\u9500, \u53d6\u308a\u4e0b\u3052",
                          TASK_A_V2_TEMPLATE, fixed = TRUE)
TASK_A_V2_TEMPLATE <- sub("{ERRATUM_WORD}", "\u52d8\u8bef", TASK_A_V2_TEMPLATE, fixed = TRUE)

# v2's examples were written from items the round-1 labellers split on, so some
# of them ARE items in the validation sample. A model shown the answer to an
# item cannot be scored on it; these mark the items to leave out when scoring
# the v2 prompt.
TASK_A_V2_EXAMPLE_MARKERS <- c("poop transplant", "tetraaza-macrobicyclic", "Woodworth",
                               "Memrise", "edited embryos")

TASK_A_PROMPT_V1 <- function(paper_title, item_title, item_summary) {
  sprintf(TASK_A_V1_TEMPLATE, nz(paper_title), nz(item_title), nz(item_summary))
}
TASK_A_PROMPT <- function(paper_title, item_title, item_summary) {
  sprintf(TASK_A_V2_TEMPLATE, nz(paper_title), nz(item_title), nz(item_summary))
}

# A missing field is empty text. sprintf would otherwise write the letters "NA"
# into the prompt, and the model would read them as the item's title.
nz <- function(x) ifelse(is.na(x), "", as.character(x))

# ------------------------------------------------------------------ task B --

# Group name and "includes", exactly as the protocol's table has them.
TASK_B_REASONS <- c(
  R1_fabrication = "Fabrication or falsification: fabricated or falsified data or results",
  R2_image = "Image problems: duplicated, manipulated or reused figures or images",
  R3_plagiarism = "Plagiarism: copied text, ideas or data from others; self-plagiarised text",
  R4_duplicate = "Duplicate publication: the same work published twice or submitted in parallel",
  R5_peer_review = "Peer review problems: compromised, manipulated or fake peer review; fake reviewer accounts",
  R6_paper_mill = "Paper mill or generated content: paper mill, computer-generated or nonsensical text, systematic manipulation, citation manipulation",
  R7_honest_error = "Honest error: errors in data, analysis, methods or results, not attributed to misconduct; failure to reproduce",
  R8_authorship_ethics = "Authorship, ethics or consent: authorship disputes, missing ethics approval, no patient consent, undisclosed conflicts of interest, legal issues",
  R9_other = "Other: a stated reason that fits none of the above",
  R0_no_reason = "No reason stated: the notice gives no reason at all. Use R0 alone, never with R1-R9.")

TASK_B_SCHEMA <- local({
  props <- lapply(names(TASK_B_REASONS), function(x) list(type = "boolean"))
  names(props) <- names(TASK_B_REASONS)
  props$clarity <- list(type = "string", enum = c("C0", "C1", "C2", "C3"))
  props$evidence <- list(type = "string")
  list(type = "object", properties = props,
       required = as.list(c(names(TASK_B_REASONS), "clarity", "evidence")))
})

TASK_B_TEMPLATE <- r"---(You are labelling the retraction notice of a scientific paper.

Read the notice. Then:

B1. Stated reasons. Mark true every reason that applies - a notice may state several. Label only what the notice STATES, not what you suspect.
{REASONS}

Deciding R0. R0 means the notice states NO reason at all. A vague or generic
reason is still a reason: put it in the group that fits, or in R9 if none does.
The test is B3 below - if you can copy words from the notice that state a
reason, it is not R0. Only when there is nothing to copy is it R0, and then R0
stands alone.

B2. Clarity. Choose one.
  C3 Clear - names the specific problem and says why it undermines the paper, e.g. "Figure 3B duplicates Figure 2A of another article".
  C2 Partial - names a kind of problem without specifics, e.g. "concerns were raised about the data".
  C1 Vague - uses only generic language that identifies no problem, e.g. "following an investigation", "at the request of the authors", "for reasons beyond our control".
  C0 No reason - gives no reason, e.g. "This article has been retracted." Always pair it with R0.

Who asked for the retraction (authors, editor, institution) is not a reason. A notice saying only "retracted at the authors' request" is R0 with C1.

Judge only the text shown.

PAPER TITLE: %s

NOTICE:
%s

B3. In "evidence" copy the words that state the reason (up to 15). It may be empty only if the answer is R0 with C0.)---"
TASK_B_TEMPLATE <- sub("{REASONS}",
                       paste(sprintf("  %s - %s", names(TASK_B_REASONS), TASK_B_REASONS),
                             collapse = "\n"),
                       TASK_B_TEMPLATE, fixed = TRUE)

TASK_B_PROMPT <- function(notice, paper_title = "") {
  sprintf(TASK_B_TEMPLATE, nz(paper_title), nz(notice))
}

# The protocol states one rule about task B that is a hard constraint rather
# than a judgement: "Use R0 alone, never with R1-R9." An answer that breaks it
# is not a different reading of the notice, it is a malformed answer, and
# adjudication of round 1 found 19 of 200 notices where the model marked R0
# together with reasons it had itself assigned. Where the reasons and R0
# contradict each other the reasons are kept, because they carry the evidence.
#
# Returns the corrected columns plus `fixed_R0`, so how often this fires is
# reported rather than silently absorbed. Nothing else is corrected: every
# other disagreement is a reading of the notice, and overriding those would be
# tuning the model's answers rather than enforcing the protocol.
task_b_enforce <- function(d, reasons = names(TASK_B_REASONS)) {
  stated <- setdiff(reasons, "R0_no_reason")
  yes <- function(x) toupper(as.character(x)) %in% c("TRUE", "Y", "YES", "1")
  any_stated <- Reduce(`|`, lapply(stated, function(r) yes(d[[r]])))
  was <- yes(d$R0_no_reason)
  d$R0_no_reason <- ifelse(any_stated, "FALSE", "TRUE")
  now <- d$R0_no_reason == "TRUE"
  # The two directions mean different things and are counted apart. Dropping an
  # R0 that sat beside stated reasons is the protocol's explicit prohibition.
  # Adding one where the model marked nothing at all is the same rule read the
  # other way - a notice states some reason or it states none - but it turns a
  # silence into an assertion, so it is reported, not buried.
  d$fixed_R0 <- was != now
  d$fixed_R0_dropped <- was & !now
  d$fixed_R0_added <- !was & now
  d
}

# One prompt per row of an item table, for llm_classify(). Task A rows carry
# the columns of data/labelling/key_A.csv, task B rows those of key_B.csv.
task_a_prompt_row <- function(row) TASK_A_PROMPT(row$paper_title, row$title, row$summary)
task_a_v1_prompt_row <- function(row) {
  TASK_A_PROMPT_V1(row$paper_title, row$title, row$summary)
}
task_b_prompt_row <- function(row) {
  TASK_B_PROMPT(row$notice, if (is.null(row$paper_title)) "" else row$paper_title)
}

# ------------------------------------------------------------------ runner --

LLM_BATCH <- as.integer(Sys.getenv("CS43_LLM_BATCH", "4"))

# A short fingerprint of a prompt template: the template filled with empty
# fields, hashed. It goes into the prompt version, so an edit to the wording
# changes the version by itself - a label a human forgot to bump cannot let two
# different prompts' answers into one file.
prompt_fingerprint <- function(prompt_fn, ...) {
  f <- tempfile(fileext = ".txt")
  on.exit(unlink(f))
  writeLines(enc2utf8(prompt_fn(...)), f, useBytes = TRUE)
  substr(unname(tools::md5sum(f)), 1, 8)
}

# Every output file has these columns in this order, whatever came back. A
# failed answer has the label columns empty and `error` filled, a good one the
# reverse - one shape for both, so the file stays one table. (An earlier runner
# wrote each answer's own fields, so a failure row and a success row had
# different columns and the file could not be read back.)
llm_columns <- function(schema) {
  c("id", "model", "prompt_version", names(schema$properties),
    "error", "prompt_tokens", "done_reason", "seconds")
}

llm_flatten <- function(res, id, model, prompt_version, schema, seconds) {
  cols <- llm_columns(schema)
  out <- stats::setNames(as.list(rep(NA_character_, length(cols))), cols)
  out$id <- id
  out$model <- model
  out$prompt_version <- prompt_version
  for (f in setdiff(intersect(names(res), cols), c("id", "model", "prompt_version"))) {
    v <- res[[f]]
    if (length(v)) out[[f]] <- as.character(v)[1]
  }
  out$seconds <- sprintf("%.3f", seconds)
  as.data.frame(out, stringsAsFactors = FALSE, check.names = FALSE)
}

# Only one writer per output file.
#
# The runner reads what is done, then appends. Two runs over the same file would
# both read the same "done" set, both classify the same items, and both append -
# doubling the work and leaving duplicate ids whose resolution depends on write
# interleaving. Nothing prevented that, and a 16-hour resumable job invites it:
# the obvious way to check on a run is to start it again.
#
# The lock records the process id and when it was taken. A lock whose process is
# gone is stale and is taken over rather than blocking for ever, which is the
# failure mode that makes people delete lock files by hand.
llm_lock <- function(out_csv) {
  path <- paste0(out_csv, ".lock")
  alive <- function(pid) {
    if (is.na(pid)) return(FALSE)
    # Native enumeration works without tasklist's RPC service and its
    # localized output. ps is already locked as a processx dependency.
    pid %in% ps::ps_pids()
  }
  if (file.exists(path)) {
    prev <- tryCatch(utils::read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
    pid <- if (is.null(prev)) NA_integer_ else suppressWarnings(as.integer(prev$pid[1]))
    if (!is.na(pid) && pid != Sys.getpid()) {
      if (alive(pid)) {
        stop("another run is already writing ", basename(out_csv), " (pid ", pid,
             ", started ", prev$started[1], ").\n",
             "  Wait for it, or stop it. Two runs would classify the same items twice.",
             call. = FALSE)
      }
      message("taking over a stale lock on ", basename(out_csv),
              " (pid ", pid, " is no longer running)")
    }
  }
  utils::write.csv(data.frame(pid = Sys.getpid(),
                              started = format(Sys.time(), "%Y-%m-%d %H:%M:%S"),
                              stringsAsFactors = FALSE),
                   path, row.names = FALSE)
  path
}

# What a run has written so far: the LAST answer per id, because a resumed run
# appends its retry after the failure it replaces.
llm_read <- function(out_csv) {
  d <- utils::read.csv(out_csv, stringsAsFactors = FALSE, colClasses = "character",
                       na.strings = "", fileEncoding = "UTF-8")
  d[!duplicated(d$id, fromLast = TRUE), , drop = FALSE]
}

# Classify a table of items, resumably. Each answer is appended as soon as its
# batch returns, so an interrupted run loses at most one batch. Failed answers
# are retried on the next run; good ones are never asked again.
#
# Every answer is written with the model name and the prompt version, because a
# result that cannot say which model produced it cannot be reproduced, and this
# one has to be. For the same reason a file is never shared between models or
# prompt versions: resuming into a file another configuration wrote is refused.
llm_classify <- function(items, prompt_fn, schema, out_csv, prompt_version,
                         model = LLM_MODEL, batch = LLM_BATCH, host = LLM_HOST,
                         limit = NULL, report_every = 200L) {
  stopifnot("id" %in% names(items), !anyDuplicated(items$id),
            is.character(prompt_version), batch >= 1L)
  items$id <- as.character(items$id)
  dir.create(dirname(out_csv), showWarnings = FALSE, recursive = TRUE)
  lock <- llm_lock(out_csv)
  on.exit(unlink(lock), add = TRUE)
  done <- character()
  if (file.exists(out_csv)) {
    prev <- llm_read(out_csv)
    if (!identical(names(prev), llm_columns(schema))) {
      stop(out_csv, " has different columns from this task; write to a new file",
           call. = FALSE)
    }
    seen <- unique(paste(prev$model, prev$prompt_version))
    if (length(setdiff(seen, paste(model, prompt_version)))) {
      stop(out_csv, " holds answers from ", paste(seen, collapse = ", "),
           "; not mixing them with ", model, " ", prompt_version, call. = FALSE)
    }
    done <- prev$id[is.na(prev$error)]
    message("resuming ", basename(out_csv), ": ", length(done), " done, ",
            sum(!is.na(prev$error)), " failed before and will be retried")
  } else {
    dir.create(dirname(out_csv), showWarnings = FALSE, recursive = TRUE)
  }
  todo <- items[!items$id %in% done, , drop = FALSE]
  if (!is.null(limit)) todo <- utils::head(todo, limit)
  if (!nrow(todo)) {
    message("nothing to do")
    return(invisible(out_csv))
  }
  # With no server, asking would only append "request failed" rows to a file
  # of saved answers. Someone reproducing the tables from those answers needs
  # no model, so the file is left as it is and the gap is reported.
  if (!llm_available(host)) {
    message("no model server at ", host, ": ", nrow(todo),
            " items left unanswered; the saved answers are used as they are")
    return(invisible(out_csv))
  }
  message(nrow(todo), " to classify with ", model, ", ", batch, " at a time")

  t0 <- Sys.time()
  n <- 0L
  failed <- 0L
  next_report <- report_every
  for (chunk in split(seq_len(nrow(todo)), ceiling(seq_len(nrow(todo)) / batch))) {
    prompts <- vapply(chunk, function(i) prompt_fn(todo[i, , drop = FALSE]), character(1))
    t1 <- Sys.time()
    res <- llm_json_many(prompts, schema, model = model, host = host)
    secs <- as.numeric(difftime(Sys.time(), t1, units = "secs")) / length(chunk)
    rows <- do.call(rbind, lapply(seq_along(chunk), function(j) {
      llm_flatten(res[[j]], todo$id[chunk[j]], model, prompt_version, schema, secs)
    }))
    new_file <- !file.exists(out_csv)
    utils::write.table(rows, out_csv, sep = ",", qmethod = "double", na = "",
                       row.names = FALSE, col.names = new_file, append = !new_file,
                       fileEncoding = "UTF-8")
    n <- n + length(chunk)
    failed <- failed + sum(!is.na(rows$error))
    if (n >= next_report || n == nrow(todo)) {
      el <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
      message(sprintf("  %d/%d  %.2f items/s  %d failed  %.1f h left", n, nrow(todo),
                      n / el, failed, (nrow(todo) - n) / (n / el) / 3600))
      next_report <- next_report + report_every
    }
  }
  invisible(out_csv)
}
