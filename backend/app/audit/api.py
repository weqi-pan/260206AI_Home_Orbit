# -*- coding: utf-8 -*-
"""Phase 8 audit query API (/api/audit/*).

Admin-only and itself audited: every query writes an ``audit.read``
AdminActionAudit row with the sanitized filter set. All list endpoints are
paginated with total counts; detail endpoints return stable 404 codes."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditQueryService, audit_read
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])


def _parse_time(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value and value.tzinfo else value


def _read(db: Session, admin: Account, endpoint: str, filters: dict):
    audit_read(db, admin, endpoint, {key: value for key, value in filters.items() if value is not None})


@router.get("/agent-runs")
def agent_runs(status: str | None = None, model_id: str | None = None, deployment_id: str | None = None,
               household_id: str | None = None,
               time_from: datetime | None = Query(default=None, alias="from"),
               time_to: datetime | None = Query(default=None, alias="to"),
               page: int = Query(default=1, ge=1),
               page_size: int = Query(default=20, ge=1, le=100),
               admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).agent_runs(
        status=status, model_id=model_id, deployment_id=deployment_id, household_id=household_id,
        time_from=_parse_time(time_from), time_to=_parse_time(time_to), page=page, page_size=page_size)
    _read(db, admin, "/api/audit/agent-runs", {"status": status, "model_id": model_id,
                                               "deployment_id": deployment_id, "household_id": household_id,
                                               "time_from": time_from, "time_to": time_to})
    db.commit()
    return result


@router.get("/tool-executions")
def tool_executions(run_id: str | None = None, tool_id: str | None = None, status: str | None = None,
                    household_id: str | None = None,
                    time_from: datetime | None = Query(default=None, alias="from"),
                    time_to: datetime | None = Query(default=None, alias="to"),
                    page: int = Query(default=1, ge=1),
                    page_size: int = Query(default=20, ge=1, le=100),
                    admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).tool_executions(
        run_id=run_id, tool_id=tool_id, status=status, household_id=household_id,
        time_from=_parse_time(time_from), time_to=_parse_time(time_to), page=page, page_size=page_size)
    _read(db, admin, "/api/audit/tool-executions", {"run_id": run_id, "tool_id": tool_id, "status": status,
                                                    "household_id": household_id, "time_from": time_from,
                                                    "time_to": time_to})
    db.commit()
    return result


@router.get("/tool-executions/{execution_id}")
def tool_execution_detail(execution_id: str, admin: Account = Depends(require_admin),
                          db: Session = Depends(get_db)):
    detail = AuditQueryService(db).tool_execution_detail(execution_id)
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "TOOL_EXECUTION_NOT_FOUND"})
    _read(db, admin, "/api/audit/tool-executions/{id}", {"execution_id": execution_id})
    db.commit()
    return detail


@router.get("/model-usage")
def model_usage(household_id: str | None = None, model_id: str | None = None, purpose: str | None = None,
                status: str | None = None,
                time_from: datetime | None = Query(default=None, alias="from"),
                time_to: datetime | None = Query(default=None, alias="to"),
                page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).model_usage(
        household_id=household_id, model_id=model_id, purpose=purpose, status=status,
        time_from=_parse_time(time_from), time_to=_parse_time(time_to), page=page, page_size=page_size)
    _read(db, admin, "/api/audit/model-usage", {"household_id": household_id, "model_id": model_id,
                                                "purpose": purpose, "status": status, "time_from": time_from,
                                                "time_to": time_to})
    db.commit()
    return result


@router.get("/model-usage/accounting")
def model_usage_accounting(days: int = Query(default=7, ge=1, le=90), household_id: str | None = None,
                           admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).usage_accounting(days=days, household_id=household_id)
    _read(db, admin, "/api/audit/model-usage/accounting", {"days": days, "household_id": household_id})
    db.commit()
    return result


@router.get("/celery-tasks")
def celery_tasks(task_type: str | None = None, status: str | None = None, household_id: str | None = None,
                 error_code: str | None = None,
                 time_from: datetime | None = Query(default=None, alias="from"),
                 time_to: datetime | None = Query(default=None, alias="to"),
                 page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100),
                 admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).celery_tasks(
        task_type=task_type, status=status, household_id=household_id, error_code=error_code,
        time_from=_parse_time(time_from), time_to=_parse_time(time_to), page=page, page_size=page_size)
    _read(db, admin, "/api/audit/celery-tasks", {"task_type": task_type, "status": status,
                                                 "household_id": household_id, "error_code": error_code,
                                                 "time_from": time_from, "time_to": time_to})
    db.commit()
    return result


@router.get("/celery-tasks/{task_id}")
def celery_task_detail(task_id: str, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    detail = AuditQueryService(db).celery_task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "CELERY_TASK_NOT_FOUND"})
    _read(db, admin, "/api/audit/celery-tasks/{id}", {"task_id": task_id})
    db.commit()
    return detail


@router.get("/admin-actions")
def admin_actions(actor_id: str | None = None, action: str | None = None, resource_type: str | None = None,
                  household_id: str | None = None,
                  time_from: datetime | None = Query(default=None, alias="from"),
                  time_to: datetime | None = Query(default=None, alias="to"),
                  page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=20, ge=1, le=100),
                  admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).admin_actions(
        actor_id=actor_id, action=action, resource_type=resource_type, household_id=household_id,
        time_from=_parse_time(time_from), time_to=_parse_time(time_to), page=page, page_size=page_size)
    _read(db, admin, "/api/audit/admin-actions", {"actor_id": actor_id, "action": action,
                                                  "resource_type": resource_type, "household_id": household_id,
                                                  "time_from": time_from, "time_to": time_to})
    db.commit()
    return result


@router.get("/deployments/pointers")
def deployment_pointers(admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).deployment_pointer_history()
    _read(db, admin, "/api/audit/deployments/pointers", {})
    db.commit()
    return result


@router.get("/models/revisions")
def model_config_revisions(admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    result = AuditQueryService(db).model_config_revisions()
    _read(db, admin, "/api/audit/models/revisions", {})
    db.commit()
    return result
