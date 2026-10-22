from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


def deep_freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ModelRuntimeSnapshot:
    purpose: str
    model_id: str
    endpoint_id: str
    config_revision: int
    profile: Mapping

    def __post_init__(self):
        object.__setattr__(self, "profile", deep_freeze(self.profile))
