from pathlib import Path

from fastapi.testclient import TestClient


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


class RecordingVectorService:
    def __init__(self):
        self.added = []
        self.known = set()

    def has_file(self, file_id):
        return file_id in self.known

    def add_file(self, file_id, path, filename):
        self.added.append((file_id, path, filename))
        self.known.add(file_id)


def _create_active_files(app, filenames):
    from backend.app.db.models import KnowledgeFile

    with app.state.database.session_factory.begin() as session:
        ids = []
        for index, name in enumerate(filenames):
            item = KnowledgeFile(filename=name, file_md5=f"{index:032x}"[-32:], file_type=Path(name).suffix)
            session.add(item)
            session.flush()
            ids.append(item.id)
    return ids


def _write_knowledge_files(settings, names):
    root = Path(settings.knowledge_path)
    paths = []
    for name in names:
        path = root / name
        path.write_text(f"{name} 的知识内容", encoding="utf-8")
        paths.append(path)
    return paths


def test_sync_knowledge_vectors_indexes_missing_files_and_is_idempotent(client, app, settings):
    from backend.app.main import sync_knowledge_vectors

    paths = _write_knowledge_files(settings, ["bootstrap-sync-a.txt", "bootstrap-sync-b.txt"])
    try:
        ids = _create_active_files(app, [path.name for path in paths])
        service = RecordingVectorService()

        sync_knowledge_vectors(app.state.database, service, settings)

        added_ids = {item[0] for item in service.added}
        assert set(ids) <= added_ids
        assert all(Path(item[1]).exists() for item in service.added)

        service.added.clear()
        sync_knowledge_vectors(app.state.database, service, settings)
        assert service.added == []
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def test_sync_knowledge_vectors_skips_records_with_missing_source_file(client, app, settings):
    from backend.app.main import sync_knowledge_vectors

    existing = _write_knowledge_files(settings, ["bootstrap-sync-keep.txt"])
    try:
        ids = _create_active_files(app, [existing[0].name, "bootstrap-sync-gone.txt"])
        service = RecordingVectorService()

        sync_knowledge_vectors(app.state.database, service, settings)

        added_ids = {item[0] for item in service.added}
        assert ids[0] in added_ids
        assert ids[1] not in added_ids
    finally:
        existing[0].unlink(missing_ok=True)


def test_sync_knowledge_vectors_warns_and_never_blocks_startup_when_unavailable(client, app, settings, caplog):
    from backend.app.main import sync_knowledge_vectors
    from backend.rag.vector_store import VectorStoreUnavailable

    class UnavailableVectorService:
        def has_file(self, file_id):
            raise VectorStoreUnavailable("向量库不可用：MODEL_NOT_CONFIGURED")

    paths = _write_knowledge_files(settings, ["bootstrap-sync-nokey.txt"])
    try:
        _create_active_files(app, [path.name for path in paths])
        with caplog.at_level("WARNING"):
            sync_knowledge_vectors(app.state.database, UnavailableVectorService(), settings)
        assert any("向量" in record.message for record in caplog.records)
    finally:
        paths[0].unlink(missing_ok=True)


def test_lifespan_runs_vector_sync_and_startup_survives_missing_key(monkeypatch, settings):
    import backend.app.main as main_module

    calls = []

    def fake_sync(database, vector_service, cfg):
        calls.append((database is not None, vector_service is not None, cfg is settings))

    monkeypatch.setattr(main_module, "sync_knowledge_vectors", fake_sync)
    with TestClient(main_module.create_app(settings)) as test_client:
        assert test_client.get("/health").json()["status"] == "ok"
    assert calls == [(True, True, True)]
