from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.llm_profiles import router
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, Household
from backend.app.llm_runtime.quotas import QuotaService
from backend.app.llm_runtime.schemas import QuotaRequest


@router.put("/households/{household_id}/quota")
def configure_quota(household_id: str, payload: QuotaRequest, admin: Account = Depends(require_admin),
                    db: Session = Depends(get_db)):
    if not db.get(Household, household_id):
        raise HTTPException(status_code=404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    item = QuotaService(db).configure(household_id, **payload.model_dump())
    from backend.app.audit.service import audit_admin_action

    audit_admin_action(db, admin, "quota.configure", "household_quota", household_id, payload.model_dump(),
                       household_id=household_id)
    return {"household_id": item.household_id, "monthly_limit": item.monthly_limit,
            "requests_per_minute": item.requests_per_minute, "agent_token_budget": item.agent_token_budget}
