"""Steam library integration: non-Steam shortcuts + artwork.

Steam rewrites shortcuts.vdf from memory, so edits only stick while Steam is
closed. Changes are staged in data/steam_pending.json and applied either
immediately (Steam not running) or by a detached helper that shuts Steam down,
writes the file and brings Steam / Game Mode back.

Artwork is written straight into userdata/<id>/config/grid, which is safe
while Steam runs.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import zlib

from . import paths, vdf, proton
from .util import read_json, write_json, download, log, pid_alive

TAG = 'PlayVault'
APP_TAG = 'PlayVault-App'
LEGACY_TAGS = ('EpicVault',)          # tiles created before the rename
LEGACY_APP_TAGS = ('EpicVault-App',)
_RESTART_LOCK = os.path.join(paths.DATA, 'steam_restart.json')
STEAMID64_BASE = 76561197960265728
_lock = threading.RLock()


# ---------------------------------------------------------------- discovery

def user_dirs():
    """[(account_id, userdata_path)] most-recent user first."""
    root = proton.steam_root()
    if not root:
        return []
    ud = os.path.join(root, 'userdata')
    if not os.path.isdir(ud):
        return []
    users = [d for d in os.listdir(ud) if d.isdigit() and d != '0']
    recent = None
    lu = os.path.join(root, 'config', 'loginusers.vdf')
    if os.path.exists(lu):
        try:
            data = vdf.load_text(lu).get('users', {})
            best_ts = -1
            for sid, info in data.items():
                acct = str(int(sid) - STEAMID64_BASE)
                ts = int(info.get('Timestamp', 0) or 0)
                if str(info.get('MostRecent', '0')) == '1':
                    ts = 1 << 62
                if acct in users and ts > best_ts:
                    best_ts, recent = ts, acct
        except Exception as e:
            log.warning('loginusers.vdf unreadable: %r', e)
    users.sort(key=lambda u: (u != recent, -os.path.getmtime(os.path.join(ud, u))))
    return [(u, os.path.join(ud, u)) for u in users]


def current_user():
    u = user_dirs()
    return u[0] if u else (None, None)


def steam_running():
    if os.name == 'nt':
        return False
    try:
        out = subprocess.run(['pgrep', '-x', 'steam'], capture_output=True, text=True).stdout
        return bool(out.strip())
    except FileNotFoundError:
        return False


def in_game_mode():
    return (os.environ.get('XDG_CURRENT_DESKTOP', '').lower() == 'gamescope'
            or os.environ.get('SteamGamepadUI') == '1'
            or bool(os.environ.get('GAMESCOPE_WAYLAND_DISPLAY')))


# ---------------------------------------------------------------- shortcuts

def shortcut_appid(exe, name):
    return (zlib.crc32((exe + name).encode('utf-8')) & 0xFFFFFFFF) | 0x80000000


def _shortcuts_path(userdir):
    return os.path.join(userdir, 'config', 'shortcuts.vdf')


def read_shortcuts(userdir):
    p = _shortcuts_path(userdir)
    if not os.path.exists(p):
        return {'shortcuts': {}}
    with open(p, 'rb') as f:
        data = f.read()
    if not data.strip(b'\x00\x08'):
        return {'shortcuts': {}}
    parsed = vdf.parse_binary(data)
    key = next((k for k in parsed if k.lower() == 'shortcuts'), None)
    return {'shortcuts': parsed.get(key, {}) if key else {}}


def write_shortcuts(userdir, data):
    p = _shortcuts_path(userdir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        shutil.copy2(p, p + '.playvault.bak')
    tmp = p + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(vdf.dump_binary(data))
    os.replace(tmp, p)


def _get(d, key, default=None):
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return default


def _tags(entry):
    tags = _get(entry, 'tags', {}) or {}
    return list(tags.values()) if isinstance(tags, dict) else []


def _is_ours(entry, app_name=None):
    if not any(t in (TAG, *LEGACY_TAGS) for t in _tags(entry)):
        return False
    return app_name is None or str(_get(entry, 'LaunchOptions', '')).strip() == app_name


def _is_app_tile(entry):
    return any(t in (APP_TAG, *LEGACY_APP_TAGS) for t in _tags(entry))


def make_entry(app_name, title, exe, start_dir, icon='', launch_options=None, extra_tags=()):
    quoted = f'"{exe}"'
    appid = shortcut_appid(quoted, title)
    tags = {str(i): t for i, t in enumerate([TAG, *extra_tags])}
    return appid, {
        'appid': appid,
        'AppName': title,
        'Exe': quoted,
        'StartDir': f'"{start_dir}"',
        'icon': icon,
        'ShortcutPath': '',
        'LaunchOptions': launch_options if launch_options is not None else app_name,
        'IsHidden': 0,
        'AllowDesktopConfig': 1,
        'AllowOverlay': 1,
        'OpenVR': 0,
        'Devkit': 0,
        'DevkitGameID': '',
        'DevkitOverrideAppID': 0,
        'LastPlayTime': 0,
        'FlatpakAppID': '',
        'tags': tags,
    }


def list_ours():
    """{app_name: appid} for shortcuts we created (as written on disk)."""
    _, ud = current_user()
    if not ud:
        return {}
    try:
        data = read_shortcuts(ud)
    except Exception as e:
        log.warning('shortcuts.vdf unreadable: %r', e)
        return {}
    out = {}
    for e in data['shortcuts'].values():
        if _is_ours(e):
            out[str(_get(e, 'LaunchOptions', '')).strip()] = _get(e, 'appid', 0) & 0xFFFFFFFF
    return out


# ---------------------------------------------------------------- artwork

def epic_art(meta):
    """Store artwork fallbacks for an Epic game: {suffix: [urls]}."""
    e = {}
    for ki in ((meta or {}).get('metadata') or {}).get('keyImages', []) or []:
        e.setdefault(ki.get('type'), ki.get('url'))

    def pick(*types):
        return [e[t] for t in types if e.get(t)]

    wide = pick('DieselGameBox', 'DieselStoreFrontWide', 'OfferImageWide')
    return {'p': pick('DieselGameBoxTall', 'OfferImageTall', 'Thumbnail'), '': wide, '_hero': wide,
            '_logo': pick('DieselGameBoxLogo')}


def gog_art(db, image=None):
    """Store artwork fallbacks for a GOG game from its gamesdb entry."""
    db = db or {}
    one = lambda *xs: [x for x in xs if x]
    return {'p': one(db.get('vertical')), '': one(db.get('horizontal'), image), '_hero': one(db.get('background'), image),
            '_logo': one(db.get('logo'))}  # art_sources tries Steam's own art first when there is a Steam match


def art_sources(fallbacks, steam_appid):
    base = []
    if steam_appid:
        base = [f'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{steam_appid}/',
                f'https://cdn.cloudflare.steamstatic.com/steam/apps/{steam_appid}/']

    def steam(name):
        return [b + name for b in base]

    fb = fallbacks or {}
    return {
        'p': steam('library_600x900_2x.jpg') + steam('library_600x900.jpg') + fb.get('p', []),
        '': steam('header.jpg') + fb.get('', []),
        '_hero': steam('library_hero.jpg') + fb.get('_hero', []),
        '_logo': steam('logo.png') + fb.get('_logo', []),
    }


def install_art(appid, fallbacks, steam_appid):
    """Download grid/hero/logo art into every Steam user's grid folder. Returns icon path."""
    users = user_dirs()
    if not users:
        return ''
    icon = ''
    cache_dir = os.path.join(paths.ART, str(appid))
    for suffix, urls in art_sources(fallbacks, steam_appid).items():
        ext = '.png' if suffix == '_logo' else '.jpg'
        cached = os.path.join(cache_dir, f'{suffix or "wide"}{ext}')
        if not os.path.exists(cached):
            for url in urls:
                if download(url, cached):
                    break
        if not os.path.exists(cached):
            continue
        if suffix == 'p':
            icon = cached
        for _, ud in users:
            grid = os.path.join(ud, 'config', 'grid')
            os.makedirs(grid, exist_ok=True)
            for old_ext in ('.png', '.jpg'):
                old = os.path.join(grid, f'{appid}{suffix}{old_ext}')
                if os.path.exists(old) and old_ext != ext:
                    os.remove(old)
            shutil.copyfile(cached, os.path.join(grid, f'{appid}{suffix}{ext}'))
    return icon


