"""OpenTelemetry tracing. A no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set.

Langfuse accepts OTLP directly, so the same setup serves Langfuse, Tempo, Jaeger, Honeycomb...:

    OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
    OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64(public_key:secret_key)>

Spans for one chat: chat > [rewrite] > retrieval (embed, search, rerank) > llm.
"""
import logging
import os

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("docchat")

__all__ = ["tracer", "setup_tracing", "Span", "SpanKind", "Status", "StatusCode", "trace"]


def setup_tracing(service_name: str) -> bool:
    """Install an OTLP exporter if configured. Safe to call more than once."""
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") and not os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint/headers from env
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry tracing enabled", extra={"service": service_name})
    return True


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()  # flushes buffered spans


def fail(span: Span, exc: BaseException) -> None:
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
