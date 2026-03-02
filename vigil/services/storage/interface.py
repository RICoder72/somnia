"""
Cloud Storage Interface

Core abstraction for cloud storage providers. Plugins use this interface
to interact with storage without knowing the underlying provider.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FileInfo:
    """Information about a remote file."""
    name: str
    path: str
    size: int
    modified: Optional[datetime] = None
    is_directory: bool = False
    provider: str = ""
    account: str = ""


@dataclass
class StorageAccount:
    """A named storage account configuration."""
    name: str           # User-defined label: "work", "personal", etc.
    provider: str       # Provider type: "gdrive", "onedrive", "dropbox", "supernote"
    credentials_ref: str  # 1Password reference for credentials
    config: Dict[str, Any]  # Provider-specific config (root path, etc.)
    
    def __post_init__(self):
        if not self.config:
            self.config = {}


class StorageProvider(ABC):
    """
    Base class for storage providers.
    
    Each provider (Google Drive, OneDrive, etc.) implements this interface.
    """
    
    provider_type: str = "base"  # Override in subclass: "gdrive", "onedrive", etc.
    
    def __init__(self, account: StorageAccount):
        """
        Initialize provider with account config.
        
        Args:
            account: StorageAccount with credentials and config
        """
        self.account = account
        self._client = None
    
    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the storage service.
        
        Returns:
            True if connected successfully
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the storage service."""
        pass
    
    @abstractmethod
    async def upload(self, local_path: Path, remote_path: str) -> str:
        """
        Upload a file to remote storage.
        
        Args:
            local_path: Local file path
            remote_path: Remote destination path
        
        Returns:
            Success message or error
        """
        pass
    
    @abstractmethod
    async def download(self, remote_path: str, local_path: Path) -> str:
        """
        Download a file from remote storage.
        
        Args:
            remote_path: Remote file path
            local_path: Local destination path
        
        Returns:
            Success message or error
        """
        pass
    
    @abstractmethod
    async def list_files(self, remote_path: str = "/") -> List[FileInfo]:
        """
        List files at a remote path.
        
        Args:
            remote_path: Remote directory path
        
        Returns:
            List of FileInfo objects
        """
        pass
    
    @abstractmethod
    async def exists(self, remote_path: str) -> bool:
        """
        Check if a remote path exists.
        
        Args:
            remote_path: Remote file/directory path
        
        Returns:
            True if exists
        """
        pass
    
    @abstractmethod
    async def delete(self, remote_path: str) -> str:
        """
        Delete a remote file.
        
        Args:
            remote_path: Remote file path
        
        Returns:
            Success message or error
        """
        pass
    
    async def mkdir(self, remote_path: str) -> str:
        """
        Create a remote directory. Override if provider supports it.
        
        Args:
            remote_path: Remote directory path
        
        Returns:
            Success message or error
        """
        return "❌ mkdir not supported by this provider"
    
    async def get_info(self, remote_path: str) -> Optional[FileInfo]:
        """
        Get info about a specific file. Override for efficiency.
        
        Args:
            remote_path: Remote file path
        
        Returns:
            FileInfo or None if not found
        """
        # Default: list parent and find file
        parent = str(Path(remote_path).parent)
        name = Path(remote_path).name
        files = await self.list_files(parent)
        for f in files:
            if f.name == name:
                return f
        return None
