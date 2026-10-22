import threading
import time
from types import SimpleNamespace

import pytest

from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, BaseChatTransport


class DelayedFirstChunkTransport:
    """Simulates a real remote LLM: slow first token, then honors cancellation."""

    def __init__(self, delay):
        self.delay = delay
        self.cancelled_after_first_chunk = False

    def stream(self, *, model, messages, cancel_event):
        time.sleep(self.delay)
        yield {"content": "first"}
        cancel_event.wait(10)
        if cancel_event.is_set():
            self.cancelled_after_first_chunk = True
            return
        yield {"content": "second"}


def test_cancel_probe_passes_with_realistic_first_token_latency():
    from backend.app.llm_runtime.adapters import ModelAdapter

    transport = DelayedFirstChunkTransport(1.5)
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=transport)
    started = time.monotonic()
    adapter.cancel_active_stream([], threading.Event(), first_chunk_timeout=5.0, cancel_timeout=2.0)
    elapsed = time.monotonic() - started

    assert elapsed >= 1.5
    assert elapsed < 4.0
    assert transport.cancelled_after_first_chunk is True


class UninterruptibleStreamTransport:
    """Yields a first chunk, then blocks in a network read that ignores cancellation."""

    def stream(self, *, model, messages, cancel_event):
        yield {"content": "first"}
        time.sleep(30)
        yield {"content": "never"}


def test_cancel_probe_fails_closed_when_stream_cannot_be_interrupted():
    from backend.app.llm_runtime.adapters import ModelAdapter

    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=UninterruptibleStreamTransport())
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        adapter.cancel_active_stream([], threading.Event(), first_chunk_timeout=5.0, cancel_timeout=0.5)
    elapsed = time.monotonic() - started

    assert str(exc.value) == "STREAM_CANCELLATION_TIMEOUT"
    assert elapsed < 2.0


class SilentFirstChunkTransport:
    """Never produces a first chunk within the probe budget."""

    def stream(self, *, model, messages, cancel_event):
        time.sleep(30)
        yield {"content": "late"}


def test_cancel_probe_reports_first_chunk_timeout_as_distinct_stable_code():
    from backend.app.llm_runtime.adapters import ModelAdapter

    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=SilentFirstChunkTransport())
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        adapter.cancel_active_stream([], threading.Event(), first_chunk_timeout=0.5, cancel_timeout=0.5)
    elapsed = time.monotonic() - started

    assert str(exc.value) == "STREAM_FIRST_CHUNK_TIMEOUT"
    assert elapsed < 2.0


class ProbeSilentTransport(BaseChatTransport):
    """Streams normally for the streaming stage, then goes silent for the cancel probe."""

    def __init__(self):
        self.stream_calls = 0

    def stream(self, *, model, messages, cancel_event):
        self.stream_calls += 1
        if self.stream_calls == 1:
            yield {"content": "hello"}
            return
        time.sleep(30)
        yield {"content": "late"}


def test_capability_evidence_distinguishes_first_chunk_timeout_from_cancellation():
    from backend.app.llm_runtime.adapters import ModelAdapter
    from backend.app.llm_runtime.capabilities import CapabilityTester

    transport = ProbeSilentTransport()
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=transport)
    result = CapabilityTester(adapter, first_chunk_timeout=0.5, cancel_timeout=0.5).run_chat()

    assert result.passed is False
    assert result.error_code == "STREAM_FIRST_CHUNK_TIMEOUT"
    assert result.capabilities == {
        "generation": "passed",
        "streaming": "passed",
        "cancellation": "failed",
        "tool_calling": "passed",
        "structured_output": "passed",
    }


def test_structured_output_uses_valid_schema_and_explicit_capability_prompt():
    from backend.app.llm_runtime.adapters import LangChainChatTransport, ModelAdapter

    class StructuredRunnable:
        def invoke(self, messages):
            assert messages[-1] == {"role": "user", "content": "Return JSON with ok=true."}
            return {"parsed": {"ok": True}, "raw": SimpleNamespace(content='{"ok":true}')}

    class ChatModel:
        def with_structured_output(self, schema, include_raw=True):
            assert include_raw is True
            assert schema == {
                "title": "CapabilityProbe",
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            }
            return StructuredRunnable()

    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=LangChainChatTransport(ChatModel()))

    event = adapter.structured_output([{"role": "user", "content": "Reply with hello."}])

    assert event.structured == {"ok": True}


