from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from pydantic import BaseModel, ConfigDict


class UnknownToolKey(KeyError):
    pass


@dataclass(frozen=True)
class ToolMetadata:
    key: str
    display_name: str
    description: str
    risk_level: str
    version: str = "1"


@dataclass(frozen=True)
class BuiltinTool:
    metadata: ToolMetadata
    handler: Callable[[dict[str, Any], Any], Any]
    args_schema: type[BaseModel]


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HighRiskTestArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


def _current_month(_arguments, _context):
    return datetime.now().strftime("%Y-%m")


def _test_high_risk(arguments, _context):
    return {"accepted": arguments}


def _context_probe(_arguments, context):
    return f"{context.household_id}|{context.principal_id}|{context.agent_version_id}|{sorted(context.grants)}"


class BuiltinToolRegistry:
    def __init__(self, tools: list[BuiltinTool]):
        self._tools = {tool.metadata.key: tool for tool in tools}

    @classmethod
    def default(cls):
        return cls([
            BuiltinTool(ToolMetadata("platform.current_month", "当前月份", "读取当前月份", "low"), _current_month, NoArguments),
            BuiltinTool(ToolMetadata("platform.test_high_risk", "测试高风险动作", "确定性离线副作用测试工具", "high"),
                        _test_high_risk, HighRiskTestArguments),
            BuiltinTool(ToolMetadata("platform.context_probe", "上下文探针", "读取当前运行的非敏感身份快照", "low"),
                        _context_probe, NoArguments),
        ])

    def resolve(self, key: str) -> BuiltinTool:
        try:
            return self._tools[key]
        except KeyError as exc:
            raise UnknownToolKey(key) from exc

    def metadata(self):
        return tuple(tool.metadata for tool in self._tools.values())