def remove_art(appid):
    for _, ud in user_dirs():
        grid = os.path.join(ud, 'config', 'grid')
        for suffix in ('p', '', '_hero', '_logo', '_icon'):
            for ext in ('.png', '.jpg'):
                p = os.path.join(grid, f'{appid}{suffix}{ext}')
                if os.path.exists(p):
                    os.remove(p)


# ---------------------------------------------------------------- staging

def pending():
    return read_json(paths.STEAM_PENDING, None) or {'add': {}, 'remove': []}


def _save_pending(p):
    write_json(paths.STEAM_PENDING, p)


def stage_add(app_name, title, art_fallbacks, steam_appid):
    """Prepare a shortcut for an installed game (downloads art now, writes VDF later)."""
    appid, entry = make_entry(app_name, title, paths.LAUNCH_SCRIPT, paths.ROOT)
    try:
        entry['icon'] = install_art(appid, art_fallbacks, steam_appid)
    except Exception as e:
        log.warning('Artwork for %s failed: %r', app_name, e)
    with _lock:
        p = pending()
        p['add'][app_name] = entry
        p['remove'] = [a for a in p['remove'] if a != app_name]
        _save_pending(p)
    return appid


def stage_remove(app_name):
    with _lock:
        p = pending()
        p['add'].pop(app_name, None)
        if app_name not in p['remove']:
            p['remove'].append(app_name)
        _save_pending(p)


