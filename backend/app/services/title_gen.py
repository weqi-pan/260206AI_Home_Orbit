from backend.model.factory import get_aux_model


class TitleService:
    def __init__(self, model=None, model_resolver=None):
        self._model = model
        self._model_resolver = model_resolver

    def generate(self, user_text, answer):
        try:
            model = self._model or (self._model_resolver("auxiliary_generation") if self._model_resolver else get_aux_model())
            content = model.invoke([{"role": "user", "content": f"为首轮对话生成不超过12字标题：\n用户：{user_text}\n助手：{answer}"}]).content
            return str(content).strip()[:12]
        except Exception:
            return user_text.strip()[:12] or "新对话"
