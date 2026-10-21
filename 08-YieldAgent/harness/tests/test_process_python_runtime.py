import asyncio
import sys

import pytest


def test_process_computes_tables_and_plots(monkeypatch):
    from harness.runtime.process import ProcessRuntime
    monkeypatch.setenv('HARNESS_API_KEY', 'must-not-be-inherited')
    result = asyncio.run(ProcessRuntime(sys.executable).execute(
        "import os; assert 'HARNESS_API_KEY' not in os.environ\n"
        "print(len(df)); emit_table(df.agg({'n':'sum'}).to_frame().T); emit_plot(px.bar(df, y='n'))",
        {'r1': [{'n': i} for i in range(120)]}))
    assert result['status'] == 'success'
    assert result['tables'][0]['rows'][0]['n'] == 7140
    assert '120' in result['stdout']
    assert '<html>' in result['plots'][0]


def test_process_errors_and_timeout():
    from harness.runtime.process import ProcessRuntime
    runtime = ProcessRuntime(sys.executable)
    result = asyncio.run(runtime.execute("raise ValueError('expected')", {}))
    assert result['error']['type'] == 'ValueError'
    result = asyncio.run(runtime.execute('while True: pass', {}, timeout=2))
    assert result['status'] == 'timeout'


def test_process_cancellation():
    from harness.runtime.process import ProcessRuntime
    async def scenario():
        task = asyncio.create_task(ProcessRuntime(sys.executable).execute('while True: pass', {}))
        await asyncio.sleep(1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    asyncio.run(scenario())


def test_runtime_configuration(monkeypatch):
    from harness.config import Settings
    assert Settings().python_runtime == 'process'
    monkeypatch.setenv('HARNESS_PYTHON_EXECUTABLE', '/app/.venv/bin/python')
    assert Settings.from_env().python_executable == '/app/.venv/bin/python'
    monkeypatch.setenv('HARNESS_PYTHON_RUNTIME', 'invalid')
    with pytest.raises(ValueError):
        Settings.from_env()
