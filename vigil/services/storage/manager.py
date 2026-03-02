"""
Storage Account Manager

Manages named storage accounts and provider instances.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Type, List
import logging

from .interface import StorageProvider, StorageAccount, FileInfo

logger = logging.getLogger(__name__)


class StorageManager:
    """
    Manages storage accounts and provider instances.
    
    Provides a unified interface for plugins to access any configured
    storage account without knowing the underlying provider.
    """
    
    def __init__(self, config_path: Path):
        """
        Initialize storage manager.
        
        Args:
            config_path: Path to accounts config file
        """
        self.config_path = config_path
        self.accounts: Dict[str, StorageAccount] = {}
        self.providers: Dict[str, StorageProvider] = {}  # Active provider instances
        self.provider_classes: Dict[str, Type[StorageProvider]] = {}  # Registered provider types
        
        self._load_accounts()
    
    def register_provider_type(self, provider_type: str, provider_class: Type[StorageProvider]) -> None:
        """
        Register a provider implementation.
        
        Args:
            provider_type: Provider identifier ("gdrive", "onedrive", etc.)
            provider_class: Class implementing StorageProvider
        """
        self.provider_classes[provider_type] = provider_class
        logger.info(f"✅ Registered storage provider: {provider_type}")
    
    def _load_accounts(self) -> None:
        """Load accounts from config file."""
        if not self.config_path.exists():
            logger.info("No storage accounts config found, starting fresh")
            return
        
        try:
            config = json.loads(self.config_path.read_text())
            for name, data in config.get("accounts", {}).items():
                self.accounts[name] = StorageAccount(
                    name=name,
                    provider=data["provider"],
                    credentials_ref=data.get("credentials_ref", ""),
                    config=data.get("config", {})
                )
            logger.info(f"✅ Loaded {len(self.accounts)} storage accounts")
        except Exception as e:
            logger.error(f"❌ Failed to load accounts: {e}")
    
    def _save_accounts(self) -> None:
        """Save accounts to config file."""
        config = {"accounts": {}}
        for name, account in self.accounts.items():
            config["accounts"][name] = {
                "provider": account.provider,
                "credentials_ref": account.credentials_ref,
                "config": account.config
            }
        
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, indent=2))
    
    def add_account(
        self,
        name: str,
        provider: str,
        credentials_ref: str,
        config: Optional[Dict] = None
    ) -> str:
        """
        Add a new storage account.
        
        Args:
            name: User-defined label ("work", "personal", etc.)
            provider: Provider type ("gdrive", "onedrive", etc.)
            credentials_ref: 1Password reference for credentials
            config: Optional provider-specific config
        
        Returns:
            Success or error message
        """
        if name in self.accounts:
            return f"❌ Account '{name}' already exists"
        
        if provider not in self.provider_classes:
            available = ", ".join(self.provider_classes.keys()) or "none"
            return f"❌ Unknown provider '{provider}'. Available: {available}"
        
        self.accounts[name] = StorageAccount(
            name=name,
            provider=provider,
            credentials_ref=credentials_ref,
            config=config or {}
        )
        self._save_accounts()
        
        return f"✅ Added storage account: {name} ({provider})"
    
    def remove_account(self, name: str) -> str:
        """
        Remove a storage account.
        
        Args:
            name: Account name to remove
        
        Returns:
            Success or error message
        """
        if name not in self.accounts:
            return f"❌ Account '{name}' not found"
        
        # Disconnect if active
        if name in self.providers:
            del self.providers[name]
        
        del self.accounts[name]
        self._save_accounts()
        
        return f"✅ Removed storage account: {name}"
    
    def list_accounts(self) -> str:
        """
        List all configured accounts.
        
        Returns:
            Formatted account list
        """
        if not self.accounts:
            return "📂 No storage accounts configured"
        
        lines = ["📂 Storage Accounts", "─" * 40]
        for name, account in self.accounts.items():
            connected = "🟢" if name in self.providers else "⚪"
            lines.append(f"{connected} {name} ({account.provider})")
            if account.config.get("root_path"):
                lines.append(f"   Root: {account.config['root_path']}")
        
        return "\n".join(lines)
    
    async def get_provider(self, account_name: str) -> Optional[StorageProvider]:
        """
        Get or create a provider instance for an account.
        
        Args:
            account_name: Name of the account
        
        Returns:
            StorageProvider instance or None if failed
        """
        if account_name not in self.accounts:
            logger.error(f"Account not found: {account_name}")
            return None
        
        # Return existing if connected
        if account_name in self.providers:
            return self.providers[account_name]
        
        account = self.accounts[account_name]
        
        if account.provider not in self.provider_classes:
            logger.error(f"Provider not registered: {account.provider}")
            return None
        
        # Create and connect
        provider_class = self.provider_classes[account.provider]
        provider = provider_class(account)
        
        if await provider.connect():
            self.providers[account_name] = provider
            return provider
        
        return None
    
    # =========================================================================
    # Convenience methods that route to the appropriate provider
    # =========================================================================
    
    async def upload(self, account_name: str, local_path: Path, remote_path: str) -> str:
        """Upload file to specified account."""
        provider = await self.get_provider(account_name)
        if not provider:
            return f"❌ Could not connect to account: {account_name}"
        return await provider.upload(local_path, remote_path)
    
    async def download(self, account_name: str, remote_path: str, local_path: Path) -> str:
        """Download file from specified account."""
        provider = await self.get_provider(account_name)
        if not provider:
            return f"❌ Could not connect to account: {account_name}"
        return await provider.download(remote_path, local_path)
    
    async def list_files(self, account_name: str, remote_path: str = "/") -> List[FileInfo]:
        """List files at specified account and path."""
        provider = await self.get_provider(account_name)
        if not provider:
            return []
        return await provider.list_files(remote_path)
    
    async def exists(self, account_name: str, remote_path: str) -> bool:
        """Check if path exists on specified account."""
        provider = await self.get_provider(account_name)
        if not provider:
            return False
        return await provider.exists(remote_path)
    
    async def delete(self, account_name: str, remote_path: str) -> str:
        """Delete file from specified account."""
        provider = await self.get_provider(account_name)
        if not provider:
            return f"❌ Could not connect to account: {account_name}"
        return await provider.delete(remote_path)
