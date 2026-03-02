"""
Gmail Mail Adapter

Implements MailAdapter interface for Gmail API.
"""

import base64
import mimetypes
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime, timezone
import logging
import os

from ..interface import (
    MailAdapter, MailAccount, Message, MessagePage, Folder,
    Address, Attachment, UploadedAttachment, MessageFlag
)

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = Path("/data/config/gmail_token.json")


class GmailAdapter(MailAdapter):
    """Gmail mail adapter."""
    
    adapter_type = "gmail"
    
    def __init__(self, account: MailAccount):
        super().__init__(account)
        self._service = None
        self._user_email = None
        self._pending_attachments: Dict[str, dict] = {}  # id -> {path, filename, mime_type}
        
        # Get token path from account config, with fallback to default
        self._token_path = Path(account.config.get("token_path", str(DEFAULT_TOKEN_PATH)))
    
    async def connect(self) -> bool:
        """Connect to Gmail API."""
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
            
            self._service = build('gmail', 'v1', credentials=creds)
            
            # Get user email
            profile = self._service.users().getProfile(userId='me').execute()
            self._user_email = profile['emailAddress']
            
            logger.info(f"✅ Connected to Gmail: {self._user_email}")
            return True
            
        except ImportError:
            logger.error("❌ Google API libraries not installed")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to connect to Gmail: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from Gmail."""
        self._service = None
        self._user_email = None
    
    def _parse_address(self, addr_str: str) -> Address:
        """Parse email address string into Address object."""
        if '<' in addr_str and '>' in addr_str:
            name = addr_str.split('<')[0].strip().strip('"')
            email = addr_str.split('<')[1].split('>')[0].strip()
            return Address(email=email, name=name if name else None)
        return Address(email=addr_str.strip())
    
    def _parse_message(self, msg_data: dict, include_body: bool = False) -> Message:
        """Parse Gmail API message into Message object."""
        headers = {}
        if 'payload' in msg_data and 'headers' in msg_data['payload']:
            for h in msg_data['payload']['headers']:
                headers[h['name']] = h['value']
        
        # Parse sender
        sender_str = headers.get('From', '')
        sender = self._parse_address(sender_str) if sender_str else Address(email='unknown')
        
        # Parse recipients
        to_str = headers.get('To', '')
        recipients = [self._parse_address(a.strip()) for a in to_str.split(',') if a.strip()]
        
        # Parse CC
        cc_str = headers.get('Cc', '')
        cc = [self._parse_address(a.strip()) for a in cc_str.split(',') if a.strip()] if cc_str else []
        
        # Parse date
        date = None
        if 'internalDate' in msg_data:
            timestamp = int(msg_data['internalDate']) / 1000
            date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        
        # Parse flags from labels
        labels = msg_data.get('labelIds', [])
        flags = []
        if 'UNREAD' in labels:
            flags.append(MessageFlag.UNREAD)
        else:
            flags.append(MessageFlag.READ)
        if 'STARRED' in labels:
            flags.append(MessageFlag.STARRED)
        if 'IMPORTANT' in labels:
            flags.append(MessageFlag.IMPORTANT)
        if 'DRAFT' in labels:
            flags.append(MessageFlag.DRAFT)
        if 'SENT' in labels:
            flags.append(MessageFlag.SENT)
        if 'TRASH' in labels:
            flags.append(MessageFlag.TRASH)
        if 'SPAM' in labels:
            flags.append(MessageFlag.SPAM)
        
        # Parse attachments
        attachments = []
        if 'payload' in msg_data:
            self._extract_attachments(msg_data['payload'], attachments)
        
        # Parse body if requested
        body_text = None
        body_html = None
        if include_body and 'payload' in msg_data:
            body_text, body_html = self._extract_body(msg_data['payload'])
        
        return Message(
            id=msg_data['id'],
            thread_id=msg_data.get('threadId'),
            subject=headers.get('Subject', '(no subject)'),
            sender=sender,
            recipients=recipients,
            cc=cc,
            date=date,
            snippet=msg_data.get('snippet', ''),
            body_text=body_text,
            body_html=body_html,
            flags=flags,
            labels=labels,
            attachments=attachments,
            headers=headers
        )
    
    def _extract_attachments(self, payload: dict, attachments: list):
        """Extract attachment metadata from payload."""
        if 'parts' in payload:
            for part in payload['parts']:
                self._extract_attachments(part, attachments)
        
        filename = payload.get('filename')
        if filename and payload.get('body', {}).get('attachmentId'):
            attachments.append(Attachment(
                id=payload['body']['attachmentId'],
                filename=filename,
                mime_type=payload.get('mimeType', 'application/octet-stream'),
                size=payload.get('body', {}).get('size', 0)
            ))
    
    def _extract_body(self, payload: dict) -> tuple:
        """Extract text and HTML body from payload."""
        text_body = None
        html_body = None
        
        def extract_parts(part):
            nonlocal text_body, html_body
            
            mime_type = part.get('mimeType', '')
            
            if mime_type == 'text/plain' and not text_body:
                data = part.get('body', {}).get('data')
                if data:
                    text_body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            elif mime_type == 'text/html' and not html_body:
                data = part.get('body', {}).get('data')
                if data:
                    html_body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            
            if 'parts' in part:
                for p in part['parts']:
                    extract_parts(p)
        
        extract_parts(payload)
        return text_body, html_body
    
    async def list_folders(self) -> List[Folder]:
        """List all Gmail labels as folders."""
        if not self._service:
            return []
        
        try:
            results = self._service.users().labels().list(userId='me').execute()
            labels = results.get('labels', [])
            
            folders = []
            for label in labels:
                # Get full label info for counts
                label_info = self._service.users().labels().get(
                    userId='me', id=label['id']
                ).execute()
                
                folder_type = None
                if label['id'] == 'INBOX':
                    folder_type = 'inbox'
                elif label['id'] == 'SENT':
                    folder_type = 'sent'
                elif label['id'] == 'DRAFT':
                    folder_type = 'drafts'
                elif label['id'] == 'TRASH':
                    folder_type = 'trash'
                elif label['id'] == 'SPAM':
                    folder_type = 'spam'
                
                folders.append(Folder(
                    id=label['id'],
                    name=label['name'],
                    path=label['name'],
                    message_count=label_info.get('messagesTotal', 0),
                    unread_count=label_info.get('messagesUnread', 0),
                    folder_type=folder_type
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
        """List messages in folder."""
        if not self._service:
            return MessagePage(messages=[])
        
        try:
            query = f"in:{folder}"
            if unread_only:
                query += " is:unread"
            
            params = {
                'userId': 'me',
                'q': query,
                'maxResults': min(limit, 100)
            }
            if cursor:
                params['pageToken'] = cursor
            
            results = self._service.users().messages().list(**params).execute()
            
            messages = []
            for msg_ref in results.get('messages', []):
                msg_data = self._service.users().messages().get(
                    userId='me',
                    id=msg_ref['id'],
                    format='metadata',
                    metadataHeaders=['From', 'To', 'Cc', 'Subject', 'Date']
                ).execute()
                messages.append(self._parse_message(msg_data))
            
            return MessagePage(
                messages=messages,
                next_cursor=results.get('nextPageToken'),
                total_estimate=results.get('resultSizeEstimate')
            )
            
        except Exception as e:
            logger.error(f"Failed to list messages: {e}")
            return MessagePage(messages=[])
    
    async def get_message(self, message_id: str) -> Optional[Message]:
        """Get full message including body."""
        if not self._service:
            return None
        
        try:
            msg_data = self._service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            return self._parse_message(msg_data, include_body=True)
            
        except Exception as e:
            logger.error(f"Failed to get message: {e}")
            return None
    
    async def list_thread(self, thread_id: str) -> List[Message]:
        """Get all messages in a thread."""
        if not self._service:
            return []
        
        try:
            thread = self._service.users().threads().get(
                userId='me',
                id=thread_id,
                format='metadata',
                metadataHeaders=['From', 'To', 'Cc', 'Subject', 'Date']
            ).execute()
            
            messages = []
            for msg_data in thread.get('messages', []):
                messages.append(self._parse_message(msg_data))
            
            # Sort by date
            messages.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=timezone.utc))
            return messages
            
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
        """Search messages using Gmail search syntax."""
        if not self._service:
            return MessagePage(messages=[])
        
        try:
            full_query = query
            if folder:
                full_query = f"in:{folder} {query}"
            
            params = {
                'userId': 'me',
                'q': full_query,
                'maxResults': min(limit, 100)
            }
            if cursor:
                params['pageToken'] = cursor
            
            results = self._service.users().messages().list(**params).execute()
            
            messages = []
            for msg_ref in results.get('messages', []):
                msg_data = self._service.users().messages().get(
                    userId='me',
                    id=msg_ref['id'],
                    format='metadata',
                    metadataHeaders=['From', 'To', 'Cc', 'Subject', 'Date']
                ).execute()
                messages.append(self._parse_message(msg_data))
            
            return MessagePage(
                messages=messages,
                next_cursor=results.get('nextPageToken'),
                total_estimate=results.get('resultSizeEstimate')
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
        mime_type = mime_type or 'application/octet-stream'
        size = path.stat().st_size
        
        # Generate a temporary ID
        import uuid
        att_id = str(uuid.uuid4())
        
        # Store for later use when sending
        self._pending_attachments[att_id] = {
            'path': str(path),
            'filename': actual_filename,
            'mime_type': mime_type
        }
        
        return UploadedAttachment(
            id=att_id,
            filename=actual_filename,
            mime_type=mime_type,
            size=size
        )
    
    async def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        local_path: str
    ) -> str:
        """Download attachment from a message."""
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            attachment = self._service.users().messages().attachments().get(
                userId='me',
                messageId=message_id,
                id=attachment_id
            ).execute()
            
            data = base64.urlsafe_b64decode(attachment['data'])
            
            path = Path(local_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            
            return f"✅ Downloaded attachment to: {local_path}"
            
        except Exception as e:
            return f"❌ Download failed: {e}"
    
    def _create_message(
        self,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        html: bool = False,
        attachment_ids: Optional[List[str]] = None,
        reply_to: Optional[Message] = None
    ) -> dict:
        """Create a message for the Gmail API."""
        if attachment_ids and any(aid in self._pending_attachments for aid in attachment_ids):
            msg = MIMEMultipart()
        else:
            msg = MIMEMultipart() if html else None
            if not msg:
                msg = MIMEText(body, 'plain')
        
        msg['To'] = ', '.join(to)
        msg['Subject'] = subject
        msg['From'] = self._user_email
        
        if cc:
            msg['Cc'] = ', '.join(cc)
        if bcc:
            msg['Bcc'] = ', '.join(bcc)
        
        # Threading headers for replies
        if reply_to:
            if reply_to.headers.get('Message-ID'):
                msg['In-Reply-To'] = reply_to.headers['Message-ID']
                msg['References'] = reply_to.headers.get('References', '') + ' ' + reply_to.headers['Message-ID']
        
        # Add body
        if isinstance(msg, MIMEMultipart):
            content_type = 'html' if html else 'plain'
            msg.attach(MIMEText(body, content_type))
        
        # Add attachments
        if attachment_ids:
            for att_id in attachment_ids:
                if att_id in self._pending_attachments:
                    att_info = self._pending_attachments[att_id]
                    with open(att_info['path'], 'rb') as f:
                        part = MIMEBase(*att_info['mime_type'].split('/'))
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename="{att_info["filename"]}"')
                    msg.attach(part)
        
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return {'raw': raw}
    
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
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            message = self._create_message(to, subject, body, cc, bcc, html, attachment_ids)
            result = self._service.users().messages().send(userId='me', body=message).execute()
            
            # Clean up used attachments
            if attachment_ids:
                for att_id in attachment_ids:
                    self._pending_attachments.pop(att_id, None)
            
            return f"✅ Sent message: {result['id']}"
            
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
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            # Get original message
            original = await self.get_message(message_id)
            if not original:
                return f"❌ Original message not found: {message_id}"
            
            # Determine recipients
            to = [original.sender.email]
            cc = None
            if reply_all:
                # Add all original recipients except self
                to.extend([r.email for r in original.recipients if r.email != self._user_email])
                if original.cc:
                    cc = [r.email for r in original.cc if r.email != self._user_email]
            
            # Create subject with Re:
            subject = original.subject
            if not subject.lower().startswith('re:'):
                subject = f"Re: {subject}"
            
            message = self._create_message(to, subject, body, cc, None, html, attachment_ids, original)
            message['threadId'] = original.thread_id
            
            result = self._service.users().messages().send(userId='me', body=message).execute()
            
            if attachment_ids:
                for att_id in attachment_ids:
                    self._pending_attachments.pop(att_id, None)
            
            return f"✅ Replied to message: {result['id']}"
            
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
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            original = await self.get_message(message_id)
            if not original:
                return f"❌ Original message not found: {message_id}"
            
            # Build forward body
            forward_body = body or ""
            forward_body += f"\n\n---------- Forwarded message ----------\n"
            forward_body += f"From: {original.sender}\n"
            forward_body += f"Date: {original.date}\n"
            forward_body += f"Subject: {original.subject}\n"
            forward_body += f"To: {', '.join(str(r) for r in original.recipients)}\n\n"
            forward_body += original.body_text or original.body_html or ""
            
            subject = original.subject
            if not subject.lower().startswith('fwd:'):
                subject = f"Fwd: {subject}"
            
            # Include original attachments
            # Note: For a full implementation, we'd need to download and re-attach
            
            message = self._create_message(to, subject, forward_body, None, None, False, attachment_ids)
            result = self._service.users().messages().send(userId='me', body=message).execute()
            
            return f"✅ Forwarded message: {result['id']}"
            
        except Exception as e:
            return f"❌ Forward failed: {e}"
    
    async def move(self, message_id: str, folder: str) -> str:
        """Move message to a folder (label)."""
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            self._service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'addLabelIds': [folder]}
            ).execute()
            return f"✅ Moved message to: {folder}"
            
        except Exception as e:
            return f"❌ Move failed: {e}"
    
    async def delete(self, message_id: str, permanent: bool = False) -> str:
        """Delete a message."""
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            if permanent:
                self._service.users().messages().delete(userId='me', id=message_id).execute()
                return f"✅ Permanently deleted message"
            else:
                self._service.users().messages().trash(userId='me', id=message_id).execute()
                return f"✅ Moved message to trash"
            
        except Exception as e:
            return f"❌ Delete failed: {e}"
    
    async def mark_read(self, message_id: str, read: bool = True) -> str:
        """Mark message as read or unread."""
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            if read:
                body = {'removeLabelIds': ['UNREAD']}
            else:
                body = {'addLabelIds': ['UNREAD']}
            
            self._service.users().messages().modify(
                userId='me',
                id=message_id,
                body=body
            ).execute()
            return f"✅ Marked message as {'read' if read else 'unread'}"
            
        except Exception as e:
            return f"❌ Mark read failed: {e}"
    
    async def mark_flagged(self, message_id: str, flagged: bool = True) -> str:
        """Mark message as starred."""
        if not self._service:
            return "❌ Not connected to Gmail"
        
        try:
            if flagged:
                body = {'addLabelIds': ['STARRED']}
            else:
                body = {'removeLabelIds': ['STARRED']}
            
            self._service.users().messages().modify(
                userId='me',
                id=message_id,
                body=body
            ).execute()
            return f"✅ {'Starred' if flagged else 'Unstarred'} message"
            
        except Exception as e:
            return f"❌ Mark flagged failed: {e}"
