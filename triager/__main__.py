"""
triager/__main__.py

Entry point: `python -m triager`

Pipeline:
  1. Parse the GitHub Actions event payload → owner, repo, PR number
  2. Fetch PR metadata + diff  (fetch_pr)
  3. Ask Jev                   (jev_client)
  4. Evaluate confidence       (thresholds)
  5. Apply labels / comment    (labels)
"""

from __future__ import annotations

import json
import os
import sys

from triager.fetch_pr import fetch_pr
from triager.jev_client import ask_jev
from triager.labels import apply_decisions, ensure_labels
from triager.thresholds import JEV_QUESTIONS, evaluate_response


# ── Event payload parsing ─────────────────────────────────────────────────────

def parse_event() -> tuple[str, str, int]:
    """
    Read the GitHub Actions event JSON at $GITHUB_EVENT_PATH and return
    (owner, repo, pull_number).
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

    repo_full = event["repository"]["full_name"]  # e.g. "octocat/Hello-World"
    owner, repo = repo_full.split("/", 1)
    pr_number: int = pull_request["number"]

    return owner, repo, pr_number


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_triage(owner: str, repo: str, pr_number: int) -> None:
    """Fetch → ask Jev → decide → label. Shared with the dry-run CLI later."""
    print(f"[triager] Fetching {owner}/{repo}#{pr_number} …")
    pr = fetch_pr(owner, repo, pr_number)
    if pr.truncated:
        print("[triager] Diff was truncated before sending to Jev")

    state = {
        "pr_title": pr.title,
        "pr_description": pr.description,
        "diff": pr.diff,
        "files_changed": pr.files_changed,
    }

    print("[triager] Asking Jev …")
    response = ask_jev(state, JEV_QUESTIONS)
    decisions = evaluate_response(response)

    for d in decisions:
        action = "auto-apply" if d.auto_apply else "comment-only"
        print(
            f"[triager] {d.question}: suggested={d.suggested!r} "
            f"label={d.label!r} confidence={d.confidence} → {action}"
        )

    print("[triager] Ensuring taxonomy labels exist …")
    ensure_labels(owner, repo)

    print("[triager] Applying decisions …")
    apply_decisions(owner, repo, pr_number, decisions)
    print("[triager] Done.")


def main() -> None:
    owner, repo, pr_number = parse_event()
    print(f"[triager] Parsed event: {owner}/{repo}#{pr_number}")
    run_triage(owner, repo, pr_number)


if __name__ == "__main__":
    main()
