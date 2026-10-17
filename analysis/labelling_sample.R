# Draw the labelling samples and build one workbook per labeller.
#
#   Rscript analysis/labelling_sample.R
#
# Protocol: docs/labelling-protocol.md. Everything is random with a fixed seed,
# so the same data gives the same samples and the same sheets on any machine.
#
# Output, in data/labelling/ (not version-controlled: it holds Altmetric text
# under the client's licence and is shared with the team only):
#   pilot/CS43_pilot_<name>.xlsx      20 items (10 A, 10 B), same for everyone
#   main/CS43_labelling_<name>.xlsx   400 items (200 A, 200 B), each in its own order
#   key_A.csv, key_B.csv              the coordinator's key: paper, period, dates
#
# Task A: one media item per paper, stratified by source (news, blog) x period
#   (before, after retraction), 50 papers per cell. Same-day items are left out.
# Task B: one retraction notice per paper, from papers not used in Task A; a
#   notice shared by several papers is drawn at most once.

SEED <- 20260919
LABELLERS <- c("Labeller A", "Labeller B", "Labeller C", "Labeller D",
               "Labeller E", "Labeller F", "Labeller G")
N_A_PER_CELL <- 50
PILOT_A <- c(news_before = 3, news_after = 3, blogs_before = 2, blogs_after = 2)
N_B <- 200
PILOT_B <- 10

clean <- file.path("data", "clean")
out <- file.path("data", "labelling")
dir.create(file.path(out, "pilot"), recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(out, "main"), recursive = TRUE, showWarnings = FALSE)
blank <- function(x) is.na(x) | !nzchar(trimws(x))

# --- inputs ---------------------------------------------------------------
rec <- utils::read.csv(file.path(clean, "records_clean.csv"), colClasses = "character",
                       encoding = "UTF-8")
paper_title <- tapply(rec$title, rec$original_doi, `[`, 1)

all_posts <- utils::read.csv(gzfile(file.path(clean, "altmetric_posts.csv.gz")),
                             colClasses = "character", encoding = "UTF-8")
stopifnot(all(c("before", "after") %in% all_posts$relative_to_retraction))
all_posts$summary[is.na(all_posts$summary)] <- ""

# The news and blog pool, EXACTLY as it was when the sheets went out.
#
# The filter below is not the filter the supplement uses, and that is
# deliberate. These sheets are with seven labellers already, and key_A.csv is
# the coordinator's copy of what they are looking at. Widening this pool would
# change the candidate vector, `sample()` would draw differently, and the key
# would stop describing the sheets people hold. So this stays frozen and the
# missing sources are drawn separately, below.
posts <- all_posts[all_posts$kind %in% c("news", "blogs") &
                   all_posts$relative_to_retraction %in% c("before", "after"), ]
posts <- posts[!blank(posts$title) & nchar(paste(posts$title, posts$summary)) >= 30, ]
posts$cell <- paste(posts$kind, posts$relative_to_retraction, sep = "_")

notices <- utils::read.csv(gzfile(file.path(clean, "stage2_notices.csv.gz")),
                           colClasses = "character", encoding = "UTF-8")
notices <- notices[!blank(notices$notice) & nchar(notices$notice) >= 50, ]

# The notice of each paper: its earliest Retraction record.
ret <- rec[rec$retraction_nature == "Retraction" & !blank(rec$retraction_doi) &
           !blank(rec$original_doi), c("original_doi", "retraction_doi", "retraction_date")]
ret <- ret[order(ret$original_doi, ret$retraction_date), ]
ret <- ret[!duplicated(ret$original_doi), ]
ret <- ret[ret$retraction_doi %in% notices$doi, ]

