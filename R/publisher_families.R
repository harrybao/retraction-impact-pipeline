# Publisher imprints, grouped under the publisher that owned them.
#
# The client asked for results by publisher. Retraction Watch writes the
# publisher as a free-text imprint, so the name that appears is whichever label
# the journal carried, and a ranking taken from the raw strings is a ranking of
# spellings. Springer is the clearest case: as written it has 5,211 records,
# but its imprints add 3,339 (Nature Publishing Group), 700 (BioMed Central),
# 280 (Cureus) and 190 (Nature Publishing Group on its own), so the real figure
# is over 9,700 and its rank changes.
#
# ## The rule
#
# One explicit mapping, listed below, and nothing else: a string not named
# there stands for itself.
#
# Retraction Watch does write a parent and its imprint as "Elsevier - Cell
# Press", and splitting on " - " is tempting, but the same punctuation appears
# inside ordinary names. Tried on the corpus it produced "Korean Society of
# Otorhinolaryngology" out of "... - Head and Neck Surgery", "Enugu State
# Chapter" out of "... - Nigerian Medical Association", and truncated several
# Russian institutes mid-title. Those are all one-record publishers, so the
# ranking would not have moved and the damage would not have shown - which is
# the reason not to leave it in. Every merge here is written down and counted.
#
# ## What is deliberately NOT merged
#
# Ownership that changed after most of the retractions happened. IOS Press was
# bought by SAGE in November 2023 and carries 1,786 records, nearly all of them
# published and retracted while it was independent; folding it into SAGE would
# attribute another publisher's decade to SAGE. The same reasoning keeps
# Frontiers, MDPI and Hindawi separate from anyone - Hindawi was acquired by
# Wiley in 2021, and its 11,524 records are the single largest block in the
# corpus, so merging it into Wiley would hide the mass-retraction story the
# analysis is about rather than reveal it. Both decisions are reported in the
# table so the client can see them and disagree.
#
# The mapping is written out to data/share/publisher_families.csv, which is
# tracked, so it is fixed and auditable before any estimate is made.

# imprint (as Retraction Watch writes it) -> parent
PUBLISHER_PARENT <- c(
  # Springer Nature: the 2015 Springer / Nature merger, plus what it has bought.
  "Nature Publishing Group"                                  = "Springer Nature",
  "Springer"                                                 = "Springer Nature",
  "Springer - Nature Publishing Group"                       = "Springer Nature",
  "Springer - Biomed Central (BMC)"                          = "Springer Nature",
  "BioMed Central (BMC)"                                     = "Springer Nature",
  "Biomed Central (BMC)"                                     = "Springer Nature",
  "Cureus (Part of Springer Nature as of December 2022)"     = "Springer Nature",
  "Pleiades Publishing (in Partnership with Springer Nature)" = "Springer Nature",
  "Scientific Reports"                                       = "Springer Nature",
  # Elsevier.
  "Elsevier - Cell Press"                                    = "Elsevier",
  "Cell Press"                                               = "Elsevier",
  # Wiley. Hindawi is left out on purpose - see the header.
  "Wiley-Blackwell"                                          = "Wiley",
  "Wiley - Blackwell"                                        = "Wiley",
  "Blackwell"                                                = "Wiley",
  # Taylor and Francis.
  "Taylor and Francis - Dove Press"                          = "Taylor and Francis",
  "Taylor & Francis"                                         = "Taylor and Francis",
  "Dove Press"                                               = "Taylor and Francis",
  "Routledge"                                                = "Taylor and Francis",
  # Wolters Kluwer.
  "Wolters Kluwer - Lippincott Williams & Wilkins"           = "Wolters Kluwer",
  "Wolters Kluwer - Medknow"                                 = "Wolters Kluwer",
  "Lippincott Williams & Wilkins"                            = "Wolters Kluwer",
  "Medknow"                                                  = "Wolters Kluwer",
  # Future Science Group and its Future Medicine imprint.
  "Future Medicine - Future Science Group"                   = "Future Science Group",
  "Future Medicine"                                          = "Future Science Group",
  # SAGE. IOS Press is left out on purpose - see the header.
  "SAGE Publications"                                        = "SAGE",
  "Sage Publications"                                        = "SAGE",
  "SAGE Publishing"                                          = "SAGE"
)

# Imprints kept apart from their present owner, with the reason, so the choice
# is visible in the output rather than buried in this file.
PUBLISHER_NOT_MERGED <- data.frame(
  publisher = c("Hindawi", "IOS Press (bought by Sage November 2023)"),
  present_owner = c("Wiley", "SAGE"),
  acquired = c("2021", "November 2023"),
  reason = c("its records are the largest block in the corpus and nearly all predate the acquisition",
             "nearly all its records predate the acquisition"),
  stringsAsFactors = FALSE)

publisher_family <- function(publisher) {
  p <- trimws(as.character(publisher))
  fam <- unname(PUBLISHER_PARENT[p])
  fam[is.na(fam)] <- p[is.na(fam)]        # not listed: the string stands for itself
  fam[is.na(fam) | fam == ""] <- NA_character_
  fam
}

# Write the mapping for every publisher string in the corpus, with how many
# records each covers, so it can be read and challenged before it is used.
write_publisher_families <- function(
    records = file.path("data", "clean", "records_clean.csv"),
    out = file.path("data", "share", "publisher_families.csv")) {
  rec <- utils::read.csv(records, stringsAsFactors = FALSE,
                         fileEncoding = "UTF-8-BOM", colClasses = "character")
  p <- trimws(rec$publisher)
  p <- p[nzchar(p)]
  tab <- as.data.frame(table(p), stringsAsFactors = FALSE)
  names(tab) <- c("publisher", "records")
  tab$family <- publisher_family(tab$publisher)
  tab$merged <- tab$family != tab$publisher
  tab <- tab[order(-tab$records), ]
  dir.create(dirname(out), showWarnings = FALSE, recursive = TRUE)
  utils::write.csv(tab, out, row.names = FALSE)
  message("written: ", out, " (", nrow(tab), " publisher strings, ",
          sum(tab$merged), " merged into a parent)")
  invisible(tab)
}
