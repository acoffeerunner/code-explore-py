"""Dual-layer rate limiting middleware (per-user + per-IP)."""

import time
from typing import Callable

import redis.asyncio as redis
import structlog
from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from code_explorer.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Dual-layer rate limiting middleware.

    Applies two independent rate limits:
    1. Per-user: Based on authenticated user ID (primary)
    2. Per-IP: Based on client IP (abuse prevention)

    Both limits use sliding window counters stored in Redis.
    """

    def __init__(self, app, settings: Settings | None = None) -> None:  # type: ignore[no-untyped-def]
        """Initialize rate limit middleware."""
        super().__init__(app)
        self.settings = settings or get_settings()
        self._redis: redis.Redis | None = None  # type: ignore[type-arg]

    async def get_redis(self) -> redis.Redis:  # type: ignore[type-arg]
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(self.settings.redis_url)
        return self._redis

    def get_client_ip(self, request: Request) -> str:
        """
        Extract client IP, respecting trusted proxy headers.

        Uses trusted_proxy_count to determine how many X-Forwarded-For
        entries to skip from the right.
        """
        if self.settings.trusted_proxy_count > 0:
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                ips = [ip.strip() for ip in xff.split(",")]
                if len(ips) >= self.settings.trusted_proxy_count:
                    return ips[-self.settings.trusted_proxy_count]

        # Fallback to direct connection
        if request.client:
            return request.client.host
        return "unknown"

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """
        Check rate limit using sliding window counter.

        Args:
            key: Redis key for this rate limit bucket
            limit: Maximum requests allowed in window
            window_seconds: Window duration in seconds

        Returns:
            Tuple of (is_allowed, remaining, reset_seconds)
        """
        r = await self.get_redis()
        now = time.time()
        window_start = now - window_seconds

        pipe = r.pipeline()

        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)

        # Count current entries
        pipe.zcard(key)

        # Add current request
        pipe.zadd(key, {str(now): now})

        # Set expiry
        pipe.expire(key, window_seconds + 1)

        results = await pipe.execute()
        count = results[1]  # zcard result

        remaining = max(0, limit - count - 1)
        reset_at = int(now + window_seconds)

        if count >= limit:
            return False, 0, reset_at

        return True, remaining, reset_at

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,  # type: ignore[type-arg]
    ) -> Response:
        """Process request with rate limiting."""
        # Skip rate limiting for health endpoints
        if request.url.path.startswith("/health"):
            return await call_next(request)

        settings = self.settings
        client_ip = self.get_client_ip(request)

        # Check IP-based rate limit first (abuse prevention)
        ip_key = f"ratelimit:ip:{client_ip}"
        ip_allowed, ip_remaining, ip_reset = await self.check_rate_limit(
            key=ip_key,
            limit=settings.rate_limit_per_ip,
            window_seconds=settings.rate_limit_window_seconds,
        )

        if not ip_allowed:
            logger.warning("IP rate limit exceeded", ip=client_ip)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please slow down.",
                headers={
                    "Retry-After": str(ip_reset - int(time.time())),
                    "X-RateLimit-Limit": str(settings.rate_limit_per_ip),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(ip_reset),
                },
            )

        # Check user-based rate limit if authenticated
        user_id = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            # Try to extract user ID from JWT (non-blocking)
            try:
                import jwt

                token = auth_header[7:]
                # Decode without verification just to get the subject
                # (verification happens in auth middleware)
                payload = jwt.decode(token, options={"verify_signature": False})
                user_id = payload.get("sub")
            except Exception:
                pass

        user_remaining = ip_remaining
        user_reset = ip_reset

        if user_id:
            user_key = f"ratelimit:user:{user_id}"
            user_allowed, user_remaining, user_reset = await self.check_rate_limit(
                key=user_key,
                limit=settings.rate_limit_per_user,
                window_seconds=settings.rate_limit_window_seconds,
            )

            if not user_allowed:
                logger.warning("User rate limit exceeded", user_id=user_id)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please slow down.",
                    headers={
                        "Retry-After": str(user_reset - int(time.time())),
                        "X-RateLimit-Limit": str(settings.rate_limit_per_user),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(user_reset),
                    },
                )

        # Process request
        response = await call_next(request)

        # Add rate limit headers to response
        limit = settings.rate_limit_per_user if user_id else settings.rate_limit_per_ip
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(user_remaining)
        response.headers["X-RateLimit-Reset"] = str(user_reset)

        return response
