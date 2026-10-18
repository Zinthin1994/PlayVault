"""Thin wrapper around the legendary CLI (the open-source Epic Games Store client).

We call the CLI rather than importing it so a legendary update can never break
our process, and read its on-disk JSON (metadata/, installed.json, user.json)
directly for fast, offline-capable library views.
"""
import configparser
import json
import os
import shutil
import subprocess
import threading
import time

from . import host, paths
from .util import read_json, write_json, tail, log

_meta_cache = {}   # app_name -> (mtime, data)
_meta_lock = threading.Lock()
_cfg_lock = threading.Lock()


def binary():
    if os.path.exists(paths.LEGENDARY_BIN):
        return paths.LEGENDARY_BIN
    return shutil.which('legendary')


def available():
    return binary() is not None


def env():
    e = os.environ.copy()
    e['LEGENDARY_CONFIG_PATH'] = paths.LEGENDARY_CONFIG
    e['PYTHONUNBUFFERED'] = '1'
    return e


def run(args, timeout=600, yes=True):
    b = binary()
    if not b:
        return 127, '', 'legendary is not installed'
    cmd = [b] + (['-y'] if yes else []) + list(args)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=env(), stdin=subprocess.DEVNULL, **host.hidden_kwargs())
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, '', f'legendary {args[0]} timed out'


def popen(args):
    b = binary()
    if not b:
        raise RuntimeError('legendary is not installed')
    return subprocess.Popen([b, '-y'] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, env=env(), stdin=subprocess.DEVNULL,
                            **host.spawn_kwargs())


def version():
    rc, out, err = run(['--version'], timeout=30, yes=False)
    return (out or err).strip().splitlines()[-1] if rc == 0 and (out or err).strip() else None


# ---------------------------------------------------------------- auth

def user():
    data = read_json(os.path.join(paths.LEGENDARY_CONFIG, 'user.json'), None)
    if isinstance(data, dict) and data.get('displayName'):
        return data['displayName']
    return None


def login_with_code(code):
    code = (code or '').strip()
    if code.startswith('{'):
        try:
            code = json.loads(code).get('authorizationCode', '')
        except ValueError:
            pass
    code = code.strip().strip('"')
    if not code:
        return False, 'No authorization code given'
    rc, out, err = run(['auth', '--code', code], timeout=90)
    name = user()
    if name:
        return True, name
    return False, tail(err or out) or 'Login failed'


def logout():
    run(['auth', '--delete'], timeout=60)


# ---------------------------------------------------------------- library

def refresh_library(force=False):
    args = ['list', '--json']
    if force:
        args.append('--force-refresh')
    rc, out, err = run(args, timeout=1200)
    if rc != 0 or not out.strip():
        raise RuntimeError(tail(err) or 'legendary list failed')
    data = json.loads(out)
    games = []
    for g in data:
        games.append({
            'app_name': g.get('app_name'),
            'dlcs': [d.get('app_name') for d in (g.get('dlcs') or []) if d.get('app_name')],
        })
    write_json(paths.LIBRARY, {'updated': time.time(), 'games': games})
    return games


def library_index():
    return read_json(paths.LIBRARY, None) or {'updated': 0, 'games': []}


def metadata(app_name):
    path = os.path.join(paths.LEGENDARY_CONFIG, 'metadata', f'{app_name}.json')
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _meta_lock:
        hit = _meta_cache.get(app_name)
        if hit and hit[0] == mtime:
            return hit[1]
    data = read_json(path, None)
    with _meta_lock:
        _meta_cache[app_name] = (mtime, data)
    return data


def installed():
    return read_json(os.path.join(paths.LEGENDARY_CONFIG, 'installed.json'), None) or {}


def latest_version(app_name, platform='Windows'):
    meta = metadata(app_name) or {}
    return ((meta.get('asset_infos') or {}).get(platform) or {}).get('build_version')


def sizes(app_name):
    """Download/install size from the manifest (needs network, a few seconds)."""
    rc, out, err = run(['info', app_name, '--json'], timeout=240)
    if rc != 0 or not out.strip():
        raise RuntimeError(tail(err) or 'legendary info failed')
    j = json.loads(out)
    m = j.get('manifest') or {}
    return {'disk': m.get('disk_size'), 'download': m.get('download_size'), 'fetched': time.time()}


# ---------------------------------------------------------------- per-app config

def set_app_config(app_name, values):
    """Write keys into legendary's config.ini [app_name] section (used for save-sync prefix)."""
    path = os.path.join(paths.LEGENDARY_CONFIG, 'config.ini')
    with _cfg_lock:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        try:
            cp.read(path, encoding='utf-8')
        except configparser.Error as e:
            log.warning('Could not parse legendary config.ini: %r', e)
            return
        if not cp.has_section(app_name):
            cp.add_section(app_name)
        for k, v in values.items():
            cp.set(app_name, k, str(v))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            cp.write(f)