# --- Task A ---------------------------------------------------------------
set.seed(SEED)
used <- character()
pick_a <- function(n_by_cell, set, pool = posts) {
  rows <- list()
  for (cell in names(n_by_cell)) {
    cand <- setdiff(unique(pool$doi[pool$cell == cell]), used)
    dois <- sample(cand, n_by_cell[[cell]])
    used <<- c(used, dois)
    for (d in dois) {
      items <- pool[pool$doi == d & pool$cell == cell, ]
      rows[[length(rows) + 1]] <- cbind(items[sample(nrow(items), 1), ], set = set)
    }
  }
  do.call(rbind, rows)
}
pilot_a <- pick_a(PILOT_A, "pilot")
cells <- c("news_before", "news_after", "blogs_before", "blogs_after")
main_a <- pick_a(setNames(rep(N_A_PER_CELL, 4), cells), "main")
pilot_a$item_id <- sprintf("AP%02d", seq_len(nrow(pilot_a)))
main_a$item_id <- sprintf("A%03d", seq_len(nrow(main_a)))
key_a <- rbind(pilot_a, main_a)
key_a$paper_title <- unname(paper_title[key_a$doi])

# --- Task B ---------------------------------------------------------------
cand_b <- ret[!ret$original_doi %in% used, ]
cand_b <- cand_b[!duplicated(cand_b$retraction_doi), ]    # a shared notice once
idx <- sample(nrow(cand_b), PILOT_B + N_B)
key_b <- cand_b[idx, ]
key_b$set <- rep(c("pilot", "main"), c(PILOT_B, N_B))
key_b$item_id <- c(sprintf("BP%02d", seq_len(PILOT_B)), sprintf("B%03d", seq_len(N_B)))
key_b$paper_title <- unname(paper_title[key_b$original_doi])
m <- match(key_b$retraction_doi, notices$doi)
key_b$notice <- notices$notice[m]
key_b$notice_chars <- nchar(key_b$notice)
key_b$notice_method <- notices$notice_method[m]
stopifnot(!anyNA(key_b$notice), !any(duplicated(key_b$retraction_doi)))

utils::write.csv(key_a[c("item_id", "set", "cell", "doi", "paper_title", "kind",
                         "relative_to_retraction", "posted_on", "retracted_on",
                         "days_from_retraction", "author", "url", "title", "summary")],
                 file.path(out, "key_A.csv"), row.names = FALSE, fileEncoding = "UTF-8")
utils::write.csv(key_b[c("item_id", "set", "original_doi", "retraction_doi",
                         "retraction_date", "paper_title", "notice_chars",
                         "notice_method", "notice")],
                 file.path(out, "key_B.csv"), row.names = FALSE, fileEncoding = "UTF-8")

# --- Task A supplement: the social media Task A never had ------------------
#
# The client asked for social media and Task A covers news and blogs only. Two
# things kept it out, and neither was a decision anyone took:
#
#   * the kinds were hardcoded to news and blogs;
#   * the filter required a non-empty `title`, and Bluesky puts its text in
#     `summary` - all 6,286 Bluesky posts have an empty title, so the whole
#     source was excluded by a condition nobody aimed at it.
#
# Twitter is genuinely out of reach: 1,281,535 posts covering 14,028 papers,
# and Altmetric's licence returns an id and a date with no text at all. Those
# can carry timing analysis and can never carry content labelling. Weibo is
# the same. That is a licence boundary, not a parsing failure.
#
# This is a SUPPLEMENT, in its own files, drawn with its own seed. The sheets
# already with the labellers are not touched and not redrawn.
SOCIAL_KINDS <- c("reddit", "facebook", "bluesky", "googleplus", "q&a")
N_SOCIAL_PER_CELL <- 50
PILOT_SOCIAL <- c(social_before = 2, social_after = 2)

social <- all_posts[all_posts$kind %in% SOCIAL_KINDS &
                    all_posts$relative_to_retraction %in% c("before", "after"), ]
# Text may be in either field. This is the condition the original filter should
# have used, applied here where changing it breaks nothing.
social <- social[nchar(trimws(paste(social$title, social$summary))) >= 30, ]
social$cell <- paste("social", social$relative_to_retraction, sep = "_")

cat("Task A supplement candidates:\n")
print(table(social$kind, social$relative_to_retraction))
cat("papers available:", length(unique(social$doi)), "\n")

