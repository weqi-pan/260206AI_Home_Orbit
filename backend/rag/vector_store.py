from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


class VectorStoreUnavailable(RuntimeError):
    pass


class VectorStoreService:
    def __init__(self, persist_directory=None, vector_store=None, vector_store_factory=None):
        self.persist_directory = Path(persist_directory or "backend/data/chroma")
        self._vector_store = vector_store
        self._factory = vector_store_factory
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

    def _get_store(self):
        if self._vector_store is not None:
            return self._vector_store
        try:
            if self._factory:
                self._vector_store = self._factory()
            else:
                import os
                if not os.getenv("DASHSCOPE_API_KEY"):
                    raise VectorStoreUnavailable("向量库不可用：未配置 DASHSCOPE_API_KEY")
                from langchain_chroma import Chroma
                from langchain_community.embeddings import DashScopeEmbeddings
                self.persist_directory.mkdir(parents=True, exist_ok=True)
                self._vector_store = Chroma(collection_name="customer-service", embedding_function=DashScopeEmbeddings(), persist_directory=str(self.persist_directory))
        except VectorStoreUnavailable:
            raise
        except Exception as exc:
            raise VectorStoreUnavailable(f"向量库不可用：{exc}") from exc
        return self._vector_store

    def add_file(self, file_id, path, filename):
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            from langchain_community.document_loaders import PyPDFLoader
            documents = PyPDFLoader(str(path)).load()
        else:
            documents = [Document(page_content=path.read_text(encoding="utf-8"))]
        chunks = self.splitter.split_documents(documents)
        for chunk in chunks:
            chunk.metadata = {"source_file_id": str(file_id), "source_type": "kb", "filename": filename}
        self._get_store().add_documents(chunks)

    def delete_file(self, file_id):
        self._get_store()._collection.delete(where={"source_file_id": str(file_id)})

    def add_faq(self, faq_id, question, answer, source_file_id):
        metadata = {"source_file_id": str(source_file_id or ""), "source_type": "faq", "filename": "FAQ", "faq_id": str(faq_id)}
        self._get_store().add_documents([Document(page_content=f"Q:{question}\nA:{answer}", metadata=metadata)])

    def delete_faq(self, faq_id):
        self._get_store()._collection.delete(where={"faq_id": str(faq_id)})

    def rebuild(self, files=(), faqs=()):
        store = self._get_store()
        store._collection.delete(where={"source_type": {"$in": ["kb", "faq"]}})
        for file_id, path, filename in files:
            self.add_file(file_id, path, filename)
        for faq_id, question, answer, source_file_id in faqs:
            self.add_faq(faq_id, question, answer, source_file_id)

    def search(self, query, k=4):
        return self._get_store().similarity_search(query, k=k)
