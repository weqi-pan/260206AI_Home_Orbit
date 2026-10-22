from backend.app.api.creator.base import *
from backend.app.api.creator.base import _draft, _draft_view, _guard_draft_not_locked_in_review, _household

@router.get("/tools")
def ceiling_tools(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return sorted(DraftValidator(db, member.household_id).ceiling_tools().values(), key=lambda item: item["key"])


@router.post("/drafts", status_code=201)
def create_draft(payload: DraftCreateRequest, account: Account = Depends(get_current_account),
                 db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = CreatorDraft(household_id=member.household_id, title=payload.title,
                         source_description=payload.description, created_by_account_id=account.id)
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return _draft_view(db, member.household_id, draft)


@router.get("/drafts")
def list_drafts(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    rows = db.scalars(select(CreatorDraft).where(CreatorDraft.household_id == member.household_id)
                      .order_by(CreatorDraft.updated_at.desc(), CreatorDraft.id.desc())).all()
    return [{"id": row.id, "title": row.title, "status": row.status, "updated_at": row.updated_at} for row in rows]


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return _draft_view(db, member.household_id, _draft(db, member.household_id, draft_id))


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: str, payload: DraftUpdateRequest, account: Account = Depends(get_current_account),
                 db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    _guard_draft_not_locked_in_review(db, member.household_id, draft_id)
    if payload.title is not None:
        draft.title = payload.title
    if payload.intent_card is not None:
        # 高级区保存同一校验边界：非法意图卡拒绝保存，绝不入库
        report = _validation_report(db, member.household_id, draft, payload.intent_card)
        if not report["valid"]:
            raise HTTPException(422, detail={"code": "DRAFT_VALIDATION_FAILED", "errors": report["errors"]})
        draft.intent_card_json = json.dumps(payload.intent_card, ensure_ascii=False)
        draft.last_validation_json = json.dumps(report, ensure_ascii=False)
        draft.status = "draft"
    draft.updated_at = utcnow()
    db.commit()
    db.refresh(draft)
    return _draft_view(db, member.household_id, draft)


def _validation_report(db, household_id, draft, card):
    validator = DraftValidator(db, household_id)
    try:
        validator.validate(card, draft_id=draft.id)
        return {"valid": True, "errors": []}
    except DraftValidationRejected as exc:
        return {"valid": False, "errors": exc.errors}
