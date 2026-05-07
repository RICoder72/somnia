"""Notification service MCP tools."""

import json
import logging

from fastmcp import Context

from .manager import NotificationManager
from .interface import Priority
from .adapters.signal import SignalAdapter
from .adapters.email import EmailNotifyAdapter
from .adapters.twilio import TwilioAdapter

logger = logging.getLogger(__name__)

notification_manager: NotificationManager = None


def register(mcp) -> None:
    """Register notification tools with the MCP server."""
    global notification_manager

    try:
        notification_manager = NotificationManager()
        notification_manager.register_adapter_type("signal", SignalAdapter)
        notification_manager.register_adapter_type("email", EmailNotifyAdapter)
        notification_manager.register_adapter_type("twilio", TwilioAdapter)
        logger.info("✅ Notification service initialized")
    except Exception as e:
        logger.error(f"❌ Notification service failed to initialize: {e}")
        return

    @mcp.tool()
    async def notify_send(
        ctx: Context,
        message: str,
        subject: str = "",
        recipient: str = "",
        priority: str = "normal",
        channel: str = "",
    ) -> str:
        """Send a notification via Signal, email, or auto-routed by priority.

        Priority routing (default):
          urgent/high → Signal first, email fallback
          normal      → email first, Signal fallback
          low         → email only

        Args:
            message:   The notification body text.
            subject:   Optional subject line (used by email, ignored by Signal).
            recipient: Recipient address. Omit to use default recipient.
            priority:  low | normal | high | urgent. Drives channel selection.
            channel:   Explicit channel name — bypasses priority routing.
        """
        try:
            prio = Priority(priority.lower())
        except ValueError:
            prio = Priority.NORMAL

        result = await notification_manager.send(
            message=message,
            subject=subject or None,
            recipient=recipient or None,
            priority=prio,
            channel=channel or None,
        )

        if result.success:
            return (
                f"✅ Notification sent via {result.channel} "
                f"to {result.recipient}"
            )
        else:
            return (
                f"❌ Notification failed: {result.error}\n"
                f"Channel: {result.channel}, Recipient: {result.recipient}"
            )

    @mcp.tool()
    async def notify_status(ctx: Context) -> str:
        """Check the status of all configured notification channels."""
        channels = notification_manager.list_channels()
        statuses = await notification_manager.get_status()

        lines = [channels, "", "Channel Health", "─" * 40]
        for name, status in statuses.items():
            icon = {"connected": "🟢", "error": "🔴", "disconnected": "⚪"}.get(
                status, "❓"
            )
            lines.append(f"{icon} {name}: {status}")

        routing_lines = ["", "Routing Rules", "─" * 40]
        for prio, adapters in notification_manager.routing.items():
            routing_lines.append(f"  {prio.value:>8} → {' → '.join(adapters)}")
        lines.extend(routing_lines)

        default = notification_manager._default_recipient
        if default:
            lines.extend(["", f"Default recipient: {default}"])

        return "\n".join(lines)

    logger.info("✅ Registered 2 notification tools")
