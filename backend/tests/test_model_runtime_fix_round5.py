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
    from backend.app.models.adapters import ModelAdapter

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
    from backend.app.models.adapters import ModelAdapter

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
    from backend.app.models.adapters import ModelAdapter

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
    from backend.app.models.adapters import ModelAdapter
    from backend.app.models.capabilities import CapabilityTester

    transport = ProbeSilentTransport()
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=transport)
    result = CapabilityTester(adapter, first_chunk_timeout=0.5, cancel_timeout=0.5).run_chat()

    assert result.passed is False
    assert result.error_code == "STREAM_FIRST_CHUNK_TIMEOUT"
    assert result.capabilities == {
        "generation": "passed",
        "streaming": "passed",
        "cancellation": "failed",
        "tool_calling": "failed",
        "structured_output": "failed",
    }


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
    from backend.app.models.adapters import LangChainChatTransport, ModelAdapter

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
