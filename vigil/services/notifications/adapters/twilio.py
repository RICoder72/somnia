"""
Twilio SMS Notification Adapter

Sends notifications via Twilio SMS API. Somnia's own phone number.

Config keys (in channel.config):
    account_sid:  Twilio Account SID
    auth_token:   Twilio Auth Token (or credentials_ref for 1Password)
    from_number:  Somnia's Twilio phone number in E.164 format (+1...)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import base64

from ..interface import (
    NotificationAdapter, NotificationChannel, Notification,
    DeliveryResult, ChannelStatus,
)

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
REQUEST_TIMEOUT = 30.0


class TwilioAdapter(NotificationAdapter):
    """Send notifications via Twilio SMS."""

    adapter_type = "twilio"

    def __init__(self, channel: NotificationChannel):
        super().__init__(channel)
        self._account_sid: str = channel.config.get("account_sid", "")
        self._auth_token: str = channel.config.get("auth_token", "")
        self._from_number: str = channel.config.get("from_number", "")
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        """Verify Twilio credentials are valid."""
        if not self._account_sid or not self._auth_token or not self._from_number:
            logger.error("Twilio adapter missing account_sid, auth_token, or from_number")
            return False

        try:
            auth = base64.b64encode(
                f"{self._account_sid}:{self._auth_token}".encode()
            ).decode()

            self._client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={"Authorization": f"Basic {auth}"},
            )

            # Verify credentials by fetching account info
            resp = await self._client.get(
                f"{TWILIO_API_BASE}/Accounts/{self._account_sid}.json"
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info(
                    f"✅ Twilio adapter connected: {data.get('friendly_name', self._account_sid)}"
                )
                return True

            logger.error(f"Twilio auth failed: {resp.status_code} {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"❌ Twilio adapter connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send(self, notification: Notification) -> DeliveryResult:
        """Send an SMS via Twilio."""
        if not self._client:
            return DeliveryResult(
                success=False, channel=self.adapter_type,
                recipient=notification.recipient.address,
                error="Not connected",
            )

        recipient = notification.recipient.address
        now = datetime.now(timezone.utc).isoformat()

        # Truncate message to SMS limits (1600 chars for concatenated SMS)
        message = notification.message[:1600]

        try:
            resp = await self._client.post(
                f"{TWILIO_API_BASE}/Accounts/{self._account_sid}/Messages.json",
                data={
                    "To": recipient,
                    "From": self._from_number,
                    "Body": message,
                },
            )

            if resp.status_code == 201:
                data = resp.json()
                logger.info(f"✅ Twilio SMS sent to {recipient}: {data.get('sid')}")
                return DeliveryResult(
                    success=True,
                    channel=self.adapter_type,
                    recipient=recipient,
                    message_id=data.get("sid"),
                    timestamp=now,
                )
            else:
                error = f"Twilio API returned {resp.status_code}: {resp.text[:300]}"
                logger.error(error)
                return DeliveryResult(
                    success=False, channel=self.adapter_type,
                    recipient=recipient, error=error, timestamp=now,
                )
        except Exception as e:
            error = f"Twilio send failed: {e}"
            logger.error(error)
            return DeliveryResult(
                success=False, channel=self.adapter_type,
                recipient=recipient, error=error, timestamp=now,
            )

    async def status(self) -> ChannelStatus:
        """Check Twilio connectivity."""
        if not self._client:
            return ChannelStatus.DISCONNECTED
        try:
            resp = await self._client.get(
                f"{TWILIO_API_BASE}/Accounts/{self._account_sid}.json"
            )
            if resp.status_code == 200:
                return ChannelStatus.CONNECTED
            return ChannelStatus.ERROR
        except Exception:
            return ChannelStatus.ERROR
