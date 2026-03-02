"""
Mail Account Manager

Manages named mail accounts and adapter instances.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Type, List
import logging

from .interface import MailAdapter, MailAccount, Message, MessagePage, Folder

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("/data/config/mail_accounts.json")


class MailManager:
    """
    Manages mail accounts and adapter instances.
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.accounts: Dict[str, MailAccount] = {}
        self.adapters: Dict[str, MailAdapter] = {}
        self.adapter_classes: Dict[str, Type[MailAdapter]] = {}
        
        self._load_accounts()
    
    def register_adapter_type(self, adapter_type: str, adapter_class: Type[MailAdapter]) -> None:
        """Register an adapter implementation."""
        self.adapter_classes[adapter_type] = adapter_class
        logger.info(f"✅ Registered mail adapter: {adapter_type}")
    
    def _load_accounts(self) -> None:
        """Load accounts from config file."""
        if not self.config_path.exists():
            logger.info("No mail accounts config found, starting fresh")
            return
        
        try:
            config = json.loads(self.config_path.read_text())
            for name, data in config.get("accounts", {}).items():
                self.accounts[name] = MailAccount(
                    name=name,
                    adapter=data.get("adapter", ""),
                    credentials_ref=data.get("credentials_ref", ""),
                    config=data.get("config", {})
                )
            logger.info(f"✅ Loaded {len(self.accounts)} mail accounts")
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
        """Add a new mail account."""
        if name in self.accounts:
            return f"❌ Account '{name}' already exists"
        
        if adapter not in self.adapter_classes:
            available = ", ".join(self.adapter_classes.keys()) or "none"
            return f"❌ Unknown adapter '{adapter}'. Available: {available}"
        
        self.accounts[name] = MailAccount(
            name=name,
            adapter=adapter,
            credentials_ref=credentials_ref,
            config=config or {}
        )
        self._save_accounts()
        
        return f"✅ Added mail account: {name} ({adapter})"
    
    def remove_account(self, name: str) -> str:
        """Remove a mail account."""
        if name not in self.accounts:
            return f"❌ Account '{name}' not found"
        
        if name in self.adapters:
            del self.adapters[name]
        
        del self.accounts[name]
        self._save_accounts()
        
        return f"✅ Removed mail account: {name}"
    
    def list_accounts(self) -> str:
        """List all configured accounts."""
        if not self.accounts:
            return "📧 No mail accounts configured"
        
        lines = ["📧 Mail Accounts", "─" * 40]
        for name, account in self.accounts.items():
            connected = "🟢" if name in self.adapters else "⚪"
            lines.append(f"{connected} {name} ({account.adapter})")
        
        return "\n".join(lines)
    
    async def get_adapter(self, account_name: str) -> Optional[MailAdapter]:
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
    
    async def list_folders(self, account_name: str) -> List[Folder]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return []
        return await adapter.list_folders()
    
    async def list_messages(
        self,
        account_name: str,
        folder: str = "INBOX",
        limit: int = 50,
        cursor: Optional[str] = None,
        unread_only: bool = False
    ) -> MessagePage:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return MessagePage(messages=[])
        return await adapter.list_messages(folder, limit, cursor, unread_only)
    
    async def get_message(self, account_name: str, message_id: str) -> Optional[Message]:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return None
        return await adapter.get_message(message_id)
    
    async def search(
        self,
        account_name: str,
        query: str,
        folder: Optional[str] = None,
        limit: int = 50
    ) -> MessagePage:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return MessagePage(messages=[])
        return await adapter.search(query, folder, limit)
    
    async def send(
        self,
        account_name: str,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        html: bool = False,
        attachment_ids: Optional[List[str]] = None
    ) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.send(to, subject, body, cc, bcc, html, attachment_ids)
    
    async def delete(self, account_name: str, message_id: str, permanent: bool = False) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.delete(message_id, permanent)
    
    async def mark_read(self, account_name: str, message_id: str, read: bool = True) -> str:
        adapter = await self.get_adapter(account_name)
        if not adapter:
            return f"❌ Could not connect to account: {account_name}"
        return await adapter.mark_read(message_id, read)
