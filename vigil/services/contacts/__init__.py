from .manager import ContactsManager
from .adapters.gcontacts import GoogleContactsAdapter

__all__ = ["ContactsManager", "GoogleContactsAdapter"]
