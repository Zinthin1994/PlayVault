"""Operating-system differences in one place, so the rest of PlayVault is the same on
SteamOS and Windows: how child processes are started and stopped, whether a pid is alive,
and where Windows keeps the user's folders."""
import os
import signal
import subprocess

IS_WINDOWS = os.name == 'nt'

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


def spawn_kwargs():
    """Popen kwargs that start a child in its own process group (Linux: session), without a
    console window on Windows, so it can be stopped as a unit and outlives its parent."""
    if IS_WINDOWS:
        return {'creationflags': _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW}
    return {'start_new_session': True}


def hidden_kwargs():
    """Popen kwargs for a short helper command that must not flash a console window."""
    return {'creationflags': _CREATE_NO_WINDOW} if IS_WINDOWS else {}


def pid_alive(pid):
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:  # os.kill(-1, 0) means every process on Linux
        return False
    if IS_WINDOWS:
        # os.kill(pid, 0) would terminate the process on Windows, so ask the OS instead
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate(pid, force=False):
    """Stop a process and everything it started. Linux: the process group (pid is a group
    leader because of spawn_kwargs). Windows: the process tree."""
    try:
        if IS_WINDOWS:
            cmd = ['taskkill', '/PID', str(int(pid)), '/T'] + (['/F'] if force else [])
            subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=15, **hidden_kwargs())
        else:
            os.killpg(int(pid), signal.SIGKILL if force else signal.SIGTERM)
    except Exception:
        pass


def known_folder(name):
    """A per-user Windows folder ('documents', 'saved games', 'local', 'local_low', 'roaming',
    'home'), following redirects such as OneDrive. Falls back to the usual path under the profile."""
    home = os.path.expanduser('~')
    fallback = {
        'home': home,
        'documents': os.path.join(home, 'Documents'),
        'saved games': os.path.join(home, 'Saved Games'),
        'local': os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local'),
        'local_low': os.path.join(home, 'AppData', 'LocalLow'),
        'roaming': os.environ.get('APPDATA') or os.path.join(home, 'AppData', 'Roaming'),
    }[name]
    if IS_WINDOWS and name in ('documents', 'saved games', 'local_low'):
        try:
            import ctypes
            from ctypes import wintypes
            guid = {'documents': '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}',
                    'saved games': '{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}',
                    'local_low': '{A520A1A4-1780-4FF6-BD18-167343C5AF16}'}[name]

            class GUID(ctypes.Structure):
                _fields_ = [('a', wintypes.DWORD), ('b', wintypes.WORD), ('c', wintypes.WORD), ('d', ctypes.c_ubyte * 8)]

            g = GUID()
            ole = ctypes.windll.ole32
            ole.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(g))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ole.CoTaskMemFree(out)
                if path:
                    return path
        except Exception:
            pass
    return fallback
