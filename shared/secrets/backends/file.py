"""
Encrypted file secrets backend.

Stores secrets in a Fernet-encrypted JSON file. This is the recommended
backend for OSS deployments — no external dependencies, supports runtime
read/write, encrypted at rest.

Vault file: /data/config/secrets.enc (configurable)
Master key: SOMNIA_MASTER_KEY env var (Fernet key, generated at bootstrap)

The vault is a JSON dict encrypted as a single Fernet token. On read,
the entire vault is decrypted into memory. On write, the vault is
re-encrypted and flushed to disk. This is fine for the expected scale
(dozens of secrets, not thousands).

File locking uses a simple .lock file for multi-process safety.
"""

import json
import os
import logging
import time
from pathlib import Path
from typing import Optional

from ..interface import SecretsBackend

logger = logging.getLogger(__name__)


def _import_fernet():
    """Lazy import so the cryptography package isn't required at module load."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
        return Fernet, InvalidToken
    except ImportError:
        raise RuntimeError(
            "The 'cryptography' package is required for the file backend. "
            "Install it: pip install cryptography"
        )


class FileBackend(SecretsBackend):

    def __init__(self, vault_path: str, master_key: str):
        self._path = Path(vault_path)
        Fernet, _ = _import_fernet()
        self._fernet = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        self._cache: Optional[dict] = None
        self._cache_mtime: float = 0

    def _read_vault(self) -> dict:
        """Read and decrypt the vault file. Returns empty dict if missing."""
        if not self._path.exists():
            return {}

        # Use file mtime as a cache invalidation signal
        mtime = self._path.stat().st_mtime
        if self._cache is not None and mtime == self._cache_mtime:
            return self._cache

        _, InvalidToken = _import_fernet()
        try:
            encrypted = self._path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            self._cache = json.loads(decrypted)
            self._cache_mtime = mtime
            return self._cache
        except InvalidToken:
            raise RuntimeError(
                f"Failed to decrypt {self._path} — wrong SOMNIA_MASTER_KEY?"
            )
        except json.JSONDecodeError:
            raise RuntimeError(
                f"Vault file {self._path} decrypted but contains invalid JSON"
            )

    def _write_vault(self, data: dict) -> None:
        """Encrypt and write the vault file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        lock_path = self._path.with_suffix(".lock")
        # Simple file-based lock with timeout
        for _ in range(50):  # 5 second timeout
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                time.sleep(0.1)
        else:
            # Stale lock — force break after timeout
            logger.warning(f"Breaking stale lock: {lock_path}")
            lock_path.unlink(missing_ok=True)

        try:
            plaintext = json.dumps(data, indent=2, sort_keys=True).encode()
            encrypted = self._fernet.encrypt(plaintext)
            # Atomic write via temp file + rename
            tmp_path = self._path.with_suffix(".tmp")
            tmp_path.write_bytes(encrypted)
            tmp_path.rename(self._path)
            # Restrictive permissions
            os.chmod(str(self._path), 0o600)
            # Invalidate cache
            self._cache = data
            self._cache_mtime = self._path.stat().st_mtime
        finally:
            lock_path.unlink(missing_ok=True)

    def get(self, key: str) -> str | None:
        vault = self._read_vault()
        return vault.get(key)

    def set(self, key: str, value: str) -> None:
        vault = self._read_vault()
        vault[key] = value
        self._write_vault(vault)

    def delete(self, key: str) -> None:
        vault = self._read_vault()
        if key in vault:
            del vault[key]
            self._write_vault(vault)

    def list(self, prefix: str = "") -> list[str]:
        vault = self._read_vault()
        return sorted(k for k in vault if k.startswith(prefix))

    def info(self) -> dict:
        vault = self._read_vault()
        return {
            "type": "file",
            "writable": True,
            "path": str(self._path),
            "secret_count": len(vault),
            "description": "Encrypted file backend (Fernet)",
        }
