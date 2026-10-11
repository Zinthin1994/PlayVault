"""umu game-id lookup so protonfixes apply the right per-game fixes."""
import os
import shutil
import time

from . import paths
from .util import read_json, write_json, http_json

API = 'https://umu.openwinecomponents.org/umu_api.php?store={store}'


def umu_run():
    if os.path.exists(paths.UMU_RUN):
        return paths.UMU_RUN
    return shutil.which('umu-run')


def _db(store):
    path = paths.UMU_DB if store == 'egs' else paths.UMU_DB.replace('.json', f'_{store}.json')
    db = read_json(path, None)
    if not db or time.time() - db.get('fetched', 0) > 3 * 86400:
        status, data = http_json(API.format(store=store), timeout=20)
        if isinstance(data, list):
            db = {'fetched': time.time(),
                  'ids': {str(d.get('codename', '')).lower(): d.get('umu_id')
                          for d in data if d.get('codename') and d.get('umu_id')}}
            write_json(path, db)
    return (db or {}).get('ids') or {}


def game_id(codename, steam_appid=None, store='egs'):
    """codename: Epic app name, or the GOG product id for store='gog'."""
    hit = _db(store).get(str(codename).lower())
    if hit:
        return hit
    if steam_appid:
        return f'umu-{steam_appid}'
    return 'umu-default'
