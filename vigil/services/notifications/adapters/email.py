"""
Email Notification Adapter

Thin wrapper around Vigil's existing mail service.
Sends notifications as emails via whichever mail account is configured.

Config keys (in channel.config):
    mail_account:  Name of the mail account in mail_accounts.json (e.g. "matt-personal")
    from_name:     Display name for the sender (e.g. "Somnia Nuntius")
    subject_prefix: Prepended to subject lines (e.g. "[Somnia]")
"""

import logging
from datetime import datetime, timezone

from ..interface import (
    NotificationAdapter, NotificationChannel, Notification,
    DeliveryResult, ChannelStatus,
)

logger = logging.getLogger(__name__)


class EmailNotifyAdapter(NotificationAdapter):
    """Send notifications as emails via the existing mail service."""

    adapter_type = "email"

    def __init__(self, channel: NotificationChannel):
        super().__init__(channel)
        self._mail_account: str = channel.config.get("mail_account", "")
        self._from_name: str = channel.config.get("from_name", "Somnia")
        self._subject_prefix: str = channel.config.get("subject_prefix", "[Somnia]")
        self._mail_manager = None

    async def connect(self) -> bool:
        """Verify the underlying mail account is accessible."""
        try:
            from services.mail.tools import mail_manager
            if mail_manager is None:
                logger.error("Mail service not initialized")
                return False
            self._mail_manager = mail_manager

            # Verify the named mail account exists
            if self._mail_account not in self._mail_manager.accounts:
                logger.error(
                    f"Mail account '{self._mail_account}' not found. "
                    f"Available: {list(self._mail_manager.accounts.keys())}"
                )
                return False

            logger.info(
                f"✅ Email notification adapter connected via {self._mail_account}"
            )
            return True
        except Exception as e:
            logger.error(f"❌ Email notification adapter failed: {e}")
            return False

    async def disconnect(self) -> None:
        self._mail_manager = None

    async def send(self, notification: Notification) -> DeliveryResult:
        """Send notification as an email."""
        if not self._mail_manager:
            return DeliveryResult(
                success=False, channel=self.adapter_type,
                recipient=notification.recipient.address,
                error="Not connected",
            )

        now = datetime.now(timezone.utc).isoformat()
        recipient = notification.recipient.address

        # Build subject
        subject = notification.subject or "Notification"
        if self._subject_prefix:
            subject = f"{self._subject_prefix} {subject}"

        try:
            result = await self._mail_manager.send(
                account_name=self._mail_account,
                to=[recipient],
                subject=subject,
                body=notification.message,
                html=False,
            )

            if result and "❌" not in result:
                logger.info(f"✅ Email notification sent to {recipient}")
                return DeliveryResult(
                    success=True, channel=self.adapter_type,
                    recipient=recipient, timestamp=now,
                )
            else:
                return DeliveryResult(
                    success=False, channel=self.adapter_type,
                    recipient=recipient, error=result, timestamp=now,
                )
        except Exception as e:
            error = f"Email send failed: {e}"
            logger.error(error)
            return DeliveryResult(
                success=False, channel=self.adapter_type,
                recipient=recipient, error=error, timestamp=now,
            )

    async def status(self) -> ChannelStatus:
        """Check if the mail account is accessible."""
        if not self._mail_manager:
            return ChannelStatus.DISCONNECTED
        if self._mail_account in self._mail_manager.accounts:
            return ChannelStatus.CONNECTED
        return ChannelStatus.ERROR
