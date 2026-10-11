"""Storage locations (internal drive, microSD, USB) and free space."""
import os
import shutil

from . import paths


def _usage(path):
    p = path
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    try:
        u = shutil.disk_usage(p or '/')
        return u.free, u.total
    except OSError:
        return 0, 0


def drives(install_dir=None):
    out = []
    seen = set()

    def add(label, mount, games_dir, kind):
        free, total = _usage(mount)
        dev = None
        try:
            dev = os.stat(mount).st_dev
        except OSError:
            pass
        if dev is not None and dev in seen:
            return
        if dev is not None:
            seen.add(dev)
        out.append({'label': label, 'mount': mount, 'games_dir': games_dir, 'kind': kind,
                    'free': free, 'total': total})

    add('Internal storage', paths.HOME, paths.DEFAULT_GAMES_DIR, 'internal')
    user = os.path.basename(paths.HOME)
    for base in (f'/run/media/{user}', '/run/media', f'/media/{user}', '/mnt'):
        if not os.path.isdir(base):
            continue
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for name in entries:
            m = os.path.join(base, name)
            if os.path.isdir(m) and os.path.ismount(m):
                kind = 'sd' if 'mmcblk' in name or 'sd' in name.lower() else 'external'
                add(name, m, os.path.join(m, 'Games'), kind)
    if install_dir and not any(install_dir.startswith(d['mount']) and d['mount'] != paths.HOME for d in out):
        free, total = _usage(install_dir)
        if not any(d['games_dir'] == install_dir for d in out):
            out.append({'label': 'Custom folder', 'mount': install_dir, 'games_dir': install_dir,
                        'kind': 'custom', 'free': free, 'total': total})
    return out


def free_space(path):
    return _usage(path)[0]
