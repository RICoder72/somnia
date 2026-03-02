"""
Tests for tools.context — domain context and instructions tools.
"""

import json
import pytest
import tools.context as ctx_module
from fastmcp import FastMCP
from tools.context import register


@pytest.fixture
def ctx_tools(tmp_data_root, mock_domain, monkeypatch):
    """Register context tools with patched DOMAINS_DIR."""
    mcp = FastMCP("test")
    domains_dir = tmp_data_root / "domains"
    triggers_file = tmp_data_root / "config" / "domain_triggers.json"

    # Write a triggers config
    triggers_file.write_text(json.dumps({
        "test-domain": {
            "description": "A test domain",
            "triggers": ["test", "testing"]
        }
    }))

    # Patch at module level — must persist through test execution
    monkeypatch.setattr(ctx_module, "DOMAINS_DIR", domains_dir)
    monkeypatch.setattr(ctx_module, "DOMAIN_TRIGGERS_FILE", triggers_file)

    register(mcp)
    return mcp._tool_manager._tools


class TestContextList:
    def test_lists_domains(self, ctx_tools):
        result = ctx_tools["context_list"].fn()
        assert "test-domain" in result
        assert "A test domain" in result

    def test_shows_triggers(self, ctx_tools):
        result = ctx_tools["context_list"].fn()
        assert "test" in result


class TestContextLoad:
    def test_load_domain(self, ctx_tools):
        result = ctx_tools["context_load"].fn(domain="test-domain")
        assert "Sample context content" in result
        assert "Loaded domain" in result

    def test_load_missing_domain(self, ctx_tools):
        result = ctx_tools["context_load"].fn(domain="nonexistent")
        assert "❌" in result


class TestContextGet:
    def test_get_context_file(self, ctx_tools):
        result = ctx_tools["context_get"].fn(domain="test-domain", file="notes.md")
        assert "Some context notes" in result

    def test_get_missing_file(self, ctx_tools):
        result = ctx_tools["context_get"].fn(domain="test-domain", file="nope.md")
        assert "❌" in result


class TestInstructions:
    def test_get_no_instructions(self, ctx_tools):
        result = ctx_tools["instructions_get"].fn(domain="test-domain")
        assert "No" in result or "instructions" in result.lower()

    def test_set_and_get_instructions(self, ctx_tools, mock_domain):
        ctx_tools["instructions_set"].fn(content="Do the thing", domain="test-domain")
        result = ctx_tools["instructions_get"].fn(domain="test-domain")
        assert "Do the thing" in result

    def test_global_instructions(self, ctx_tools, tmp_data_root, monkeypatch):
        import config
        monkeypatch.setattr(config, "DATA_ROOT", tmp_data_root)
        ctx_tools["instructions_set"].fn(content="Global rule", domain="")
        result = ctx_tools["instructions_get"].fn(domain="")
        assert "Global rule" in result
