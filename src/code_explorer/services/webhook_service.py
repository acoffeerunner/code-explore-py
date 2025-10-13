"""Webhook delivery service with HMAC signatures and retry logic."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from code_explorer.config import Settings, get_settings
from code_explorer.models.db import Repo, WebhookDelivery
from code_explorer.models.domain import DeliveryStatus, RepoStatus, WebhookEvent, WebhookPayload

logger = structlog.get_logger(__name__)


class WebhookError(Exception):
    """Error during webhook delivery."""

    pass


class WebhookService:
    """Service for webhook delivery with HMAC signatures."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize webhook service."""
        self.settings = settings or get_settings()
        self.max_retries = self.settings.webhook_max_retries
        self.retry_window_hours = self.settings.webhook_retry_window_hours

    def generate_secret(self) -> str:
        """Generate a secure webhook secret."""
        return secrets.token_urlsafe(32)

    def sign_payload(self, payload: bytes, secret: str) -> str:
        """
        Generate HMAC-SHA256 signature for payload.

        Args:
            payload: JSON payload bytes
            secret: Webhook secret

        Returns:
            Signature in format 'sha256={hex_digest}'
        """
        signature = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={signature}"

    def verify_signature(self, payload: bytes, signature: str, secret: str) -> bool:
        """
        Verify HMAC-SHA256 signature.

        Args:
            payload: JSON payload bytes
            signature: Signature from header
            secret: Webhook secret

        Returns:
            True if signature is valid
        """
        expected = self.sign_payload(payload, secret)
        return hmac.compare_digest(signature, expected)

    async def create_delivery(
        self,
        db: AsyncSession,
        repo: Repo,
        event: WebhookEvent,
        commit_hash: str,
    ) -> WebhookDelivery | None:
        """
        Create a webhook delivery record.

        Args:
            db: Database session
            repo: Repository triggering the webhook
            event: Event type
            commit_hash: Current commit hash

        Returns:
            WebhookDelivery if webhook URL is set, None otherwise
        """
        if not repo.webhook_url or not repo.webhook_secret:
            return None

        # Generate idempotency key
        idempotency_key = f"{repo.id}:{event.value}:{commit_hash}"

        # Check for existing delivery with same idempotency key
        existing = await db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.idempotency_key == idempotency_key
            )
        )
        if existing.scalar_one_or_none():
            logger.info("Webhook delivery already exists", idempotency_key=idempotency_key)
            return None

        # Create payload
        payload = WebhookPayload(
            event=event,
            repo_id=repo.id,
            url=repo.url,
            branch=repo.branch,
            commit_hash=commit_hash,
            status=RepoStatus(repo.status),
            timestamp=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )

        delivery = WebhookDelivery(
            repo_id=repo.id,
            event_type=event.value,
            idempotency_key=idempotency_key,
            payload=payload.model_dump(mode="json"),
            status=DeliveryStatus.PENDING.value,
            next_retry_at=datetime.now(UTC),
        )
        db.add(delivery)
        await db.flush()

        logger.info("Created webhook delivery", delivery_id=str(delivery.id))
        return delivery

    async def deliver(
        self,
        db: AsyncSession,
        delivery: WebhookDelivery,
    ) -> bool:
        """
        Attempt to deliver a webhook.

        Args:
            db: Database session
            delivery: Webhook delivery record

        Returns:
            True if delivery succeeded
        """
        log = logger.bind(
            delivery_id=str(delivery.id),
            attempt=delivery.attempts + 1,
        )

        # Get repo for webhook URL and secret
        result = await db.execute(select(Repo).where(Repo.id == delivery.repo_id))
        repo = result.scalar_one_or_none()

        if not repo or not repo.webhook_url or not repo.webhook_secret:
            await log.awarning("Webhook URL or secret not found")
            delivery.status = DeliveryStatus.FAILED.value
            await db.flush()
            return False

        # Prepare payload
        payload_bytes = WebhookPayload(**delivery.payload).model_dump_json().encode()
        signature = self.sign_payload(payload_bytes, repo.webhook_secret)

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": str(int(datetime.now(UTC).timestamp())),
            "X-Webhook-Id": str(delivery.id),
            "X-Idempotency-Key": delivery.idempotency_key,
        }

        # Update attempt count
        delivery.attempts += 1
        delivery.last_attempt_at = datetime.now(UTC)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    repo.webhook_url,
                    content=payload_bytes,
                    headers=headers,
                )

            delivery.response_status = response.status_code
            delivery.response_body = response.text[:1000]  # Limit stored response

            if 200 <= response.status_code < 300:
                await log.ainfo("Webhook delivered successfully")
                delivery.status = DeliveryStatus.DELIVERED.value
                await db.flush()
                return True

            await log.awarning(
                "Webhook delivery failed",
                status_code=response.status_code,
            )

        except httpx.TimeoutException:
            await log.awarning("Webhook delivery timed out")
            delivery.response_body = "Timeout"
        except httpx.RequestError as e:
            await log.awarning("Webhook delivery error", error=str(e))
            delivery.response_body = str(e)[:1000]

        # Calculate next retry
        if delivery.attempts >= self.max_retries:
            await log.awarning("Webhook exhausted all retries")
            delivery.status = DeliveryStatus.EXHAUSTED.value
        else:
            # Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s
            delay_seconds = 2 ** (delivery.attempts - 1)
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            await log.ainfo("Webhook scheduled for retry", delay_seconds=delay_seconds)

        await db.flush()
        return False

    async def trigger_indexed(
        self,
        db: AsyncSession,
        repo: Repo,
        commit_hash: str,
    ) -> None:
        """Trigger repo.indexed webhook."""
        delivery = await self.create_delivery(
            db=db,
            repo=repo,
            event=WebhookEvent.REPO_INDEXED,
            commit_hash=commit_hash,
        )
        if delivery:
            await self.deliver(db, delivery)

    async def trigger_failed(
        self,
        db: AsyncSession,
        repo: Repo,
        commit_hash: str = "unknown",
    ) -> None:
        """Trigger repo.failed webhook."""
        delivery = await self.create_delivery(
            db=db,
            repo=repo,
            event=WebhookEvent.REPO_FAILED,
            commit_hash=commit_hash,
        )
        if delivery:
            await self.deliver(db, delivery)
