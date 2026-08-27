from pathlib import Path


class FakeCollection:
    def __init__(self):
        self.deletes = []

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


class FakeStore:
    def __init__(self):
        self.documents = []
        self._collection = FakeCollection()

    def add_documents(self, documents):
        self.documents.extend(documents)


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
    assert store._collection.deletes == [{"where": {"source_file_id": "file-1"}}, {"where": {"faq_id": "7"}}]


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

    assert store._collection.deletes == [{"where": {"source_type": {"$in": ["kb", "faq"]}}}]
    assert [document.metadata["source_type"] for document in store.documents] == ["kb", "faq"]
