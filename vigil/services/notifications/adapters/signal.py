"""
Signal Notification Adapter

Sends messages via bbernhard/signal-cli-rest-api Docker container.
The container runs as a linked device on Matt's Signal account.

Config keys (in channel.config):
    api_url:  Base URL of the signal-cli-rest-api (e.g. "http://signal-api:8080")
    sender:   Signal sender number in international format (e.g. "+1XXXXXXXXXX")

The sender number is the number the container is registered/linked to.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..interface import (
    NotificationAdapter, NotificationChannel, Notification,
    DeliveryResult, ChannelStatus,
)

logger = logging.getLogger(__name__)

# Timeout for HTTP calls to the signal-cli-rest-api
REQUEST_TIMEOUT = 30.0


class SignalAdapter(NotificationAdapter):
    """Send notifications via Signal Messenger."""

    adapter_type = "signal"

    def __init__(self, channel: NotificationChannel):
        super().__init__(channel)
        self._api_url: str = channel.config.get("api_url", "http://signal-api:8080")
        self._sender: str = channel.config.get("sender", "")
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        """Verify the signal-cli-rest-api is reachable and registered."""
        try:
            self._client = httpx.AsyncClient(
                base_url=self._api_url, timeout=REQUEST_TIMEOUT
            )
            # Health check — the /v1/about endpoint returns registration info
            resp = await self._client.get("/v1/about")
            if resp.status_code == 200:
                logger.info(f"✅ Signal adapter connected: {self._api_url}")
                return True
            logger.warning(
                f"Signal API returned {resp.status_code}: {resp.text[:200]}"
            )
            return False
        except Exception as e:
            logger.error(f"❌ Signal adapter connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send(self, notification: Notification) -> DeliveryResult:
        """Send a Signal message."""
        if not self._client:
            return DeliveryResult(
                success=False, channel=self.adapter_type,
                recipient=notification.recipient.address,
                error="Not connected",
            )

        recipient = notification.recipient.address
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "message": notification.message,
            "number": self._sender,
            "recipients": [recipient],
        }

        # Attach files if any (signal-cli-rest-api supports base64 attachments)
        if notification.attachments:
            import base64
            from pathlib import Path
            attachments_data = []
            for fpath in notification.attachments:
                p = Path(fpath)
                if p.exists() and p.stat().st_size < 10_000_000:  # 10MB limit
                    data = base64.b64encode(p.read_bytes()).decode()
                    attachments_data.append(data)
            if attachments_data:
                payload["base64_attachments"] = attachments_data

        try:
            resp = await self._client.post("/v2/send", json=payload)
            if resp.status_code == 201:
                logger.info(f"✅ Signal message sent to {recipient}")
                return DeliveryResult(
                    success=True,
                    channel=self.adapter_type,
                    recipient=recipient,
                    timestamp=now,
                )
            else:
                error = f"Signal API returned {resp.status_code}: {resp.text[:300]}"
                logger.error(error)
                return DeliveryResult(
                    success=False, channel=self.adapter_type,
                    recipient=recipient, error=error, timestamp=now,
                )
        except Exception as e:
            error = f"Signal send failed: {e}"
            logger.error(error)
            return DeliveryResult(
                success=False, channel=self.adapter_type,
                recipient=recipient, error=error, timestamp=now,
            )

    async def status(self) -> ChannelStatus:
        """Check if the signal-cli-rest-api is up and registered."""
        if not self._client:
            return ChannelStatus.DISCONNECTED
        try:
            resp = await self._client.get("/v1/about")
            if resp.status_code == 200:
                return ChannelStatus.CONNECTED
            return ChannelStatus.ERROR
        except Exception:
            return ChannelStatus.ERROR
