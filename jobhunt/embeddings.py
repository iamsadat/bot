"""Embedding and vector similarity for JD text and user skills.

Fully offline, stdlib-only implementation using the "hashing trick"
(feature hashing), the same family of technique as scikit-learn's
``HashingVectorizer`` / ``FeatureHasher``. Each token is hashed with a
stable, process-independent hash (``hashlib.md5``, *not* the builtin
``hash()`` which is salted per-process via ``PYTHONHASHSEED`` and would
break reproducibility of persisted vectors and cross-process similarity
comparisons) into one of ``EMBEDDING_DIM`` buckets, with a deterministic
sign to reduce collision bias. The resulting bag-of-words vector is
L2-normalized so ``cosine_similarity`` behaves as expected.

This is a real, lightweight embedding: documents that share vocabulary
land closer together in cosine-similarity space than unrelated documents.
It is not a substitute for a learned semantic embedding model, but it
requires no network access, no paid API, and no third-party dependency,
so it works identically in CI and in fully sandboxed environments.
"""

from __future__ import annotations

import hashlib
import re

EMBEDDING_DIM = 1024

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Small, cheap stopword list to reduce noise from very common English words
# that carry little signal for short job-description-style text.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "for", "with", "on",
    "at", "by", "from", "is", "are", "be", "as", "it", "this", "that",
    "we", "you", "your", "our",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase and split ``text`` into word tokens, dropping stopwords."""
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _embed_tokens(tokens: list[str]) -> list[float]:
    """Hashing-trick bag-of-words embedding shared by both public functions.

    For each token occurrence, derive a deterministic bucket index and sign
    from an MD5 digest of the token, accumulate ``vec[bucket] += sign``, then
    L2-normalize. Returns the all-zero vector for no tokens (or when the
    accumulated vector happens to have zero norm), so ``cosine_similarity``
    safely returns 0.0 for it.
    """
    vec = [0.0] * EMBEDDING_DIM

    for token in tokens:
        digest = hashlib.md5(token.encode()).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        sign = 1.0 if digest[4] & 1 == 0 else -1.0
        vec[bucket] += sign

    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0.0:
        return vec

    return [x / norm for x in vec]


def embed_jd_text(text: str) -> list[float]:
    """Generate embedding vector for job description text.

    Hashing-trick bag-of-words embedding (stdlib-only, deterministic across
    calls and processes). Returns a 1024-dim L2-normalized dense vector for
    JD similarity matching; documents sharing vocabulary land closer
    together in cosine-similarity space.
    """
    return _embed_tokens(_tokenize(text))


def embed_user_skills(skills: list[str]) -> list[float]:
    """Generate embedding for a user's skill profile.

    Joins the skill strings into a single token stream and routes it
    through the same hashing-trick embedding as ``embed_jd_text``, so skill
    lists and job descriptions sharing vocabulary show meaningfully
    positive cosine similarity.
    """
    return _embed_tokens(_tokenize(" ".join(skills)))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (0..1)."""
    if not a or not b:
        return 0.0

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)
