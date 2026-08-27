import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, FaqPair, KnowledgeFile, utcnow
from backend.app.services.faq_gen import FaqGenerator
from backend.rag.vector_store import VectorStoreService, VectorStoreUnavailable


router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_admin)])
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


class ReviewRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")


def vector_service(request):
    return getattr(request.app.state, "vector_store_service", None) or VectorStoreService(request.app.state.settings.chroma_path)


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
    if db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_md5 == digest, KnowledgeFile.status == "active")):
        raise HTTPException(status_code=409, detail="文件内容重复")
    directory = Path(request.app.state.settings.knowledge_upload_path)
    directory.mkdir(parents=True, exist_ok=True)
    item = KnowledgeFile(filename=Path(file.filename).name, file_md5=digest, file_type=suffix, uploaded_by=admin.id)
    db.add(item)
    db.flush()
    path = directory / f"{item.id}{suffix}"
    path.write_bytes(content)
    try:
        vector_service(request).add_file(item.id, path, item.filename)
    except VectorStoreUnavailable as exc:
        db.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    item.status = "deleted"
    db.commit()
    response.status_code = 204


@router.post("/rebuild")
def rebuild(request: Request, db: Session = Depends(get_db)):
    directory = Path(request.app.state.settings.knowledge_upload_path)
    bootstrap = Path(request.app.state.settings.knowledge_path)
    files = [
        (item.id, directory / f"{item.id}{item.file_type}" if (directory / f"{item.id}{item.file_type}").exists() else bootstrap / item.filename, item.filename)
        for item in db.scalars(select(KnowledgeFile).where(KnowledgeFile.status == "active")).all()
        if (directory / f"{item.id}{item.file_type}").exists() or (bootstrap / item.filename).exists()
    ]
    faqs = [
        (item.id, item.question, item.answer, item.source_file_id)
        for item in db.scalars(select(FaqPair).where(FaqPair.status == "approved")).all()
    ]
    try:
        vector_service(request).rebuild(files=files, faqs=faqs)
    except VectorStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "rebuilt"}


@router.post("/files/{file_id}/generate-faq")
def generate_faq(file_id: str, request: Request, db: Session = Depends(get_db)):
    item = db.get(KnowledgeFile, file_id)
    if not item or item.status != "active":
        raise HTTPException(status_code=404, detail="知识文件不存在")
    service = getattr(request.app.state, "faq_generator", None)
    if not service:
        service = FaqGenerator()
    path = Path(request.app.state.settings.knowledge_upload_path) / f"{item.id}{item.file_type}"
    result = []
    for pair in service.generate(path):
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
        if payload.status == "approved":
            vector_service(request).add_faq(item.id, item.question, item.answer, item.source_file_id)
        else:
            vector_service(request).delete_faq(item.id)
    except VectorStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    item.status = payload.status
    item.reviewed_at = utcnow()
    db.commit()
    db.refresh(item)
    return faq_dict(item)
