from types import SimpleNamespace


class FakeModel:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


def test_sentiment_service_parses_structured_json_and_falls_back():
    from backend.app.services.sentiment import SentimentService

    assert SentimentService(FakeModel('{"label":"negative","score":0.85}')).analyze("很不满意") == {
        "label": "negative",
        "score": 0.85,
    }
    assert SentimentService(FakeModel("not-json")).analyze("普通消息") == {"label": "neutral", "score": 0.0}


def test_title_and_followup_services_normalize_model_output():
    from backend.app.services.followup import FollowupService
    from backend.app.services.title_gen import TitleService

    assert TitleService(FakeModel("  扫地机器人选购建议很长  ")).generate("问题", "回答") == "扫地机器人选购建议很长"[:12]
    assert FollowupService(FakeModel('["问题1","问题2","问题3","问题4"]')).generate("问题", "回答") == [
        "问题1",
        "问题2",
        "问题3",
    ]


def test_faq_generator_accepts_injected_model_and_parses_pairs(tmp_path):
    from backend.app.services.faq_gen import FaqGenerator

    path = tmp_path / "faq.txt"
    path.write_text("维护知识", encoding="utf-8")
    service = FaqGenerator(FakeModel('[{"question":"如何维护？","answer":"定期清洁。"}]'))

    assert service.generate(path) == [{"question": "如何维护？", "answer": "定期清洁。"}]


def test_faq_generator_strips_markdown_fences_and_raises_clear_error(tmp_path):
    import pytest
    from backend.app.services.faq_gen import FaqGenerationError, FaqGenerator

    path = tmp_path / "faq.txt"
    path.write_text("维护知识", encoding="utf-8")
    fenced = '```json\n[{"question":"如何维护？","answer":"定期清洁。"}]\n```'
    assert FaqGenerator(FakeModel(fenced)).generate(path) == [{"question": "如何维护？", "answer": "定期清洁。"}]

    noisy = f'好的，以下是生成的FAQ：\n{fenced}\n请查收。'
    assert FaqGenerator(FakeModel(noisy)).generate(path) == [{"question": "如何维护？", "answer": "定期清洁。"}]

    with pytest.raises(FaqGenerationError):
        FaqGenerator(FakeModel("抱歉，无法完成该任务")).generate(path)
    with pytest.raises(FaqGenerationError):
        FaqGenerator(FakeModel('{"question":"不是数组","answer":"格式错误"}')).generate(path)
    with pytest.raises(FaqGenerationError):
        FaqGenerator(FakeModel("[{\"question\": 坏JSON]")).generate(path)


def test_ticket_summary_service_summarizes_and_degrades_to_truncation(tmp_path):
    from backend.app.services.ticket_summary import TicketSummaryService

    assert TicketSummaryService(FakeModel("  一句话摘要  ")).summarize("工单内容") == "一句话摘要"

    no_key = TicketSummaryService()
    long_text = "字" * 500
    assert no_key.summarize(long_text) == "字" * 200
