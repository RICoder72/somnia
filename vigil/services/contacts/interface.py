"""
Contacts Service Interface

Core abstraction for contacts providers. Adapters implement this interface
to provide contacts functionality for Google Contacts, Outlook, CardDAV, etc.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class PhoneType(Enum):
    """Phone number types."""
    MOBILE = "mobile"
    HOME = "home"
    WORK = "work"
    MAIN = "main"
    FAX_HOME = "fax_home"
    FAX_WORK = "fax_work"
    PAGER = "pager"
    OTHER = "other"


class EmailType(Enum):
    """Email address types."""
    HOME = "home"
    WORK = "work"
    OTHER = "other"


class AddressType(Enum):
    """Address types."""
    HOME = "home"
    WORK = "work"
    OTHER = "other"


@dataclass
class Name:
    """Structured name."""
    given: Optional[str] = None
    family: Optional[str] = None
    middle: Optional[str] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    display: Optional[str] = None
    
    def __str__(self):
        if self.display:
            return self.display
        parts = [self.prefix, self.given, self.middle, self.family, self.suffix]
        return " ".join(p for p in parts if p)


@dataclass
class EmailAddress:
    """Email address."""
    address: str
    type: EmailType = EmailType.OTHER
    primary: bool = False
    label: Optional[str] = None


@dataclass
class PhoneNumber:
    """Phone number."""
    number: str
    type: PhoneType = PhoneType.OTHER
    primary: bool = False
    label: Optional[str] = None


@dataclass
class Address:
    """Physical address."""
    formatted: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    type: AddressType = AddressType.OTHER
    primary: bool = False


@dataclass
class Organization:
    """Organization/company info."""
    name: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    primary: bool = False


@dataclass
class Contact:
    """A contact."""
    id: str
    name: Name
    emails: List[EmailAddress] = field(default_factory=list)
    phones: List[PhoneNumber] = field(default_factory=list)
    addresses: List[Address] = field(default_factory=list)
    organizations: List[Organization] = field(default_factory=list)
    birthday: Optional[date] = None
    notes: Optional[str] = None
    photo_url: Optional[str] = None
    groups: List[str] = field(default_factory=list)
    etag: Optional[str] = None
    
    @property
    def display_name(self) -> str:
        """Get display name."""
        return str(self.name) or "(No name)"
    
    @property
    def primary_email(self) -> Optional[str]:
        """Get primary email address."""
        for email in self.emails:
            if email.primary:
                return email.address
        return self.emails[0].address if self.emails else None
    
    @property
    def primary_phone(self) -> Optional[str]:
        """Get primary phone number."""
        for phone in self.phones:
            if phone.primary:
                return phone.number
        return self.phones[0].number if self.phones else None


@dataclass
class ContactGroup:
    """A contact group/label."""
    id: str
    name: str
    member_count: int = 0
    group_type: str = "user"  # "user" or "system"


@dataclass
class ContactPage:
    """Paginated contact results."""
    contacts: List[Contact]
    next_cursor: Optional[str] = None
    total_count: Optional[int] = None


@dataclass
class ContactsAccount:
    """A named contacts account configuration."""
    name: str
    adapter: str
    credentials_ref: str
    config: Dict[str, Any] = field(default_factory=dict)


class ContactsAdapter(ABC):
    """Base class for contacts adapters."""
    
    adapter_type: str = "base"
    
    def __init__(self, account: ContactsAccount):
        self.account = account
        self._client = None
    
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to contacts service."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to contacts service."""
        pass
    
    @abstractmethod
    async def list_contacts(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        group_id: Optional[str] = None
    ) -> ContactPage:
        """List contacts with pagination."""
        pass
    
    @abstractmethod
    async def get_contact(self, contact_id: str) -> Optional[Contact]:
        """Get full contact details by ID."""
        pass
    
    @abstractmethod
    async def search_contacts(
        self,
        query: str,
        limit: int = 50
    ) -> List[Contact]:
        """Search contacts by name, email, or phone."""
        pass
    
    @abstractmethod
    async def create_contact(
        self,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        organization: Optional[str] = None,
        title: Optional[str] = None,
        notes: Optional[str] = None
    ) -> str:
        """Create a new contact. Returns contact ID."""
        pass
    
    @abstractmethod
    async def update_contact(
        self,
        contact_id: str,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        organization: Optional[str] = None,
        title: Optional[str] = None,
        notes: Optional[str] = None
    ) -> str:
        """Update an existing contact."""
        pass
    
    @abstractmethod
    async def delete_contact(self, contact_id: str) -> str:
        """Delete a contact."""
        pass
    
    @abstractmethod
    async def list_groups(self) -> List[ContactGroup]:
        """List contact groups/labels."""
        pass
    
    @abstractmethod
    async def add_to_group(self, contact_id: str, group_id: str) -> str:
        """Add contact to a group."""
        pass
    
    @abstractmethod
    async def remove_from_group(self, contact_id: str, group_id: str) -> str:
        """Remove contact from a group."""
        pass
