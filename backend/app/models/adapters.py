import json
import queue
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    type: str
    text: str | None = None
    tool: dict[str, Any] | None = None
    structured: Any = None


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) and block.get("type") in {"text", "output_text"} else ""
            for block in content
        )
    return ""


class ModelAdapter:
    def __init__(self, protocol: str, model: str, chat_transport=None, embedding_transport=None):
        self.protocol = protocol
        self.model = model
        self.chat_transport = chat_transport
        self.embedding_transport = embedding_transport

    def generate(self, messages) -> StreamEvent:
        result = self.chat_transport.generate(model=self.model, messages=messages)
        return StreamEvent("text", text=content_text(result.get("content")))

    def stream_text(self, messages, cancel_event) -> StreamEvent:
        text = "".join(content_text(chunk.get("content")) for chunk in self.chat_transport.stream(
            model=self.model, messages=messages, cancel_event=cancel_event
        ))
        return StreamEvent("text", text=text)

    def cancel_active_stream(self, messages, cancel_event) -> None:
        outcome = []
        first_chunk = threading.Event()

        def consume():
            iterator = self.chat_transport.stream(model=self.model, messages=messages, cancel_event=cancel_event)
            try:
                next(iterator)
                first_chunk.set()
                cancel_event.wait(0.5)
                if cancel_event.is_set():
                    try:
                        next(iterator)
                    except StopIteration:
                        pass
            except Exception as exc:
                outcome.append(exc)
            finally:
                close = getattr(iterator, "close", None)
                if close:
                    try:
                        close()
                    except Exception as exc:
                        outcome.append(exc)

        worker = threading.Thread(target=consume, name="model-stream-cancel-probe", daemon=True)
        worker.start()
        first_chunk.wait(0.05)
        cancel_event.set()
        worker.join(timeout=0.5)
        if worker.is_alive():
            raise RuntimeError("STREAM_CANCELLATION_TIMEOUT")
        if outcome:
            raise outcome[0]

    def tool_call(self, messages) -> StreamEvent:
        result = self.chat_transport.generate(
            model=self.model,
            messages=messages,
            tools=[{"name": "echo", "description": "Returns a value without side effects", "parameters": {"type": "object"}}],
        )
        calls = result.get("tool_calls") or []
        if not calls:
            raise RuntimeError("TOOL_CALL_MISSING")
        return StreamEvent("tool", tool=calls[0])

    def structured_output(self, messages) -> StreamEvent:
        result = self.chat_transport.generate(model=self.model, messages=messages, structured_schema={"type": "object"})
        return StreamEvent("structured", structured=json.loads(content_text(result.get("content"))))

    def embed(self, texts):
        if not self.embedding_transport:
            raise RuntimeError("EMBEDDING_UNAVAILABLE")
        return self.embedding_transport.embed(model=self.model, texts=texts)


class LangChainChatTransport:
    def __init__(self, chat_model):
        self.chat_model = chat_model

    def generate(self, *, model, messages, tools=None, structured_schema=None):
        runnable = self.chat_model
        if tools:
            runnable = runnable.bind_tools(tools)
        if structured_schema:
            runnable = runnable.with_structured_output(structured_schema, include_raw=True)
            result = runnable.invoke(messages)
            parsed = result.get("parsed") if isinstance(result, dict) else result
            return {"content": json.dumps(parsed), "tool_calls": []}
        result = runnable.invoke(messages)
        return {"content": getattr(result, "content", ""), "tool_calls": getattr(result, "tool_calls", [])}

    def stream(self, *, model, messages, cancel_event):
        iterator = self.chat_model.stream(messages)
        output = queue.Queue()
        finished = object()

        def consume():
            try:
                for chunk in iterator:
                    output.put(("chunk", chunk))
            except Exception as exc:
                output.put(("error", exc))
            finally:
                output.put(("done", finished))

        worker = threading.Thread(target=consume, name="model-stream-reader", daemon=True)
        worker.start()
        cancel_lock = threading.Lock()
        cancellation_attempted = False
        cancellation_error = None
        completed = False

        def bounded_cancel():
            nonlocal cancellation_attempted, cancellation_error
            with cancel_lock:
                if cancellation_attempted:
                    if cancellation_error:
                        raise cancellation_error
                    return
                cancellation_attempted = True
            operations = []
            cancel_request = getattr(self.chat_model, "_runtime_cancel", None)
            close = getattr(iterator, "close", None)
            for name, operation in (("http", cancel_request), ("iterator", close)):
                if operation:
                    thread = threading.Thread(target=operation, name=f"model-stream-close-{name}", daemon=True)
                    thread.start()
                    operations.append(thread)
            deadline = __import__("time").monotonic() + 0.25
            for thread in operations:
                thread.join(timeout=max(0, deadline - __import__("time").monotonic()))
            worker.join(timeout=max(0, deadline - __import__("time").monotonic()))
            if worker.is_alive() or any(thread.is_alive() for thread in operations):
                cancellation_error = RuntimeError("STREAM_CANCELLATION_TIMEOUT")
                raise cancellation_error

        try:
            while True:
                if cancel_event.is_set():
                    bounded_cancel()
                    return
                try:
                    kind, value = output.get(timeout=0.02)
                except queue.Empty:
                    continue
                if kind == "chunk":
                    if not cancel_event.is_set():
                        yield {"content": getattr(value, "content", "")}
                elif kind == "error":
                    raise value
                else:
                    worker.join(timeout=0.25)
                    completed = True
                    return
        finally:
            if not completed:
                bounded_cancel()
            else:
                worker.join(timeout=0.25)


class LangChainEmbeddingTransport:
    def __init__(self, embeddings):
        self.embeddings = embeddings

    def embed(self, *, model, texts):
        return self.embeddings.embed_documents(texts)


def build_langchain_chat(protocol: str, *, api_key: str, model: str, base_url: str, timeout: int, max_retries: int,
                         temperature: float, max_tokens: int | None, use_responses_api: bool = False, http_client=None):
    if protocol == "openai_compatible":
        from langchain_openai import ChatOpenAI
        result = ChatOpenAI(api_key=api_key, model=model, base_url=base_url, timeout=timeout, max_retries=max_retries,
                            temperature=temperature, max_tokens=max_tokens, use_responses_api=use_responses_api,
                            http_client=http_client)
        if http_client is not None:
            result.__dict__["_runtime_cancel"] = http_client.close
        return result
    if protocol == "anthropic":
        import anthropic
        from langchain_anthropic import ChatAnthropic
        result = ChatAnthropic(api_key=api_key, model=model, base_url=base_url, timeout=timeout, max_retries=max_retries,
                               temperature=temperature, max_tokens=max_tokens or 1024)
        if http_client is not None:
            result.__dict__["_client"] = anthropic.Client(api_key=api_key, base_url=base_url, timeout=timeout,
                                                          max_retries=max_retries, http_client=http_client)
            result.__dict__["_runtime_cancel"] = http_client.close
        return result
    raise ValueError("Unsupported model protocol")


def build_langchain_embeddings(*, api_key: str, model: str, base_url: str, dimensions: int | None, http_client=None):
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(api_key=api_key, model=model, base_url=base_url, dimensions=dimensions,
                            http_client=http_client)
