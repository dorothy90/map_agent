"""Adapter to the bundled Hermes engine using the backend Python interpreter."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import yaml

from .completion import render_results
from .executor import ToolExecutor

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_COMMIT = 'e473f5a9c976a0b5bc292aa415dae28c638a47c3'


def native_history(previous):
    from .context import session_context
    from langchain_core.messages import convert_to_openai_messages
    native_at = next((i for i, item in enumerate(previous) if item.get('native_messages')), None)
    if native_at is None:
        messages, _, _ = session_context(previous)
        return convert_to_openai_messages(messages)
    history = list(previous[native_at]['native_messages'])
    for item in reversed(previous[:native_at]):
        history.append({'role': 'user', 'content': item.get('goal_request', item['query'])})
        answer = item.get('answer_text', item.get('answer', ''))
        if answer:
            history.append({'role': 'assistant', 'content': answer})
    return history


class NativeBridge:
    def __init__(self, registry, context):
        self.registry = registry
        self.allowed = {name for name, spec in registry.tools.items() if spec.effect in ('read', 'artifact')}
        self.executor = ToolExecutor(registry, context)
        self.observations = []

    def schemas(self):
        return [tool for tool in self.registry.model_tools() if tool['function']['name'] in self.allowed]

    async def invoke(self, call):
        if call['name'] not in self.allowed:
            raise PermissionError('Tool is outside this run capability set')
        observation = await self.executor.execute(call)
        self.observations.append(observation.model_dump(mode='json'))
        return observation.model_view()


def result_status(result):
    if result.get('failed'):
        return 'failed'
    if result.get('partial') or result.get('interrupted'):
        return 'partial'
    if result.get('error'):
        return 'failed'
    if not result.get('completed'):
        return 'partial'
    return 'completed'


def prepare_profile(principal_id):
    profile = ROOT / '.runtime/hermes/profiles' / hashlib.sha256(principal_id.encode()).hexdigest()
    profile.mkdir(parents=True, exist_ok=True)
    for directory in Path(__file__).with_name('skills').iterdir():
        if directory.is_dir():
            target = profile / 'skills' / directory.name
            if not target.exists():
                shutil.copytree(directory, target)
    config = profile / 'config.yaml'
    content = yaml.safe_load(config.read_text()) if config.exists() else {'skills': {'auto_load': ['yield-analysis']}}
    # This worker has no host terminal tool; probing its package manager is irrelevant.
    if content.setdefault('agent', {}).get('environment_probe') is not False:
        content['agent']['environment_probe'] = False
        with tempfile.NamedTemporaryFile(mode='w', dir=profile, delete=False) as pending:
            pending.write(yaml.safe_dump(content, allow_unicode=True))
        os.replace(pending.name, config)
    return profile


async def run_native(context, registry, *, history=None):
    run, settings = context.run, context.settings
    prior_usage = run.get('usage', {})
    for kind, limit in (('models', settings.model_limit), ('tokens', settings.token_limit)):
        if prior_usage.get(kind, 0) >= limit:
            return {'status': 'partial', 'stop_reason': kind + '_limit',
                'answer': '실행 예산을 모두 사용하여 추가 모델 호출 없이 중지했습니다.',
                'observations': run.get('observations', []), 'result_ids': run.get('result_ids', [])}
    python = sys.executable
    bridge = NativeBridge(registry, context)
    profile = prepare_profile(run['principal_id'])
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}
    env.update(HERMES_HOME=str(profile), PYTHONUNBUFFERED='1', HERMES_DISABLE_LAZY_INSTALLS='1')
    workspace = tempfile.TemporaryDirectory(prefix="yield-hermes-")
    try:
        process = await asyncio.create_subprocess_exec(str(python), '-I', str(Path(__file__).with_name('native_worker.py')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=env, cwd=workspace.name, limit=16 * 1024 * 1024, start_new_session=True)
    except BaseException:
        workspace.cleanup()
        raise
    write_lock = asyncio.Lock()
    async def send(payload):
        async with write_lock:
            process.stdin.write((json.dumps(payload, ensure_ascii=False, default=str) + '\n').encode())
            await process.stdin.drain()
    startup = {'model': settings.model, 'base_url': settings.base_url,
        'api_key': settings.api_key.get_secret_value(), 'max_tokens': settings.max_output_tokens,
        'max_iterations': settings.model_limit - prior_usage.get('models', 0), 'run_budget_seconds': context.remaining_seconds(),
        'token_limit': max(0, settings.token_limit - prior_usage.get('tokens', 0)), 'session_id': run['session_id'],
        'query': run.get('goal_request', run['query']), 'instructions': Path(__file__).with_name('instructions').joinpath('native.md').read_text(),
        'tools': bridge.schemas(), 'history': history or [], 'upstream_commit': UPSTREAM_COMMIT}
    tasks = set()
    async def invoke(message):
        try:
            value = await bridge.invoke({'id': message['id'], 'name': message['name'], 'args': message['args']})
        except Exception as exc:
            from .runtime.domain_process import safe_error_message
            value = {'error': safe_error_message(exc), 'status': 'error'}
        await send({'id': message['id'], 'result': value})
    try:
        await send(startup)
        result = None
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=max(.1, context.remaining_seconds()))
            if not line:
                break
            message = json.loads(line)
            await context.check()
            if message['type'] == 'tool_call':
                task = asyncio.create_task(invoke(message))
                tasks.add(task)
            elif message['type'] == 'commentary':
                await context.store.event(run['run_id'], 'commentary', {'content': message['content']}, epoch=run['epoch'])
            elif message['type'] == 'step':
                await context.store.runs.update_one({'_id': run['run_id'], 'epoch': run['epoch'], 'status': 'running'},
                    {'$max': {'usage.tokens': prior_usage.get('tokens', 0) + message['tokens'],
                        'usage.models': prior_usage.get('models', 0) + message['models']}})
                await context.store.event(run['run_id'], 'progress', {'name': 'reasoning',
                    'message': 'Hermes 원본 엔진이 요청과 도구 결과를 확인하고 있습니다.'}, epoch=run['epoch'])
            elif message['type'] == 'result':
                result = message['result']
                break
            elif message['type'] == 'error':
                raise RuntimeError(message['message'])
        if result is None:
            raise RuntimeError('Native Hermes worker exited without a result')
        await context.store.runs.update_one({'_id': run['run_id'], 'epoch': run['epoch'], 'status': 'running'},
            {'$max': {'usage.tokens': prior_usage.get('tokens', 0) + (result.get('total_tokens') or 0),
                'usage.models': prior_usage.get('models', 0) + (result.get('api_calls') or 0)}})
        observations = bridge.observations
        answer = result.get('final_response') or 'Hermes 실행이 답변을 완성하지 못했습니다.'
        tables = render_results(observations, source_index={o['result_id']: o for o in observations})
        return {'status': result_status(result), 'stop_reason': result.get('turn_exit_reason') or 'hermes_returned',
            'answer_text': answer, 'answer': answer + ('\n\n' + tables if tables else ''),
            'observations': observations, 'result_ids': [o['result_id'] for o in observations],
            'native_messages': result.get('messages', []), 'native_usage': {k: result.get(k) for k in
                ('api_calls', 'input_tokens', 'output_tokens', 'total_tokens')},
            'native_engine': {'name': 'NousResearch/hermes-agent', 'commit': UPSTREAM_COMMIT}}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if process.returncode is None:
            import signal
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        workspace.cleanup()
