from backend.app.api.chat.base import *
from backend.app.api.chat.base import owned_session, submit_title_generation
from backend.app.api.chat.sessions import official_household_deployment

@router.post("/sessions/{session_id}/chat")
def chat(session_id: str, payload: ChatRequest, request: Request, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    owned_session(db, session_id, account.id)
    chat_session = db.get(ChatSession, session_id)
    household_id = chat_session.household_id
    if not chat_session.deployment_id:
        deployment = official_device_deployment(db, household_id)
        if not deployment:
            raise HTTPException(status_code=409, detail={"code": "SESSION_DEPLOYMENT_REQUIRED"})
        chat_session.deployment_id = deployment.id
        db.commit()
    injected_runner = getattr(request.app.state, "agent_runner", None)
    resolved_runtime = None
    usage_id = None
    enforce_runtime = injected_runner is None or getattr(request.app.state, "enforce_model_runtime_for_injected_runner", False)
    if enforce_runtime:
        if not setup_status(db, account).ai_ready:
            raise HTTPException(status_code=503, detail={"code": "MODEL_NOT_CONFIGURED"})
        try:
            resolved_runtime = ModelRuntimeService(db).resolve("primary_chat", household_id=household_id)
        except ModelRuntimeError as exc:
            status_code = 403 if exc.code == "MODEL_NOT_GRANTED" else 503
            raise HTTPException(status_code=status_code, detail={"code": exc.code}) from exc
        profile = resolved_runtime.snapshot.profile
        request_budget = profile["agent_token_budget"] or profile["max_tokens"]
        if not request_budget:
            raise HTTPException(status_code=503, detail={"code": "MODEL_REQUEST_BUDGET_NOT_CONFIGURED"})
        try:
            usage_id = QuotaService(db).reserve(household_id, resolved_runtime.snapshot.model_id, request_budget).id
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail={"code": exc.code}) from exc
    previous_count = db.scalar(select(ConversationHistory.id).where(ConversationHistory.session_id == session_id).limit(1))
    user_message = ConversationHistory(session_id=session_id, user_id=account.user_id, role="user", content=payload.message)
    db.add(user_message)
    db.commit()
    history = db.scalars(select(ConversationHistory).where(ConversationHistory.session_id == session_id).order_by(ConversationHistory.id)).all()
    messages = [{"role": item.role, "content": item.content} for item in history]
    vector_store_service = (getattr(request.app.state, "vector_store_service", None)
                            if purpose_ready(db, "embedding") else None)
    context = {"account_id": account.id, "user_id": account.user_id, "city": account.user.city if account.user else None,
                "session_id": session_id, "repositories": HouseholdToolRepositories(request.app.state.database.session_factory),
                "vector_store_service": vector_store_service}
    if enforce_runtime:
        context["model_resolver"] = purpose_model_resolver(request.app.state.database.session_factory, household_id)
    if resolved_runtime:
        profile = resolved_runtime.snapshot.profile
        context["model_snapshot"] = resolved_runtime.snapshot
        builder = getattr(request.app.state, "chat_model_builder", None)
        try:
            context["chat_model"] = builder(resolved_runtime) if builder else ModelRuntimeService(db).build_chat_model(resolved_runtime)
        except Exception as exc:
            if usage_id:
                QuotaService(db).complete(usage_id, status="failed")
            raise HTTPException(status_code=503, detail={"code": "MODEL_PROVIDER_UNAVAILABLE"}) from exc
    if chat_session.deployment_id:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account.id,
                                                          HouseholdMember.household_id == household_id))
        deployment = db.scalar(select(AgentDeployment).where(AgentDeployment.id == chat_session.deployment_id,
                                                              AgentDeployment.household_id == household_id))
        version = db.get(AgentVersion, deployment.agent_version_id) if deployment else None
        if not member or not deployment or not version:
            raise HTTPException(status_code=404, detail={"code": "DEPLOYMENT_NOT_FOUND"})
        snapshot = resolved_runtime.snapshot if resolved_runtime else None
        from backend.agent.platform.authority import ExecutionAuthority
        from backend.agent.platform.snapshots import CompiledSnapshotService
        installation = ExecutionAuthority(db).installation(deployment.id, household_id, version.id)[1]
        provisional_context = RunContext(principal_id=account.id, household_id=household_id, member_id=member.id,
            role=member.role, session_id=session_id, deployment_id=deployment.id, agent_version_id=version.id,
            run_id="pending", timezone="Asia/Shanghai", grants={}, consent={}, deployment_revision=deployment.revision,
            model_id=snapshot.model_id if snapshot else "injected-model",
            model_revision=snapshot.config_revision if snapshot else 1,
            model_snapshot={"id": snapshot.model_id, "revision": snapshot.config_revision,
                            "profile": dict(snapshot.profile)} if snapshot else {"id": "injected-model", "revision": 1})
        compiled_snapshot = CompiledSnapshotService(db).capture(deployment.id, installation.id, provisional_context)
        run = AgentRun(household_id=household_id, deployment_id=deployment.id, principal_id=account.id,
                       member_id=member.id, session_id=session_id, agent_version_id=version.id,
                       deployment_revision=deployment.revision, model_id=snapshot.model_id if snapshot else "injected-model",
                       model_revision=snapshot.config_revision if snapshot else 1,
                       compiled_snapshot_json=json.dumps(compiled_snapshot, ensure_ascii=False, sort_keys=True),
                       model_snapshot_json=json.dumps(provisional_context.model_snapshot, sort_keys=True),
                        permission_snapshot_json=json.dumps(list(compiled_snapshot["grants"]), sort_keys=True),
                       consent_snapshot_json="{}", budget_snapshot_json=json.dumps(
                           compiled_snapshot["agent_version"]["definition"].get("budgets", {}), sort_keys=True))
        db.add(run); db.commit(); db.refresh(run)
        grants = {item["key"]: item for item in compiled_snapshot["grants"]}
        platform_model = context.get("chat_model")
        context = RunContext(
            principal_id=account.id, household_id=household_id, member_id=member.id, role=member.role,
            session_id=session_id, deployment_id=deployment.id, agent_version_id=version.id, run_id=run.id,
            timezone="Asia/Shanghai", grants=grants, consent={}, deployment_revision=deployment.revision,
            model_id=run.model_id, model_revision=run.model_revision, user_id=account.user_id,
            model_snapshot=json.loads(run.model_snapshot_json), compiled_snapshot=compiled_snapshot,
            skill_version_ids=tuple(item["id"] for item in compiled_snapshot["skills"]),
            city=account.user.city if account.user else None,
            repositories=HouseholdToolRepositories(request.app.state.database.session_factory),
            vector_store_service=vector_store_service,
            budget=RunBudget(
                max_delegation_depth=int(compiled_snapshot["budget"].get("delegation_depth", 4)),
                max_tool_calls=int(compiled_snapshot["budget"].get("tool_calls", 16)),
                max_tokens=int(compiled_snapshot["budget"].get("tokens", 32000)),
                wall_time_seconds=int(compiled_snapshot["budget"].get("wall_time_seconds", 120))),
        )
        request.app.state.deployment_agent_runner.bind_model(run.id, platform_model)
    runner_override = injected_runner
    if isinstance(context, RunContext) and runner_override is None:
        runner_override = request.app.state.deployment_agent_runner
    return EventSourceResponse(
        chat_event_stream(request.app, session_id, account, payload.message, messages, context, user_message.id,
                          previous_count is None, usage_id=usage_id, runner_override=runner_override),
        ping=15,
        headers={"X-Accel-Buffering": "no"},
    )

