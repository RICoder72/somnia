"""
Tests for tools.filesystem — fs_* tools via FastMCP registration.
"""

import pytest
from fastmcp import FastMCP
from tools.filesystem import register


@pytest.fixture
def fs_tools(tmp_data_root):
    """Register filesystem tools on a fresh FastMCP and return tool lookup."""
    mcp = FastMCP("test")
    register(mcp)
    tools = mcp._tool_manager._tools
    return tools


class TestFsList:
    def test_list_empty_dir(self, fs_tools, tmp_data_root):
        (tmp_data_root / "emptydir").mkdir()
        result = fs_tools["fs_list"].fn(path="emptydir")
        assert "(empty)" in result

    def test_list_with_files(self, fs_tools, tmp_data_root):
        (tmp_data_root / "stuff").mkdir()
        (tmp_data_root / "stuff" / "a.txt").write_text("aaa")
        result = fs_tools["fs_list"].fn(path="stuff")
        assert "a.txt" in result

    def test_list_nonexistent(self, fs_tools, tmp_data_root):
        result = fs_tools["fs_list"].fn(path="nope")
        assert "❌" in result


class TestFsRead:
    def test_read_file(self, fs_tools, tmp_data_root):
        (tmp_data_root / "readme.txt").write_text("hello world")
        result = fs_tools["fs_read"].fn(path="readme.txt")
        assert result == "hello world"

    def test_read_nonexistent(self, fs_tools, tmp_data_root):
        result = fs_tools["fs_read"].fn(path="missing.txt")
        assert "❌" in result


class TestFsWrite:
    def test_write_creates_file(self, fs_tools, tmp_data_root):
        result = fs_tools["fs_write"].fn(path="new.txt", content="data")
        assert "✅" in result
        assert (tmp_data_root / "new.txt").read_text() == "data"

    def test_write_creates_parents(self, fs_tools, tmp_data_root):
        result = fs_tools["fs_write"].fn(path="a/b/c.txt", content="nested")
        assert "✅" in result
        assert (tmp_data_root / "a" / "b" / "c.txt").read_text() == "nested"


class TestFsDelete:
    def test_delete_file(self, fs_tools, tmp_data_root):
        (tmp_data_root / "del.txt").write_text("bye")
        result = fs_tools["fs_delete"].fn(path="del.txt")
        assert "✅" in result
        assert not (tmp_data_root / "del.txt").exists()

    def test_delete_nonexistent(self, fs_tools, tmp_data_root):
        result = fs_tools["fs_delete"].fn(path="ghost.txt")
        assert "❌" in result


class TestFsMkdirRmdir:
    def test_mkdir_and_rmdir(self, fs_tools, tmp_data_root):
        result = fs_tools["fs_mkdir"].fn(path="newdir")
        assert "✅" in result
        assert (tmp_data_root / "newdir").is_dir()

        result = fs_tools["fs_rmdir"].fn(path="newdir")
        assert "✅" in result
        assert not (tmp_data_root / "newdir").exists()

    def test_rmdir_nonempty_fails(self, fs_tools, tmp_data_root):
        (tmp_data_root / "full").mkdir()
        (tmp_data_root / "full" / "file.txt").write_text("x")
        result = fs_tools["fs_rmdir"].fn(path="full")
        assert "not empty" in result.lower() or "❌" in result


class TestFsCopyMove:
    def test_copy_file(self, fs_tools, tmp_data_root):
        (tmp_data_root / "src.txt").write_text("copy me")
        result = fs_tools["fs_copy"].fn(source="src.txt", destination="dst.txt")
        assert "✅" in result
        assert (tmp_data_root / "dst.txt").read_text() == "copy me"
        assert (tmp_data_root / "src.txt").exists()  # original still there

    def test_move_file(self, fs_tools, tmp_data_root):
        (tmp_data_root / "orig.txt").write_text("move me")
        result = fs_tools["fs_move"].fn(source="orig.txt", destination="moved.txt")
        assert "✅" in result
        assert (tmp_data_root / "moved.txt").read_text() == "move me"
        assert not (tmp_data_root / "orig.txt").exists()
