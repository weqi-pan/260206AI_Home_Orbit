from fastapi import Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.api.llm_profiles import router
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, Household, LlmModel, ModelGrant


@router.get("/households/{household_id}/grants")
def list_grants(household_id: str, db: Session = Depends(get_db)):
    if not db.get(Household, household_id):
        raise HTTPException(status_code=404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    return [{"household_id": item.household_id, "model_id": item.model_id}
            for item in db.scalars(select(ModelGrant).where(ModelGrant.household_id == household_id)).all()]


@router.put("/households/{household_id}/grants/{model_id}")
def grant_model(household_id: str, model_id: str, admin: Account = Depends(require_admin),
                db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    if not db.get(Household, household_id):
        raise HTTPException(status_code=404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    if not db.get(LlmModel, model_id):
        raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND"})
    item = db.scalar(select(ModelGrant).where(ModelGrant.household_id == household_id, ModelGrant.model_id == model_id))
    if not item:
        item = ModelGrant(household_id=household_id, model_id=model_id)
        db.add(item)
        db.commit()
    audit_admin_action(db, admin, "models.grant.create", "model_grant", model_id,
                       {"household_id": household_id}, household_id=household_id)
    return {"household_id": item.household_id, "model_id": item.model_id}


@router.delete("/households/{household_id}/grants/{model_id}", status_code=204)
def revoke_model(household_id: str, model_id: str, admin: Account = Depends(require_admin),
                 db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    db.execute(delete(ModelGrant).where(ModelGrant.household_id == household_id, ModelGrant.model_id == model_id))
    db.commit()
    audit_admin_action(db, admin, "models.grant.delete", "model_grant", model_id,
                       {"household_id": household_id}, household_id=household_id)
