import asyncio
from types import SimpleNamespace

import pytest
import yaml

from harness.config import Settings


def test_timezone_env_alias(monkeypatch, isolated_settings_env):
    monkeypatch.setenv('HERMES_TIMEZONE', 'Europe/London')
    monkeypatch.delenv('HARNESS_TIMEZONE', raising=False)
    assert Settings.from_env().timezone == 'Europe/London'
    monkeypatch.setenv('HARNESS_TIMEZONE', 'Asia/Seoul')
    assert Settings.from_env().timezone == 'Asia/Seoul'


@pytest.mark.parametrize('key,value', [('external_dirs', ['/some/skill/directory']), ('create_dir', '/some/skill/directory'), ('inline_shell', True)])
def test_unsupported_profile_skill_settings_are_rejected(monkeypatch, tmp_path, key, value):
    import harness.native_hermes as native
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    profile = native.prepare_profile('alice')
    config_path = profile / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config.setdefault('skills', {})[key] = value
    config_path.write_text(yaml.safe_dump(config))
    from harness.hermes_chat import run_chat
    context = SimpleNamespace(settings=Settings(), store=object(),
                              run={'principal_id': 'alice', 'session_id': 'test', 'run_id': 'r', 'epoch': 1})
    with pytest.raises(ValueError, match='authenticated profile'):
        asyncio.run(run_chat(context))
