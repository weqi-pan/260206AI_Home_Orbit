from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeCollection:
    def __init__(self):
        self.deletes = []

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


class FakeStore:
    def __init__(self):
        self.documents = []
        self._collection = FakeCollection()

    def add_documents(self, documents, ids=None):
        self.documents.extend(documents)
        self.ids = getattr(self, "ids", []) + list(ids or [])


def test_vector_store_adds_required_metadata_and_deletes_by_ids(tmp_path: Path):
    from backend.rag.vector_store import VectorStoreService

    store = FakeStore()
    service = VectorStoreService(vector_store=store)
    path = tmp_path / "guide.txt"
    path.write_text("扫地机器人维护知识" * 100, encoding="utf-8")

    service.add_file("file-1", path, "guide.txt")
    service.add_faq(7, "如何维护？", "定期清洁。", "file-1")
    service.delete_file("file-1")
    service.delete_faq(7)

    assert store.documents[0].metadata == {"source_file_id": "file-1", "source_type": "kb", "filename": "guide.txt"}
    assert store.documents[-1].metadata == {"source_file_id": "file-1", "source_type": "faq", "filename": "FAQ", "faq_id": "7"}
    assert store.ids[-1] == "faq:7"
    assert store._collection.deletes == [
        {"where": {"source_file_id": "file-1"}},
        {"where": {"faq_id": "7"}},
        {"where": {"source_file_id": "file-1"}},
        {"where": {"faq_id": "7"}},
    ]


def test_vector_store_rebuilds_files_and_approved_faqs(tmp_path: Path):
    from backend.rag.vector_store import VectorStoreService

    store = FakeStore()
    service = VectorStoreService(vector_store=store)
    path = tmp_path / "guide.txt"
    path.write_text("维护说明", encoding="utf-8")

    service.rebuild(
        files=[("file-1", path, "guide.txt")],
        faqs=[(7, "如何维护？", "定期清洁。", "file-1")],
    )

    assert store._collection.deletes == [
        {"where": {"source_type": {"$in": ["kb", "faq"]}}},
        {"where": {"source_file_id": "file-1"}},
        {"where": {"faq_id": "7"}},
    ]
    assert [document.metadata["source_type"] for document in store.documents] == ["kb", "faq"]


def test_vector_store_has_file_checks_collection_ids():
    from backend.rag.vector_store import VectorStoreService

    class QueryableCollection(FakeCollection):
        def __init__(self, ids):
            super().__init__()
            self.ids = ids
            self.queries = []

        def get(self, **kwargs):
            self.queries.append(kwargs)
            return {"ids": self.ids}

    present = SimpleNamespace(_collection=QueryableCollection(["kb:file-1:0"]))
    service = VectorStoreService(vector_store=present)
    assert service.has_file("file-1") is True
    assert present._collection.queries == [{"where": {"source_file_id": "file-1"}, "limit": 1}]

    empty = SimpleNamespace(_collection=QueryableCollection([]))
    assert VectorStoreService(vector_store=empty).has_file("file-1") is False


def test_vector_store_scopes_has_file_by_skill_version():
    from backend.rag.vector_store import VectorStoreService

    class QueryableCollection(FakeCollection):
        def __init__(self, ids):
            super().__init__()
            self.ids = ids
            self.queries = []

        def get(self, **kwargs):
            self.queries.append(kwargs)
            return {"ids": self.ids}

    present = SimpleNamespace(_collection=QueryableCollection(["kb:file-1:sv-2:0"]))
    service = VectorStoreService(vector_store=present)
    assert service.has_file("file-1", skill_version_id="sv-2") is True
    assert present._collection.queries == [
        {"where": {"$and": [{"source_file_id": "file-1"}, {"skill_version_id": "sv-2"}]}, "limit": 1}]

    empty = SimpleNamespace(_collection=QueryableCollection([]))
    assert VectorStoreService(vector_store=empty).has_file("file-1", skill_version_id="sv-3") is False


def test_vector_store_scopes_file_copy_per_skill_version(tmp_path: Path):
    from backend.rag.vector_store import VectorStoreService

    store = FakeStore()
    service = VectorStoreService(vector_store=store)
    path = tmp_path / "guide.txt"
    path.write_text("维护说明", encoding="utf-8")

    service.add_file("file-1", path, "guide.txt", household_id="h-1", skill_version_id="sv-1")
    service.add_file("file-1", path, "guide.txt", household_id="h-1", skill_version_id="sv-2")

    # 每个技能版本一份副本：删除作用域与 id 都按 (source_file_id, skill_version_id)，
    # sv-2 的重加不得清掉 sv-1 的副本
    assert store._collection.deletes == [
        {"where": {"$and": [{"source_file_id": "file-1"}, {"skill_version_id": "sv-1"}]}},
        {"where": {"$and": [{"source_file_id": "file-1"}, {"skill_version_id": "sv-2"}]}},
    ]
    assert store.ids == ["kb:file-1:sv-1:0", "kb:file-1:sv-2:0"]
    assert [document.metadata["skill_version_id"] for document in store.documents] == ["sv-1", "sv-2"]


def test_configured_vector_store_reports_embedding_not_configured_before_store_creation():
    from backend.app.models.runtime_service import ModelRuntimeError
    from backend.rag.vector_store import VectorStoreUnavailable, configured_vector_store

    class Runtime:
        def resolve(self, purpose, require_grant=False):
            assert purpose == "embedding"
            raise ModelRuntimeError("MODEL_NOT_CONFIGURED")

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, key):
            return SimpleNamespace(collection_name="knowledge", model_id="embed-model")

    service = configured_vector_store(lambda: Session(), "unused", runtime_factory=lambda session: Runtime(),
                                      store_factory=lambda *args: (_ for _ in ()).throw(
                                          AssertionError("embedding 未配置时不得创建 store")))

    with pytest.raises(VectorStoreUnavailable) as exc:
        service.search("query")
    assert str(exc.value) == "EMBEDDING_NOT_CONFIGURED"


def test_configured_vector_store_reports_embedding_not_configured_without_active_collection():
    from backend.rag.vector_store import VectorStoreUnavailable, configured_vector_store

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, model, key):
            return None

    service = configured_vector_store(lambda: Session(), "unused",
                                      store_factory=lambda *args: (_ for _ in ()).throw(
                                          AssertionError("embedding 未配置时不得创建 store")))

    with pytest.raises(VectorStoreUnavailable) as exc:
        service.search("query")
    assert str(exc.value) == "EMBEDDING_NOT_CONFIGURED"
