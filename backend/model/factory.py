from backend.app.models.adapters import build_langchain_chat


def build_chat_model(protocol: str, api_key: str, model_name: str, base_url: str, timeout_seconds: int = 30,
                     max_retries: int = 2, temperature: float = 0.2, max_tokens: int | None = None,
                     use_responses_api: bool = False):
    return build_langchain_chat(protocol, api_key=api_key, model=model_name, base_url=base_url,
                                timeout=timeout_seconds, max_retries=max_retries, temperature=temperature,
                                max_tokens=max_tokens, use_responses_api=use_responses_api)


def get_chat_model(*_args, **_kwargs):
    raise RuntimeError("MODEL_NOT_CONFIGURED")


def get_aux_model(*_args, **_kwargs):
    raise RuntimeError("MODEL_NOT_CONFIGURED")
