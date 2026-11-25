"""Launch a Python child in a capability-free Windows AppContainer; its Job dies with the supervisor."""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


def _scope_name(scope):
    if len(scope) != 64 or any(c not in '0123456789abcdef' for c in scope):
        raise ValueError("Expected a SHA-256 session scope")
    # Keep the entire digest within the AppContainer name length limit.
    return 'yield.' + base64.b32encode(bytes.fromhex(scope)).decode('ascii').rstrip('=').lower()


def command(*, scope, python, cwd, read_dirs, write_dirs, argv):
    """Build a trusted supervisor command. argv goes directly to the restricted interpreter."""
    _scope_name(scope)
    if not Path(python).is_absolute() or not Path(cwd).is_absolute() or Path(cwd) not in map(Path, write_dirs):
        raise ValueError("Windows analysis requires an absolute interpreter and a writable working directory")
    args = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), 'launch',
            '--scope', scope, '--parent', str(os.getpid()), '--python', str(python), '--cwd', str(cwd)]
    for flag, paths in (('--read', read_dirs), ('--write', write_dirs)):
        for path in paths:
            if not Path(path).is_absolute():
                raise ValueError("AppContainer directories must be absolute")
            args.extend((flag, str(path)))
    return [*args, '--', *map(str, argv)]


if os.name == 'nt':
    import msvcrt
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32file
    import win32job
    import win32security as security


@contextmanager
def _owned(handle):
    try:
        yield handle
    finally:
        handle.Close()


def _caller_sid():
    with _owned(security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)) as token:
        sid, _ = security.GetTokenInformation(token, security.TokenUser)
    return security.ConvertSidToStringSid(sid)


def _descriptor(sddl):
    return security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)


def _pinned_directory(stack, directory, access):
    """Hold ancestor entries against rename/removal and open reparse points themselves."""
    path = Path(directory)
    if not path.is_absolute() or '..' in path.parts or not path.drive or path.drive.startswith('\\\\'):
        raise ValueError('Windows isolation requires a local, absolute filesystem path')
    chain = [*reversed(path.parents), path]
    for part in chain:
        handle = win32file.CreateFile(str(part), access if part == path else 0x80,
                                      win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None,
                                      win32con.OPEN_EXISTING, win32file.FILE_FLAG_OPEN_REPARSE_POINT |
                                      win32con.FILE_FLAG_BACKUP_SEMANTICS, None)
        stack.callback(handle.Close)
        attrs = win32file.GetFileInformationByHandle(handle)[0]
        if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT or not attrs & stat.FILE_ATTRIBUTE_DIRECTORY:
            raise PermissionError(f'Unsafe Windows isolation directory: {part}')
    return handle


def ensure_private_root(root):
    """Atomically create, or verify, a dedicated root owned and accessible only by the user and SYSTEM."""
    if os.name != 'nt':
        raise OSError('Windows isolation requires Windows')
    root = Path(root)
    if root == root.parent:
        raise ValueError('Select a dedicated subdirectory for Windows isolation')
    user = _caller_sid()
    sddl = f'O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)'
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = _descriptor(sddl)
    with ExitStack() as stack:
        _pinned_directory(stack, root.parent, 0x80)
        try:
            win32file.CreateDirectory(str(root), attributes)
        except pywintypes.error as exc:
            if exc.winerror != 183:  # ERROR_ALREADY_EXISTS
                raise
        handle = _pinned_directory(stack, root, win32con.READ_CONTROL)
        sd = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT,
                                      security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        if (security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner()) != user or
                not sd.GetSecurityDescriptorControl()[0] & 0x1000 or dacl is None):
            raise PermissionError('Select a fresh private NTFS directory for Windows isolation')
        allowed = set()
        for index in range(dacl.GetAceCount()):
            (kind, flags), mask, sid = dacl.GetAce(index)
            identity = security.ConvertSidToStringSid(sid)
            if kind != 0 or flags != 3 or mask != 0x1f01ff or identity not in {user, 'S-1-5-18'}:
                raise PermissionError('Windows isolation root has an unexpected NTFS access rule')
            allowed.add(identity)
        if allowed != {user, 'S-1-5-18'}:
            raise PermissionError('Incomplete Windows isolation root access rules')


