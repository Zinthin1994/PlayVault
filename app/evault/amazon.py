"""Amazon Games support: wraps nile (imLinguin/nile, the open-source Amazon Games client).

Amazon games are keyed as 'amazon_<productId>' everywhere in PlayVault so they can never
collide with Epic app names or GOG keys. nile does the sign-in, library sync, downloads,
updates, verification and uninstalling; we call its CLI (never import it) and read its
on-disk JSON for fast, offline library views.

nile keeps its files in data/amazon/nile/ (NILE_CONFIG_PATH=data/amazon):
    current_user.json   {name, user_id}
    library.json        owned games (entitlements)
    installed.json      [{id, version, path, size}]
PlayVault adds data/amazon/updates.json (cached list-updates) and login_pending.json
(the PKCE data of a sign-in in progress).
"""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse

from . import host, paths
from .util import read_json, write_json, tail, log

PREFIX = 'amazon_'
UPDATES_TTL = 6 * 3600
LOGIN_TTL = 3600

_lock = threading.RLock()


def key(pid):
    return f'{PREFIX}{pid}'


def is_amazon(app_name):
    return str(app_name).startswith(PREFIX)


def pid(app_name):
    return str(app_name)[len(PREFIX):]


# ---------------------------------------------------------------- nile process

def binary():
    if os.path.exists(paths.NILE_BIN):
        return paths.NILE_BIN
    return shutil.which('nile')


def available():
    return binary() is not None


def env():
    e = os.environ.copy()
    e['NILE_CONFIG_PATH'] = paths.AMAZON
    e['PYTHONUNBUFFERED'] = '1'
    e['PYTHONIOENCODING'] = 'utf-8'
    for k in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        e.pop(k, None)
    return e


def run(args, timeout=600):
    b = binary()
    if not b:
        return 127, '', 'nile (Amazon Games client) is not installed - re-run the installer'
    try:
        p = subprocess.run([b] + list(args), capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=timeout, env=env(), stdin=subprocess.DEVNULL, **host.hidden_kwargs())
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, '', f'nile {args[0]} timed out'


def popen(args):
    b = binary()
    if not b:
        raise RuntimeError('nile (Amazon Games client) is not installed - re-run the installer')
    return subprocess.Popen([b] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', bufsize=1, env=env(),
                            stdin=subprocess.DEVNULL, **host.spawn_kwargs())


NILE_URL = 'https://github.com/imLinguin/nile/archive/e9ca6ccb8c2a46f1adc7eea8eaba1a3017d38d58.zip'
# ^ nile 1.2.0 + setuptools fix, just before upstream deleted a module it still imports (2026-10-29).
# The v1.2.0 tag itself does not build (flat-layout error) and main crashes on start.
_ensure_lock = threading.Lock()


def ensure_installed():
    """Self-updates only swap app/, never run the installer, so an install that predates
    Amazon support gets nile here, into its own venv. Returns True if nile is usable."""
    with _ensure_lock:
        broken = False
        if available():
            if version():
                return True
            broken = True  # present but crashes on start (e.g. the broken upstream main build)
        py = paths.VENV_PYTHON
        if not os.path.exists(py):
            return False
        log.info('%s nile (Amazon Games client) in the PlayVault venv', 'Repairing' if broken else 'Installing')
        try:
            p = subprocess.run([py, '-m', 'pip', 'install', '--quiet', '--upgrade'] +
                               (['--force-reinstall', '--no-deps'] if broken else []) + [NILE_URL],
                               capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
                               **host.hidden_kwargs())
            if p.returncode != 0:
                log.warning('nile install failed: %s', tail(p.stderr or p.stdout, 400))
        except Exception as e:
            log.warning('nile install failed: %r', e)
        return available()


def version():
    rc, out, err = run(['--version'], timeout=30)
    return (out or err).strip().splitlines()[-1] if rc == 0 and (out or err).strip() else None


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

def user():
    data = read_json(paths.AMAZON_USER, None)
    if isinstance(data, dict) and data.get('user_id'):
        return data.get('name') or 'Amazon user'
    return None


def login_url():
    """Start a sign-in: nile makes the PKCE data + login URL, we remember the data for the code."""
    rc, out, err = run(['auth', '--login', '--non-interactive'], timeout=60)
    data = _last_json(out)
    if not isinstance(data, dict) or not data.get('url'):
        if rc == 0 and user():
            raise RuntimeError('Already signed in to Amazon Games')
        raise RuntimeError(tail(err or out, 300) or 'Could not start the Amazon sign-in')
    write_json(paths.AMAZON_LOGIN, {'created': time.time(), 'client_id': data['client_id'],
                                    'code_verifier': data['code_verifier'], 'serial': data['serial']})
    return data['url']


def extract_code(text):
    """The authorization code from the amazon.com address you land on after signing in
    (a bare code is accepted too)."""
    text = (text or '').strip().strip('"')
    if 'authorization_code=' in text:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        return (q.get('openid.oa2.authorization_code') or [''])[0]
    return text


def login_with_code(code):
    code = extract_code(code)
    if not code:
        return False, 'No authorization code given'
    pend = read_json(paths.AMAZON_LOGIN, None)
    if not pend or time.time() - pend.get('created', 0) > LOGIN_TTL:
        return False, 'The sign-in expired - start it again'
    rc, out, err = run(['register', '--code', code, '--client-id', pend['client_id'],
                        '--code-verifier', pend['code_verifier'], '--serial', pend['serial']], timeout=300)
    name = user()
    if not name:
        return False, tail(err or out, 300) or 'Amazon sign-in failed - the code may have expired, try again'
    try:
        os.remove(paths.AMAZON_LOGIN)
    except OSError:
        pass
    return True, name