def chat_event_stream(app, session_id, account, message, messages, context, user_message_id, first_message, usage_id=None,
                      runner_override=None):
    answer = ""
    persisted_assistant_id = None
    started = time.monotonic()
    model_resolver = context.get("model_resolver") if isinstance(context, dict) else None
    sentiment_service = getattr(app.state, "sentiment_service", None) or get_sentiment_service(model_resolver)
    sentiment_future = AUX_EXECUTOR.submit(sentiment_service.analyze, message)
    try:
        runner = runner_override or getattr(app.state, "agent_runner", None) or get_agent_runner()
        for event in runner.stream(messages, context):
            if event.get("event") == "token":
                answer += event.get("data", {}).get("content", "")
            yield {"event": event["event"], "data": json.dumps(event.get("data", {}), ensure_ascii=False)}
        try:
            remaining = max(0, SENTIMENT_DEADLINE_SECONDS - (time.monotonic() - started))
            sentiment = sentiment_future.result(timeout=remaining)
        except Exception:
            sentiment = {"label": "neutral", "score": 0.0}
        with app.state.database.session_factory.begin() as write_db:
            stored_user = write_db.get(ConversationHistory, user_message_id)
            stored_user.sentiment = sentiment.get("label", "neutral")
            stored_user.sentiment_score = sentiment.get("score", 0)
            assistant = ConversationHistory(session_id=session_id, user_id=account.user_id, role="assistant", content=answer)
            write_db.add(assistant)
            write_db.flush()
            assistant_id = assistant.id
        persisted_assistant_id = assistant_id
        yield {"event": "sentiment", "data": json.dumps(sentiment, ensure_ascii=False)}
        if first_message:
            submit_title_generation(app, session_id, message, answer, model_resolver)
        followup_service = getattr(app.state, "followup_service", None) or get_followup_service(model_resolver)
        followup_future = AUX_EXECUTOR.submit(followup_service.generate, message, answer)
        followup_timeout = float(getattr(getattr(app.state, "settings", None), "followup_timeout_seconds", DEFAULT_FOLLOWUP_TIMEOUT_SECONDS))
        try:
            questions = followup_future.result(timeout=followup_timeout)
        except Exception:
            questions = []
        yield {"event": "followup", "data": json.dumps({"questions": questions}, ensure_ascii=False)}
        if usage_id:
            with app.state.database.session_factory() as usage_db:
                QuotaService(usage_db).complete(usage_id)
        yield {"event": "done", "data": json.dumps({"message_id": assistant_id})}
    except (GeneratorExit, asyncio.CancelledError):
        _persist_disconnected_answer(app, session_id, account.user_id, user_message_id, answer, sentiment_future, persisted_assistant_id)
        if usage_id:
            with app.state.database.session_factory() as usage_db:
                QuotaService(usage_db).complete(usage_id, status="cancelled")
        raise
    except Exception as exc:
        stored_answer = f"{answer}\n\n[回答中断]" if answer else "[回答生成失败] 聊天服务暂不可用"
        with app.state.database.session_factory.begin() as write_db:
            stored_user = write_db.get(ConversationHistory, user_message_id)
            if stored_user and stored_user.sentiment is None:
                stored_user.sentiment = "neutral"
                stored_user.sentiment_score = 0.0
            write_db.add(ConversationHistory(
                session_id=session_id, user_id=account.user_id, role="assistant", content=stored_answer
            ))
        if usage_id:
            with app.state.database.session_factory() as usage_db:
                QuotaService(usage_db).complete(usage_id, status="failed")
        yield {"event": "error", "data": json.dumps({"code": "MODEL_EXECUTION_FAILED", "message": "聊天服务暂不可用"}, ensure_ascii=False)}


def _persist_disconnected_answer(app, session_id, user_id, user_message_id, answer, sentiment_future, persisted_assistant_id=None):
    """客户端断开兜底：assistant 已持久化则只补写 sentiment，否则持久化部分回答或明确占位。"""
    content = f"{answer}\n\n[回答中断]" if answer else "[回答生成失败] 客户端提前断开"
    try:
        sentiment = sentiment_future.result(timeout=SENTIMENT_DISCONNECT_GRACE_SECONDS)
    except Exception:
        sentiment = {"label": "neutral", "score": 0.0}
    try:
        with app.state.database.session_factory.begin() as write_db:
            stored_user = write_db.get(ConversationHistory, user_message_id)
            if stored_user and stored_user.sentiment is None:
                stored_user.sentiment = sentiment.get("label", "neutral")
                stored_user.sentiment_score = sentiment.get("score", 0)
            if persisted_assistant_id is None:
                write_db.add(ConversationHistory(
                    session_id=session_id, user_id=user_id, role="assistant", content=content
                ))
    except Exception:
        logger.exception("客户端断开兜底持久化失败 session_id=%s", session_id)
