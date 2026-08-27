from langchain_community.chat_models.tongyi import ChatTongyi


def build_chat_model(api_key: str, model_name: str = "qwen3-max") -> ChatTongyi:
    return ChatTongyi(api_key=api_key, model=model_name)
