"""
Shared fixtures for Vigil tests.
"""

import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def tmp_data_root(tmp_path):
    """Patch DATA_ROOT in both config and core.paths to use a temp directory."""
    with patch("config.DATA_ROOT", tmp_path), \
         patch("core.paths.DATA_ROOT", tmp_path):
        # Create standard subdirectories
        (tmp_path / "config").mkdir()
        (tmp_path / "domains").mkdir()
        (tmp_path / "outputs").mkdir()
        yield tmp_path


@pytest.fixture
def mock_domain(tmp_data_root):
    """Create a sample domain directory for context tests."""
    domain_dir = tmp_data_root / "domains" / "test-domain"
    domain_dir.mkdir(parents=True)

    (domain_dir / "test-domain.md").write_text("# Test Domain\nSample context content.")

    context_dir = domain_dir / "context"
    context_dir.mkdir()
    (context_dir / "notes.md").write_text("Some context notes.")

    (domain_dir / "state.json").write_text('{"created": "2025-01-01"}')

    return domain_dir
