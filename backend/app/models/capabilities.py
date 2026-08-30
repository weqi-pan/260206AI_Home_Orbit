import threading
from dataclasses import dataclass

from backend.app.models.adapters import StreamEvent


@dataclass(frozen=True)
class CapabilityEvidence:
    passed: bool
    capabilities: dict[str, str]
    events: list[StreamEvent]
    error_code: str | None = None
    dimensions: int | None = None


class CapabilityTester:
    def __init__(self, adapter, *, first_chunk_timeout=20.0, cancel_timeout=3.0):
        self.adapter = adapter
        self.first_chunk_timeout = first_chunk_timeout
        self.cancel_timeout = cancel_timeout

    def run_chat(self) -> CapabilityEvidence:
        states = {name: "failed" for name in ("generation", "streaming", "cancellation", "tool_calling", "structured_output")}
        events = []
        stage = "generation"
        try:
            messages = [{"role": "user", "content": "Reply with hello."}]
            generated = self.adapter.generate(messages)
            if not generated.text:
                raise RuntimeError("GENERATION_EMPTY")
            states["generation"] = "passed"
            events.append(generated)
            stage = "streaming"
            streamed = self.adapter.stream_text(messages, threading.Event())
            if not streamed.text:
                raise RuntimeError("STREAM_EMPTY")
            states["streaming"] = "passed"
            events.append(streamed)
            stage = "tool_calling"
            tool_messages = [{
                "role": "user",
                "content": (
                    "You must call the echo tool. Do not answer directly. "
                    "Use empty arguments or set value to hello."
                ),
            }]
            tool = self.adapter.tool_call(tool_messages)
            states["tool_calling"] = "passed"
            events.append(tool)
            stage = "structured_output"
            structured = self.adapter.structured_output(messages)
            states["structured_output"] = "passed"
            events.append(structured)
            stage = "cancellation"
            cancelled = threading.Event()
            self.adapter.cancel_active_stream(
                messages, cancelled,
                first_chunk_timeout=self.first_chunk_timeout,
                cancel_timeout=self.cancel_timeout,
            )
            states["cancellation"] = "passed"
        except Exception as exc:
            code = {
                "generation": "GENERATION_FAILED", "streaming": "STREAMING_FAILED", "cancellation": "CANCELLATION_FAILED",
                "tool_calling": "TOOL_CALLING_FAILED", "structured_output": "STRUCTURED_OUTPUT_FAILED",
            }[stage]
            if stage == "cancellation" and str(exc) == "STREAM_FIRST_CHUNK_TIMEOUT":
                code = "STREAM_FIRST_CHUNK_TIMEOUT"
            return CapabilityEvidence(False, states, events, code)
        return CapabilityEvidence(True, states, events)

    def run_embedding(self, expected_dimensions: int | None) -> CapabilityEvidence:
        try:
            vectors = self.adapter.embed(["dimension probe"])
            dimensions = len(vectors[0])
            if expected_dimensions and dimensions != expected_dimensions:
                raise RuntimeError("EMBEDDING_DIMENSION_MISMATCH")
        except Exception:
            return CapabilityEvidence(False, {"embedding": "failed"}, [], "EMBEDDING_TEST_FAILED")
        return CapabilityEvidence(True, {"embedding": "passed"}, [], dimensions=dimensions)