class Job:
    def __init__(self):
        # pywin32 311 requires a string here. The Win32 NULL name creates a private unnamed job.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = _function(kernel, 'CreateJobObjectW', w.HANDLE, w.LPVOID, w.LPCWSTR)
        self.handle = pywintypes.HANDLE(_check(create(None, None)))
        try:
            info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
            info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.handle.Close()


def attach_process_tree(pid):
    """For a trusted worker that has not yet accepted any requests."""
    job = Job()
    try:
        with _owned(win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)) as process:
            win32job.AssignProcessToJobObject(job.handle, process)
        return job
    except BaseException:
        job.close()
        raise


def _grant(directory, package_sid, writable):
    """Grant only this session on its staging directories. Never recurse into a worker-created tree."""
    sid = security.ConvertStringSidToSid(package_sid)
    # These are FILE_GENERIC_READ/EXECUTE, plus write and delete for scratch space.
    mask = 0x1200a9 if not writable else 0x1301bf
    rights = win32con.READ_CONTROL | win32con.WRITE_DAC | (win32con.WRITE_OWNER if writable else 0)
    with ExitStack() as stack:
        h = _pinned_directory(stack, directory, rights)
        sd = security.GetSecurityInfo(h, security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None:
            raise PermissionError('A private NTFS DACL is required')
        already = False
        for index in range(dacl.GetAceCount()):
            (kind, flags), access, who = dacl.GetAce(index)
            if security.ConvertSidToStringSid(who) == package_sid:
                already = kind == 0 and flags & 3 == 3 and access == mask
                if not already:
                    raise PermissionError('Unexpected rights on a session staging directory')
        if not already:
            # SetEntriesInAcl inserts the explicit ACE before inherited ACEs.
            dacl.SetEntriesInAcl([{'AccessPermissions': mask, 'AccessMode': security.GRANT_ACCESS,
                                   'Inheritance': 3, 'Trustee': {'TrusteeForm': security.TRUSTEE_IS_SID,
                                   'TrusteeType': security.TRUSTEE_IS_UNKNOWN, 'Identifier': sid}}])
            if writable:
                sd.SetSecurityDescriptorDacl(True, dacl, False)
                security.SetKernelObjectSecurity(h, security.DACL_SECURITY_INFORMATION, sd)
            else:
                # Existing inputs and trusted bootstrap files may predate this launch.
                security.SetSecurityInfo(h, security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION,
                                         None, None, dacl, None)
        if writable:
            low = _descriptor('S:(ML;OICI;NW;;;LW)')
            security.SetKernelObjectSecurity(h, 0x10, low)  # LABEL_SECURITY_INFORMATION


def _function(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


def _check(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return ok


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [('AppContainerSid', w.LPVOID), ('Capabilities', w.LPVOID),
                ('CapabilityCount', w.DWORD), ('Reserved', w.DWORD)]


class _Startup(ctypes.Structure):
    _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR),
                ('lpTitle', w.LPWSTR), ('dwX', w.DWORD), ('dwY', w.DWORD), ('dwXSize', w.DWORD),
                ('dwYSize', w.DWORD), ('dwXCountChars', w.DWORD), ('dwYCountChars', w.DWORD),
                ('dwFillAttribute', w.DWORD), ('dwFlags', w.DWORD), ('wShowWindow', w.WORD),
                ('cbReserved2', w.WORD), ('lpReserved2', w.LPVOID), ('hStdInput', w.HANDLE),
                ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE), ('lpAttributeList', w.LPVOID)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]


