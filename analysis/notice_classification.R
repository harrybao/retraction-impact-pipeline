# What do retraction notices say, and how clearly?
#
#   Rscript analysis/notice_classification.R [model]
#
# The client asked for this alongside the media analysis, broken down in his
# order: reason, country, publisher, discipline. Run
# analysis/llm_classify_corpus.R B first; this reads its answers.
#
# **The unit is the notice text, then the paper.** 85,896 notices are only
# 60,605 distinct texts, because publishers reuse boilerplate - one Hindawi
# template appears in thousands. The model is asked once per distinct text and
# the answer is carried back to every notice that uses it, so a template is
# read once and counted once per paper, not once per model call.
#
# **The reasons are reported, the clarity is reported with a warning.** Against
# the seven labellers the model matches or beats one labeller on eight of the
# ten reason columns, but on clarity it reaches .492 where a labeller reaches
# .710, and the labellers themselves only reach alpha .545 there. So clarity
# appears with both figures next to it and is not used to rank anything.
#
# **"No reason given" is the number most easily got wrong**, and this project
# has already seen how: a prompt that paraphrased the protocol sent 8,997 IEEE
# notices and 11,445 paper-mill notices to the wrong column. The tables below
# therefore report the stated-reason counts and the R0 count together, and how
# often the protocol's "use R0 alone" rule had to correct the answer.

source("R/labelling.R")   # LABEL_C, the clarity scale the labellers used
source("R/llm.R")
source("R/llm_text.R")
source("R/analysis_data.R")
source("R/citation_panel.R")  # panel_dimensions()
source("R/publisher_families.R")

args <- commandArgs(trailingOnly = TRUE)
model <- if (length(args)) args[1] else LLM_MODEL
slug <- gsub("[^A-Za-z0-9]+", "-", model)
CORPUS <- file.path("data", "llm", "corpus")
OUT <- file.path("data", "reports")
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)
MIN_PAPERS <- 30L

map <- utils::read.csv(file.path(CORPUS, "items_B_map.csv"), colClasses = "character",
                       encoding = "UTF-8")
f <- list.files(CORPUS, sprintf("^%s_B_B-v2-.*[^d]\\.csv$", slug), full.names = TRUE)
f <- f[!grepl("_enforced\\.csv$", f)]
if (!length(f)) stop("no task B answers for ", model, " in ", CORPUS, call. = FALSE)
ans <- task_b_enforce(llm_read(f[1]))
# The protocol's R9 definition applied to the answers: a notice citing a
# violated policy states a reason (R/notice_rules.R; measured on the 200
# validation notices by analysis/notice_r0_check.R). It needs each answer's
# text, rebuilt exactly as analysis/llm_classify_corpus.R built it.
source("R/notice_rules.R")
nt <- utils::read.csv(gzfile(file.path("data", "clean", "stage2_notices.csv.gz")),
                      colClasses = "character", encoding = "UTF-8")
nt <- nt[!is.na(nt$notice) & nzchar(trimws(nt$notice)) & nchar(nt$notice) >= 50, ]
nt$notice <- decode_entities(nt$notice)
nt <- nt[nchar(nt$notice) >= 50, ]
nt$notice <- substr(nt$notice, 1, 3000L)
nt$id <- vapply(nt$notice, function(s) substr(unname(tools::md5sum(bytes = charToRaw(enc2utf8(s)))), 1, 16),
                character(1), USE.NAMES = FALSE)
ans <- notice_policy_rule(ans, nt$notice[match(ans$id, nt$id)])
cat("answers moved from 'no reason' to 'other' by the policy-violation rule:",
    sum(ans$fixed_policy & is.na(ans$error), na.rm = TRUE), "\n")
cat("model:", model, "  distinct texts answered:", sum(is.na(ans$error)), "\n")
cat("R0 corrected by the protocol's 'use R0 alone' rule:",
    sum(ans$fixed_R0_dropped & is.na(ans$error), na.rm = TRUE), "dropped,",
    sum(ans$fixed_R0_added & is.na(ans$error), na.rm = TRUE), "added\n")
ans <- ans[is.na(ans$error), ]

# Back to notices, then to the retracted papers those notices belong to.
d <- merge(map, ans, by = "id")
cat("notices covered:", nrow(d), "of", nrow(map), "\n")
rec <- utils::read.csv(file.path("data", "clean", "records_clean.csv"),
                       colClasses = "character", encoding = "UTF-8")
rec <- rec[!is.na(rec$retraction_doi) & nzchar(rec$retraction_doi) &
           !is.na(rec$original_doi) & nzchar(rec$original_doi), ]
