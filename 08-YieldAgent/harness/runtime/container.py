import asyncio
import json
import os
from pathlib import Path
import tempfile
import uuid


class ContainerRuntime:
    def __init__(self, image="yield-harness-python:2"):
        self.image = image

    async def execute(self, code, datasets, timeout=60):
        name = "yield-harness-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="yield-harness-") as root:
            directory = Path(root)
            inputs, outputs = directory / "input", directory / "output"
            inputs.mkdir(); outputs.mkdir()
            (inputs / "code.py").write_text(code)
            (inputs / "data.json").write_text(json.dumps(datasets, ensure_ascii=False, default=str, allow_nan=False))
            command = ["docker", "run", "--rm", "--pull=never", "--name", name, "--network=none", "--read-only",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=1", "--memory=1g", "--memory-swap=1g", "--pids-limit=64",
                "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--ulimit", "fsize=20971520:20971520", "--mount", f"type=bind,src={inputs},dst=/input,readonly",
                "--mount", f"type=bind,src={outputs},dst=/output", self.image]
            try:
                process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            except FileNotFoundError:
                raise ValueError("Docker runtime unavailable") from None
            try:
                await asyncio.wait_for(process.wait(), timeout)
                result_path = outputs / "result.json"
                if process.returncode != 0 or not result_path.is_file() or result_path.is_symlink():
                    return {"status": "error", "tables": [], "plots": [], "stdout": "", "stderr": "", "error": {"type": "runtime_lost", "message": "Container failed or configured image is unavailable"}}
                if result_path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("Python output exceeds limit")
                result = json.loads(result_path.read_text())
                if result.get("status") not in ("success", "error") or not isinstance(result.get("tables"), list):
                    raise ValueError("Invalid Python result protocol")
                return result
            except TimeoutError:
                return {"status": "timeout", "tables": [], "plots": [], "stdout": "", "stderr": "", "error": {"type": "execution_timeout"}}
            finally:
                cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.shield(cleanup.wait())
                await asyncio.shield(process.wait())
