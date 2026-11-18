"""Test helpers: an in-memory OpenTelemetry exporter and a Prometheus sample reader.

The tracer provider is global and can be set once, so it is installed at import time (conftest
imports this module before the app) and each test just clears the exporter."""
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

SPANS = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(SPANS))
trace.set_tracer_provider(_provider)


def sample(name: str, **labels) -> float:
    """Current value of one Prometheus sample (0 if it has never been touched)."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def app_spans() -> list:
    """Only the spans this app creates (tracer "docchat"). Recent FastAPI versions emit their own
    request/endpoint spans when OpenTelemetry is installed; ours nest underneath them."""
    return [s for s in SPANS.get_finished_spans() if s.instrumentation_scope.name == "docchat"]


def spans_by_name() -> dict:
    return {s.name: s for s in app_spans()}
