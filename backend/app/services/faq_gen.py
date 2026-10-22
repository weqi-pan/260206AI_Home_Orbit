import json
import re
from pathlib import Path

from backend.llm_factory.factory import get_chat_model


class FaqGenerationError(RuntimeError):
    """模型输出无法解析为 FAQ JSON 数组。"""


def _parse_pairs(content):
    text = str(content).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[\w-]*\s*", "", text)
        end = text.rfind("```")
        if end != -1:
            text = text[:end]
        text = text.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise FaqGenerationError("模型未返回有效的FAQ JSON数组")
    try:
        pairs = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise FaqGenerationError("FAQ_RESULT_INVALID_JSON") from exc
    if not isinstance(pairs, list):
        raise FaqGenerationError("FAQ结果必须是JSON数组")
    return pairs


class FaqGenerator:
    def __init__(self, model=None, model_resolver=None):
        self._model = model
        self._model_resolver = model_resolver

    def generate(self, path):
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        else:
            text = path.read_text(encoding="utf-8")
        model = self._model or (self._model_resolver("review_model") if self._model_resolver else get_chat_model())
        content = model.invoke([{"role": "user", "content": f"从资料生成FAQ，仅返回question/answer对象数组：\n{text[:12000]}"}]).content
        return [{"question": str(item["question"]).strip(), "answer": str(item["answer"]).strip()} for item in _parse_pairs(content)
                if item.get("question") and item.get("answer")]
