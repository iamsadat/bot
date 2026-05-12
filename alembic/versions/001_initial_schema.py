"""Initial schema: users, plans, companies, job postings, applications, documents, reasoning traces.

Revision ID: 001_initial
Revises: None
Create Date: 2026-05-12

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users table
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("target_roles", sa.JSON(), nullable=False),
        sa.Column("locations", sa.JSON(), nullable=False),
        sa.Column("min_salary", sa.Integer(), nullable=True),
        sa.Column("remote_ok", sa.Boolean(), nullable=False),
        sa.Column("culture_keywords", sa.JSON(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("experiences", sa.JSON(), nullable=False),
        sa.Column("veto_companies", sa.JSON(), nullable=False),
        sa.Column("weekly_target", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("email"),
    )

    # plans table
    op.create_table(
        "plans",
        sa.Column("plan_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("milestones", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("plan_id"),
    )
    op.create_index("ix_plans_user_id", "plans", ["user_id"])

    # plan_steps table
    op.create_table(
        "plan_steps",
        sa.Column("step_id", sa.String(32), nullable=False),
        sa.Column("plan_id", sa.String(32), nullable=False),
        sa.Column("agent", sa.String(50), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result_ref", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.plan_id"]),
        sa.PrimaryKeyConstraint("step_id"),
    )
    op.create_index("ix_plan_steps_plan_id", "plan_steps", ["plan_id"])
    op.create_index("ix_plan_steps_status", "plan_steps", ["status"])

    # companies table
    op.create_table(
        "companies",
        sa.Column("company_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("glassdoor_rating", sa.Float(), nullable=True),
        sa.Column("funding_stage", sa.String(50), nullable=True),
        sa.Column("headcount", sa.Integer(), nullable=True),
        sa.Column("layoffs_12mo", sa.Integer(), nullable=True),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column("tech_stack", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("company_id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_companies_domain", "companies", ["domain"])

    # job_postings table
    op.create_table(
        "job_postings",
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("company_id", sa.String(32), nullable=True),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("ghost_score", sa.Float(), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index("ix_job_postings_fingerprint", "job_postings", ["fingerprint"])
    op.create_index("ix_job_postings_company", "job_postings", ["company"])
    op.create_index("ix_job_postings_source", "job_postings", ["source"])
    op.create_index("ix_job_postings_relevance", "job_postings", ["relevance_score"])

    # applications table
    op.create_table(
        "applications",
        sa.Column("application_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("status", sa.Enum("saved", "applied", "assessment", "interview", "offer", "closed", name="applicationstatus"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("confirmation_id", sa.String(255), nullable=True),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["job_postings.job_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("application_id"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_status", "applications", ["status"])

    # tailored_documents table
    op.create_table(
        "tailored_documents",
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("application_id", sa.String(32), nullable=True),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("document_type", sa.String(20), nullable=False),
        sa.Column("keyword_coverage", sa.Float(), nullable=False),
        sa.Column("missing_keywords", sa.JSON(), nullable=False),
        sa.Column("s3_key", sa.String(1024), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.application_id"]),
        sa.ForeignKeyConstraint(["job_id"], ["job_postings.job_id"]),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index("ix_tailored_documents_job_id", "tailored_documents", ["job_id"])
    op.create_index("ix_tailored_documents_application_id", "tailored_documents", ["application_id"])

    # reasoning_traces table
    op.create_table(
        "reasoning_traces",
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("agent", sa.String(100), nullable=False),
        sa.Column("task_id", sa.String(100), nullable=False),
        sa.Column("thoughts", sa.JSON(), nullable=False),
        sa.Column("self_critique", sa.JSON(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("parent_trace_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_trace_id"], ["reasoning_traces.trace_id"]),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_reasoning_traces_agent", "reasoning_traces", ["agent"])
    op.create_index("ix_reasoning_traces_task_id", "reasoning_traces", ["task_id"])
    op.create_index("ix_reasoning_traces_created_at", "reasoning_traces", ["created_at"])
    op.create_index("ix_reasoning_traces_parent_id", "reasoning_traces", ["parent_trace_id"])

    # tool_calls table
    op.create_table(
        "tool_calls",
        sa.Column("tool_call_id", sa.String(32), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("args_summary", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["trace_id"], ["reasoning_traces.trace_id"]),
        sa.PrimaryKeyConstraint("tool_call_id"),
    )
    op.create_index("ix_tool_calls_trace_id", "tool_calls", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_calls_trace_id", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_reasoning_traces_parent_id", table_name="reasoning_traces")
    op.drop_index("ix_reasoning_traces_created_at", table_name="reasoning_traces")
    op.drop_index("ix_reasoning_traces_task_id", table_name="reasoning_traces")
    op.drop_index("ix_reasoning_traces_agent", table_name="reasoning_traces")
    op.drop_table("reasoning_traces")
    op.drop_index("ix_tailored_documents_application_id", table_name="tailored_documents")
    op.drop_index("ix_tailored_documents_job_id", table_name="tailored_documents")
    op.drop_table("tailored_documents")
    op.drop_index("ix_applications_status", table_name="applications")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
    op.drop_index("ix_job_postings_relevance", table_name="job_postings")
    op.drop_index("ix_job_postings_source", table_name="job_postings")
    op.drop_index("ix_job_postings_company", table_name="job_postings")
    op.drop_index("ix_job_postings_fingerprint", table_name="job_postings")
    op.drop_table("job_postings")
    op.drop_index("ix_companies_domain", table_name="companies")
    op.drop_table("companies")
    op.drop_index("ix_plan_steps_status", table_name="plan_steps")
    op.drop_index("ix_plan_steps_plan_id", table_name="plan_steps")
    op.drop_table("plan_steps")
    op.drop_index("ix_plans_user_id", table_name="plans")
    op.drop_table("plans")
    op.drop_table("users")