set.seed(SEED + 1000)
used <- unique(key_a$doi)            # never a paper already in the sent sheets
pilot_s <- pick_a(PILOT_SOCIAL, "pilot", pool = social)
main_s <- pick_a(stats::setNames(rep(N_SOCIAL_PER_CELL, 2),
                                 c("social_before", "social_after")), "main",
                 pool = social)
pilot_s$item_id <- sprintf("SP%02d", seq_len(nrow(pilot_s)))
main_s$item_id <- sprintf("S%03d", seq_len(nrow(main_s)))
key_s <- rbind(pilot_s, main_s)
key_s$paper_title <- unname(paper_title[key_s$doi])
stopifnot(!any(key_s$doi %in% key_a$doi), !any(duplicated(key_s$doi)))

utils::write.csv(key_s[c("item_id", "set", "cell", "doi", "paper_title", "kind",
                         "relative_to_retraction", "posted_on", "retracted_on",
                         "days_from_retraction", "author", "url", "title", "summary")],
                 file.path(out, "key_A_social.csv"), row.names = FALSE,
                 fileEncoding = "UTF-8")
cat("written:", file.path(out, "key_A_social.csv"), "-", nrow(key_s), "items\n")

# --- workbooks ------------------------------------------------------------
A_CODES <- c("A1 Repeats/supports finding", "A2 Acknowledges retraction",
             "A3 Questions/corrects", "A4 Unrelated", "A0 Cannot tell")
C_CODES <- c("C3 Clear", "C2 Partial", "C1 Vague", "C0 No reason")
R_COLS <- c("R1_fabrication", "R2_image", "R3_plagiarism", "R4_duplicate",
            "R5_peer_review", "R6_paper_mill", "R7_honest_error", "R8_authorship_ethics",
            "R9_other", "R0_no_reason")

README <- c(
  "CS-43 labelling / 标注表",
  "",
  "三条铁律 / Three rules:",
  "1. 独立完成，不和任何人讨论。Label on your own; do not discuss items with anyone.",
  "2. 只根据表格里显示的文字判断，不要打开链接或上网查。Judge only the text shown.",
  "3. 几类都适用时按优先顺序 A2 > A3 > A1 > A4。Precedence: A2 > A3 > A1 > A4.",
  "",
  "A_media: 每行选一个 label，并在 evidence 里抄最多 15 个关键词（A4/A0 可不填）。",
  "   A1 重复或支持原结论 / repeats or supports the finding",
  "   A2 提到撤稿、撤回或关注声明 / acknowledges retraction, withdrawal or expression of concern",
  "   A3 质疑或纠正（未提正式撤稿）/ questions or corrects, no formal retraction mentioned",
  "   A4 无关 / unrelated",
  "   A0 无法判断 / cannot tell (empty, too short, unreadable)",
  "   用了翻译工具就在 translated 填 Y / put Y in translated if you used a translator",
  "",
  "B_notice: 在声明写到的每个原因列选 Y（可多选；没写原因只选 R0），再选 clarity，并填 evidence（R0/C0 可不填）。",
  "   R1 捏造/篡改 fabrication/falsification   R2 图片问题 image problems   R3 抄袭 plagiarism",
  "   R4 重复发表 duplicate publication   R5 同行评审问题 peer review   R6 论文工厂/机器生成 paper mill/generated",
  "   R7 无心之过 honest error   R8 作者/伦理/知情同意 authorship/ethics/consent   R9 其他 other   R0 没写原因 no reason",
  "   C3 清楚 clear (具体问题)   C2 部分 partial (问题类型，无细节)   C1 含糊 vague (套话)   C0 无原因 no reason",
  "",
  "只填这些列：label, evidence, translated, note, R1-R0, clarity。不要改文件名、不要增删行列。",
  "Fill only those columns. Do not rename the file or add, delete or reorder rows or columns.",
  "完整规则见 CS43_labelling_protocol.docx / Full rules: CS43_labelling_protocol.docx")

