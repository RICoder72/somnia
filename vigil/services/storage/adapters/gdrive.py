"""
Google Drive Storage Provider

Implements StorageProvider interface for Google Drive.
Uses existing token file or credentials from account config.
"""

from pathlib import Path
from typing import List, Optional
from datetime import datetime
import json
import logging

from ..interface import StorageProvider, StorageAccount, FileInfo

logger = logging.getLogger(__name__)

# Token locations: writable copy preferred, read-only original as fallback
TOKEN_PATH_RO = Path("/data/config/gdrive_token.json")
TOKEN_PATH_RW = Path("/data/runtime/vigil/tokens/gdrive_token.json")


class GoogleDriveProvider(StorageProvider):
    """
    Google Drive storage provider.
    
    Authentication priority:
    1. Existing token file at /data/config/gdrive_token.json
    2. credentials_json in account.config (for programmatic setup)
    """
    
    provider_type = "gdrive"
    
    def __init__(self, account: StorageAccount):
        super().__init__(account)
        self._service = None
        self._root_folder_id = None
    
    async def connect(self) -> bool:
        """Connect to Google Drive API."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            
            creds = None
            
            # Priority 1: Check writable token (refreshed copy)
            for token_path in (TOKEN_PATH_RW, TOKEN_PATH_RO):
                if token_path.exists():
                    try:
                        creds = Credentials.from_authorized_user_file(str(token_path))
                        logger.info(f"Loaded credentials from {token_path}")
                        break
                    except Exception as e:
                        logger.warning(f"Failed to load {token_path}: {e}")
            
            # Priority 2: credentials_json in config (fallback)
            if not creds:
                creds_json = self.account.config.get("credentials_json")
                if creds_json:
                    creds_data = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
                    creds = Credentials.from_authorized_user_info(creds_data)
                    logger.info("Loaded credentials from config")
            
            if not creds:
                logger.error("No credentials available. Complete OAuth flow first.")
                return False
            
            # Refresh if expired
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Save refreshed token to writable location
                TOKEN_PATH_RW.parent.mkdir(parents=True, exist_ok=True)
                with open(TOKEN_PATH_RW, 'w') as f:
                    f.write(creds.to_json())
                logger.info(f"Token refreshed and saved to {TOKEN_PATH_RW}")
            
            self._service = build('drive', 'v3', credentials=creds)
            
            # Test connection
            self._service.about().get(fields="user").execute()
            
            logger.info(f"✅ Connected to Google Drive: {self.account.name}")
            return True
            
        except ImportError:
            logger.error("❌ Google API libraries not installed")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to connect to Google Drive: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from Google Drive."""
        self._service = None
        self._root_folder_id = None
    
    def _resolve_path(self, path: str) -> str:
        """Resolve a path like '/Supernote/Note' to a folder ID."""
        if not path or path == "/":
            return "root"
        
        if not path.startswith("/"):
            return path  # Already an ID
        
        parts = [p for p in path.split("/") if p]
        current_id = "root"
        
        for part in parts:
            query = f"name='{part}' and '{current_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self._service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])
            
            if not files:
                raise ValueError(f"Folder not found: {part} in path {path}")
            current_id = files[0]['id']
        
        return current_id
    
    async def upload(self, local_path: Path, remote_path: str) -> str:
        """Upload file to Google Drive."""
        if not self._service:
            return "❌ Not connected to Google Drive"
        
        try:
            from googleapiclient.http import MediaFileUpload
            
            file_name = Path(remote_path).name
            parent_path = str(Path(remote_path).parent)
            
            try:
                parent_id = self._resolve_path(parent_path) if parent_path != "." else "root"
            except ValueError:
                parent_id = "root"
            
            # Check if file exists
            query = f"name='{file_name}' and '{parent_id}' in parents and trashed=false"
            results = self._service.files().list(q=query, fields="files(id)").execute()
            existing = results.get('files', [])
            
            media = MediaFileUpload(str(local_path), resumable=True)
            
            if existing:
                file_id = existing[0]['id']
                self._service.files().update(fileId=file_id, media_body=media).execute()
                return f"✅ Updated: {remote_path}"
            else:
                metadata = {'name': file_name, 'parents': [parent_id]}
                self._service.files().create(body=metadata, media_body=media, fields='id').execute()
                return f"✅ Uploaded: {remote_path}"
                
        except Exception as e:
            return f"❌ Upload failed: {e}"
    
    async def download(self, remote_path: str, local_path: Path) -> str:
        """Download file from Google Drive."""
        if not self._service:
            return "❌ Not connected to Google Drive"
        
        try:
            from googleapiclient.http import MediaIoBaseDownload
            import io
            
            file_name = Path(remote_path).name
            parent_path = str(Path(remote_path).parent)
            
            try:
                parent_id = self._resolve_path(parent_path) if parent_path != "." else "root"
            except ValueError:
                return f"❌ Path not found: {remote_path}"
            
            # Find file
            query = f"name='{file_name}' and '{parent_id}' in parents and trashed=false"
            results = self._service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])
            
            if not files:
                return f"❌ File not found: {remote_path}"
            
            file_id = files[0]['id']
            request = self._service.files().get_media(fileId=file_id)
            
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(local_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            
            return f"✅ Downloaded: {remote_path} → {local_path}"
            
        except Exception as e:
            return f"❌ Download failed: {e}"
    
    async def list_files(self, remote_path: str = "/") -> List[FileInfo]:
        """List files in Google Drive folder."""
        if not self._service:
            return []
        
        try:
            parent_id = self._resolve_path(remote_path)
            
            query = f"'{parent_id}' in parents and trashed=false"
            results = self._service.files().list(
                q=query,
                fields="files(id, name, size, modifiedTime, mimeType)",
                orderBy="name"
            ).execute()
            
            files = []
            for item in results.get('files', []):
                is_dir = item['mimeType'] == 'application/vnd.google-apps.folder'
                modified = None
                if 'modifiedTime' in item:
                    modified = datetime.fromisoformat(item['modifiedTime'].replace('Z', '+00:00'))
                
                files.append(FileInfo(
                    name=item['name'],
                    path=f"{remote_path}/{item['name']}".replace("//", "/"),
                    size=int(item.get('size', 0)),
                    modified=modified,
                    is_directory=is_dir,
                    provider="gdrive",
                    account=self.account.name
                ))
            
            return files
            
        except Exception as e:
            logger.error(f"List failed: {e}")
            return []
    
    async def exists(self, remote_path: str) -> bool:
        """Check if file exists in Google Drive."""
        if not self._service:
            return False
        
        try:
            file_name = Path(remote_path).name
            parent_path = str(Path(remote_path).parent)
            
            try:
                parent_id = self._resolve_path(parent_path) if parent_path != "." else "root"
            except ValueError:
                return False
            
            query = f"name='{file_name}' and '{parent_id}' in parents and trashed=false"
            results = self._service.files().list(q=query, fields="files(id)").execute()
            
            return len(results.get('files', [])) > 0
            
        except Exception:
            return False
    
    async def delete(self, remote_path: str) -> str:
        """Delete file from Google Drive."""
        if not self._service:
            return "❌ Not connected to Google Drive"
        
        try:
            file_name = Path(remote_path).name
            parent_path = str(Path(remote_path).parent)
            
            try:
                parent_id = self._resolve_path(parent_path) if parent_path != "." else "root"
            except ValueError:
                return f"❌ Path not found: {remote_path}"
            
            query = f"name='{file_name}' and '{parent_id}' in parents and trashed=false"
            results = self._service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])
            
            if not files:
                return f"❌ File not found: {remote_path}"
            
            self._service.files().delete(fileId=files[0]['id']).execute()
            return f"✅ Deleted: {remote_path}"
            
        except Exception as e:
            return f"❌ Delete failed: {e}"