def logout():
    run(['auth', '--logout'], timeout=60)
    if user():  # deregistering failed (offline?): at least forget the account locally
        for p in (paths.AMAZON_USER,):
            try:
                os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------- library

def refresh_library(force=False):
    if not user():
        return []
    args = ['library', 'sync'] + (['--force'] if force else [])
    rc, out, err = run(args, timeout=900)
    if rc != 0:
        raise RuntimeError(tail(err or out, 300) or 'nile library sync failed')
    return library_index()


def _library_raw():
    data = read_json(paths.AMAZON_LIBRARY, None)
    return data if isinstance(data, list) else []


def _details(g):
    return ((g.get('product') or {}).get('productDetail') or {}).get('details') or {}


def library_index():
    """[{id, title, developer, publisher, icon, logo, background, crown, genres, description, release_date}]"""
    out = []
    for g in _library_raw():
        p = g.get('product') or {}
        if not p.get('id'):
            continue
        d = _details(g)
        pd = p.get('productDetail') or {}
        out.append({
            'id': p['id'],
            'title': p.get('title') or p['id'],
            'developer': d.get('developer'),
            'publisher': d.get('publisher'),
            'icon': pd.get('iconUrl'),
            'logo': d.get('logoUrl'),
            'background': d.get('backgroundUrl1') or d.get('backgroundUrl2'),
            'crown': d.get('pgCrownImageUrl'),
            'genres': (d.get('genres') or [])[:4],
            'description': d.get('shortDescription'),
            'release_date': (d.get('releaseDate') or '')[:10] or None,
        })
    out.sort(key=lambda e: e['title'].lower())
    return out


def library_entry(p):
    for e in library_index():
        if e['id'] == str(p):
            return e
    return None


# ---------------------------------------------------------------- installs

def installed():
    """{'amazon_<id>': {id, version, path, size, install_path, install_size, title}}"""
    data = read_json(paths.AMAZON_INSTALLED, None)
    out = {}
    if not isinstance(data, list):
        return out
    titles = {e['id']: e['title'] for e in library_index()}
    for i in data:
        if not isinstance(i, dict) or not i.get('id'):
            continue
        out[key(i['id'])] = {
            'id': i['id'], 'version': i.get('version'), 'path': i.get('path'),
            'install_path': i.get('path'), 'install_size': i.get('size') or 0,
            'title': titles.get(i['id']) or i['id'], 'platform': 'windows',
        }
    return out


def set_install_path(p, new_path):
    """Used after moving a game's folder: nile has no move command."""
    with _lock:
        data = read_json(paths.AMAZON_INSTALLED, None)
        if not isinstance(data, list):
            return
        for i in data:
            if i.get('id') == str(p):
                i['path'] = new_path
        write_json(paths.AMAZON_INSTALLED, data)


def info(p):
    """Download size via nile install --info --json (network, a few seconds)."""
    rc, out, err = run(['install', str(p), '--info', '--json'], timeout=240)
    data = _last_json(out)
    if not isinstance(data, dict):
        raise RuntimeError(tail(err, 300) or 'Could not read game info from Amazon')
    dl = data.get('download_size') or 0
    return {'download': dl, 'disk': dl, 'fetched': time.time()}


def _updates_cache():
    return read_json(paths.AMAZON_UPDATES, None) or {'fetched': 0, 'ids': []}


def refresh_updates(force=False):
    """Ids of installed games with a newer version (cached a few hours)."""
    with _lock:
        c = _updates_cache()
        if not force and time.time() - c.get('fetched', 0) < UPDATES_TTL:
            return c['ids']
    if not user() or not installed():
        return []
    rc, out, err = run(['list-updates', '--json'], timeout=120)
    ids = _last_json(out)
    if rc != 0 or not isinstance(ids, list):
        log.warning('Amazon update check failed: %s', tail(err, 300))
        return _updates_cache()['ids']
    with _lock:
        write_json(paths.AMAZON_UPDATES, {'fetched': time.time(), 'ids': ids})
    return ids


def update_available(inst):
    """Cached answer only (the cache is refreshed in the background at startup)."""
    return bool(inst and inst.get('id') in _updates_cache()['ids'])


def clear_update(p):
    with _lock:
        c = _updates_cache()
        c['ids'] = [i for i in c['ids'] if i != str(p)]
        write_json(paths.AMAZON_UPDATES, c)


# ---------------------------------------------------------------- launching

def launch_info(p):
    """nile's dry run: (exe, args, cwd, env) of an installed game."""
    rc, out, err = run(['launch', str(p), '--json', '--no-wine'], timeout=120)
    data = _last_json(out)
    if not isinstance(data, dict) or 'command' not in data:
        raise RuntimeError(tail(err, 300) or 'Could not get launch parameters (is the game installed?)')
    cmd = data['command']
    exe = cmd.get('instruction')
    if not exe or not os.path.exists(exe):
        raise RuntimeError('The game executable was not found - try Verify & repair')
    return exe, list(cmd.get('arguments') or []), data.get('game_directory') or os.path.dirname(exe), \
        dict(data.get('env') or {})
