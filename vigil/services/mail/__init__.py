from .manager import MailManager
from .adapters.gmail import GmailAdapter
from .adapters.outlook import OutlookAdapter

__all__ = ["MailManager", "GmailAdapter", "OutlookAdapter"]
