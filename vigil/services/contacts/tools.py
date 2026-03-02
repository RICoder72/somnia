"""Contacts service MCP tools."""

import json
import logging
from .manager import ContactsManager
from .adapters.gcontacts import GoogleContactsAdapter

logger = logging.getLogger(__name__)

contacts_manager: ContactsManager = None


def register(mcp) -> None:
    """Register contacts tools with the MCP server."""
    global contacts_manager

    try:
        contacts_manager = ContactsManager()
        contacts_manager.register_adapter_type("gcontacts", GoogleContactsAdapter)
        logger.info("✅ Contacts service initialized")
    except Exception as e:
        logger.error(f"❌ Contacts service failed to initialize: {e}")
        return

    @mcp.tool()
    async def contacts_list(account: str, limit: int = 50) -> str:
        """List contacts."""
        page = await contacts_manager.list_contacts(account, limit)
        if not page.contacts:
            return f"No contacts found in {account}"
        lines = [f"👤 Contacts in {account}", "─" * 40]
        for c in page.contacts:
            email = c.primary_email or ""
            phone = c.primary_phone or ""
            info = f" | {email}" if email else ""
            info += f" | {phone}" if phone else ""
            lines.append(f"  {c.display_name}{info}")
            lines.append(f"    ID: {c.id}")
        if page.next_cursor:
            lines.append(f"\n(more contacts available)")
        return "\n".join(lines)

    @mcp.tool()
    async def contacts_search(account: str, query: str, limit: int = 20) -> str:
        """Search contacts by name, email, or phone."""
        contacts = await contacts_manager.search_contacts(account, query, limit)
        if not contacts:
            return f"No contacts matching: {query}"
        lines = [f"🔍 Search: {query}", "─" * 40]
        for c in contacts:
            email = c.primary_email or ""
            lines.append(f"  {c.display_name} | {email}")
            lines.append(f"    ID: {c.id}")
        return "\n".join(lines)

    @mcp.tool()
    async def contacts_get(account: str, contact_id: str) -> str:
        """Get full contact details."""
        contact = await contacts_manager.get_contact(account, contact_id)
        if not contact:
            return f"Contact not found: {contact_id}"
        lines = [
            f"👤 {contact.display_name}",
            "─" * 40,
        ]
        if contact.organizations:
            org = contact.organizations[0]
            if org.title and org.name:
                lines.append(f"🏢 {org.title} at {org.name}")
            elif org.name:
                lines.append(f"🏢 {org.name}")
            elif org.title:
                lines.append(f"💼 {org.title}")
        if contact.emails:
            lines.append("\nEmails:")
            for e in contact.emails:
                primary = " (primary)" if e.primary else ""
                lines.append(f"  📧 {e.address}{primary}")
        if contact.phones:
            lines.append("\nPhones:")
            for p in contact.phones:
                primary = " (primary)" if p.primary else ""
                lines.append(f"  📱 {p.number} ({p.type.value}){primary}")
        if contact.addresses:
            lines.append("\nAddresses:")
            for a in contact.addresses:
                lines.append(f"  📍 {a.formatted or 'No formatted address'}")
        if contact.birthday:
            lines.append(f"\n🎂 Birthday: {contact.birthday}")
        if contact.notes:
            lines.append(f"\nNotes: {contact.notes}")
        return "\n".join(lines)

    @mcp.tool()
    async def contacts_create(
        account: str,
        given_name: str = "",
        family_name: str = "",
        email: str = "",
        phone: str = "",
        organization: str = "",
        title: str = "",
        notes: str = ""
    ) -> str:
        """Create a new contact."""
        return await contacts_manager.create_contact(
            account,
            given_name=given_name or None,
            family_name=family_name or None,
            email=email or None,
            phone=phone or None,
            organization=organization or None,
            title=title or None,
            notes=notes or None
        )

    @mcp.tool()
    async def contacts_update(
        account: str,
        contact_id: str,
        given_name: str = None,
        family_name: str = None,
        email: str = None,
        phone: str = None,
        organization: str = None,
        title: str = None,
        notes: str = None
    ) -> str:
        """Update an existing contact."""
        return await contacts_manager.update_contact(
            account, contact_id,
            given_name=given_name,
            family_name=family_name,
            email=email,
            phone=phone,
            organization=organization,
            title=title,
            notes=notes
        )

    @mcp.tool()
    async def contacts_delete(account: str, contact_id: str) -> str:
        """Delete a contact."""
        return await contacts_manager.delete_contact(account, contact_id)

    logger.info("✅ Registered 6 contacts tools")
