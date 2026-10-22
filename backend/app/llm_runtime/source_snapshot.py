import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    staging_path: Path | None = None


def _token(records) -> str:
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capture_embedding_sources(session, settings, path_resolver, lock=False, staging_root=None) -> EmbeddingSourceSnapshot:
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
    staging_path = None
    if staging_root is not None:
        root = Path(staging_root)
        root.mkdir(parents=True, exist_ok=True)
        staging_path = Path(tempfile.mkdtemp(prefix="embedding-build-capturing-", dir=root))
    try:
        for index, item in enumerate(active):
            path = path_resolver(settings, item)
            digest = None
            try:
                supported = path and path.is_file() and path.suffix.lower() in {".pdf", ".txt"}
                if supported:
                    hasher = hashlib.sha256()
                    captured_path = staging_path / f"{index}{path.suffix.lower()}" if staging_path else path
                    with path.open("rb") as source:
                        if staging_path:
                            with captured_path.open("xb") as captured:
                                for block in iter(lambda: source.read(1024 * 1024), b""):
                                    hasher.update(block)
                                    captured.write(block)
                        else:
                            for block in iter(lambda: source.read(1024 * 1024), b""):
                                hasher.update(block)
                    digest = hasher.hexdigest()
            except (OSError, PermissionError):
                supported = False
            if supported:
                files.append((item.id, captured_path, item.filename))
                file_records.append({"id": item.id, "file_md5": item.file_md5, "content_sha256": digest})
            else:
                missing_ids.append(item.id)
    except Exception:
        if staging_path:
            shutil.rmtree(staging_path, ignore_errors=True)
        raise
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
        if staging_path:
            shutil.rmtree(staging_path, ignore_errors=True)
        raise EmbeddingSourceIncomplete(evidence)
    if staging_path:
        try:
            token_path = staging_path.with_name(f"embedding-build-{token}-{staging_path.name.rsplit('-', 1)[-1]}")
            staging_path.rename(token_path)
        except Exception:
            shutil.rmtree(staging_path, ignore_errors=True)
            raise
        files = [(file_id, token_path / path.name, filename) for file_id, path, filename in files]
        staging_path = token_path
    return EmbeddingSourceSnapshot(token, files, faqs, evidence, staging_path)


def cleanup_embedding_sources(snapshot: EmbeddingSourceSnapshot | None) -> None:
    if snapshot and snapshot.staging_path:
        shutil.rmtree(snapshot.staging_path, ignore_errors=True)
