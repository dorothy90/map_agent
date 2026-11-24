"""Load employee-local MCP tools once per normal chat turn."""
from contextlib import nullcontext
import logging
import os
from pathlib import Path

import yaml


logger = logging.getLogger(__name__)


def attach_employee_mcp_toolsets(web_toolsets, *, scheduled=False) -> list[str]:
    if scheduled:
        return []
    added = []
    try:
        from hermes_constants import get_hermes_home

        home = Path(os.environ['HERMES_HOME'])
        if not home.is_absolute() or home.absolute() != home.resolve() or get_hermes_home().resolve() != home:
            return []
        config_path = home / 'config.yaml'
        if config_path.is_symlink() or not config_path.exists() or config_path.stat().st_nlink != 1:
            return []
        # Read only this employee's file, not merged config or plugin portable servers.
        config = yaml.safe_load(config_path.read_text()) or {}
        from tools.mcp_tool_common import mcp_server_enabled
        from tools.mcp_tool_discovery import register_mcp_servers
        from tools.registry import registry

        servers = {name: cfg for name, cfg in config.get('mcp_servers', {}).items()
                   if mcp_server_enabled(cfg)}
        if not servers:
            return []
        try:
            from tools.mcp_oauth import suppress_interactive_oauth
        except ImportError:
            suppress_interactive_oauth = nullcontext
        with suppress_interactive_oauth():
            registered = register_mcp_servers(servers)
        registered_toolsets = {registry.get_toolset_for_tool(name) for name in registered}
        for name in servers:
            toolset = f'mcp-{name}'
            if toolset in registered_toolsets and toolset not in web_toolsets:
                web_toolsets.append(toolset)
                added.append(toolset)
    except Exception:
        # Server/config exception text can contain credentials; log no payloads.
        logger.warning('Employee MCP tools could not be loaded; continuing chat.')
    return added


def shutdown_employee_mcp_tools() -> None:
    try:
        from tools.mcp_tool_lifecycle import shutdown_mcp_servers
        shutdown_mcp_servers()
    except Exception:
        logger.warning('Employee MCP shutdown failed.')
