"""Gateway-owned Hermes kernels running inside the authenticated OS sandbox."""
import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import tempfile
import time
import uuid

from pydantic import Field

from ..engine.tools.code_kernel import (
    DEFAULT_KERNEL_IDLE_TIMEOUT, DEFAULT_MAX_SESSION_KERNELS, KERNEL_RUNNER_SOURCE,
)
from ..isolation.backend import isolation_backend
from ..tools.registry import ToolResult, ToolSpec
from ..types import Contract
from . import output_files


class CellInput(Contract):
    code: str = Field(min_length=1, max_length=100_000)
    reset: bool = False


@dataclass
class SessionKernel:
    backend: object
    marker: str = field(default_factory=lambda: uuid.uuid4().hex)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    process: object = None
    idle_timer: object = None
    active: asyncio.Task | None = None
    count: int = 0

    async def start(self):
        # Ship the upstream runner and helpers through the verified launcher. The kernel
        # never needs to read the application source tree.
        helpers = Path(__file__).with_name('worker.py').read_text(encoding='utf-8')
        bootstrap = (
            "import os\n"
            f"os.environ['HERMES_KERNEL_SENTINEL']={self.marker!r}\n"
            "runner = {'__name__': 'hermes_session_runner'}\n"
            f"exec({KERNEL_RUNNER_SOURCE!r}, runner)\n"
            "helpers = {'__name__': 'yield_analysis'}\n"
            f"exec({helpers!r}, helpers)\n"
            "runner['GLOBALS']['_yield_cell'] = helpers['run_session_cell']\n"
            "runner['main']()\n"
        )
        command, options = self.backend.python_program(bootstrap, name="kernel.py")
        self.process = await asyncio.create_subprocess_exec(
            *command, **options,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=2_100_000,
        )

    async def cell(self, source, cell_id):
        request = json.dumps({'id': cell_id, 'code': source}).encode() + b'\n'
        self.process.stdin.write(request)
        await self.process.stdin.drain()
        stream = self.process.stdout
        await stream.readuntil(('\n' + self.marker + ' ').encode())
        length = int(await stream.readline())
        if not 1 <= length <= 16_000_000:
            raise ValueError('Invalid Hermes kernel response length')
        result = json.loads(await stream.readexactly(length))
        if result.get('id') != cell_id:
            raise ValueError('Unexpected Hermes kernel response')
        return result

    async def collect(self, directory):
        source = Path(output_files.__file__).read_text(encoding='utf-8')
        command, options = self.backend.python_program(source, [directory], name="collect.py")
        with tempfile.TemporaryFile() as transfer:
            proc = await asyncio.create_subprocess_exec(*command, **options,
                stdout=transfer, stdin=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                if await proc.wait():
                    raise ValueError('Could not collect the files published by this cell')
                transfer.seek(0)
                return output_files.read_transfer(transfer)
            finally:
                if proc.returncode is None:
                    self.backend._terminate(proc)
                    await asyncio.shield(proc.wait())

    async def stop(self):
        if self.idle_timer is not None:
            self.idle_timer.cancel()
        if self.process is not None:
            self.process.stdin.close()
            if self.process.returncode is None:
                self.backend._terminate(self.process)
            await asyncio.shield(self.process.wait())
            self.process = None
            await asyncio.to_thread(self.backend.cleanup_workspace)


class SessionKernels:
    def __init__(self):
        self.sessions = {}
        self.admission = asyncio.Lock()

    def spec(self):
        return ToolSpec('execute_code', 'Run a Python cell in the session kernel.', CellInput,
                        self.execute, read_only=False, effect='artifact', timeout=300)

    async def close(self, owner=None):
        if owner is None:
            await asyncio.gather(*(self.close(key) for key in list(self.sessions)))
        elif kernel := self.sessions.pop(owner, None):
            if kernel.active and kernel.active is not asyncio.current_task():
                kernel.active.cancel()
                async with kernel.lock:
                    await kernel.stop()
            else:
                await kernel.stop()

    async def execute(self, args, ctx):
        started = time.monotonic()
        if ctx.settings.isolation is None:
            raise PermissionError('A configured OS sandbox is required for Python')
        owner = (ctx.run['principal_id'], ctx.run['session_id'])
        async with self.admission:
            kernel = self.sessions.get(owner)
            if kernel is None:
                if len(self.sessions) >= DEFAULT_MAX_SESSION_KERNELS:
                    idle = next((k for k, v in self.sessions.items() if not v.lock.locked()), None)
                    if idle is None:
                        raise ValueError('All Python kernels are busy. Retry after another cell finishes.')
                    await self.close(idle)
                backend = isolation_backend(ctx.settings.isolation, 'kernel-' + uuid.uuid4().hex, *owner)
                kernel = self.sessions.setdefault(owner, SessionKernel(backend))
        async with kernel.lock:
            if self.sessions.get(owner) is not kernel:
                raise ValueError('The Python session was closed. Retry the cell.')
            if kernel.idle_timer is not None:
                kernel.idle_timer.cancel()
            reused = kernel.process is not None and kernel.process.returncode is None
            reset = args.reset and reused
            kernel.active = asyncio.current_task()
            try:
                if reset:
                    await kernel.stop()
                if reset or not reused:
                    await kernel.start()
                    kernel.count = 0
                cell_id = uuid.uuid4().hex
                directory = kernel.backend.workdir / ('cell-' + cell_id)
                code = f"_yield_cell({args.code!r}, globals(), {str(directory)!r})"
                result = await kernel.cell(code, cell_id)
                kernel.count += 1
                success = result['status'] == 'ok'
                output = ''.join(str(result.get(k) or '') for k in ('stdout', 'stderr', 'traceback'))
                if len(output) > 50_000:
                    output = output[:20_000] + '\n[output truncated]\n' + output[-30_000:]
                payload = {'status': 'success' if success else 'error', 'output': output,
                           'exit_code': 0 if success else 1, 'tool_calls_made': 0,
                           'duration_seconds': round(time.monotonic() - started, 3),
                           'kernel': {'mode': 'session', 'execution_count': kernel.count,
                                      'reused': reused and not reset, 'state_reset': reset}}
                data = await kernel.collect(directory) if success else {}
                artifacts = [{'title': f'분석 차트 {i+1}', 'mime': 'text/html', 'data': plot}
                             for i, plot in enumerate(data.get('plots', []))]
                artifacts.extend(data.get('generated_files', []))
                try:
                    observation = ToolResult(status=payload['status'], summary=output[:1200] or payload['status'],
                                             scope={'execute_code': payload}, artifacts=artifacts,
                                             tables=data.get('tables', []))
                except BaseException:
                    for file in data.get('generated_files', []):
                        file['data'].close()
                    raise
                if result['status'] == 'exit' and self.sessions.get(owner) is kernel:
                    await self.close(owner)
                return observation
            except BaseException:
                if self.sessions.get(owner) is kernel:
                    await self.close(owner)
                raise
            finally:
                kernel.active = None
                if self.sessions.get(owner) is kernel:
                    loop = asyncio.get_running_loop()
                    kernel.idle_timer = loop.call_later(DEFAULT_KERNEL_IDLE_TIMEOUT,
                        lambda: asyncio.create_task(self.close(owner)))
