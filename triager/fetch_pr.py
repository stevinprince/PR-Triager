"""
triager/fetch_pr.py

Fetch PR metadata + unified diff from the GitHub REST API, then truncate
the diff so it fits within Jev's context budget.

Public surface:

    from triager.fetch_pr import fetch_pr

    pr = fetch_pr(owner, repo, pr_number)
    # pr.title, pr.description, pr.diff, pr.files_changed, pr.truncated
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import requests

# ── Tunable limits (overridable via env) ──────────────────────────────────────

MAX_DIFF_CHARS = int(os.environ.get("MAX_DIFF_CHARS", "8000"))
MAX_FILE_CHARS = int(os.environ.get("MAX_FILE_CHARS", "8000"))
# Above this absolute size we drop hunks entirely and send summaries only.
HARD_DIFF_CHARS = int(os.environ.get("HARD_DIFF_CHARS", "32000"))

_API_BASE = "https://api.github.com"
_DIFF_ACCEPT = "application/vnd.github.v3.diff"
_JSON_ACCEPT = "application/vnd.github+json"

# Split a unified diff into per-file sections (each starts with "diff --git ").
_FILE_SPLIT_RE = re.compile(r"(?=^diff --git )", re.MULTILINE)
# Extract +N / -N counts from a "diff --git" header block's @@ hunks.
_HUNK_STATS_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", re.MULTILINE)
_FILENAME_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)


@dataclass
class PRData:
    """Normalised PR payload ready to hand to ask_jev()."""

    owner: str
    repo: str
    number: int
    title: str
    description: str
    diff: str
    files_changed: list[str] = field(default_factory=list)
    truncated: bool = False


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _headers(accept: str = _JSON_ACCEPT) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(url: str, *, accept: str = _JSON_ACCEPT) -> requests.Response:
    resp = requests.get(url, headers=_headers(accept), timeout=60)
    if resp.status_code == 404:
        raise LookupError(f"GitHub resource not found: {url}")
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        raise RuntimeError(
            "GitHub API rate limit hit. Set GITHUB_TOKEN for a higher quota."
        )
    resp.raise_for_status()
    return resp


# ── Diff truncation ───────────────────────────────────────────────────────────

def _file_stats(section: str) -> tuple[str, int, int]:
    """Return (filename, additions, deletions) for one diff --git section."""
    name_match = _FILENAME_RE.search(section)
    filename = name_match.group(2) if name_match else "<unknown>"

    additions = deletions = 0
    for m in _HUNK_STATS_RE.finditer(section):
        # git unified diff: -,N is deletions from old; +,N is additions to new.
        # Missing count means 1 line (git default).
        old_count = int(m.group(1)) if m.group(1) is not None else 1
        new_count = int(m.group(2)) if m.group(2) is not None else 1
        deletions += old_count
        additions += new_count
    return filename, additions, deletions


def _split_diff_sections(diff: str) -> list[str]:
    sections = [s for s in _FILE_SPLIT_RE.split(diff) if s.strip()]
    return sections


def truncate_diff(diff: str) -> tuple[str, bool]:
    """
    Apply the three-tier truncation strategy from PLAN.md.

    Returns (possibly_truncated_diff, was_truncated).
    """
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False

    sections = _split_diff_sections(diff)

    # Hard tier: summary only (filename + line counts, no hunks).
    if len(diff) > HARD_DIFF_CHARS:
        lines: list[str] = [
            f"[DIFF SUMMARY ONLY — original diff was {len(diff)} chars, "
            f"exceeded HARD_DIFF_CHARS={HARD_DIFF_CHARS}]",
        ]
        for section in sections:
            filename, adds, dels = _file_stats(section)
            lines.append(f"  {filename}: +{adds} / -{dels}")
        return "\n".join(lines), True

    # Mid tier: keep first MAX_FILE_CHARS of each file section.
    parts: list[str] = []
    for section in sections:
        if len(section) <= MAX_FILE_CHARS:
            parts.append(section)
        else:
            parts.append(section[:MAX_FILE_CHARS].rstrip() + "\n[TRUNCATED]\n")
    return "".join(parts), True


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_pr(owner: str, repo: str, pr_number: int) -> PRData:
    """
    Fetch title, description, changed-file list, and a size-capped unified
    diff for the given pull request.
    """
    base = f"{_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"

    meta = _get(base).json()
    title = meta.get("title") or ""
    description = meta.get("body") or ""

    # Paginate the files list — always send filenames in full to Jev.
    files_changed: list[str] = []
    page = 1
    while True:
        files_resp = _get(f"{base}/files?per_page=100&page={page}")
        batch = files_resp.json()
        if not batch:
            break
        files_changed.extend(f["filename"] for f in batch)
        if len(batch) < 100:
            break
        page += 1

    raw_diff = _get(base, accept=_DIFF_ACCEPT).text
    diff, truncated = truncate_diff(raw_diff)

    return PRData(
        owner=owner,
        repo=repo,
        number=pr_number,
        title=title,
        description=description,
        diff=diff,
        files_changed=files_changed,
        truncated=truncated,
    )
