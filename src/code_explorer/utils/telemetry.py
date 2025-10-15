"""OpenTelemetry and Prometheus observability setup."""

import logging
import sys
from typing import Any

import structlog
from prometheus_client import Counter, Histogram, make_asgi_app

from code_explorer.config import Settings, get_settings

# =============================================================================
# Prometheus Metrics
# =============================================================================

# HTTP request metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Indexing metrics
INDEXING_JOBS_TOTAL = Counter(
    "indexing_jobs_total",
    "Total indexing jobs",
    ["status"],  # pending, cloning, indexing, ready, failed
)

INDEXING_DURATION = Histogram(
    "indexing_duration_seconds",
    "Indexing job duration in seconds",
    ["status"],
    buckets=[10, 30, 60, 120, 300, 600, 1200],
)

# External API metrics
PINECONE_OPERATIONS_TOTAL = Counter(
    "pinecone_operations_total",
    "Total Pinecone operations",
    ["operation"],  # upsert, query, delete
)

OPENAI_TOKENS_TOTAL = Counter(
    "openai_tokens_total",
    "Total OpenAI tokens used",
    ["model", "type"],  # type: prompt, completion
)

EMBEDDING_REQUESTS_TOTAL = Counter(
    "embedding_requests_total",
    "Total embedding requests",
    ["status"],  # success, error
)

# Chat metrics
CHAT_REQUESTS_TOTAL = Counter(
    "chat_requests_total",
    "Total chat requests",
    ["status"],  # success, error
)

CHAT_SOURCES_RETRIEVED = Histogram(
    "chat_sources_retrieved",
    "Number of source chunks retrieved per chat request",
    buckets=[0, 1, 2, 5, 10, 15, 20, 30, 50],
)


def get_metrics_app():
    """Get Prometheus metrics ASGI app."""
    return make_asgi_app()


# =============================================================================
# Structlog Configuration
# =============================================================================


def setup_logging(settings: Settings | None = None) -> None:
    """
    Configure structured logging with structlog.

    Args:
        settings: Application settings (uses global if not provided)
    """
    if settings is None:
        settings = get_settings()

    # Determine processors based on log format
    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Common processors
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Add callsite info in debug mode
    if settings.log_level == "DEBUG":
        processors.insert(0, structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            ]
        ))

    processors.append(renderer)

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
    )

    # Suppress uvicorn's default access logs (we have our own middleware)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # Keep uvicorn error logs but reduce noise
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


# =============================================================================
# OpenTelemetry Configuration
# =============================================================================


def setup_opentelemetry(settings: Settings | None = None) -> None:
    """
    Configure OpenTelemetry tracing.

    Args:
        settings: Application settings
    """
    if settings is None:
        settings = get_settings()

    if not settings.otlp_endpoint:
        # OpenTelemetry disabled
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Create resource
        resource = Resource.create(
            {
                "service.name": "code-explorer",
                "service.version": "0.1.0",
            }
        )

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # Add OTLP exporter
        otlp_exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Set global tracer provider
        trace.set_tracer_provider(provider)

        # Instrument libraries
        HTTPXClientInstrumentor().instrument()

        structlog.get_logger().info(
            "OpenTelemetry configured",
            endpoint=settings.otlp_endpoint,
        )

    except ImportError as e:
        structlog.get_logger().warning(
            "OpenTelemetry dependencies not available",
            error=str(e),
        )


def instrument_fastapi(app) -> None:  # type: ignore[no-untyped-def]
    """
    Instrument FastAPI app with OpenTelemetry.

    Args:
        app: FastAPI application
    """
    settings = get_settings()

    if not settings.otlp_endpoint:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)

        structlog.get_logger().info("FastAPI instrumented with OpenTelemetry")

    except ImportError:
        pass


# =============================================================================
# Utility Functions
# =============================================================================


def record_http_request(method: str, path: str, status_code: int, duration: float) -> None:
    """Record HTTP request metrics."""
    # Normalize path to avoid cardinality explosion
    normalized_path = normalize_path(path)

    HTTP_REQUESTS_TOTAL.labels(
        method=method,
        path=normalized_path,
        status_code=str(status_code),
    ).inc()

    HTTP_REQUEST_DURATION.labels(
        method=method,
        path=normalized_path,
    ).observe(duration)


def normalize_path(path: str) -> str:
    """
    Normalize path for metrics to avoid high cardinality.

    Replaces UUIDs and other dynamic segments with placeholders.
    """
    import re

    # Replace UUIDs with placeholder
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        path,
        flags=re.IGNORECASE,
    )

    # Replace numeric IDs with placeholder
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)

    return path


def record_indexing_job(status: str, duration: float | None = None) -> None:
    """Record indexing job metrics."""
    INDEXING_JOBS_TOTAL.labels(status=status).inc()

    if duration is not None:
        INDEXING_DURATION.labels(status=status).observe(duration)


def record_openai_tokens(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Record OpenAI token usage."""
    OPENAI_TOKENS_TOTAL.labels(model=model, type="prompt").inc(prompt_tokens)
    OPENAI_TOKENS_TOTAL.labels(model=model, type="completion").inc(completion_tokens)


def record_pinecone_operation(operation: str) -> None:
    """Record Pinecone operation."""
    PINECONE_OPERATIONS_TOTAL.labels(operation=operation).inc()


def record_embedding_request(success: bool) -> None:
    """Record embedding request."""
    status = "success" if success else "error"
    EMBEDDING_REQUESTS_TOTAL.labels(status=status).inc()


def record_chat_request(success: bool, source_count: int) -> None:
    """Record chat request metrics."""
    status = "success" if success else "error"
    CHAT_REQUESTS_TOTAL.labels(status=status).inc()

    if success:
        CHAT_SOURCES_RETRIEVED.observe(source_count)
