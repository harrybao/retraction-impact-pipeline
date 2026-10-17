# Is the evidence a real quote?
#
# Both prompts end with "quote at most 15 words from the item text", and the
# client asked for a reason with every answer. An answer whose evidence is not in the text has not given a reason - it
# has produced something that looks like one, and a reader checking the label
# against the quote would be checking it against nothing.
#
# This is checkable without a human. The evidence either occurs in the text it
# claims to quote or it does not, and an adjudication of round 1 found the
# model quoting the paper's title instead of the item, and in four cases
# quoting the prompt's own worked example - "the study, which has since been
# retracted" - as if it were the item's words.
#
# The comparison is deliberately generous, because the aim is to find invention,
# not paraphrase: case, punctuation, quotation marks and whitespace are
# stripped, and an evidence phrase counts as grounded if the longest run of its
# words that appears verbatim in the text covers most of it.

# Words of a string, lowercased, punctuation dropped.
ev_words <- function(x) {
  x <- tolower(as.character(x))
  x <- gsub("[[:punct:]‘’“”–—]+", " ", x)
  strsplit(trimws(gsub("[[:space:]]+", " ", x)), " ", fixed = TRUE)
}

# The longest run of `quote`'s words that appears consecutively in `text`,
# as a share of the quote's length. 1 means the whole quote is in the text.
longest_run_share <- function(quote, text) {
  q <- ev_words(quote)[[1]]
  t <- paste0(" ", paste(ev_words(text)[[1]], collapse = " "), " ")
  q <- q[nzchar(q)]
  if (!length(q)) return(NA_real_)
  best <- 0L
  for (i in seq_along(q)) {
    # Longest run starting at i. Stop as soon as it cannot beat `best`.
    j <- i
    while (j <= length(q) &&
           grepl(paste0(" ", paste(q[i:j], collapse = " "), " "), t, fixed = TRUE)) {
      j <- j + 1L
    }
    best <- max(best, j - i)
    if (best == length(q)) break
  }
  best / length(q)
}

# One row per answer: how much of its evidence is really in the text, whether
# it instead quotes one of the `other` fields (the paper's title, the prompt),
# and a verdict.
#
# `min_share` is the share of the quote that must appear verbatim to count as
# grounded, and `min_words` the length below which a quote is too short to
# judge - two or three common words match anything.
evidence_check <- function(evidence, text, other = NULL, min_share = 0.6,
                           min_words = 4L) {
  n <- length(evidence)
  stopifnot(length(text) == n)
  share <- vapply(seq_len(n), function(i) longest_run_share(evidence[i], text[i]), numeric(1))
  nw <- vapply(ev_words(evidence), function(w) sum(nzchar(w)), integer(1))
  out <- data.frame(words = nw, share = round(share, 3), stringsAsFactors = FALSE)
  if (!is.null(other)) {
    for (nm in names(other)) {
      out[[paste0("share_", nm)]] <- round(vapply(seq_len(n), function(i)
        longest_run_share(evidence[i], other[[nm]][i]), numeric(1)), 3)
    }
  }
  out$verdict <- ifelse(is.na(share) | nw == 0L, "empty",
                 ifelse(nw < min_words, "too short to judge",
                 ifelse(share >= min_share, "grounded", "not in the text")))
  # Where it came from instead, if anywhere.
  if (!is.null(other)) {
    for (nm in names(other)) {
      took <- out$verdict == "not in the text" & !is.na(out[[paste0("share_", nm)]]) &
        out[[paste0("share_", nm)]] >= min_share
      out$verdict[took] <- paste("quotes the", nm)
    }
  }
  out
}

# Does the text contain any word that could support "the retraction has
# happened"?
#
# Protocol v2's Q1 sends an item to A2 only if the text SAYS the paper was
# retracted, withdrawn, removed by the journal, or placed under an expression of
# concern, and it lists the words. So an A2 answer whose text contains none of
# them is suspect, and that can be tested without a person.
#
# It is a flag, not a correction. The list cannot cover every language the
# corpus contains, so a missing word is weaker evidence than a present one:
# treat "no retraction word" as "look at this", never as "the label is wrong".
# The point is to put a number on how much of A2 rests on the model's reading
# alone - adjudication of round 1 found it raising A3 material to A2, and the
# 14 October saved corpus has 10,167 A2 answers against 493 A3, including Q&A.
RETRACTION_WORDS <- c(
  # English and the romance/germanic languages the corpus actually contains
  "retract", "withdraw", "withdrew", "withdrawn", "pulled the",
  "expression of concern", "removed by the journal", "unpublish",
  "rétract", "retiré", "retirado", "retratado", "ritirat",
  "zurückgezogen", "zurückziehung", "ingetrokken", "tilbaketrukket",
  "wycofan", "stažen", "odvolan", "geri çekil",
  # Cyrillic, CJK, and the scripts the sample turned up
  "отозв", "відклик",
  "撤稿", "撤回", "撤销", "撤下",
  "取り下げ", "撤回され", "철회",
  "سحب")

has_retraction_word <- function(text) {
  text <- tolower(as.character(text))
  text[is.na(text)] <- ""
  Reduce(`|`, lapply(RETRACTION_WORDS, function(w) grepl(w, text, fixed = TRUE)))
}

# For each A2 answer, whether a listed lexical cue is present. Returns one row per
# input with `label`, `has_word`, and `suspect` (labelled A2, no word found).
a2_support <- function(label, text) {
  stopifnot(length(label) == length(text))
  w <- has_retraction_word(text)
  data.frame(label = label, has_word = w,
             suspect = !is.na(label) & label == "A2" & !w,
             stringsAsFactors = FALSE)
}

evidence_summary <- function(chk) {
  t <- table(factor(chk$verdict, unique(c("grounded", "not in the text", "empty",
                                          "too short to judge", chk$verdict))))
  data.frame(measure = names(t), value = as.integer(t), stringsAsFactors = FALSE)
}
