from langchain_core.messages import AIMessage, AIMessageChunk

from backend.app.models.adapters import content_text


class UnavailableAgentRunner:
    def stream(self, messages, context):
        from backend.agent.deep_agent import create_deep_agent
        chat_model = context.get("chat_model")
        agent = create_deep_agent(chat_model) if chat_model is not None else create_deep_agent()
        for mode, chunk in agent.stream({"messages": messages}, stream_mode=["messages", "custom"], context=context):
            if mode == "messages":
                message = chunk[0] if isinstance(chunk, tuple) else chunk
                if not isinstance(message, (AIMessageChunk, AIMessage)):
                    continue
                content = content_text(getattr(message, "content", ""))
                if content:
                    yield {"event": "token", "data": {"content": content}}
            elif mode == "custom" and isinstance(chunk, dict):
                yield {"event": "tool", "data": chunk}


def get_agent_runner(): return UnavailableAgentRunner()
def get_sentiment_service():
    from backend.app.services.sentiment import SentimentService
    return SentimentService()
def get_followup_service():
    from backend.app.services.followup import FollowupService
    return FollowupService()
def get_title_service():
    from backend.app.services.title_gen import TitleService
    return TitleService()
