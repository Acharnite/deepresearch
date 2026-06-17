"""OpenTelemetry tracing setup and helpers."""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)


def setup_tracing(service_name: str = "deepresearch") -> trace.Tracer:
    """Configure OpenTelemetry tracing.

    In dev: logs spans to console.
    In prod: exports to OTLP collector (set OTEL_EXPORTER_OTLP_ENDPOINT).
    """
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "0.12.0",
    })

    provider = TracerProvider(resource=resource)

    # Always log to console in dev
    provider.add_span_processor(
        SimpleSpanProcessor(ConsoleSpanExporter())
    )

    # OTLP export if endpoint configured
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
            )
        )

    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


# Module-level tracer — import and use directly
tracer = setup_tracing()