# A notice's DOI in items_B_map.csv is usually the notice's own, but 32,124 of
# the 85,896 are the retracted ARTICLE's DOI: that text is the retraction
# banner on the article page. So the join is on either DOI, and a paper with
# both keeps its notice page - the fuller text - over the banner.
cols <- c("record_id", "original_doi", "retraction_doi", "publisher", "retraction_nature")
on_notice <- merge(rec[, cols], d, by.x = "retraction_doi", by.y = "retraction_doi")
on_article <- merge(rec[, cols], d, by.x = "original_doi", by.y = "retraction_doi")
on_notice$via <- "notice page"
on_article$via <- "article-page banner"
pap <- rbind(on_notice, on_article[, names(on_notice)])
# A paper with several notices keeps its Retraction notice: an expression of
# concern or a correction on the same paper usually gives no reason for a
# retraction, and taking whichever DOI sorts first chose one of those for
# many papers and pushed "no reason given" up.
pap <- pap[order(pap$via != "notice page", pap$retraction_nature != "Retraction",
                 pap$original_doi, pap$retraction_doi), ]
pap <- pap[!duplicated(pap$original_doi), ]
cat("retracted papers with a classified notice:", nrow(pap), "\n")
print(table(text_from = pap$via))

yes <- function(x) toupper(as.character(x)) %in% c("TRUE", "Y", "YES", "1")
w <- function(x, name) {
  p <- file.path(OUT, sprintf("notice-classification-%s.csv", name))
  utils::write.csv(x, p, row.names = FALSE, fileEncoding = "UTF-8")
  cat("written:", p, "\n")
}

# -------------------------------------------------------------- overall ---
overall <- data.frame(
  reason = names(TASK_B_REASONS),
  notices = vapply(names(TASK_B_REASONS), function(r) sum(yes(d[[r]])), integer(1)),
  papers = vapply(names(TASK_B_REASONS), function(r) sum(yes(pap[[r]])), integer(1)),
  stringsAsFactors = FALSE)
overall$share_of_papers <- round(100 * overall$papers / nrow(pap), 1)
overall <- overall[order(-overall$papers), ]
cat("\n-- stated reasons, over", nrow(pap), "papers --\n")
print(overall, row.names = FALSE)
w(overall, "reasons-overall")

n_reasons <- rowSums(vapply(setdiff(names(TASK_B_REASONS), "R0_no_reason"),
                            function(r) yes(pap[[r]]), logical(nrow(pap))))
cat("\nreasons stated per paper:\n")
print(table(n_reasons))

cl <- data.frame(table(clarity = factor(pap$clarity, LABEL_C)))
cl$share <- round(100 * cl$Freq / sum(cl$Freq), 1)
cat("\n-- clarity (model .492 against a labeller's .710; labellers' alpha .545) --\n")
print(cl, row.names = FALSE)
w(cl, "clarity-overall")

# ----------------------------------------------------------- breakdowns ---
# The client's order: reason is what we are measuring, so the cuts are country,
# publisher and discipline. A paper with two countries counts in both.
dims <- panel_dimensions()
DISCIPLINE_NAMES <- c(BLS = "Basic Life Sciences", HSC = "Health Sciences",
                      "B/T" = "Business and Technology", PHY = "Physical Sciences",
                      SOC = "Social Sciences", ENV = "Environmental Sciences",
                      HUM = "Humanities")
subj <- dims$subject
code <- ifelse(is.na(subj$subject_code) | subj$subject_code == "", "(no code)",
               subj$subject_code)
named <- unname(DISCIPLINE_NAMES[code])
subj$discipline <- ifelse(is.na(named), code, sprintf("%s (%s)", named, code))

pap$family <- publisher_family(pap$publisher)

cut_by <- function(values, label) {
  rows <- list()
  for (v in names(values)) {
    sel <- pap$original_doi %in% values[[v]]
    if (sum(sel) < MIN_PAPERS) next
    s <- pap[sel, ]
    row <- data.frame(value = v, papers = nrow(s), stringsAsFactors = FALSE)
    for (r in names(TASK_B_REASONS)) row[[r]] <- round(100 * mean(yes(s[[r]])), 1)
    row$clear_C3 <- round(100 * mean(s$clarity %in% "C3", na.rm = TRUE), 1)
    row$no_reason_C0 <- round(100 * mean(s$clarity %in% "C0", na.rm = TRUE), 1)
    rows[[length(rows) + 1L]] <- row
  }
  if (!length(rows)) return(NULL)
  out <- do.call(rbind, rows)
  out <- out[order(-out$papers), ]
  cat("\n==", label, "==", nrow(out), "values with at least", MIN_PAPERS, "papers\n")
  print(utils::head(out[, c("value", "papers", "R0_no_reason", "clear_C3")], 12),
        row.names = FALSE)
  w(out, paste0("by-", label))
  out
}

# The dimension tables are keyed by Retraction Watch record; a paper can have
# several records, and a value on any of them counts for the paper.
split_doi <- function(long, col) {
  long$original_doi <- rec$original_doi[match(long$record_id, rec$record_id)]
  long <- long[!is.na(long$original_doi) & long$original_doi %in% pap$original_doi, ]
  split(long$original_doi, long[[col]])
}
cut_by(split_doi(dims$reason, "reason"), "reason")
cut_by(split_doi(dims$country, "country"), "country")
cut_by(split(pap$original_doi[!is.na(pap$family) & nzchar(pap$family)],
             pap$family[!is.na(pap$family) & nzchar(pap$family)]), "publisher")
cut_by(split_doi(subj, "discipline"), "discipline")

cat("\nClarity is reported but not ranked on: see the header of this script.\n")
