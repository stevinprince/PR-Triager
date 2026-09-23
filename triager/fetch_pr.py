# triager/fetch_pr.py
#
# Responsibilities:
#   - Fetch PR metadata (title, body, changed file list) via GitHub REST API
#   - Fetch the unified diff via the vnd.github.v3.diff content-type
#   - Truncate the diff if it exceeds configured size limits
#
# Implemented in Step 4.
