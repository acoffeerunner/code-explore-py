"""Request/response logging middleware."""

import time
import uuid
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from code_explorer.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for structured request/response logging."""

    def __init__(self, app, settings: Settings | None = None) -> None:  # type: ignore[no-untyped-def]
        """Initialize logging middleware."""
        super().__init__(app)
        self.settings = settings or get_settings()

    def get_client_ip(self, request: Request) -> str:
        """Extract client IP respecting proxy headers."""
        if self.settings.trusted_proxy_count > 0:
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                ips = [ip.strip() for ip in xff.split(",")]
                if len(ips) >= self.settings.trusted_proxy_count:
                    return ips[-self.settings.trusted_proxy_count]

        if request.client:
            return request.client.host
        return "unknown"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,  # type: ignore[type-arg]
    ) -> Response:
        """Log request and response."""
        # Generate request ID
        request_id = str(uuid.uuid4())[:8]

        # Extract request info
        method = request.method
        path = request.url.path
        client_ip = self.get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "")[:100]

        # Try to get user ID from auth header
        user_id = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                import jwt

                token = auth_header[7:]
                payload = jwt.decode(token, options={"verify_signature": False})
                user_id = payload.get("sub")
            except Exception:
                pass

        # Bind context for this request
        log = logger.bind(
            request_id=request_id,
            method=method,
            path=path,
            client_ip=client_ip,
        )
        if user_id:
            log = log.bind(user_id=user_id)

        # Log request start
        start_time = time.perf_counter()
        await log.ainfo("Request started")

        # Add request ID to request state for downstream access
        request.state.request_id = request_id

        try:
            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log response
            await log.ainfo(
                "Request completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            # Add request ID header to response
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            await log.aerror(
                "Request failed",
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            raise
