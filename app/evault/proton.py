"""Discovers Steam installs and the Proton builds available on this machine."""
import os
import re

from . import paths, vdf

AUTO_TOOLS = [
    {'id': 'GE-Proton', 'name': 'GE-Proton (latest, auto-download)', 'auto': True},
    {'id': 'UMU-Proton', 'name': 'UMU-Proton (Valve Proton + umu fixes, auto-download)', 'auto': True},
]


def _windows_steam_candidates():
    cands = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            cands.append(os.path.normpath(winreg.QueryValueEx(k, 'SteamPath')[0]))
    except OSError:
        pass
    for var in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(var):
            cands.append(os.path.join(os.environ[var], 'Steam'))
    return cands


def steam_roots():
    if paths.IS_WINDOWS:
        cands = _windows_steam_candidates()
    else:
        cands = [
            os.path.join(paths.HOME, '.local', 'share', 'Steam'),
            os.path.join(paths.HOME, '.steam', 'steam'),
            os.path.join(paths.HOME, '.steam', 'root'),
            os.path.join(paths.HOME, '.var', 'app', 'com.valvesoftware.Steam', 'data', 'Steam'),
        ]
    seen, out = set(), []
    for c in cands:
        if os.path.isdir(c):
            real = os.path.realpath(c)
            if real in seen:
                continue
            if os.path.isdir(os.path.join(real, 'steamapps')) or os.path.isdir(os.path.join(real, 'userdata')):
                seen.add(real)
                out.append(real)
    return out


def steam_root():
    roots = steam_roots()
    return roots[0] if roots else None


def library_folders():
    libs = []
    for root in steam_roots():
        libs.append(root)
        lf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        if os.path.exists(lf):
            try:
                data = vdf.load_text(lf)
                folders = data.get('libraryfolders') or data.get('LibraryFolders') or {}
                for v in folders.values():
                    p = v.get('path') if isinstance(v, dict) else v
                    if p and os.path.isdir(p):
                        libs.append(os.path.realpath(p))
            except Exception:
                pass
    seen, out = set(), []
    for l in libs:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out


def _ver_key(name):
    nums = [int(n) for n in re.findall(r'\d+', name)]
    exp = 1 if 'experimental' in name.lower() else 0
    return (exp, nums)


def list_tools():
    if paths.IS_WINDOWS:
        return []  # games run natively, there is no compatibility layer to pick
    tools = []
    seen = set()
    # Valve Proton builds installed through Steam
    for lib in library_folders():
        common = os.path.join(lib, 'steamapps', 'common')
        if not os.path.isdir(common):
            continue
        for d in os.listdir(common):
            full = os.path.join(common, d)
            if d.lower().startswith('proton') and os.path.isfile(os.path.join(full, 'proton')):
                real = os.path.realpath(full)
                if real not in seen:
                    seen.add(real)
                    tools.append({'id': full, 'name': d, 'auto': False, 'source': 'Steam'})
    # Custom tools (GE-Proton etc.)
    ctd = []
    for root in steam_roots():
        ctd.append(os.path.join(root, 'compatibilitytools.d'))
    ctd.append(os.path.join(paths.HOME, '.steam', 'root', 'compatibilitytools.d'))
    for c in ctd:
        if not os.path.isdir(c):
            continue
        for d in os.listdir(c):
            full = os.path.join(c, d)
            if os.path.isfile(os.path.join(full, 'proton')):
                real = os.path.realpath(full)
                if real not in seen:
                    seen.add(real)
                    tools.append({'id': full, 'name': d, 'auto': False, 'source': 'Custom'})
    tools.sort(key=lambda t: _ver_key(t['name']), reverse=True)
    return AUTO_TOOLS + tools


def resolve(tool_id):
    """Value for umu's PROTONPATH. Falls back to GE-Proton if a configured path vanished."""
    if not tool_id:
        return 'GE-Proton'
    if tool_id in ('GE-Proton', 'UMU-Proton'):
        return tool_id
    if os.path.isfile(os.path.join(tool_id, 'proton')):
        return tool_id
    return 'GE-Proton'


def display_name(tool_id):
    for t in AUTO_TOOLS:
        if t['id'] == tool_id:
            return t['name']
    return os.path.basename(tool_id.rstrip('/')) if tool_id else 'Default'
