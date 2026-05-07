"""
Notification Service Interface

Core abstraction for outbound notification channels.
Unlike mail (bidirectional), notifications are fire-and-forget:
send a message, optionally with attachments, to a recipient.

Adapters implement this interface for Signal, email relay,
Slack, SMS, Pushover, etc.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum


class Priority(Enum):
    """Notification priority — drives channel routing."""
    LOW = "low"          # digest / batch-friendly
    NORMAL = "normal"    # default
    HIGH = "high"        # immediate delivery
    URGENT = "urgent"    # immediate + ensure delivery


class ChannelStatus(Enum):
    """Adapter health status."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class Recipient:
    """Notification recipient.
    
    The `address` field is channel-specific:
      - Signal: phone number in international format (+1...)
      - Email: email address
      - Slack: channel name or user ID
    """
    address: str
    name: Optional[str] = None

    def __str__(self):
        if self.name:
            return f"{self.name} ({self.address})"
        return self.address


@dataclass
class Notification:
    """A notification to send."""
    recipient: Recipient
    message: str
    subject: Optional[str] = None       # used by email, ignored by Signal
    priority: Priority = Priority.NORMAL
    attachments: List[str] = field(default_factory=list)  # file paths
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    """Result of sending a notification."""
    success: bool
    channel: str              # adapter_type that handled it
    recipient: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass
class NotificationChannel:
    """A named notification channel configuration."""
    name: str
    adapter: str              # "signal", "email", "slack", etc.
    credentials_ref: str      # 1Password ref or env var name
    config: Dict[str, Any] = field(default_factory=dict)
    # config examples:
    #   signal: {"api_url": "http://signal-api:8080", "sender": "+1..."}
    #   email:  {"mail_account": "matt-personal", "from_name": "Somnia"}


class NotificationAdapter(ABC):
    """Base class for notification channel adapters.
    
    Much simpler than MailAdapter — notifications are unidirectional.
    Adapters only need to: connect, send, report status, disconnect.
    """

    adapter_type: str = "base"

    def __init__(self, channel: NotificationChannel):
        self.channel = channel

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the notification service."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        pass

    @abstractmethod
    async def send(self, notification: Notification) -> DeliveryResult:
        """Send a notification. Returns delivery result."""
        pass

    @abstractmethod
    async def status(self) -> ChannelStatus:
        """Check adapter health / connectivity."""
        pass