def _copy_app_art(appid):
    for suffix, name in (('p', 'portrait.png'), ('', 'wide.png'), ('_hero', 'hero.png'), ('_logo', 'logo.png')):
        src = os.path.join(paths.APP_ART, name)
        if not os.path.exists(src):
            continue
        for _, ud in user_dirs():
            grid = os.path.join(ud, 'config', 'grid')
            os.makedirs(grid, exist_ok=True)
            for ext in ('.png', '.jpg'):
                old = os.path.join(grid, f'{appid}{suffix}{ext}')
                if os.path.exists(old):
                    os.remove(old)
            shutil.copyfile(src, os.path.join(grid, f'{appid}{suffix}.png'))


def stage_self():
    """The PlayVault launcher tile itself."""
    appid, entry = make_entry('__playvault__', 'PlayVault', paths.APP_SCRIPT, paths.APP, launch_options='')
    entry['tags'] = {'0': APP_TAG}
    _copy_app_art(appid)
    icon = os.path.join(paths.APP_ART, 'icon.png')
    entry['icon'] = icon if os.path.exists(icon) else ''
    with _lock:
        p = pending()
        p['self'] = entry
        _save_pending(p)
    return appid


def stage_migrate():
    """Ask for the EpicVault -> PlayVault tile rewrite on the next apply."""
    with _lock:
        p = pending()
        p['migrate'] = True
        _save_pending(p)


def needs_migration():
    _, ud = current_user()
    if not ud:
        return False
    try:
        entries = read_shortcuts(ud)['shortcuts'].values()
    except Exception:
        return False
    return any(any(t in LEGACY_TAGS + LEGACY_APP_TAGS for t in _tags(e)) for e in entries)


def has_pending():
    p = pending()
    return bool(p.get('add') or p.get('remove') or p.get('self') or p.get('migrate'))


def pending_count():
    p = pending()
    return len(p.get('add', {})) + len(p.get('remove', [])) + (1 if p.get('self') else 0) + (1 if p.get('migrate') else 0)


def _migrate_entry(e):
    """Point an EpicVault tile at PlayVault, keeping its Steam app id (and so its artwork,
    controller layout and playtime)."""
    old_id = _get(e, 'appid', 0) & 0xFFFFFFFF
    app_tile = _is_app_tile(e)
    name = 'PlayVault' if app_tile else _get(e, 'AppName', '')
    exe = paths.APP_SCRIPT if app_tile else paths.LAUNCH_SCRIPT
    out = {k: v for k, v in e.items() if k.lower() not in ('appname', 'exe', 'startdir', 'tags', 'icon')}
    out['AppName'] = name
    out['Exe'] = f'"{exe}"'
    out['StartDir'] = f'"{paths.APP if app_tile else paths.ROOT}"'
    out['tags'] = {'0': APP_TAG if app_tile else TAG}
    icon = str(_get(e, 'icon', '') or '')
    out['icon'] = os.path.join(paths.APP_ART, 'icon.png') if app_tile else icon.replace('/epicvault/', '/playvault/')
    # in case Steam ever recomputes ids from Exe+name, mirror the artwork to that id too
    new_id = shortcut_appid(out['Exe'], name)
    if not old_id:
        out['appid'] = new_id
    if app_tile:
        for i in {old_id, new_id} - {0}:
            _copy_app_art(i)
    elif old_id and new_id != old_id:
        for _, ud in user_dirs():
            grid = os.path.join(ud, 'config', 'grid')
            for suffix in ('p', '', '_hero', '_logo', '_icon'):
                for ext in ('.png', '.jpg'):
                    src = os.path.join(grid, f'{old_id}{suffix}{ext}')
                    if os.path.exists(src):
                        shutil.copyfile(src, os.path.join(grid, f'{new_id}{suffix}{ext}'))
    return out


