"""Give stock Hermes file handlers a private read-only view of published yield files."""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

# Bound per-call temporary disk and inode use, including on cross-device NAS mounts.
MAX_COPY_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 10_000
SHARED_FILE_TOOLS = ('read_file', 'search_files', 'vision_analyze')


def _relative_path(root, raw):
    if not isinstance(root, Path) or not root.is_absolute() or root == root.parent:
        raise ValueError("The shared yield folder has not been configured.")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Specify a path in the shared yield folder.")
    path = Path(raw)
    if path.is_absolute():
        if not path.is_relative_to(root):
            raise ValueError("Use a path inside the shared yield folder.")
        path = path.relative_to(root)
    if '..' in path.parts or '.git' in path.parts or raw.startswith(('~', '//')) or '\\' in raw:
        raise ValueError("Use a path inside the shared yield folder.")
    return path


def _stage_file(directory, name, target, budget, *, search, names_only=False):
    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Only regular files in the shared yield folder can be read.")
    if names_only and name not in {'.ignore', '.rgignore', '.gitignore'}:
        # Filename searches need names and mtimes, even across mounts.
        target.touch()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        return budget
    if search:
        try:
            os.link(name, target, src_dir_fd=directory, follow_symlinks=False)
        except OSError as exc:
            if exc.errno not in {errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EMLINK}:
                raise
        else:
            # Validate the linked inode before making the private tree visible to Hermes.
            linked = target.lstat()
            if not stat.S_ISREG(linked.st_mode) or not os.path.samestat(info, linked):
                raise ValueError("The published file changed during search. Retry.")
            return budget
    if info.st_size > budget:
        raise ValueError("Shared-file staging limit exceeded. Choose a smaller file or search a narrower folder.")
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, 'rb') as source, target.open('xb') as dest:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(info, opened):
            raise ValueError("The published file changed during read. Retry.")
        # A publisher may append while a file is being copied; keep the limit effective.
        remaining = info.st_size
        while remaining:
            chunk = source.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            dest.write(chunk)
            remaining -= len(chunk)
        if source.read(1):
            raise ValueError("The published file changed during read. Retry.")
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    return budget - info.st_size


def _stage_tree(directory, root, target, names_only):
    from agent.file_safety import get_read_block_error
    from .isolation.seatbelt_local import FLAGS

    remaining, entries = MAX_COPY_BYTES, 0

    def stage_directory(fd, source, dest):
        nonlocal remaining, entries
        dest.mkdir()
        with os.scandir(fd) as children:
            for child in children:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError("Too many shared files to stage. Search a narrower folder.")
                name = child.name
                if name == '.git' or child.is_symlink() or get_read_block_error(str(source / name)):
                    continue
                if child.is_dir(follow_symlinks=False):
                    subdir = os.open(name, FLAGS, dir_fd=fd)
                    try:
                        stage_directory(subdir, source / name, dest / name)
                    finally:
                        os.close(subdir)
                elif child.is_file(follow_symlinks=False):
                    remaining = _stage_file(fd, name, dest / name, remaining,
                                            search=True, names_only=names_only)
        info = os.fstat(fd)
        os.utime(dest, ns=(info.st_atime_ns, info.st_mtime_ns))

    stage_directory(directory, root, target)


class _RunViews:
    """Stable paths for Hermes repeat guards; hold a view only while a call uses it."""
    def __init__(self):
        # The web worker's cwd is a private run directory cleaned by its parent on cancellation.
        self.temp = tempfile.TemporaryDirectory(prefix='yield-read-', dir=Path.cwd())
        self.users = {}
        self.lock = threading.Lock()

    @contextmanager
    def publish(self, built, relative, mode):
        key = hashlib.sha256(os.fsencode(str(relative)) + mode).hexdigest()
        visible = Path(self.temp.name) / key
        with self.lock:
            if visible not in self.users:
                built.rename(visible)
            self.users[visible] = self.users.get(visible, 0) + 1
        try:
            yield visible / relative, visible
        finally:
            with self.lock:
                self.users[visible] -= 1
                if not self.users[visible]:
                    del self.users[visible]
                    visible.rename(built.parent / 'retired')


