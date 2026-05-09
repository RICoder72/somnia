"""
Abstract base class for secrets backends.

All backends implement this interface. Keys use dot-separated namespaces:
    db.password
    auth.jwt_secret
    claude.api_key
    hooks.gmail.refresh_token
"""

from abc import ABC, abstractmethod


class SecretsBackend(ABC):

    @abstractmethod
    def get(self, key: str) -> str | None:
        """Retrieve a secret. Returns None if not found."""
        ...

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        """Store a secret. Raises NotImplementedError if backend is read-only."""
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove a secret."""
        ...

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """List keys matching the prefix."""
        ...

    def exists(self, key: str) -> bool:
        """Check if a key exists. Default: try get()."""
        return self.get(key) is not None

    @abstractmethod
    def info(self) -> dict:
        """Return diagnostic info about this backend."""
        ...
