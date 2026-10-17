# Preparing an item's text for reading.
#
# The model reads what this returns, so anything wrong here is wrong in every
# answer. Three problems were found by adjudicating round 1 and then counting
# how often each occurs in the corpus.
#
# **Undecoded HTML entities.** Earlier, pre-Q&A diagnostics reported 11,277
# of 114,922 eligible raw posts and 1,853 with numeric references. These are
# historical diagnostics, not current deduplicated-item counts; the exact
# historical counting definition has not been independently recovered.
# Mostly &gt; &nbsp; &amp; &rsquo;, which merely clutter; but a Chinese item that reads
# "&#25454;Retraction Watch&#32593;&#31449;" is unreadable while the identical
# story from another outlet, properly decoded, is plain text. Decoding is not
# cosmetic: it decides whether the item can be read at all.
#
# **Altmetric's placeholder title**, 3,604 news items over 592 papers, of the
# form "News story from Stat News on Thursday 05 August 2021" with an empty
# summary. Altmetric writes it when it has no title, so the item has no content
# whatever - but it is 51 characters, so it passed the sampler's 30-character
# floor and seven people were asked to label seven of them. It is A0 by
# construction, and marking it here both gives the right answer and saves the
# model 3,604 calls. Note what it is NOT: the coverage is real and still counts
# as coverage; only its text is missing.
#
# **Chinese, Japanese and Korean text has no spaces**, so counting words by
# whitespace understates it. 742 in-scope CJK items have fewer than ten
# whitespace-separated words and a median of 166 characters - full articles.
# The protocol's "fewer than 10 words" rule, read literally, would send every
# one of them to A0. Length is therefore measured as words OR characters,
# whichever is the more generous.

# An item's identity: a content hash, so the same item gets the same id on
# every run whatever order the source rows arrive in, and a resumed run never
# asks the same question twice. Defined here, beside the text preparation,
# because the id is a hash of the PREPARED text - anything that reads answers
# back and needs to find their items must prepare the text the same way first.
hash_id <- function(x) {
  vapply(x, function(s) substr(unname(tools::md5sum(bytes = charToRaw(enc2utf8(s)))), 1, 16),
         character(1), USE.NAMES = FALSE)
}

# The id of a media item, from its paper and its prepared title and summary.
item_id_A <- function(doi, title, summary) {
  hash_id(paste(doi, title, summary, sep = "\t"))
}

# Altmetric's "no title" placeholder.
PLACEHOLDER_RE <- paste0("^(News story|Blog post|Post|Video|Podcast) from .+ on ",
                         "(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day [0-9]{1,2} ",
                         "[A-Z][a-z]+ [0-9]{4}$")

# HTML entities to characters, and tags away. Named entities are the ones that
# actually occur in this corpus; numeric references are decoded arithmetically.
NAMED_ENTITIES <- c(amp = "&", lt = "<", gt = ">", quot = "\"", apos = "'",
                    nbsp = " ", rsquo = "’", lsquo = "‘",
                    ldquo = "“", rdquo = "”", mdash = "—",
                    ndash = "–", hellip = "…", eacute = "é",
                    egrave = "è", agrave = "à", ccedil = "ç",
                    uuml = "ü", ouml = "ö", auml = "ä",
                    szlig = "ß", ntilde = "ñ", deg = "°",
                    middot = "·", bull = "•", trade = "™",
                    copy = "©", reg = "®", times = "×",
                    minus = "−", plusmn = "±", frac12 = "½",
                    alpha = "α", beta = "β", gamma = "γ",
                    micro = "µ", sup2 = "²", sup3 = "³")

decode_entities <- function(x) {
  x <- as.character(x)
  x[is.na(x)] <- ""
  # An entity that decodes to "&" is held aside until the very end. Decoding it
  # in place would let the next pass read what it produced: "&amp;gt;" is the
  # literal text "&gt;", not ">", and a sequential decode turns it into ">".
  AMP <- "\u0001"
  # Numeric references, decimal and hexadecimal. Each DISTINCT reference is
  # decoded once and replaced everywhere: the corpus has 115,000 items and a
  # handful of references repeated across them, so per-occurrence work would be
  # wasted.
  decode_num <- function(s, pattern, base) {
    refs <- unique(unlist(regmatches(s, gregexpr(pattern, s, perl = TRUE))))
    for (ref in refs) {
      code <- base::strtoi(gsub("[^0-9A-Fa-f]", "", sub("^&#[xX]?", "", ref)), base = base)
      repl <- if (is.na(code) || code < 1 || code > 0x10FFFF) ""
              else if (code == 38L) AMP else intToUtf8(code)
      s <- gsub(ref, repl, s, fixed = TRUE)
    }
    s
  }
  x <- decode_num(x, "&#[0-9]{1,10};", 10L)
  x <- decode_num(x, "&#[xX][0-9A-Fa-f]{1,8};", 16L)
  for (nm in names(NAMED_ENTITIES)) {
    v <- NAMED_ENTITIES[[nm]]
    x <- gsub(paste0("&", nm, ";"), if (v == "&") AMP else v, x, fixed = TRUE)
  }
  x <- gsub("<[a-zA-Z/][^>]{0,60}>", " ", x)          # stray markup
  x <- gsub(AMP, "&", x, fixed = TRUE)
  trimws(gsub("[[:space:]]+", " ", x))
}

# Words for a length rule that works in any script: whitespace-separated words,
# or one word per two CJK characters, whichever is larger. (Chinese averages
# about 1.5 characters per word; two is deliberately conservative.)
text_length_words <- function(x) {
  x <- as.character(x); x[is.na(x)] <- ""
  w <- vapply(strsplit(trimws(x), "[[:space:]]+"), function(s) sum(nzchar(s)), integer(1))
  cjk <- vapply(gregexpr("[一-鿿぀-ヿ가-힯]", x),
                function(m) if (m[1] < 0) 0L else length(m), integer(1))
  pmax(w, cjk %/% 2L)
}

# The text of a media item as the model should read it, plus why it may not be
# readable. `title` and `summary` are the raw Altmetric fields.
#
# Returns: text, words, placeholder (Altmetric had no title and no summary),
# and unreadable (placeholder, or under `min_words` - the protocol's A0 rule).
prepare_item_text <- function(title, summary, min_words = 10L) {
  title <- as.character(title); summary <- as.character(summary)
  title[is.na(title)] <- ""; summary[is.na(summary)] <- ""
  ph <- grepl(PLACEHOLDER_RE, trimws(title)) & !nzchar(trimws(summary))
  t <- decode_entities(title)
  s <- decode_entities(summary)
  txt <- trimws(paste(t, s))
  n <- text_length_words(txt)
  data.frame(title = t, summary = s, text = txt, words = n, placeholder = ph,
             unreadable = ph | n < min_words, stringsAsFactors = FALSE)
}
