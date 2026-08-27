import os

from langchain_community.chat_models.tongyi import ChatTongyi


def build_chat_model(api_key: str, model_name: str = "qwen3-max") -> ChatTongyi:
    return ChatTongyi(api_key=api_key, model=model_name)


def get_chat_model(model_name: str = "qwen3-max") -> ChatTongyi:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("聊天模型不可用：未配置 DASHSCOPE_API_KEY")
    return build_chat_model(api_key, model_name)


def get_aux_model(model_name: str = "qwen-flash") -> ChatTongyi:
    return get_chat_model(model_name)
