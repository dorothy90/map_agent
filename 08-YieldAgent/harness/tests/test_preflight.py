import asyncio
import sys
from types import SimpleNamespace

from harness.config import Settings
from harness.evals import preflight


def test_preflight_uses_configured_isolation(monkeypatch, isolated_settings_env):
    settings = Settings.from_env()
    monkeypatch.setattr(preflight.Settings, 'from_env', lambda: settings)
    monkeypatch.setattr(preflight, 'load_dotenv', lambda: None)

    async def ping(*args):
        return {'ok': 1}

    client = SimpleNamespace(admin=SimpleNamespace(command=ping), close=lambda: None)
    monkeypatch.setattr(preflight, 'HarnessStore', lambda *args: SimpleNamespace(client=client))

    class Model:
        def bind_tools(self, *args, **kwargs):
            return self

        async def ainvoke(self, *args):
            return SimpleNamespace(tool_calls=[{'name': 'check_connection'}])

    monkeypatch.setattr(preflight, 'build_model', lambda *args: Model())

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return self

        def execute(self, *args):
            pass

        def fetchone(self):
            return (1,)

    monkeypatch.setitem(sys.modules, 'oracledb', SimpleNamespace(connect=lambda **kwargs: Connection()))
    search = SimpleNamespace(indices=SimpleNamespace(exists=lambda **kwargs: True))
    monkeypatch.setitem(sys.modules, 'fail_history_tools', SimpleNamespace(
        _get_opensearch_client=lambda: search, _OPENSEARCH_INDEX='fixture'))

    calls = []

    class Runtime:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def execute(self, *args):
            return {'status': 'success'}

    monkeypatch.setattr(preflight, 'ProcessRuntime', Runtime)
    results = asyncio.run(preflight.check())

    assert results['python']['ok'] is True
    assert results['python']['runtime'] == 'isolated'
    assert calls[0]['isolation'] is settings.isolation
    assert calls[0]['principal_id'] == settings.principal
    assert calls[0]['run_id'].startswith('preflight-')
