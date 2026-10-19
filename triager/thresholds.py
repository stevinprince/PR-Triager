"""
triager/thresholds.py

Map Jev answers → label decisions using confidence thresholds.

Public surface:

    from triager.thresholds import JEV_QUESTIONS, evaluate_response

    decisions = evaluate_response(response)
    # each Decision: .label, .auto_apply, .comment_body, ...
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from triager.jev_client import Choice, Noul, Score

# ── Thresholds (env-overridable) ──────────────────────────────────────────────

RISK_CONFIDENCE_THRESHOLD = float(os.environ.get("RISK_CONFIDENCE_THRESHOLD", "0.75"))
COMPLEXITY_CONFIDENCE_THRESHOLD = float(
    os.environ.get("COMPLEXITY_CONFIDENCE_THRESHOLD", "0.70")
)

# ── Label name constants ──────────────────────────────────────────────────────

RISK_LABELS = ("risk:low", "risk:medium", "risk:high")
SIZE_LABELS = (
    "size:trivial",
    "size:moderate",
    "size:substantial",
    "size:extensive",
)
NEEDS_TESTS_LABEL = "needs-tests"

SIZE_CRITERIA = ("trivial", "moderate", "substantial", "extensive")

# ── Jev questions (atomic, one per label category) ────────────────────────────

JEV_QUESTIONS: dict[str, Any] = {
    "risk_level": Choice(
        instructions="How risky is this change to merge into the main branch?",
        criteria={
            "low": (
                "Purely additive or cosmetic; no existing behaviour changes."
            ),
            "medium": (
                "Modifies existing behaviour but has limited blast radius."
            ),
            "high": (
                "Touches auth, infra, data migrations, public APIs, "
                "or has wide blast radius."
            ),
        },
    ),
    "complexity": Score(
        instructions="How complex is this change to review?",
        criteria=list(SIZE_CRITERIA),
    ),
    "needs_tests": Noul(
        instructions=(
            "Does this PR modify application logic (not just docs, configs, "
            "or dependency bumps) in a way that should have test coverage?"
        ),
    ),
}


# ── Decision type ─────────────────────────────────────────────────────────────

@dataclass
class Decision:
    """One triage outcome ready for labels.apply_decisions()."""

    question: str
    category: str  # "risk" | "size" | "needs-tests"
    label: str | None  # label to add; None means remove needs-tests
    confidence: float | None
    auto_apply: bool
    suggested: str  # human-readable lean (e.g. "medium", "substantial", "True")
    alternatives: tuple[str, ...]  # labels a human can pick from


def _low_confidence_comment(d: Decision, threshold: float) -> str:
    conf = f"{d.confidence:.2f}" if d.confidence is not None else "n/a"
    alts = " / ".join(f"`{a}`" for a in d.alternatives)
    return (
        f"**PR Triager** — Low confidence on `{d.question}` "
        f"(confidence: {conf}, below threshold {threshold:.2f}). "
        f"Jev leans `{d.suggested}` but a human should confirm. "
        f"Apply manually: {alts}."
    )


def comment_for(decision: Decision) -> str | None:
    """Return a PR comment body when auto_apply is False; else None."""
    if decision.auto_apply:
        return None
    if decision.category == "risk":
        return _low_confidence_comment(decision, RISK_CONFIDENCE_THRESHOLD)
    if decision.category == "size":
        return _low_confidence_comment(decision, COMPLEXITY_CONFIDENCE_THRESHOLD)
    return None


# ── Mapping helpers ───────────────────────────────────────────────────────────

def _risk_label(choice: str) -> str | None:
    key = choice.strip().lower()
    if key in ("low", "medium", "high"):
        return f"risk:{key}"
    return None


def _size_label(score: Any) -> str | None:
    """Accept criteria string or 0–3 index from Jev."""
    if isinstance(score, (int, float)):
        idx = int(score)
        if 0 <= idx < len(SIZE_CRITERIA):
            return f"size:{SIZE_CRITERIA[idx]}"
        return None

    key = str(score).strip().lower()
    # Sometimes models return "size:moderate" already.
    if key.startswith("size:"):
        key = key.split(":", 1)[1]
    if key in SIZE_CRITERIA:
        return f"size:{key}"
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate_response(response: Any) -> list[Decision]:
    """
    Turn a Jev response into Decision objects.

    Auto-apply when confidence ≥ threshold (needs_tests always auto-applies).
    """
    decisions: list[Decision] = []

    # risk_level → risk:*
    risk = response.choices["risk_level"]
    risk_label = _risk_label(risk.choice)
    risk_conf = float(risk.confidence)
    decisions.append(
        Decision(
            question="risk_level",
            category="risk",
            label=risk_label,
            confidence=risk_conf,
            auto_apply=risk_label is not None
            and risk_conf >= RISK_CONFIDENCE_THRESHOLD,
            suggested=str(risk.choice),
            alternatives=RISK_LABELS,
        )
    )

    # complexity → size:*
    complexity = response.scores["complexity"]
    size_label = _size_label(complexity.score)
    size_conf = float(complexity.confidence)
    decisions.append(
        Decision(
            question="complexity",
            category="size",
            label=size_label,
            confidence=size_conf,
            auto_apply=size_label is not None
            and size_conf >= COMPLEXITY_CONFIDENCE_THRESHOLD,
            suggested=str(complexity.score),
            alternatives=SIZE_LABELS,
        )
    )

    # needs_tests → needs-tests (always act; no confidence field)
    needs = response.nouls["needs_tests"]
    wants_tests = bool(needs.noul)
    decisions.append(
        Decision(
            question="needs_tests",
            category="needs-tests",
            label=NEEDS_TESTS_LABEL if wants_tests else None,
            confidence=None,
            auto_apply=True,
            suggested=str(wants_tests),
            alternatives=(NEEDS_TESTS_LABEL,),
        )
    )

    return decisions
