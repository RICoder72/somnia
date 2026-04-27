"""
Outlook / Microsoft 365 Mail Adapter

Implements MailAdapter interface for Microsoft Graph API.
Supports both personal Microsoft accounts (Outlook.com) and
organizational accounts (M365 / Exchange Online).

Authentication uses MSAL (Microsoft Authentication Library) with
OAuth2 device code or authorization code flow. Tokens are persisted
to a JSON file and refreshed automatically.

Required Azure AD App Registration permissions (delegated):
  - Mail.Read
  - Mail.ReadWrite
  - Mail.Send
  - User.Read

Config keys in mail_accounts.json:
  - token_path: path to persisted token cache (default: /data/tokens/outlook_token_{account}.json)
  - client_id: Azure AD application (client) ID
  - tenant_id: Azure AD tenant ID ("common" for multi-tenant/personal, org GUID for M365)
  - client_secret: (optional) client secret for confidential apps; omit for public/device-code flow
"""

import json
import mimetypes
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import base64
import logging

from ..interface import (
    MailAdapter, MailAccount, Message, MessagePage, Folder,
    Address, Attachment, UploadedAttachment, MessageFlag
)

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
]


class OutlookAdapter(MailAdapter):
    """Microsoft Outlook / M365 mail adapter via Microsoft Graph API."""

    adapter_type = "outlook"

    def __init__(self, account: MailAccount):
        super().__init__(account)
        self._session = None          # aiohttp or httpx session
        self._access_token = None
        self._user_email = None
        self._msal_app = None
        self._token_cache = None
        self._pending_attachments: Dict[str, dict] = {}

        # Config
        cfg = account.config
        self._client_id = cfg.get("client_id", "")
        self._tenant_id = cfg.get("tenant_id", "common")
        self._client_secret = cfg.get("client_secret")
        self._token_path = Path(cfg.get(
            "token_path",
            f"/data/tokens/outlook_token_{account.name}.json"
        ))

    # ── Auth helpers ────────────────────────────────────────────────────────

    def _build_msal_app(self):
        """Build an MSAL application instance with persistent token cache."""
        import msal

        self._token_cache = msal.SerializableTokenCache()

        if self._token_path.exists():
            try:
                self._token_cache.deserialize(self._token_path.read_text())
            except Exception as e:
                logger.warning(f"Failed to load token cache: {e}")

        authority = f"https://login.microsoftonline.com/{self._tenant_id}"

        if self._client_secret:
            app = msal.ConfidentialClientApplication(
                self._client_id,
                authority=authority,
                client_credential=self._client_secret,
                token_cache=self._token_cache,
            )
        else:
            app = msal.PublicClientApplication(
                self._client_id,
                authority=authority,
                token_cache=self._token_cache,
            )

        return app

    def _save_token_cache(self):
        """Persist token cache to disk if changed."""
        if self._token_cache and self._token_cache.has_state_changed:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(self._token_cache.serialize())

    def _acquire_token(self) -> Optional[str]:
        """Acquire an access token (silent refresh first, then error)."""
        accounts = self._msal_app.get_accounts()

        if accounts:
            result = self._msal_app.acquire_token_silent(
                GRAPH_SCOPES, account=accounts[0]
            )
            if result and "access_token" in result:
                self._save_token_cache()
                return result["access_token"]

        # If we get here, we have no cached token and can't do interactive
        # auth in a headless container. The user needs to run the bootstrap
        # script to get the initial token.
        logger.error(
            "No valid token available. Run the Outlook OAuth bootstrap "
            "script to obtain initial tokens."
        )
        return None

    # ── HTTP helpers ────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _graph_get(self, path: str, params: Optional[dict] = None) -> dict:
        """Synchronous GET against Graph API."""
        import requests
        url = f"{GRAPH_BASE}{path}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _graph_post(self, path: str, body: dict) -> dict:
        """Synchronous POST against Graph API."""
        import requests
        url = f"{GRAPH_BASE}{path}"
        resp = requests.post(url, headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _graph_patch(self, path: str, body: dict) -> dict:
        """Synchronous PATCH against Graph API."""
        import requests
        url = f"{GRAPH_BASE}{path}"
        resp = requests.patch(url, headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _graph_delete(self, path: str) -> None:
        """Synchronous DELETE against Graph API."""
        import requests
        url = f"{GRAPH_BASE}{path}"
        resp = requests.delete(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()

    # ── Parsing helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_address(addr: dict) -> Address:
        """Parse a Graph emailAddress object."""
        ea = addr.get("emailAddress", addr)
        return Address(
            email=ea.get("address", ""),
            name=ea.get("name") or None,
        )

    @staticmethod
    def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """Parse Graph datetime string (ISO 8601) into UTC datetime."""
        if not dt_str:
            return None
        try:
            # Graph returns e.g. "2026-04-26T14:30:00Z" or with offset
            dt_str = dt_str.replace("Z", "+00:00")
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None

    def _parse_message(self, data: dict, include_body: bool = False) -> Message:
        """Parse a Graph message resource into our Message dataclass."""
        sender = Address(email="unknown")
        if data.get("from"):
            sender = self._parse_address(data["from"])

        recipients = [
            self._parse_address(r)
            for r in data.get("toRecipients", [])
        ]
        cc = [
            self._parse_address(r)
            for r in data.get("ccRecipients", [])
        ]
        bcc = [
            self._parse_address(r)
            for r in data.get("bccRecipients", [])
        ]

        # Flags
        flags = []
        if data.get("isRead") is True:
            flags.append(MessageFlag.READ)
        elif data.get("isRead") is False:
            flags.append(MessageFlag.UNREAD)
        if data.get("flag", {}).get("flagStatus") == "flagged":
            flags.append(MessageFlag.STARRED)
        if data.get("importance") == "high":
            flags.append(MessageFlag.IMPORTANT)
        if data.get("isDraft"):
            flags.append(MessageFlag.DRAFT)

        # Labels / categories
        labels = data.get("categories", [])
        # Add pseudo-labels for folder context
        parent_folder = data.get("parentFolderId", "")

        # Attachments (metadata only from list view)
        attachments = []
        if data.get("hasAttachments") and include_body:
            # Fetch attachment metadata
            try:
                att_resp = self._graph_get(f"/me/messages/{data['id']}/attachments")
                for att in att_resp.get("value", []):
                    if att.get("@odata.type") == "#microsoft.graph.fileAttachment":
                        attachments.append(Attachment(
                            id=att["id"],
                            filename=att.get("name", "attachment"),
                            mime_type=att.get("contentType", "application/octet-stream"),
                            size=att.get("size", 0),
                        ))
            except Exception as e:
                logger.warning(f"Failed to fetch attachments for {data['id']}: {e}")

        # Date
        date = self._parse_datetime(
            data.get("receivedDateTime") or data.get("sentDateTime")
        )

        # Body
        body_text = None
        body_html = None
        if include_body and data.get("body"):
            body_content = data["body"].get("content", "")
            content_type = data["body"].get("contentType", "text")
            if content_type == "html":
                body_html = body_content
                # Basic HTML-to-text fallback
                try:
                    import re
                    body_text = re.sub(r"<[^>]+>", "", body_content)
                    body_text = body_text.strip()
                except Exception:
                    pass
            else:
                body_text = body_content

        return Message(
            id=data["id"],
            thread_id=data.get("conversationId"),
            subject=data.get("subject", "(no subject)"),
            sender=sender,
            recipients=recipients,
            cc=cc,
            bcc=bcc,
            date=date,
            snippet=data.get("bodyPreview", ""),
            body_text=body_text,
            body_html=body_html,
            flags=flags,
            labels=labels,
            attachments=attachments,
            headers={},
        )

    # ── Folder ID mapping ──────────────────────────────────────────────────

    # Graph uses folder IDs, but callers use names like "INBOX", "SENT", etc.
    _WELL_KNOWN_FOLDERS = {
        "INBOX": "inbox",
        "SENT": "sentitems",
        "DRAFT": "drafts",
        "DRAFTS": "drafts",
        "TRASH": "deleteditems",
        "DELETED": "deleteditems",
        "JUNK": "junkemail",
        "SPAM": "junkemail",
        "ARCHIVE": "archive",
    }

    def _resolve_folder(self, folder: str) -> str:
        """Map common folder names to Graph well-known folder names."""
        return self._WELL_KNOWN_FOLDERS.get(folder.upper(), folder)

    # ── Interface implementation ────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to Microsoft Graph."""
        try:
            import msal  # noqa: F401
            import requests  # noqa: F401
        except ImportError:
            logger.error("❌ msal or requests not installed")
            return False

        try:
            self._msal_app = self._build_msal_app()
            self._access_token = self._acquire_token()

            if not self._access_token:
                return False

            # Verify by getting user profile
            profile = self._graph_get("/me")
            self._user_email = profile.get("mail") or profile.get("userPrincipalName", "unknown")
            logger.info(f"✅ Connected to Outlook: {self._user_email}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to connect to Outlook: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from Microsoft Graph."""
        self._access_token = None
        self._user_email = None
        self._msal_app = None

    async def list_folders(self) -> List[Folder]:
        """List all mail folders."""
        if not self._access_token:
            return []

        try:
            resp = self._graph_get("/me/mailFolders", params={"$top": "100"})

            folders = []
            for f in resp.get("value", []):
                folder_type = None
                display = f.get("displayName", "")
                if display.lower() == "inbox":
                    folder_type = "inbox"
                elif display.lower() == "sent items":
                    folder_type = "sent"
                elif display.lower() == "drafts":
                    folder_type = "drafts"
                elif display.lower() == "deleted items":
                    folder_type = "trash"
                elif display.lower() == "junk email":
                    folder_type = "spam"

                folders.append(Folder(
                    id=f["id"],
                    name=display,
                    path=display,
                    message_count=f.get("totalItemCount", 0),
                    unread_count=f.get("unreadItemCount", 0),
                    folder_type=folder_type,
                ))

            return folders

        except Exception as e:
            logger.error(f"Failed to list folders: {e}")
            return []

    async def list_messages(
        self,
        folder: str = "INBOX",
        limit: int = 50,
        cursor: Optional[str] = None,
        unread_only: bool = False
    ) -> MessagePage:
        """List messages in folder with OData pagination."""
        if not self._access_token:
            return MessagePage(messages=[])

        try:
            resolved = self._resolve_folder(folder)
            path = f"/me/mailFolders/{resolved}/messages"

            params: Dict[str, Any] = {
                "$top": str(min(limit, 100)),
                "$orderby": "receivedDateTime desc",
                "$select": "id,conversationId,subject,from,toRecipients,ccRecipients,"
                           "receivedDateTime,bodyPreview,isRead,flag,importance,isDraft,"
                           "hasAttachments,categories,parentFolderId",
            }

            if unread_only:
                params["$filter"] = "isRead eq false"

            if cursor:
                # cursor is a $skip value
                params["$skip"] = cursor

            resp = self._graph_get(path, params=params)

            messages = [self._parse_message(m) for m in resp.get("value", [])]

            # Determine next cursor
            next_cursor = None
            if "@odata.nextLink" in resp:
                # Extract $skip from next link, or just use count-based offset
                next_cursor = str(int(cursor or 0) + len(messages))

            return MessagePage(
                messages=messages,
                next_cursor=next_cursor,
                total_estimate=resp.get("@odata.count"),
            )

        except Exception as e:
            logger.error(f"Failed to list messages: {e}")
            return MessagePage(messages=[])

    async def get_message(self, message_id: str) -> Optional[Message]:
        """Get full message including body."""
        if not self._access_token:
            return None

        try:
            data = self._graph_get(f"/me/messages/{message_id}")
            return self._parse_message(data, include_body=True)

        except Exception as e:
            logger.error(f"Failed to get message: {e}")
            return None

    async def list_thread(self, thread_id: str) -> List[Message]:
        """Get all messages in a conversation."""
        if not self._access_token:
            return []

        try:
            params = {
                "$filter": f"conversationId eq '{thread_id}'",
                "$orderby": "receivedDateTime asc",
                "$top": "50",
                "$select": "id,conversationId,subject,from,toRecipients,ccRecipients,"
                           "receivedDateTime,bodyPreview,isRead,flag,importance,isDraft,"
                           "hasAttachments,categories",
            }
            resp = self._graph_get("/me/messages", params=params)
            return [self._parse_message(m) for m in resp.get("value", [])]

        except Exception as e:
            logger.error(f"Failed to list thread: {e}")
            return []

    async def search(
        self,
        query: str,
        folder: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None
    ) -> MessagePage:
        """Search messages using $search (KQL syntax)."""
        if not self._access_token:
            return MessagePage(messages=[])

        try:
            if folder:
                resolved = self._resolve_folder(folder)
                path = f"/me/mailFolders/{resolved}/messages"
            else:
                path = "/me/messages"

            params: Dict[str, Any] = {
                "$search": f'"{query}"',
                "$top": str(min(limit, 100)),
                "$select": "id,conversationId,subject,from,toRecipients,ccRecipients,"
                           "receivedDateTime,bodyPreview,isRead,flag,importance,isDraft,"
                           "hasAttachments,categories,parentFolderId",
            }

            if cursor:
                params["$skip"] = cursor

            resp = self._graph_get(path, params=params)

            messages = [self._parse_message(m) for m in resp.get("value", [])]

            next_cursor = None
            if "@odata.nextLink" in resp:
                next_cursor = str(int(cursor or 0) + len(messages))

            return MessagePage(
                messages=messages,
                next_cursor=next_cursor,
                total_estimate=resp.get("@odata.count"),
            )

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return MessagePage(messages=[])

    async def upload_attachment(
        self,
        local_path: str,
        filename: Optional[str] = None
    ) -> UploadedAttachment:
        """Stage an attachment for sending."""
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")

        actual_filename = filename or path.name
        mime_type, _ = mimetypes.guess_type(local_path)
        mime_type = mime_type or "application/octet-stream"
        size = path.stat().st_size

        att_id = str(uuid.uuid4())
        self._pending_attachments[att_id] = {
            "path": str(path),
            "filename": actual_filename,
            "mime_type": mime_type,
        }

        return UploadedAttachment(
            id=att_id,
            filename=actual_filename,
            mime_type=mime_type,
            size=size,
        )

    async def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        local_path: str
    ) -> str:
        """Download attachment from a message."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            import requests as req
            url = f"{GRAPH_BASE}/me/messages/{message_id}/attachments/{attachment_id}/$value"
            resp = req.get(url, headers=self._headers(), timeout=60)
            resp.raise_for_status()

            path = Path(local_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)

            return f"✅ Downloaded attachment to: {local_path}"

        except Exception as e:
            return f"❌ Download failed: {e}"

    def _build_graph_message(
        self,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        html: bool = False,
        attachment_ids: Optional[List[str]] = None,
    ) -> dict:
        """Build a Graph API message payload."""
        message: Dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "HTML" if html else "Text",
                "content": body,
            },
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to
            ],
        }

        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]
        if bcc:
            message["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc
            ]

        # Inline attachments
        if attachment_ids:
            atts = []
            for att_id in attachment_ids:
                if att_id in self._pending_attachments:
                    info = self._pending_attachments[att_id]
                    data = Path(info["path"]).read_bytes()
                    atts.append({
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": info["filename"],
                        "contentType": info["mime_type"],
                        "contentBytes": base64.b64encode(data).decode(),
                    })
            if atts:
                message["attachments"] = atts

        return message

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
        """Send a new message."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            message = self._build_graph_message(
                to, subject, body, cc, bcc, html, attachment_ids
            )
            payload = {"message": message, "saveToSentItems": True}

            import requests as req
            url = f"{GRAPH_BASE}/me/sendMail"
            resp = req.post(url, headers=self._headers(), json=payload, timeout=30)
            resp.raise_for_status()

            # Clean up staged attachments
            if attachment_ids:
                for att_id in attachment_ids:
                    self._pending_attachments.pop(att_id, None)

            return "✅ Sent message"

        except Exception as e:
            return f"❌ Send failed: {e}"

    async def reply(
        self,
        message_id: str,
        body: str,
        reply_all: bool = False,
        html: bool = False,
        attachment_ids: Optional[List[str]] = None
    ) -> str:
        """Reply to a message."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            endpoint = "replyAll" if reply_all else "reply"
            payload: Dict[str, Any] = {
                "comment": body,
            }

            # Graph's reply endpoint handles threading automatically
            import requests as req
            url = f"{GRAPH_BASE}/me/messages/{message_id}/{endpoint}"
            resp = req.post(url, headers=self._headers(), json=payload, timeout=30)
            resp.raise_for_status()

            return f"✅ Replied to message"

        except Exception as e:
            return f"❌ Reply failed: {e}"

    async def forward(
        self,
        message_id: str,
        to: List[str],
        body: Optional[str] = None,
        attachment_ids: Optional[List[str]] = None
    ) -> str:
        """Forward a message."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            payload: Dict[str, Any] = {
                "toRecipients": [
                    {"emailAddress": {"address": addr}} for addr in to
                ],
            }
            if body:
                payload["comment"] = body

            import requests as req
            url = f"{GRAPH_BASE}/me/messages/{message_id}/forward"
            resp = req.post(url, headers=self._headers(), json=payload, timeout=30)
            resp.raise_for_status()

            return "✅ Forwarded message"

        except Exception as e:
            return f"❌ Forward failed: {e}"

    async def move(self, message_id: str, folder: str) -> str:
        """Move message to a folder."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            resolved = self._resolve_folder(folder)
            # Need to get folder ID — well-known names work directly
            body = {"destinationId": resolved}
            self._graph_post(f"/me/messages/{message_id}/move", body)
            return f"✅ Moved message to: {folder}"

        except Exception as e:
            return f"❌ Move failed: {e}"

    async def delete(self, message_id: str, permanent: bool = False) -> str:
        """Delete a message."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            if permanent:
                self._graph_delete(f"/me/messages/{message_id}")
                return "✅ Permanently deleted message"
            else:
                # Move to deleted items
                body = {"destinationId": "deleteditems"}
                self._graph_post(f"/me/messages/{message_id}/move", body)
                return "✅ Moved message to trash"

        except Exception as e:
            return f"❌ Delete failed: {e}"

    async def mark_read(self, message_id: str, read: bool = True) -> str:
        """Mark message as read or unread."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            self._graph_patch(f"/me/messages/{message_id}", {"isRead": read})
            return f"✅ Marked message as {'read' if read else 'unread'}"

        except Exception as e:
            return f"❌ Mark read failed: {e}"

    async def mark_flagged(self, message_id: str, flagged: bool = True) -> str:
        """Mark message as flagged."""
        if not self._access_token:
            return "❌ Not connected to Outlook"

        try:
            flag_status = "flagged" if flagged else "notFlagged"
            self._graph_patch(
                f"/me/messages/{message_id}",
                {"flag": {"flagStatus": flag_status}},
            )
            return f"✅ {'Flagged' if flagged else 'Unflagged'} message"

        except Exception as e:
            return f"❌ Mark flagged failed: {e}"
