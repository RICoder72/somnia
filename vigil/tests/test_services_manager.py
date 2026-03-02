"""
Tests for services.mail.manager — MailManager CRUD operations.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from services.mail.manager import MailManager
from services.mail.interface import MailAdapter, MailAccount


class FakeAdapter(MailAdapter):
    """Minimal adapter for testing manager CRUD."""
    adapter_type = "fake"

    async def connect(self): return True
    async def disconnect(self): pass
    async def list_folders(self): return []
    async def list_messages(self, *a, **kw): return MagicMock(messages=[])
    async def get_message(self, *a): return None
    async def list_thread(self, *a): return []
    async def search(self, *a, **kw): return MagicMock(messages=[])
    async def upload_attachment(self, *a, **kw): return None
    async def download_attachment(self, *a, **kw): return ""
    async def send(self, *a, **kw): return "sent"
    async def reply(self, *a, **kw): return "replied"
    async def forward(self, *a, **kw): return "forwarded"
    async def move(self, *a, **kw): return "moved"
    async def delete(self, *a, **kw): return "deleted"
    async def mark_read(self, *a, **kw): return "marked"
    async def mark_flagged(self, *a, **kw): return "flagged"


@pytest.fixture
def manager(tmp_path):
    """Create a MailManager with a temp config path."""
    config_path = tmp_path / "config" / "mail_accounts.json"
    config_path.parent.mkdir(parents=True)
    mgr = MailManager(config_path=config_path)
    mgr.register_adapter_type("fake", FakeAdapter)
    return mgr


class TestAddAccount:
    def test_add_account_success(self, manager):
        result = manager.add_account("work", "fake", "cred-ref")
        assert "✅" in result
        assert "work" in manager.accounts

    def test_add_duplicate_rejected(self, manager):
        manager.add_account("work", "fake")
        result = manager.add_account("work", "fake")
        assert "❌" in result
        assert "already exists" in result

    def test_add_unknown_adapter_rejected(self, manager):
        result = manager.add_account("work", "nonexistent")
        assert "❌" in result
        assert "Unknown adapter" in result


class TestRemoveAccount:
    def test_remove_existing(self, manager):
        manager.add_account("work", "fake")
        result = manager.remove_account("work")
        assert "✅" in result
        assert "work" not in manager.accounts

    def test_remove_nonexistent(self, manager):
        result = manager.remove_account("nope")
        assert "❌" in result


class TestListAccounts:
    def test_no_accounts(self, manager):
        result = manager.list_accounts()
        assert "No mail accounts" in result

    def test_with_accounts(self, manager):
        manager.add_account("personal", "fake")
        result = manager.list_accounts()
        assert "personal" in result
        assert "fake" in result


class TestPersistence:
    def test_config_persists(self, tmp_path):
        config_path = tmp_path / "config" / "mail_accounts.json"
        config_path.parent.mkdir(parents=True)

        mgr1 = MailManager(config_path=config_path)
        mgr1.register_adapter_type("fake", FakeAdapter)
        mgr1.add_account("persist-test", "fake", "cred")

        # New manager reads the same file
        mgr2 = MailManager(config_path=config_path)
        assert "persist-test" in mgr2.accounts
        assert mgr2.accounts["persist-test"].credentials_ref == "cred"


class TestGetAdapter:
    @pytest.mark.asyncio
    async def test_get_adapter_connects(self, manager):
        manager.add_account("test", "fake")
        adapter = await manager.get_adapter("test")
        assert adapter is not None
        assert "test" in manager.adapters

    @pytest.mark.asyncio
    async def test_get_adapter_unknown_account(self, manager):
        adapter = await manager.get_adapter("nonexistent")
        assert adapter is None
