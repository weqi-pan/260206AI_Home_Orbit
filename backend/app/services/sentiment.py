import json

from backend.model.factory import get_aux_model


class SentimentService:
    def __init__(self, model=None):
        self._model = model

    def analyze(self, text):
        try:
            model = self._model or get_aux_model()
            content = model.invoke([{"role": "user", "content": f"判断情绪并仅返回JSON(label,score)：{text}"}]).content
            result = json.loads(content)
            if result.get("label") not in {"positive", "neutral", "negative"}:
                raise ValueError("invalid label")
            return {"label": result["label"], "score": max(0.0, min(1.0, float(result["score"])))}
        except Exception:
            return {"label": "neutral", "score": 0.0}
