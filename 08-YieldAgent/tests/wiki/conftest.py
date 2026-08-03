import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def pytest_configure(config):
    config.addinivalue_line("markers", "no_server: test does not require agent_server")


@pytest.fixture(autouse=True)
def clear_wiki_modules():
    yield
    for name in (
        "wiki_config",
        "wiki_evidence_enrichment",
        "wiki_materializer",
        "wiki_router",
        "wiki_safe_mutation",
        "wiki_store",
    ):
        sys.modules.pop(name, None)
