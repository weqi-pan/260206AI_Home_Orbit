from backend.app.db.base import utcnow


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
        if checkpointer is None:
            session = session_factory()
            try:
                from backend.agent.platform.checkpointer import checkpoint_saver_for
                checkpointer = checkpoint_saver_for(session.bind, session_factory)
            finally:
                session.close()
        self.checkpointer = checkpointer
        super().__init__(self._compile)

    def _compile(self, context):
        from backend.agent.platform.compiler import AgentCompiler
        session = self.session_factory()
        try:
            compiled = AgentCompiler(session, checkpointer=self.checkpointer).compile(context.deployment_id, context)
        except Exception:
            session.close()
            raise
        class SessionBoundCompiled:
            def stream(self, messages, run_context):
                try:
                    yield from compiled.stream(messages, run_context)
                finally:
                    session.close()

        return SessionBoundCompiled()

    def resume(self, approval_id, context):
        compiled = self._compile(context)
        yield {"event": "approval_resumed", "data": {"approval_id": approval_id, "run_id": context.run_id}}
        yield from compiled.resume(approval_id, context)
