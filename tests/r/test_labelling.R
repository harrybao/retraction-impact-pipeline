# Checks for R/labelling.R. Run from the project root:
#   Rscript tests/r/test_labelling.R
#
# The agreement statistics are written from the formulas rather than taken from
# a package, so they are checked against Krippendorff's own worked example
# (Krippendorff 2011, "Computing Krippendorff's Alpha-Reliability"), which
# publishes the answers: alpha = .743 nominal and .815 ordinal. A reliability
# figure that is quietly wrong is worse than none, because it will be quoted.

source("R/labelling.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
near <- function(label, got, want, tol = 1e-3) {
  if (!isTRUE(abs(got - want) < tol))
    cat("      got:", format(got, digits = 6), " want:", want, "\n")
  ok(label, isTRUE(abs(got - want) < tol))
}

# -- Krippendorff's published example ---------------------------------------
# Units down the rows, observers across. NA where an observer skipped a unit.
kd <- rbind(
  c("1", "1", NA,  "1"),
  c("2", "2", "3", "2"),
  c("3", "3", "3", "3"),
  c("3", "3", "3", "3"),
  c("2", "2", "2", "2"),
  c("1", "2", "3", "4"),
  c("4", "4", "4", "4"),
  c("1", "1", "2", "1"),
  c("2", "2", "2", "2"),
  c(NA,  "5", "5", "5"),
  c(NA,  NA,  "1", "1"),
  c(NA,  "3", NA,  NA))
rownames(kd) <- paste0("u", 1:12)

near("nominal alpha matches the published .743",
     krippendorff_alpha(kd, "nominal"), 0.743)
near("ordinal alpha matches the published .815",
     krippendorff_alpha(kd, "ordinal"), 0.815)

# -- boundary behaviour -----------------------------------------------------
perfect <- rbind(c("A1", "A1", "A1"), c("A2", "A2", "A2"), c("A3", "A3", "A3"))
near("perfect agreement is alpha 1", krippendorff_alpha(perfect, "nominal"), 1)

one_value <- rbind(c("A1", "A1"), c("A1", "A1"))
ok("alpha is undefined when only one category was ever used",
   is.na(krippendorff_alpha(one_value, "nominal")))

# Systematic disagreement: two labellers who never agree score at or below 0.
disagree <- rbind(c("A1", "A2"), c("A2", "A1"), c("A1", "A2"), c("A2", "A1"))
ok("chance-level or worse agreement is not positive",
   krippendorff_alpha(disagree, "nominal") <= 0)

# A unit only one person labelled contributes nothing and must not crash.
sparse <- rbind(c("A1", "A1"), c("A2", NA), c("A3", "A3"))
ok("single-label units are skipped, not counted as agreement",
   !is.na(krippendorff_alpha(sparse, "nominal")))

# The ordinal metric must treat C0 vs C1 as a smaller error than C0 vs C3.
close_pairs <- rbind(c("C0", "C1"), c("C1", "C2"), c("C2", "C3"), c("C3", "C2"))
far_pairs <- rbind(c("C0", "C3"), c("C1", "C2"), c("C2", "C3"), c("C3", "C0"))
ok("ordinal alpha punishes far disagreement more than near",
   krippendorff_alpha(close_pairs, "ordinal", LABEL_C) >
     krippendorff_alpha(far_pairs, "ordinal", LABEL_C))
ok("nominal alpha cannot tell near from far",
   isTRUE(all.equal(krippendorff_alpha(close_pairs, "nominal", LABEL_C),
                    krippendorff_alpha(far_pairs, "nominal", LABEL_C))))

# -- Fleiss kappa and pairwise agreement, computed by hand ------------------
# 4 items, 3 raters, 2 categories. Pbar = 2/3, Pe = 1/2, kappa = 1/3.
fk <- rbind(c("Y", "Y", "Y"), c("Y", "Y", "N"), c("N", "N", "N"), c("Y", "N", "N"))
near("Fleiss kappa", fleiss_kappa(fk), 1 / 3)
near("pairwise agreement", pairwise_agreement(fk), 8 / 12)

# Fleiss uses only items everyone rated; alpha uses them all.
fk_na <- rbind(fk, c("Y", "N", NA))
near("Fleiss ignores incomplete items", fleiss_kappa(fk_na), 1 / 3)

# -- rating_matrix ----------------------------------------------------------
long <- data.frame(
  item_id = c("AP01", "AP01", "AP02", "AP02", "AP03"),
  labeller = c("Ann", "Bo", "Ann", "Bo", "Ann"),
  label = c("A2", "A2", "A1", "A3", "A4"),
  stringsAsFactors = FALSE)
m <- rating_matrix(long$item_id, long$labeller, long$label)
ok("matrix is items x labellers", identical(dim(m), c(3L, 2L)))
ok("a missing label stays NA", is.na(m["AP03", "Bo"]))
ok("labels land in the right cell", m["AP02", "Bo"] == "A3")

# -- majority vote and ties -------------------------------------------------
v <- majority_vote(rating_matrix(
  rep(c("i1", "i2", "i3"), each = 3),
  rep(c("Ann", "Bo", "Cy"), 3),
  c("A1", "A1", "A2",    # clear majority A1
    "A1", "A2", "A3",    # three-way tie
    "A2", "A2", "A2")),  # unanimous
  LABEL_A_PRECEDENCE)
ok("majority is found", v$majority[v$item_id == "i1"] == "A1")
near("agreement share", v$agreement[v$item_id == "i1"], 2 / 3)
ok("a tie has no majority", is.na(v$majority[v$item_id == "i2"]))
ok("a tie is flagged for the coordinator", v$tied[v$item_id == "i2"])
ok("the tie's suggestion follows the protocol precedence A2 > A3 > A1",
   v$suggested[v$item_id == "i2"] == "A2")
ok("a unanimous item is not flagged", !v$tied[v$item_id == "i3"])
near("unanimous agreement is 1", v$agreement[v$item_id == "i3"], 1)
ok("vote counts are recorded", v$votes[v$item_id == "i3"] == 3L)

# -- the dropdown writes the whole option, not the code ---------------------
# The workbook's list reads "A2 Acknowledges retraction", so that is what Excel
# stores, and that is what came back from all six labellers. Comparing against
# "A2" matched nothing: every label scored invalid and alpha came out NA, which
# looked like unusable sheets and was an unusable reader.
eq <- function(got, want) isTRUE(all.equal(unname(got), want))
ok("the dropdown's full option yields its code",
   eq(label_code("A2 Acknowledges retraction", LABEL_A), "A2"))
ok("a bare code still works", eq(label_code("A2", LABEL_A), "A2"))
ok("lowercase works", eq(label_code("a2", LABEL_A), "A2"))
ok("surrounding space works", eq(label_code("  A2  ", LABEL_A), "A2"))
ok("clarity's full option too", eq(label_code("C3 Clear", LABEL_C), "C3"))
ok("an empty cell is missing", is.na(label_code("", LABEL_A)))
ok("NA is missing", is.na(label_code(NA, LABEL_A)))
ok("a code from the wrong scale is rejected", is.na(label_code("C3 Clear", LABEL_A)))
ok("free text is rejected", is.na(label_code("not sure", LABEL_A)))
ok("an out-of-range code is rejected", is.na(label_code("A9 Whatever", LABEL_A)))
eq_v <- label_code(c("A1 Repeats/supports finding", "A2", "", "junk"), LABEL_A)
ok("vectorised", eq(eq_v, c("A1", "A2", NA, NA)))

# End to end: full-option labels must score, not fall through to invalid.
a_full <- data.frame(
  item_id = rep(c("A001", "A002"), each = 3),
  labeller = rep(c("Ann", "Bo", "Cy"), 2),
  label = c("A2 Acknowledges retraction", "A2 Acknowledges retraction", "A2",
            "A1 Repeats/supports finding", "A1 Repeats/supports finding",
            "A4 Unrelated"),
  stringsAsFactors = FALSE)
ra <- score_task_a(a_full)
ok("no full-option label is scored invalid", nrow(ra$invalid) == 0)
ok("the majority is found from them", ra$vote$majority[ra$vote$item_id == "A001"] == "A2")
ok("and it is unanimous", ra$vote$votes[ra$vote$item_id == "A001"] == 3L)
ok("alpha is computable", !is.na(ra$stats$value[1]))

# -- task B: a blank row is not ten considered Nos --------------------------
# The sheet asks for a Y against the reasons that apply and nothing against the
# rest, so a blank cell on an answered row is a real N. A row where nothing at
# all was filled in is a row nobody labelled, and scoring it as ten Ns would
# credit the labeller with agreeing with everyone who said no.
b <- data.frame(
  item_id = rep(c("BP01", "BP02"), each = 3),
  labeller = rep(c("Ann", "Bo", "Cy"), 2),
  R1_fabrication = c("Y", "Y", "",   "Y", "",  ""),
  R2_image       = c("",  "",  "",   "",  "",  ""),
  R9_other       = c("",  "",  "",   "",  "Y", ""),
  R0_no_reason   = c("",  "",  "",   "",  "",  ""),
  clarity        = c("C3", "C2", "", "C1", "C2", ""),
  stringsAsFactors = FALSE)
rb <- suppressMessages(score_task_b(b))
r1 <- rb$reason_votes[rb$reason_votes$reason == "R1_fabrication", ]
ok("an answered row with a blank cell counts that cell as No",
   r1$n_labels[r1$item_id == "BP01"] == 2L)
ok("a wholly blank row is not counted at all",
   r1$n_labels[r1$item_id == "BP02"] == 2L)
# Cy answered nothing on either item, so Cy contributes no reason labels.
ok("the untouched labeller contributes nothing",
   all(rb$reason_votes$n_labels <= 2L))
# A labeller who gave only clarity has answered, so their blanks are real Nos.
b2 <- b
b2$clarity[3] <- "C0"
rb2 <- suppressMessages(score_task_b(b2))
r1b <- rb2$reason_votes[rb2$reason_votes$reason == "R1_fabrication", ]
ok("clarity alone counts as having answered",
   r1b$n_labels[r1b$item_id == "BP01"] == 3L)

# -- repeated notice text ---------------------------------------------------
# Publisher boilerplate repeats, so the same words can be drawn several times.
# Seven people agreeing on one template is one piece of evidence, not five.
bd <- data.frame(
  item_id = rep(c("B01", "B02", "B03"), each = 2),
  labeller = rep(c("Ann", "Bo"), 3),
  notice_text = rep(c("This article has been retracted.",   # same words
                      "This article has been retracted.",
                      "Figure 3B duplicates Figure 2A."), each = 2),
  R0_no_reason = c("Y", "Y", "Y", "Y", "", ""),
  R2_image     = c("",  "",  "",  "",  "Y", "Y"),
  clarity      = c("C0", "C0", "C0", "C1", "C3", "C3"),
  stringsAsFactors = FALSE)
rbd <- suppressMessages(score_task_b(bd))
ok("duplication is reported", !is.null(rbd$duplication))
val <- function(m) rbd$duplication$value[rbd$duplication$metric == m]
ok("three items drawn", val("items drawn") == 3L)
ok("but only two distinct texts", val("distinct notice texts") == 2L)
ok("two items share their text", val("items sharing their text with another") == 2L)
ok("the largest repeat group is two", val("largest repeat group") == 2L)
ok("a distinct-text agreement figure is reported",
   any(grepl("distinct texts", rbd$stats$task)))
# Ann said C0 both times; Bo said C0 then C1.
ok("self-consistency across a repeat is counted",
   val("labeller saw the same text twice and agreed with themselves") == 1L)
ok("and so is inconsistency", val("... and did not") == 1L)

# With no repeats there is nothing to report beyond the counts.
bu <- bd[bd$item_id != "B02", ]
rbu <- suppressMessages(score_task_b(bu))
ok("no repeats means no distinct-text row",
   !any(grepl("distinct texts", rbu$stats$task)))

# -- name parsing -----------------------------------------------------------
ok("pilot filename to labeller", labeller_of("CS43_pilot_Labeller_B.xlsx") == "Labeller B")
ok("main filename to labeller",
   labeller_of("data/labelling/main/CS43_labelling_Labeller_F.xlsx") == "Labeller F")
ok("supplement filename to labeller",
   labeller_of("data/labelling/supplement/CS43_social_Labeller_C.xlsx") == "Labeller C")
ok("an unknown prefix is not silently kept as a name",
   labeller_of("CS43_social_Labeller_E.xlsx") == "Labeller E")
# Sheets come back through chat and email, and a browser adds (1) to a file
# downloaded twice. Left in, one person who sent their sheet twice becomes two
# labellers who never disagree, and every agreement figure goes up.
ok("a browser's duplicate suffix is not part of the name",
   labeller_of("CS43_labelling_Labeller_E(1).xlsx") == "Labeller E")
ok("with a space before it either",
   labeller_of("CS43_labelling_Labeller_C (2).xlsx") == "Labeller C")
ok("a name that really ends in a number is left alone",
   labeller_of("CS43_labelling_Labeller_3.xlsx") == "Labeller 3")
# Sheets come back renamed. One arrived marked "already labelled" in Chinese.
ok("a prefix added by the labeller is discarded",
   labeller_of("（已标注）CS43_labelling_Labeller_D.xlsx") == "Labeller D")
ok("an English prefix too",
   labeller_of("done - CS43_labelling_Labeller_B.xlsx") == "Labeller B")
ok("a round 2 sheet parses",
   labeller_of("CS43_round2_Labeller_C.xlsx") == "Labeller C")

# -- reading a workbook that holds more than one task A sheet ----------------
# Round 2 and the social supplement go out in one workbook per person, as
# "A_media" and "A_social". A reader that knew only "A_media" would score the
# round-2 items and silently ignore the 100 social ones.
local({
  dir <- tempfile(); dir.create(dir)
  on.exit(unlink(dir, recursive = TRUE))
  mk <- function(who) {
    wb <- openxlsx::createWorkbook()
    openxlsx::addWorksheet(wb, "Read me")
    openxlsx::writeData(wb, "Read me", "rules")
    openxlsx::addWorksheet(wb, "A_media")
    openxlsx::writeData(wb, "A_media", data.frame(
      item_id = c("A1", "A2"), label = c("A1 Repeats/supports finding", "A2 x"),
      evidence = c("e1", "e2"), stringsAsFactors = FALSE))
    openxlsx::addWorksheet(wb, "A_social")
    openxlsx::writeData(wb, "A_social", data.frame(
      item_id = c("S1", "S2"), source = c("reddit", "bluesky"),
      label = c("A4 Unrelated", "A0 Cannot tell"), evidence = c("", ""),
      stringsAsFactors = FALSE))
    openxlsx::saveWorkbook(wb, file.path(dir, sprintf("CS43_round2_%s.xlsx", who)))
  }
  mk("Labeller_B"); mk("Labeller_C")
  got <- suppressMessages(read_returned_labels(dir))
  ok("both task A sheets are read", nrow(got$A) == 8L)
  ok("the sheet each row came from is kept",
     setequal(unique(got$A$sheet), c("A_media", "A_social")))
  ok("two workbooks are two labellers, not four",
     length(unique(got$A$labeller)) == 2L)
  ok("a column only one sheet has is kept, not dropped", "source" %in% names(got$A))
  ok("... and is NA on the sheet that lacks it",
     all(is.na(got$A$source[got$A$sheet == "A_media"])))
  ok("... and holds its value where it exists",
     setequal(stats::na.omit(got$A$source), c("reddit", "bluesky")))
  ok("a workbook with no task B sheet is reported, not fatal",
     is.null(got$B) && !is.null(got$problems) && all(got$problems$task == "B"))
  ok("labels still parse from the dropdown text",
     identical(sort(unique(label_code(got$A$label, LABEL_A))), c("A0", "A1", "A2", "A4")))
})

cat("\n", if (fails) sprintf("%d FAILED", fails) else "all passed", "\n", sep = "")
quit(status = if (fails) 1L else 0L)
