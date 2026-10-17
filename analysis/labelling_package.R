# One workbook per labeller, holding everything still to be labelled.
#
#   Rscript analysis/labelling_package.R
#
# Round 2 (127 items) and the social media supplement (100 items) were built by
# two different scripts on two different days and were sitting as two workbooks
# per person. They go out as one.
#
# **Why one file and not two.** Two files is two chances to return one. Round 1
# already cost a week of chasing, and the seventh sheet arrived three days after
# the sixth.
#
# **Why this is not just a merge.** The social workbook was built before the
# protocol was rewritten. Its instructions still carry v1's rule - "几类都适用
# 时按优先顺序 A2 > A3 > A1 > A4", pick the description that fits best and break
# ties by precedence - and it tells the reader "规则与主表完全相同", the rules
# are the same as the main sheet. The main sheet is now round 2, which asks four
# questions in a fixed order. Sent as they stood, one person would have applied
# two different rule sets in one sitting, and the social labels would not have
# been comparable with anything. Both sheets now carry the same v2 instructions.
#
# The items themselves are unchanged: the same 127 and the same 100, drawn by
# analysis/labelling_round2.R and analysis/labelling_sample.R, so the keys
# already written still describe them. Only the wrapper is rebuilt.
#
# Writes data/labelling/to_send/<name>/ with the workbook and the protocol, a
# manifest, and the message to send with them.

source("R/labelling.R")

SEED <- 20260926
LABELLERS <- c("Labeller A", "Labeller B", "Labeller C", "Labeller D",
               "Labeller E", "Labeller F", "Labeller G")
LAB <- file.path("data", "labelling")
OUT <- file.path(LAB, "to_send")
PROTOCOL <- file.path(LAB, "CS43_labelling_protocol_v2.docx")
unlink(OUT, recursive = TRUE)
dir.create(OUT, recursive = TRUE, showWarnings = FALSE)

