# PR Auto-Triager — Implementation Plan

## Overview

A GitHub Action that runs on every pull request, fetches the diff + metadata,
sends it to TypeSafe's Jev model via `typesafe-sdk`, and auto-applies GitHub
labels based on structured output — with configurable confidence thresholds to
decide when to label automatically vs. flag for human review.

---

## 1. Repository Structure

```
PR-Triager/
├── .github/
│   └── workflows/
│       └── pr-triager.yml          # GitHub Action definition
├── triager/
│   __init__.py
│   ├── fetch_pr.py                 # Fetch diff + metadata from GitHub API
│   ├── jev_client.py               # Thin wrapper around typesafe-sdk / OpenRouter fallback
│   ├── labels.py                   # Label taxonomy + application logic
│   └── thresholds.py               # Confidence threshold strategy
├── scripts/
│   └── dry_run.py                  # Local dry-run CLI (no push to Actions required)
├── .env.example                    # Documents expected env vars
├── requirements.txt
├── PLAN.md                         # This file
└── README.md
```

---

## 2. GitHub Action Trigger Design

```yaml
on:
  pull_request:
    types: [opened, synchronize]
```

- **`opened`** — fires when a PR is first created.
- **`synchronize`** — fires on every new push to the PR branch (re-evaluates as
  the PR evolves).
- The workflow checks out the repo (needed to have the `triager/` package
  available) and runs `python scripts/dry_run.py` (or the equivalent inline
  invocation) with env vars injected from repository secrets.
- Required secrets: `TYPESAFE_API_KEY` (or `OPENROUTER_API_KEY` as fallback),
  `GITHUB_TOKEN` (automatically provided by Actions — no manual setup needed).

---

## 3. Diff Fetching & Truncation

**Source of truth:** The GitHub REST API endpoint
`GET /repos/{owner}/{repo}/pulls/{pull_number}` with
`Accept: application/vnd.github.v3.diff` returns the full unified diff.

