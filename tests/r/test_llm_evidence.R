# Checks for R/llm_evidence.R. Run from the project root:
#   Rscript tests/r/test_llm_evidence.R

source("R/llm_evidence.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}

TXT <- "The journal has retracted the study after readers raised concerns about Figure 3B."

near <- function(label, got, want) ok(label, isTRUE(abs(got - want) < 1e-9))
near("a whole quote is fully in the text",
     longest_run_share("readers raised concerns about Figure 3B", TXT), 1)
near("case and punctuation do not matter",
     longest_run_share("“READERS RAISED CONCERNS,”", TXT), 1)
near("a quote sharing no word at all scores zero",
     longest_run_share("entirely fabricated wording here", TXT), 0)
near("one shared word is one word's worth",
     longest_run_share("entirely fabricated journal wording", TXT), 1 / 4)
near("a quote half in the text scores half",
     longest_run_share("raised concerns lunar rover", TXT), 2 / 4)
ok("an empty quote is NA", is.na(longest_run_share("", TXT)))
ok("words out of order do not count as a run",
     longest_run_share("concerns raised readers about", TXT) < 0.6)

ev <- c("readers raised concerns about Figure 3B",   # grounded
        "the study, which has since been retracted", # invented (the prompt's example)
        "Long noncoding RNA PEG10 facilitates",      # taken from the paper title
        "",                                          # empty
        "the study")                                 # too short to judge
chk <- evidence_check(ev, rep(TXT, 5),
                      other = list(`paper title` = rep("Long noncoding RNA PEG10 facilitates cancer", 5)))
ok("a real quote is grounded", chk$verdict[1] == "grounded")
ok("an invented quote is caught", chk$verdict[2] == "not in the text")
ok("a quote taken from the paper title says so", chk$verdict[3] == "quotes the paper title")
ok("an empty quote is empty", chk$verdict[4] == "empty")
ok("a two-word quote is not judged", chk$verdict[5] == "too short to judge")
ok("word counts are reported", chk$words[1] == 6L)

s <- evidence_summary(chk)
ok("the summary counts every answer", sum(s$value) == 5L)
ok("the summary leads with grounded", s$measure[1] == "grounded")

# A long text must not be slow: the check runs over ~175,000 answers.
big <- paste(rep("lorem ipsum dolor sit amet consectetur", 400), collapse = " ")
t0 <- Sys.time()
invisible(longest_run_share("dolor sit amet consectetur lorem ipsum", big))
ok("a 2,400-word text is checked in under a second",
   as.numeric(difftime(Sys.time(), t0, units = "secs")) < 1)

# -- does the text support an A2 at all? -------------------------------------
ok("an English retraction word is found",
   has_retraction_word("The Lancet retracted the study on Tuesday"))
ok("withdrawn counts", has_retraction_word("the paper was withdrawn"))
ok("an expression of concern counts",
   has_retraction_word("the journal issued an expression of concern"))
ok("Chinese counts", has_retraction_word("该论文已被撤稿"))
ok("French counts", has_retraction_word("l'article a été rétracté"))
ok("case does not matter", has_retraction_word("RETRACTED"))
ok("an item that merely questions the paper has no such word",
   !has_retraction_word("Experts question the study's data and methods"))
ok("a misconduct story has none either",
   !has_retraction_word("Former scientist committed misconduct, watchdog finds"))
ok("empty text has none", !has_retraction_word(""))
ok("NA is handled", !has_retraction_word(NA))

s <- a2_support(c("A2", "A2", "A3", "A1", NA),
                c("the paper was retracted", "experts raised doubts",
                  "experts raised doubts", "the drug works", "x"))
ok("an A2 backed by the text is not suspect", !s$suspect[1])
ok("an A2 with no retraction word is suspect", s$suspect[2])
ok("a non-A2 label is never suspect", !s$suspect[3] && !s$suspect[4])
ok("an unlabelled row is not suspect", !s$suspect[5])
ok("the word flag is reported for every row", sum(s$has_word) == 1L)

cat(if (fails) sprintf("\n%d FAILED\n", fails) else "\nall passed\n")
quit(status = if (fails) 1L else 0L)
