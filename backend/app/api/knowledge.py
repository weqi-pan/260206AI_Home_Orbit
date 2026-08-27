import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, FaqPair, HouseholdMember, KnowledgeFile, utcnow
from backend.app.models.execution import purpose_model_resolver
from backend.app.services.faq_gen import FaqGenerationError, FaqGenerator
from backend.rag.vector_store import VectorStoreService, VectorStoreUnavailable


router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_admin)])
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


class ReviewRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")


def vector_service(request):
    service = getattr(request.app.state, "vector_store_service", None)
    if service is None:
        service = VectorStoreService(request.app.state.settings.chroma_path)
        request.app.state.vector_store_service = service
    return service


def _bootstrap_file(settings, filename):
    root = Path(settings.knowledge_path).resolve()
    candidate = (root / filename).resolve()
    return candidate if candidate.is_relative_to(root) else None


def _knowledge_file_path(settings, item):
    uploaded = Path(settings.knowledge_upload_path) / f"{item.id}{item.file_type}"
    if uploaded.exists():
        return uploaded
    bootstrap = _bootstrap_file(settings, item.filename)
    return bootstrap if bootstrap and bootstrap.exists() else None


def file_dict(item):
    return {"id": item.id, "filename": item.filename, "file_md5": item.file_md5, "file_type": item.file_type, "status": item.status, "created_at": item.created_at}


def faq_dict(item):
    return {"id": item.id, "question": item.question, "answer": item.answer, "source_file_id": item.source_file_id, "status": item.status,
            "created_at": item.created_at, "reviewed_at": item.reviewed_at}


@router.get("/files")
def files(db: Session = Depends(get_db)):
    return [file_dict(item) for item in db.scalars(select(KnowledgeFile).where(KnowledgeFile.status == "active").order_by(KnowledgeFile.created_at.desc())).all()]


@router.post("/files", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".txt"}:
        raise HTTPException(status_code=422, detail="仅支持 PDF/TXT 文件")
    content = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="文件不能超过20MB")
    digest = hashlib.md5(content).hexdigest()
    existing = db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_md5 == digest))
    if existing and existing.status == "active":
        raise HTTPException(status_code=409, detail="文件内容重复")
    directory = Path(request.app.state.settings.knowledge_upload_path)
    directory.mkdir(parents=True, exist_ok=True)
    item = existing or KnowledgeFile(file_md5=digest)
    item.filename = Path(file.filename).name
    item.file_type = suffix
    item.uploaded_by = admin.id
    item.status = "active"
    if existing is None:
        db.add(item)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="文件内容重复") from exc
    path = directory / f"{item.id}{suffix}"
    path.write_bytes(content)
    try:
        vector_service(request).add_file(item.id, path, item.filename)
    except Exception as exc:
        db.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail={"code": "VECTOR_STORE_WRITE_FAILED"}) from exc
    db.commit()
    db.refresh(item)
    return file_dict(item)


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: str, request: Request, response: Response, db: Session = Depends(get_db)):
    item = db.get(KnowledgeFile, file_id)
    if not item or item.status != "active":
        raise HTTPException(status_code=404, detail="知识文件不存在")
    try:
        vector_service(request).delete_file(file_id)
        for faq in db.scalars(select(FaqPair).where(FaqPair.source_file_id == file_id)).all():
            vector_service(request).delete_faq(faq.id)
            faq.status = "rejected"
            faq.reviewed_at = utcnow()
    except VectorStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "VECTOR_STORE_UNAVAILABLE"}) from exc
    item.status = "deleted"
    db.commit()
    response.status_code = 204


@router.post("/rebuild")
def rebuild(request: Request, db: Session = Depends(get_db)):
    files = []
    for item in db.scalars(select(KnowledgeFile).where(KnowledgeFile.status == "active")).all():
        path = _knowledge_file_path(request.app.state.settings, item)
        if path:
            files.append((item.id, path, item.filename))
    faqs = [
        (item.id, item.question, item.answer, item.source_file_id)
        for item in db.scalars(select(FaqPair).where(FaqPair.status == "approved")).all()
    ]
    try:
        vector_service(request).rebuild(files=files, faqs=faqs)
    except VectorStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "VECTOR_STORE_UNAVAILABLE"}) from exc
    return {"status": "rebuilt"}


@router.post("/files/{file_id}/generate-faq")
def generate_faq(file_id: str, request: Request, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(KnowledgeFile, file_id)
    if not item or item.status != "active":
        raise HTTPException(status_code=404, detail="知识文件不存在")
    service = getattr(request.app.state, "faq_generator", None)
    if not service:
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))
        service = FaqGenerator(model_resolver=purpose_model_resolver(request.app.state.database.session_factory, household_id))
    path = _knowledge_file_path(request.app.state.settings, item)
    if not path:
        raise HTTPException(status_code=409, detail="知识文件源文件不存在，请重新上传或重建")
    try:
        pairs = service.generate(path)
    except FaqGenerationError as exc:
        raise HTTPException(status_code=400, detail={"code": "FAQ_RESULT_INVALID"}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": "FAQ_MODEL_UNAVAILABLE"}) from exc
    result = []
    for pair in pairs:
        faq = FaqPair(question=pair["question"], answer=pair["answer"], source_file_id=item.id)
        db.add(faq)
        db.flush()
        result.append(faq_dict(faq))
    db.commit()
    return result


@router.get("/faq")
def faq_list(status: str | None = None, db: Session = Depends(get_db)):
    query = select(FaqPair)
    if status:
        query = query.where(FaqPair.status == status)
    return [faq_dict(item) for item in db.scalars(query.order_by(FaqPair.id.desc())).all()]


@router.put("/faq/{faq_id}/review")
def review(faq_id: int, payload: ReviewRequest, request: Request, db: Session = Depends(get_db)):
    item = db.get(FaqPair, faq_id)
    if not item:
        raise HTTPException(status_code=404, detail="FAQ不存在")
    try:
        if payload.status == item.status:
            return faq_dict(item)
        if payload.status == "approved":
            vector_service(request).add_faq(item.id, item.question, item.answer, item.source_file_id)
        else:
            vector_service(request).delete_faq(item.id)
    except VectorStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "VECTOR_STORE_UNAVAILABLE"}) from exc
    item.status = payload.status
    item.reviewed_at = utcnow()
    db.commit()
    db.refresh(item)
    return faq_dict(item)
