"""Collect completed worker output. Only flat, regular files may cross the boundary."""
from contextlib import ExitStack, contextmanager
import json
import mimetypes
import os
from pathlib import PureWindowsPath
import shutil
import stat
import sys
import tempfile


METADATA_LIMIT = 20 * 1024 * 1024


def _validate_name(name):
    if (not isinstance(name, str) or name in ("", ".", "..") or not name.isprintable()
            or PureWindowsPath(name).name != name or len(name.encode("utf-8")) > 255):
        raise ValueError("Invalid published filename")


def _open_file(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise ValueError("Published output must be a regular file with a single link")
    return os.fdopen(fd, "rb")


def _parse_result(data):
    if len(data) > METADATA_LIMIT:
        raise ValueError("Python output exceeds limit")
    result = json.loads(data)
    if (not isinstance(result, dict) or result.get("status") not in ("success", "error")
            or not isinstance(result.get("tables"), list)):
        raise ValueError("Invalid Python result protocol")
    # Only the parent can attach file streams to the parsed worker result.
    result.pop("generated_files", None)
    return result


def _artifact(name, stream):
    return {"title": name, "mime": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "generated_file": True, "data": stream}


@contextmanager
def _worker_outputs(directory):
    with ExitStack() as stack:
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("Collecting local worker files requires POSIX directory descriptors")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root = os.open(directory, flags)
        stack.callback(os.close, root)
        with _open_file(root, "result.json") as source:
            result = _parse_result(source.read(METADATA_LIMIT + 1))
        publish = None
        if result["status"] == "success":
            try:
                publish = os.open("publish", flags, dir_fd=root)
            except FileNotFoundError:
                pass  # Workers from older images only produce JSON.
            else:
                stack.callback(os.close, publish)
        yield result, publish


def _names(directory):
    if directory is not None:
        for name in sorted(os.listdir(directory)):
            _validate_name(name)
            if not stat.S_ISDIR(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
                yield name


def read_worker_output(directory):
    with _worker_outputs(directory) as (result, publish), ExitStack() as pending:
        files = []
        for name in _names(publish):
            stream = pending.enter_context(_open_file(publish, name))
            files.append(_artifact(name, stream))
        result["generated_files"] = files
        pending.pop_all()
        return result


def write_transfer(directory, output):
    """Send JSON metadata, followed by size-delimited binary files and a blank terminator."""
    with _worker_outputs(directory) as (result, publish):
        output.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        for name in _names(publish):
            with _open_file(publish, name) as source:
                size = os.fstat(source.fileno()).st_size
                output.write(json.dumps([name, size], ensure_ascii=False).encode() + b"\n")
                shutil.copyfileobj(source, output)
                if source.tell() != size:
                    raise ValueError("Published file changed during transfer")
        output.write(b"\n")


def read_transfer(source):
    result = _parse_result(source.readline(METADATA_LIMIT + 2).removesuffix(b"\n"))
    files = []
    names = set()
    with ExitStack() as pending:
        while True:
            header = source.readline(2048)
            if header == b"\n":
                break
            if not header.endswith(b"\n") or result["status"] != "success":
                raise ValueError("Invalid published file header")
            name, remaining = json.loads(header)
            _validate_name(name)
            if name in names or type(remaining) is not int or remaining < 0:
                raise ValueError("Invalid published file metadata")
            names.add(name)
            stream = pending.enter_context(tempfile.TemporaryFile())
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("Incomplete published file")
                stream.write(chunk)
                remaining -= len(chunk)
            stream.seek(0)
            files.append(_artifact(name, stream))
        if source.read(1):
            raise ValueError("Unexpected trailing Python output")
        result["generated_files"] = files
        pending.pop_all()
        return result


if __name__ == "__main__":
    write_transfer(sys.argv[1], sys.stdout.buffer)
