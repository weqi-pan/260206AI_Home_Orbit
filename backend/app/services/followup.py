import json

from backend.model.factory import get_aux_model


class FollowupService:
    def __init__(self, model=None):
        self._model = model

    def generate(self, user_text, answer):
        try:
            model = self._model or get_aux_model()
            content = model.invoke([{"role": "user", "content": f"生成3个追问，仅返回JSON数组：\n用户：{user_text}\n助手：{answer}"}]).content
            return [str(item).strip() for item in json.loads(content) if str(item).strip()][:3]
        except Exception:
            return []
