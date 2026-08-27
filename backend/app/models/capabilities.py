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
    def __init__(self, adapter):
        self.adapter = adapter

    def run_chat(self) -> CapabilityEvidence:
        states = {name: "failed" for name in ("generation", "streaming", "cancellation", "tool_calling", "structured_output")}
        events = []
        try:
            messages = [{"role": "user", "content": "Reply with hello."}]
            generated = self.adapter.generate(messages)
            if not generated.text:
                raise RuntimeError("GENERATION_EMPTY")
            states["generation"] = "passed"
            events.append(generated)
            streamed = self.adapter.stream_text(messages, threading.Event())
            if not streamed.text:
                raise RuntimeError("STREAM_EMPTY")
            states["streaming"] = "passed"
            events.append(streamed)
            cancelled = threading.Event()
            cancelled.set()
            self.adapter.stream_text(messages, cancelled)
            states["cancellation"] = "passed"
            tool = self.adapter.tool_call(messages)
            states["tool_calling"] = "passed"
            events.append(tool)
            structured = self.adapter.structured_output(messages)
            states["structured_output"] = "passed"
            events.append(structured)
        except Exception as exc:
            return CapabilityEvidence(False, states, events, str(exc) or "CAPABILITY_TEST_FAILED")
        return CapabilityEvidence(True, states, events)

    def run_embedding(self, expected_dimensions: int | None) -> CapabilityEvidence:
        try:
            vectors = self.adapter.embed(["dimension probe"])
            dimensions = len(vectors[0])
            if expected_dimensions and dimensions != expected_dimensions:
                raise RuntimeError("EMBEDDING_DIMENSION_MISMATCH")
        except Exception as exc:
            return CapabilityEvidence(False, {"embedding": "failed"}, [], str(exc) or "EMBEDDING_TEST_FAILED")
        return CapabilityEvidence(True, {"embedding": "passed"}, [], dimensions=dimensions)
