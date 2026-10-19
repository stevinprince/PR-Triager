# PR Auto-Triager

A GitHub Action that runs on every pull request, sends the PR’s diff and
description to TypeSafe’s **Jev** model (typed System One decisions — not chat
text), and auto-applies labels from the structured output.

Human-applied labels in a category are never overwritten. Low-confidence
answers become a PR comment instead of a label.

---

## How it works

1. Trigger: `pull_request` → `opened` or `synchronize`
2. Fetch title, body, changed files, and a size-capped unified diff
3. Ask Jev three atomic questions: risk, complexity, needs-tests
4. Apply labels when confidence clears the threshold; otherwise comment

---

## Labels

| Label | Meaning |
|---|---|
| `risk:low` | Routine / cosmetic; safe to merge without deep review |
| `risk:medium` | Behaviour change with limited blast radius |
| `risk:high` | Auth, infra, migrations, public APIs, or wide blast radius |
| `size:trivial` | Docs, typos, tiny one-liners |
| `size:moderate` | A few functions or a small feature |
| `size:substantial` | Multi-file feature or refactor |
| `size:extensive` | Large-scale change — consider splitting |
| `needs-tests` | Logic changed in a way that should have test coverage |

Labels are created in the target repo on first run if missing.

**Human overrides:** If a person already set a label in a category (`risk:*`,
`size:*`, or `needs-tests`), the triager skips that category and comments with
Jev’s suggestion. Only labels previously applied by the bot are replaced on
re-runs.

---

## Confidence thresholds

| Question | Auto-apply when | Default |
|---|---|---|
| `risk_level` | confidence ≥ threshold | `0.75` |
| `complexity` | confidence ≥ threshold | `0.70` |
| `needs_tests` | always (no confidence field) | — |

Override via env / Actions secrets-style vars:

- `RISK_CONFIDENCE_THRESHOLD`
- `COMPLEXITY_CONFIDENCE_THRESHOLD`

---

## Setup (GitHub Actions)

1. Copy this repo into your project (or add `triager/`, `scripts/`,
   `requirements.txt`, and `.github/workflows/pr-triager.yml`).
2. In the target repository: **Settings → Secrets and variables → Actions**,
   add **one** of:
   - `TYPESAFE_API_KEY` — TypeSafe direct API (preferred)
   - `OPENROUTER_API_KEY` — fallback; uses model `typesafe/jev-1.13`
3. `GITHUB_TOKEN` is provided automatically. The workflow already requests
   `pull-requests: write` and `issues: write` for labels and comments.
4. Open a PR — the **PR Auto-Triager** workflow runs on open and every push.

Optional workflow env overrides (uncomment in
`.github/workflows/pr-triager.yml`):

- `RISK_CONFIDENCE_THRESHOLD` / `COMPLEXITY_CONFIDENCE_THRESHOLD`
- `MAX_DIFF_CHARS` / `MAX_FILE_CHARS` / `HARD_DIFF_CHARS`

---

## Local setup & dry-run

Use the dry-run CLI to sanity-check Jev’s output against a real PR **without**
writing labels or comments.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set TYPESAFE_API_KEY or OPENROUTER_API_KEY
# Optional: GITHUB_TOKEN (higher rate limit; required for private repos)
```

```bash
python scripts/dry_run.py https://github.com/owner/repo/pull/42
# or
python scripts/dry_run.py owner/repo#42
# or set DRY_RUN_PR in .env and run:
python scripts/dry_run.py
```

Example output:

```
PR #42: 'Add user auth module'
owner/repo  |  8 file(s) changed
──────────────────────────────────────────────────────────────
  risk_level     : high           (confidence: 0.91) → [OK] would apply label risk:high
  complexity     : substantial    (confidence: 0.83) → [OK] would apply label size:substantial
  needs_tests    : True           (confidence: n/a) → [OK] would apply label needs-tests
──────────────────────────────────────────────────────────────
Dry-run only — no labels or comments were written to GitHub.
```

---

## Project layout

```
.github/workflows/pr-triager.yml   # Action trigger + env injection
triager/
  fetch_pr.py                      # Diff + metadata + truncation
  jev_client.py                    # TypeSafe SDK / OpenRouter fallback
  thresholds.py                    # Questions + confidence → decisions
  labels.py                        # Taxonomy + apply (preserve humans)
  __main__.py                      # Actions entry: python -m triager
scripts/dry_run.py                 # Local dry-run CLI
.env.example                       # Documented environment variables
PLAN.md                            # Design notes
```

---

## Diff truncation

| Diff size | What Jev receives |
|---|---|
| ≤ 8 000 chars | Full unified diff |
| 8 000–32 000 | First 8 000 chars per file, then `[TRUNCATED]` |
| > 32 000 | Filename + `+N / -N` summaries only |

`files_changed` is always sent in full. Limits: `MAX_DIFF_CHARS`,
`MAX_FILE_CHARS`, `HARD_DIFF_CHARS`.

---

## Requirements

- Python 3.11+
- See `requirements.txt` (`typesafe-sdk`, `PyGithub`, `requests`, `openai`,
  `python-dotenv`)
