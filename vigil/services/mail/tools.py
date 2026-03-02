"""Mail service MCP tools."""

import json
import logging
from .manager import MailManager
from .adapters.gmail import GmailAdapter

logger = logging.getLogger(__name__)

mail_manager: MailManager = None


def register(mcp) -> None:
    """Register mail tools with the MCP server."""
    global mail_manager

    try:
        mail_manager = MailManager()
        mail_manager.register_adapter_type("gmail", GmailAdapter)
        logger.info("✅ Mail service initialized")
    except Exception as e:
        logger.error(f"❌ Mail service failed to initialize: {e}")
        return

    @mcp.tool()
    async def mail_list_messages(
        account: str,
        folder: str = "INBOX",
        limit: int = 20,
        unread_only: bool = False
    ) -> str:
        """List messages in a folder."""
        page = await mail_manager.list_messages(account, folder, limit, unread_only=unread_only)
        if not page.messages:
            return f"No messages in {folder}"
        lines = [f"📧 Messages in {account}/{folder}", "─" * 40]
        for m in page.messages:
            date_str = m.date.strftime("%m/%d %H:%M") if m.date else ""
            unread = "●" if any(f.value == "unread" for f in m.flags) else " "
            lines.append(f"{unread} {date_str} | {m.sender.email[:25]:<25} | {m.subject[:40]}")
            lines.append(f"    ID: {m.id}")
        if page.next_cursor:
            lines.append(f"\n(more messages available)")
        return "\n".join(lines)

    @mcp.tool()
    async def mail_get_message(account: str, message_id: str) -> str:
        """Get full message with body."""
        msg = await mail_manager.get_message(account, message_id)
        if not msg:
            return f"Message not found: {message_id}"
        lines = [
            f"📧 Message: {msg.subject}",
            "─" * 40,
            f"From: {msg.sender}",
            f"To: {', '.join(str(r) for r in msg.recipients)}",
            f"Date: {msg.date}",
        ]
        if msg.cc:
            lines.append(f"CC: {', '.join(str(r) for r in msg.cc)}")
        if msg.attachments:
            lines.append(f"Attachments: {len(msg.attachments)}")
        lines.append("")
        lines.append(msg.body_text or msg.body_html or "(no body)")
        return "\n".join(lines)

    @mcp.tool()
    async def mail_search(account: str, query: str, limit: int = 20) -> str:
        """Search messages."""
        page = await mail_manager.search(account, query, limit=limit)
        if not page.messages:
            return f"No messages matching: {query}"
        lines = [f"🔍 Search: {query}", "─" * 40]
        for m in page.messages:
            date_str = m.date.strftime("%m/%d") if m.date else ""
            lines.append(f"{date_str} | {m.sender.email[:20]} | {m.subject[:35]}")
            lines.append(f"    ID: {m.id}")
        return "\n".join(lines)

    @mcp.tool()
    async def mail_send(
        account: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        html: bool = False
    ) -> str:
        """Send an email."""
        to_list = [t.strip() for t in to.split(",") if t.strip()]
        cc_list = [c.strip() for c in cc.split(",") if c.strip()] if cc else None
        return await mail_manager.send(account, to_list, subject, body, cc=cc_list, html=html)

    @mcp.tool()
    async def mail_delete(account: str, message_id: str, permanent: bool = False) -> str:
        """Delete a message (moves to trash by default)."""
        return await mail_manager.delete(account, message_id, permanent)

    @mcp.tool()
    async def mail_mark_read(account: str, message_id: str, read: bool = True) -> str:
        """Mark message as read or unread."""
        return await mail_manager.mark_read(account, message_id, read)

    logger.info("✅ Registered 6 mail tools")
