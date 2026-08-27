from backend.app.db.base import utcnow
import json


def persist_run_outcome(session, run_id, status, error_code=None):
    from backend.app.db.models import AgentRun
    run = session.get(AgentRun, run_id)
    if run:
        run.status = status; run.error_code = error_code; run.finished_at = utcnow(); session.commit()
    return run


class PlatformAgentRunner:
    """Deployment-aware runner preserving the V1 stream event boundary."""

    def __init__(self, compiler):
        self.compiler = compiler

    def stream(self, messages, context):
        yield {"event": "run", "data": {"run_id": context.run_id, "deployment_id": context.deployment_id,
                                          "agent_version_id": context.agent_version_id, "status": "started"}}
        try:
            compiled = self.compiler(context)
            for event in compiled.stream(messages, context):
                yield event
        except Exception as exc:
            if getattr(exc, "code", None) == "APPROVAL_REQUIRED":
                yield {"event": "approval_required", "data": {"run_id": context.run_id,
                                                                 "approval_id": getattr(exc, "approval_id", None),
                                                                 "code": "APPROVAL_REQUIRED"}}
                return
            yield {"event": "run", "data": {"run_id": context.run_id, "status": "failed"}}
            raise
        yield {"event": "run", "data": {"run_id": context.run_id, "status": "completed"}}


class DeploymentAgentRunner(PlatformAgentRunner):
    def __init__(self, session_factory, checkpointer=None):
        self.session_factory = session_factory
        self._models = {}
        if checkpointer is None:
            session = session_factory()
            try:
                from backend.agent.platform.checkpointer import checkpoint_saver_for
                checkpointer = checkpoint_saver_for(session.bind, session_factory)
            finally:
                session.close()
        self.checkpointer = checkpointer
        super().__init__(self._compile)

    def bind_model(self, run_id, model): self._models[run_id] = model
    def model_for(self, run_id): return self._models.get(run_id)

    def _compile_agent(self, context, session):
        from backend.agent.platform.compiler import AgentCompiler
        return AgentCompiler(session, checkpointer=self.checkpointer,
                             model_factory=lambda _deployment: self.model_for(context.run_id)).compile(
                                 context.deployment_id, context)

    def _compile(self, context):
        session = self.session_factory()
        try:
            compiled = self._compile_agent(context, session)
        except Exception:
            session.close()
            raise
        class SessionBoundCompiled:
            def persist_budget(self):
                from backend.app.db.models import AgentRun
                run = session.get(AgentRun, context.run_id)
                if run:
                    current = json.loads(run.budget_snapshot_json or "{}")
                    current["counters"] = dict(context.budget.snapshot())
                    run.budget_snapshot_json = json.dumps(current, sort_keys=True); session.commit()

            def stream(self, messages, run_context):
                try:
                    yield from compiled.stream(messages, run_context)
                finally:
                    self.persist_budget()
                    session.close()

            def resume(self, approval_id, run_context):
                try:
                    yield from compiled.resume(approval_id, run_context)
                finally:
                    self.persist_budget()
                    session.close()

        return SessionBoundCompiled()

    def resume(self, approval_id, context):
        compiled = self._compile(context)
        yield {"event": "approval_resumed", "data": {"approval_id": approval_id, "run_id": context.run_id}}
        yield from compiled.resume(approval_id, context)
