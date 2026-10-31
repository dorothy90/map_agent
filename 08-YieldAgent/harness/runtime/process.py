"""Execute analysis locally, or through the required yieldrun SSH boundary."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from ..isolation.backend import IsolatedSSHBackend


class ProcessRuntime:
    def __init__(self, executable=None, *, isolation=None, run_id=None, principal_id=None):
        self.executable = executable or sys.executable
        self.isolated = (IsolatedSSHBackend(isolation, run_id, principal_id)
                         if isolation is not None else None)

    async def execute(self, code, datasets, timeout=60):
        if self.isolated is not None:
            return await self.isolated.execute_python(code, datasets, timeout)
        with tempfile.TemporaryDirectory(prefix="yield-harness-") as root:
            directory = Path(root)
            inputs, outputs = directory / "input", directory / "output"
            inputs.mkdir(); outputs.mkdir()
            (inputs / "code.py").write_text(code, encoding="utf-8")
            (inputs / "data.json").write_text(json.dumps(datasets, ensure_ascii=False, default=str, allow_nan=False), encoding="utf-8")
            # Do not inherit API keys, DB credentials, or Python startup overrides.
            env = {"PATH": os.defpath, "HOME": root, "TMPDIR": root,
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
            command = [self.executable, "-I", "-B", str(Path(__file__).with_name("worker.py")),
                "--input-dir", str(inputs), "--output-dir", str(outputs)]
            try:
                process = await asyncio.create_subprocess_exec(*command, cwd=root, env=env,
                    start_new_session=True, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            except OSError as exc:
                raise ValueError(f"Python runtime unavailable: {self.executable}") from exc
            try:
                await asyncio.wait_for(process.wait(), timeout)
                result_path = outputs / "result.json"
                if process.returncode != 0 or not result_path.is_file() or result_path.is_symlink():
                    return {"status": "error", "tables": [], "plots": [], "stdout": "", "stderr": "",
                        "error": {"type": "runtime_lost", "message": "Python worker failed; check interpreter and analysis dependencies"}}
                if result_path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("Python output exceeds limit")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("status") not in ("success", "error") or not isinstance(result.get("tables"), list):
                    raise ValueError("Invalid Python result protocol")
                return result
            except TimeoutError:
                return {"status": "timeout", "tables": [], "plots": [], "stdout": "", "stderr": "",
                    "error": {"type": "execution_timeout"}}
            finally:
                # Also terminate children left behind by a completed worker.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.shield(process.wait())
