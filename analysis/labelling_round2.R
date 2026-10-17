# Round 2 of Task A, under the v2 decision questions.
#
#   Rscript analysis/labelling_round2.R [directory of returned round-1 sheets]
#
# Round 1 reached Krippendorff's alpha 0.58 on five categories - not a basis
# for conclusions - and the reason was the protocol, not the labellers: where
# one person met the same text twice they agreed with themselves 38 times out
# of 42, and one labeller chose A3 once where another chose it 45 times. The
# categories are unchanged; how you arrive at them is now four questions asked
# in a fixed order (docs/labelling-protocol.md section 3, v2).
#
# ## What goes in the sheet, and why it is not all 200
#
# The items two or more labellers disagreed on - that is where the old wording
# failed - plus blind samples of the ones they nearly or entirely agreed on.
#
# The samples are the point. Leaving the agreed items out assumes v2 would not
# move them, and that assumption is exactly the kind this project keeps finding
# in its own past work. So it is measured: if the checks come back where round
# 1 left them, the two rounds merge and only the contested items needed doing.
# If they move, v2 changed more than the boundaries and all 200 need redoing.
#
# Two levels of check, because they carry different risk. A unanimous item is
# unlikely to move. An item with a single dissenter can: A001 is a press release
# about a language-learning app that states no finding at all, and six of seven
# called it A1 - under v2's Q3 it is plainly A4.

source("R/labelling.R")

SEED <- 20260925
# Re-label what was genuinely contested, and sample-check what was not.
#
# With seven labellers, 71 items were unanimous, 42 had one dissenter, and 87
# had two or more. Asking everyone to redo all 200 costs about 3.3 hours each
# on top of the 10 they have already spent; asking them to redo the 87 costs
# 1.5. The difference is only worth taking if the items left out would not have
# moved - so a sample of them is mixed in unmarked, at both levels of
# agreement, and that assumption is measured instead of assumed.
CONTESTED_BELOW <- 6L  # fewer than 6 of 7 agreeing counts as contested
N_CHECK_UNANIMOUS <- 20
N_CHECK_NEAR <- 20     # one dissenter - these can still move under v2
LABELLERS <- c("Labeller A", "Labeller B", "Labeller C", "Labeller D",
               "Labeller E", "Labeller F", "Labeller G")

args <- commandArgs(trailingOnly = TRUE)
round1 <- if (length(args)) args[1] else file.path("data", "labelling", "returned")
out <- file.path("data", "labelling", "round2")
dir.create(out, recursive = TRUE, showWarnings = FALSE)

got <- read_returned_labels(round1)
ra <- score_task_a(got$A)
v <- ra$vote
n_lab <- max(v$n_labels, na.rm = TRUE)
v$group <- ifelse(v$tied | v$votes < CONTESTED_BELOW, "contested",
                  ifelse(v$votes == n_lab, "unanimous", "near"))
cat("round 1:", nrow(v), "items from", n_lab, "labellers -",
    sum(v$group == "contested"), "contested,",
    sum(v$group == "near"), "with one dissenter,",
    sum(v$group == "unanimous"), "unanimous\n")