@contextmanager
def _container_sid(name):
    userenv = ctypes.WinDLL('userenv', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    create = _function(userenv, 'CreateAppContainerProfile', w.LONG, w.LPCWSTR, w.LPCWSTR,
                       w.LPCWSTR, w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID))
    derive = _function(userenv, 'DeriveAppContainerSidFromAppContainerName', w.LONG, w.LPCWSTR,
                       ctypes.POINTER(w.LPVOID))
    sid = w.LPVOID()
    status = create(name, name, 'Yield chat analysis', None, 0, ctypes.byref(sid))
    if (status & 0xffffffff) == 0x800700b7:
        status = derive(name, ctypes.byref(sid))
    if status < 0:
        raise OSError(f'AppContainer profile creation failed: HRESULT {status & 0xffffffff:#010x}')
    try:
        value = w.LPWSTR()
        convert = _function(advapi, 'ConvertSidToStringSidW', w.BOOL, w.LPVOID, ctypes.POINTER(w.LPWSTR))
        _check(convert(sid, ctypes.byref(value)))
        try:
            yield sid, value.value
        finally:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            _function(kernel, 'LocalFree', w.HANDLE, w.HANDLE)(ctypes.cast(value, w.HANDLE))
    finally:
        _function(advapi, 'FreeSid', w.LPVOID, w.LPVOID)(sid)


def _child_environment(cwd, python, windows, local_appdata):
    return {'SystemRoot': windows, 'WINDIR': windows,
            'PATH': os.pathsep.join((str(Path(python).parent), str(Path(windows) / 'System32'))),
            'TMP': str(cwd), 'TEMP': str(cwd), 'HOME': str(cwd), 'USERPROFILE': str(cwd),
            'LOCALAPPDATA': str(local_appdata),
            'MPLCONFIGDIR': str(Path(cwd) / '.matplotlib'), 'OPENBLAS_NUM_THREADS': '1'}


