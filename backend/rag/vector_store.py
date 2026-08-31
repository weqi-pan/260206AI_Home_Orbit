from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


class VectorStoreUnavailable(RuntimeError):
    pass


class VectorStoreService:
    def __init__(self, persist_directory=None, vector_store=None, vector_store_factory=None, store_resolver=None):
        self.persist_directory = Path(persist_directory or "backend/data/chroma")
        self._vector_store = vector_store
        self._factory = vector_store_factory
        self._resolver = store_resolver
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

    def _get_store(self):
        if self._resolver:
            return self._resolver()
        if self._vector_store is not None:
            return self._vector_store
        try:
            if self._factory:
                self._vector_store = self._factory()
            else:
                raise VectorStoreUnavailable("向量库不可用：MODEL_NOT_CONFIGURED")
        except VectorStoreUnavailable:
            raise
        except Exception as exc:
            raise VectorStoreUnavailable(f"向量库不可用：{exc}") from exc
        return self._vector_store

    def add_file(self, file_id, path, filename, household_id=None, skill_version_id=None):
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            documents = [Document(page_content=page.extract_text() or "", metadata={"page": index})
                         for index, page in enumerate(PdfReader(str(path)).pages)]
        else:
            documents = [Document(page_content=path.read_text(encoding="utf-8"))]
        chunks = self.splitter.split_documents(documents)
        for chunk in chunks:
            chunk.metadata = {"source_file_id": str(file_id), "source_type": "kb", "filename": filename}
            if household_id: chunk.metadata["household_id"] = str(household_id)
            if skill_version_id: chunk.metadata["skill_version_id"] = str(skill_version_id)
        store = self._get_store()
        if skill_version_id:
            # 同一文件可在多个技能版本下各持一份副本：替换作用域与 id 均按
            # (source_file_id, skill_version_id)，重加新版本不清除旧版本副本。
            store._collection.delete(where={"$and": [{"source_file_id": str(file_id)},
                                                     {"skill_version_id": str(skill_version_id)}]})
            ids = [f"kb:{file_id}:{skill_version_id}:{index}" for index in range(len(chunks))]
        else:
            store._collection.delete(where={"source_file_id": str(file_id)})
            ids = [f"kb:{file_id}:{index}" for index in range(len(chunks))]
        store.add_documents(chunks, ids=ids)

    def delete_file(self, file_id):
        self._get_store()._collection.delete(where={"source_file_id": str(file_id)})

    def has_file(self, file_id, skill_version_id=None):
        where = {"source_file_id": str(file_id)}
        if skill_version_id:
            where = {"$and": [where, {"skill_version_id": str(skill_version_id)}]}
        result = self._get_store()._collection.get(where=where, limit=1)
        return bool(result.get("ids"))

    def add_faq(self, faq_id, question, answer, source_file_id, household_id=None, skill_version_id=None):
        metadata = {"source_file_id": str(source_file_id or ""), "source_type": "faq", "filename": "FAQ",
                    "faq_id": str(faq_id)}
        if household_id: metadata["household_id"] = str(household_id)
        if skill_version_id: metadata["skill_version_id"] = str(skill_version_id)
        store = self._get_store()
        store._collection.delete(where={"faq_id": str(faq_id)})
        store.add_documents([Document(page_content=f"Q:{question}\nA:{answer}", metadata=metadata)], ids=[f"faq:{faq_id}"])

    def delete_faq(self, faq_id):
        self._get_store()._collection.delete(where={"faq_id": str(faq_id)})

    def rebuild(self, files=(), faqs=(), household_id=None, skill_version_id=None):
        store = self._get_store()
        scope = {"source_type": {"$in": ["kb", "faq"]}}
        if household_id and skill_version_id:
            scope = {"$and": [{"household_id": str(household_id)},
                                {"skill_version_id": str(skill_version_id)}, scope]}
        elif household_id or skill_version_id:
            raise ValueError("KNOWLEDGE_SCOPE_INCOMPLETE")
        store._collection.delete(where=scope)
        for file_id, path, filename in files:
            self.add_file(file_id, path, filename, household_id=household_id, skill_version_id=skill_version_id)
        for faq_id, question, answer, source_file_id in faqs:
            self.add_faq(faq_id, question, answer, source_file_id,
                         household_id=household_id, skill_version_id=skill_version_id)

    def search(self, query, k=4, household_id=None, skill_version_ids=()):
        where = None
        if household_id:
            where = {"household_id": str(household_id)}
            if skill_version_ids:
                where = {"$and": [where, {"skill_version_id": {"$in": [str(item) for item in skill_version_ids]}}]}
        return self._get_store().similarity_search(query, k=k, filter=where) if where else self._get_store().similarity_search(query, k=k)


def configured_vector_store(session_factory, persist_directory, runtime_factory=None, embedding_builder=None, store_factory=None):
    from backend.app.db.models import ActiveEmbeddingCollection
    from backend.app.models.adapters import build_langchain_embeddings
    from backend.app.models.endpoint_security import safe_http_client
    from backend.app.models.runtime_service import ModelRuntimeService
    from backend.app.models.runtime_service import ModelRuntimeError

    def resolve_store():
        with session_factory() as session:
            pointer = session.get(ActiveEmbeddingCollection, "knowledge")
            if pointer is None:
                raise VectorStoreUnavailable("EMBEDDING_NOT_CONFIGURED")
            try:
                runtime = (runtime_factory or ModelRuntimeService)(session).resolve("embedding", require_grant=False)
            except ModelRuntimeError as exc:
                code = "EMBEDDING_NOT_CONFIGURED" if exc.code == "MODEL_NOT_CONFIGURED" else exc.code
                raise VectorStoreUnavailable(code) from exc
            if runtime.snapshot.model_id != pointer.model_id:
                raise VectorStoreUnavailable("EMBEDDING_COLLECTION_STALE")
            profile = runtime.snapshot.profile
            embeddings = embedding_builder(runtime) if embedding_builder else build_langchain_embeddings(
                api_key=runtime.api_key, model=profile["model_name"], base_url=runtime.base_url,
                dimensions=profile["embedding_dimensions"], http_client=safe_http_client(),
            )
            if store_factory:
                return store_factory(pointer.collection_name, embeddings, persist_directory)
            from langchain_chroma import Chroma
            return Chroma(collection_name=pointer.collection_name, embedding_function=embeddings,
                          persist_directory=str(persist_directory))

    return VectorStoreService(persist_directory=persist_directory, store_resolver=resolve_store)
