"""
triager/labels.py

Label taxonomy, idempotent creation, and apply/remove logic that preserves
human-applied labels.

Public surface:

    from triager.labels import ensure_labels, apply_decisions

    ensure_labels(owner, repo)
    apply_decisions(owner, repo, pr_number, decisions)
"""

from __future__ import annotations

import os
from typing import Iterable

from github import Github, GithubException
from github.Repository import Repository

from triager.thresholds import (
    NEEDS_TESTS_LABEL,
    RISK_LABELS,
    SIZE_LABELS,
    Decision,
    comment_for,
)

# ── Taxonomy ──────────────────────────────────────────────────────────────────

# name → (color without '#', description)
TAXONOMY: dict[str, tuple[str, str]] = {
    "risk:low": (
        "0e8a16",
        "Routine change, safe to merge without deep review",
    ),
    "risk:medium": (
        "e4a000",
        "Moderate blast radius; deserves a second pair of eyes",
    ),
    "risk:high": (
        "b60205",
        "Breaking / infra / auth / data-migration change",
    ),
    "size:trivial": (
        "bfd4f2",
        "Docs, typos, tiny one-liners",
    ),
    "size:moderate": (
        "84b6eb",
        "A few functions or a small feature",
    ),
    "size:substantial": (
        "0075ca",
        "Multi-file feature or refactor",
    ),
    "size:extensive": (
        "0052cc",
        "Large-scale change, consider splitting",
    ),
    "needs-tests": (
        "e11d48",
        "Logic was modified but test coverage is missing or thin",
    ),
}

MANAGED_LABELS: frozenset[str] = frozenset(TAXONOMY)
CATEGORY_LABELS: dict[str, frozenset[str]] = {
    "risk": frozenset(RISK_LABELS),
    "size": frozenset(SIZE_LABELS),
    "needs-tests": frozenset({NEEDS_TESTS_LABEL}),
}

# Actors we treat as "the triager bot" (safe to overwrite their labels).
_BOT_LOGINS = frozenset(
    {
        "github-actions[bot]",
        "github-actions",
    }
)


# ── GitHub client helpers ─────────────────────────────────────────────────────

def _github() -> Github:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise EnvironmentError(
            "GITHUB_TOKEN is required to create/apply labels. "
            "In Actions it is provided automatically; locally set it in .env."
        )
    return Github(token)


def _repo(owner: str, repo: str) -> Repository:
    return _github().get_repo(f"{owner}/{repo}")


def _bot_logins(gh: Github) -> frozenset[str]:
    """Include the authenticated user so PATs used locally count as the bot."""
    logins = set(_BOT_LOGINS)
    try:
        logins.add(gh.get_user().login)
    except GithubException:
        pass
    return frozenset(logins)


# ── Label provenance (human vs bot) ───────────────────────────────────────────

def _label_actors(repo: Repository, pr_number: int) -> dict[str, str]:
    """
    Return {label_name: last_actor_login} for labeled events on this PR.

    The most recent 'labeled' event wins for each label name.
    """
    issue = repo.get_issue(pr_number)
    actors: dict[str, str] = {}
    for event in issue.get_events():
        if event.event != "labeled" or event.label is None:
            continue
        login = event.actor.login if event.actor else ""
        actors[event.label.name] = login
    return actors


def _human_owns_category(
    current: Iterable[str],
    category_labels: frozenset[str],
    actors: dict[str, str],
    bot_logins: frozenset[str],
) -> bool:
    """True if any label in this category on the PR was last applied by a human."""
    for name in current:
        if name not in category_labels:
            continue
        actor = actors.get(name, "")
        # Unknown actor (no event history) → treat as human to be safe.
        if not actor or actor not in bot_logins:
            return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_labels(owner: str, repo_name: str) -> None:
    """Idempotently create every taxonomy label in the target repo."""
    repo = _repo(owner, repo_name)
    existing = {label.name for label in repo.get_labels()}

    for name, (color, description) in TAXONOMY.items():
        if name in existing:
            continue
        try:
            repo.create_label(name=name, color=color, description=description)
            print(f"[triager] Created label: {name}")
        except GithubException as exc:
            # Race or already exists — safe to ignore.
            if exc.status != 422:
                raise
            print(f"[triager] Label already exists (race): {name}")


def apply_decisions(
    owner: str,
    repo_name: str,
    pr_number: int,
    decisions: list[Decision],
) -> None:
    """
    Apply auto-approved decisions; post comments for low-confidence ones.

    Human-applied labels in a category are never removed. If a human already
    set a label in that category, we skip auto-apply for it and comment with
    Jev's suggestion instead.
    """
    gh = _github()
    repo = gh.get_repo(f"{owner}/{repo_name}")
    issue = repo.get_issue(pr_number)
    bot_logins = _bot_logins(gh)
    actors = _label_actors(repo, pr_number)
    current = {label.name for label in issue.get_labels()}

    for decision in decisions:
        category_set = CATEGORY_LABELS[decision.category]

        if not decision.auto_apply:
            body = comment_for(decision)
            if body:
                issue.create_comment(body)
                print(f"[triager] Low confidence on {decision.question} — commented")
            continue

        # Preserve human-applied labels in this category.
        if _human_owns_category(current, category_set, actors, bot_logins):
            issue.create_comment(
                f"**PR Triager** — Skipping `{decision.question}`: a human "
                f"already set a label in this category. "
                f"Jev suggests `{decision.suggested}` "
                f"→ `{decision.label or '(remove needs-tests)'}`."
            )
            print(
                f"[triager] Preserved human label(s) for {decision.category}; skipped"
            )
            continue

        # Remove only bot-applied labels in this exclusive category.
        for name in list(current & category_set):
            actor = actors.get(name, "")
            if actor in bot_logins:
                issue.remove_from_labels(name)
                current.discard(name)
                print(f"[triager] Removed bot label: {name}")

        # needs_tests False → label already removed above (or never present).
        if decision.label is None:
            continue

        if decision.label not in current:
            issue.add_to_labels(decision.label)
            current.add(decision.label)
            actors[decision.label] = next(iter(bot_logins))  # mark as ours
            print(f"[triager] Applied label: {decision.label}")
        else:
            print(f"[triager] Label already present: {decision.label}")
