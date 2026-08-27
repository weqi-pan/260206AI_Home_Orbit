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
    def __init__(self, session_factory):
        self.session_factory = session_factory
        super().__init__(self._compile)

    def _compile(self, context):
        from backend.agent.platform.compiler import AgentCompiler
        from backend.agent.platform.checkpointer import checkpoint_saver_for
        session = self.session_factory()
        try:
            saver = checkpoint_saver_for(session.bind, self.session_factory)
            compiled = AgentCompiler(session, checkpointer=saver).compile(context.deployment_id, context)
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