def _launch(args):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    name = _scope_name(args.scope)
    if Path(args.cwd) not in map(Path, args.write):
        raise ValueError('Working directory must be one of the writable directories')
    with ExitStack() as stack:
        parent = stack.enter_context(_owned(win32api.OpenProcess(win32con.SYNCHRONIZE, False, args.parent)))
        sid, sid_text = stack.enter_context(_container_sid(name))
        for directory in args.read:
            _grant(directory, sid_text, False)
        for directory in args.write:
            _grant(directory, sid_text, True)
        job = Job()
        stack.callback(job.close)
        capabilities = _SecurityCapabilities(sid.value, None, 0, 0)
        job_handles = (w.HANDLE * 1)(int(job.handle))
        handles = []
        current = win32api.GetCurrentProcess()
        for fd in (0, 1, 2):
            handle = win32api.DuplicateHandle(current, msvcrt.get_osfhandle(fd), current, 0,
                                              True, win32con.DUPLICATE_SAME_ACCESS)
            stack.callback(handle.Close)
            handles.append(int(handle))
        stdio = (w.HANDLE * 3)(*handles)
        attrs = ((0x20009, capabilities), (0x20002, stdio), (0x2000d, job_handles))
        initialize = _function(kernel, 'InitializeProcThreadAttributeList', w.BOOL, w.LPVOID,
                               w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t))
        size = ctypes.c_size_t()
        initialize(None, len(attrs), 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buf = ctypes.create_string_buffer(size.value)
        _check(initialize(buf, len(attrs), 0, ctypes.byref(size)))
        stack.callback(_function(kernel, 'DeleteProcThreadAttributeList', None, w.LPVOID), buf)
        update = _function(kernel, 'UpdateProcThreadAttribute', w.BOOL, w.LPVOID, w.DWORD,
                           ctypes.c_size_t, w.LPVOID, ctypes.c_size_t, w.LPVOID, w.LPVOID)
        for key, value in attrs:
            _check(update(buf, 0, key, ctypes.byref(value), ctypes.sizeof(value), None, None))
        setup = _Startup()
        setup.cb = ctypes.sizeof(setup)
        setup.dwFlags = 0x100  # STARTF_USESTDHANDLES
        setup.hStdInput, setup.hStdOutput, setup.hStdError = handles
        setup.lpAttributeList = ctypes.addressof(buf)
        from win32com.shell import shell, shellcon
        # CreateProcessW resolves this AppContainer's package under the real LocalAppData folder.
        local_appdata = shell.SHGetFolderPath(0, shellcon.CSIDL_LOCAL_APPDATA, 0, 0)
        env = _child_environment(args.cwd, args.python, win32api.GetWindowsDirectory(), local_appdata)
        environment = ctypes.create_unicode_buffer('\0'.join(f'{k}={v}' for k, v in sorted(env.items(), key=lambda item: item[0].upper())) + '\0')
        arguments = subprocess.list2cmdline([args.python, *args.argv])
        info = _ProcessInfo()
        create = _function(kernel, 'CreateProcessW', w.BOOL, w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID,
                           w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, ctypes.POINTER(_Startup),
                           ctypes.POINTER(_ProcessInfo))
        # Security capabilities, explicit handle inheritance and Job assignment apply atomically.
        flags = 0x08000000 | 0x00080000 | 0x00000400 | 0x00000004  # no window, extended startup, Unicode, suspended
        _check(create(args.python, ctypes.create_unicode_buffer(arguments), None, None, True,
                      flags, environment, args.cwd, ctypes.byref(setup), ctypes.byref(info)))
        close = _function(kernel, 'CloseHandle', w.BOOL, w.HANDLE)
        stack.callback(close, info.process)
        stack.callback(close, info.thread)
        with _owned(security.OpenProcessToken(info.process, win32con.TOKEN_QUERY)) as token:
            is_container = w.DWORD()
            length = w.DWORD()
            get_info = _function(advapi, 'GetTokenInformation', w.BOOL, w.HANDLE, ctypes.c_int,
                                 w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))
            _check(get_info(int(token), 29, ctypes.byref(is_container), ctypes.sizeof(is_container),
                            ctypes.byref(length)))  # TokenIsAppContainer
            if not is_container.value:
                raise PermissionError('Worker did not receive an AppContainer token')
        resume = _function(kernel, 'ResumeThread', w.DWORD, w.HANDLE)
        if resume(info.thread) == 0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        finished = win32event.WaitForMultipleObjects((parent, info.process), False, win32event.INFINITE)
        if finished == win32event.WAIT_OBJECT_0:
            return 125
        if finished != win32event.WAIT_OBJECT_0 + 1:
            raise OSError(f'Unexpected process wait result: {finished}')
        code = w.DWORD()
        get_exit_code = _function(kernel, 'GetExitCodeProcess', w.BOOL, w.HANDLE, ctypes.POINTER(w.DWORD))
        _check(get_exit_code(info.process, ctypes.byref(code)))
        return code.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    paths = sub.add_parser('runtime-paths', help='Print analysis runtime roots requiring READ & EXECUTE')
    paths.add_argument('--python', required=True)
    launch = sub.add_parser('launch')
    launch.add_argument('--python', required=True)
    launch.add_argument('--scope', required=True)
    launch.add_argument('--cwd', required=True)
    launch.add_argument('--parent', required=True, type=int)
    launch.add_argument('--read', action='append', default=[])
    launch.add_argument('--write', action='append', default=[])
    launch.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'runtime-paths':
        code = 'import json,sys;print(json.dumps(dict(prefix=sys.prefix,base_prefix=sys.base_prefix)))'
        print(subprocess.check_output([args.python, '-I', '-B', '-c', code], text=True).strip())
        return 0
    if args.argv[:1] != ['--'] or os.name != 'nt':
        parser.error('launch requires Windows and an explicit analysis command after --')
    args.argv.pop(0)
    return _launch(args)


if __name__ == '__main__':
    sys.exit(main())
