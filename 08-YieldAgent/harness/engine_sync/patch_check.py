"""Split ordinary unified diffs and check them only in disposable /tmp copies."""
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from .compare import digest, excluded

HUNK = re.compile(rb'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def split_patch(patch):
    """Parse the engine's --- a/path / +++ b/path format, preserving raw bytes."""
    lines = patch.splitlines(keepends=True)
    files = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(b'--- a/') or i + 1 >= len(lines) or not lines[i + 1].startswith(b'+++ b/'):
            raise ValueError('Expected unified patch file headers')
        old = lines[i][6:].rstrip(b'\r\n').decode('utf-8')
        path = lines[i + 1][6:].rstrip(b'\r\n').decode('utf-8')
        if (path != old or not path or PurePosixPath(path).is_absolute()
                or '..' in PurePosixPath(path).parts or excluded(path)):
            raise ValueError('Unsupported or unsafe patch path')
        headers = b''.join(lines[i:i + 2])
        i += 2
        hunks = []
        while i < len(lines) and lines[i].startswith(b'@@ '):
            start = i
            match = HUNK.match(lines[i])
            if match is None:
                raise ValueError('Invalid hunk header')
            # Store only the structural range, not optional source text after @@.
            header = match[0].decode('ascii')
            old_left = int(match[2]) if match[2] is not None else 1
            new_left = int(match[4]) if match[4] is not None else 1
            i += 1
            while old_left or new_left:
                if i >= len(lines):
                    raise ValueError('Truncated hunk')
                prefix = lines[i][:1]
                if prefix == b' ':
                    old_left -= 1
                    new_left -= 1
                elif prefix == b'-':
                    old_left -= 1
                elif prefix == b'+':
                    new_left -= 1
                elif prefix != b'\\':
                    raise ValueError('Invalid hunk body')
                if old_left < 0 or new_left < 0:
                    raise ValueError('Invalid hunk counts')
                i += 1
            if i < len(lines) and lines[i].startswith(b'\\ No newline at end of file'):
                i += 1
            hunks.append({'header': header, 'bytes': b''.join(lines[start:i])})
        if not hunks:
            raise ValueError('Patch file has no hunks')
        files.append({'path': path, 'headers': headers, 'hunks': hunks})
    return files


def git_environment():
    # Prevent inherited repository/index settings from redirecting Git outside cwd.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, LC_ALL='C')
    return env


def _check(root, patch):
    result = subprocess.run(['git', 'apply', '--check', '-p1', '-'], cwd=root,
                            input=patch, capture_output=True, env=git_environment())
    # Without --verbose Git reports a location, not the source/context lines.
    first = result.stderr.decode('utf-8', errors='replace').splitlines()
    return {'applies': result.returncode == 0,
            'error': None if result.returncode == 0 else (first[0] if first else 'git apply --check failed')}


def check_patches(patch, hermes, pin_hashes):
    results = []
    for entry in split_patch(patch):
        path = entry['path']
        content = hermes.get(path)
        missing = content is None
        hh = None if missing else digest(content)
        pin = pin_hashes.get(path)
        row = {'path': path, 'missing_in_hermes': missing, 'hermes_sha256': hh,
               'upstream_pin_sha256': pin, 'hermes_matches_pin': hh == pin if hh and pin else None}
        if missing:
            row.update(applies=False, error='missing_in_hermes')
            row['hunks'] = [{'header': h['header'], 'applies': False, 'error': 'missing_in_hermes'}
                            for h in entry['hunks']]
        else:
            with tempfile.TemporaryDirectory(prefix='engine-sync-', dir='/tmp') as directory:
                root = Path(directory)
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                row.update(_check(root, entry['headers'] + b''.join(h['bytes'] for h in entry['hunks'])))
                row['hunks'] = [{'header': h['header'], **_check(root, entry['headers'] + h['bytes'])}
                                for h in entry['hunks']]
        results.append(row)
    return sorted(results, key=lambda r: r['path'])
