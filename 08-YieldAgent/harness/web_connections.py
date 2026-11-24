"""Employee-local MCP management. Hosted connector operations never enter this toolset."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from .types import Contract


class _Server(Contract):
    name: str = Field(min_length=1)
    mcp: Literal[True]
    config: dict | None = Field(default=None, description='Local MCP configuration: command/args/env or url/headers, and enabled.')


class _Manage(Contract):
    action: Literal['list', 'status', 'add', 'install', 'update', 'modify', 'remove', 'delete', 'enable', 'connect']
    connectors: list[_Server] = Field(default_factory=list)


def guard_mcp_profile(home=None):
    """Validate a profile before reading or writing local MCP configuration."""
    if home is None:
        from hermes_constants import get_hermes_home

        home = Path(os.environ['HERMES_HOME'])
        if get_hermes_home() != home:
            raise ValueError('Local MCP profile mismatch.')
    home = Path(home)
    if not home.is_absolute() or home.resolve() != home:
        raise ValueError('Local MCP profile mismatch.')
    config_path = home / 'config.yaml'
    if config_path.is_symlink() or (config_path.exists() and (
            not config_path.is_file() or config_path.stat().st_nlink != 1)):
        raise ValueError('Invalid local MCP profile configuration.')
    return home


def install_web_connections(registry):
    from hermes_cli import mcp_config
    from tools.registry import tool_error

    def error(message):
        return tool_error(message, local_mcp=True)

    def handle(args, **kwargs):
        # Check the explicit local marker before validation, including unsupported hosted actions.
        entries = args.get('connectors', [])
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) or entry.get('mcp') is not True for entry in entries
        ):
            return error('Hosted connectors are disabled. Use local MCP entries with name and mcp=true.')
        try:
            cmd = _Manage.model_validate(args)
        except ValidationError:
            return error('Invalid local MCP request. Use list/status, add/install, update/modify, remove/delete or enable/connect; OAuth is unavailable.')
        try:
            guard_mcp_profile()
            servers = mcp_config._get_mcp_servers()
            if cmd.action in ('list', 'status'):
                names = [entry.name for entry in cmd.connectors] if cmd.connectors else sorted(servers)
                if any(name not in servers for name in names):
                    return error('No such local MCP server in this employee profile.')
                return json.dumps(dict(ok=True, local_mcp=True, action=cmd.action, servers=[
                    dict(name=mcp_config.redact_mcp_probe_text(name), mcp=True,
                         enabled=mcp_config.mcp_server_enabled({
                             'enabled': servers[name].get('enabled', True)
                             if isinstance(servers[name].get('enabled', True), bool) else True})) for name in names]))
            if not cmd.connectors:
                return error('Supply at least one local MCP server.')
            results = []
            for entry in cmd.connectors:
                exists = entry.name in servers
                if cmd.action in ('add', 'install'):
                    if exists:
                        return error('Local MCP server already exists; use update.')
                    config = entry.config or {}
                else:
                    if not exists:
                        return error('No such local MCP server in this employee profile.')
                    config = {**servers[entry.name], **(entry.config or {})}
                if cmd.action in ('remove', 'delete'):
                    mcp_config._remove_mcp_server(entry.name)
                else:
                    if not (isinstance(config.get('command'), str) and config['command'].strip()
                            or isinstance(config.get('url'), str) and config['url'].strip()):
                        return error('Local MCP configuration requires a command or URL.')
                    if 'enabled' in config and not isinstance(config['enabled'], bool):
                        return error('Local MCP enabled must be a boolean.')
                    if config.get('auth') == 'oauth' or config.get('oauth'):
                        return error('OAuth authorization is unavailable in employee chat.')
                    # Engine validation can print rejected command text; never send credentials to logs.
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        if not mcp_config._validate_or_warn(entry.name, config):
                            return error('Local MCP configuration rejected as suspicious.')
                        count = None
                        if cmd.action in ('enable', 'connect'):
                            config['enabled'] = True
                            count = len(mcp_config._probe_single_server(entry.name, config))
                        if not mcp_config._save_mcp_server(entry.name, config):
                            return error('Local MCP configuration rejected as suspicious.')
                    servers[entry.name] = config
                results.append(dict(name=mcp_config.redact_mcp_probe_text(entry.name), mcp=True,
                                    status='removed' if cmd.action in ('remove', 'delete') else 'saved'))
                if cmd.action in ('enable', 'connect'):
                    results[-1].update(status='connected', tool_count=count)
            return json.dumps(dict(ok=True, local_mcp=True, action=cmd.action, servers=results,
                                   note='Configuration saved in this employee profile. Tool changes take effect on the next normal chat turn, not mid-turn. Connect verifies server availability.'))
        except Exception:
            # Exception strings may contain opaque env/header credentials supplied by a server.
            return error('Local MCP operation failed. Check the server configuration and availability.')

    registry.register(name='manage_connections', toolset='web_connections', override=True,
                      schema=dict(name='manage_connections', description=(
                          'Manage only this employee’s local MCP servers. Hosted connectors and OAuth are disabled. '
                          'Use connectors entries with name, mcp=true, and config for add/install or update/modify. '
                          'Install registers explicit configuration; it does not use the hosted catalog. '
                          'Connect/enable saves enabled=true and probes a configured server. Tool changes take effect on the next normal chat turn, not mid-turn.'),
                          parameters=_Manage.model_json_schema()), handler=handle, emoji='🔌')

    # Hermes dispatches this name inline before consulting the registry. Bind the same
    # local-only handler in this employee worker; never enter the hosted connector adapter.
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
    INLINE_TOOL_EXECUTORS['manage_connections'] = lambda agent, args, ctx: handle(args)
