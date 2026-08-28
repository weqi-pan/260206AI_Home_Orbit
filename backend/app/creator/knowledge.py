"""Phase 6 creator knowledge assets: household-scoped upload, scan and deletion.

Files are stored per household; the scan (type/size/content safety including
prompt-injection detection) runs before anything can be vectorized, and the
verdict plus reason are persisted on the asset row as auditable evidence.
"""
import hashlib
from pathlib import Path

from sqlalchemy import select

from backend.app.creator.validation import scan_injection
from backend.agent.platform.versions import UNSAFE_CONTENT
from backend.app.db.models import CreatorDraft, CreatorKnowledgeAsset

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


class KnowledgeAssetRejected(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


class CreatorKnowledgeService:
    def __init__(self, session, household_id, upload_root):
        self.session = session
        self.household_id = household_id
        self.upload_root = Path(upload_root)

    def _draft(self, draft_id) -> CreatorDraft:
        draft = self.session.scalar(select(CreatorDraft).where(
            CreatorDraft.id == draft_id, CreatorDraft.household_id == self.household_id))
        if draft is None:
            raise KnowledgeAssetRejected("DRAFT_NOT_FOUND")
        return draft

    def asset_path(self, asset: CreatorKnowledgeAsset) -> Path:
        return self.upload_root / "creator" / self.household_id / f"{asset.id}{asset.file_type}"

    def list_assets(self, draft_id) -> list[CreatorKnowledgeAsset]:
        self._draft(draft_id)
        return list(self.session.scalars(select(CreatorKnowledgeAsset).where(
            CreatorKnowledgeAsset.draft_id == draft_id,
            CreatorKnowledgeAsset.household_id == self.household_id,
            CreatorKnowledgeAsset.status == "active").order_by(CreatorKnowledgeAsset.created_at)).all())

    def _extract_text(self, suffix: str, content: bytes) -> str:
        if suffix == ".pdf":
            import io

            from pypdf import PdfReader

            try:
                reader = PdfReader(io.BytesIO(content))
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception as exc:
                raise KnowledgeAssetRejected("KNOWLEDGE_CONTENT_UNREADABLE") from exc
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KnowledgeAssetRejected("KNOWLEDGE_CONTENT_UNREADABLE") from exc

    def _scan_text(self, text: str) -> str | None:
        label = scan_injection(text)
        if label:
            return label
        if UNSAFE_CONTENT.search(text):
            return "unsafe_content"
        return None

    def register_asset(self, draft_id, filename, suffix, content: bytes, skill_key="",
                       uploaded_by=None) -> CreatorKnowledgeAsset:
        self._draft(draft_id)
        suffix = suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise KnowledgeAssetRejected("KNOWLEDGE_TYPE_UNSUPPORTED")
        if len(content) > MAX_UPLOAD_SIZE:
            raise KnowledgeAssetRejected("KNOWLEDGE_TOO_LARGE")
        if len(content) == 0:
            raise KnowledgeAssetRejected("KNOWLEDGE_EMPTY")
        md5 = hashlib.md5(content).hexdigest()
        duplicate = self.session.scalar(select(CreatorKnowledgeAsset).where(
            CreatorKnowledgeAsset.draft_id == draft_id,
            CreatorKnowledgeAsset.household_id == self.household_id,
            CreatorKnowledgeAsset.file_md5 == md5,
            CreatorKnowledgeAsset.status == "active"))
        if duplicate is not None:
            raise KnowledgeAssetRejected("KNOWLEDGE_DUPLICATE")

        # uq_creator_asset_draft_md5 覆盖软删行：同 (draft, md5) 已存在时复活该行并重扫，
        # 避免插入撞唯一约束以未处理 500 逃逸。
        asset = self.session.scalar(select(CreatorKnowledgeAsset).where(
            CreatorKnowledgeAsset.draft_id == draft_id,
            CreatorKnowledgeAsset.household_id == self.household_id,
            CreatorKnowledgeAsset.file_md5 == md5))
        if asset is not None:
            asset.status = "active"
            asset.scan_status = "pending"
            asset.scan_reason = None
            asset.skill_key = skill_key[:100]
            asset.filename = Path(filename).name[:255]
            asset.file_type = suffix
            asset.content_hash = hashlib.sha256(content).hexdigest()
            asset.size_bytes = len(content)
            asset.uploaded_by_account_id = uploaded_by
            self.session.flush()
        else:
            asset = CreatorKnowledgeAsset(household_id=self.household_id, draft_id=draft_id,
                                          skill_key=skill_key[:100], filename=Path(filename).name[:255],
                                          file_type=suffix, file_md5=md5,
                                          content_hash=hashlib.sha256(content).hexdigest(),
                                          size_bytes=len(content), status="active", scan_status="pending",
                                          uploaded_by_account_id=uploaded_by)
            self.session.add(asset)
            self.session.flush()

        text = self._extract_text(suffix, content)
        reason = self._scan_text(text)
        if reason:
            asset.scan_status = "rejected"
            asset.scan_reason = f"prompt_injection:{reason}"[:300]
            self._write_file(asset, content)
            self.session.commit()
            raise KnowledgeAssetRejected("KNOWLEDGE_SCAN_REJECTED",
                                         detail=f"prompt_injection:{reason}")
        asset.scan_status = "passed"
        asset.scan_reason = None
        self._write_file(asset, content)
        self.session.commit()
        return asset

    def _write_file(self, asset: CreatorKnowledgeAsset, content: bytes) -> None:
        path = self.asset_path(asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get_asset(self, draft_id, asset_id) -> CreatorKnowledgeAsset:
        self._draft(draft_id)
        asset = self.session.get(CreatorKnowledgeAsset, asset_id)
        if asset is None or asset.household_id != self.household_id or asset.draft_id != draft_id:
            raise KnowledgeAssetRejected("KNOWLEDGE_ASSET_NOT_FOUND")
        return asset

    def delete_asset(self, draft_id, asset_id, vector_service=None) -> CreatorKnowledgeAsset:
        asset = self.get_asset(draft_id, asset_id)
        if asset.status != "active":
            raise KnowledgeAssetRejected("KNOWLEDGE_ASSET_NOT_FOUND")
        if vector_service is not None and asset.scan_status == "passed":
            vector_service.delete_file(asset.id)
        asset.status = "deleted"
        self.session.commit()
        path = self.asset_path(asset)
        path.unlink(missing_ok=True)
        return asset
