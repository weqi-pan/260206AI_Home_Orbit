from dataclasses import dataclass, field
from uuid import uuid4

from backend.app.db.models import ActiveEmbeddingCollection, EmbeddingCollectionBuild


@dataclass
class CollectionBuild:
    id: str
    model_id: str
    status: str = "building"
    collection_name: str | None = None


@dataclass
class InMemoryCollectionCatalog:
    active: str | None = None
    retained: list[str] = field(default_factory=list)
    builds: dict[str, CollectionBuild] = field(default_factory=dict)


class CollectionSwitchService:
    def __init__(self, catalog):
        self.catalog = catalog

    def prepare(self, model_id: str) -> CollectionBuild:
        build = CollectionBuild(str(uuid4()), model_id)
        self.catalog.builds[build.id] = build
        return build

    def complete(self, build_id: str, built_collection: str) -> CollectionBuild:
        build = self.catalog.builds[build_id]
        if self.catalog.active:
            self.catalog.retained.append(self.catalog.active)
        build.status = "active"
        build.collection_name = built_collection
        self.catalog.active = built_collection
        return build


class DatabaseCollectionSwitchService:
    def __init__(self, session, store_factory):
        self.session = session
        self.store_factory = store_factory

    def prepare(self, model_id: str) -> EmbeddingCollectionBuild:
        build = EmbeddingCollectionBuild(model_id=model_id, collection_name=f"knowledge-{uuid4().hex}", status="building")
        self.session.add(build)
        self.session.commit()
        self.session.refresh(build)
        return build

    def build(self, build_id: str, rebuild_callback, commit: bool = True) -> EmbeddingCollectionBuild:
        build = self.session.get(EmbeddingCollectionBuild, build_id)
        try:
            store = self.store_factory(build.collection_name, build.model_id)
            rebuild_callback(store)
        except Exception:
            build.status = "failed"
            self.session.commit()
            raise
        pointer = self.session.get(ActiveEmbeddingCollection, "knowledge")
        if pointer is None:
            pointer = ActiveEmbeddingCollection(key="knowledge", collection_name=build.collection_name, model_id=build.model_id)
            self.session.add(pointer)
        else:
            pointer.previous_collection_name = pointer.collection_name
            pointer.collection_name = build.collection_name
            pointer.model_id = build.model_id
        build.status = "active"
        if commit:
            self.session.commit()
            self.session.refresh(build)
        else:
            self.session.flush()
        return build

    def rebuild(self, model_id: str, rebuild_callback, commit: bool = True) -> EmbeddingCollectionBuild:
        return self.build(self.prepare(model_id).id, rebuild_callback, commit=commit)
