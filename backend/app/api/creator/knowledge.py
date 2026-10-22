from backend.app.api.creator.base import *
from backend.app.api.creator.base import _draft, _guard_draft_not_locked_in_review, _household

@router.post("/drafts/{draft_id}/knowledge", status_code=201)
def upload_knowledge(draft_id: str, request: Request, file: UploadFile = File(...),
                     skill_key: str = Form(default=""), account: Account = Depends(get_current_account),
                     db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    _draft(db, member.household_id, draft_id)
    content = file.file.read(MAX_UPLOAD_SIZE + 1)
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, detail={"code": "KNOWLEDGE_TOO_LARGE"})
    service = CreatorKnowledgeService(db, member.household_id,
                                       request.app.state.settings.knowledge_upload_path)
    try:
        asset = service.register_asset(draft_id, file.filename or "upload.txt",
                                       Path(file.filename or "").suffix, content,
                                       skill_key=skill_key, uploaded_by=account.id)
    except KnowledgeAssetRejected as exc:
        status_code = {"DRAFT_NOT_FOUND": 404, "KNOWLEDGE_DUPLICATE": 409}.get(exc.code, 422)
        raise HTTPException(status_code, detail={"code": exc.code, "reason": exc.detail}) from exc
    return {"id": asset.id, "filename": asset.filename, "file_type": asset.file_type,
            "size_bytes": asset.size_bytes, "skill_key": asset.skill_key,
            "status": asset.status, "scan_status": asset.scan_status, "scan_reason": asset.scan_reason,
            "content_hash": asset.content_hash}


@router.get("/drafts/{draft_id}/knowledge")
def list_knowledge(draft_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    _draft(db, member.household_id, draft_id)
    service = CreatorKnowledgeService(db, member.household_id, "")
    return [{"id": item.id, "filename": item.filename, "file_type": item.file_type,
             "size_bytes": item.size_bytes, "skill_key": item.skill_key,
             "scan_status": item.scan_status, "scan_reason": item.scan_reason,
             "content_hash": item.content_hash} for item in service.list_assets(draft_id)]


@router.delete("/drafts/{draft_id}/knowledge/{asset_id}", status_code=204)
def delete_knowledge(draft_id: str, asset_id: str, request: Request,
                     account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    _draft(db, member.household_id, draft_id)
    # 冻结工件按资产 id 引用知识：审核窗口期删除会使已审工件缺源
    _guard_draft_not_locked_in_review(db, member.household_id, draft_id)
    service = CreatorKnowledgeService(db, member.household_id, request.app.state.settings.knowledge_upload_path)
    try:
        service.delete_asset(draft_id, asset_id,
                             vector_service=(getattr(request.app.state, "vector_store_service", None)
                                             if purpose_ready(db, "embedding") else None))
    except KnowledgeAssetRejected as exc:
        raise HTTPException(404, detail={"code": exc.code}) from exc
    return None
