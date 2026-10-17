# What the first day of the control-group run produced, from the local cache
# only (no OpenAlex calls).
#
#   Rscript analysis/control_day1_check.R

dir <- file.path("data", "controls")
groups <- utils::read.csv(file.path(dir, "groups.csv"), colClasses = "character")
papers <- utils::read.csv(file.path(dir, "papers.csv"), colClasses = "character")
status <- utils::read.csv(file.path(dir, "status_shard_1.csv"), colClasses = "character")

cat("OpenAlex types of the retracted papers:\n")
print(sort(table(papers$type), decreasing = TRUE))

done <- status$group_id[status$status == "done"]
cache <- file.path(dir, "shard_1", "cache", paste0(gsub("[^A-Za-z0-9]+", "_", done), ".rds"))
ctl <- do.call(rbind, Filter(NROW, lapply(cache, readRDS)))
p_done <- papers[papers$group_id %in% done, ]
n <- table(factor(ctl$retracted_doi, levels = p_done$doi))
cat("\npapers in finished groups:", nrow(p_done), "\n")
cat("controls per paper:\n"); print(table(pmin(as.integer(n), 3)))
cat("\ncontrols by match level:\n"); print(table(ctl$match_level))

short <- p_done[as.integer(n[p_done$doi]) < 3, ]
cat("\npapers with fewer than 3 controls, by type:\n"); print(sort(table(short$type), decreasing = TRUE))
cat("by source present:\n"); print(table(!is.na(short$source_id) & short$source_id != "NA"))

unfinished <- groups[!groups$group_id %in% done, ]
cat("\nunfinished groups by type:\n"); print(sort(table(unfinished$type), decreasing = TRUE))
cat("unfinished papers:", sum(as.integer(unfinished$n_papers)), "\n")
