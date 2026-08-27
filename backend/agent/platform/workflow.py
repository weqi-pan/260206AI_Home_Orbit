from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class WorkflowRejected(ValueError):
    def __init__(self, code): self.code = code; super().__init__(code)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=100)
    next: str | None = None


class RouteNode(Node):
    type: Literal["route"]
    field: Literal["message", "intent", "route"]
    equals: str
    then: str
    else_: str = Field(alias="else")


class DelegateNode(Node):
    type: Literal["delegate"]
    subagent: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


class CallToolNode(Node):
    type: Literal["call_tool"]
    tool: str = Field(pattern=r"^platform\.[a-z0-9_]+$")
    arguments: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ConditionNode(Node):
    type: Literal["condition"]
    field: Literal["message", "intent", "route"]
    equals: str | int | float | bool
    then: str
    else_: str = Field(alias="else")


class ConfirmNode(Node):
    type: Literal["confirm"]
    message: str = Field(min_length=1, max_length=1000)
    action: Literal["continue"]
    tool: Literal["platform.workflow_confirm"]


class RespondNode(Node):
    type: Literal["respond"]
    text: str = Field(min_length=1, max_length=5000)


class ParallelNode(Node):
    type: Literal["parallel"]
    branches: list[str] = Field(min_length=1, max_length=8)


class EndNode(Node):
    type: Literal["end"]
    next: None = None


WorkflowNode = Annotated[Union[RouteNode, DelegateNode, CallToolNode, ConditionNode, ConfirmNode,
                               RespondNode, ParallelNode, EndNode], Field(discriminator="type")]
NODE_ADAPTER = TypeAdapter(WorkflowNode)


def _edges(node):
    result = []
    for key in ("next", "then", "else"):
        if node.get(key): result.append(node[key])
    result.extend(node.get("branches", [])); return result


def validate_workflow(workflow, max_nodes=64, max_parallel=8):
    if not workflow: return workflow
    if not isinstance(workflow, dict) or set(workflow) != {"entry", "nodes"} or not isinstance(workflow["entry"], str):
        raise WorkflowRejected("WORKFLOW_INVALID")
    if not isinstance(workflow["nodes"], list) or not workflow["nodes"] or len(workflow["nodes"]) > max_nodes:
        raise WorkflowRejected("RUN_LIMIT_EXCEEDED")
    try:
        nodes = [NODE_ADAPTER.validate_python(node).model_dump(by_alias=True, exclude_none=True) for node in workflow["nodes"]]
    except ValidationError as exc: raise WorkflowRejected("WORKFLOW_INVALID") from exc
    ids = [node["id"] for node in nodes]
    if len(set(ids)) != len(ids) or workflow["entry"] not in ids: raise WorkflowRejected("WORKFLOW_INVALID")
    by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        if node["type"] == "parallel":
            if len(node["branches"]) > max_parallel or any(by_id.get(branch, {}).get("type") != "respond" for branch in node["branches"]):
                raise WorkflowRejected("WORKFLOW_INVALID")
        if any(edge not in by_id for edge in _edges(node)): raise WorkflowRejected("WORKFLOW_INVALID")
    visiting, visited = set(), set()
    def visit(item):
        if item in visiting: raise WorkflowRejected("WORKFLOW_INVALID")
        if item in visited: return
        visiting.add(item)
        for edge in _edges(by_id[item]): visit(edge)
        visiting.remove(item); visited.add(item)
    visit(workflow["entry"])
    return {"entry": workflow["entry"], "nodes": nodes}


class WorkflowExecutor:
    def __init__(self, call_tool, delegate=None, confirm=None, max_steps=64, budget=None):
        self.call_tool, self.delegate, self.confirm = call_tool, delegate, confirm
        self.max_steps, self.budget = max_steps, budget
    def run(self, workflow, inputs):
        safe = validate_workflow(workflow); nodes = {node["id"]: node for node in safe["nodes"]}
        current = safe["entry"]; steps = 0
        while current:
            steps += 1
            if steps > self.max_steps: raise WorkflowRejected("RUN_LIMIT_EXCEEDED")
            node = nodes[current]; kind = node["type"]
            if kind == "end": return
            if kind in {"route", "condition"}:
                current = node["then"] if inputs.get(node["field"]) == node["equals"] else node["else"]; continue
            if kind == "call_tool":
                if self.budget: self.budget.consume_tool()
                result = self.call_tool(node["tool"], node.get("arguments", {}), node["id"])
                yield {"event": "tool", "data": {"name": node["tool"], "result": result}}
                yield {"event": "token", "data": {"content": str(result)}}
            elif kind == "respond": yield {"event": "token", "data": {"content": node["text"]}}
            elif kind == "delegate":
                if not self.delegate: raise WorkflowRejected("WORKFLOW_INVALID")
                yield from self.delegate(node)
            elif kind == "confirm":
                if not self.confirm: raise WorkflowRejected("WORKFLOW_INVALID")
                if self.budget: self.budget.consume_tool()
                yield from (self.confirm(node) or ())
            elif kind == "parallel":
                for branch in node["branches"]:
                    if self.budget: self.budget.consume_tokens(max(1, len(nodes[branch]["text"]) // 4))
                    yield {"event": "token", "data": {"content": nodes[branch]["text"]}}
            current = node.get("next")
