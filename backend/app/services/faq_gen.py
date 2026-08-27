import json
from pathlib import Path

from backend.model.factory import get_chat_model


class FaqGenerator:
    def __init__(self, model=None):
        self._model = model

    def generate(self, path):
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        else:
            text = path.read_text(encoding="utf-8")
        model = self._model or get_chat_model()
        content = model.invoke([{"role": "user", "content": f"从资料生成FAQ，仅返回question/answer对象数组：\n{text[:12000]}"}]).content
        pairs = json.loads(content)
        return [{"question": str(item["question"]).strip(), "answer": str(item["answer"]).strip()} for item in pairs
                if item.get("question") and item.get("answer")]
