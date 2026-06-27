"""Tests for embedding and vector similarity.

``embed_jd_text`` / ``embed_user_skills`` use a deterministic, stdlib-only
hashing-trick bag-of-words embedding (see ``jobhunt/embeddings.py``): no
paid API, no network calls, fully reproducible across processes. Documents
that share vocabulary land closer together in cosine-similarity space than
unrelated documents.
"""

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
    assert all(isinstance(x, float) for x in embedding)


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


# ---------------------------------------------------------------------------
# Real embedding behaviour: hashing-trick bag-of-words
# ---------------------------------------------------------------------------


def test_similar_jd_text_scores_higher_than_unrelated():
    """Two JDs sharing most vocabulary should be far more similar than two
    JDs about unrelated roles."""
    backend_a = embed_jd_text("Senior Backend Engineer Python")
    backend_b = embed_jd_text("Senior Backend Developer Python")
    unrelated = embed_jd_text("Pastry Chef seeking baker for bakery")

    sim_similar = cosine_similarity(backend_a, backend_b)
    sim_unrelated = cosine_similarity(backend_a, unrelated)

    assert sim_similar > 0.5
    assert sim_unrelated < 0.3
    assert sim_similar > sim_unrelated


def test_embed_jd_text_is_deterministic_across_calls():
    """Calling embed_jd_text twice with the same input must produce an
    identical vector — this is the whole point of avoiding the salted
    builtin ``hash()`` in favour of a stable hash."""
    text = "Senior Backend Engineer Python"
    assert embed_jd_text(text) == embed_jd_text(text)


def test_embed_user_skills_is_deterministic_across_calls():
    skills = ["python", "redis", "kubernetes"]
    assert embed_user_skills(skills) == embed_user_skills(skills)


def test_embed_user_skills_overlap_gives_positive_similarity():
    """Skill lists sharing most tokens should show positive similarity."""
    skills_a = embed_user_skills(["python", "redis", "kubernetes"])
    skills_b = embed_user_skills(["python", "redis", "k8s"])

    sim = cosine_similarity(skills_a, skills_b)
    assert sim > 0.0


def test_jd_and_skills_share_vocabulary_show_positive_similarity():
    """A skills list and a JD that share vocabulary should show meaningfully
    positive similarity in the shared embedding space."""
    jd = embed_jd_text("Senior Backend Engineer, Python, Kubernetes, Redis")
    skills = embed_user_skills(["python", "redis", "kubernetes"])

    assert cosine_similarity(jd, skills) > 0.3


def test_embed_jd_text_empty_string_returns_zero_vector():
    """No tokens means no signal: the embedding degrades to the all-zero
    vector, so cosine_similarity safely returns 0.0 for it."""
    embedding = embed_jd_text("")
    assert embedding == [0.0] * 1024
    assert cosine_similarity(embedding, embed_jd_text("Senior Backend Engineer")) == 0.0


def test_embed_user_skills_empty_list_returns_zero_vector():
    embedding = embed_user_skills([])
    assert embedding == [0.0] * 1024
    assert cosine_similarity(embedding, embed_user_skills(["python"])) == 0.0
