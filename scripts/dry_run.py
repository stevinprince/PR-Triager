#!/usr/bin/env python3
"""
scripts/dry_run.py

Local dry-run: fetch a real PR, ask Jev, print what labels *would* be applied.
Does not create labels, apply labels, or post PR comments.

Usage:
    python scripts/dry_run.py https://github.com/owner/repo/pull/42
    python scripts/dry_run.py owner/repo#42

Or set DRY_RUN_PR in .env and omit the argument:
    python scripts/dry_run.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow `python scripts/dry_run.py` from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from triager.fetch_pr import fetch_pr  # noqa: E402
from triager.jev_client import ask_jev  # noqa: E402
from triager.thresholds import JEV_QUESTIONS, Decision, evaluate_response  # noqa: E402

# ── PR ref parsing ────────────────────────────────────────────────────────────

_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)
_SHORT_RE = re.compile(
    r"^(?P<owner>[^/]+)/(?P<repo>[^#]+)#(?P<number>\d+)$",
)


def parse_pr_ref(ref: str) -> tuple[str, str, int]:
    """Accept a PR URL or OWNER/REPO#NUMBER."""
    ref = ref.strip()
    for pattern in (_URL_RE, _SHORT_RE):
        m = pattern.match(ref)
        if m:
            return m.group("owner"), m.group("repo"), int(m.group("number"))
    sys.exit(
        f"Could not parse PR reference: {ref!r}\n"
        "Expected a URL like https://github.com/owner/repo/pull/42\n"
        "or short form owner/repo#42"
    )


# ── Pretty output ─────────────────────────────────────────────────────────────

def _decision_line(d: Decision) -> str:
    conf = f"{d.confidence:.2f}" if d.confidence is not None else "n/a"
    suggested = d.suggested

    if d.category == "needs-tests":
        if d.label:
            outcome = "would apply label needs-tests"
        else:
            outcome = "would remove label needs-tests (if bot-applied)"
    elif d.auto_apply and d.label:
        outcome = f"would apply label {d.label}"
    elif d.auto_apply:
        outcome = "would skip (unrecognised answer)"
    else:
        outcome = (
            f"would comment only (below threshold) — leans {d.label or suggested}"
        )

    mark = "OK" if d.auto_apply and (d.label or d.category == "needs-tests") else ".."
    # needs_tests with label=None and auto_apply=True is still an action (remove).
    if d.category == "needs-tests" and d.auto_apply:
        mark = "OK"

    return (
        f"  {d.question:<14} : {suggested:<14} "
        f"(confidence: {conf}) → [{mark}] {outcome}"
    )


def print_report(
    owner: str,
    repo: str,
    pr_number: int,
    title: str,
    files_changed: list[str],
    truncated: bool,
    decisions: list[Decision],
) -> None:
    bar = "─" * 62
    print()
    print(f"PR #{pr_number}: {title!r}")
    print(f"{owner}/{repo}  |  {len(files_changed)} file(s) changed", end="")
    if truncated:
        print("  |  diff truncated", end="")
    print()
    print(bar)
    for d in decisions:
        print(_decision_line(d))
    print(bar)
    print("Dry-run only — no labels or comments were written to GitHub.")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run PR Auto-Triager against a real pull request.",
    )
    parser.add_argument(
        "pr",
        nargs="?",
        default=None,
        help="PR URL or OWNER/REPO#NUMBER (or set DRY_RUN_PR)",
    )
    args = parser.parse_args()

    import os

    ref = args.pr or os.environ.get("DRY_RUN_PR", "").strip()
    if not ref:
        parser.error("Pass a PR reference or set DRY_RUN_PR in the environment / .env")

    owner, repo, pr_number = parse_pr_ref(ref)

    print(f"[dry-run] Fetching {owner}/{repo}#{pr_number} …")
    pr = fetch_pr(owner, repo, pr_number)

    state = {
        "pr_title": pr.title,
        "pr_description": pr.description,
        "diff": pr.diff,
        "files_changed": pr.files_changed,
    }

    print("[dry-run] Asking Jev …")
    response = ask_jev(state, JEV_QUESTIONS)
    decisions = evaluate_response(response)

    print_report(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        title=pr.title,
        files_changed=pr.files_changed,
        truncated=pr.truncated,
        decisions=decisions,
    )


if __name__ == "__main__":
    main()
