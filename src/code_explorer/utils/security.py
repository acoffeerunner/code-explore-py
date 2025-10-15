"""Security utilities for URL validation, SSRF protection, and path sanitization."""

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import structlog

from code_explorer.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class SecurityError(Exception):
    """Security validation error."""

    pass


class SSRFError(SecurityError):
    """SSRF protection triggered."""

    pass


class InvalidURLError(SecurityError):
    """Invalid URL format or scheme."""

    pass


class PathTraversalError(SecurityError):
    """Path traversal attempt detected."""

    pass


def is_private_ip(ip: str) -> bool:
    """
    Check if an IP address is private/internal.

    Blocks:
    - Private ranges (10.x, 172.16-31.x, 192.168.x)
    - Loopback (127.x, ::1)
    - Link-local (169.254.x, fe80::)
    - Multicast
    - Unspecified (0.0.0.0, ::)
    """
    try:
        addr = ipaddress.ip_address(ip)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_unspecified
            or addr.is_reserved
        )
    except ValueError:
        # Invalid IP format
        return True  # Err on the side of caution


def validate_url_ssrf(
    url: str,
    allowed_hosts: list[str] | None = None,
    require_https: bool = True,
) -> str:
    """
    Validate URL for SSRF protection.

    Args:
        url: URL to validate
        allowed_hosts: Optional allowlist of permitted hostnames
        require_https: Whether to require HTTPS scheme

    Returns:
        The validated URL

    Raises:
        InvalidURLError: If URL format is invalid
        SSRFError: If URL targets internal/private resources
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise InvalidURLError(f"Invalid URL format: {e}")

    # Check scheme
    if require_https:
        if parsed.scheme != "https":
            raise InvalidURLError("Only HTTPS URLs are allowed")
    elif parsed.scheme not in ("http", "https"):
        raise InvalidURLError(f"Invalid URL scheme: {parsed.scheme}")

    # Check for credentials in URL
    if parsed.username or parsed.password:
        raise InvalidURLError("URLs with embedded credentials are not allowed")

    # Get hostname
    hostname = parsed.hostname
    if not hostname:
        raise InvalidURLError("URL must have a hostname")

    # Check against allowlist if provided
    if allowed_hosts:
        if hostname.lower() not in [h.lower() for h in allowed_hosts]:
            raise SSRFError(f"Host '{hostname}' is not in the allowed hosts list")

    # Resolve hostname and check for private IPs
    try:
        # Get all IP addresses for the hostname
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in infos:
            ip = info[4][0]
            if is_private_ip(ip):
                logger.warning(
                    "SSRF protection: blocked private IP",
                    url=url,
                    hostname=hostname,
                    ip=ip,
                )
                raise SSRFError(f"URL resolves to private/internal IP: {ip}")
    except socket.gaierror as e:
        raise InvalidURLError(f"Cannot resolve hostname '{hostname}': {e}")

    return url


def validate_git_url(url: str, settings: Settings | None = None) -> str:
    """
    Validate a Git repository URL.

    Args:
        url: Git URL to validate
        settings: Optional settings (uses global if not provided)

    Returns:
        The validated URL

    Raises:
        InvalidURLError: If URL is not a valid Git URL
        SSRFError: If URL targets internal resources
    """
    if settings is None:
        settings = get_settings()

    # Validate with SSRF protection and host allowlist
    return validate_url_ssrf(
        url=url,
        allowed_hosts=settings.allowed_git_hosts,
        require_https=True,
    )


def validate_webhook_url(url: str, settings: Settings | None = None) -> str:
    """
    Validate a webhook callback URL.

    Same SSRF protections as Git URLs but without host allowlist
    (users can specify their own webhook endpoints).

    Args:
        url: Webhook URL to validate
        settings: Optional settings

    Returns:
        The validated URL

    Raises:
        InvalidURLError: If URL is invalid
        SSRFError: If URL targets internal resources
    """
    return validate_url_ssrf(
        url=url,
        allowed_hosts=None,  # No host allowlist for webhooks
        require_https=True,
    )


def sanitize_file_path(path: str, base_path: Path) -> Path:
    """
    Sanitize a file path to prevent path traversal attacks.

    Args:
        path: Relative path to sanitize
        base_path: Base directory that paths must be within

    Returns:
        Resolved absolute path

    Raises:
        PathTraversalError: If path escapes base directory
    """
    # Reject absolute paths
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise PathTraversalError("Absolute paths are not allowed")

    # Reject obvious traversal attempts
    if ".." in path:
        raise PathTraversalError("Path traversal detected: '..' not allowed")

    # Resolve the full path
    full_path = (base_path / path).resolve()

    # Ensure it's still within base_path
    try:
        full_path.relative_to(base_path.resolve())
    except ValueError:
        raise PathTraversalError(
            f"Path '{path}' escapes base directory"
        )

    return full_path


def sanitize_branch_name(branch: str) -> str:
    """
    Sanitize a Git branch name.

    Args:
        branch: Branch name to sanitize

    Returns:
        Sanitized branch name

    Raises:
        SecurityError: If branch name contains dangerous characters
    """
    # Reject empty branch names
    if not branch or not branch.strip():
        raise SecurityError("Branch name cannot be empty")

    branch = branch.strip()

    # Reject dangerous characters that could be used for command injection
    dangerous_chars = [";", "&", "|", "$", "`", "(", ")", "{", "}", "<", ">", "\\", "\n", "\r"]
    for char in dangerous_chars:
        if char in branch:
            raise SecurityError(f"Branch name contains forbidden character: {repr(char)}")

    # Reject branch names starting with -
    if branch.startswith("-"):
        raise SecurityError("Branch name cannot start with '-'")

    return branch
