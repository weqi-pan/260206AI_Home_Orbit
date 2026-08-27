import os

from langchain_core.messages import AIMessage, AIMessageChunk


class UnavailableAgentRunner:
    def stream(self, messages, context):
        if not os.getenv("DASHSCOPE_API_KEY"):
            raise RuntimeError("聊天模型不可用：未配置 DASHSCOPE_API_KEY")
        from backend.agent.deep_agent import create_deep_agent
        agent = create_deep_agent()
        for mode, chunk in agent.stream({"messages": messages}, stream_mode=["messages", "custom"], context=context):
            if mode == "messages":
                message = chunk[0] if isinstance(chunk, tuple) else chunk
                if not isinstance(message, (AIMessageChunk, AIMessage)):
                    continue
                content = getattr(message, "content", "")
                if isinstance(content, str) and content:
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
