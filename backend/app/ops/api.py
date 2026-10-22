# -*- coding: utf-8 -*-
"""Phase 8 operations governance API (/api/ops/*).

Admin-only and audited: overview reads write an ``ops.read`` audit row; the
expired-reservation release is the single read-path-adjacent mutation here
(conservative reconciliation of crashed reservations)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.audit.service import audit_admin_action, sanitize_detail
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, Household
from backend.app.llm_runtime.quotas import QuotaService
from backend.app.ops.service import (MAX_WINDOW_DAYS, MIN_WINDOW_DAYS, OpsOverviewService,
                                     QuotaGovernanceService)

router = APIRouter(prefix="/ops", tags=["ops"], dependencies=[Depends(require_admin)])


def _read(db: Session, admin: Account, endpoint: str, filters: dict):
    audit_admin_action(db, admin, "ops.read", "ops_query", endpoint,
                       {"filters": {key: value for key, value in filters.items() if value is not None}},
                       commit=False)


@router.get("/overview")
def overview(window_days: int = Query(default=7, ge=MIN_WINDOW_DAYS, le=MAX_WINDOW_DAYS),
             admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = OpsOverviewService(db).overview(window_days=window_days)
    _read(db, admin, "/api/ops/overview", {"window_days": window_days})
    db.commit()
    return result


@router.get("/households/{household_id}/quota")
def household_quota(household_id: str, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    if not db.get(Household, household_id):
        raise HTTPException(status_code=404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    result = QuotaGovernanceService(db).household_quota_view(household_id)
    _read(db, admin, "/api/ops/households/{id}/quota", {"household_id": household_id})
    db.commit()
    return result


@router.get("/quota/incidents")
def quota_incidents(household_id: str | None = None, code: str | None = None,
                    time_from: datetime | None = Query(default=None, alias="from"),
                    time_to: datetime | None = Query(default=None, alias="to"),
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                    admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = QuotaGovernanceService(db).quota_incidents(
        household_id=household_id, code=code,
        time_from=time_from.replace(tzinfo=None) if time_from and time_from.tzinfo else time_from,
        time_to=time_to.replace(tzinfo=None) if time_to and time_to.tzinfo else time_to,
        page=page, page_size=page_size)
    _read(db, admin, "/api/ops/quota/incidents", {"household_id": household_id, "code": code,
                                                  "from": time_from, "to": time_to})
    db.commit()
    return result


@router.post("/model-usage/release-expired")
def release_expired(older_than_minutes: int = Query(default=15, ge=1, le=1440),
                    admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    released = QuotaService(db).release_expired(older_than_minutes=older_than_minutes)
    audit_admin_action(db, admin, "quota.release_expired", "model_usage", "expired-reservations",
                       {"released": released, "older_than_minutes": older_than_minutes})
    return {"released": released}


@router.get("/tasks/types")
def task_types(admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    """Manual re-trigger boundary support: the registered task type catalog."""
    from backend.app.tasks.definitions import TASK_TYPES

    items = [{"task_type": task_type, "max_attempts": spec.max_attempts,
              "description": spec.description} for task_type, spec in sorted(TASK_TYPES.items())]
    _read(db, admin, "/api/ops/tasks/types", {})
    db.commit()
    return {"items": items}
