from dataclasses import dataclass


ALLOWED_NODES = {"route", "delegate", "call_tool", "condition", "confirm", "respond", "parallel", "end"}
COMMON_FIELDS = {"id", "type", "next"}
NODE_FIELDS = {
    "route": COMMON_FIELDS,
    "delegate": COMMON_FIELDS | {"subagent", "message"},
    "call_tool": COMMON_FIELDS | {"tool", "arguments"},
    "condition": COMMON_FIELDS | {"field", "equals", "then", "else"},
    "confirm": COMMON_FIELDS | {"message"},
    "respond": COMMON_FIELDS | {"text"},
    "parallel": COMMON_FIELDS | {"branches"},
    "end": {"id", "type"},
}


class WorkflowRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _edges(node):
    values = []
    for key in ("next", "then", "else"):
        if node.get(key): values.append(node[key])
    values.extend(node.get("branches", []))
    return values


def validate_workflow(workflow: dict | None, max_nodes: int = 64, max_parallel: int = 8) -> dict | None:
    if not workflow:
        return workflow
    if not isinstance(workflow, dict) or set(workflow) - {"entry", "nodes"}:
        raise WorkflowRejected("WORKFLOW_INVALID")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes or len(nodes) > max_nodes:
        raise WorkflowRejected("RUN_LIMIT_EXCEEDED" if isinstance(nodes, list) and len(nodes) > max_nodes else "WORKFLOW_INVALID")
    ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(ids) != len(nodes) or len(set(ids)) != len(ids) or any(not isinstance(item, str) or not item for item in ids):
        raise WorkflowRejected("WORKFLOW_INVALID")
    by_id = {node["id"]: node for node in nodes}
    entry = workflow.get("entry", ids[0])
    if entry not in by_id:
        raise WorkflowRejected("WORKFLOW_INVALID")
    for node in nodes:
        node_type = node.get("type")
        if node_type not in ALLOWED_NODES or set(node) - NODE_FIELDS[node_type]:
            raise WorkflowRejected("WORKFLOW_INVALID")
        if node_type == "parallel" and (not isinstance(node.get("branches"), list) or len(node["branches"]) > max_parallel):
            raise WorkflowRejected("RUN_LIMIT_EXCEEDED")
        if any(edge not in by_id for edge in _edges(node)):
            raise WorkflowRejected("WORKFLOW_INVALID")
    visiting, visited = set(), set()
    def visit(node_id):
        if node_id in visiting: raise WorkflowRejected("WORKFLOW_INVALID")
        if node_id in visited: return
        visiting.add(node_id)
        for target in _edges(by_id[node_id]): visit(target)
        visiting.remove(node_id); visited.add(node_id)
    visit(entry)
    return {"entry": entry, "nodes": nodes}


class WorkflowExecutor:
    def __init__(self, call_tool, delegate=None, confirm=None, max_steps=64, budget=None):
        self.call_tool = call_tool
        self.delegate = delegate
        self.confirm = confirm
        self.max_steps = max_steps
        self.budget = budget

    def run(self, workflow, inputs):
        safe = validate_workflow(workflow)
        nodes = {node["id"]: node for node in safe["nodes"]}
        current = safe["entry"]
        steps = 0
        while current:
            steps += 1
            if steps > self.max_steps: raise WorkflowRejected("RUN_LIMIT_EXCEEDED")
            node = nodes[current]; kind = node["type"]
            if kind == "end": return
            if kind in {"route", "condition"}:
                current = node.get("then") if inputs.get(node.get("field")) == node.get("equals") else node.get("else")
                current = current or node.get("next")
                continue
            if kind == "call_tool":
                if self.budget: self.budget.consume_tool()
                result = self.call_tool(node["tool"], node.get("arguments", {}), node["id"])
                yield {"event": "tool", "data": {"name": node["tool"], "result": result}}
                yield {"event": "token", "data": {"content": str(result)}}
            elif kind == "respond":
                yield {"event": "token", "data": {"content": node.get("text", "")}}
            elif kind == "delegate":
                if not self.delegate: raise WorkflowRejected("WORKFLOW_INVALID")
                if self.budget: self.budget.enter_delegation()
                try: yield from self.delegate(node)
                finally:
                    if self.budget: self.budget.exit_delegation()
            elif kind == "confirm":
                if not self.confirm: raise WorkflowRejected("WORKFLOW_INVALID")
                yield from (self.confirm(node) or ())
            elif kind == "parallel":
                for branch in node["branches"]:
                    branch_workflow = {"entry": branch, "nodes": safe["nodes"]}
                    yield from WorkflowExecutor(self.call_tool, self.delegate, self.confirm, self.max_steps,
                                                self.budget).run(branch_workflow, inputs)
            current = node.get("next")
