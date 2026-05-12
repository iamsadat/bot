"""Add embedding columns for JD and skill vectors.

Revision ID: 002_embeddings
Revises: 001_initial
Create Date: 2026-05-12

Adds JSON columns for embeddings (compatible with both SQLite and Postgres).
Phase 2 will upgrade to pgvector Vector type on Postgres.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "002_embeddings"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add embedding columns."""
    op.add_column("job_postings", sa.Column("jd_embedding", sa.JSON(), nullable=True))
    op.add_column("job_postings", sa.Column("jd_embedding_model", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("skills_embedding", sa.JSON(), nullable=True))
    op.add_column("users", sa.Column("skills_embedding_model", sa.String(100), nullable=True))


def downgrade() -> None:
    """Remove embedding columns."""
    op.drop_column("users", "skills_embedding_model")
    op.drop_column("users", "skills_embedding")
    op.drop_column("job_postings", "jd_embedding_model")
    op.drop_column("job_postings", "jd_embedding")
