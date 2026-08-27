from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class ModelRuntimeSnapshot:
    purpose: str
    model_id: str
    endpoint_id: str
    config_revision: int
    profile: Mapping

    def __post_init__(self):
        object.__setattr__(self, "profile", MappingProxyType(dict(self.profile)))
