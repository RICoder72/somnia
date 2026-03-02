"""
Mail Service Interface

Core abstraction for email providers. Adapters implement this interface
to provide email functionality for Gmail, Outlook, IMAP, etc.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MessageFlag(Enum):
    """Standard message flags."""
    READ = "read"
    UNREAD = "unread"
    STARRED = "starred"
    IMPORTANT = "important"
    DRAFT = "draft"
    SENT = "sent"
    TRASH = "trash"
    SPAM = "spam"


@dataclass
class Address:
    """Email address with optional display name."""
    email: str
    name: Optional[str] = None
    
    def __str__(self):
        if self.name:
            return f"{self.name} <{self.email}>"
        return self.email


@dataclass
class Attachment:
    """Email attachment metadata (from received message)."""
    id: str
    filename: str
    mime_type: str
    size: int


@dataclass
class UploadedAttachment:
    """Result of uploading an attachment for sending."""
    id: str
    filename: str
    mime_type: str
    size: int


@dataclass
class Message:
    """Email message."""
    id: str
    thread_id: Optional[str]
    subject: str
    sender: Address
    recipients: List[Address]
    cc: List[Address] = field(default_factory=list)
    bcc: List[Address] = field(default_factory=list)
    date: Optional[datetime] = None
    snippet: str = ""
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    flags: List[MessageFlag] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass 
class Folder:
    """Mailbox folder."""
    id: str
    name: str
    path: str
    message_count: int = 0
    unread_count: int = 0
    folder_type: Optional[str] = None


@dataclass
class MessagePage:
    """Paginated message results."""
    messages: List[Message]
    next_cursor: Optional[str] = None
    total_estimate: Optional[int] = None


@dataclass
class MailAccount:
    """A named mail account configuration."""
    name: str
    adapter: str
    credentials_ref: str
    config: Dict[str, Any] = field(default_factory=dict)


class MailAdapter(ABC):
    """Base class for mail adapters."""
    
    adapter_type: str = "base"
    
    def __init__(self, account: MailAccount):
        self.account = account
        self._client = None
    
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to mail service."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to mail service."""
        pass
    
    @abstractmethod
    async def list_folders(self) -> List[Folder]:
        """List all mailbox folders."""
        pass
    
    @abstractmethod
    async def list_messages(
        self,
        folder: str = "INBOX",
        limit: int = 50,
        cursor: Optional[str] = None,
        unread_only: bool = False
    ) -> MessagePage:
        """List messages in folder with cursor-based pagination."""
        pass
    
    @abstractmethod
    async def get_message(self, message_id: str) -> Optional[Message]:
        """Get full message including body content."""
        pass
    
    @abstractmethod
    async def list_thread(self, thread_id: str) -> List[Message]:
        """Get all messages in a thread, ordered chronologically."""
        pass
    
    @abstractmethod
    async def search(
        self,
        query: str,
        folder: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None
    ) -> MessagePage:
        """Search messages."""
        pass
    
    @abstractmethod
    async def upload_attachment(
        self,
        local_path: str,
        filename: Optional[str] = None
    ) -> UploadedAttachment:
        """Upload attachment for use in send/reply/forward."""
        pass
    
    @abstractmethod
    async def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        local_path: str
    ) -> str:
        """Download attachment from a received message."""
        pass
    
    @abstractmethod
    async def send(
        self,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        html: bool = False,
        attachment_ids: Optional[List[str]] = None
    ) -> str:
        """Send new message."""
        pass
    
    @abstractmethod
    async def reply(
        self,
        message_id: str,
        body: str,
        reply_all: bool = False,
        html: bool = False,
        attachment_ids: Optional[List[str]] = None
    ) -> str:
        """Reply to a message."""
        pass
    
    @abstractmethod
    async def forward(
        self,
        message_id: str,
        to: List[str],
        body: Optional[str] = None,
        attachment_ids: Optional[List[str]] = None
    ) -> str:
        """Forward a message."""
        pass
    
    @abstractmethod
    async def move(self, message_id: str, folder: str) -> str:
        """Move message to a folder."""
        pass
    
    @abstractmethod
    async def delete(self, message_id: str, permanent: bool = False) -> str:
        """Delete a message."""
        pass
    
    @abstractmethod
    async def mark_read(self, message_id: str, read: bool = True) -> str:
        """Mark message as read or unread."""
        pass
    
    @abstractmethod
    async def mark_flagged(self, message_id: str, flagged: bool = True) -> str:
        """Mark message as starred/flagged."""
        pass
