import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from backend.app.db.models import FaqPair, KnowledgeFile


class EmbeddingSourceIncomplete(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__("EMBEDDING_SOURCE_INCOMPLETE")


class EmbeddingSourceSnapshotStale(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        self.code = "EMBEDDING_SOURCE_SNAPSHOT_STALE"
        super().__init__(self.code)


@dataclass(frozen=True)
class EmbeddingSourceSnapshot:
    token: str
    files: list[tuple]
    faqs: list[tuple]
    evidence: dict


def _token(records) -> str:
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capture_embedding_sources(session, settings, path_resolver, lock=False) -> EmbeddingSourceSnapshot:
    file_query = select(KnowledgeFile).where(KnowledgeFile.status == "active").order_by(KnowledgeFile.id)
    faq_query = select(FaqPair).where(FaqPair.status == "approved").order_by(FaqPair.id)
    if lock:
        file_query = file_query.with_for_update()
        faq_query = faq_query.with_for_update()
    active = session.scalars(file_query).all()
    approved = session.scalars(faq_query).all()
    files = []
    missing_ids = []
    file_records = []
    for item in active:
        path = path_resolver(settings, item)
        digest = None
        try:
            supported = path and path.is_file() and path.suffix.lower() in {".pdf", ".txt"}
            if supported:
                hasher = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        hasher.update(block)
                digest = hasher.hexdigest()
        except (OSError, PermissionError):
            supported = False
        if supported:
            files.append((item.id, path, item.filename))
            file_records.append({"id": item.id, "file_md5": item.file_md5, "content_sha256": digest})
        else:
            missing_ids.append(item.id)
    faq_records = [{
        "id": item.id,
        "question_sha256": hashlib.sha256(item.question.encode("utf-8")).hexdigest(),
        "answer_sha256": hashlib.sha256(item.answer.encode("utf-8")).hexdigest(),
        "source_file_id": item.source_file_id,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
    } for item in approved]
    faqs = [(item.id, item.question, item.answer, item.source_file_id) for item in approved]
    records = {"files": file_records, "faqs": faq_records}
    token = _token(records)
    evidence = {
        "expected_file_count": len(active), "included_file_count": len(files),
        "expected_faq_count": len(approved), "included_faq_count": len(faqs),
        "included_file_ids": sorted(item[0] for item in files), "missing_file_ids": sorted(missing_ids),
        "included_faq_ids": sorted(item[0] for item in faqs), "source_snapshot_token": token,
    }
    if missing_ids:
        raise EmbeddingSourceIncomplete(evidence)
    return EmbeddingSourceSnapshot(token, files, faqs, evidence)
