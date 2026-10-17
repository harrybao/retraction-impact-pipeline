# Is the OpenAlex key readable, and does OpenAlex accept it? Prints the
# allowance headers, never the key.
#
#   Rscript analysis/check_openalex_key.R

source("R/python.R")   # polite_contact()

k <- Sys.getenv("OPENALEX_API_KEY")
cat("R sees OPENALEX_API_KEY:", nzchar(k), "\n")
req <- httr2::request("https://api.openalex.org/works")
req <- httr2::req_url_query(req, `per-page` = 1, select = "id",
                            mailto = polite_contact())
if (nzchar(k)) req <- httr2::req_url_query(req, api_key = k)
req <- httr2::req_error(req, is_error = function(r) FALSE)
resp <- httr2::req_perform(req)
cat("status:", httr2::resp_status(resp), "\n")
h <- httr2::resp_headers(resp)
for (n in c("x-ratelimit-limit-usd", "x-ratelimit-remaining-usd", "x-ratelimit-limit",
            "x-ratelimit-remaining")) {
  v <- h[[n]]
  cat(n, ":", if (is.null(v)) "-" else v, "\n")
}
