"""
Calendar Service Interface

Core abstraction for calendar providers. Adapters implement this interface
to provide calendar functionality for Google Calendar, Outlook, CalDAV, etc.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class EventStatus(Enum):
    """Event confirmation status."""
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


class ResponseStatus(Enum):
    """Attendee response status."""
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"
    NEEDS_ACTION = "needs_action"


class Visibility(Enum):
    """Event visibility."""
    DEFAULT = "default"
    PUBLIC = "public"
    PRIVATE = "private"
    CONFIDENTIAL = "confidential"


@dataclass
class Attendee:
    """Event attendee."""
    email: str
    name: Optional[str] = None
    response: ResponseStatus = ResponseStatus.NEEDS_ACTION
    optional: bool = False
    organizer: bool = False
    self_: bool = False


@dataclass
class Reminder:
    """Event reminder."""
    minutes_before: int
    method: str = "popup"


@dataclass
class Event:
    """Calendar event."""
    id: str
    calendar_id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    description: Optional[str] = None
    location: Optional[str] = None
    status: EventStatus = EventStatus.CONFIRMED
    visibility: Visibility = Visibility.DEFAULT
    busy: bool = True
    organizer: Optional[str] = None
    attendees: List[Attendee] = field(default_factory=list)
    recurrence: Optional[str] = None
    recurring_event_id: Optional[str] = None
    reminders: List[Reminder] = field(default_factory=list)
    use_default_reminders: bool = True
    conference_link: Optional[str] = None
    html_link: Optional[str] = None
    created: Optional[datetime] = None
    updated: Optional[datetime] = None
    etag: Optional[str] = None


@dataclass
class Calendar:
    """A calendar (container for events)."""
    id: str
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    primary: bool = False
    writable: bool = True
    owner: Optional[str] = None
    timezone: Optional[str] = None


@dataclass
class TimeSlot:
    """A time slot (for free/busy queries)."""
    start: datetime
    end: datetime


@dataclass
class FreeBusyResult:
    """Result of a free/busy query."""
    calendar_id: str
    busy_slots: List[TimeSlot]


@dataclass
class EventPage:
    """Paginated event results."""
    events: List[Event]
    next_cursor: Optional[str] = None


@dataclass
class CalendarAccount:
    """A named calendar account configuration."""
    name: str
    adapter: str
    credentials_ref: str
    config: Dict[str, Any] = field(default_factory=dict)


class CalendarAdapter(ABC):
    """Base class for calendar adapters."""
    
    adapter_type: str = "base"
    
    def __init__(self, account: CalendarAccount):
        self.account = account
        self._client = None
    
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to calendar service."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to calendar service."""
        pass
    
    @abstractmethod
    async def list_calendars(self) -> List[Calendar]:
        """List all accessible calendars."""
        pass
    
    @abstractmethod
    async def get_calendar(self, calendar_id: str) -> Optional[Calendar]:
        """Get calendar details by ID."""
        pass
    
    @abstractmethod
    async def list_events(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> EventPage:
        """List events in date range."""
        pass
    
    @abstractmethod
    async def get_event(self, calendar_id: str, event_id: str) -> Optional[Event]:
        """Get full event details."""
        pass
    
    @abstractmethod
    async def search_events(
        self,
        query: str,
        calendar_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 50,
        cursor: Optional[str] = None
    ) -> EventPage:
        """Search events by text."""
        pass
    
    @abstractmethod
    async def list_event_instances(
        self,
        calendar_id: str,
        event_id: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> EventPage:
        """Get instances of a recurring event."""
        pass
    
    @abstractmethod
    async def create_event(
        self,
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        reminders: Optional[List[Reminder]] = None,
        all_day: bool = False,
        recurrence: Optional[str] = None,
        visibility: Visibility = Visibility.DEFAULT,
        conference: bool = False
    ) -> str:
        """Create a new event."""
        pass
    
    @abstractmethod
    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        title: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        reminders: Optional[List[Reminder]] = None,
        visibility: Optional[Visibility] = None
    ) -> str:
        """Update an existing event."""
        pass
    
    @abstractmethod
    async def update_event_instance(
        self,
        calendar_id: str,
        event_id: str,
        instance_id: str,
        title: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> str:
        """Update a single instance of a recurring event."""
        pass
    
    @abstractmethod
    async def delete_event(self, calendar_id: str, event_id: str) -> str:
        """Delete an event."""
        pass
    
    @abstractmethod
    async def delete_event_instance(
        self,
        calendar_id: str,
        event_id: str,
        instance_id: str
    ) -> str:
        """Delete a single instance of a recurring event."""
        pass
    
    @abstractmethod
    async def get_free_busy(
        self,
        calendar_ids: List[str],
        start: datetime,
        end: datetime
    ) -> List[FreeBusyResult]:
        """Get free/busy information for calendars."""
        pass
    
    async def find_free_slots(
        self,
        calendar_ids: List[str],
        start: datetime,
        end: datetime,
        duration: timedelta,
        working_hours: Optional[tuple] = None
    ) -> List[TimeSlot]:
        """Find available time slots across calendars."""
        free_busy = await self.get_free_busy(calendar_ids, start, end)
        
        all_busy: List[TimeSlot] = []
        for fb in free_busy:
            all_busy.extend(fb.busy_slots)
        
        all_busy.sort(key=lambda s: s.start)
        
        free_slots: List[TimeSlot] = []
        current = start
        
        for busy in all_busy:
            if busy.start > current:
                gap = busy.start - current
                if gap >= duration:
                    free_slots.append(TimeSlot(start=current, end=busy.start))
            current = max(current, busy.end)
        
        if end > current and (end - current) >= duration:
            free_slots.append(TimeSlot(start=current, end=end))
        
        return free_slots
    
    async def respond_to_event(
        self,
        calendar_id: str,
        event_id: str,
        response: ResponseStatus,
        comment: Optional[str] = None
    ) -> str:
        """Respond to an event invitation."""
        return "❌ Event responses not supported by this adapter"
