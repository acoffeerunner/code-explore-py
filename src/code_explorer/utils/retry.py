"""Retry utilities with exponential backoff."""

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def log_retry(retry_state: RetryCallState) -> None:
    """Log retry attempts."""
    if retry_state.attempt_number > 1:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        logger.warning(
            "Retrying operation",
            attempt=retry_state.attempt_number,
            exception=str(exception) if exception else None,
            function=retry_state.fn.__name__ if retry_state.fn else "unknown",
        )


def with_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 60.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator for adding retry logic with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries in seconds
        max_wait: Maximum wait time between retries in seconds
        exceptions: Tuple of exception types to retry on

    Returns:
        Decorated function with retry logic
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(exceptions),
            before_sleep=log_retry,
            reraise=True,
        )
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return func(*args, **kwargs)

        return wrapper

    return decorator


def with_async_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 60.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator for adding retry logic to async functions.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries in seconds
        max_wait: Maximum wait time between retries in seconds
        exceptions: Tuple of exception types to retry on

    Returns:
        Decorated async function with retry logic
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(exceptions),
            before_sleep=log_retry,
            reraise=True,
        )
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:  # type: ignore[misc]
            return await func(*args, **kwargs)  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


class RetryableError(Exception):
    """Base exception for errors that should trigger retry."""

    pass


class RateLimitError(RetryableError):
    """Raised when API rate limit is hit."""

    pass


class TransientError(RetryableError):
    """Raised for transient/temporary errors."""

    pass