write_sheet <- function(wb, name, df, text_cols, fill_cols, widths) {
  openxlsx::addWorksheet(wb, name)
  openxlsx::writeData(wb, name, df, headerStyle = openxlsx::createStyle(
    textDecoration = "bold", fgFill = "#DDEBF7", border = "bottom", wrapText = TRUE))
  openxlsx::freezePane(wb, name, firstRow = TRUE, firstCol = TRUE)
  openxlsx::setColWidths(wb, name, cols = seq_along(df), widths = widths)
  wrap <- openxlsx::createStyle(wrapText = TRUE, valign = "top")
  openxlsx::addStyle(wb, name, wrap, rows = 2:(nrow(df) + 1), cols = seq_along(df),
                     gridExpand = TRUE)
  fill <- openxlsx::createStyle(fgFill = "#FFF2CC", wrapText = TRUE, valign = "top")
  openxlsx::addStyle(wb, name, fill, rows = 2:(nrow(df) + 1),
                     cols = match(fill_cols, names(df)), gridExpand = TRUE)
}

list_ref <- function(col, n) sprintf("'Codes'!$%s$2:$%s$%d", col, col, n + 1)

build <- function(a, b, file, order_seed) {
  set.seed(order_seed)
  a <- a[sample(nrow(a)), ]
  b <- b[sample(nrow(b)), ]
  wb <- openxlsx::createWorkbook()
  openxlsx::addWorksheet(wb, "Read me")
  openxlsx::writeData(wb, "Read me", README)
  openxlsx::setColWidths(wb, "Read me", 1, 140)

  sa <- data.frame(item_id = a$item_id, paper_title = a$paper_title,
                   item_title = a$title, item_summary = a$summary,
                   label = "", evidence = "", translated = "", note = "")
  write_sheet(wb, "A_media", sa, c("paper_title", "item_title", "item_summary"),
              c("label", "evidence", "translated", "note"), c(9, 40, 45, 70, 28, 35, 11, 25))

  sb <- data.frame(item_id = b$item_id, paper_title = b$paper_title, notice_text = b$notice)
  for (r in R_COLS) sb[[r]] <- ""
  sb$clarity <- ""; sb$evidence <- ""; sb$note <- ""
  write_sheet(wb, "B_notice", sb, c("paper_title", "notice_text"),
              c(R_COLS, "clarity", "evidence", "note"),
              c(9, 35, 90, rep(8, length(R_COLS)), 14, 35, 25))

  openxlsx::addWorksheet(wb, "Codes", visible = FALSE)
  openxlsx::writeData(wb, "Codes", data.frame(A = A_CODES), startCol = 1)
  openxlsx::writeData(wb, "Codes", data.frame(C = C_CODES), startCol = 2)
  openxlsx::writeData(wb, "Codes", data.frame(Y = "Y"), startCol = 3)

  rows_a <- 2:(nrow(sa) + 1); rows_b <- 2:(nrow(sb) + 1)
  openxlsx::dataValidation(wb, "A_media", col = match("label", names(sa)), rows = rows_a,
                           type = "list", value = list_ref("A", length(A_CODES)))
  openxlsx::dataValidation(wb, "A_media", col = match("translated", names(sa)), rows = rows_a,
                           type = "list", value = list_ref("C", 1))
  for (r in R_COLS) {
    openxlsx::dataValidation(wb, "B_notice", col = match(r, names(sb)), rows = rows_b,
                             type = "list", value = list_ref("C", 1))
  }
  openxlsx::dataValidation(wb, "B_notice", col = match("clarity", names(sb)), rows = rows_b,
                           type = "list", value = list_ref("B", length(C_CODES)))
  openxlsx::saveWorkbook(wb, file, overwrite = TRUE)
}

slug <- function(x) gsub("[^A-Za-z]+", "_", x)
for (i in seq_along(LABELLERS)) {
  who <- LABELLERS[i]
  # The pilot is discussed item by item, so everyone gets the same order.
  build(key_a[key_a$set == "pilot", ], key_b[key_b$set == "pilot", ],
        file.path(out, "pilot", sprintf("CS43_pilot_%s.xlsx", slug(who))), SEED)
  build(key_a[key_a$set == "main", ], key_b[key_b$set == "main", ],
        file.path(out, "main", sprintf("CS43_labelling_%s.xlsx", slug(who))), SEED + i)
}

