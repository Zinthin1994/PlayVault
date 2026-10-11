"""App settings, per-game preferences and playtime."""
import os
import threading
import time

from . import paths
from .util import read_json, write_json

_lock = threading.RLock()

DEFAULT_SETTINGS = {
    'install_dir': paths.DEFAULT_GAMES_DIR,
    'default_proton': 'GE-Proton',     # path to a Proton build, 'GE-Proton' or 'UMU-Proton' (auto-download)
    'cloud_saves': True,
    'install_dlc': True,
    'max_workers': 0,                  # 0 = legendary default
    'auto_add_steam': True,
    'remove_steam_on_uninstall': True,
    'delete_prefix_on_uninstall': False,
    'hide_while_playing': True,
    'skip_update_check': False,
    'background_downloads': True,
    'ratings_enabled': True,
    'country': 'us',
    'show_hidden': False,
    'sort': 'title',
    'filter': 'all',
    'card_size': 'medium',
    'accent': '#4f8cff',
    'gog_language': 'en-US',
    'reviews_language': 'english',     # Steam review score language ('all' = every language)
    'gog_prefer_linux': False,         # GOG games with both builds: native Linux instead of Windows + Proton
    'app_updates': 'auto',             # PlayVault updates: 'auto' (download, install on next start), 'ask', 'off'
}

DEFAULT_GAME = {
    'favorite': False,
    'hidden': False,
    'proton': '',          # '' = use default
    'args': '',
    'env': {},
    'offline': False,
    'cloud_saves': None,   # None = use default
    'proton_log': False,
    'steam_appid': None,   # manual Steam match override
    'steam_shortcut': None,  # shortcut appid once added to Steam
    'umu_gameid': '',
}


def get_settings():
    with _lock:
        s = dict(DEFAULT_SETTINGS)
        s.update(read_json(paths.SETTINGS, {}) or {})
        # EpicVault stored the Epic folder itself (~/Games/Epic); PlayVault stores the games root
        if os.path.basename(s['install_dir'].rstrip('/')) == 'Epic':
            s['install_dir'] = os.path.dirname(s['install_dir'].rstrip('/'))
        return s


def update_settings(patch):
    with _lock:
        cur = read_json(paths.SETTINGS, {}) or {}
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                cur[k] = v
        write_json(paths.SETTINGS, cur)
        return get_settings()


def all_games():
    with _lock:
        return read_json(paths.GAMES, {}) or {}


def get_game(app_name, data=None):
    data = all_games() if data is None else data
    g = dict(DEFAULT_GAME)
    g.update(data.get(app_name, {}))
    return g


def update_game(app_name, patch):
    with _lock:
        data = all_games()
        cur = data.get(app_name, {})
        for k, v in patch.items():
            if k in DEFAULT_GAME:
                cur[k] = v
        data[app_name] = cur
        write_json(paths.GAMES, data)
        return get_game(app_name, data)


def _pt_path(app_name):
    return os.path.join(paths.PLAYTIME, f'{app_name}.json')


def get_playtime(app_name):
    return read_json(_pt_path(app_name), None) or {'total': 0, 'last_played': 0, 'sessions': 0}


def add_playtime(app_name, seconds):
    pt = get_playtime(app_name)
    pt['total'] = int(pt.get('total', 0) + max(0, seconds))
    pt['last_played'] = int(time.time())
    pt['sessions'] = pt.get('sessions', 0) + 1
    write_json(_pt_path(app_name), pt)
    return pt


def all_playtime():
    out = {}
    try:
        for f in os.listdir(paths.PLAYTIME):
            if f.endswith('.json'):
                out[f[:-5]] = read_json(os.path.join(paths.PLAYTIME, f), {})
    except FileNotFoundError:
        pass
    return out
