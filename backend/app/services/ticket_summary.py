from backend.llm_factory.factory import get_aux_model


class TicketSummaryService:
    def __init__(self, model=None, model_resolver=None):
        self._model = model
        self._model_resolver = model_resolver

    def summarize(self, text):
        try:
            model = self._model or (self._model_resolver("review_model") if self._model_resolver else get_aux_model())
            summary = str(model.invoke([{"role": "user", "content": f"用一段不超过100字的话总结以下工单内容：\n{text[:4000]}"}]).content).strip()
            return summary or text.strip()[:200]
        except Exception:
            return text.strip()[:200]
