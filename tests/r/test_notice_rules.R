# Rscript tests/r/test_notice_rules.R
source("R/notice_rules.R")

check <- function(ok, what) if (!isTRUE(ok)) stop("FAILED: ", what, call. = FALSE)

a <- data.frame(
  id = c("ieee", "plain", "request", "stated", "notR0"),
  R0_no_reason = c("TRUE", "TRUE", "TRUE", "TRUE", "FALSE"),
  R9_other = c("FALSE", "FALSE", "FALSE", "FALSE", "FALSE"),
  clarity = c("C0", "C0", "C1", "C0", "C3"),
  stringsAsFactors = FALSE)
text <- c(
  "This paper has been found to be in violation of IEEE's Publication Principles.",
  "This article has been retracted.",
  "Retracted at the request of the authors.",
  "The article violates the journal's policies on authorship.",
  "Figure 3 duplicates Figure 2 of another article, in violation of the journal's policies.")
r <- notice_policy_rule(a, text)

check(r$R0_no_reason[1] == "FALSE" && r$R9_other[1] == "TRUE", "a violated policy is a stated reason")
check(r$clarity[1] == "C1", "and a vague one: C0 becomes C1")
check(r$R0_no_reason[2] == "TRUE" && !r$fixed_policy[2], "a bare notice stays R0")
check(r$R0_no_reason[3] == "TRUE" && !r$fixed_policy[3], "who asked is not a reason (protocol B2)")
check(r$R9_other[4] == "TRUE", "'violates ... policies' is caught")
check(r$R0_no_reason[5] == "FALSE" && r$R9_other[5] == "FALSE" && r$clarity[5] == "C3",
      "an answer that was not R0 is never touched")
check(identical(notice_policy_rule(a, rep(NA_character_, 5))$R0_no_reason, a$R0_no_reason),
      "no text, no change")
cat("all passed\n")
