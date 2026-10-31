"""PR-7 smoke lifespan/registry guard, hosted by the existing PR8 fixture.

Only executed by the explicitly opted-in real-model setup. No secrets are read
or copied here; harness Settings loads its own provider configuration.
"""
import asyncio
from contextlib import asynccontextmanager, redirect_stdout
import json
import os
from pathlib import Path
import signal
import sys
from urllib.parse import urlsplit


def make_real_app(uri, database):
    if os.environ.get('PR8_REAL_MODEL') != '1':
        raise RuntimeError('PR8_REAL_MODEL opt-in required')
    # Never connect this destructive temporary-DB fixture to a remote Mongo host.
    target = urlsplit(uri)
    if target.scheme != 'mongodb' or target.hostname not in ('localhost', '127.0.0.1', '::1') or ',' in target.netloc:
        raise RuntimeError('Real-model fixture requires localhost Mongo')
    os.environ['HARNESS_MONGO_DB'] = database
    os.environ['HARNESS_ENGINE'] = 'hermes'
    os.environ.pop('HARNESS_NATIVE_PROMPTS', None)
    from pr8_gateway_server import make_app, GatewayServices, HarnessStore
    from harness.app import app as harness_app
    fixture = make_app(uri, database)

    @asynccontextmanager
    async def lifespan(app):
        cleanup_store = HarnessStore(uri=uri, database=database)
        workers = []
        subprocess_exec = asyncio.create_subprocess_exec

        async def tracked_exec(*args, **kwargs):
            native = any(str(arg) == str(Path(__file__).resolve().parents[2] / 'harness/native_worker.py') for arg in args)
            if native:
                # Native stdout is IPC. Discard diagnostic stderr before it can
                # reach the native worker log file or the test runner.
                kwargs['stderr'] = asyncio.subprocess.DEVNULL
            process = await subprocess_exec(*args, **kwargs)
            if native:
                workers.append(process)
                print(json.dumps({'worker': process.pid}), file=sys.__stdout__, flush=True)
            return process

        asyncio.create_subprocess_exec = tracked_exec
        try:
            # Reuse the PR-7 real controller lifecycle, including close/recovery.
            async with harness_app.router.lifespan_context(harness_app):
                ctrl = harness_app.state.harness
                base = ctrl.registry_factory
                ctrl.blocked, ctrl.launches = [], []

                def guarded():
                    reg = base()
                    for spec in reg.tools.values():
                        if spec.name == 'read_result':
                            continue
                        def make(name):
                            async def handler(args, context):
                                ctrl.blocked.append(name)
                                raise RuntimeError('tool blocked by smoke guard')
                            return handler
                        spec.handler = make(spec.name)
                    return reg

                ctrl.registry_factory = guarded
                execute = ctrl._run

                async def one_execution(run):
                    # Count acquired executions, not launch() calls deduplicated
                    # by the real controller. Fail closed before a second model run.
                    ctrl.launches.append(run['run_id'])
                    if len(ctrl.launches) != 1:
                        raise RuntimeError('Real-model execution budget exceeded')
                    return await execute(run)

                ctrl._run = one_execution
                app.state.store, app.state.controller = ctrl.store, ctrl
                app.state.gateway = GatewayServices(ctrl.store, ctrl, poll_interval=0.1)
                app.state.sockets, app.state.tickets = set(), set()
                app.state.origin = os.environ['PR8_FIXTURE_ORIGIN']
                yield
        finally:
            asyncio.create_subprocess_exec = subprocess_exec
            try:
                for worker in workers:
                    if worker.returncode is None:
                        try:
                            os.killpg(worker.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        await worker.wait()
            finally:
                try:
                    await cleanup_store.client.drop_database(database)
                finally:
                    cleanup_store.client.close()

    fixture.app.router.lifespan_context = lifespan
    return fixture


if __name__ == '__main__':
    if os.environ.get('PR8_REAL_MODEL') != '1':
        raise SystemExit('PR8_REAL_MODEL opt-in required')
    # Only explicit ready/worker control records use __stdout__. All other
    # Python output goes to stderr, discarded by global setup; no raw log file.
    with redirect_stdout(sys.stderr):
        try:
            from pr8_gateway_server import main
            asyncio.run(main(make_real_app))
        except BaseException:
            print('PR8 real-model fixture failed (diagnostics suppressed)', file=sys.stderr)
            raise SystemExit(1) from None
