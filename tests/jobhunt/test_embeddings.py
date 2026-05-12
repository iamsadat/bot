"""Tests for embedding and vector similarity (Phase 2 deferred).

These are placeholders for the embedding API integration coming in Phase 2.
Current implementation returns zero vectors for all inputs.
"""

import pytest
from jobhunt.embeddings import embed_jd_text, embed_user_skills, cosine_similarity


def test_embed_jd_text_returns_vector():
    """Test that JD embedding returns a fixed-dimension vector."""
    embedding = embed_jd_text("Senior Backend Engineer")
    assert isinstance(embedding, list)
    assert len(embedding) == 1024
    assert all(isinstance(x, float) for x in embedding)


def test_embed_user_skills_returns_vector():
    """Test that skill embedding returns a fixed-dimension vector."""
    embedding = embed_user_skills(["python", "postgresql", "kubernetes"])
    assert isinstance(embedding, list)
    assert len(embedding) == 1024


def test_cosine_similarity_identical_vectors():
    """Test that identical vectors have similarity 1.0."""
    v = [1.0, 0.0, 0.0]
    sim = cosine_similarity(v, v)
    assert abs(sim - 1.0) < 0.0001


def test_cosine_similarity_orthogonal_vectors():
    """Test that orthogonal vectors have similarity ~0.0."""
    v1 = [1.0, 0.0]
    v2 = [0.0, 1.0]
    sim = cosine_similarity(v1, v2)
    assert abs(sim) < 0.0001


def test_cosine_similarity_parallel_vectors():
    """Test that parallel vectors have similarity ~1.0."""
    v1 = [1.0, 2.0, 3.0]
    v2 = [2.0, 4.0, 6.0]  # Same direction, 2x magnitude
    sim = cosine_similarity(v1, v2)
    assert abs(sim - 1.0) < 0.0001


def test_cosine_similarity_empty_vectors():
    """Test that empty vectors return 0.0 similarity."""
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0], []) == 0.0
    assert cosine_similarity([], []) == 0.0
