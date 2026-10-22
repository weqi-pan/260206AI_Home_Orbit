from backend.app.api.creator.base import *
from backend.app.api.creator.base import _draft, _draft_view, _guard_draft_not_locked_in_review, _household
from backend.app.api.creator.drafts import _validation_report

@router.post("/drafts/{draft_id}/generate")
def generate_card(draft_id: str, payload: GenerateRequest, request: Request,
                  account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    from backend.app.llm_runtime.execution import purpose_model_resolver
    from backend.app.llm_runtime.quotas import QuotaExceeded
    from backend.app.llm_runtime.runtime_service import ModelRuntimeError

    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    _guard_draft_not_locked_in_review(db, member.household_id, draft_id)
    validator = DraftValidator(db, member.household_id)
    generator = getattr(request.app.state, "intent_card_generator", None) or IntentCardGenerator(
        model_resolver=purpose_model_resolver(request.app.state.database.session_factory, member.household_id))
    try:
        card = generator.generate(payload.description, catalog=validator.ceiling_tools())
    except GenerationError as exc:
        status_code = 429 if "QUOTA" in exc.code or "RATE" in exc.code else 403 if "GRANT" in exc.code else 503
        raise HTTPException(status_code, detail={"code": exc.code}) from exc
    except (ModelRuntimeError, QuotaExceeded) as exc:
        status_code = 429 if "QUOTA" in exc.code or "RATE" in exc.code else 403 if "GRANT" in exc.code else 503
        raise HTTPException(status_code, detail={"code": exc.code}) from exc
    report = _validation_report(db, member.household_id, draft, card)
    draft.source_description = payload.description
    draft.intent_card_json = json.dumps(card, ensure_ascii=False)
    draft.last_validation_json = json.dumps(report, ensure_ascii=False)
    if not report["valid"]:
        draft.status = "draft"
    db.commit()
    db.refresh(draft)
    return {"intent_card": card, "validation": report,
            "status": draft.status}


@router.post("/drafts/{draft_id}/validate")
def validate_draft(draft_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    if not draft.intent_card_json:
        raise HTTPException(409, detail={"code": "DRAFT_EMPTY"})
    card = json.loads(draft.intent_card_json)
    report = _validation_report(db, member.household_id, draft, card)
    draft.last_validation_json = json.dumps(report, ensure_ascii=False)
    if report["valid"]:
        draft.status = "validated"
    else:
        draft.status = "draft"
        raise HTTPException(422, detail={"code": "DRAFT_VALIDATION_FAILED", "errors": report["errors"]})
    db.commit()
    db.refresh(draft)
    return {"status": draft.status, "validation": report}
