import json

from backend.model.factory import get_aux_model


class FollowupService:
    def __init__(self, model=None, model_resolver=None):
        self._model = model
        self._model_resolver = model_resolver

    def generate(self, user_text, answer):
        try:
            model = self._model or (self._model_resolver("auxiliary_generation") if self._model_resolver else get_aux_model())
            content = model.invoke([{"role": "user", "content": f"生成3个追问，仅返回JSON数组：\n用户：{user_text}\n助手：{answer}"}]).content
            return [str(item).strip() for item in json.loads(content) if str(item).strip()][:3]
        except Exception:
            return []
