"""Calendar service MCP tools."""

import json
import logging

from fastmcp import Context

from .manager import CalendarManager
from .adapters.gcal import GCalAdapter

from core.binding_helpers import resolve_or_error

logger = logging.getLogger(__name__)

calendar_manager: CalendarManager = None


def register(mcp) -> None:
    """Register calendar tools with the MCP server."""
    global calendar_manager

    try:
        calendar_manager = CalendarManager()
        calendar_manager.register_adapter_type("gcal", GCalAdapter)
        logger.info("✅ Calendar service initialized")
    except Exception as e:
        logger.error(f"❌ Calendar service failed to initialize: {e}")
        return

    @mcp.tool()
    async def calendar_list_calendars(ctx: Context, account: str = "") -> str:
        """List available calendars. Uses the active workspace's calendar binding if account is omitted."""
        account, err = await resolve_or_error(ctx, account, "calendar")
        if err:
            return err
        calendars = await calendar_manager.list_calendars(account)
        if not calendars:
            return f"No calendars found or could not connect to {account}"
        lines = [f"📅 Calendars in {account}", "─" * 40]
        for c in calendars:
            primary = " (primary)" if c.primary else ""
            lines.append(f"  {c.name}{primary}")
            lines.append(f"    ID: {c.id}")
        return "\n".join(lines)

    @mcp.tool()
    async def calendar_list_events(
        ctx: Context,
        account: str = "",
        calendar_id: str = "primary",
        days: int = 7,
        limit: int = 50
    ) -> str:
        """List upcoming events. Uses the active workspace's calendar binding if account is omitted."""
        account, err = await resolve_or_error(ctx, account, "calendar")
        if err:
            return err
        from datetime import datetime, timedelta, timezone
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        page = await calendar_manager.list_events(account, calendar_id, start, end, limit)
        if not page.events:
            return f"No events in the next {days} days"
        lines = [f"📅 Events ({days} days)", "─" * 40]
        for e in page.events:
            date_str = e.start.strftime("%m/%d %H:%M") if not e.all_day else e.start.strftime("%m/%d") + " (all day)"
            lines.append(f"{date_str} | {e.title}")
            if e.location:
                lines.append(f"    📍 {e.location}")
            lines.append(f"    ID: {e.id}")
        return "\n".join(lines)

    @mcp.tool()
    async def calendar_get_event(ctx: Context, calendar_id: str, event_id: str, account: str = "") -> str:
        """Get full event details. Uses the active workspace's calendar binding if account is omitted."""
        account, err = await resolve_or_error(ctx, account, "calendar")
        if err:
            return err
        event = await calendar_manager.get_event(account, calendar_id, event_id)
        if not event:
            return f"Event not found: {event_id}"
        lines = [
            f"📅 {event.title}",
            "─" * 40,
            f"Start: {event.start}",
            f"End: {event.end}",
        ]
        if event.location:
            lines.append(f"Location: {event.location}")
        if event.description:
            lines.append(f"\nDescription:\n{event.description}")
        if event.attendees:
            lines.append(f"\nAttendees:")
            for a in event.attendees:
                status = a.response.value if a.response else "unknown"
                lines.append(f"  - {a.email} ({status})")
        if event.conference_link:
            lines.append(f"\nConference: {event.conference_link}")
        return "\n".join(lines)

    @mcp.tool()
    async def calendar_create_event(
        ctx: Context,
        title: str,
        start: str,
        end: str,
        account: str = "",
        calendar_id: str = "primary",
        description: str = "",
        location: str = "",
        attendees: str = "",
        all_day: bool = False,
        conference: bool = False
    ) -> str:
        """Create a new event. Dates should be ISO format (YYYY-MM-DDTHH:MM:SS). Uses the active workspace's calendar binding if account is omitted."""
        account, err = await resolve_or_error(ctx, account, "calendar")
        if err:
            return err
        from datetime import datetime
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError as e:
            return f"❌ Invalid date format: {e}"

        attendee_list = [a.strip() for a in attendees.split(",") if a.strip()] if attendees else None

        return await calendar_manager.create_event(
            account, calendar_id, title, start_dt, end_dt,
            description=description or None,
            location=location or None,
            attendees=attendee_list,
            all_day=all_day,
            conference=conference
        )

    @mcp.tool()
    async def calendar_delete_event(ctx: Context, calendar_id: str, event_id: str, account: str = "") -> str:
        """Delete an event. Uses the active workspace's calendar binding if account is omitted."""
        account, err = await resolve_or_error(ctx, account, "calendar")
        if err:
            return err
        return await calendar_manager.delete_event(account, calendar_id, event_id)

    logger.info("✅ Registered 5 calendar tools")
