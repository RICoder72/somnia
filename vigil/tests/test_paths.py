"""
Tests for core.paths — sandbox enforcement.
"""

import pytest
from core.paths import validate


class TestValidate:
    def test_relative_path_resolved_under_data_root(self, tmp_data_root):
        (tmp_data_root / "hello.txt").write_text("hi")
        result = validate("hello.txt")
        assert result == tmp_data_root / "hello.txt"

    def test_nested_relative_path(self, tmp_data_root):
        (tmp_data_root / "sub").mkdir()
        (tmp_data_root / "sub" / "file.txt").write_text("x")
        result = validate("sub/file.txt")
        assert result == tmp_data_root / "sub" / "file.txt"

    def test_absolute_path_inside_sandbox(self, tmp_data_root):
        target = tmp_data_root / "abs.txt"
        target.write_text("data")
        result = validate(str(target))
        assert result == target

    def test_traversal_with_dotdot_rejected(self, tmp_data_root):
        with pytest.raises(ValueError, match="outside sandbox"):
            validate("../../../etc/passwd")

    def test_absolute_path_outside_sandbox_rejected(self, tmp_data_root):
        with pytest.raises(ValueError, match="outside sandbox"):
            validate("/etc/passwd")

    def test_dot_resolves_to_data_root(self, tmp_data_root):
        result = validate(".")
        assert result == tmp_data_root

    def test_path_with_embedded_dotdot_rejected(self, tmp_data_root):
        with pytest.raises(ValueError, match="outside sandbox"):
            validate("subdir/../../../../../../etc/shadow")
