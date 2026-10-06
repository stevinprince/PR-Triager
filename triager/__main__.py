"""
triager/__main__.py

Entry point: `python -m triager`

Responsibilities (wired together in Step 5):
  1. Parse the GitHub Actions event payload → extract owner, repo, PR number.
  2. Fetch PR metadata + diff  (fetch_pr)
  3. Ask Jev                   (jev_client)
  4. Apply labels / post comments based on confidence thresholds
     (labels + thresholds)
"""

from __future__ import annotations

import json
import os
import sys


# ── Step 2: Event payload parsing ────────────────────────────────────────────

def parse_event() -> tuple[str, str, int]:
    """
    Read the GitHub Actions event JSON at $GITHUB_EVENT_PATH and return
    (owner, repo, pull_number).

    Raises SystemExit with a clear message when called outside of a
    pull_request event or when the env var is missing.
    """
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        sys.exit(
            "GITHUB_EVENT_PATH is not set. "
            "This script must be run inside a GitHub Actions workflow, "
            "or use scripts/dry_run.py for local testing."
        )

    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)

    pull_request = event.get("pull_request")
    if not pull_request:
        sys.exit(
            f"Event payload has no 'pull_request' key. "
            f"Make sure the workflow trigger is 'on: pull_request'. "
            f"Event keys found: {list(event.keys())}"
        )

    repo_full = event["repository"]["full_name"]   # e.g. "octocat/Hello-World"
    owner, repo = repo_full.split("/", 1)
    pr_number: int = pull_request["number"]

    return owner, repo, pr_number


# ── Main (full wiring added in Step 5) ───────────────────────────────────────

def main() -> None:
    owner, repo, pr_number = parse_event()
    # Steps 3-5 will add: fetch diff → ask Jev → apply labels
    print(f"[triager] Parsed event: {owner}/{repo}#{pr_number}")
    print("[triager] Full pipeline not yet wired — see Step 5.")


if __name__ == "__main__":
    main()
