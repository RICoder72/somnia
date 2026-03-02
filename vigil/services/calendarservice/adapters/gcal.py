"""
Google Calendar Adapter

Implements CalendarAdapter interface for Google Calendar API.
"""

from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import logging

from ..interface import (
    CalendarAdapter, CalendarAccount, Event, EventPage,
    Calendar, Attendee, TimeSlot, FreeBusyResult, Reminder,
    EventStatus, ResponseStatus, Visibility
)

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = Path("/data/config/gcal_token.json")


class GCalAdapter(CalendarAdapter):
    """Google Calendar adapter."""
    
    adapter_type = "gcal"
    
    def __init__(self, account: CalendarAccount):
        super().__init__(account)
        self._service = None
        
        # Get token path from account config, with fallback to default
        self._token_path = Path(account.config.get("token_path", str(DEFAULT_TOKEN_PATH)))
    
    async def connect(self) -> bool:
        """Connect to Google Calendar API."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            
            creds = None
            
            if self._token_path.exists():
                try:
                    creds = Credentials.from_authorized_user_file(str(self._token_path))
                except Exception as e:
                    logger.warning(f"Failed to load token file: {e}")
            
            if not creds:
                logger.error("No credentials available. Complete OAuth flow first.")
                return False
            
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(self._token_path, 'w') as f:
                    f.write(creds.to_json())
            
            self._service = build('calendar', 'v3', credentials=creds)
            
            # Test connection
            self._service.calendarList().list(maxResults=1).execute()
            
            logger.info(f"✅ Connected to Google Calendar: {self.account.name}")
            return True
            
        except ImportError:
            logger.error("❌ Google API libraries not installed")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to connect to Google Calendar: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from Google Calendar."""
        self._service = None
    
    def _parse_datetime(self, dt_dict: dict) -> tuple:
        """Parse datetime from API response. Returns (datetime, all_day)."""
        if 'date' in dt_dict:
            # All-day event
            date_str = dt_dict['date']
            dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            return dt, True
        elif 'dateTime' in dt_dict:
            dt_str = dt_dict['dateTime']
            # Handle timezone offset
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            return dt, False
        return None, False
    
    def _format_datetime(self, dt: datetime, all_day: bool = False) -> dict:
        """Format datetime for API request."""
        if all_day:
            return {'date': dt.strftime('%Y-%m-%d')}
        else:
            return {'dateTime': dt.isoformat(), 'timeZone': 'UTC'}
    
    def _parse_event(self, event_data: dict, calendar_id: str) -> Event:
        """Parse API event into Event object."""
        start_dt, all_day = self._parse_datetime(event_data.get('start', {}))
        end_dt, _ = self._parse_datetime(event_data.get('end', {}))
        
        # Parse attendees
        attendees = []
        for att in event_data.get('attendees', []):
            attendees.append(Attendee(
                email=att.get('email', ''),
                name=att.get('displayName'),
                response=self._parse_response_status(att.get('responseStatus', 'needsAction')),
                optional=att.get('optional', False),
                organizer=att.get('organizer', False),
                self_=att.get('self', False)
            ))
        
        # Parse reminders
        reminders = []
        reminder_data = event_data.get('reminders', {})
        if not reminder_data.get('useDefault', True):
            for r in reminder_data.get('overrides', []):
                reminders.append(Reminder(
                    minutes_before=r.get('minutes', 10),
                    method=r.get('method', 'popup')
                ))
        
        # Parse status
        status_str = event_data.get('status', 'confirmed')
        status = EventStatus.CONFIRMED
        if status_str == 'tentative':
            status = EventStatus.TENTATIVE
        elif status_str == 'cancelled':
            status = EventStatus.CANCELLED
        
        # Parse visibility
        vis_str = event_data.get('visibility', 'default')
        visibility = Visibility.DEFAULT
        if vis_str == 'public':
            visibility = Visibility.PUBLIC
        elif vis_str == 'private':
            visibility = Visibility.PRIVATE
        elif vis_str == 'confidential':
            visibility = Visibility.CONFIDENTIAL
        
        # Parse created/updated
        created = None
        if 'created' in event_data:
            created = datetime.fromisoformat(event_data['created'].replace('Z', '+00:00'))
        updated = None
        if 'updated' in event_data:
            updated = datetime.fromisoformat(event_data['updated'].replace('Z', '+00:00'))
        
        # Get conference link
        conference_link = None
        if 'conferenceData' in event_data:
            for ep in event_data['conferenceData'].get('entryPoints', []):
                if ep.get('entryPointType') == 'video':
                    conference_link = ep.get('uri')
                    break
        
        return Event(
            id=event_data['id'],
            calendar_id=calendar_id,
            title=event_data.get('summary', '(no title)'),
            start=start_dt,
            end=end_dt,
            all_day=all_day,
            description=event_data.get('description'),
            location=event_data.get('location'),
            status=status,
            visibility=visibility,
            busy=event_data.get('transparency', 'opaque') == 'opaque',
            organizer=event_data.get('organizer', {}).get('email'),
            attendees=attendees,
            recurrence=event_data.get('recurrence', [None])[0] if event_data.get('recurrence') else None,
            recurring_event_id=event_data.get('recurringEventId'),
            reminders=reminders,
            use_default_reminders=reminder_data.get('useDefault', True),
            conference_link=conference_link,
            html_link=event_data.get('htmlLink'),
            created=created,
            updated=updated,
            etag=event_data.get('etag')
        )
    
    def _parse_response_status(self, status: str) -> ResponseStatus:
        """Parse response status string."""
        mapping = {
            'accepted': ResponseStatus.ACCEPTED,
            'declined': ResponseStatus.DECLINED,
            'tentative': ResponseStatus.TENTATIVE,
            'needsAction': ResponseStatus.NEEDS_ACTION
        }
        return mapping.get(status, ResponseStatus.NEEDS_ACTION)
    
    async def list_calendars(self) -> List[Calendar]:
        """List all accessible calendars."""
        if not self._service:
            return []
        
        try:
            results = self._service.calendarList().list().execute()
            
            calendars = []
            for cal in results.get('items', []):
                calendars.append(Calendar(
                    id=cal['id'],
                    name=cal.get('summary', cal['id']),
                    description=cal.get('description'),
                    color=cal.get('backgroundColor'),
                    primary=cal.get('primary', False),
                    writable=cal.get('accessRole') in ['owner', 'writer'],
                    owner=cal.get('id') if cal.get('accessRole') == 'owner' else None,
                    timezone=cal.get('timeZone')
                ))
            
            return calendars
            
        except Exception as e:
            logger.error(f"Failed to list calendars: {e}")
            return []
    
    async def get_calendar(self, calendar_id: str) -> Optional[Calendar]:
        """Get calendar by ID."""
        if not self._service:
            return None
        
        try:
            cal = self._service.calendarList().get(calendarId=calendar_id).execute()
            return Calendar(
                id=cal['id'],
                name=cal.get('summary', cal['id']),
                description=cal.get('description'),
                color=cal.get('backgroundColor'),
                primary=cal.get('primary', False),
                writable=cal.get('accessRole') in ['owner', 'writer'],
                owner=cal.get('id') if cal.get('accessRole') == 'owner' else None,
                timezone=cal.get('timeZone')
            )
        except Exception as e:
            logger.error(f"Failed to get calendar: {e}")
            return None
    
    async def list_events(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> EventPage:
        """List events in date range."""
        if not self._service:
            return EventPage(events=[])
        
        try:
            params = {
                'calendarId': calendar_id,
                'timeMin': start.isoformat(),
                'timeMax': end.isoformat(),
                'maxResults': min(limit, 250),
                'singleEvents': True,
                'orderBy': 'startTime'
            }
            if cursor:
                params['pageToken'] = cursor
            
            results = self._service.events().list(**params).execute()
            
            events = [self._parse_event(e, calendar_id) for e in results.get('items', [])]
            
            return EventPage(
                events=events,
                next_cursor=results.get('nextPageToken')
            )
            
        except Exception as e:
            logger.error(f"Failed to list events: {e}")
            return EventPage(events=[])
    
    async def get_event(self, calendar_id: str, event_id: str) -> Optional[Event]:
        """Get event by ID."""
        if not self._service:
            return None
        
        try:
            event_data = self._service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            return self._parse_event(event_data, calendar_id)
        except Exception as e:
            logger.error(f"Failed to get event: {e}")
            return None
    
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
        if not self._service:
            return EventPage(events=[])
        
        try:
            cal_id = calendar_id or 'primary'
            
            params = {
                'calendarId': cal_id,
                'q': query,
                'maxResults': min(limit, 250),
                'singleEvents': True,
                'orderBy': 'startTime'
            }
            if start:
                params['timeMin'] = start.isoformat()
            if end:
                params['timeMax'] = end.isoformat()
            if cursor:
                params['pageToken'] = cursor
            
            results = self._service.events().list(**params).execute()
            events = [self._parse_event(e, cal_id) for e in results.get('items', [])]
            
            return EventPage(
                events=events,
                next_cursor=results.get('nextPageToken')
            )
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return EventPage(events=[])
    
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
        if not self._service:
            return EventPage(events=[])
        
        try:
            params = {
                'calendarId': calendar_id,
                'eventId': event_id,
                'timeMin': start.isoformat(),
                'timeMax': end.isoformat(),
                'maxResults': min(limit, 250)
            }
            if cursor:
                params['pageToken'] = cursor
            
            results = self._service.events().instances(**params).execute()
            events = [self._parse_event(e, calendar_id) for e in results.get('items', [])]
            
            return EventPage(
                events=events,
                next_cursor=results.get('nextPageToken')
            )
            
        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            return EventPage(events=[])
    
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
        if not self._service:
            return "❌ Not connected to Google Calendar"
        
        try:
            event_body: Dict[str, Any] = {
                'summary': title,
                'start': self._format_datetime(start, all_day),
                'end': self._format_datetime(end, all_day)
            }
            
            if description:
                event_body['description'] = description
            if location:
                event_body['location'] = location
            
            if attendees:
                event_body['attendees'] = [{'email': e} for e in attendees]
            
            if reminders:
                event_body['reminders'] = {
                    'useDefault': False,
                    'overrides': [
                        {'method': r.method, 'minutes': r.minutes_before}
                        for r in reminders
                    ]
                }
            
            if recurrence:
                event_body['recurrence'] = [recurrence]
            
            if visibility != Visibility.DEFAULT:
                event_body['visibility'] = visibility.value
            
            params = {'calendarId': calendar_id, 'body': event_body}
            
            if conference:
                params['conferenceDataVersion'] = 1
                event_body['conferenceData'] = {
                    'createRequest': {
                        'requestId': f"super-claude-{datetime.now().timestamp()}",
                        'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                    }
                }
            
            result = self._service.events().insert(**params).execute()
            return f"✅ Created event: {result['id']}"
            
        except Exception as e:
            return f"❌ Create failed: {e}"
    
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
        if not self._service:
            return "❌ Not connected to Google Calendar"
        
        try:
            # Get existing event
            event = self._service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            
            # Update fields
            if title is not None:
                event['summary'] = title
            if description is not None:
                event['description'] = description
            if location is not None:
                event['location'] = location
            if start is not None:
                all_day = 'date' in event.get('start', {})
                event['start'] = self._format_datetime(start, all_day)
            if end is not None:
                all_day = 'date' in event.get('end', {})
                event['end'] = self._format_datetime(end, all_day)
            if attendees is not None:
                event['attendees'] = [{'email': e} for e in attendees]
            if reminders is not None:
                event['reminders'] = {
                    'useDefault': False,
                    'overrides': [
                        {'method': r.method, 'minutes': r.minutes_before}
                        for r in reminders
                    ]
                }
            if visibility is not None:
                event['visibility'] = visibility.value
            
            self._service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event
            ).execute()
            
            return f"✅ Updated event: {event_id}"
            
        except Exception as e:
            return f"❌ Update failed: {e}"
    
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
        # For Google Calendar, instance_id is the actual event ID of the instance
        return await self.update_event(
            calendar_id, instance_id,
            title=title, start=start, end=end,
            description=description, location=location
        )
    
    async def delete_event(self, calendar_id: str, event_id: str) -> str:
        """Delete an event."""
        if not self._service:
            return "❌ Not connected to Google Calendar"
        
        try:
            self._service.events().delete(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            return f"✅ Deleted event"
            
        except Exception as e:
            return f"❌ Delete failed: {e}"
    
    async def delete_event_instance(
        self,
        calendar_id: str,
        event_id: str,
        instance_id: str
    ) -> str:
        """Delete a single instance of a recurring event."""
        return await self.delete_event(calendar_id, instance_id)
    
    async def get_free_busy(
        self,
        calendar_ids: List[str],
        start: datetime,
        end: datetime
    ) -> List[FreeBusyResult]:
        """Get free/busy information."""
        if not self._service:
            return []
        
        try:
            body = {
                'timeMin': start.isoformat(),
                'timeMax': end.isoformat(),
                'items': [{'id': cal_id} for cal_id in calendar_ids]
            }
            
            result = self._service.freebusy().query(body=body).execute()
            
            results = []
            for cal_id, data in result.get('calendars', {}).items():
                busy_slots = []
                for busy in data.get('busy', []):
                    slot_start = datetime.fromisoformat(busy['start'].replace('Z', '+00:00'))
                    slot_end = datetime.fromisoformat(busy['end'].replace('Z', '+00:00'))
                    busy_slots.append(TimeSlot(start=slot_start, end=slot_end))
                
                results.append(FreeBusyResult(
                    calendar_id=cal_id,
                    busy_slots=busy_slots
                ))
            
            return results
            
        except Exception as e:
            logger.error(f"Free/busy query failed: {e}")
            return []
    
    async def respond_to_event(
        self,
        calendar_id: str,
        event_id: str,
        response: ResponseStatus,
        comment: Optional[str] = None
    ) -> str:
        """Respond to an event invitation."""
        if not self._service:
            return "❌ Not connected to Google Calendar"
        
        try:
            # Get current event
            event = self._service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            
            # Map response status
            response_map = {
                ResponseStatus.ACCEPTED: 'accepted',
                ResponseStatus.DECLINED: 'declined',
                ResponseStatus.TENTATIVE: 'tentative'
            }
            response_str = response_map.get(response, 'needsAction')
            
            # Update self attendee
            for att in event.get('attendees', []):
                if att.get('self'):
                    att['responseStatus'] = response_str
                    if comment:
                        att['comment'] = comment
                    break
            
            self._service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event
            ).execute()
            
            return f"✅ Responded to event: {response_str}"
            
        except Exception as e:
            return f"❌ Response failed: {e}"
