import asyncio
import sys

import pytest


def test_process_computes_tables_and_plots(monkeypatch):
    from harness.runtime.process import ProcessRuntime
    monkeypatch.setenv('HARNESS_API_KEY', 'must-not-be-inherited')
    monkeypatch.setenv('HARNESS_MONGO_URI', 'must-not-be-inherited')
    monkeypatch.setenv('PYTHONPATH', '/must/not/be/inherited')
    monkeypatch.setenv('BASH_ENV', '/must/not/be/inherited')
    monkeypatch.setenv('SSH_AUTH_SOCK', '/must/not/be/inherited')
    result = asyncio.run(ProcessRuntime(sys.executable).execute(
        "import os; assert not {'HARNESS_API_KEY','HARNESS_MONGO_URI','PYTHONPATH','BASH_ENV','SSH_AUTH_SOCK'} & os.environ.keys()\n"
        "print(len(df)); emit_table(df.agg({'n':'sum'}).to_frame().T); emit_plot(px.bar(df, y='n'))",
        {'r1': [{'n': i} for i in range(120)]}))
    assert result['status'] == 'success'
    assert result['tables'][0]['rows'][0]['n'] == 7140
    assert '120' in result['stdout']
    assert '<html>' in result['plots'][0]


def test_legacy_process_namespace_keeps_scipy_and_statsmodels():
    from harness.runtime.process import ProcessRuntime
    result = asyncio.run(ProcessRuntime(sys.executable).execute(
        "assert scipy.__name__ == 'scipy'; assert sm.__name__ == 'statsmodels.api'; print('ok')", {}))
    assert result['status'] == 'success'
    assert result['stdout'].strip() == 'ok'


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


def test_runtime_configuration(monkeypatch, isolated_settings_env):
    from harness.config import Settings
    assert Settings().python_runtime == 'process'
    monkeypatch.setenv('HARNESS_PYTHON_EXECUTABLE', '/app/.venv/bin/python')
    assert Settings.from_env().python_executable == '/app/.venv/bin/python'
    monkeypatch.setenv('HARNESS_PYTHON_RUNTIME', 'invalid')
    with pytest.raises(ValueError):
        Settings.from_env()


def test_run_python_publishes_all_final_files_as_streams():
    from types import SimpleNamespace
    from harness.tools.python_tools import PythonInput, run_python

    ctx = SimpleNamespace(run={}, settings=SimpleNamespace(python_runtime="process", python_executable=sys.executable))
    code = """
(publish_directory / 'slides.pptx').write_bytes(b'PK\\x03\\x04')
(publish_directory / 'raw.unknownformat').write_bytes(b'x' * (13 * 1024 * 1024))
(publish_directory / 'drafts').mkdir()
(publish_directory / 'drafts' / 'note.txt').write_text('draft')
"""
    result = asyncio.run(run_python(PythonInput(code=code), ctx))
    assert result.status == "success", result.summary
    files = {item["title"]: item for item in result.artifacts}
    try:
        assert set(files) == {"slides.pptx", "raw.unknownformat"}
        assert all(item["generated_file"] for item in files.values())
        assert files["slides.pptx"]["mime"].endswith("presentationml.presentation")
        assert files["slides.pptx"]["data"].read() == b"PK\x03\x04"
        assert files["raw.unknownformat"]["mime"] == "application/octet-stream"
        assert files["raw.unknownformat"]["data"].read() == b"x" * (13 * 1024 * 1024)
    finally:
        for item in files.values():
            item["data"].close()


def test_worker_failure_discards_partial_files():
    from harness.runtime.process import ProcessRuntime

    result = asyncio.run(ProcessRuntime(sys.executable).execute(
        "(publish_directory / 'draft.pdf').write_bytes(b'x')\nraise ValueError('incomplete')", {}))
    assert result["status"] == "error"
    assert result["generated_files"] == []


@pytest.mark.parametrize("link", ["soft", "hard"])
def test_output_collector_rejects_linked_files(tmp_path, link):
    import os
    from harness.runtime.output_files import read_worker_output

    (tmp_path / "result.json").write_text('{"status":"success","tables":[]}')
    publish = tmp_path / "publish"
    publish.mkdir()
    source = tmp_path / "other"
    source.write_text("private")
    if link == "soft":
        (publish / "linked").symlink_to(source)
    else:
        os.link(source, publish / "linked")
    with pytest.raises((ValueError, OSError)):
        read_worker_output(tmp_path)


@pytest.mark.parametrize("record", [b'["../other",0]\n\n', b'["short.bin",3]\nab', b'["draft",0]\n["draft",0]\n\n'])
def test_isolated_transfer_rejects_invalid_file_records(record):
    import io
    from harness.runtime.output_files import read_transfer

    with pytest.raises(ValueError):
        read_transfer(io.BytesIO(b'{"status":"success","tables":[]}\n' + record))