def apply_pending_now():
    """Write staged changes into shortcuts.vdf for every Steam user. Steam must be closed."""
    with _lock:
        p = pending()
        if not has_pending():
            return {'added': 0, 'removed': 0}
        users = user_dirs()
        if not users:
            raise RuntimeError('No Steam user found - log into Steam once first')
        added = removed = migrated = 0
        for _, ud in users:
            data = read_shortcuts(ud)
            sc = data['shortcuts']
            entries = list(sc.values())
            drop = set(p.get('remove', [])) | set(p.get('add', {}).keys())
            kept = []
            for e in entries:
                lo = str(_get(e, 'LaunchOptions', '')).strip()
                if _is_ours(e) and lo in drop:
                    if lo in p.get('remove', []):
                        removed += 1
                        remove_art(_get(e, 'appid', 0) & 0xFFFFFFFF)
                    continue
                if p.get('self') and _is_app_tile(e):
                    continue
                if any(t in LEGACY_TAGS + LEGACY_APP_TAGS for t in _tags(e)):
                    e = _migrate_entry(e)
                    migrated += 1
                kept.append(e)
            kept.extend(p.get('add', {}).values())
            if p.get('self'):
                kept.append(p['self'])
            added = len(p.get('add', {})) + (1 if p.get('self') else 0)
            write_shortcuts(ud, {'shortcuts': {str(i): e for i, e in enumerate(kept)}})
        _save_pending({'add': {}, 'remove': []})
        return {'added': added, 'removed': removed, 'migrated': migrated}


def apply_with_restart(game_mode):
    """Apply now if Steam is closed, otherwise hand off to a detached helper that restarts Steam."""
    if not steam_running():
        return {'restarting': False, **apply_pending_now()}
    # one restart at a time: a second press while Steam is still shutting down would
    # otherwise start a second helper and restart Steam twice
    lock = read_json(_RESTART_LOCK, None) or {}
    if lock.get('pid') and pid_alive(lock['pid']) and time.time() - lock.get('started', 0) < 180:
        return {'restarting': True, 'already': True}
    py = paths.VENV_PYTHON if os.path.exists(paths.VENV_PYTHON) else sys.executable
    env = os.environ.copy()
    env['PYTHONPATH'] = paths.APP
    env['PLAYVAULT_GAMEMODE'] = '1' if game_mode else '0'
    logf = open(os.path.join(paths.LOGS, 'steam_apply.log'), 'a')
    proc = subprocess.Popen([py, '-m', 'evault.steam', 'apply-restart'], env=env, cwd=paths.APP,
                            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    write_json(_RESTART_LOCK, {'pid': proc.pid, 'started': time.time()})
    return {'restarting': True}


def _helper_apply_restart():
    try:
        _apply_restart()
    finally:
        lock = read_json(_RESTART_LOCK, None) or {}
        if lock.get('pid') == os.getpid():
            try:
                os.remove(_RESTART_LOCK)
            except OSError:
                pass


def _apply_restart():
    game_mode = os.environ.get('PLAYVAULT_GAMEMODE') == '1'
    print(time.ctime(), 'helper: shutting down Steam (game mode=%s)' % game_mode, flush=True)
    time.sleep(1.5)
    subprocess.Popen(['steam', '-shutdown'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        if not steam_running():
            break
        time.sleep(0.5)
    else:
        print('Steam did not exit in time; aborting', flush=True)
        return
    time.sleep(1.0)
    try:
        print('applied:', apply_pending_now(), flush=True)
    except Exception as e:
        print('apply failed:', repr(e), flush=True)
    if game_mode:
        # gamescope-session normally restarts Steam by itself; if it hasn't, ask for Game Mode again.
        for _ in range(30):
            if steam_running():
                return
            time.sleep(1)
        print('Steam not back, requesting game mode session', flush=True)
        subprocess.Popen(['steamos-session-select', 'gamescope'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(['steam'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def main(argv):
    paths.ensure()
    cmd = argv[0] if argv else ''
    if cmd == 'apply-restart':
        _helper_apply_restart()
    elif cmd in ('add-self', 'migrate'):
        if cmd == 'add-self':
            stage_self()
        if needs_migration():
            stage_migrate()
        if steam_running():
            print('Steam is running - changes are staged; PlayVault applies them on the next Steam restart')
        else:
            print(apply_pending_now())
    elif cmd == 'apply':
        print(apply_pending_now())
    elif cmd == 'restart':
        # apply staged changes with a safe Steam restart (works from SSH too)
        print(apply_with_restart('--game-mode' in argv or in_game_mode()))
    elif cmd == 'list':
        print(list_ours())
    else:
        print('usage: python -m evault.steam [add-self|migrate|apply|restart [--game-mode]|list]')


if __name__ == '__main__':
    main(sys.argv[1:])