def test_tool_call_probe_explicitly_requires_echo_without_changing_text_prompts():
    from backend.app.llm_runtime.adapters import ModelAdapter
    from backend.app.llm_runtime.capabilities import CapabilityTester

    class InstructionSensitiveTransport(BaseChatTransport):
        def __init__(self):
            self.generate_messages = []
            self.stream_messages = []

        def generate(self, *, model, messages, tools=None, structured_schema=None):
            self.generate_messages.append((messages, tools, structured_schema))
            if tools:
                prompt = messages[-1]["content"]
                if "must call the echo tool" in prompt and "Do not answer directly" in prompt:
                    return {"content": "", "tool_calls": [{"name": "echo", "args": {"value": "hello"}}]}
                return {"content": "hello", "tool_calls": []}
            if structured_schema:
                return {"content": '{"ok":true}'}
            return {"content": "hello"}

        def stream(self, *, model, messages, cancel_event):
            self.stream_messages.append(messages)
            yield {"content": "hello"}

    transport = InstructionSensitiveTransport()
    result = CapabilityTester(
        ModelAdapter("openai_compatible", "chat", chat_transport=transport),
        first_chunk_timeout=0.5,
        cancel_timeout=0.5,
    ).run_chat()

    assert result.passed is True
    assert result.error_code is None
    assert result.capabilities == {
        "generation": "passed",
        "streaming": "passed",
        "cancellation": "passed",
        "tool_calling": "passed",
        "structured_output": "passed",
    }
    assert transport.generate_messages[0][0] == [{"role": "user", "content": "Reply with hello."}]
    assert transport.stream_messages == [
        [{"role": "user", "content": "Reply with hello."}],
        [{"role": "user", "content": "Reply with hello."}],
    ]


def test_full_capability_probe_finishes_non_destructive_checks_before_closing_shared_client():
    from backend.app.llm_runtime.adapters import LangChainChatTransport, ModelAdapter
    from backend.app.llm_runtime.capabilities import CapabilityTester

    calls = []

    class SharedHttpClient:
        closed = False

        def close(self):
            calls.append("cancellation")
            self.closed = True

    client = SharedHttpClient()

    class CancellationIterator:
        def __init__(self):
            self.first = True
            self.released = threading.Event()

        def __iter__(self):
            return self

        def __next__(self):
            if self.first:
                self.first = False
                return SimpleNamespace(content="first")
            self.released.wait(2)
            raise StopIteration

        def close(self):
            self.released.set()

    class Runnable:
        def __init__(self, operation):
            self.operation = operation

        def invoke(self, messages):
            assert client.closed is False
            calls.append(self.operation)
            if self.operation == "tool_calling":
                return SimpleNamespace(content="", tool_calls=[{"name": "echo", "args": {}}])
            return {"parsed": {"ok": True}, "raw": SimpleNamespace(content='{"ok":true}')}

    class ChatModel:
        def __init__(self):
            self.stream_calls = 0
            self._runtime_cancel = client.close

        def invoke(self, messages):
            assert client.closed is False
            calls.append("generation")
            return SimpleNamespace(content="hello", tool_calls=[])

        def stream(self, messages):
            assert client.closed is False
            self.stream_calls += 1
            if self.stream_calls == 1:
                calls.append("streaming")
                return iter([SimpleNamespace(content="hello")])
            return CancellationIterator()

        def bind_tools(self, tools):
            return Runnable("tool_calling")

        def with_structured_output(self, schema, include_raw=True):
            assert schema["title"] == "CapabilityProbe"
            return Runnable("structured_output")

    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=LangChainChatTransport(ChatModel()))

    result = CapabilityTester(adapter, first_chunk_timeout=0.5, cancel_timeout=0.5).run_chat()

    assert result.passed is True
    assert result.error_code is None
    assert result.capabilities == {
        "generation": "passed",
        "streaming": "passed",
        "cancellation": "passed",
        "tool_calling": "passed",
        "structured_output": "passed",
    }
    assert calls == ["generation", "streaming", "tool_calling", "structured_output", "cancellation"]


class SlowTeardownIterator:
    """Blocking stream whose close() needs realistic SSL/connection teardown time."""

    def __init__(self, teardown):
        self.teardown = teardown
        self.first = True
        self.release = threading.Event()
        self.closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        if self.first:
            self.first = False
            return SimpleNamespace(content="first")
        self.release.wait(10)
        if self.closed.is_set():
            raise StopIteration
        return SimpleNamespace(content="late")

    def close(self):
        time.sleep(self.teardown)
        self.closed.set()
        self.release.set()


def test_stream_cancellation_survives_realistic_slow_teardown():
    from backend.app.llm_runtime.adapters import LangChainChatTransport, ModelAdapter

    iterator = SlowTeardownIterator(1.0)
    model = SimpleNamespace(stream=lambda messages: iterator)
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=LangChainChatTransport(model))
    cancel = threading.Event()
    result = []
    worker = threading.Thread(target=lambda: result.append(adapter.stream_text([], cancel)), daemon=True)
    started = time.monotonic()
    worker.start()
    time.sleep(0.05)
    cancel.set()
    worker.join(timeout=5)
    elapsed = time.monotonic() - started

    assert worker.is_alive() is False
    assert iterator.closed.is_set()
    assert result and result[0].text == "first"
    assert elapsed < 3.0
