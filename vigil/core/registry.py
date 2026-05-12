"""
Hooks Registry — Unified account definitions for all service types.

Loads config/hooks_registry.yaml and provides lookup methods for
account discovery and validation. Used by the bindings resolver
for error messages ("available accounts for this service") and
by future tooling for account management.

The registry is read-only at runtime. Account changes go through
Claude → write to hooks_registry.yaml → Vigil restart (or future
hot-reload).

Usage:
    from core.registry import get_registry

    reg = get_registry()
    account = reg.get_account("mail", "user@example.com")
    all_mail = reg.list_accounts("mail")
    exists = reg.account_exists("mail", "user@example.com")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path("/data/config/hooks_registry.yaml")
GLOBAL_BINDINGS_PATH = Path("/data/config/global_bindings.yaml")

_registry: Optional[HooksRegistry] = None
_global_bindings: Optional[dict] = None


@dataclass
class AccountEntry:
    """A single account in the registry."""
    name: str
    service: str
    adapter: str
    credentials_ref: str = ""
    config: dict = field(default_factory=dict)
    display: str = ""


class HooksRegistry:
    """Loaded hooks registry — read-only account catalog."""

    def __init__(self, data: dict):
        self._accounts: dict[str, dict[str, AccountEntry]] = {}
        self._raw = data

        for service, accounts in data.get("accounts", {}).items():
            if not isinstance(accounts, dict):
                continue
            self._accounts[service] = {}
            for name, entry in accounts.items():
                if not isinstance(entry, dict):
                    continue
                self._accounts[service][name] = AccountEntry(
                    name=name,
                    service=service,
                    adapter=entry.get("adapter", ""),
                    credentials_ref=entry.get("credentials_ref", ""),
                    config=entry.get("config", {}),
                    display=entry.get("display", name),
                )

    def get_account(self, service: str, name: str) -> Optional[AccountEntry]:
        """Look up an account by service type and name."""
        return self._accounts.get(service, {}).get(name)

    def list_accounts(self, service: Optional[str] = None) -> list[AccountEntry]:
        """List accounts, optionally filtered by service type."""
        if service:
            return list(self._accounts.get(service, {}).values())
        result = []
        for svc_accounts in self._accounts.values():
            result.extend(svc_accounts.values())
        return result

    def list_services(self) -> list[str]:
        """List all service types that have registered accounts."""
        return list(self._accounts.keys())

    def account_exists(self, service: str, name: str) -> bool:
        """Check if an account exists for a service type."""
        return name in self._accounts.get(service, {})

    def account_names(self, service: str) -> list[str]:
        """List account names for a service type."""
        return list(self._accounts.get(service, {}).keys())

    def notify_routing(self) -> dict:
        """Return notification routing config, if present."""
        return self._raw.get("notify_routing", {})

    def notify_default_recipient(self) -> dict:
        """Return notification default recipient, if present."""
        return self._raw.get("notify_default_recipient", {})


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning {} on any failure."""
    if not path.exists():
        logger.info(f"Config not found: {path}")
        return {}
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            logger.warning(f"{path} is not a mapping; ignoring")
            return {}
        return data
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
        return {}


def get_registry(reload: bool = False) -> HooksRegistry:
    """Get the hooks registry (singleton, lazily loaded).

    Args:
        reload: Force reload from disk.
    """
    global _registry
    if _registry is None or reload:
        data = _load_yaml(REGISTRY_PATH)
        _registry = HooksRegistry(data)
        count = sum(len(accts) for accts in _registry._accounts.values())
        logger.info(f"✅ Hooks registry loaded: {count} accounts across "
                     f"{len(_registry._accounts)} services")
    return _registry


def get_global_defaults(reload: bool = False) -> dict[str, str]:
    """Get global binding defaults (service → account name).

    Args:
        reload: Force reload from disk.
    """
    global _global_bindings
    if _global_bindings is None or reload:
        data = _load_yaml(GLOBAL_BINDINGS_PATH)
        _global_bindings = data.get("defaults", {})
        logger.info(f"✅ Global bindings loaded: {_global_bindings}")
    return _global_bindings


def get_global_default(service: str) -> Optional[str]:
    """Get the global default account name for a service type."""
    return get_global_defaults().get(service)