**Truncation strategy (to stay within Jev's context limits):**

| Condition | Action |
|---|---|
| Diff ≤ 8 000 chars | Send in full |
| 8 000 < diff ≤ 32 000 chars | Keep first 8 000 chars of each changed file, mark truncated files with `[TRUNCATED]` |
| Diff > 32 000 chars | Send per-file summaries only: filename + `+N / -N` line counts, no raw hunks |

The `files_changed` list (filenames only) is always sent in full regardless of
diff size — it's a short list and gives Jev signal about which subsystems are
touched.

The truncation limit constants live in `triager/fetch_pr.py` and are easily
overridden via env vars (`MAX_DIFF_CHARS`, `MAX_FILE_CHARS`) so tuning doesn't
require code changes.

---

## 4. Label Taxonomy

Labels are created in the target repo on first run if they don't exist (idempotent).

### Risk level (`risk:*`)
| Label | Color | Meaning |
|---|---|---|
| `risk:low` | `#0e8a16` (green) | Routine change, safe to merge without deep review |
| `risk:medium` | `#e4a000` (amber) | Moderate blast radius; deserves a second pair of eyes |
| `risk:high` | `#b60205` (red) | Breaking / infra / auth / data-migration change |

### Size / complexity (`size:*`)
| Label | Color | Meaning |
|---|---|---|
| `size:trivial` | `#bfd4f2` (light blue) | Docs, typos, tiny one-liners |
| `size:moderate` | `#84b6eb` (medium blue) | A few functions or a small feature |
| `size:substantial` | `#0075ca` (blue) | Multi-file feature or refactor |
| `size:extensive` | `#0052cc` (dark blue) | Large-scale change, consider splitting |

### Test coverage (`needs-tests`)
| Label | Color | Meaning |
|---|---|---|
| `needs-tests` | `#e11d48` (rose) | Logic was modified but test coverage is missing or thin |

---

## 5. Jev Questions & Mapping to Labels

Three atomic Jev questions (one per label category):

```python
questions = {
    "risk_level": Choice(
        instructions="How risky is this change to merge into the main branch?",
        criteria={
            "low":    "Purely additive or cosmetic; no existing behaviour changes.",
            "medium": "Modifies existing behaviour but has limited blast radius.",
            "high":   "Touches auth, infra, data migrations, public APIs, or has wide blast radius.",
        },
    ),
    "complexity": Score(
        instructions="How complex is this change to review?",
        criteria=["trivial", "moderate", "substantial", "extensive"],
    ),
    "needs_tests": Noul(
        instructions=(
            "Does this PR modify application logic (not just docs, configs, "
            "or dependency bumps) in a way that should have test coverage?"
        ),
    ),
}
```

### Mapping response → labels

```
risk_level.choice           → risk:{low|medium|high}
complexity.score (0–3 idx)  → size:{trivial|moderate|substantial|extensive}
needs_tests.noul == True    → needs-tests label applied
needs_tests.noul == False   → needs-tests label removed (if previously set)
```

---

## 6. Confidence Threshold Strategy

The guiding principle: **high confidence → act automatically; low confidence →
add a PR comment explaining why we're unsure and leave it for a human.**

| Metric | Auto-apply threshold | Comment-only threshold |
|---|---|---|
| `risk_level.confidence` | ≥ 0.75 | < 0.75 |
| `complexity.confidence` | ≥ 0.70 | < 0.70 |
| `needs_tests.noul` | no confidence field — always act | — |

When a label is *not* auto-applied, the bot posts a PR comment like:

> 🤖 **PR Triager** — Low confidence on `risk_level`
> (confidence: 0.61, below threshold 0.75). Jev leans `medium` but a human
> should confirm. Apply manually: `risk:low` / `risk:medium` / `risk:high`.

Thresholds are env-var configurable (`RISK_CONFIDENCE_THRESHOLD`,
`COMPLEXITY_CONFIDENCE_THRESHOLD`) so they can be tuned per-repo without
code changes.

---

## 7. API Key Fallback Logic

```python
import os
from typesafe_sdk import TypeSafeClient
from openai import OpenAI  # fallback

if os.getenv("TYPESAFE_API_KEY"):
    client = TypeSafeClient()        # reads TYPESAFE_API_KEY automatically
elif os.getenv("OPENROUTER_API_KEY"):
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    # Wrap with the same call signature via a thin adapter
    MODEL = "typesafe/jev-1.13"
else:
    raise EnvironmentError("Set TYPESAFE_API_KEY or OPENROUTER_API_KEY")
```

The thin adapter ensures the rest of the codebase calls a single `ask_jev(state, questions)` function regardless of which backend is in use.

---

## 8. Local Dry-Run

`scripts/dry_run.py` is a standalone CLI that:

1. Accepts a PR URL or `OWNER/REPO#NUMBER` as a positional argument.
2. Fetches the diff + metadata using the GitHub REST API (respects
   `GITHUB_TOKEN` from env; falls back to unauthenticated for public repos).
3. Runs the full Jev pipeline.
4. Prints the structured response + which labels would be applied / skipped, in
   a readable table — **does not touch GitHub labels**.

```
$ TYPESAFE_API_KEY=... GITHUB_TOKEN=... python scripts/dry_run.py \
    https://github.com/owner/repo/pull/42

PR #42: "Add user auth module"
──────────────────────────────────────────────────────
risk_level   : high   (confidence: 0.91) → ✅ would apply label risk:high
complexity   : substantial (confidence: 0.83) → ✅ would apply label size:substantial
needs_tests  : True                          → ✅ would apply label needs-tests
```

---

## 9. Numbered Implementation Steps

| # | Step | Files touched |
|---|---|---|
| 1 | **Scaffold** — repo layout, `requirements.txt`, `.env.example`, empty `__init__.py` stubs | `requirements.txt`, `.env.example`, `triager/__init__.py` (empty), `triager/fetch_pr.py` (stub), `triager/jev_client.py` (stub), `triager/labels.py` (stub), `triager/thresholds.py` (stub) |
| 2 | **GitHub Action YAML + PR event payload parsing** — `pr-triager.yml` with the correct trigger, env injection, and a `main` entrypoint call | `.github/workflows/pr-triager.yml` |
| 3 | **Jev client wrapper** — `jev_client.py` with `TypeSafeClient` + OpenRouter fallback, `ask_jev()` function, model routing | `triager/jev_client.py` |
| 4 | **Diff fetching module** — `fetch_pr.py` with REST API calls, truncation logic, `files_changed` list | `triager/fetch_pr.py` |
| 5 | **Label application + confidence thresholds** — `labels.py` (taxonomy + idempotent create), `thresholds.py` (apply-vs-comment logic), `__main__.py` entry point that wires it all together | `triager/labels.py`, `triager/thresholds.py`, `triager/__main__.py` |
| 6 | **Dry-run CLI** — `scripts/dry_run.py`, pretty-printed output, no side effects | `scripts/dry_run.py` |
| 7 | **README** — setup instructions, secrets config, local dry-run usage, label descriptions | `README.md` |

---

## Decisions / Open Questions (resolve before or during implementation)

- [ ] **PyGithub vs raw `requests`?** — Plan currently uses raw `requests` for
      the diff endpoint (PyGithub doesn't expose the raw diff content-type
      directly) and PyGithub for label management. We can go all-`requests` if
      you prefer fewer dependencies.
- [ ] **PR comment bot identity** — comments will be posted as the
      `GITHUB_TOKEN` actor (usually `github-actions[bot]`). Fine?
- [ ] **Re-run idempotency** — on `synchronize`, old triager labels will be
      removed and re-evaluated. Is that the desired behaviour, or should
      existing human-set labels be preserved?
