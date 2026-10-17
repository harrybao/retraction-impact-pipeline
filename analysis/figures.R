# The media and notice figures, each drawn from the report CSV beside it.
#
#   Rscript analysis/figures.R
#
# Run after the analysis scripts (reproduce_results() runs it last). The
# citation figures are drawn by analysis/citation_impact.R. Writes PNGs to
# data/reports/figures/.

source("R/figures.R")

REP <- file.path("data", "reports")
OUT <- file.path(REP, "figures")
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)
rd <- function(f) utils::read.csv(file.path(REP, f), stringsAsFactors = FALSE)
comma <- function(x) format(x, big.mark = ",", trim = TRUE)
MEDIA_SOURCE <- paste("Source: Altmetric (paper and retraction-notice DOIs); each text classified by",
                      "a local model (Qwen3 14B), checked against seven student labellers.")
KINDS <- c(news = "News", blogs = "Blogs", reddit = "Reddit", facebook = "Facebook",
           bluesky = "Bluesky", googleplus = "Google+", video = "Video",
           podcasts = "Podcasts", "q&a" = "Q&A")

# 1. After retraction, by source
k <- rd("media-with-notices-by-kind.csv")
k <- k[k$period == "after" & k$articles >= 50, ]
hbar(file.path(OUT, "media-says-retracted-by-source.png"),
     sprintf("%s (%s)", KINDS[k$kind], comma(k$articles)), k$share_pct,
     "After retraction, most media items do not say the paper was retracted",
     paste("Share of articles published after the retraction that say the paper was retracted",
           "or withdrawn, by source; number of articles in brackets. Each article counted once.",
           "Sources with fewer than 50 articles are left out."),
     MEDIA_SOURCE)

# 2. Before, on the day, after
p <- rd("media-with-notices-by-period.csv")
p <- p[match(c("before", "same-day", "after"), p$period), ]
hbar(file.path(OUT, "media-says-retracted-by-period.png"),
     sprintf("%s (%s)", c("Before retraction", "On the retraction day", "After retraction"),
             comma(p$articles)), p$share_pct,
     "Media items that say the paper was retracted, before and after the retraction",
     paste("Share of articles, all text sources, by when they were published relative to the",
           "retraction date; number of articles in brackets. Before the retraction a few already",
           "report it (an expression of concern, or a retraction announced ahead of the notice)."),
     MEDIA_SOURCE, sort = FALSE)

# 3. Papers ever told, by publisher (paper DOIs)
pb <- rd("media-by-publisher.csv")
pb <- pb[pb$papers_mentioned_after >= 100, ]
hbar(file.path(OUT, "media-papers-told-by-publisher.png"),
     sprintf("%s (%s)", short_name(pb$value), comma(pb$papers_mentioned_after)),
     pb$papers_ever_told_pct,
     "Papers mentioned after retraction that are ever described as retracted, by publisher",
     paste("Of the papers with at least one media mention after their retraction, the share with",
           "at least one mention saying it was retracted; papers in brackets. Paper DOIs;",
           "publishers with at least 100 such papers."),
     sub("paper and retraction-notice DOIs", "paper DOIs", MEDIA_SOURCE))

# 4. What the notices say
r <- rd("notice-classification-reasons-overall.csv")
REASONS <- c(R0_no_reason = "No reason given", R1_fabrication = "Fabrication or falsification",
             R2_image = "Image problems", R3_plagiarism = "Plagiarism",
             R4_duplicate = "Duplicate publication", R5_peer_review = "Peer-review problems",
             R6_paper_mill = "Paper mill or systematic manipulation",
             R7_honest_error = "Honest error", R8_authorship_ethics = "Authorship or ethics",
             R9_other = "Other, incl. a violated policy")
# The papers behind the shares: the clarity table counts each paper with a
# classified notice once (back-calculating from a rounded share gave 52,988).
n_papers <- sum(rd("notice-classification-clarity-overall.csv")$Freq)
NOTICE_SOURCE <- paste("Source: retraction-notice text from Stage 2; reasons read by a local model",
                       "(Qwen3 14B) under the labelling protocol, checked against seven student labellers.")
hbar(file.path(OUT, "notice-reasons.png"), REASONS[r$reason], r$share_of_papers,
     "What retraction notices give as the reason",
     sprintf(paste("Share of %s retracted papers whose notice states each reason. A notice can",
                   "state several, so the bars sum to more than 100%%."), comma(n_papers)),
     NOTICE_SOURCE)

# 5. No reason at all, by publisher
np <- rd("notice-classification-by-publisher.csv")
np <- np[order(-np$papers), ][seq_len(min(15, nrow(np))), ]
hbar(file.path(OUT, "notice-no-reason-by-publisher.png"),
     sprintf("%s (%s)", short_name(np$value), comma(np$papers)), np$R0_no_reason,
     "Retraction notices that give no reason at all, by publisher",
     paste("Share of each publisher's retracted papers whose notice states no reason; papers in",
           "brackets. The 15 publishers with the most retracted papers. A notice citing only a",
           "violated policy states a reason (a vague one) and is not counted here."),
     NOTICE_SOURCE)

cat("written:", paste(list.files(OUT, "[.]png$"), collapse = ", "), "\n")
