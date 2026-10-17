# Discard the cached results of groups matched on a notice type, and try the
# corrected rule on one small such group (a handful of OpenAlex calls).
#
#   Rscript analysis/control_fix_notice_types.R

source("R/python.R"); source("R/control_group.R")
dir <- file.path("data", "controls")
groups <- utils::read.csv(file.path(dir, "groups.csv"), colClasses = "character")
groups[groups == "NA"] <- NA
papers <- utils::read.csv(file.path(dir, "papers.csv"), colClasses = "character")
f <- file.path(dir, "shard_1", "cache", paste0(gsub("[^A-Za-z0-9]+", "_", groups$group_id), ".rds"))
bad <- groups$type %in% NOTICE_TYPES & file.exists(f)
unlink(f[bad])
cat("discarded cached notice-type groups:", sum(bad), "\n")

g <- groups[groups$type == "retraction" & !is.na(groups$source_id) & groups$n_papers == "1", ][1, ]
cat("trial group:", g$group_id, "\n")
res <- match_group(g, papers[papers$group_id == g$group_id, ], readLines(file.path(dir, "rw_dois.txt")))
print(res[c("retracted_doi", "control_doi", "match_level", "control_year", "control_type")], right = FALSE)
cat("calls:", oa_spent$calls, " remaining today:", oa_spent$remaining, "\n")
