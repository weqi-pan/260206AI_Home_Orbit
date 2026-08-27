from dataclasses import dataclass, field
from uuid import uuid4


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
