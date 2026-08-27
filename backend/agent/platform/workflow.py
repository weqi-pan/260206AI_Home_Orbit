ALLOWED_NODES = {"route", "delegate", "call_tool", "condition", "confirm", "respond", "parallel", "end"}


class WorkflowRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_workflow(workflow: dict | None, max_nodes: int = 64) -> dict | None:
    if not workflow:
        return workflow
    nodes = workflow.get("nodes", [])
    if len(nodes) > max_nodes:
        raise WorkflowRejected("RUN_LIMIT_EXCEEDED")
    ids = {node.get("id") for node in nodes}
    for node in nodes:
        if node.get("type") not in ALLOWED_NODES or "expression" in node or "script" in node:
            raise WorkflowRejected("WORKFLOW_INVALID")
        if node.get("next") == node.get("id") or (node.get("next") and node.get("next") not in ids):
            raise WorkflowRejected("WORKFLOW_INVALID")
    return workflow
