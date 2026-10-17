# Checks for R/llm_text.R. Run from the project root:
#   Rscript tests/r/test_llm_text.R

source("R/llm_text.R")

fails <- 0L
ok <- function(label, cond) {
  if (isTRUE(cond)) cat("ok   ", label, "\n")
  else { fails <<- fails + 1L; cat("FAIL ", label, "\n") }
}
eq <- function(label, got, want) {
  if (!identical(got, want)) cat("      got: [", got, "]\n      want: [", want, "]\n", sep = "")
  ok(label, identical(got, want))
}

# -- entities ---------------------------------------------------------------
eq("named entities decode", decode_entities("p &lt; 0.05 &amp; n &gt; 30"), "p < 0.05 & n > 30")
eq("a numeric reference decodes to its character",
   decode_entities("&#25454;Retraction Watch&#32593;"), "据Retraction Watch网")
eq("several numeric references in one string all decode",
   decode_entities("&#20013;&#25991;&#27979;&#35797;"), "中文测试")
eq("hexadecimal references decode", decode_entities("caf&#xE9; &#x4E2D;"), "café 中")
eq("an out-of-range reference is dropped, not left as text",
   decode_entities("a&#99999999;b"), "ab")
eq("a bare ampersand is left alone", decode_entities("Smith & Jones"), "Smith & Jones")
eq("&amp;gt; decodes once, not twice", decode_entities("&amp;gt;"), "&gt;")
eq("stray markup goes", decode_entities("the <em>p</em> value"), "the p value")
eq("a lone < is not treated as a tag", decode_entities("if x < y then"), "if x < y then")
eq("runs of whitespace collapse", decode_entities("a  \n b&nbsp;&nbsp;c"), "a b c")
eq("NA becomes empty text", decode_entities(NA), "")
ok("a vector is handled elementwise",
   identical(decode_entities(c("&lt;", "&gt;")), c("<", ">")))

# -- length in any script ---------------------------------------------------
eq("English words are counted by whitespace",
   text_length_words("one two three four five"), 5L)
ok("a full Chinese sentence is not 'fewer than 10 words'",
   text_length_words(paste(rep("中文", 12), collapse = "")) >= 10L)
eq("empty text is zero words", text_length_words(""), 0L)
eq("the larger of the two counts wins",
   text_length_words("word 中文测试一二三四"), 4L)

# -- the placeholder --------------------------------------------------------
ph <- prepare_item_text("News story from Stat News on Thursday 05 August 2021", "")
ok("Altmetric's placeholder title is recognised", ph$placeholder)
ok("... and the item counts as unreadable", ph$unreadable)
ok("a placeholder WITH a summary is not a placeholder",
   !prepare_item_text("News story from Stat News on Thursday 05 August 2021",
                      "Researchers found that the drug reduced mortality by a third.")$placeholder)
ok("a real headline of the same shape is not a placeholder",
   !prepare_item_text("What we learned from Stat News on Thursday about the trial", "")$placeholder)

r <- prepare_item_text(c("Drug &amp; dosage study questioned by readers of the journal",
                         "News story from The Mirror on Sunday 02 February 2025",
                         "Short one"),
                       c("A team reported that the effect was smaller than first claimed here", "", ""))
ok("a real item is readable", !r$unreadable[1])
ok("entities are decoded in the prepared text", grepl("Drug & dosage", r$text[1], fixed = TRUE))
ok("the placeholder is unreadable", r$unreadable[2])
ok("a too-short item is unreadable", r$unreadable[3])
ok("title and summary are joined", grepl("questioned by readers", r$text[1]) &&
     grepl("smaller than first claimed", r$text[1]))

cat(if (fails) sprintf("\n%d FAILED\n", fails) else "\nall passed\n")
quit(status = if (fails) 1L else 0L)
