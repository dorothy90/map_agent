"""Offline paths for the harness integration tests."""
import os
from pathlib import Path
import sys


MAC_TEST_RESOURCES = Path(__file__).resolve().parents[3] / '.runtime/hermes/test-resources'


def hermes_checkout():
    configured = os.getenv('HERMES_CONTRACT_ROOT')
    if configured:
        return Path(configured)
    existing_default = Path('/tmp/hermes-agent-ro')
    cached = MAC_TEST_RESOURCES / 'upstream'
    if sys.platform == 'darwin' and (cached.exists() or not existing_default.exists()):
        return cached
    return existing_default
