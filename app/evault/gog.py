"""GOG support: gogdl (Heroic's open-source GOG client) + GOG's public web APIs.

GOG games are keyed as 'gog_<productId>' everywhere in PlayVault so they can never
collide with Epic app names. gogdl handles login tokens, downloads, updates, repair
and cloud-save transfers; everything else (library, artwork, Steam ids, builds,
cloud-save locations) comes straight from GOG's web APIs.

On-disk data (data/gog/):
    auth.json        gogdl tokens
    user.json        {username, user_id}
    library.json     owned games {updated, games: [...]}
    gamesdb.json     cached GOG gamesdb entries (covers, logo, Steam id, summary)
    installed.json   {key: {id, title, platform, install_path, build_id, version, install_size, ...}}
    saves.json       last cloud-save sync timestamps
    heroic_gogdl/    gogdl manifests (GOGDL_CONFIG_PATH)
"""
import glob
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import zlib

from . import host, paths
from .util import read_json, write_json, http_get, http_json, tail, log

PREFIX = 'gog_'
CLIENT_ID = '46899977096215655'
LOGIN_URL = ('https://auth.gog.com/auth?client_id=46899977096215655'
             '&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient'
             '&response_type=code&layout=galaxy')
EMBED = 'https://embed.gog.com'
CONTENT_SYSTEM = 'https://content-system.gog.com'
GAMESDB_TTL = 30 * 86400
BUILDS_TTL = 6 * 3600

_lock = threading.RLock()
_creds = {'data': None, 'until': 0}


def key(gid):
    return f'{PREFIX}{gid}'


def is_gog(app_name):
    return str(app_name).startswith(PREFIX)


def gid(app_name):
    return str(app_name)[len(PREFIX):]


# ---------------------------------------------------------------- gogdl process

def binary():
    if os.path.exists(paths.GOGDL_BIN):
        return paths.GOGDL_BIN
    return shutil.which('gogdl')


def available():
    return binary() is not None


def env():
    e = os.environ.copy()
    e['GOGDL_CONFIG_PATH'] = paths.GOG
    e['PYTHONUNBUFFERED'] = '1'
    for k in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        e.pop(k, None)
    return e


def _cmd(args):
    b = binary()
    if not b:
        raise RuntimeError('gogdl (GOG client) is not installed - re-run the installer')
    return [b, '--auth-config-path', paths.GOG_AUTH] + list(args)


def run(args, timeout=600):
    try:
        p = subprocess.run(_cmd(args), capture_output=True, text=True, timeout=timeout,
                           env=env(), stdin=subprocess.DEVNULL, **host.hidden_kwargs())
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, '', f'gogdl {args[0]} timed out'
    except RuntimeError as e:
        return 127, '', str(e)