r2 <- utils::read.csv(file.path(LAB, "round2", "key_A_round2.csv"),
                      stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
so <- utils::read.csv(file.path(LAB, "key_A_social.csv"),
                      stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")
so <- so[so$set == "main", ]
stopifnot(nrow(r2) > 0, nrow(so) > 0, !anyDuplicated(r2$item_id), !anyDuplicated(so$item_id))
cat("round 2:", nrow(r2), "items   social supplement:", nrow(so), "items   total per person:",
    nrow(r2) + nrow(so), sprintf("(about %.1f h)\n", (nrow(r2) + nrow(so)) / 60))

A_CODES <- c("A1 Repeats/supports finding", "A2 Acknowledges retraction",
             "A3 Questions/corrects", "A4 Unrelated", "A0 Cannot tell")

# The v2 questions, once, for both sheets. This is the wording of
# docs/labelling-protocol.md section 3; the sheet is not the place to paraphrase
# it, because a paraphrase is how round 1's disagreement started and how the
# model's notice answers went wrong.
README <- c(
  "CS-43 标注（第二轮 + 社交媒体补充）/ Task A: round 2 and the social supplement",
  "",
  "这一份里有两个要填的表，规则完全一样，一起交回来就行。",
  "  A_media   第二轮，127 条",
  "  A_social  社交媒体补充，100 条 (Reddit, Facebook, Bluesky, Google+, Q&A)",
  "This file has two sheets to fill in. The rules are the same for both. Return",
  "the one file when both are done.",
  "",
  "规则改了。不是「挑一个最像的描述」，是按顺序回答四个问题，答是就停。",
  "上一轮的「优先顺序 A2 > A3 > A1 > A4」不再用了。",
  "The rules have changed. Do not pick the description that fits best. Work down",
  "these questions in order and stop at the first yes. The old precedence rule",
  "A2 > A3 > A1 > A4 no longer applies - the order of the questions replaces it.",
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
  "三条铁律不变：独立完成；只看表里的文字，不要点链接；",
  "evidence 列拄最多 15 个词，A4/A0 可以不填。",
  "Unchanged: work alone; judge only the text shown, do not open links; evidence",
  "up to 15 words, and it may be left empty for A4 and A0.",
  "",
  "A_media 里的条目是第一轮大家意见不一致的，外加一些做对照用的。",
  "哪些是哪些不告诉你，请当成全新的一批来标。",
  "A_media holds items round 1 split on, plus some controls. Which is which is",
  "not marked. Treat every row as new.",
  "",
  "A_social 是上一轮漏掉的来源：筛选条件要求标题非空，而 Bluesky 的帖子没有标题，",
  "整个渠道就被排掉了。Twitter 只有 ID 和日期没有正文，所以不在这里。",
  "A_social covers sources round 1 missed: the filter required a non-empty title",
  "and every Bluesky post has none, so the whole channel was excluded. Twitter",
  "carries no text under the licence, so it cannot be labelled at all.",
  "",
  "只填这几列：label, evidence, translated, note。不要改文件名，不要增删行列。",
  "Fill only: label, evidence, translated, note. Do not rename the file or add,",
  "delete or reorder rows or columns.")

sheet_style <- function(wb, name, d, widths, fill_cols) {
  openxlsx::addWorksheet(wb, name)
  openxlsx::writeData(wb, name, d, headerStyle = openxlsx::createStyle(
    textDecoration = "bold", fgFill = "#DDEBF7", border = "bottom", wrapText = TRUE))
  openxlsx::freezePane(wb, name, firstRow = TRUE, firstCol = TRUE)
  openxlsx::setColWidths(wb, name, cols = seq_along(d), widths = widths)
  wrap <- openxlsx::createStyle(wrapText = TRUE, valign = "top")
  openxlsx::addStyle(wb, name, wrap, rows = 2:(nrow(d) + 1), cols = seq_along(d),
                     gridExpand = TRUE)
  fill <- openxlsx::createStyle(fgFill = "#FFF2CC", wrapText = TRUE, valign = "top")
  openxlsx::addStyle(wb, name, fill, rows = 2:(nrow(d) + 1),
                     cols = match(fill_cols, names(d)), gridExpand = TRUE)
}

build <- function(file, order_seed) {
  # Each labeller gets the rows in a different order, so that a shared fatigue
  # effect near the end of a long sheet does not land on the same items for
  # everyone and look like agreement.
  set.seed(order_seed)
  m <- r2[sample(nrow(r2)), ]
  s <- so[sample(nrow(so)), ]
  wb <- openxlsx::createWorkbook()
  openxlsx::addWorksheet(wb, "Read me")
  openxlsx::writeData(wb, "Read me", README)
  openxlsx::setColWidths(wb, "Read me", 1, 100)

  sa <- data.frame(item_id = m$item_id, paper_title = m$paper_title,
                   item_title = m$title, item_summary = m$summary,
                   label = "", evidence = "", translated = "", note = "",
                   stringsAsFactors = FALSE)
  sheet_style(wb, "A_media", sa, c(9, 40, 45, 70, 28, 35, 11, 25),
              c("label", "evidence", "translated", "note"))

  ss <- data.frame(item_id = s$item_id, paper_title = s$paper_title, source = s$kind,
                   item_title = s$title, item_summary = s$summary,
                   label = "", evidence = "", translated = "", note = "",
                   stringsAsFactors = FALSE)
  sheet_style(wb, "A_social", ss, c(9, 40, 11, 45, 70, 28, 35, 11, 25),
              c("label", "evidence", "translated", "note"))

  openxlsx::addWorksheet(wb, "Codes", visible = FALSE)
  openxlsx::writeData(wb, "Codes", data.frame(A = A_CODES))
  openxlsx::writeData(wb, "Codes", data.frame(C = "Y"), startCol = 2)
  ref <- sprintf("'Codes'!$A$2:$A$%d", length(A_CODES) + 1)
  openxlsx::dataValidation(wb, "A_media", col = 5, rows = 2:(nrow(sa) + 1),
                           type = "list", value = ref)
  openxlsx::dataValidation(wb, "A_media", col = 7, rows = 2:(nrow(sa) + 1),
                           type = "list", value = "'Codes'!$B$2:$B$2")
  openxlsx::dataValidation(wb, "A_social", col = 6, rows = 2:(nrow(ss) + 1),
                           type = "list", value = ref)
  openxlsx::dataValidation(wb, "A_social", col = 8, rows = 2:(nrow(ss) + 1),
                           type = "list", value = "'Codes'!$B$2:$B$2")
  openxlsx::saveWorkbook(wb, file, overwrite = TRUE)
}

slug <- function(x) gsub("[^A-Za-z]+", "_", x)
man <- list()
for (i in seq_along(LABELLERS)) {
  who <- LABELLERS[i]
  d <- file.path(OUT, slug(who))
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  f <- file.path(d, sprintf("CS43_round2_%s.xlsx", slug(who)))
  build(f, SEED + i)
  if (file.exists(PROTOCOL)) file.copy(PROTOCOL, d, overwrite = TRUE)
  man[[i]] <- data.frame(labeller = who, folder = basename(d),
                         workbook = basename(f), a_media = nrow(r2),
                         a_social = nrow(so), items = nrow(r2) + nrow(so),
                         hours = round((nrow(r2) + nrow(so)) / 60, 1),
                         stringsAsFactors = FALSE)
}
manifest <- do.call(rbind, man)

# One zip per person, so the whole thing is a single attachment: the workbook
# and the protocol it must be read with cannot then arrive separately, or half.
old <- setwd(OUT)
for (i in seq_along(LABELLERS)) {
  d <- slug(LABELLERS[i])
  z <- paste0("CS43_", d, ".zip")
  if (file.exists(z)) unlink(z)
  utils::zip(z, d, flags = "-r9Xq")
  manifest$zip[i] <- z
}
setwd(old)
manifest$zip_kb <- round(file.size(file.path(OUT, manifest$zip)) / 1024)
utils::write.csv(manifest, file.path(OUT, "manifest.csv"), row.names = FALSE,
                 fileEncoding = "UTF-8")
print(manifest, row.names = FALSE)

# The message to send with the file. Kept here so it says exactly what the
# sheet says, and changes when the sheet does.
msg <- c(
  "标注第二轮，每人一个文件，里面两个表一起填：",
  sprintf("  A_media   %d 条（第二轮）", nrow(r2)),
  sprintf("  A_social  %d 条（社交媒体补充）", nrow(so)),
  sprintf("合计 %d 条，大概 %.1f 小时。", nrow(r2) + nrow(so),
          (nrow(r2) + nrow(so)) / 60),
  "",
  "重要：规则改了。不再是「挑最像的描述」，而是按顺序回答 Q0-Q4，",
  "答是就停。上一轮的优先顺序规则作废。具体看文件里的 Read me，",
  "或者附件里的协议。",
  "",
  "第一轮七个人的一致性是 0.594，低于可以下结论的门槛。",
  "查了之后发现不是大家标得不认真（同一个人碰到同一段文字两次，",
  "42 次里有 38 次自己跟自己一致），而是旧规则允许好几种读法。",
  "所以重写的是规则，不是要求大家重来一遍。",
  "",
  "Round 2 of the labelling. One file each; both sheets in it go together.",
  sprintf("%d + %d = %d items, about %.1f hours.", nrow(r2), nrow(so),
          nrow(r2) + nrow(so), (nrow(r2) + nrow(so)) / 60),
  "The rules have changed: four questions in a fixed order, stop at the first",
  "yes. The old precedence rule is gone. Round 1 reached alpha 0.594, below the",
  "threshold any conclusion rests on, and the cause was the wording, not the",
  "labelling - where one person met the same text twice they agreed with",
  "themselves 38 times out of 42. It is the protocol that was rewritten.")
writeLines(msg, file.path(OUT, "send-message.txt"), useBytes = TRUE)
cat("\nwritten:", OUT, "\n")
if (!file.exists(PROTOCOL)) {
  cat("NOTE: ", PROTOCOL, " does not exist, so no protocol was copied into the\n",
      "folders. Build it from docs/labelling-protocol.md first.\n", sep = "")
}