key <- utils::read.csv(file.path("data", "labelling", "key_A.csv"),
                       stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
key <- key[key$set == "main", ]
stopifnot(all(v$item_id %in% key$item_id))

set.seed(SEED)
pick <- function(g, n) {
  ids <- v$item_id[v$group == g]
  sample(ids, min(n, length(ids)))
}
check_u <- pick("unanimous", N_CHECK_UNANIMOUS)
check_n <- pick("near", N_CHECK_NEAR)
contested <- v$item_id[v$group == "contested"]
take <- c(contested, check_u, check_n)
items <- key[match(take, key$item_id), ]
items$why <- ifelse(items$item_id %in% check_u, "unanimous in round 1 (blind check)",
             ifelse(items$item_id %in% check_n, "one dissenter in round 1 (blind check)",
                    "contested in round 1"))
cat("round 2 sheet:", nrow(items), "items -", length(contested), "contested +",
    length(check_u), "unanimous checks +", length(check_n), "near-unanimous checks",
    sprintf("(about %.1f h per labeller)\n", nrow(items) / 60))

# The coordinator's key. Round 1's answer is recorded here and nowhere the
# labellers can see it.
k <- merge(items[c("item_id", "why", "doi", "paper_title", "kind",
                   "relative_to_retraction", "title", "summary")],
           v[c("item_id", "majority", "votes", "agreement", "tied", "all_labels")],
           by = "item_id")
names(k)[names(k) == "majority"] <- "round1_majority"
utils::write.csv(k, file.path(out, "key_A_round2.csv"), row.names = FALSE,
                 fileEncoding = "UTF-8")
cat("written:", file.path(out, "key_A_round2.csv"), "\n")

# --- workbooks -------------------------------------------------------------
A_CODES <- c("A1 Repeats/supports finding", "A2 Acknowledges retraction",
             "A3 Questions/corrects", "A4 Unrelated", "A0 Cannot tell")

README <- c(
  "CS-43 标注 第二轮 / Task A round 2",
  "",
  "规则改了。这次不是「挑一个最像的描述」，是按顺序回答四个问题，答是就停。",
  "The rules have changed. Do not pick the description that fits best. Work down",
  "these questions in order and stop at the first yes.",
  "",
  "Q0 能不能读？空的、不到 10 个词、翻译工具也读不了  -> A0",
  "   「我拿不准」不算 A0。能读就必须往下答。",
  "   Q0 Can you read it? empty, under 10 words, or a script you cannot read -> A0",
  "   \"I am unsure\" is NOT A0. If you can read it, answer Q1-Q4.",
  "",
  "Q1 文中说撤稿「已经发生」了吗？ -> A2",
  "   要求撤稿、建议撤稿、正在调查、发了勘误 —— 都不算，往下走。",
  "   Q1 Does the text say the retraction HAS HAPPENED or been decided? -> A2",
  "   A demand, a recommendation, an investigation or an erratum is NOT this.",
  "",
  "Q2 文中质疑的是「这篇论文」吗？ -> A3",
  "   只讲作者、只讲机构、只讲这个领域而没说到这篇论文 —— 不算，往下走。",
  "   Q2 Does the text raise doubt about THIS paper? -> A3",
  "   About the authors or the institution only, not this paper: not this.",
  "",
  "Q3 文中说出了这篇论文「发现了什么」吗？ -> A1",
  "   只列作者和标题、只是同一个话题 —— 不算。",
  "   Q3 Does the text say what THIS paper found or claimed? -> A1",
  "   A citation string or an item merely on the same subject does not.",
  "",
  "Q4 以上都不是 -> A4",
  "",
  "三条铁律不变：独立完成；只看表里的文字；evidence 列抄最多 15 个词。",
  "Unchanged: work alone; judge only the text shown; evidence up to 15 words.",
  "",
  "这些条目是第一轮大家意见不一致的，外加一些做对照用的。哪些是哪些不告诉你，",
  "请当成全新的一批来标。",
  "These are items round 1 split on, plus some controls. Which is which is not",
  "marked. Treat every row as new.")

build <- function(items, file, order_seed) {
  set.seed(order_seed)
  d <- items[sample(nrow(items)), ]
  wb <- openxlsx::createWorkbook()
  openxlsx::addWorksheet(wb, "Read me")
  openxlsx::writeData(wb, "Read me", README)
  openxlsx::setColWidths(wb, "Read me", 1, 100)

  sa <- data.frame(item_id = d$item_id, paper_title = d$paper_title,
                   item_title = d$title, item_summary = d$summary,
                   label = "", evidence = "", translated = "", note = "",
                   stringsAsFactors = FALSE)
  openxlsx::addWorksheet(wb, "A_media")
  openxlsx::writeData(wb, "A_media", sa, headerStyle = openxlsx::createStyle(
    textDecoration = "bold", fgFill = "#DDEBF7", border = "bottom", wrapText = TRUE))
  openxlsx::freezePane(wb, "A_media", firstRow = TRUE, firstCol = TRUE)
  openxlsx::setColWidths(wb, "A_media", cols = 1:8, widths = c(9, 40, 45, 70, 28, 35, 11, 25))
  wrap <- openxlsx::createStyle(wrapText = TRUE, valign = "top")
  openxlsx::addStyle(wb, "A_media", wrap, rows = 2:(nrow(sa) + 1), cols = 1:8,
                     gridExpand = TRUE)
  fill <- openxlsx::createStyle(fgFill = "#FFF2CC", wrapText = TRUE, valign = "top")
  openxlsx::addStyle(wb, "A_media", fill, rows = 2:(nrow(sa) + 1), cols = 5:8,
                     gridExpand = TRUE)

  openxlsx::addWorksheet(wb, "Codes", visible = FALSE)
  openxlsx::writeData(wb, "Codes", data.frame(A = A_CODES))
  openxlsx::writeData(wb, "Codes", data.frame(C = "Y"), startCol = 2)
  rows <- 2:(nrow(sa) + 1)
  openxlsx::dataValidation(wb, "A_media", col = 5, rows = rows, type = "list",
                           value = sprintf("'Codes'!$A$2:$A$%d", length(A_CODES) + 1))
  openxlsx::dataValidation(wb, "A_media", col = 7, rows = rows, type = "list",
                           value = "'Codes'!$B$2:$B$2")
  openxlsx::saveWorkbook(wb, file, overwrite = TRUE)
}

slug <- function(x) gsub("[^A-Za-z]+", "_", x)
for (i in seq_along(LABELLERS)) {
  build(items, file.path(out, sprintf("CS43_round2_%s.xlsx", slug(LABELLERS[i]))),
        SEED + i)
}
cat("written:", length(list.files(out, pattern = "xlsx")), "workbooks in", out, "\n")

utils::write.csv(
  data.frame(metric = c("round 1 items", "contested in round 1",
                        "one dissenter in round 1", "unanimous in round 1",
                        "blind checks drawn", "round 2 sheet size", "labellers"),
             value = c(nrow(v), length(contested), sum(v$group == "near"),
                       sum(v$group == "unanimous"),
                       length(check_u) + length(check_n),
                       nrow(items), length(LABELLERS))),
  file.path("data", "reports", "labelling-round2-scope.csv"), row.names = FALSE)
