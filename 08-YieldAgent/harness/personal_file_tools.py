"""Map /workspace to this web employee's files, keeping the stock Hermes edit handlers."""
from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from .shared_file_tools import _RunViews, _text_handler

PERSONAL_FILE_TOOLS = ('write_file', 'patch')
WORKSPACE = Path('/workspace')


@contextmanager
def _workspace(profile, database, shared):
    if os.name != 'posix':
        raise ValueError('Personal workspace access requires a Unix server.')
    import fcntl
    from .isolation.seatbelt_local import open_directory, private_child

    parts = ('web-files', hashlib.sha256(database.encode()).hexdigest())
    root = profile.joinpath(*parts)
    if shared is not None and (root.is_relative_to(shared) or shared.is_relative_to(root)):
        raise ValueError('Configure separate shared and personal directories.')
    fd = open_directory(profile)
    try:
        for part in parts:
            child = private_child(fd, part)
            os.close(fd)
            fd = child
        # The directory itself is stable across workers and can't be edited as a file.
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield root
    finally:
        os.close(fd)


def _target(root, raw, *, directory=False):
    from agent.file_safety import get_read_block_error

    if not isinstance(raw, str):
        raise ValueError('Specify a file under /workspace/.')
    path = Path(raw)
    if (not path.is_relative_to(WORKSPACE) or '..' in path.parts or '.git' in path.parts
            or '\\' in raw or (path == WORKSPACE and not directory)):
        raise ValueError('Use /workspace/filename for personal files. Published files are read-only.')
    target = root
    for part in path.relative_to(WORKSPACE).parts:
        target = target / part
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if not (stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
            raise ValueError('Use regular files inside your personal workspace.')
    if get_read_block_error(str(target)):
        raise ValueError('This file is protected by Hermes.')
    return str(target)


def _map_patch(root, patch):
    from tools import file_tools
    from tools.patch_parser import parse_v4a_patch

    if not isinstance(patch, str):
        raise ValueError('Provide patch text.')
    operations, error = parse_v4a_patch(patch)
    if error or not operations:
        raise ValueError('Provide a valid file patch inside /workspace.')
    # Hermes also probes header paths for locks before it applies parsed operations.
    headers = file_tools._collect_v4a_header_paths(patch)
    if isinstance(headers, str):
        raise ValueError('Use paths inside /workspace for every patch operation.')

    def paths(ops):
        return [p for op in ops for p in (op.file_path, op.new_path) if p is not None]

    mapped = {p: _target(root, p) for p in set(headers[0]) | set(paths(operations))}
    translated = file_tools._rewrite_v4a_patch_paths_for_host(patch, mapped, mapped, None)
    rewritten, error = parse_v4a_patch(translated)
    if error or paths(rewritten) != [mapped[p] for p in paths(operations)]:
        raise ValueError('Could not resolve every patch path inside /workspace.')
    return translated


def _describe_scope(schema, hint):
    schema = deepcopy(schema)
    if 'description' in schema:
        schema['description'] += ' ' + hint
    if 'parameters' in schema:
        schema['parameters']['properties']['path']['description'] = hint
    return schema


def install_personal_file_policy(registry, profile: Path, database: str, shared: Path | None, *, enabled=True):
    from tools import file_tools
    from tools.delegate_tool_registry import get_subagent_attribution
    from tools.registry import tool_error

    views = _RunViews()
    def wrap(name, original):
        def scoped(args, **kwargs):
            raw = args.get('path')
            reading = name in ('read_file', 'search_files')
            if reading and not (isinstance(raw, str) and Path(raw).is_relative_to(WORKSPACE)):
                return original(args, **kwargs)
            if not enabled or get_subagent_attribution(kwargs.get('task_id')):
                return tool_error('Personal workspace access is unavailable for this task.')
            try:
                with _workspace(profile, database, shared) as root:
                    if name == 'search_files':
                        target = _target(root, raw, directory=True)
                        search = _text_handler(views, root, file_tools._handle_search_files, True,
                                               display_root=str(WORKSPACE))
                        return search(dict(args, path=target), **kwargs)
                    safe = dict(args, cross_profile=False)
                    if name == 'patch' and args.get('mode') == 'patch':
                        safe['patch'] = _map_patch(root, args.get('patch'))
                        safe.pop('path', None)
                    else:
                        safe['path'] = _target(root, raw)
                    handler = file_tools._handle_read_file if name == 'read_file' else original
                    return handler(safe, **kwargs).replace(str(root), str(WORKSPACE))
            except ValueError as exc:
                return tool_error(str(exc))
            except OSError:
                return tool_error('Personal workspace or requested file is unavailable.')
        return scoped

    for name in ('read_file', 'search_files', *PERSONAL_FILE_TOOLS):
        entry = registry.get_entry(name)
        hint = ('Relative paths read the published yield folder. Use /workspace for your personal files.'
                if name in ('read_file', 'search_files') else
                'Edit personal text files under /workspace/ only. Published yield files are read-only.')
        schema = _describe_scope(entry.schema, hint)
        dynamic = entry.dynamic_schema_overrides
        new = replace(entry, handler=wrap(name, entry.handler), description=schema['description'], schema=schema,
                      dynamic_schema_overrides=(lambda build=dynamic, h=hint: _describe_scope(build(), h))
                      if dynamic else None)
        if not registry.restore_registration(name, entry, new):
            raise RuntimeError(f'Could not register personal workspace for {name}')
