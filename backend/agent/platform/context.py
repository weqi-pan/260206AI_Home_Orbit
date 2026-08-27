from dataclasses import dataclass, field
from threading import Lock
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
    deadline_at: str | None = None
    child_scopes: list = field(default_factory=list, repr=False)


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
            next_value = self.__state.values["tool_calls"] + 1
            if next_value > self.max_tool_calls or any(
                    next_value - scope["tool_calls"] > scope["max_tool_calls"]
                    for scope in self.__state.child_scopes):
                raise RuntimeError("RUN_LIMIT_EXCEEDED")
            self.__state.values["tool_calls"] = next_value

    def consume_tokens(self, count):
        self.check_time()
        with self.__state.lock:
            next_value = self.__state.values["tokens"] + max(0, int(count))
            if next_value > self.max_tokens or any(
                    next_value - scope["tokens"] > scope["max_tokens"]
                    for scope in self.__state.child_scopes):
                raise RuntimeError("RUN_LIMIT_EXCEEDED")
            self.__state.values["tokens"] = next_value

    def enter_delegation(self):
        self.check_time()
        with self.__state.lock:
            next_depth = self.__state.values["delegation_depth"] + 1
            if next_depth > self.max_delegation_depth or any(
                    next_depth - scope["delegation_depth"] > scope["max_delegation_depth"]
                    for scope in self.__state.child_scopes):
                raise RuntimeError("RUN_LIMIT_EXCEEDED")
            self.__state.values["delegation_depth"] = next_depth
            self.__state.values["delegations"] = self.__state.values.get("delegations", 0) + 1

    def exit_delegation(self):
        with self.__state.lock:
            self.__state.values["delegation_depth"] = max(0, self.__state.values["delegation_depth"] - 1)

    def snapshot(self):
        with self.__state.lock:
            return _freeze({**self.__state.values, "deadline_at": self.__state.deadline_at})

    @classmethod
    def restore(cls, limits, counters):
        values = {"delegation_depth": 0, "tool_calls": 0, "tokens": 0, **dict(counters)}
        deadline = values.pop("deadline_at", None)
        state = _BudgetState(values=values, deadline_at=deadline)
        budget = cls(max_delegation_depth=limits.get("delegation_depth", 4),
                     max_tool_calls=limits.get("tool_calls", 16), max_tokens=limits.get("tokens", 32000),
                     wall_time_seconds=limits.get("wall_time_seconds", 120))
        object.__setattr__(budget, "_RunBudget__state", state)
        return budget

    def check_time(self):
        from backend.app.db.base import utcnow
        from datetime import datetime, timedelta
        with self.__state.lock:
            if self.__state.deadline_at is None:
                self.__state.deadline_at = (utcnow() + timedelta(seconds=self.wall_time_seconds)).isoformat()
            deadline = datetime.fromisoformat(self.__state.deadline_at)
            child_deadlines = [scope["deadline_at"] for scope in self.__state.child_scopes]
        now = utcnow()
        if now >= deadline or any(now >= item for item in child_deadlines):
            raise RuntimeError("RUN_LIMIT_EXCEEDED")

    def enforce_child_limits(self, limits):
        return ChildBudgetScope(self, limits)


class ChildBudgetScope:
    def __init__(self, parent, limits): self.parent, self.limits = parent, limits
    def __enter__(self):
        from backend.app.db.base import utcnow
        from datetime import timedelta
        self.parent.enter_delegation()
        state = self.parent._RunBudget__state
        with state.lock:
            self.scope = {
                "tool_calls": state.values["tool_calls"],
                "tokens": state.values["tokens"],
                "delegation_depth": state.values["delegation_depth"] - 1,
                "max_tool_calls": int(self.limits.get("tool_calls", self.parent.max_tool_calls)),
                "max_tokens": int(self.limits.get("tokens", self.parent.max_tokens)),
                "max_delegation_depth": int(self.limits.get("delegation_depth", self.parent.max_delegation_depth)),
                "deadline_at": utcnow() + timedelta(seconds=int(
                    self.limits.get("wall_time_seconds", self.parent.wall_time_seconds))),
            }
            state.child_scopes.append(self.scope)
        return self.parent
    def __exit__(self, exc_type, exc, traceback):
        limit_error = None
        if exc_type is None:
            try: self.parent.check_time()
            except RuntimeError as error: limit_error = error
        state = self.parent._RunBudget__state
        with state.lock:
            if self.scope in state.child_scopes: state.child_scopes.remove(self.scope)
        self.parent.exit_delegation()
        if limit_error: raise limit_error


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
