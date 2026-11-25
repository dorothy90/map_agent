"""Scope and staging contracts also exercised from non-Windows release workstations."""
import asyncio
from pathlib import Path
import tempfile

import pytest

from harness.config import WindowsIsolationSettings
from harness.isolation.backend import IsolationError, isolation_backend
from harness.isolation import windows_launcher


@pytest.fixture
def config(tmp_path, monkeypatch):
    config = WindowsIsolationSettings.model_construct(home=tmp_path, work_root=tmp_path / "work",
                                                      analysis_venv=tmp_path / "analysis")
    monkeypatch.setattr(windows_launcher, "ensure_private_root", lambda p: p.mkdir(exist_ok=True))
    return config


def test_query_export_survives_turn_but_changes_directory_between_chats(config):
    one = isolation_backend(config, "first-turn", "employee", "first-chat")
    next_turn = isolation_backend(config, "later-turn", "employee", "first-chat")
    second = isolation_backend(config, "other-turn", "employee", "second-chat")
    other_employee = isolation_backend(config, "first-turn", "other", "first-chat")
    data = "LOT,wafer\n4SA,8\n".encode()
    # Query and read_result pass a full seekable spool which can initially be at EOF.
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.flush()
        path = Path(asyncio.run(one.upload_file(source)))
    assert path.parent == next_turn.results_dir
    assert len({one.results_dir, second.results_dir, other_employee.results_dir}) == 3
    assert path.read_bytes() == data
    assert list(path.parent.iterdir()) == [path]
    assert next_turn.workdir != one.workdir
    with pytest.raises(IsolationError, match="chat"):
        isolation_backend(config, "turn", "employee")


def test_kernel_and_collector_get_trusted_scripts_and_private_inputs(config, monkeypatch):
    backend = isolation_backend(config, "kernel", "employee", "한글")
    invocation = {}
    def record(**options):
        invocation.update(options)
        return ["launcher"]
    monkeypatch.setattr(windows_launcher, "command", record)
    argv, _ = backend.python_program("print('ok')", [backend.workdir / "cell-1"], name="collect.py")
    assert argv == ["launcher"]
    assert invocation["scope"] == backend.scope
    assert invocation["cwd"] == backend.workdir
    assert invocation["write_dirs"] == (backend.workdir,)
    assert set(invocation["read_dirs"]) == {backend.results_dir, backend.programs}
    assert all(not p.is_relative_to(backend.workdir) for p in invocation["read_dirs"])
    assert invocation["argv"][-2:] == [str(backend.programs / "collect.py"), str(backend.workdir / "cell-1")]
    assert (backend.programs / "collect.py").read_text() == "print('ok')"
    with pytest.raises(IsolationError, match="changed"):
        backend.python_program("changed", name="collect.py")


def test_cancelled_csv_copy_does_not_publish_partial_input(config):
    import threading

    async def run(source):
        started = threading.Event()
        release = threading.Event()
        class StalledSource:
            def seek(self, offset):
                return source.seek(offset)

            def fileno(self):
                return source.fileno()

            def read(self, limit):
                started.set()
                assert release.wait(5)
                return source.read(limit)

        backend = isolation_backend(config, "query", "employee", "chat")
        task = asyncio.create_task(backend.upload_file(StalledSource()))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list(backend.results_dir.iterdir())

    with tempfile.TemporaryFile() as source:
        source.write(b"data")
        source.flush()
        asyncio.run(run(source))


def test_staged_programs_preserve_state_and_stream_excel(config, monkeypatch):
    """Exercise the actual runner/collector protocol; Windows OS policy has a separate acceptance check."""
    import sys
    from types import SimpleNamespace
    from openpyxl import load_workbook
    from harness.runtime.session_kernel import SessionKernels, CellInput

    def launch(*, cwd, argv, **kwargs):
        # Match the launcher's cwd/UTF-8 contract without simulating AppContainer.
        flags = ["-I", "-B", "-X", "utf8", "-u"]
        assert argv[:len(flags)] == flags
        # Run the staged script in this process: Windows execv starts a replacement
        # process, which would release a wait on the parent before output is ready.
        return [sys.executable, *flags, "-c",
                "import os,runpy,sys; os.chdir(sys.argv[1]); sys.argv=sys.argv[2:]; "
                "runpy.run_path(sys.argv[0],run_name='__main__')", str(cwd), *argv[len(flags):]]
    monkeypatch.setattr(windows_launcher, "command", launch)

    async def exercise():
        ctx = SimpleNamespace(run={"principal_id": "user", "session_id": "chat"},
                              settings=SimpleNamespace(isolation=config))
        backend = isolation_backend(config, "query", "user", "chat")
        path = await backend.upload_bytes(b"x,y,bin\n1,2,3\n")
        kernels = SessionKernels()
        try:
            first = await kernels.execute(CellInput(code=f"points=pd.read_csv({path!r})"), ctx)
            assert first.status == "success"
            second = await kernels.execute(CellInput(code=
                "points.to_excel(publish_directory / '웨이퍼 맵.xlsx', index=False)"), ctx)
            assert second.scope["execute_code"]["kernel"]["reused"]
            file, = second.artifacts
            assert file["title"] == "웨이퍼 맵.xlsx"
            with file["data"] as stream:
                book = load_workbook(stream)
                try:
                    assert list(book.active.values) == [("x","y","bin"), (1,2,3)]
                finally:
                    book.close()
        finally:
            await kernels.close()
    asyncio.run(exercise())