@contextmanager
def _view(views, root: Path, raw: str, *, search: bool, names_only: bool = False):
    from agent.file_safety import get_read_block_error

    if os.name != 'posix':
        raise ValueError("Shared file access requires a Unix server.")
    from .isolation.seatbelt_local import FLAGS, open_directory

    relative = _relative_path(root, raw)
    if get_read_block_error(str(root / relative)):
        raise ValueError("This file is protected by Hermes.")
    fd = open_directory(root)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        name = relative.name or '.'
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        with tempfile.TemporaryDirectory(dir=views.temp.name) as tmp:
            base = Path(tmp).resolve() / 'shared'
            if base.is_relative_to(root):
                raise ValueError("Configure a dedicated shared folder outside the temporary staging area.")
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISDIR(info.st_mode) and search:
                folder = os.open(name, FLAGS, dir_fd=fd)
                try:
                    _stage_tree(folder, root / relative, target, names_only)
                finally:
                    os.close(folder)
            else:
                _stage_file(fd, name, target, MAX_COPY_BYTES, search=search, names_only=names_only)
            mode = b'\x00names' if names_only else (b'\x00search' if search else b'\x00read')
            with views.publish(base, relative, mode) as view:
                yield view
    finally:
        os.close(fd)


def _text_handler(views, root, original, search, *, display_root=""):
    from tools.registry import tool_error

    def scoped(args, **kwargs):
        path = args.get('path') or ('.' if search else '')
        try:
            names_only = search and args.get('target') in ('files', 'find')
            with _view(views, root, path, search=search, names_only=names_only) as (target, base):
                safe = dict(args, path=str(target))
                # Stock rg receives the expression as a positional argument. A leading
                # dash must remain regex data (including in the grep fallback).
                if search and safe.get('target', 'content') not in ('files', 'find'):
                    pattern = safe.get('pattern', '')
                    if isinstance(pattern, str) and pattern.startswith('-'):
                        safe['pattern'] = '[-]' + pattern[1:]
                result = original(safe, **kwargs)
                prefix = display_root + '/' if display_root else ''
                return result.replace(str(base) + '/', prefix).replace(str(base), display_root or '.')
        except (OSError, ValueError, RuntimeError) as exc:
            return tool_error(f"Shared yield folder: {exc}" if isinstance(exc, ValueError)
                              else "Shared yield folder or requested file is unavailable.")
    return scoped


def _image_handler(views, root, original):
    from tools.registry import tool_error
    from tools.vision_tools_image_prep import _detect_image_mime_type_from_bytes

    async def scoped(args, **kwargs):
        try:
            with _view(views, root, args.get('image_url', ''), search=False) as (target, base):
                # SVG renderers can resolve external file references. Only pass
                # self-contained raster images into the stock vision pipeline.
                if not _detect_image_mime_type_from_bytes(target.read_bytes()):
                    return tool_error("Choose a supported raster image inside the shared yield folder.")
                result = await original(dict(args, image_url=str(target)), **kwargs)
                if isinstance(result, dict) and 'image_url' in result.get('meta', {}):
                    result['meta']['image_url'] = str(target.relative_to(base))
                return result
        except (OSError, ValueError, RuntimeError):
            return tool_error("Choose a regular image file inside the shared yield folder.")
    return scoped


def install_shared_file_policy(registry, root: Path):
    views = _RunViews()
    for name in SHARED_FILE_TOOLS:
        entry = registry.get_entry(name)
        field = 'image_url' if name == 'vision_analyze' else 'path'
        schema = deepcopy(entry.schema)
        hint = ("Path of a published raster image in the shared yield folder."
                if name == 'vision_analyze' else "Path inside the shared yield folder.")
        schema['parameters']['properties'][field]['description'] = (
            hint + " Use a relative path; '.' is the shared folder root.")
        handler = (_image_handler(views, root, entry.handler) if name == 'vision_analyze'
                   else _text_handler(views, root, entry.handler, name == 'search_files'))
        if not registry.restore_registration(name, entry, replace(entry, handler=handler, schema=schema)):
            raise RuntimeError(f"Could not apply the shared-file policy to {name}")
