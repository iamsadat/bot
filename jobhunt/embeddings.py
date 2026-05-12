"""Embedding and vector similarity for JD text and user skills.

Deferred to Phase 2. For now, placeholders to structure the API.
Production: Use Anthropic's embedding API or pgvector for similarity search.
"""

from __future__ import annotations

from typing import Any


def embed_jd_text(text: str) -> list[float]:
    """Generate embedding vector for job description text.

    Phase 2: Call Anthropic embed-api or similar.
    Returns 1024-dim dense vector for JD similarity matching.
    """
    # Placeholder: return zero vector
    return [0.0] * 1024


def embed_user_skills(skills: list[str]) -> list[float]:
    """Generate embedding for user's skill profile.

    Combines skill names into a unified vector for role matching.
    Phase 2: Anthropic embedding API.
    """
    # Placeholder
    return [0.0] * 1024


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (0..1)."""
    if not a or not b:
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)
