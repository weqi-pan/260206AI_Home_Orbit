from dataclasses import dataclass, field
from threading import Lock
import time
from typing import Mapping, Protocol, runtime_checkable


class FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("RunContext mappings are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable


def _freeze(value):
    if isinstance(value, Mapping):
        return FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


@runtime_checkable
class RepositoryCapability(Protocol):
    def search_devices(self, keyword): ...


@runtime_checkable
class KnowledgeCapability(Protocol):
    def search(self, query): ...


@dataclass
class _BudgetState:
    values: dict = field(default_factory=lambda: {"delegation_depth": 0, "tool_calls": 0, "tokens": 0})
    lock: object = field(default_factory=Lock, repr=False)
    started_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class RunBudget:
    max_delegation_depth: int = 4
    max_tool_calls: int = 16
    max_tokens: int = 32000
    wall_time_seconds: int = 120
    __state: _BudgetState = field(default_factory=_BudgetState, repr=False)

    def consume_tool(self):
        self.check_time()
        with self.__state.lock:
            self.__state.values["tool_calls"] += 1
            if self.__state.values["tool_calls"] > self.max_tool_calls: raise RuntimeError("RUN_LIMIT_EXCEEDED")

    def consume_tokens(self, count):
        self.check_time()
        with self.__state.lock:
            self.__state.values["tokens"] += max(0, int(count))
            if self.__state.values["tokens"] > self.max_tokens: raise RuntimeError("RUN_LIMIT_EXCEEDED")

    def enter_delegation(self):
        self.check_time()
        with self.__state.lock:
            next_depth = self.__state.values["delegation_depth"] + 1
            if next_depth > self.max_delegation_depth: raise RuntimeError("RUN_LIMIT_EXCEEDED")
            self.__state.values["delegation_depth"] = next_depth
            self.__state.values["delegations"] = self.__state.values.get("delegations", 0) + 1

    def exit_delegation(self):
        with self.__state.lock:
            self.__state.values["delegation_depth"] = max(0, self.__state.values["delegation_depth"] - 1)

    def snapshot(self):
        with self.__state.lock:
            return _freeze(dict(self.__state.values))

    @classmethod
    def restore(cls, limits, counters):
        state = _BudgetState(values={"delegation_depth": 0, "tool_calls": 0, "tokens": 0, **dict(counters)})
        budget = cls(max_delegation_depth=limits.get("delegation_depth", 4),
                     max_tool_calls=limits.get("tool_calls", 16), max_tokens=limits.get("tokens", 32000),
                     wall_time_seconds=limits.get("wall_time_seconds", 120))
        object.__setattr__(budget, "_RunBudget__state", state)
        return budget

    def check_time(self):
        if time.monotonic() - self.__state.started_at > self.wall_time_seconds:
            raise RuntimeError("RUN_LIMIT_EXCEEDED")


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
    grants: Mapping[str, Mapping[str, str]]
    consent: Mapping[str, str]
    deployment_revision: int = 1
    model_id: str = "test-model"
    model_revision: int = 1
    model_snapshot: Mapping[str, object] = field(default_factory=dict)
    compiled_snapshot: Mapping[str, object] = field(default_factory=dict)
    skill_version_ids: tuple[str, ...] = ()
    user_id: str | None = None
    city: str | None = None
    repositories: RepositoryCapability | None = None
    vector_store_service: KnowledgeCapability | None = None
    budget: RunBudget = field(default_factory=RunBudget)

    def __post_init__(self):
        object.__setattr__(self, "grants", _freeze(self.grants))
        object.__setattr__(self, "consent", _freeze(self.consent))
        object.__setattr__(self, "skill_version_ids", tuple(self.skill_version_ids))
        object.__setattr__(self, "model_snapshot", _freeze(self.model_snapshot))
        object.__setattr__(self, "compiled_snapshot", _freeze(self.compiled_snapshot))
