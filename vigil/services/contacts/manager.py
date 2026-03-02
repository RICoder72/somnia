"""
Contacts Account Manager

Manages named contacts accounts and adapter instances.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Type, List
import logging

from .interface import (
    ContactsAdapter, ContactsAccount, Contact, ContactPage, ContactGroup
)

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("/data/config/contacts_accounts.json")


class ContactsManager:
    """Manages contacts accounts and adapter instances."""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.accounts: Dict[str, ContactsAccount] = {}
        self.adapters: Dict[str, ContactsAdapter] = {}
        self.adapter_classes: Dict[str, Type[ContactsAdapter]] = {}
        
        self._load_accounts()
    
    def register_adapter_type(self, adapter_type: str, adapter_class: Type[ContactsAdapter]) -> None:
        """Register an adapter implementation."""
        self.adapter_classes[adapter_type] = adapter_class
        logger.info(f"✅ Registered contacts adapter: {adapter_type}")
    
    def _load_accounts(self) -> None:
        """Load accounts from config file."""
        if not self.config_path.exists():
            logger.info("No contacts accounts config found, starting fresh")
            return
        
        try:
            config = json.loads(self.config_path.read_text())
            for name, data in config.get("accounts", {}).items():
                self.accounts[name] = ContactsAccount(
                    name=name,
                    adapter=data.get("adapter", ""),
                    credentials_ref=data.get("credentials_ref", ""),
                    config=data.get("config", {})
                )
            logger.info(f"✅ Loaded {len(self.accounts)} contacts accounts")
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
        """Add a new contacts account."""
        if name in self.accounts:
            return f"❌ Account '{name}' already exists"
        
        if adapter not in self.adapter_classes:
            available = ", ".join(self.adapter_classes.keys()) or "none"
            return f"❌ Unknown adapter '{adapter}'. Available: {available}"
        
        self.accounts[name] = ContactsAccount(
            name=name,
            adapter=adapter,
            credentials_ref=credentials_ref,
            config=config or {}
        )
        self._save_accounts()
        
        return f"✅ Added contacts account: {name} ({adapter})"
    
    def remove_account(self, name: str) -> str:
        """Remove a contacts account."""
        if name not in self.accounts:
            return f"❌ Account '{name}' not found"
        
        if name in self.adapters:
            del self.adapters[name]
        
        del self.accounts[name]
        self._save_accounts()
        
        return f"✅ Removed contacts account: {name}"
    
    def list_accounts(self) -> str:
        """List all configured accounts."""
        if not self.accounts:
            return "👤 No contacts accounts configured"
        
        lines = ["👤 Contacts Accounts", "─" * 40]
        for name, account in self.accounts.items():
            connected = "🟢" if name in self.adapters else "⚪"
            lines.append(f"{connected} {name} ({account.adapter})")
        
        return "\n".join(lines)
    
    async def get_adapter(self, account_name: str) -> Optional[ContactsAdapter]:
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
    
    async def list_contacts(
        self,
        account_name: str,
        limit: int = 100,
        cursor: Optional[str] = None,
        group_id: Optional[str] = None
    ) -> ContactPage:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return ContactPage(contacts=[])
        return await adapter.list_contacts(limit, cursor, group_id)
    
    async def get_contact(self, account_name: str, contact_id: str) -> Optional[Contact]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return None
        return await adapter.get_contact(contact_id)
    
    async def search_contacts(
        self,
        account_name: str,
        query: str,
        limit: int = 50
    ) -> List[Contact]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return []
        return await adapter.search_contacts(query, limit)
    
    async def create_contact(
        self,
        account_name: str,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        organization: Optional[str] = None,
        title: Optional[str] = None,
        notes: Optional[str] = None
    ) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.create_contact(
            given_name, family_name, email, phone, organization, title, notes
        )
    
    async def update_contact(
        self,
        account_name: str,
        contact_id: str,
        **kwargs
    ) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.update_contact(contact_id, **kwargs)
    
    async def delete_contact(self, account_name: str, contact_id: str) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.delete_contact(contact_id)
    
    async def list_groups(self, account_name: str) -> List[ContactGroup]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return []
        return await adapter.list_groups()
    
    async def add_to_group(self, account_name: str, contact_id: str, group_id: str) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.add_to_group(contact_id, group_id)
    
    async def remove_from_group(self, account_name: str, contact_id: str, group_id: str) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.remove_from_group(contact_id, group_id)
