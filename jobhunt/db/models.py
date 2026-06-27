"""SQLAlchemy ORM models for JobHunt persistence layer.

Maps Python dataclasses (jobhunt.models) to Postgres tables. Includes:
- User profiles and their job hunt preferences
- Plan and plan steps (execution graph)
- Job postings, companies, and discovery batches
- Applications and tailored documents
- Reasoning traces with tool calls (append-only audit log)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey,
    Index, Enum, TIMESTAMP,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class ApplicationStatus(str, PyEnum):
    SAVED = "saved"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    OFFER = "offer"
    CLOSED = "closed"


class User(Base):
    __tablename__ = "users"

    user_id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    target_roles = Column(JSON, nullable=False, default=[])
    locations = Column(JSON, nullable=False, default=[])
    min_salary = Column(Integer, nullable=True)
    remote_ok = Column(Boolean, default=True)
    culture_keywords = Column(JSON, nullable=False, default=[])
    skills = Column(JSON, nullable=False, default=[])
    experiences = Column(JSON, nullable=False, default=[])
    veto_companies = Column(JSON, nullable=False, default=[])
    weekly_target = Column(Integer, default=10)
    skills_embedding = Column(JSON, nullable=True)  # Phase 2: pgvector vector type for Postgres
    skills_embedding_model = Column(String(100), nullable=True)  # e.g., "text-embed-3-large"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plans = relationship("Plan", back_populates="user")
    applications = relationship("Application", back_populates="user")


class Plan(Base):
    __tablename__ = "plans"

    plan_id = Column(String(32), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.user_id"), nullable=False)
    milestones = Column(JSON, nullable=False, default=[])
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    version = Column(Integer, default=1)
    notes = Column(Text, default="")

    user = relationship("User", back_populates="plans")
    steps = relationship("PlanStep", back_populates="plan")

    __table_args__ = (
        Index("ix_plans_user_id", "user_id"),
    )


class PlanStep(Base):
    __tablename__ = "plan_steps"

    step_id = Column(String(32), primary_key=True)
    plan_id = Column(String(32), ForeignKey("plans.plan_id"), nullable=False)
    agent = Column(String(50), nullable=False)
    action = Column(String(255), nullable=False)
    inputs = Column(JSON, nullable=False, default={})
    depends_on = Column(JSON, nullable=False, default=[])
    status = Column(String(20), default="pending")  # pending|running|done|failed|skipped
    result_ref = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    plan = relationship("Plan", back_populates="steps")

    __table_args__ = (
        Index("ix_plan_steps_plan_id", "plan_id"),
        Index("ix_plan_steps_status", "status"),
    )


class Company(Base):
    __tablename__ = "companies"

    company_id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    domain = Column(String(255), nullable=True)
    glassdoor_rating = Column(Float, nullable=True)
    funding_stage = Column(String(50), nullable=True)
    headcount = Column(Integer, nullable=True)
    layoffs_12mo = Column(Integer, nullable=True)
    sentiment = Column(Float, nullable=True)  # -1..1
    tech_stack = Column(JSON, nullable=False, default=[])
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job_postings = relationship("JobPosting", back_populates="company_obj")

    __table_args__ = (
        Index("ix_companies_domain", "domain"),
    )


class JobPosting(Base):
    __tablename__ = "job_postings"

    job_id = Column(String(32), primary_key=True)
    company_id = Column(String(32), ForeignKey("companies.company_id"), nullable=True)
    company = Column(String(255), nullable=False)  # redundant for queries
    source = Column(String(50), nullable=False)
    source_id = Column(String(255), nullable=False)
    url = Column(String(1024), nullable=False)
    title = Column(String(255), nullable=False)
    location = Column(String(255), nullable=False)
    jd_text = Column(Text, nullable=False)
    posted_at = Column(TIMESTAMP, nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    remote = Column(Boolean, default=False)
    relevance_score = Column(Float, default=0.0)
    ghost_score = Column(Float, default=0.0)
    fingerprint = Column(String(16), nullable=False, unique=True)
    raw = Column(JSON, nullable=False, default={})
    jd_embedding = Column(JSON, nullable=True)  # Phase 2: pgvector vector type for Postgres
    jd_embedding_model = Column(String(100), nullable=True)  # e.g., "text-embed-3-large"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    company_obj = relationship("Company", back_populates="job_postings")
    applications = relationship("Application", back_populates="job_posting")

    __table_args__ = (
        Index("ix_job_postings_fingerprint", "fingerprint"),
        Index("ix_job_postings_company", "company"),
        Index("ix_job_postings_source", "source"),
        Index("ix_job_postings_relevance", "relevance_score"),
    )


class Application(Base):
    __tablename__ = "applications"

    application_id = Column(String(32), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.user_id"), nullable=False)
    job_id = Column(String(32), ForeignKey("job_postings.job_id"), nullable=False)
    status = Column(Enum(ApplicationStatus), default=ApplicationStatus.SAVED)
    submitted_at = Column(DateTime, nullable=True)
    confirmation_id = Column(String(255), nullable=True)
    documents = Column(JSON, nullable=False, default=[])  # S3 keys
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="applications")
    job_posting = relationship("JobPosting", back_populates="applications")

    __table_args__ = (
        Index("ix_applications_user_id", "user_id"),
        Index("ix_applications_status", "status"),
    )


class TailoredDocument(Base):
    __tablename__ = "tailored_documents"

    document_id = Column(String(32), primary_key=True)
    application_id = Column(String(32), ForeignKey("applications.application_id"), nullable=True)
    job_id = Column(String(32), ForeignKey("job_postings.job_id"), nullable=False)
    company = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    document_type = Column(String(20), default="resume")  # resume|cover_letter
    keyword_coverage = Column(Float, default=0.0)
    missing_keywords = Column(JSON, nullable=False, default=[])
    s3_key = Column(String(1024), nullable=True)
    content = Column(Text, nullable=True)  # for versioning before upload
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    application = relationship("Application")
    job_posting = relationship("JobPosting")

    __table_args__ = (
        Index("ix_tailored_documents_job_id", "job_id"),
        Index("ix_tailored_documents_application_id", "application_id"),
    )


class ToolCall(Base):
    __tablename__ = "tool_calls"

    tool_call_id = Column(String(32), primary_key=True)
    trace_id = Column(String(32), ForeignKey("reasoning_traces.trace_id"), nullable=False)
    tool = Column(String(100), nullable=False)
    args_summary = Column(Text, nullable=False)
    ok = Column(Boolean, nullable=False)
    latency_ms = Column(Integer, nullable=False)
    retries = Column(Integer, default=0)
    fallback_used = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    trace = relationship("ReasoningTrace", back_populates="tool_calls")

    __table_args__ = (
        Index("ix_tool_calls_trace_id", "trace_id"),
    )


class ReasoningTrace(Base):
    __tablename__ = "reasoning_traces"

    trace_id = Column(String(32), primary_key=True)
    agent = Column(String(100), nullable=False)
    task_id = Column(String(100), nullable=False)
    thoughts = Column(JSON, nullable=False, default=[])
    self_critique = Column(JSON, nullable=False, default={})
    decision = Column(Text, default="")
    confidence = Column(Float, default=0.0)
    parent_trace_id = Column(String(32), ForeignKey("reasoning_traces.trace_id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tool_calls = relationship("ToolCall", back_populates="trace")
    children = relationship(
        "ReasoningTrace",
        remote_side=[trace_id],
        backref="parent",
        foreign_keys=[parent_trace_id],
    )

    __table_args__ = (
        Index("ix_reasoning_traces_agent", "agent"),
        Index("ix_reasoning_traces_task_id", "task_id"),
        Index("ix_reasoning_traces_created_at", "created_at"),
        Index("ix_reasoning_traces_parent_id", "parent_trace_id"),
    )
