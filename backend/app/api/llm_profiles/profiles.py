from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.llm_profiles import router
from backend.app.api.llm_profiles.views import model_view
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, LlmModel
from backend.app.llm_runtime.registry import ModelRegistry, RegistryValidationError
from backend.app.llm_runtime.runtime_service import ModelRuntimeError, ModelRuntimeService
from backend.app.llm_runtime.schemas import ModelCreate, ModelUpdate


@router.get("/profiles")
def list_models(db: Session = Depends(get_db)):
    return [model_view(item) for item in db.scalars(select(LlmModel)).all()]


@router.post("/profiles", status_code=201)
def create_model(payload: ModelCreate, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    try:
        item = ModelRegistry(db).create_model(**payload.model_dump())
    except RegistryValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    audit_admin_action(db, admin, "models.profile.create", "llm_model", item.id, payload.model_dump())
    return model_view(item)


@router.patch("/profiles/{model_id}")
def update_model(model_id: str, payload: ModelUpdate, admin: Account = Depends(require_admin),
                 db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    item = db.get(LlmModel, model_id)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND"})
    values = payload.model_dump(exclude_unset=True)
    effective_max_tokens = values.get("max_tokens", item.max_tokens)
    effective_agent_budget = values.get("agent_token_budget", item.agent_token_budget)
    if item.model_type == "chat" and effective_max_tokens is None and effective_agent_budget is None:
        raise HTTPException(status_code=422, detail={"code": "MODEL_REQUEST_BUDGET_REQUIRED"})
    for key, value in values.items():
        setattr(item, key, value)
    item.revision += 1
    item.capability_flags_json = "{}"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "MODEL_PROFILE_INVALID"}) from exc
    db.refresh(item)
    from backend.app.audit.service import audit_admin_action

    audit_admin_action(db, admin, "models.profile.update", "llm_model", item.id, payload.model_dump(exclude_unset=True))
    return model_view(item)


@router.post("/profiles/{model_id}/capability-tests")
def run_capability_test(model_id: str, request: Request, db: Session = Depends(get_db)):
    model = db.get(LlmModel, model_id)
    if not model: raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND"})
    tester_factory = getattr(request.app.state, "capability_tester_factory", None)
    try:
        tester = tester_factory(model) if tester_factory else ModelRuntimeService(db).capability_tester(model)
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
    result = tester.run_embedding(model.embedding_dimensions) if model.model_type == "embedding" else tester.run_chat()
    ModelRegistry(db).record_capability_result(model.id, result.capabilities, result.error_code,
                                               {"event_types": [event.type for event in result.events], "dimensions": result.dimensions})
    return {"passed": result.passed, "capabilities": result.capabilities, "error_code": result.error_code, "dimensions": result.dimensions}
