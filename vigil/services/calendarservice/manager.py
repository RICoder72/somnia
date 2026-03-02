"""
Calendar Account Manager

Manages named calendar accounts and adapter instances.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Type, List
from datetime import datetime, timedelta
import logging

from .interface import (
    CalendarAdapter, CalendarAccount, Event, EventPage,
    Calendar, FreeBusyResult, TimeSlot, Reminder, Visibility
)

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("/data/config/calendar_accounts.json")


class CalendarManager:
    """Manages calendar accounts and adapter instances."""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.accounts: Dict[str, CalendarAccount] = {}
        self.adapters: Dict[str, CalendarAdapter] = {}
        self.adapter_classes: Dict[str, Type[CalendarAdapter]] = {}
        
        self._load_accounts()
    
    def register_adapter_type(self, adapter_type: str, adapter_class: Type[CalendarAdapter]) -> None:
        """Register an adapter implementation."""
        self.adapter_classes[adapter_type] = adapter_class
        logger.info(f"✅ Registered calendar adapter: {adapter_type}")
    
    def _load_accounts(self) -> None:
        """Load accounts from config file."""
        if not self.config_path.exists():
            logger.info("No calendar accounts config found, starting fresh")
            return
        
        try:
            config = json.loads(self.config_path.read_text())
            for name, data in config.get("accounts", {}).items():
                self.accounts[name] = CalendarAccount(
                    name=name,
                    adapter=data.get("adapter", ""),
                    credentials_ref=data.get("credentials_ref", ""),
                    config=data.get("config", {})
                )
            logger.info(f"✅ Loaded {len(self.accounts)} calendar accounts")
        except Exception as e:
            logger.error(f"❌ Failed to load accounts: {e}")
    
    def _save_accounts(self) -> None:
        """Save accounts to config file."""
        config = {"accounts": {}}
        for name, account in self.accounts.items():
            config["accounts"][name] = {
                "adapter": account.adapter,
                "credentials_ref": account.credentials_ref,
                "config": account.config
            }
        
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, indent=2))
    
    def add_account(
        self,
        name: str,
        adapter: str,
        credentials_ref: str = "",
        config: Optional[Dict] = None
    ) -> str:
        """Add a new calendar account."""
        if name in self.accounts:
            return f"❌ Account '{name}' already exists"
        
        if adapter not in self.adapter_classes:
            available = ", ".join(self.adapter_classes.keys()) or "none"
            return f"❌ Unknown adapter '{adapter}'. Available: {available}"
        
        self.accounts[name] = CalendarAccount(
            name=name,
            adapter=adapter,
            credentials_ref=credentials_ref,
            config=config or {}
        )
        self._save_accounts()
        
        return f"✅ Added calendar account: {name} ({adapter})"
    
    def remove_account(self, name: str) -> str:
        """Remove a calendar account."""
        if name not in self.accounts:
            return f"❌ Account '{name}' not found"
        
        if name in self.adapters:
            del self.adapters[name]
        
        del self.accounts[name]
        self._save_accounts()
        
        return f"✅ Removed calendar account: {name}"
    
    def list_accounts(self) -> str:
        """List all configured accounts."""
        if not self.accounts:
            return "📅 No calendar accounts configured"
        
        lines = ["📅 Calendar Accounts", "─" * 40]
        for name, account in self.accounts.items():
            connected = "🟢" if name in self.adapters else "⚪"
            lines.append(f"{connected} {name} ({account.adapter})")
        
        return "\n".join(lines)
    
    async def get_adapter(self, account_name: str) -> Optional[CalendarAdapter]:
        """Get or create an adapter instance for an account."""
        if account_name not in self.accounts:
            logger.error(f"Account not found: {account_name}")
            return None
        
        if account_name in self.adapters:
            return self.adapters[account_name]
        
        account = self.accounts[account_name]
        
        if account.adapter not in self.adapter_classes:
            logger.error(f"Adapter not registered: {account.adapter}")
            return None
        
        adapter_class = self.adapter_classes[account.adapter]
        adapter = adapter_class(account)
        
        if await adapter.connect():
            self.adapters[account_name] = adapter
            return adapter
        
        return None
    
    # Convenience methods
    
    async def list_calendars(self, account_name: str) -> List[Calendar]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return []
        return await adapter.list_calendars()
    
    async def list_events(
        self,
        account_name: str,
        calendar_id: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> EventPage:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return EventPage(events=[])
        return await adapter.list_events(calendar_id, start, end, limit, cursor)
    
    async def get_event(self, account_name: str, calendar_id: str, event_id: str) -> Optional[Event]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return None
        return await adapter.get_event(calendar_id, event_id)
    
    async def create_event(
        self,
        account_name: str,
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
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.create_event(
            calendar_id, title, start, end, description, location,
            attendees, reminders, all_day, recurrence, visibility, conference
        )
    
    async def update_event(
        self,
        account_name: str,
        calendar_id: str,
        event_id: str,
        **kwargs
    ) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.update_event(calendar_id, event_id, **kwargs)
    
    async def delete_event(self, account_name: str, calendar_id: str, event_id: str) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.delete_event(calendar_id, event_id)
    
    async def get_free_busy(
        self,
        account_name: str,
        calendar_ids: List[str],
        start: datetime,
        end: datetime
    ) -> List[FreeBusyResult]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return []
        return await adapter.get_free_busy(calendar_ids, start, end)
    
    async def find_free_slots(
        self,
        account_name: str,
        calendar_ids: List[str],
        start: datetime,
        end: datetime,
        duration: timedelta
    ) -> List[TimeSlot]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return []
        return await adapter.find_free_slots(calendar_ids, start, end, duration)
