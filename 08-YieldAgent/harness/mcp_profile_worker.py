"""Isolated Hermes MCP config/probe adapter; stdout is one JSON response."""
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().with_name('engine')))


def failure(status, detail):
    return {'error': status, 'detail': detail}


def run(action, payload):
    from harness.web_connections import guard_mcp_profile
    from hermes_cli.mcp_config import (
        _get_mcp_servers, _save_mcp_server, _remove_mcp_server, _validate_or_warn,
        _probe_single_server, redact_mcp_probe_text, mcp_server_enabled,
    )
    from hermes_cli.web_server_mcp import _mcp_server_summary, _redact_mcp_env
    from tools.mcp_tool_schema import mcp_prefixed_tool_name

    guard_mcp_profile()
    servers = _get_mcp_servers()
    if action == 'list':
        return {'servers': [_mcp_server_summary(name, cfg) for name, cfg in sorted(servers.items())]}
    name = payload.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        return failure(400, '서버 이름은 영문, 숫자, _, -로 1~64자 입력해주세요.')
    body = payload.get('body')
    existing = servers.get(name)
    if action == 'add' and existing is not None:
        return failure(409, '이미 등록된 서버 이름이에요.')
    if existing is None and action != 'add' and not (action == 'test' and body is not None):
        return failure(404, '서버를 찾을 수 없어요.')
    if action == 'delete':
        _remove_mcp_server(name)
        return {'ok': True}
    config = dict(existing or {})
    if action == 'enabled':
        config['enabled'] = body['enabled']
    elif body is not None:
        allowed = {'command', 'args', 'env', 'transport'} | ({'name', 'enabled'} if action == 'add' else set())
        if (not isinstance(body, dict) or body.keys() - allowed
                or body.get('transport', 'stdio') != 'stdio'
                or config.get('url') or config.get('transport', 'stdio') != 'stdio'):
            return failure(400, '로컬 명령(stdio) 서버만 추가하거나 편집할 수 있어요.')
        command, args, env = body.get('command'), body.get('args', []), body.get('env', {})
        if not isinstance(command, str) or not command.strip():
            return failure(400, '실행할 명령을 입력해주세요.')
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            return failure(400, '인자는 문자열 목록으로 입력해주세요.')
        if not isinstance(env, dict) or any(
                not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str)
                for key, value in env.items()):
            return failure(400, '환경 변수 이름과 문자열 값을 확인해주세요.')
        if action == 'add' and not isinstance(body.get('enabled', True), bool):
            return failure(400, '켜짐 설정은 참 또는 거짓이어야 해요.')
        previous_env = config.get('env') or {}
        redacted = _redact_mcp_env(previous_env)
        config.update(command=command.strip(), args=args, env={
            key: previous_env[key] if key in previous_env and value == redacted[key] else value
            for key, value in env.items()})
        if action == 'add':
            config['enabled'] = body.get('enabled', True)
        if not _validate_or_warn(name, config):
            return failure(400, '안전하지 않은 서버 설정이에요.')
    if action == 'test':
        start = time.monotonic()
        try:
            found = _probe_single_server(name, config)
            result = {'ok': True, 'tools': [
                {'name': mcp_prefixed_tool_name(name, tool), 'description': redact_mcp_probe_text(description)}
                for tool, description in found]}
        except Exception as exc:
            # Also remove opaque configured values which the generic redactor cannot recognize.
            message = str(exc)
            for value in (config.get('env') or {}).values():
                if value:
                    message = message.replace(str(value), '***')
            result = {'ok': False, 'error': redact_mcp_probe_text(message) or '연결 테스트에 실패했어요.', 'tools': []}
        return {**result, 'elapsed_ms': int((time.monotonic() - start) * 1000)}
    if not _save_mcp_server(name, config):
        return failure(400, '안전하지 않은 서버 설정이에요.')
    if action == 'enabled':
        return {'ok': True, 'name': name, 'enabled': mcp_server_enabled(config)}
    return _mcp_server_summary(name, config)


if __name__ == '__main__':
    # Redirect file descriptors as well as Python output, including child process logs.
    output = os.dup(1)
    with open(os.devnull, 'w') as quiet:
        os.dup2(quiet.fileno(), 1)
        os.dup2(quiet.fileno(), 2)
        try:
            result = run(sys.argv[1], json.loads(sys.argv[2]))
        except Exception:
            result = failure(503, 'MCP 프로필을 불러오지 못했어요.')
        sys.stdout.flush()
        sys.stderr.flush()
    with os.fdopen(output, 'w') as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + '\n')
