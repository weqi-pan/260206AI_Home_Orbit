from dataclasses import dataclass
from typing import Any, Mapping


class FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("RunContext mappings are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class RunContext:
    principal_id: str
    household_id: str
    member_id: str
    role: str
    session_id: str
    deployment_id: str
    agent_version_id: str
    run_id: str
    timezone: str
    grants: Mapping[str, Any]
    consent: Mapping[str, Any]
    deployment_revision: int = 1
    model_id: str = "test-model"
    model_revision: int = 1
    skill_version_ids: tuple[str, ...] = ()
    user_id: str | None = None
    city: str | None = None
    repositories: Any = None
    vector_store_service: Any = None
    chat_model: Any = None
    model_resolver: Any = None

    def __post_init__(self):
        object.__setattr__(self, "grants", _freeze(self.grants))
        object.__setattr__(self, "consent", _freeze(self.consent))
        object.__setattr__(self, "skill_version_ids", tuple(self.skill_version_ids))
