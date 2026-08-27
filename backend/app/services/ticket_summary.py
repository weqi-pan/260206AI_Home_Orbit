from backend.model.factory import get_aux_model


class TicketSummaryService:
    def __init__(self, model=None):
        self._model = model

    def summarize(self, text):
        try:
            model = self._model or get_aux_model()
            summary = str(model.invoke([{"role": "user", "content": f"用一段不超过100字的话总结以下工单内容：\n{text[:4000]}"}]).content).strip()
            return summary or text.strip()[:200]
        except Exception:
            return text.strip()[:200]
