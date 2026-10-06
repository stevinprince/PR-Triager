"""
triager/jev_client.py

Thin wrapper around the TypeSafe SDK (primary) and the OpenRouter
OpenAI-compatible endpoint (fallback).

Public surface — the only function callers should ever import:

    from triager.jev_client import ask_jev

    response = ask_jev(state, questions)
    response.choices["risk_level"].choice
    response.choices["risk_level"].confidence
    response.scores["complexity"].score
    response.scores["complexity"].confidence
    response.nouls["needs_tests"].noul
"""

from __future__ import annotations

import os
from typing import Any

# ── TypeSafe SDK types (always imported for type hints) ───────────────────────
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient  # noqa: F401

# Re-export so callers can do `from triager.jev_client import Choice, Noul, Score`
__all__ = ["ask_jev", "Choice", "Noul", "Score"]

# ── Constants ─────────────────────────────────────────────────────────────────
_TYPESAFE_MODEL = "jev-1.13.0"
_OPENROUTER_MODEL = "typesafe/jev-1.13"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ── Backend detection (runs once at import time) ───────────────────────────────

def _build_client() -> tuple[Any, str]:
    """
    Return (client, backend) where backend is "typesafe" or "openrouter".

    Priority:
      1. TYPESAFE_API_KEY  → native TypeSafeClient
      2. OPENROUTER_API_KEY → OpenAI-compat client pointed at OpenRouter
      3. Neither set       → raise EnvironmentError with clear instructions
    """
    typesafe_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    if typesafe_key:
        # TypeSafeClient reads TYPESAFE_API_KEY from the environment itself.
        return TypeSafeClient(), "typesafe"

    if openrouter_key:
        try:
            from openai import OpenAI  # soft import — only needed on this path
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for the OpenRouter fallback. "
                "Run: pip install openai"
            ) from exc

        client = OpenAI(
            base_url=_OPENROUTER_BASE_URL,
            api_key=openrouter_key,
        )
        return client, "openrouter"

    raise EnvironmentError(
        "No API key found. Set one of:\n"
        "  TYPESAFE_API_KEY   — TypeSafe direct API (primary)\n"
        "  OPENROUTER_API_KEY — OpenRouter fallback (model: typesafe/jev-1.13)\n"
        "See .env.example for details."
    )


_client, _backend = _build_client()


# ── OpenRouter adapter ────────────────────────────────────────────────────────

class _OpenRouterResponse:
    """
    Wraps an OpenRouter system_one response to match the TypeSafe SDK shape:
      .choices[key].choice / .confidence
      .scores[key].score   / .confidence
      .nouls[key].noul
    """

    class _Choice:
        def __init__(self, choice: str, confidence: float) -> None:
            self.choice = choice
            self.confidence = confidence

    class _Score:
        def __init__(self, score: str, confidence: float) -> None:
            self.score = score
            self.confidence = confidence

    class _Noul:
        def __init__(self, noul: bool) -> None:
            self.noul = noul

    def __init__(self, raw: dict[str, Any], questions: dict[str, Any]) -> None:
        self.choices: dict[str, _OpenRouterResponse._Choice] = {}
        self.scores: dict[str, _OpenRouterResponse._Score] = {}
        self.nouls: dict[str, _OpenRouterResponse._Noul] = {}

        for key, question in questions.items():
            answer = raw.get(key, {})
            if isinstance(question, Choice):
                self.choices[key] = self._Choice(
                    choice=answer.get("choice", ""),
                    confidence=float(answer.get("confidence", 0.0)),
                )
            elif isinstance(question, Score):
                self.scores[key] = self._Score(
                    score=answer.get("score", ""),
                    confidence=float(answer.get("confidence", 0.0)),
                )
            elif isinstance(question, Noul):
                self.nouls[key] = self._Noul(
                    noul=bool(answer.get("noul", False)),
                )


def _ask_via_openrouter(
    state: dict[str, Any],
    questions: dict[str, Any],
) -> _OpenRouterResponse:
    """
    Call Jev through the OpenRouter OpenAI-compatible endpoint.
    The model accepts a structured system_one payload as a JSON-encoded
    user message and returns a JSON object.
    """
    import json

    # Serialise questions into a JSON-friendly form for the prompt.
    serialised_questions: dict[str, Any] = {}
    for key, q in questions.items():
        if isinstance(q, Choice):
            serialised_questions[key] = {
                "type": "choice",
                "instructions": q.instructions,
                "criteria": q.criteria,
            }
        elif isinstance(q, Score):
            serialised_questions[key] = {
                "type": "score",
                "instructions": q.instructions,
                "criteria": q.criteria,
            }
        elif isinstance(q, Noul):
            serialised_questions[key] = {
                "type": "noul",
                "instructions": q.instructions,
            }

    payload = json.dumps({"state": state, "questions": serialised_questions})

    completion = _client.chat.completions.create(
        model=_OPENROUTER_MODEL,
        messages=[{"role": "user", "content": payload}],
        response_format={"type": "json_object"},
    )

    raw_text = completion.choices[0].message.content or "{}"
    raw: dict[str, Any] = json.loads(raw_text)
    return _OpenRouterResponse(raw, questions)


# ── Public API ────────────────────────────────────────────────────────────────

def ask_jev(
    state: dict[str, Any],
    questions: dict[str, Any],
) -> Any:
    """
    Send *state* and *questions* to Jev and return a response object.

    When using TypeSafe directly the response is the native SDK object.
    When using OpenRouter it is an _OpenRouterResponse that matches the
    same attribute shape (.choices / .scores / .nouls).

    Args:
        state:     Dict of PR context (title, description, diff, files_changed).
        questions: Dict of Choice / Score / Noul instances.

    Returns:
        Response object with .choices, .scores, .nouls dicts.
    """
    if _backend == "typesafe":
        return _client.system_one(
            state=state,
            questions=questions,
            model=_TYPESAFE_MODEL,
        )

    # openrouter path
    return _ask_via_openrouter(state, questions)
