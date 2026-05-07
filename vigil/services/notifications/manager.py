"""
Notification Channel Manager

Manages named notification channels and adapter instances.
Handles priority-based routing: urgent → Signal, digest → email, etc.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Type, List
from datetime import datetime, timezone
import logging

from .interface import (
    NotificationAdapter, NotificationChannel, Notification,
    DeliveryResult, Priority, ChannelStatus, Recipient,
)

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("/data/config/notification_channels.json")


# Default routing: which adapter types handle which priorities.
# Can be overridden in config. First match wins.
DEFAULT_ROUTING = {
    Priority.URGENT: ["signal", "email"],   # Signal first, email fallback
    Priority.HIGH:   ["signal", "email"],
    Priority.NORMAL: ["email", "signal"],   # email first for normal
    Priority.LOW:    ["email"],             # email only for low/digest
}


class NotificationManager:
    """
    Manages notification channels and routes messages by priority.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.channels: Dict[str, NotificationChannel] = {}
        self.adapters: Dict[str, NotificationAdapter] = {}
        self.adapter_classes: Dict[str, Type[NotificationAdapter]] = {}
        self.routing: Dict[Priority, List[str]] = dict(DEFAULT_ROUTING)
        self._default_recipient: Optional[Recipient] = None

        self._load_config()

    def register_adapter_type(
        self, adapter_type: str, adapter_class: Type[NotificationAdapter]
    ) -> None:
        """Register an adapter implementation."""
        self.adapter_classes[adapter_type] = adapter_class
        logger.info(f"✅ Registered notification adapter: {adapter_type}")

    def _load_config(self) -> None:
        """Load channels and routing from config file."""
        if not self.config_path.exists():
            logger.info("No notification config found, starting fresh")
            return

        try:
            config = json.loads(self.config_path.read_text())

            for name, data in config.get("channels", {}).items():
                self.channels[name] = NotificationChannel(
                    name=name,
                    adapter=data.get("adapter", ""),
                    credentials_ref=data.get("credentials_ref", ""),
                    config=data.get("config", {}),
                )

            # Override default routing if specified
            routing_cfg = config.get("routing", {})
            for priority_name, adapter_types in routing_cfg.items():
                try:
                    priority = Priority(priority_name)
                    self.routing[priority] = adapter_types
                except ValueError:
                    logger.warning(f"Unknown priority in routing config: {priority_name}")

            # Default recipient
            default_recip = config.get("default_recipient")
            if default_recip:
                self._default_recipient = Recipient(
                    address=default_recip.get("address", ""),
                    name=default_recip.get("name"),
                )

            logger.info(
                f"✅ Loaded {len(self.channels)} notification channels"
            )
        except Exception as e:
            logger.error(f"❌ Failed to load notification config: {e}")

    def _save_config(self) -> None:
        """Save channels and routing to config file."""
        config = {
            "channels": {},
            "routing": {p.value: types for p, types in self.routing.items()},
        }
        if self._default_recipient:
            config["default_recipient"] = {
                "address": self._default_recipient.address,
                "name": self._default_recipient.name,
            }
        for name, channel in self.channels.items():
            config["channels"][name] = {
                "adapter": channel.adapter,
                "credentials_ref": channel.credentials_ref,
                "config": channel.config,
            }

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, indent=2))

    # ── Channel management ──────────────────────────────────────────────

    def add_channel(
        self,
        name: str,
        adapter: str,
        credentials_ref: str = "",
        config: Optional[Dict] = None,
    ) -> str:
        if name in self.channels:
            return f"❌ Channel '{name}' already exists"
        if adapter not in self.adapter_classes:
            available = ", ".join(self.adapter_classes.keys()) or "none"
            return f"❌ Unknown adapter '{adapter}'. Available: {available}"

        self.channels[name] = NotificationChannel(
            name=name,
            adapter=adapter,
            credentials_ref=credentials_ref,
            config=config or {},
        )
        self._save_config()
        return f"✅ Added notification channel: {name} ({adapter})"

    def remove_channel(self, name: str) -> str:
        if name not in self.channels:
            return f"❌ Channel '{name}' not found"
        self.adapters.pop(name, None)
        del self.channels[name]
        self._save_config()
        return f"✅ Removed notification channel: {name}"

    def list_channels(self) -> str:
        if not self.channels:
            return "🔔 No notification channels configured"
        lines = ["🔔 Notification Channels", "─" * 40]
        for name, channel in self.channels.items():
            connected = "🟢" if name in self.adapters else "⚪"
            lines.append(f"{connected} {name} ({channel.adapter})")
        return "\n".join(lines)

    # ── Adapter lifecycle ───────────────────────────────────────────────

    async def get_adapter(self, channel_name: str) -> Optional[NotificationAdapter]:
        """Get or create an adapter instance for a channel."""
        if channel_name not in self.channels:
            logger.error(f"Channel not found: {channel_name}")
            return None

        if channel_name in self.adapters:
            return self.adapters[channel_name]

        channel = self.channels[channel_name]
        if channel.adapter not in self.adapter_classes:
            logger.error(f"Adapter not registered: {channel.adapter}")
            return None

        adapter_class = self.adapter_classes[channel.adapter]
        adapter = adapter_class(channel)

        if await adapter.connect():
            self.adapters[channel_name] = adapter
            return adapter
        return None

    # ── Sending ─────────────────────────────────────────────────────────

    async def send(
        self,
        message: str,
        subject: Optional[str] = None,
        recipient: Optional[str] = None,
        priority: Priority = Priority.NORMAL,
        channel: Optional[str] = None,
        attachments: Optional[List[str]] = None,
    ) -> DeliveryResult:
        """Send a notification, routing by priority or explicit channel.

        Args:
            message:    The notification body.
            subject:    Optional subject (used by email adapter).
            recipient:  Recipient address. Falls back to default_recipient.
            priority:   Drives automatic channel selection.
            channel:    Explicit channel name — bypasses priority routing.
            attachments: Optional list of file paths.
        """
        # Resolve recipient
        recip = Recipient(address=recipient) if recipient else self._default_recipient
        if not recip or not recip.address:
            return DeliveryResult(
                success=False, channel="none", recipient="",
                error="No recipient specified and no default configured",
            )

        notification = Notification(
            recipient=recip,
            message=message,
            subject=subject,
            priority=priority,
            attachments=attachments or [],
        )

        # Explicit channel override
        if channel:
            return await self._send_via_channel(channel, notification)

        # Priority-based routing — try channels in order
        adapter_types = self.routing.get(priority, ["email"])
        for adapter_type in adapter_types:
            # Find first channel matching this adapter type
            for ch_name, ch in self.channels.items():
                if ch.adapter == adapter_type:
                    result = await self._send_via_channel(ch_name, notification)
                    if result.success:
                        return result
                    logger.warning(
                        f"Channel {ch_name} failed: {result.error}, trying next"
                    )

        return DeliveryResult(
            success=False, channel="none", recipient=recip.address,
            error=f"All channels failed for priority={priority.value}",
        )

    async def _send_via_channel(
        self, channel_name: str, notification: Notification
    ) -> DeliveryResult:
        """Send via a specific named channel."""
        adapter = await self.get_adapter(channel_name)
        if not adapter:
            return DeliveryResult(
                success=False,
                channel=channel_name,
                recipient=notification.recipient.address,
                error=f"Could not connect to channel: {channel_name}",
            )
        try:
            result = adapter.send(notification)
            # Handle both sync and async adapters
            if hasattr(result, "__await__"):
                result = await result
            return result
        except Exception as e:
            logger.error(f"Send failed on {channel_name}: {e}")
            return DeliveryResult(
                success=False,
                channel=channel_name,
                recipient=notification.recipient.address,
                error=str(e),
            )

    # ── Status ──────────────────────────────────────────────────────────

    async def get_status(self) -> Dict[str, str]:
        """Get status of all channels."""
        statuses = {}
        for name, channel in self.channels.items():
            adapter = await self.get_adapter(name)
            if adapter:
                s = await adapter.status()
                statuses[name] = s.value
            else:
                statuses[name] = ChannelStatus.DISCONNECTED.value
        return statuses
