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
from sqlalchemy.orm import declarative_base, sessionmaker

from jobhunt.approval import ApprovalQueue, ApprovalRequest, ApprovalState
from jobhunt.models import UserProfile

_Base = declarative_base()


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
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DashboardStore:
    """Single-row snapshot store backed by SQLite.

    The dashboard is single-user in this build, so we keep one row that
    we upsert on every mutation. ``load()`` returns ``None`` when the
    database is fresh, ``save()`` is idempotent.
    """

    def __init__(self, db_path: str | Path = "jobhunt.db") -> None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = path
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )
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
        veto_companies=list(d.get("veto_companies", [])),
        weekly_target=int(d.get("weekly_target", 10)),
        application_answers=dict(d.get("application_answers", {})),
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
