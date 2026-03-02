"""
Google Contacts Adapter

Implements ContactsAdapter interface for Google People API.
"""

from pathlib import Path
from typing import List, Optional
from datetime import date
import logging

from ..interface import (
    ContactsAdapter, ContactsAccount, Contact, ContactPage, ContactGroup,
    Name, EmailAddress, PhoneNumber, Address, Organization,
    EmailType, PhoneType, AddressType
)

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = Path("/data/config/gcontacts_token.json")

# Field mask for reading contact data
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,addresses,organizations,birthdays,biographies,photos,memberships"


class GoogleContactsAdapter(ContactsAdapter):
    """Google Contacts adapter using People API."""
    
    adapter_type = "gcontacts"
    
    def __init__(self, account: ContactsAccount):
        super().__init__(account)
        self._service = None
        
        # Get token path from account config, with fallback to default
        self._token_path = Path(account.config.get("token_path", str(DEFAULT_TOKEN_PATH)))
    
    async def connect(self) -> bool:
        """Connect to Google People API."""
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
                logger.error(f"No credentials available at {self._token_path}. Complete OAuth flow first.")
                return False
            
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(self._token_path, 'w') as f:
                    f.write(creds.to_json())
            
            self._service = build('people', 'v1', credentials=creds)
            
            # Test connection using connections list (works with contacts scope)
            # Note: people/me requires 'profile' scope, but connections.list works with contacts scope
            self._service.people().connections().list(
                resourceName='people/me',
                pageSize=1,
                personFields='names'
            ).execute()
            
            logger.info(f"✅ Connected to Google Contacts")
            return True
            
        except ImportError:
            logger.error("❌ Google API libraries not installed")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to connect to Google Contacts: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from Google Contacts."""
        self._service = None
    
    def _parse_contact(self, person: dict) -> Contact:
        """Parse People API person into Contact object."""
        resource_name = person.get('resourceName', '')
        contact_id = resource_name.replace('people/', '') if resource_name else ''
        
        # Parse name
        names = person.get('names', [])
        name = Name()
        if names:
            n = names[0]
            name = Name(
                given=n.get('givenName'),
                family=n.get('familyName'),
                middle=n.get('middleName'),
                prefix=n.get('honorificPrefix'),
                suffix=n.get('honorificSuffix'),
                display=n.get('displayName')
            )
        
        # Parse emails
        emails = []
        for e in person.get('emailAddresses', []):
            email_type = self._map_email_type(e.get('type', ''))
            emails.append(EmailAddress(
                address=e.get('value', ''),
                type=email_type,
                primary=e.get('metadata', {}).get('primary', False),
                label=e.get('formattedType')
            ))
        
        # Parse phones
        phones = []
        for p in person.get('phoneNumbers', []):
            phone_type = self._map_phone_type(p.get('type', ''))
            phones.append(PhoneNumber(
                number=p.get('value', ''),
                type=phone_type,
                primary=p.get('metadata', {}).get('primary', False),
                label=p.get('formattedType')
            ))
        
        # Parse addresses
        addresses = []
        for a in person.get('addresses', []):
            addr_type = self._map_address_type(a.get('type', ''))
            addresses.append(Address(
                formatted=a.get('formattedValue'),
                street=a.get('streetAddress'),
                city=a.get('city'),
                region=a.get('region'),
                postal_code=a.get('postalCode'),
                country=a.get('country'),
                type=addr_type,
                primary=a.get('metadata', {}).get('primary', False)
            ))
        
        # Parse organizations
        organizations = []
        for o in person.get('organizations', []):
            organizations.append(Organization(
                name=o.get('name'),
                title=o.get('title'),
                department=o.get('department'),
                primary=o.get('metadata', {}).get('primary', False)
            ))
        
        # Parse birthday
        birthday = None
        birthdays = person.get('birthdays', [])
        if birthdays:
            b = birthdays[0].get('date', {})
            if b.get('year') and b.get('month') and b.get('day'):
                try:
                    birthday = date(b['year'], b['month'], b['day'])
                except:
                    pass
        
        # Parse notes/biography
        notes = None
        bios = person.get('biographies', [])
        if bios:
            notes = bios[0].get('value')
        
        # Parse photo
        photo_url = None
        photos = person.get('photos', [])
        if photos:
            photo_url = photos[0].get('url')
        
        # Parse group memberships
        groups = []
        for m in person.get('memberships', []):
            group_ref = m.get('contactGroupMembership', {}).get('contactGroupResourceName')
            if group_ref:
                groups.append(group_ref.replace('contactGroups/', ''))
        
        return Contact(
            id=contact_id,
            name=name,
            emails=emails,
            phones=phones,
            addresses=addresses,
            organizations=organizations,
            birthday=birthday,
            notes=notes,
            photo_url=photo_url,
            groups=groups,
            etag=person.get('etag')
        )
    
    def _map_email_type(self, type_str: str) -> EmailType:
        """Map Google type string to EmailType."""
        mapping = {
            'home': EmailType.HOME,
            'work': EmailType.WORK,
        }
        return mapping.get(type_str.lower(), EmailType.OTHER)
    
    def _map_phone_type(self, type_str: str) -> PhoneType:
        """Map Google type string to PhoneType."""
        mapping = {
            'mobile': PhoneType.MOBILE,
            'home': PhoneType.HOME,
            'work': PhoneType.WORK,
            'main': PhoneType.MAIN,
            'homeFax': PhoneType.FAX_HOME,
            'workFax': PhoneType.FAX_WORK,
            'pager': PhoneType.PAGER,
        }
        return mapping.get(type_str, PhoneType.OTHER)
    
    def _map_address_type(self, type_str: str) -> AddressType:
        """Map Google type string to AddressType."""
        mapping = {
            'home': AddressType.HOME,
            'work': AddressType.WORK,
        }
        return mapping.get(type_str.lower(), AddressType.OTHER)
    
    async def list_contacts(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        group_id: Optional[str] = None
    ) -> ContactPage:
        """List contacts with pagination."""
        if not self._service:
            return ContactPage(contacts=[])
        
        try:
            # Build request parameters
            params = {
                'resourceName': 'people/me',
                'pageSize': min(limit, 1000),
                'personFields': PERSON_FIELDS,
                'sortOrder': 'LAST_NAME_ASCENDING'
            }
            
            if cursor:
                params['pageToken'] = cursor
            
            # Filter by group if specified
            if group_id:
                # Use searchContacts for group filtering
                results = self._service.people().searchContacts(
                    query='',
                    pageSize=min(limit, 30),
                    readMask=PERSON_FIELDS
                ).execute()
                
                # Filter by group membership
                contacts = []
                for person in results.get('results', []):
                    p = person.get('person', {})
                    contact = self._parse_contact(p)
                    if group_id in contact.groups:
                        contacts.append(contact)
                
                return ContactPage(contacts=contacts)
            
            results = self._service.people().connections().list(**params).execute()
            
            contacts = []
            for person in results.get('connections', []):
                contacts.append(self._parse_contact(person))
            
            return ContactPage(
                contacts=contacts,
                next_cursor=results.get('nextPageToken'),
                total_count=results.get('totalPeople')
            )
            
        except Exception as e:
            logger.error(f"Failed to list contacts: {e}")
            return ContactPage(contacts=[])
    
    async def get_contact(self, contact_id: str) -> Optional[Contact]:
        """Get full contact details by ID."""
        if not self._service:
            return None
        
        try:
            resource_name = f"people/{contact_id}" if not contact_id.startswith('people/') else contact_id
            
            person = self._service.people().get(
                resourceName=resource_name,
                personFields=PERSON_FIELDS
            ).execute()
            
            return self._parse_contact(person)
            
        except Exception as e:
            logger.error(f"Failed to get contact: {e}")
            return None
    
    async def search_contacts(
        self,
        query: str,
        limit: int = 50
    ) -> List[Contact]:
        """Search contacts by name, email, or phone."""
        if not self._service:
            return []
        
        try:
            results = self._service.people().searchContacts(
                query=query,
                pageSize=min(limit, 30),  # API max is 30
                readMask=PERSON_FIELDS
            ).execute()
            
            contacts = []
            for result in results.get('results', []):
                person = result.get('person', {})
                contacts.append(self._parse_contact(person))
            
            return contacts
            
        except Exception as e:
            logger.error(f"Failed to search contacts: {e}")
            return []
    
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
        """Create a new contact."""
        if not self._service:
            return "❌ Not connected"
        
        try:
            person = {}
            
            if given_name or family_name:
                person['names'] = [{
                    'givenName': given_name or '',
                    'familyName': family_name or ''
                }]
            
            if email:
                person['emailAddresses'] = [{'value': email}]
            
            if phone:
                person['phoneNumbers'] = [{'value': phone}]
            
            if organization or title:
                person['organizations'] = [{
                    'name': organization or '',
                    'title': title or ''
                }]
            
            if notes:
                person['biographies'] = [{'value': notes, 'contentType': 'TEXT_PLAIN'}]
            
            result = self._service.people().createContact(body=person).execute()
            
            contact_id = result.get('resourceName', '').replace('people/', '')
            display = given_name or family_name or email or 'contact'
            
            return f"✅ Created contact: {display} (ID: {contact_id})"
            
        except Exception as e:
            return f"❌ Failed to create contact: {e}"
    
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
        if not self._service:
            return "❌ Not connected"
        
        try:
            resource_name = f"people/{contact_id}" if not contact_id.startswith('people/') else contact_id
            
            # Get current contact to get etag
            current = self._service.people().get(
                resourceName=resource_name,
                personFields=PERSON_FIELDS
            ).execute()
            
            etag = current.get('etag')
            
            # Build update
            person = {'etag': etag}
            update_fields = []
            
            if given_name is not None or family_name is not None:
                person['names'] = [{
                    'givenName': given_name or current.get('names', [{}])[0].get('givenName', ''),
                    'familyName': family_name or current.get('names', [{}])[0].get('familyName', '')
                }]
                update_fields.append('names')
            
            if email is not None:
                person['emailAddresses'] = [{'value': email}]
                update_fields.append('emailAddresses')
            
            if phone is not None:
                person['phoneNumbers'] = [{'value': phone}]
                update_fields.append('phoneNumbers')
            
            if organization is not None or title is not None:
                current_org = current.get('organizations', [{}])[0] if current.get('organizations') else {}
                person['organizations'] = [{
                    'name': organization if organization is not None else current_org.get('name', ''),
                    'title': title if title is not None else current_org.get('title', '')
                }]
                update_fields.append('organizations')
            
            if notes is not None:
                person['biographies'] = [{'value': notes, 'contentType': 'TEXT_PLAIN'}]
                update_fields.append('biographies')
            
            if not update_fields:
                return "❌ No fields to update"
            
            self._service.people().updateContact(
                resourceName=resource_name,
                updatePersonFields=','.join(update_fields),
                body=person
            ).execute()
            
            return f"✅ Updated contact: {contact_id}"
            
        except Exception as e:
            return f"❌ Failed to update contact: {e}"
    
    async def delete_contact(self, contact_id: str) -> str:
        """Delete a contact."""
        if not self._service:
            return "❌ Not connected"
        
        try:
            resource_name = f"people/{contact_id}" if not contact_id.startswith('people/') else contact_id
            
            self._service.people().deleteContact(resourceName=resource_name).execute()
            
            return f"✅ Deleted contact: {contact_id}"
            
        except Exception as e:
            return f"❌ Failed to delete contact: {e}"
    
    async def list_groups(self) -> List[ContactGroup]:
        """List contact groups/labels."""
        if not self._service:
            return []
        
        try:
            results = self._service.contactGroups().list(
                pageSize=100
            ).execute()
            
            groups = []
            for g in results.get('contactGroups', []):
                groups.append(ContactGroup(
                    id=g.get('resourceName', '').replace('contactGroups/', ''),
                    name=g.get('name', ''),
                    member_count=g.get('memberCount', 0),
                    group_type='system' if g.get('groupType') == 'SYSTEM_CONTACT_GROUP' else 'user'
                ))
            
            return groups
            
        except Exception as e:
            logger.error(f"Failed to list groups: {e}")
            return []
    
    async def add_to_group(self, contact_id: str, group_id: str) -> str:
        """Add contact to a group."""
        if not self._service:
            return "❌ Not connected"
        
        try:
            resource_name = f"contactGroups/{group_id}" if not group_id.startswith('contactGroups/') else group_id
            contact_resource = f"people/{contact_id}" if not contact_id.startswith('people/') else contact_id
            
            self._service.contactGroups().members().modify(
                resourceName=resource_name,
                body={'resourceNamesToAdd': [contact_resource]}
            ).execute()
            
            return f"✅ Added contact to group"
            
        except Exception as e:
            return f"❌ Failed to add to group: {e}"
    
    async def remove_from_group(self, contact_id: str, group_id: str) -> str:
        """Remove contact from a group."""
        if not self._service:
            return "❌ Not connected"
        
        try:
            resource_name = f"contactGroups/{group_id}" if not group_id.startswith('contactGroups/') else group_id
            contact_resource = f"people/{contact_id}" if not contact_id.startswith('people/') else contact_id
            
            self._service.contactGroups().members().modify(
                resourceName=resource_name,
                body={'resourceNamesToRemove': [contact_resource]}
            ).execute()
            
            return f"✅ Removed contact from group"
            
        except Exception as e:
            return f"❌ Failed to remove from group: {e}"
