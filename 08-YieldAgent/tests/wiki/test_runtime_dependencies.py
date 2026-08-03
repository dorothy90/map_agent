from pathlib import Path

import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.no_server


def test_opensearch_client_is_declared_for_uv_and_requirements_installs():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    requirement_lines = {
        line.strip().lower()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert any(item.lower().startswith("opensearch-py") for item in dependencies)
    assert any(item.startswith("opensearch-py") for item in requirement_lines)
