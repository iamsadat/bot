"""SQLite-backed persistence for the live dashboard state.

Survives server restarts.  Stores everything as JSON blobs in a single
``dashboard_snapshots`` table — simple to reason about, fast to query,
and good enough for the single-user dev experience.  Multi-user can move
to the existing per-table SQLAlchemy ORM later without touching this
file's callers.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column, DateTime, Integer, JSON, String, Text, create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from jobhunt.approval import ApprovalQueue, ApprovalRequest, ApprovalState
from jobhunt.models import UserProfile

_Base = declarative_base()


def build_engine(db_path: str | Path, db_url: str | None) -> Engine:
    """Build a SQLAlchemy engine for a JSON-blob snapshot store.

    When ``db_url`` is given (e.g. a Postgres connection string), it is used
    directly so the store can be pointed at a durable external database
    instead of the host's ephemeral disk — mirrors the pooling pattern in
    ``jobhunt/db/engine.py``: ``NullPool`` + ``pool_pre_ping`` for non-SQLite,
    ``check_same_thread=False`` for SQLite. When ``db_url`` is ``None``, falls
    back to today's behavior: a SQLite file at ``db_path`` (directory
    auto-created).
    """
    if db_url:
        if db_url.startswith("sqlite"):
            return create_engine(db_url, connect_args={"check_same_thread": False})
        return create_engine(db_url, poolclass=NullPool, pool_pre_ping=True)
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


class _Snapshot(_Base):
    __tablename__ = "dashboard_snapshots"

    id = Column(Integer, primary_key=True)
    profile_json = Column(JSON, nullable=True)
    jobs_json = Column(JSON, default=list)
    applications_json = Column(JSON, default=list)
    approvals_json = Column(JSON, default=list)
    plan_json = Column(JSON, nullable=True)
    documents_json = Column(JSON, default=dict)  # job_id → tailored doc dict
    hunt_status = Column(String(20), default="idle")
    hunt_error = Column(Text, default="")
    ats_config_json = Column(JSON, default=dict)
    applies_today_json = Column(JSON, default=dict)  # date-iso → count (autonomy cap)
    activity_days_json = Column(JSON, default=list)  # ISO date strings (streaks)
    market_value_json = Column(JSON, default=list)  # Career Radar comp history
    contacts_json = Column(JSON, default=list)  # Career CRM contacts
    experiments_json = Column(JSON, default=dict)  # ExperimentRegistry.to_dict()
    linked_email = Column(String(320), nullable=True)  # verified email (magic link)
    plan = Column(String(20), nullable=True)  # "free" (default) | "pro" (billing webhook)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DashboardStore:
    """Single-row snapshot store backed by SQLite.

    The dashboard is single-user in this build, so we keep one row that
    we upsert on every mutation. ``load()`` returns ``None`` when the
    database is fresh, ``save()`` is idempotent.
    """

    def __init__(self, db_path: str | Path = "jobhunt.db", db_url: str | None = None) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.engine = build_engine(db_path, db_url)
        _Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    # ------------------------------------------------------------------ load

    def load(self) -> dict[str, Any] | None:
        """Return the most recent snapshot or ``None``."""
        with self.Session() as s:
            row = s.query(_Snapshot).order_by(_Snapshot.id.desc()).first()
            if row is None:
                return None
            return {
                "profile": _profile_from_dict(row.profile_json) if row.profile_json else None,
                "jobs": list(row.jobs_json or []),
                "applications": list(row.applications_json or []),
                "approvals": list(row.approvals_json or []),
                "plan": row.plan_json,
                "documents": dict(row.documents_json or {}),
                "hunt_status": row.hunt_status or "idle",
                "hunt_error": row.hunt_error or "",
                "ats_config": dict(row.ats_config_json or {}),
                "applies_today": dict(row.applies_today_json or {}),
                "activity_days": list(row.activity_days_json or []),
                "market_value": list(row.market_value_json or []),
                "contacts": list(row.contacts_json or []),
                "experiments": dict(row.experiments_json or {}),
                "linked_email": row.linked_email,
                "billing_plan": row.plan,
            }

    # ------------------------------------------------------------------ save

    def save(
        self,
        *,
        profile: UserProfile | None,
        jobs: list[dict],
        applications: list[dict],
        approvals: list[ApprovalRequest] | list[dict],
        plan: dict | None,
        hunt_status: str,
        hunt_error: str = "",
        ats_config: dict | None = None,
        documents: dict | None = None,
        applies_today: dict | None = None,
        activity_days: list | None = None,
        market_value: list | None = None,
        contacts: list | None = None,
        experiments: dict | None = None,
        linked_email: str | None = None,
        billing_plan: str | None = None,
    ) -> None:
        """Upsert the snapshot row."""
        appr_dicts = [
            a.to_dict() if isinstance(a, ApprovalRequest) else a for a in approvals
        ]
        with self.Session() as s:
            row = s.query(_Snapshot).order_by(_Snapshot.id.desc()).first()
            if row is None:
                row = _Snapshot(id=1)
                s.add(row)
            row.profile_json = asdict(profile) if profile else None
            row.jobs_json = jobs
            row.applications_json = applications
            row.approvals_json = appr_dicts
            row.plan_json = plan
            row.documents_json = documents or {}
            row.hunt_status = hunt_status
            row.hunt_error = hunt_error
            if ats_config is not None:
                row.ats_config_json = ats_config
            if applies_today is not None:
                row.applies_today_json = applies_today
            if activity_days is not None:
                row.activity_days_json = activity_days
            if market_value is not None:
                row.market_value_json = market_value
            if contacts is not None:
                row.contacts_json = contacts
            if experiments is not None:
                row.experiments_json = experiments
            if linked_email is not None:
                row.linked_email = linked_email
            if billing_plan is not None:
                row.plan = billing_plan
            s.commit()

    def clear(self) -> None:
        """Reset everything (used by tests)."""
        with self.Session() as s:
            s.query(_Snapshot).delete()
            s.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile_from_dict(d: dict) -> UserProfile:
    return UserProfile(
        user_id=d.get("user_id", ""),
        name=d.get("name", ""),
        email=d.get("email", ""),
        phone=d.get("phone", ""),
        target_roles=list(d.get("target_roles", [])),
        locations=list(d.get("locations", [])),
        min_salary=d.get("min_salary"),
        remote_ok=d.get("remote_ok", True),
        culture_keywords=list(d.get("culture_keywords", [])),
        skills=list(d.get("skills", [])),
        experiences=list(d.get("experiences", [])),
        education=list(d.get("education", [])),
        projects=list(d.get("projects", [])),
        links=dict(d.get("links", {})),
        veto_companies=list(d.get("veto_companies", [])),
        weekly_target=int(d.get("weekly_target", 10)),
        application_answers=dict(d.get("application_answers", {})),
        auto_apply=bool(d.get("auto_apply", False)),
        daily_apply_cap=int(d.get("daily_apply_cap", 0)),
        relevance_floor=float(d.get("relevance_floor", 0.0)),
        radar_enabled=bool(d.get("radar_enabled", False)),
        current_salary=d.get("current_salary"),
        current_title=d.get("current_title", ""),
        radar_keywords=list(d.get("radar_keywords", [])),
    )


def restore_approval_queue(
    queue: ApprovalQueue, snapshots: list[dict],
) -> None:
    """Reload approval requests from snapshot dicts into a fresh queue."""
    for s in snapshots:
        req = ApprovalRequest(
            request_id=s["request_id"],
            job_id=s["job_id"],
            document_id=s["document_id"],
            company=s["company"],
            title=s["title"],
            state=ApprovalState(s["state"]),
            reviewer=s.get("reviewer", ""),
            notes=s.get("notes", ""),
            created_at=s.get("created_at", 0.0),
            updated_at=s.get("updated_at", 0.0),
        )
        # Bypass the public API to avoid re-firing pubsub events on restore.
        queue._items[req.request_id] = req  # type: ignore[attr-defined]