def popen(args):
    return subprocess.Popen(_cmd(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, env=env(), stdin=subprocess.DEVNULL,
                            **host.spawn_kwargs())


def version():
    if not available():
        return None
    try:
        p = subprocess.run([binary(), '--version'], capture_output=True, text=True, timeout=30,
                           env=env(), stdin=subprocess.DEVNULL, **host.hidden_kwargs())
        return (p.stdout or p.stderr).strip().splitlines()[-1] if (p.stdout or p.stderr).strip() else None
    except Exception:
        return None


def _last_json(text):
    for line in reversed((text or '').strip().splitlines()):
        line = line.strip()
        if line.startswith('{') or line.startswith('['):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------- auth

def credentials():
    """Valid access token data (refreshed by gogdl when needed), or None if signed out."""
    if not os.path.exists(paths.GOG_AUTH):
        return None
    with _lock:
        if _creds['data'] and time.time() < _creds['until']:
            return _creds['data']
        rc, out, err = run(['auth'], timeout=60)
        data = _last_json(out)
        if not isinstance(data, dict) or not data.get('access_token'):
            log.warning('GOG credentials unavailable: %s', tail(err, 300))
            return None
        _creds['data'] = data
        _creds['until'] = data.get('loginTime', time.time()) + int(data.get('expires_in', 3600)) - 120
        return data


def _api(url, timeout=30):
    c = credentials()
    if not c:
        raise RuntimeError('Not signed in to GOG')
    status, body = http_get(url, timeout, headers={'Authorization': f'Bearer {c["access_token"]}'})
    if status != 200:
        raise RuntimeError(f'GOG request failed ({status or "network error"})')
    return json.loads(body)


def user():
    data = read_json(paths.GOG_USER, None)
    if isinstance(data, dict) and data.get('username') and os.path.exists(paths.GOG_AUTH):
        return data['username']
    return None


def user_id():
    data = read_json(paths.GOG_USER, None) or {}
    return data.get('user_id')


def login_with_code(code):
    code = (code or '').strip()
    if 'code=' in code:  # the whole on_login_success URL was pasted
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get('code', [''])[0]
    code = code.strip().strip('"')
    if not code:
        return False, 'No authorization code given'
    os.makedirs(paths.GOG, exist_ok=True)
    rc, out, err = run(['auth', '--code', code], timeout=90)
    data = _last_json(out)
    if not isinstance(data, dict) or data.get('error') or not data.get('access_token'):
        return False, tail(err) or 'GOG sign-in failed - the code may have expired, try again'
    with _lock:
        _creds['data'] = None
    try:
        info = _api(f'{EMBED}/userData.json')
        name = info.get('username') or 'GOG user'
        write_json(paths.GOG_USER, {'username': name, 'user_id': info.get('userId') or data.get('user_id')})
    except Exception as e:
        log.warning('GOG userData failed: %r', e)
        name = 'GOG user'
        write_json(paths.GOG_USER, {'username': name, 'user_id': data.get('user_id')})
    return True, name


def logout():
    with _lock:
        _creds['data'] = None
        for p in (paths.GOG_AUTH, paths.GOG_USER, paths.GOG_LIBRARY):
            try:
                os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------- library

def refresh_library():
    games = []
    page, total = 1, 1
    while page <= total:
        data = _api(f'{EMBED}/account/getFilteredProducts?mediaType=1&sortBy=title&page={page}')
        total = int(data.get('totalPages') or 1)
        for p in data.get('products') or []:
            if p.get('isGame') is False:
                continue
            works = p.get('worksOn') or {}
            games.append({
                'id': str(p['id']),
                'title': p.get('title') or str(p['id']),
                'image': ('https:' + p['image']) if str(p.get('image', '')).startswith('//') else p.get('image'),
                'slug': p.get('slug'),
                'platforms': [k for k, osname in (('windows', 'Windows'), ('linux', 'Linux')) if works.get(osname)],
                'dlc_count': p.get('dlcCount') or 0,
            })
        page += 1
    write_json(paths.GOG_LIBRARY, {'updated': time.time(), 'games': games})
    return games


def library_index():
    return read_json(paths.GOG_LIBRARY, None) or {'updated': 0, 'games': []}


def library_entry(g):
    for e in library_index()['games']:
        if e['id'] == str(g):
            return e
    return None


def _fmt(img, formatter='', ext='jpg'):
    url = (img or {}).get('url_format')
    if not url:
        return None
    return url.replace('{formatter}', formatter).replace('{ext}', ext)


def _loc(v):
    if isinstance(v, dict):
        return v.get('en-US') or v.get('*') or next(iter(v.values()), None)
    return v


def fetch_gamesdb(g):
    status, d = http_json(f'https://gamesdb.gog.com/platforms/gog/external_releases/{g}', timeout=20)
    if not d:
        return {'fetched': time.time(), 'missing': True}
    game = d.get('game') or {}
    steam = next((r.get('external_id') for r in game.get('releases') or []
                  if r.get('platform_id') == 'steam' and str(r.get('external_id', '')).isdigit()), None)
    return {
        'fetched': time.time(),
        'title': _loc(d.get('title')) or _loc(game.get('title')),
        'vertical': _fmt(game.get('vertical_cover')),
        'background': _fmt(game.get('background')),
        'horizontal': _fmt(game.get('horizontal_artwork')) or _fmt(game.get('background')),
        'logo': _fmt(game.get('logo'), ext='png') or _fmt(d.get('logo'), ext='png'),
        'icon': _fmt(game.get('square_icon')),
        'steam_appid': int(steam) if steam else None,
        'summary': _loc(d.get('summary')) or _loc(game.get('summary')),
        'genres': [_loc(x.get('name')) for x in game.get('genres') or [] if x.get('name')][:4],
        'developers': [x.get('name') for x in game.get('developers') or [] if x.get('name')],
        'publishers': [x.get('name') for x in game.get('publishers') or [] if x.get('name')],
        'release_date': (d.get('first_release_date') or game.get('first_release_date') or '')[:10] or None,
    }


def gamesdb_all():
    return read_json(paths.GOG_DB, {}) or {}


def gamesdb(g, refresh=False):
    with _lock:
        db = gamesdb_all()
        hit = db.get(str(g))
    if hit and not refresh and time.time() - hit.get('fetched', 0) < GAMESDB_TTL:
        return hit
    entry = fetch_gamesdb(g)
    with _lock:
        db = gamesdb_all()
        db[str(g)] = entry
        write_json(paths.GOG_DB, db)
    return entry


def fill_gamesdb(on_progress=None, gap=0.15):
    """Fetch artwork/Steam ids for every owned game that has no cached entry yet."""
    db = gamesdb_all()
    todo = [e['id'] for e in library_index()['games']
            if e['id'] not in db or time.time() - db[e['id']].get('fetched', 0) > GAMESDB_TTL]
    for i, g in enumerate(todo):
        try:
            gamesdb(g, refresh=True)
        except Exception as e:
            log.warning('gamesdb %s failed: %r', g, e)
        if on_progress and (i % 10 == 9 or i == len(todo) - 1):
            on_progress()
        time.sleep(gap)


# ---------------------------------------------------------------- installs

def installed():
    return read_json(paths.GOG_INSTALLED, None) or {}


def _game_info_file(path, g):
    for base in (path, os.path.join(path, 'game')):
        f = os.path.join(base, f'goggame-{g}.info')
        if os.path.exists(f):
            return f
    found = glob.glob(os.path.join(path, 'goggame-*.info'))
    return found[0] if found else None


def read_game_info(path, g):
    f = _game_info_file(path, g)
    return read_json(f, None) if f else None


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for n in files:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def register_install(g, platform, path, info=None, title=None):
    """Record an install after gogdl finished (also used after update/repair/move)."""
    g = str(g)
    info = info or {}
    entry = dict(installed().get(key(g)) or {})
    entry.update({'id': g, 'platform': platform, 'install_path': path})
    entry['title'] = title or entry.get('title') or (library_entry(g) or {}).get('title') or g
    gi = read_game_info(path, g) if platform == 'windows' else None
    build_file = os.path.join(path, f'goggame-{g}.id')
    build = read_json(build_file, None) if os.path.exists(build_file) else None
    entry['build_id'] = (build or {}).get('buildId') or (gi or {}).get('buildId') or info.get('buildId') or entry.get('build_id')
    if platform == 'linux':
        gameinfo = os.path.join(path, 'gameinfo')
        if os.path.exists(gameinfo):
            lines = open(gameinfo, encoding='utf-8', errors='replace').read().split('\n')
            entry['version'] = lines[1] if len(lines) > 1 else info.get('versionName')
        else:
            entry['version'] = info.get('versionName') or entry.get('version')
    else:
        entry['version'] = info.get('versionName') or entry.get('version') or entry['build_id']
    entry['client_id'] = (gi or {}).get('clientId') or entry.get('client_id')
    entry['install_size'] = _dir_size(path) if os.path.isdir(path) else 0
    entry['updated'] = time.time()
    with _lock:
        data = installed()
        data[key(g)] = entry
        write_json(paths.GOG_INSTALLED, data)
    return entry


def unregister(g):
    with _lock:
        data = installed()
        data.pop(key(g), None)
        write_json(paths.GOG_INSTALLED, data)
    try:
        os.remove(os.path.join(paths.GOG, 'heroic_gogdl', 'manifests', str(g)))
    except OSError:
        pass


def info(g, platform, lang='en-US', dlcs=True):
    """Download/disk size + folder name + version via gogdl info (network, a few seconds)."""
    rc, out, err = run(['info', str(g), '--platform', platform, '--lang', lang,
                        '--with-dlcs' if dlcs else '--skip-dlcs'], timeout=240)
    data = _last_json(out)
    if not isinstance(data, dict):
        raise RuntimeError(tail(err, 300) or 'Could not read game info from GOG')
    size = data.get('size') or {}
    star = size.get('*') or size.get('en-US') or {}
    extra = {}
    for k, v in size.items():
        if k not in ('*',) and isinstance(v, dict) and k.lower().startswith(lang.split('-')[0].lower()):
            extra = v
            break
    dl = data.get('download_size') if 'download_size' in data else (star.get('download_size', 0) + extra.get('download_size', 0))
    disk = data.get('disk_size') if 'disk_size' in data else (star.get('disk_size', 0) + extra.get('disk_size', 0))
    if dlcs:
        for d in data.get('dlcs') or []:
            ds = (d.get('size') or {})
            for v in ds.values():
                if isinstance(v, dict):
                    dl += v.get('download_size', 0)
                    disk += v.get('disk_size', 0)
    return {'download': dl, 'disk': disk, 'folder_name': data.get('folder_name'),
            'versionName': data.get('versionName'), 'buildId': data.get('buildId'),
            'dlcs': [{'id': str(d.get('id')), 'title': d.get('title')} for d in data.get('dlcs') or []],
            'fetched': time.time()}


def latest_build(g, platform='windows'):
    """Newest build id on the default branch (cached a few hours). None if unknown."""
    if platform != 'windows':
        return None
    with _lock:
        cache = read_json(paths.GOG_BUILDS, {}) or {}
        hit = cache.get(str(g))
    if hit and time.time() - hit.get('fetched', 0) < BUILDS_TTL:
        return hit.get('build_id')
    status, d = http_json(f'{CONTENT_SYSTEM}/products/{g}/os/{platform}/builds?generation=2', timeout=20)
    build = None
    if d and d.get('items'):
        items = d['items']
        build = next((b for b in items if b.get('branch') is None), items[0])
    entry = {'fetched': time.time(), 'build_id': build.get('build_id') if build else None,
             'version': build.get('version_name') if build else None}
    with _lock:
        cache = read_json(paths.GOG_BUILDS, {}) or {}
        cache[str(g)] = entry
        write_json(paths.GOG_BUILDS, cache)
    return entry['build_id']


def update_available(inst):
    if not inst or inst.get('platform') != 'windows' or not inst.get('build_id'):
        return False
    latest = latest_build(inst['id'])
    return bool(latest and str(latest) != str(inst['build_id']))


# ---------------------------------------------------------------- launching

def play_task(inst):
    """(executable, args, working_dir) for an installed game."""
    path = inst['install_path']
    if inst.get('platform') == 'linux':
        start = os.path.join(path, 'start.sh')
        if not os.path.exists(start):
            raise RuntimeError('start.sh not found - try Verify & repair')
        return start, [], path
    gi = read_game_info(path, inst['id'])
    if not gi:
        raise RuntimeError('goggame info file not found - try Verify & repair')
    tasks = gi.get('playTasks') or []
    task = next((t for t in tasks if t.get('isPrimary')), None) or next(
        (t for t in tasks if t.get('category') == 'game' and t.get('path')), None)
    if not task or not task.get('path'):
        raise RuntimeError('This GOG game has no launchable executable')
    exe = os.path.join(path, task['path'].replace('\\', '/'))
    exe = _case_insensitive(exe)
    args = task.get('arguments') or []
    if isinstance(args, str):
        import shlex
        args = shlex.split(args.replace('\\', '/'))
    wd = task.get('workingDir')
    wd = _case_insensitive(os.path.join(path, wd.replace('\\', '/'))) if wd else os.path.dirname(exe)
    return os.path.normpath(exe), args, os.path.normpath(wd)


def _case_insensitive(p):
    if os.path.exists(p):
        return p
    parts = p.split('/')
    cur = '/' if p.startswith('/') else ''
    for part in parts:
        if not part:
            continue
        cand = os.path.join(cur, part)
        if not os.path.exists(cand) and os.path.isdir(cur or '.'):
            match = next((n for n in os.listdir(cur or '.') if n.lower() == part.lower()), None)
            cand = os.path.join(cur, match) if match else cand
        cur = cand
    return cur


# ---------------------------------------------------------------- cloud saves

def _client_id(inst):
    if inst.get('client_id'):
        return inst['client_id']
    status, d = http_json(f'{CONTENT_SYSTEM}/products/{inst["id"]}/os/windows/builds?generation=2', timeout=20)
    try:
        link = d['items'][0]['link']
        st, body = http_get(link, timeout=30)
        meta = json.loads(zlib.decompress(body))
        cid = meta.get('clientId')
    except Exception:
        cid = None
    if cid:
        with _lock:
            data = installed()
            if key(inst['id']) in data:
                data[key(inst['id'])]['client_id'] = cid
                write_json(paths.GOG_INSTALLED, data)
    return cid


def cloud_locations(inst):
    """[{'name', 'location'}] for a Windows install, [] if the game has no GOG cloud saves."""
    if inst.get('platform') != 'windows':
        return []
    if 'cloud' in inst and time.time() - inst['cloud'].get('fetched', 0) < 7 * 86400:
        return inst['cloud'].get('locations') or []
    cid = _client_id(inst)
    locations = []
    if cid:
        status, d = http_json(f'https://remote-config.gog.com/components/galaxy_client/clients/{cid}'
                              '?component_version=2.0.45', timeout=20)
        cs = (((d or {}).get('content') or {}).get('Windows') or {}).get('cloudStorage') or {}
        if cs.get('enabled'):
            locations = cs.get('locations') or [{
                'name': '__default',
                'location': f'<?APPLICATION_DATA_LOCAL?>/GOG.com/Galaxy/Applications/{cid}/Storage/Shared/Files'}]
    with _lock:
        data = installed()
        if key(inst['id']) in data:
            data[key(inst['id'])]['cloud'] = {'fetched': time.time(), 'locations': locations}
            write_json(paths.GOG_INSTALLED, data)
    return locations


def resolve_location(location, inst, wine_root):
    if paths.IS_WINDOWS:
        # games run natively: saves live in the real user folders (wine_root is unused)
        kf = host.known_folder
        repl = {
            '<?INSTALL?>': inst['install_path'],
            '<?DOCUMENTS?>': kf('documents'),
            '<?SAVED_GAMES?>': kf('saved games'),
            '<?APPLICATION_DATA_LOCAL?>': kf('local'),
            '<?APPLICATION_DATA_LOCAL_LOW?>': kf('local_low'),
            '<?APPLICATION_DATA_ROAMING?>': kf('roaming'),
            '<?USER_HOME?>': kf('home'),
        }
    else:
        user = os.path.join(wine_root, 'drive_c', 'users', 'steamuser')
        repl = {
            '<?INSTALL?>': inst['install_path'],
            '<?DOCUMENTS?>': os.path.join(user, 'Documents'),
            '<?SAVED_GAMES?>': os.path.join(user, 'Saved Games'),
            '<?APPLICATION_DATA_LOCAL?>': os.path.join(user, 'AppData', 'Local'),
            '<?APPLICATION_DATA_LOCAL_LOW?>': os.path.join(user, 'AppData', 'LocalLow'),
            '<?APPLICATION_DATA_ROAMING?>': os.path.join(user, 'AppData', 'Roaming'),
            '<?USER_HOME?>': user,
        }
    out = location
    for k, v in repl.items():
        out = out.replace(k, v)
    return os.path.normpath(out.replace('\\', '/'))


def sync_saves(inst, wine_root, direction, log_fn):
    """direction 'download' (before playing) or 'upload' (after). Returns True if anything ran."""
    locations = cloud_locations(inst)
    if not locations:
        return False
    state = read_json(paths.GOG_SAVES, {}) or {}
    gstate = state.get(inst['id']) or {}
    for loc in locations:
        name = loc.get('name') or '__default'
        path = resolve_location(loc.get('location', ''), inst, wine_root)
        ts = gstate.get(name, 0)
        args = ['save-sync', path, inst['id'], '--os', 'windows', '--ts', str(ts), '--name', name,
                '--skip-upload' if direction == 'download' else '--skip-download']
        log_fn(f'GOG cloud saves ({direction}) - {name}: {path}')
        rc, out, err = run(args, timeout=300)
        log_fn((err or '').strip()[-1200:] or f'save-sync exit {rc}')
        new_ts = (out or '').strip().splitlines()[-1:] or ['']
        try:
            gstate[name] = float(new_ts[0])
        except ValueError:
            pass
    state[inst['id']] = gstate
    write_json(paths.GOG_SAVES, state)
    return True
