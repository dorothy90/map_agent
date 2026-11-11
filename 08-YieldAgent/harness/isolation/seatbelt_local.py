"""Run one authenticated local scope under mandatory macOS Seatbelt."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
HASH = re.compile(r'[0-9a-f]{64}\Z')


def file_read_probe(sentinel):
    """Probe data reads and inherited sandbox policy; only permission denial passes."""
    attempt = (
        f"try:\n with open({str(sentinel)!r}, 'rb') as f: f.read(1)\n"
        "except PermissionError:\n denied=True\n"
        "else:\n denied=False\n"
    )
    child = attempt + "raise SystemExit(0 if denied else 1)\n"
    return (
        "import subprocess,sys\n"
        + attempt.replace('denied=True', "denied['file_open']=True")
                 .replace('denied=False', "denied['file_open']=False")
        + f"child=subprocess.run([sys.executable,'-I','-B','-c',{child!r}],"
          "capture_output=True,timeout=5,check=False)\n"
        + "denied['child_file_open']=child.returncode == 0\n"
    )


def open_directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path != path.resolve(strict=True):
        raise ValueError('directory must be absolute and canonical')
    fd = os.open('/', FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_owned(fd):
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise PermissionError('directory must be owned and not group/world writable')


def private_child(parent, name):
    if not name or name in ('.', '..') or '/' in name:
        raise ValueError('invalid directory component')
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    child = os.open(name, FLAGS, dir_fd=parent)
    try:
        check_owned(child)
        os.fchmod(child, 0o700)
        return child
    except BaseException:
        os.close(child)
        raise


def parse_request(token):
    if len(token) > 1_048_576 or '\0' in token:
        raise ValueError('invalid scoped request')
    parts = token.split()
    if len(parts) != 2 or parts[0] != 'yieldrun-v1':
        raise ValueError('invalid scoped request')
    request = json.loads(base64.b64decode(parts[1] + '=' * (-len(parts[1]) % 4), altchars=b'-_', validate=True))
    if (not isinstance(request, dict) or set(request) != {'v', 'principal', 'run', 'op', 'command'}
            or request['v'] != 1 or request['op'] != 'command'
            or not isinstance(request['principal'], str) or not HASH.fullmatch(request['principal'])
            or not isinstance(request['run'], str) or not HASH.fullmatch(request['run'])
            or not isinstance(request['command'], str) or not request['command'] or '\0' in request['command']):
        raise ValueError('invalid scoped request')
    return request


def policy_for(home, work_root, venv, profile, workdir):
    read_roots = [Path(p) for p in ('/usr/lib', '/usr/bin', '/bin', '/System',
                                  '/Library/Developer/CommandLineTools', '/private/var/db/dyld')] + [venv]
    ancestors = set()
    for path in (profile, workdir, venv):
        ancestors.update(p for p in path.parents if str(p) != '/')
    literal = lambda p: '(literal ' + json.dumps(str(p)) + ')'
    subpath = lambda p: '(subpath ' + json.dumps(str(p)) + ')'
    return '\n'.join((
        '(version 1)', '(deny default)', '(deny network*)',
        '(allow process-fork process-exec process-info*)', '(allow signal (target self))',
        '(allow sysctl-read)',
        '(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))',
        '(allow file-read* ' + ' '.join(map(subpath, read_roots)) + ' '
        + ' '.join(map(literal, ('/', '/dev/random', '/dev/urandom'))) + ')',
        '(allow file-read-metadata ' + ' '.join(map(literal, sorted(ancestors))) + ')',
        '(allow file-read* file-write* ' + subpath(profile) + ' ' + subpath(workdir) + ')',
        '(allow file-read* file-write* (literal "/dev/null"))',
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--work-root', required=True, type=Path)
    parser.add_argument('--venv', required=True, type=Path)
    parser.add_argument('request')
    args = parser.parse_args()
    if sys.platform != 'darwin' or args.work_root.parent != args.home:
        raise ValueError('invalid local Seatbelt configuration')
    if args.venv == args.home or args.home in args.venv.parents:
        raise ValueError('analysis environment must be outside the sandbox home')
    request = parse_request(args.request)
    os.umask(0o077)
    descriptors = []
    try:
        for path in (args.home, args.work_root, args.venv):
            descriptor = open_directory(path)
            descriptors.append(descriptor)
            check_owned(descriptor)
        home_fd, work_fd, venv_fd = descriptors
        profiles_fd = private_child(work_fd, 'profiles'); descriptors.append(profiles_fd)
        runs_fd = private_child(work_fd, 'runs'); descriptors.append(runs_fd)
        profile_fd = private_child(profiles_fd, request['principal']); descriptors.append(profile_fd)
        run_fd = private_child(runs_fd, request['run']); descriptors.append(run_fd)
        for parent, name in ((profile_fd, 'tmp'), (profile_fd, 'cache'), (run_fd, 'tmp')):
            os.close(private_child(parent, name))
        results_fd = private_child(profile_fd, 'results'); descriptors.append(results_fd)
        profile = args.work_root / 'profiles' / request['principal']
        workdir = args.work_root / 'runs' / request['run']
        os.fchdir(run_fd)
        environment = {
            'HOME': str(profile), 'TMPDIR': str(workdir / 'tmp'),
            'XDG_CACHE_HOME': str(profile / 'cache'),
            'PATH': str(args.venv / 'bin') + ':/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
            'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'VIRTUAL_ENV': str(args.venv), 'YIELD_ISOLATED': '1',
            'YIELD_SANDBOX': 'sandbox-exec', 'YIELD_LAUNCHER': str(Path(__file__).resolve()),
            'YIELD_RESULTS_FD': str(results_fd),
        }
        for descriptor in descriptors[:-1]:
            os.close(descriptor)
        descriptors = [results_fd]
        result = subprocess.run(['/usr/bin/sandbox-exec', '-p',
            policy_for(args.home, args.work_root, args.venv, profile, workdir),
            '/bin/bash', '--noprofile', '--norc', '-c', request['command']],
            env=environment, pass_fds=(results_fd,))
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError):
        sys.stderr.write('local scoped executor failed; execution refused\n')
        raise SystemExit(1)
