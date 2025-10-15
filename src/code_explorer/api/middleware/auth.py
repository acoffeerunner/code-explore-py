"""Supabase JWT authentication middleware."""

import functools
from typing import Annotated
from uuid import UUID

import httpx
import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel

from code_explorer.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# HTTP Bearer token security scheme
security = HTTPBearer(auto_error=True)

# Cache for JWKS client
_jwks_client: PyJWKClient | None = None


class AuthenticatedUser(BaseModel):
    """Authenticated user information from Supabase JWT."""

    user_id: UUID
    email: str | None = None
    role: str = "authenticated"


def get_jwks_client(supabase_url: str) -> PyJWKClient:
    """Get or create cached JWKS client for Supabase."""
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
    return _jwks_client


async def verify_supabase_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """
    Verify Supabase JWT and extract user information.

    Supports both ES256 (new Supabase projects) and HS256 (legacy).

    Args:
        credentials: Bearer token from Authorization header
        settings: Application settings

    Returns:
        AuthenticatedUser with user_id, email, and role

    Raises:
        HTTPException: If token is invalid, expired, or missing
    """
    token = credentials.credentials

    try:
        # First, try to get the signing key from JWKS (ES256)
        try:
            jwks_client = get_jwks_client(settings.supabase_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            key = signing_key.key
            algorithms = ["ES256"]
        except (jwt.exceptions.PyJWKClientError, jwt.exceptions.DecodeError):
            # Fall back to HS256 with secret
            key = settings.supabase_jwt_secret.get_secret_value()
            algorithms = ["HS256"]

        # Decode and verify the JWT
        payload = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience="authenticated",
            options={
                "require": ["sub", "exp", "aud"],
                "verify_exp": True,
                "verify_aud": True,
            },
        )

        # Extract user information from token
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing subject claim",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return AuthenticatedUser(
            user_id=UUID(user_id),
            email=payload.get("email"),
            role=payload.get("role", "authenticated"),
        )

    except jwt.ExpiredSignatureError:
        await logger.awarning("JWT token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidAudienceError:
        await logger.awarning("JWT invalid audience")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token audience",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        await logger.awarning("JWT validation failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except ValueError as e:
        await logger.awarning("Invalid user ID in token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID format",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Type alias for dependency injection
CurrentUser = Annotated[AuthenticatedUser, Depends(verify_supabase_token)]


async def get_optional_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser | None:
    """
    Optionally verify Supabase JWT if present.

    Use this for endpoints that work with or without authentication.

    Args:
        request: FastAPI request object
        settings: Application settings

    Returns:
        AuthenticatedUser if valid token present, None otherwise
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # Remove "Bearer " prefix

    try:
        # Try ES256 (JWKS) first, fall back to HS256
        try:
            jwks_client = get_jwks_client(settings.supabase_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            key = signing_key.key
            algorithms = ["ES256"]
        except (jwt.exceptions.PyJWKClientError, jwt.exceptions.DecodeError):
            key = settings.supabase_jwt_secret.get_secret_value()
            algorithms = ["HS256"]

        payload = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience="authenticated",
            options={
                "require": ["sub", "exp", "aud"],
                "verify_exp": True,
                "verify_aud": True,
            },
        )

        user_id = payload.get("sub")
        if user_id:
            return AuthenticatedUser(
                user_id=UUID(user_id),
                email=payload.get("email"),
                role=payload.get("role", "authenticated"),
            )
    except (jwt.InvalidTokenError, ValueError):
        pass

    return None


# Type alias for optional user dependency
OptionalUser = Annotated[AuthenticatedUser | None, Depends(get_optional_user)]