# --- supplement workbooks, media only --------------------------------------
#
# Written to their own folder and NOT sent. The seven labellers already have
# the main sheets; whether to add these is the coordinator's call, not this
# script's. Task B is absent because the supplement is about media sources -
# the notices are unchanged.
build_media_only <- function(a, file, order_seed) {
  set.seed(order_seed)
  a <- a[sample(nrow(a)), ]
  wb <- openxlsx::createWorkbook()
  openxlsx::addWorksheet(wb, "Read me")
  openxlsx::writeData(wb, "Read me", c(
    README[1:2],
    "补充表：社交媒体 (Reddit, Facebook, Bluesky, Google+, Q&A)。规则与主表完全相同。",
    "Supplement: social media sources. The rules are exactly those of the main sheet.",
    "Twitter 只有 ID 和日期，没有正文，所以不在这里 / Twitter carries no text under the licence.",
    "", README[3:8]))
  openxlsx::setColWidths(wb, "Read me", 1, 140)
  sa <- data.frame(item_id = a$item_id, paper_title = a$paper_title,
                   source = a$kind, item_title = a$title, item_summary = a$summary,
                   label = "", evidence = "", translated = "", note = "")
  write_sheet(wb, "A_media", sa, c("paper_title", "item_title", "item_summary"),
              c("label", "evidence", "translated", "note"),
              c(9, 36, 11, 40, 70, 28, 35, 11, 25))
  openxlsx::addWorksheet(wb, "Codes", visible = FALSE)
  openxlsx::writeData(wb, "Codes", data.frame(A = A_CODES), startCol = 1)
  openxlsx::writeData(wb, "Codes", data.frame(C = C_CODES), startCol = 2)
  rows_a <- 2:(nrow(sa) + 1)
  openxlsx::dataValidation(wb, "A_media", col = match("label", names(sa)), rows = rows_a,
                           type = "list", value = list_ref("A", length(A_CODES)))
  openxlsx::dataValidation(wb, "A_media", col = match("translated", names(sa)),
                           rows = rows_a, type = "list", value = list_ref("C", 1))
  openxlsx::saveWorkbook(wb, file, overwrite = TRUE)
}

sup <- file.path(out, "supplement")
dir.create(sup, recursive = TRUE, showWarnings = FALSE)
for (i in seq_along(LABELLERS)) {
  build_media_only(key_s[key_s$set == "main", ],
                   file.path(sup, sprintf("CS43_social_%s.xlsx", slug(LABELLERS[i]))),
                   SEED + 1000 + i)
}
build_media_only(key_s[key_s$set == "pilot", ],
                 file.path(sup, "CS43_social_pilot.xlsx"), SEED + 1000)
cat("written:", length(list.files(sup)), "supplement workbooks in", sup,
    "(not sent - the coordinator decides)\n")

summary <- data.frame(
  metric = c("A frame: media items (news/blogs, before/after, >= 30 chars)", "A frame: papers",
             paste("A frame papers:", cells), "A pilot items", "A main items",
             "B frame: papers with an extracted notice", "B pilot items", "B main items",
             "labellers"),
  value = c(nrow(posts), length(unique(posts$doi)),
            sapply(cells, function(c) length(unique(posts$doi[posts$cell == c]))),
            nrow(pilot_a), nrow(main_a), nrow(ret), PILOT_B, N_B, length(LABELLERS)))
summary <- rbind(summary, data.frame(
  metric = c("A supplement frame: social items (text in title or summary)",
             "A supplement frame: papers",
             "A supplement: items drawn (not sent)",
             "excluded, no text under the licence: twitter items",
             "excluded, no text under the licence: twitter papers"),
  value = c(nrow(social), length(unique(social$doi)), nrow(key_s),
            sum(all_posts$kind == "twitter"),
            length(unique(all_posts$doi[all_posts$kind == "twitter"])))))
utils::write.csv(summary, file.path("data", "reports", "labelling-sample-2026-09-19.csv"),
                 row.names = FALSE)
print(summary, row.names = FALSE)
print(table(key_a$set, key_a$cell))
