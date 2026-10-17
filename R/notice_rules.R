# A rule applied to the model's notice answers, from the protocol's own
# definitions.
#
# The protocol defines R0 as "the notice gives no reason at all" and R9 as "a
# stated reason that fits none of the above". A notice saying the paper "has
# been found to be in violation of IEEE's Publication Principles" - or of a
# journal's policies, guidelines, terms or information for authors - states a
# reason, however vague: R9, with clarity C1.
#
# The model reads those as R0. On the 200 validation notices it said "no
# reason" 46 times where the seven labellers' majority said so 20 times, and 21
# of the 26 it added are this sentence (analysis/notice_r0_check.R). The
# labellers applied the definition; the model did not. So, like "use R0
# alone" (task_b_enforce in R/llm.R), the definition is applied to the answers
# rather than left to the model, and written beside the raw answers, never over
# them.

POLICY_VIOLATION_RE <- paste0(
  "violat[a-z]* [^.]{0,60}",
  "(principles|policy|policies|guidelines|information for authors|terms)")

# answers: task_b_enforce()d answers; text: the notice text each was asked about.
notice_policy_rule <- function(answers, text) {
  r0 <- toupper(as.character(answers$R0_no_reason)) == "TRUE"
  hit <- r0 & !is.na(text) & grepl(POLICY_VIOLATION_RE, text, ignore.case = TRUE, perl = TRUE)
  hit[is.na(hit)] <- FALSE
  answers$R0_no_reason[hit] <- "FALSE"
  answers$R9_other[hit] <- "TRUE"
  answers$clarity[hit & answers$clarity %in% "C0"] <- "C1"
  answers$fixed_policy <- hit
  answers
}
