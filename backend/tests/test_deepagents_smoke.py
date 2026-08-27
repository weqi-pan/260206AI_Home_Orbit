def test_deepagents_and_chat_tongyi_import_and_construction_without_real_key():
    import deepagents
    from langchain_community.chat_models.tongyi import ChatTongyi

    from backend.model.factory import build_chat_model

    model = build_chat_model(api_key="mock-key", model_name="qwen3-max")

    assert deepagents.create_deep_agent
    assert isinstance(model, ChatTongyi)


def test_create_deep_agent_assembly_path_is_mockable_without_network(monkeypatch):
    from backend.agent import deep_agent

    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return "assembled-agent"

    monkeypatch.setattr(deep_agent, "create_deep_agent", fake_create_deep_agent)
    result = deep_agent.build_deep_agent(model=object(), tools=["tool"], subagents=[{"name": "sub"}])

    assert result == "assembled-agent"
    assert captured["model"] is not None
    assert captured["tools"] == ["tool"]
    assert captured["subagents"] == [{"name": "sub"}]
    assert len(captured["middleware"]) == 1
