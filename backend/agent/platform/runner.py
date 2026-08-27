import json

from backend.app.db.base import utcnow


def persist_run_outcome(session, run_id, status, error_code=None, finished=False):
    from backend.app.db.models import AgentRun
    run = session.get(AgentRun, run_id)
    if run:
        run.status = status
        run.error_code = error_code
        now = utcnow()
        if status == "running" and run.started_at is None: run.started_at = now
        if status == "waiting_approval": run.interrupted_at = now
        if status == "resuming": run.resumed_at = now
        run.finished_at = now if finished else None
        session.commit()
    return run


class PlatformAgentRunner:
    """Deployment-aware runner preserving the V1 stream event boundary."""

    def __init__(self, compiler): self.compiler = compiler

    def stream(self, messages, context):
        yield {"event": "run", "data": {"run_id": context.run_id, "deployment_id": context.deployment_id,
                                           "agent_version_id": context.agent_version_id, "status": "started"}}
        try:
            waiting = False
            for event in self.compiler(context).stream(messages, context):
                waiting = waiting or event.get("event") == "approval_required"
                yield event
            status = "waiting_approval" if waiting else "completed"
            yield {"event": "run", "data": {"run_id": context.run_id, "status": status}}
        except GeneratorExit:
            raise
        except Exception:
            yield {"event": "run", "data": {"run_id": context.run_id, "status": "failed",
                                               "error_code": "AGENT_RUN_FAILED"}}
            raise


class DeploymentAgentRunner(PlatformAgentRunner):
    def __init__(self, session_factory, checkpointer=None):
        self.session_factory = session_factory
        self._models = {}
        if checkpointer is None:
            session = session_factory()
            try:
                from backend.agent.platform.checkpointer import checkpoint_saver_for
                checkpointer = checkpoint_saver_for(session.bind, session_factory)
            finally: session.close()
        self.checkpointer = checkpointer
        super().__init__(self._compile)

    def bind_model(self, run_id, model): self._models[run_id] = model
    def model_for(self, run_id): return self._models.get(run_id)

    def _compile_agent(self, context, session):
        from backend.agent.platform.snapshots import SnapshotCompiler
        if context.compiled_snapshot:
            compiler = SnapshotCompiler(session, self.checkpointer,
                model_factory=lambda _deployment: self.model_for(context.run_id))
            snapshot = compiler.validate_and_load(context.compiled_snapshot, context)
            compiler.validate_safety(snapshot, context)
            return compiler.rehydrate(snapshot, context)
        from backend.agent.platform.compiler import AgentCompiler
        return AgentCompiler(session, checkpointer=self.checkpointer,
            model_factory=lambda _deployment: self.model_for(context.run_id)).compile(context.deployment_id, context)

    def _persist(self, context, status, error_code=None, finished=False):
        with self.session_factory() as session:
            persist_run_outcome(session, context.run_id, status, error_code, finished)

    def _persist_budget(self, context):
        with self.session_factory() as session:
            from backend.app.db.models import AgentRun
            run = session.get(AgentRun, context.run_id)
            if run:
                current = json.loads(run.budget_snapshot_json or "{}")
                current["counters"] = dict(context.budget.snapshot())
                run.budget_snapshot_json = json.dumps(current, sort_keys=True); session.commit()

    def _compile(self, context):
        session = self.session_factory()
        try: compiled = self._compile_agent(context, session)
        except Exception: session.close(); raise
        parent = self
        class SessionBoundCompiled:
            def stream(self, messages, run_context):
                try: yield from compiled.stream(messages, run_context)
                finally: session.close()
            def resume(self, approval_id, run_context):
                try: yield from compiled.resume(approval_id, run_context)
                finally: session.close()
        return SessionBoundCompiled()

    def stream(self, messages, context):
        self._persist(context, "running")
        yield {"event": "run", "data": {"run_id": context.run_id, "deployment_id": context.deployment_id,
                                           "agent_version_id": context.agent_version_id, "status": "started"}}
        terminal = False
        try:
            waiting = False
            for event in self._compile(context).stream(messages, context):
                waiting = waiting or event.get("event") == "approval_required"
                yield event
            status, code = ("waiting_approval", "APPROVAL_REQUIRED") if waiting else ("completed", None)
            self._persist(context, status, code, finished=not waiting); terminal = True
            yield {"event": "run", "data": {"run_id": context.run_id, "status": status,
                                               **({"error_code": code} if code else {})}}
        except GeneratorExit:
            self._persist(context, "cancelled", "RUN_CANCELLED", finished=True); terminal = True
            raise
        except Exception as exc:
            code = getattr(exc, "code", None) or "AGENT_RUN_FAILED"
            self._persist(context, "failed", code, finished=True); terminal = True
            yield {"event": "run", "data": {"run_id": context.run_id, "status": "failed", "error_code": code}}
            return
        finally:
            self._persist_budget(context)
            if not terminal:
                self._persist(context, "cancelled", "RUN_CANCELLED", finished=True)

    def resume(self, approval_id, context):
        self._persist(context, "resuming")
        yield {"event": "approval_resumed", "data": {"approval_id": approval_id, "run_id": context.run_id}}
        try:
            waiting = False
            for event in self._compile(context).resume(approval_id, context):
                waiting = waiting or event.get("event") == "approval_required"
                yield event
            status, code = ("waiting_approval", "APPROVAL_REQUIRED") if waiting else ("completed", None)
            self._persist(context, status, code, finished=not waiting)
            yield {"event": "run", "data": {"run_id": context.run_id, "status": status,
                                               **({"error_code": code} if code else {})}}
        except GeneratorExit:
            self._persist(context, "cancelled", "RUN_CANCELLED", finished=True); raise
        except Exception as exc:
            code = getattr(exc, "code", None) or "AGENT_RUN_FAILED"
            status = "retry_required" if code == "APPROVAL_RETRY_REQUIRED" else "failed"
            self._persist(context, status, code, finished=True)
            yield {"event": "run", "data": {"run_id": context.run_id, "status": status, "error_code": code}}
            return
        finally: self._persist_budget(context)
