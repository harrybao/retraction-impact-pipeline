# How much did the labellers actually agree, and on what?
#
#   Rscript analysis/labelling_agreement.R [directory of returned sheets]
#
# The labelled set is not the analysis. It is the ruler: the client gave no
# budget for a commercial model, so Stage 3 classifies tens of thousands of
# items with a local one, and this is the only thing that can say whether that
# model is right. A ruler has to be checked before it is used, and the check is
# whether people applying the same protocol to the same text reach the same
# answer.
#
# Conventional reading of Krippendorff's alpha (Krippendorff 2004): 0.80 and
# above is reliable, 0.667 to 0.80 supports tentative conclusions, below 0.667
# is not a basis for conclusions.
#
# Writes the agreement figures - numbers only, no licensed text - to
# data/reports/. The per-item majority and the ties stay in data/labelling/,
# which is not version-controlled because it carries Altmetric text.

source("R/labelling.R")

args <- commandArgs(trailingOnly = TRUE)
dir <- if (length(args)) args[1] else file.path("data", "labelling", "returned", "round1")
if (!dir.exists(dir)) {
  stop("no such directory: ", dir,
       "\nPass the folder holding the returned workbooks.", call. = FALSE)
}
OUT <- file.path("data", "reports")
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)

got <- read_returned_labels(dir)
cat("sheets returned:", length(got$files), "\n")
cat("labellers:", paste(sort(unique(got$A$labeller)), collapse = ", "), "\n\n")

ra <- score_task_a(got$A)
rb <- score_task_b(got$B)
m <- ra$matrix

rows <- list()
add <- function(scope, measure, value, note = "") {
  rows[[length(rows) + 1L]] <<- data.frame(
    scope = scope, measure = measure,
    value = if (is.na(value)) NA_real_ else round(value, 4),
    note = note, stringsAsFactors = FALSE)
}
band <- function(a) {
  if (is.na(a)) "" else if (a >= 0.80) "reliable"
  else if (a >= 0.667) "tentative conclusions only" else "not a basis for conclusions"
}

add("task A, as labelled (5 categories)", "Krippendorff alpha",
    krippendorff_alpha(m, "nominal", LABEL_A),
    band(krippendorff_alpha(m, "nominal", LABEL_A)))
add("task A, as labelled (5 categories)", "Fleiss kappa", fleiss_kappa(m, LABEL_A))
add("task A, as labelled (5 categories)", "pairwise agreement", pairwise_agreement(m))
add("task A, as labelled (5 categories)", "items needing the coordinator",
    sum(ra$vote$tied), "no majority")

# The five-way scheme is finer than the question the client asked. He wants to
# know whether the reader is told the paper was retracted; A1, A3 and A4 all
# say "no" to that in different words, and the boundaries between them are
# where the labellers part company.
collapse <- function(label, mm, levels, note = "") {
  a <- krippendorff_alpha(mm, "nominal", levels)
  add(label, "Krippendorff alpha", a, paste(band(a), note))
  add(label, "pairwise agreement", pairwise_agreement(mm))
}
bin <- ifelse(is.na(m), NA, ifelse(m == "A2", "mentions", "does not"))
collapse("task A, does the coverage mention the retraction?", bin,
         c("mentions", "does not"), "- the client's question")
merged <- m; merged[merged %in% c("A1", "A4")] <- "A1/A4"
collapse("task A, A1 and A4 merged", merged, c("A1/A4", "A2", "A3", "A0"))

for (r in c("clarity", LABEL_R)) {
  s <- rb$stats[grepl(r, rb$stats$task, fixed = TRUE) &
                grepl("alpha", rb$stats$measure), ]
  if (nrow(s)) add(paste("task B,", r), "Krippendorff alpha", s$value[1],
                   band(s$value[1]))
}

# Is any one labeller carrying the disagreement? Leaving each out in turn says
# whether this is one person reading the protocol differently or a protocol
# that reads several ways.
for (who in colnames(m)) {
  add("task A, leave one out (5 categories)", who,
      krippendorff_alpha(m[, colnames(m) != who, drop = FALSE], "nominal", LABEL_A))
}
for (who in colnames(bin)) {
  add("task A, leave one out (the client's question)", who,
      krippendorff_alpha(bin[, colnames(bin) != who, drop = FALSE], "nominal",
                         c("mentions", "does not")))
}

# How each labeller spent the categories. A category one person never uses and
# another uses forty times is a protocol problem, not noise.
use <- t(apply(m, 2, function(v) table(factor(v, levels = LABEL_A))))
for (who in rownames(use)) {
  for (lab in colnames(use)) add("task A, label use", paste(who, lab), use[who, lab])
}

# Which boundaries fail. Counted over items where the labellers split.
pairs <- new.env(parent = emptyenv())
for (i in seq_len(nrow(m))) {
  u <- sort(unique(m[i, ][!is.na(m[i, ])]))
  if (length(u) < 2) next
  for (x in seq_along(u)) for (y in seq_along(u)) if (x < y) {
    k <- paste(u[x], u[y], sep = " vs ")
    assign(k, (if (exists(k, pairs)) get(k, pairs) else 0) + 1, pairs)
  }
}
pv <- sort(unlist(as.list(pairs)), decreasing = TRUE)
for (k in names(pv)) add("task A, disagreeing pairs", k, pv[[k]], "items")

summary <- do.call(rbind, rows)
print(summary[summary$scope != "task A, label use", c("scope", "measure", "value", "note")],
      row.names = FALSE, right = FALSE)
f <- file.path(OUT, "labelling-agreement.csv")
utils::write.csv(summary, f, row.names = FALSE, fileEncoding = "UTF-8")
cat("\nwritten:", f, "\n")

# The per-item results carry the labelled text's item ids, so they stay with
# the sheets rather than in the tracked reports folder.
score_labelling(dir, out = file.path("data", "labelling", "scored"),
                tag = format(Sys.Date(), "returned-%Y-%m-%d"))
