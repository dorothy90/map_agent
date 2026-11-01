"""Opt-in PR9c real-model loopback fixture with PR9 development identities."""
import asyncio
from contextlib import asynccontextmanager, redirect_stdout
import json
import logging
import os
from pathlib import Path
import signal
import socket
import sys
from urllib.parse import urlsplit
import uuid


def resolve_allowed_tools(registry, raw):
    """Validate explicit read-only exceptions without invoking or mutating tools."""
    names = list(dict.fromkeys(name.strip() for name in raw.split(',') if name.strip()))
    for name in names:
        spec = registry.tools.get(name)
        if spec is None:
            raise ValueError('PR9_ALLOW_TOOLS contains an unknown tool')
        if spec.read_only is not True or spec.effect != 'read':
            raise ValueError('PR9_ALLOW_TOOLS requires read_only=True and effect=read')
    return names


def apply_allowed_tools(controller, raw, allowed):
    if raw is not None:
        controller.settings = controller.settings.model_copy(update={
            'native_allowed_tools': allowed,
        })


def make_real_app(uri, database):
    if os.environ.get('PR9_REAL_MODEL') != '1':
        raise SystemExit('PR9_REAL_MODEL opt-in required')
    target = urlsplit(uri)
    if target.scheme != 'mongodb' or target.hostname not in ('localhost', '127.0.0.1', '::1') or ',' in target.netloc:
        raise RuntimeError('Real-model fixture requires localhost Mongo')
    max_runs = int(os.environ.get('PR9_MAX_RUNS', '4'))
    if not 1 <= max_runs <= 10:
        raise ValueError('PR9_MAX_RUNS must be between 1 and 10')
    os.environ['HARNESS_MONGO_DB'] = database
    os.environ['HARNESS_ENGINE'] = 'hermes'
    os.environ.pop('HARNESS_NATIVE_PROMPTS', None)
    from pr9_dev_server import make_app, GatewayServices, HarnessStore
    from harness.tools.registry import domain_registry

    raw = os.environ.get('PR9_ALLOW_TOOLS')
    allowed = resolve_allowed_tools(
        domain_registry(), raw if raw is not None else ''
    )
    if allowed:
        print('PR9_ALLOW_TOOLS enabled (read-only tools may query configured external DBs): '
              + ','.join(allowed), file=sys.stderr, flush=True)

    from fastapi import HTTPException, Request
    from harness.app import app as harness_app

    def lifespan_factory(uri, database, seed):
        @asynccontextmanager
        async def lifespan(app):
            cleanup_store = HarnessStore(uri=uri, database=database)
            workers = []
            app.state.workers = workers
            ctrl = None
            subprocess_exec = asyncio.create_subprocess_exec

            async def tracked_exec(*args, **kwargs):
                native = any(str(arg) == str(Path(__file__).resolve().parents[2] / 'harness/native_worker.py') for arg in args)
                if native:
                    # stdout remains the native IPC channel.
                    kwargs['stderr'] = asyncio.subprocess.DEVNULL
                process = await subprocess_exec(*args, **kwargs)
                if native:
                    workers.append(process)
                    print(f'worker {process.pid}', file=sys.stderr, flush=True)
                return process

            asyncio.create_subprocess_exec = tracked_exec
            try:
                async with harness_app.router.lifespan_context(harness_app):
                    ctrl = harness_app.state.harness
                    apply_allowed_tools(ctrl, raw, allowed)
                    base = ctrl.registry_factory
                    ctrl.blocked, ctrl.launches = [], []

                    def guarded():
                        reg = base()
                        for spec in reg.tools.values():
                            if spec.name == 'read_result' or spec.name in allowed:
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

                    async def bounded_execution(run):
                        # Count acquired executions, including rejected attempts.
                        ctrl.launches.append(run['run_id'])
                        if len(ctrl.launches) > max_runs:
                            raise RuntimeError('Real-model execution budget exceeded')
                        return await execute(run)

                    ctrl._run = bounded_execution
                    app.state.harness = app.state.controller = ctrl
                    app.state.gateway = GatewayServices(ctrl.store, ctrl)
                    await seed(ctrl.store, app.state.gateway)
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
                        if ctrl is not None:
                            ctrl.store.client.close()
        return lifespan

    app = make_app(uri, database, lifespan_factory=lifespan_factory)

    @app.get('/__dev/state')
    async def state(request: Request):
        if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(status_code=403, detail='Loopback only')
        ctrl = request.app.state.controller
        return {'launches': ctrl.launches, 'blocked': ctrl.blocked, 'allowed': allowed,
                'workers': [worker.pid for worker in request.app.state.workers]}

    return app


async def main():
    if os.environ.get('PR9_REAL_MODEL') != '1':
        raise SystemExit('PR9_REAL_MODEL opt-in required')
    uri = os.environ.get('HARNESS_MONGO_URI')
    if not uri:
        raise SystemExit('HARNESS_MONGO_URI is required')
    import uvicorn
    database = 'zz_pr9r_' + uuid.uuid4().hex
    app = make_real_app(uri, database)
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', int(os.environ.get('PR9_PORT', '8001'))))
        sock.listen(128)
        # Uvicorn handles SIGINT/SIGTERM by shutting down through the lifespan.
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError('Development fixture startup failed')
                await asyncio.sleep(0.02)
            print(json.dumps({'port': sock.getsockname()[1], 'db': database, 'workers': []}),
                  file=sys.__stdout__, flush=True)
            await task
        finally:
            server.should_exit = True
            await task
    finally:
        sock.close()


if __name__ == '__main__':
    if os.environ.get('PR9_REAL_MODEL') != '1':
        raise SystemExit('PR9_REAL_MODEL opt-in required')
    logging.disable(logging.ERROR)
    with redirect_stdout(sys.stderr):
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
        except BaseException:
            print('PR9 real-model fixture failed (diagnostics suppressed)', file=sys.stderr)
            raise SystemExit(1) from None
