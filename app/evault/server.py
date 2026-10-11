"""PlayVault local API + static web UI server (127.0.0.1 only)."""
import json
import logging
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import VERSION, gog, legendary, paths, proton, ratings as ratings_mod, steam, store, system, umu
from .jobs import JobManager
from .updater import Updater
from .util import read_json, write_json, pid_alive, log

IDLE_EXIT_SECONDS = 150
STEAM_CDN = 'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps'
ALLOWED_HOSTS = {f'127.0.0.1:{paths.PORT}', f'localhost:{paths.PORT}'}
ALLOWED_ORIGINS = {f'http://{h}' for h in ALLOWED_HOSTS}


def epic_img(url, w=None, h=None):
    if not url:
        return None
    if w and 'epicgames.com' in url and '?' not in url:
        return f'{url}?w={w}&h={h}&resize=1&quality=high'
    return url


def key_images(md):
    imgs = {}
    for ki in md.get('keyImages', []) or []:
        imgs.setdefault(ki.get('type'), ki.get('url'))
    return imgs


def steam_art(sid, name):
    return f'{STEAM_CDN}/{sid}/{name}' if sid else None


class App:
    def __init__(self):
        paths.ensure()
        self.version = 1
        self.lock = threading.RLock()
        self.last_request = time.time()
        self.library_status = {'refreshing': False, 'error': None,
                               'updated': legendary.library_index().get('updated', 0)}
        self.steam_cache = (0, {})
        self.legendary_version = None
        self.gogdl_version = None
        self.ratings = ratings_mod.Ratings(on_update=self.bump)
        s = store.get_settings()
        self.ratings.country = s.get('country', 'us')
        self.ratings.lang = s.get('reviews_language', 'english')
        self.ratings.enabled = s.get('ratings_enabled', True)
        self.jobs = JobManager(on_change=self.bump, on_done=self.on_job_done)
        self.updater = Updater(on_change=self.bump)
        self.updater.start()
        threading.Thread(target=self._startup, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def bump(self):
        with self.lock:
            self.version += 1

    # ------------------------------------------------------------ background
    def _startup(self):
        self.legendary_version = legendary.version()
        self.gogdl_version = gog.version()
        threading.Thread(target=self._gog_check_updates, daemon=True).start()
        stale = False
        if legendary.user():
            idx = legendary.library_index()
            stale |= not idx['games'] or time.time() - idx.get('updated', 0) > 6 * 3600
        if gog.user():
            idx = gog.library_index()
            stale |= not idx['games'] or time.time() - idx.get('updated', 0) > 6 * 3600
        if stale:
            self.refresh_library()
        else:
            self.queue_ratings()
            threading.Thread(target=gog.fill_gamesdb, kwargs={'on_progress': self._gog_art_done},
                             daemon=True).start()

    def _watchdog(self):
        while True:
            time.sleep(15)
            idle = time.time() - self.last_request
            if idle > IDLE_EXIT_SECONDS and not self.jobs.busy():
                log.info('Idle with no jobs, exiting')
                os._exit(0)

    def _gog_art_done(self):
        self.queue_ratings()
        self.bump()

    def _gog_check_updates(self):
        for i in gog.installed().values():
            try:
                gog.latest_build(i['id'], i.get('platform', 'windows'))
            except Exception as e:
                log.warning('GOG update check for %s failed: %r', i.get('id'), e)
        self.bump()

    def refresh_library(self, force=False):
        with self.lock:
            if self.library_status['refreshing']:
                return
            self.library_status.update(refreshing=True, error=None)
        self.bump()
        errors = []
        if legendary.user():
            try:
                legendary.refresh_library(force)
            except Exception as e:
                log.warning('Epic library refresh failed: %r', e)
                errors.append(f'Epic: {e}')
        if gog.user():
            try:
                gog.refresh_library()
            except Exception as e:
                log.warning('GOG library refresh failed: %r', e)
                errors.append(f'GOG: {e}')
        self.library_status.update(refreshing=False, updated=time.time(), error='; '.join(errors) or None)
        self.bump()
        self.queue_ratings()
        if gog.user():
            gog.fill_gamesdb(on_progress=self._gog_art_done)

    def queue_ratings(self, force=False):
        if not store.get_settings().get('ratings_enabled', True):
            return
        games = store.all_games()
        inst = legendary.installed()
        ginst = gog.installed()
        items = []
        for e in legendary.library_index()['games']:
            app = e['app_name']
            meta = legendary.metadata(app) or {}
            md = meta.get('metadata') or {}
            items.append((app in inst, app, meta.get('app_title') or md.get('title') or app, md.get('namespace'), None))
        db = gog.gamesdb_all()
        for e in self._gog_entries():
            app = gog.key(e['id'])
            gdb = db.get(e['id']) or {}
            if not gdb.get('fetched'):
                continue  # wait for the GOG catalog lookup; it usually knows the exact Steam id
            items.append((app in ginst, app, e['title'], None, gdb.get('steam_appid')))
        # installed games first so the stuff you play gets ratings first
        items.sort(key=lambda x: not x[0])
        for _, app, title, ns, hint in items:
            g = store.get_game(app, games)
            self.ratings.request(app, title, ns, force=force, steam_appid=g.get('steam_appid'), hint=hint)

    def on_job_done(self, job):
        app = job['app_name']
        if job['status'] != 'done':
            return
        s = store.get_settings()
        if job['kind'] == 'install':
            if s.get('auto_add_steam'):
                try:
                    self.steam_add([app])
                except Exception as e:
                    log.warning('Auto add to Steam failed: %r', e)
            if gog.is_gog(app):
                inst = gog.installed().get(app)
                if inst:
                    threading.Thread(target=gog.cloud_locations, args=(inst,), daemon=True).start()
        elif job['kind'] == 'uninstall':
            if s.get('remove_steam_on_uninstall') and app in self.steam_shortcuts():
                self.steam_remove([app])
        self.bump()

    # ------------------------------------------------------------ store helpers
    @staticmethod
    def _gog_entries():
        """Owned GOG games, plus installed ones (still playable after signing out)."""
        lib = gog.library_index()['games']
        seen = {e['id'] for e in lib}
        out = list(lib)
        for app, i in gog.installed().items():
            if i['id'] not in seen:
                out.append({'id': i['id'], 'title': i.get('title') or i['id'], 'image': None,
                            'platforms': [i.get('platform', 'windows')]})
        return out

    def installed_any(self, app):
        return app in (gog.installed() if gog.is_gog(app) else legendary.installed())

    def title(self, app):
        if gog.is_gog(app):
            i = gog.installed().get(app) or {}
            e = gog.library_entry(gog.gid(app)) or {}
            return e.get('title') or i.get('title') or app
        meta = legendary.metadata(app) or {}
        return meta.get('app_title') or app

    # ------------------------------------------------------------ steam
    def steam_shortcuts(self, fresh=False):
        t, data = self.steam_cache
        if fresh or time.time() - t > 10:
            data = steam.list_ours()
            self.steam_cache = (time.time(), data)
        return data

    def steam_state(self):
        return self.steam_shortcuts(), steam.pending()

    def _maybe_apply(self):
        if not steam.steam_running():
            try:
                steam.apply_pending_now()
            except Exception as e:
                log.warning('Applying Steam shortcuts failed: %r', e)
            self.steam_shortcuts(fresh=True)

    def steam_add(self, apps):
        inst = legendary.installed()
        ginst = gog.installed()
        rat = self.ratings.snapshot()
        games = store.all_games()
        added = []
        for app in apps:
            g = store.get_game(app, games)
            sid = g.get('steam_appid') or (rat.get(app) or {}).get('steam_appid')
            if gog.is_gog(app):
                if app not in ginst:
                    continue
                gid = gog.gid(app)
                entry = gog.library_entry(gid) or {}
                db = gog.gamesdb(gid)
                sid = sid or db.get('steam_appid')
                title = entry.get('title') or ginst[app].get('title') or app
                fallbacks = steam.gog_art(db, entry.get('image'))
            else:
                if app not in inst:
                    continue
                meta = legendary.metadata(app) or {}
                title = meta.get('app_title') or inst[app].get('title') or app
                fallbacks = steam.epic_art(meta)
            appid = steam.stage_add(app, title, fallbacks, sid)
            store.update_game(app, {'steam_shortcut': appid})
            added.append(app)
        self._maybe_apply()
        self.bump()
        return added

    def steam_remove(self, apps):
        for app in apps:
            steam.stage_remove(app)
            store.update_game(app, {'steam_shortcut': None})
        self._maybe_apply()
        self.bump()

    # ------------------------------------------------------------ running game
    def running(self):
        r = read_json(paths.RUNNING, None)
        if r and pid_alive(r.get('pid')):
            return r
        if r:
            try:
                os.remove(paths.RUNNING)
            except OSError:
                pass
        return None

    def launch(self, app):
        if self.running():
            raise ValueError('A game is already running')
        if os.name == 'nt':
            raise ValueError('Launching games only works on the Steam Machine (Linux)')
        if not self.installed_any(app):
            raise ValueError('Game is not installed')
        logf = open(os.path.join(paths.LOGS, 'launch_spawn.log'), 'a')
        subprocess.Popen([paths.LAUNCH_SCRIPT, app], stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=paths.ROOT)
        write_json(paths.RUNNING, {'app_name': app, 'pid': os.getpid(), 'started': time.time(), 'stage': 'spawning'})
        threading.Timer(20, self._clear_spawn_marker, args=(app,)).start()
        self.bump()

    def _clear_spawn_marker(self, app):
        r = read_json(paths.RUNNING, None)
        if r and r.get('stage') == 'spawning' and r.get('pid') == os.getpid():
            try:
                os.remove(paths.RUNNING)
            except OSError:
                pass
            self.bump()

    def stop(self):
        r = self.running()
        if r and r.get('pid') != os.getpid():
            try:
                os.kill(int(r['pid']), signal.SIGTERM)
            except OSError:
                pass

    # ------------------------------------------------------------ library views
    def _common(self, app, title, inst, g, r, pt, job, ours, pend, sid, detail):
        reviews = r.get('reviews') or {}
        pdb = r.get('protondb') or {}
        rec = {
            'app_name': app,
            'title': title,
            'installed': bool(inst),
            'install_size': (inst or {}).get('install_size') or 0,
            'favorite': g['favorite'],
            'hidden': g['hidden'],
            'last_played': pt.get('last_played', 0),
            'playtime': pt.get('total', 0),
            'steam_appid': sid,
            'rating': {'desc': reviews.get('desc'), 'pct': reviews.get('pct'), 'total': reviews.get('total'),
                       'score': reviews.get('score'),
                       'sdb': ratings_mod.steamdb_rating(reviews.get('positive') or 0, reviews.get('total') or 0)}
                      if reviews else None,
            'ratings_status': r.get('status') or ('pending' if not r else None),
            'protondb': pdb.get('tier'),
            'deck': (r.get('deck') or {}).get('label'),
            'anticheat': (r.get('anticheat') or {}).get('status'),
            'in_steam': (app in ours or app in pend.get('add', {})) and app not in pend.get('remove', []),
            'job': {k: job[k] for k in ('id', 'kind', 'status', 'progress', 'message')} if job else None,
        }
        if detail:
            d = r.get('details') or {}
            rec.update({
                'reviews': reviews or None,
                'protondb_full': pdb or None,
                'deck_full': r.get('deck'),
                'anticheat_full': r.get('anticheat'),
                'genres': d.get('genres') or [],
                'metacritic': d.get('metacritic'),
                'release_date': d.get('release_date'),
                'steam_name': r.get('steam_name'),
                'match_score': r.get('match_score'),
                'match_manual': bool(g.get('steam_appid')),
                'ratings_fetched': r.get('fetched'),
                'settings': g,
                'sessions': pt.get('sessions', 0),
                'last_launch': read_json(os.path.join(paths.LOGS, f'{app}.status.json'), None),
                'sizes': (read_json(paths.SIZES, {}) or {}).get(app),
                'steam_pending': app in pend.get('add', {}) or app in pend.get('remove', []),
                'proton_name': proton.display_name(g['proton'] or store.get_settings()['default_proton']),
                'install_path': (inst or {}).get('install_path'),
            })
        return rec

    def _epic_record(self, app, meta, inst, g, r, pt, job, ours, pend, detail=False):
        md = meta.get('metadata') or {}
        imgs = key_images(md)
        title = meta.get('app_title') or md.get('title') or (inst or {}).get('title') or app
        latest = ((meta.get('asset_infos') or {}).get('Windows') or {}).get('build_version')
        sid = g.get('steam_appid') or r.get('steam_appid')
        attrs = md.get('customAttributes') or {}
        third = (attrs.get('ThirdPartyManagedApp') or {}).get('value') or \
                (attrs.get('ThirdPartyManagedProvider') or {}).get('value')
        tall = imgs.get('DieselGameBoxTall') or imgs.get('OfferImageTall') or imgs.get('Thumbnail')
        wide = imgs.get('DieselGameBox') or imgs.get('DieselStoreFrontWide') or imgs.get('OfferImageWide')
        rec = self._common(app, title, inst, g, r, pt, job, ours, pend, sid, detail)
        rec.update({
            'store': 'epic',
            'developer': md.get('developer'),
            'cover': epic_img(tall, 400, 533) or steam_art(sid, 'library_600x900_2x.jpg'),
            'cover_fallback': (sid and f'https://cdn.cloudflare.steamstatic.com/steam/apps/{sid}/library_600x900.jpg') if tall
                              else steam_art(sid, 'header.jpg'),
            'update_available': bool(inst and latest and inst.get('version') != latest),
            'third_party': third,
        })
        if detail:
            rec.update({
                'description': md.get('description'),
                'publisher': md.get('publisher') or ((md.get('releaseInfo') or [{}])[0]).get('publisher'),
                'hero': steam_art(sid, 'library_hero.jpg'),
                'hero_fallback': epic_img(wide, 1920, 1080),
                'logo': steam_art(sid, 'logo.png') or imgs.get('DieselGameBoxLogo'),
                'logo_fallback': imgs.get('DieselGameBoxLogo'),
                'wide': epic_img(wide, 960, 540),
                'version': (inst or {}).get('version'),
                'latest_version': latest,
                'can_run_offline': (inst or {}).get('can_run_offline'),
                'cloud_saves': 'CloudSaveFolder' in attrs,
                'platforms': ['windows'],
                'platform': 'windows' if inst else None,
            })
        return rec

    def _gog_record(self, e, inst, db, g, r, pt, job, ours, pend, detail=False):
        app = gog.key(e['id'])
        title = e.get('title') or (inst or {}).get('title') or db.get('title') or app
        sid = g.get('steam_appid') or r.get('steam_appid') or db.get('steam_appid')
        rec = self._common(app, title, inst, g, r, pt, job, ours, pend, sid, detail)
        rec.update({
            'store': 'gog',
            'developer': ', '.join(db.get('developers') or []) or None,
            'cover': db.get('vertical') or steam_art(sid, 'library_600x900_2x.jpg') or e.get('image'),
            'cover_fallback': steam_art(sid, 'library_600x900_2x.jpg') if db.get('vertical') else
                              (steam_art(sid, 'header.jpg') or e.get('image')),
            'update_available': bool(inst and gog.update_available(inst)) if detail else
                                bool(inst and inst.get('platform') == 'windows' and self._gog_update_cached(inst)),
            'third_party': None,
            'platforms': e.get('platforms') or ['windows'],
        })
        if detail:
            cloud = (inst or {}).get('cloud') or {}
            rec.update({
                'description': db.get('summary'),
                'publisher': ', '.join(db.get('publishers') or []) or None,
                'hero': db.get('background') or steam_art(sid, 'library_hero.jpg'),
                'hero_fallback': steam_art(sid, 'library_hero.jpg') or e.get('image'),
                'logo': steam_art(sid, 'logo.png') or db.get('logo'),
                'logo_fallback': db.get('logo'),
                'wide': db.get('horizontal') or e.get('image'),
                'version': (inst or {}).get('version'),
                'latest_version': None,
                'can_run_offline': True,
                'cloud_saves': bool(cloud.get('locations')),
                'platform': (inst or {}).get('platform'),
                'genres': rec['genres'] or db.get('genres') or [],
                'release_date': rec['release_date'] or db.get('release_date'),
            })
        return rec

    @staticmethod
    def _gog_update_cached(inst):
        cache = read_json(paths.GOG_BUILDS, {}) or {}
        hit = cache.get(inst['id']) or {}
        return bool(hit.get('build_id') and inst.get('build_id') and str(hit['build_id']) != str(inst['build_id']))

    def library(self):
        inst = legendary.installed()
        ginst = gog.installed()
        games = store.all_games()
        rat = self.ratings.snapshot()
        pts = store.all_playtime()
        jobs = {}
        for j in self.jobs.list():
            if j['status'] in ('queued', 'running'):
                jobs[j['app_name']] = j
        ours, pend = self.steam_state()
        out = []
        epic_on = bool(legendary.user())
        for e in legendary.library_index()['games']:
            app = e['app_name']
            if not epic_on and app not in inst:
                continue  # signed out of Epic: only keep what's installed
            meta = legendary.metadata(app)
            if not meta:
                continue
            out.append(self._epic_record(app, meta, inst.get(app), store.get_game(app, games), rat.get(app) or {},
                                         pts.get(app) or {}, jobs.get(app), ours, pend))
        db = gog.gamesdb_all()
        for e in self._gog_entries():
            app = gog.key(e['id'])
            out.append(self._gog_record(e, ginst.get(app), db.get(e['id']) or {}, store.get_game(app, games),
                                        rat.get(app) or {}, pts.get(app) or {}, jobs.get(app), ours, pend))
        return out

    def detail(self, app):
        ours, pend = self.steam_state()
        if gog.is_gog(app):
            gid = gog.gid(app)
            e = next((x for x in self._gog_entries() if x['id'] == gid), None)
            if not e:
                return None
            inst = gog.installed().get(app)
            rec = self._gog_record(e, inst, gog.gamesdb(gid), store.get_game(app), self.ratings.get(app),
                                   store.get_playtime(app), self.jobs.active_for(app), ours, pend, detail=True)
            rec['dlcs'] = []
            return rec
        meta = legendary.metadata(app)
        if not meta:
            return None
        inst = legendary.installed()
        rec = self._epic_record(app, meta, inst.get(app), store.get_game(app), self.ratings.get(app),
                                store.get_playtime(app), self.jobs.active_for(app), ours, pend, detail=True)
        dlcs = next((e.get('dlcs') for e in legendary.library_index()['games'] if e['app_name'] == app), []) or []
        rec['dlcs'] = []
        for d in dlcs:
            dm = legendary.metadata(d) or {}
            rec['dlcs'].append({'app_name': d, 'title': dm.get('app_title') or d, 'installed': d in inst})
        return rec

    def sizes(self, app, refresh=False, platform=None):
        cache = read_json(paths.SIZES, {}) or {}
        ck = f'{app}:{platform}' if platform else app
        if ck in cache and not refresh:
            return cache[ck]
        if gog.is_gog(app):
            s = store.get_settings()
            info = gog.info(gog.gid(app), platform or 'windows', s.get('gog_language') or 'en-US',
                            bool(s.get('install_dlc')))
            sz = {'download': info['download'], 'disk': info['disk'], 'fetched': info['fetched']}
        else:
            sz = legendary.sizes(app)
        cache = read_json(paths.SIZES, {}) or {}
        cache[ck] = sz
        write_json(paths.SIZES, cache)
        return sz

    def state(self):
        s = store.get_settings()
        jobs = self.jobs.list()
        active = [j for j in jobs if j['status'] in ('queued', 'running')]
        epic_user = legendary.user()
        gog_user = gog.user()
        return {
            'version': self.version,
            'app_version': VERSION,
            'user': epic_user or gog_user,
            'accounts': {'epic': epic_user, 'gog': gog_user},
            'legendary': legendary.available(),
            'gogdl': gog.available(),
            'umu': bool(umu.umu_run()),
            'library': self.library_status,
            'ratings_pending': self.ratings.pending(),
            'running': self.running(),
            'jobs_active': len(active),
            'job_current': next((j for j in active if j['status'] == 'running'), None),
            'steam_pending': steam.pending_count(),
            'update': self.updater.snapshot(),
            'settings': s,
        }

    def busy_reason(self):
        """Why PlayVault can't restart right now (None if it can)."""
        if self.jobs.busy():
            return 'A download is in progress'
        if self.running():
            return 'A game is running'
        return None

    def system_info(self):
        uid, ud = steam.current_user()
        return {
            'app_version': VERSION,
            'legendary_version': self.legendary_version,
            'gogdl_version': self.gogdl_version,
            'umu_path': umu.umu_run(),
            'steam_root': proton.steam_root(),
            'steam_user': uid,
            'steam_running': steam.steam_running(),
            'game_mode': steam.in_game_mode(),
            'data_dir': paths.DATA,
            'python': sys.version.split()[0],
        }


APP = None


class Handler(BaseHTTPRequestHandler):
    server_version = 'PlayVault'

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------ helpers
    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return {}

    def _static(self, path):
        rel = path.lstrip('/') or 'index.html'
        full = os.path.realpath(os.path.join(paths.WEB, rel))
        if not full.startswith(os.path.realpath(paths.WEB)) or not os.path.isfile(full):
            full = os.path.join(paths.WEB, 'index.html')
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if full.endswith('.js'):
            ctype = 'application/javascript'
        with open(full, 'rb') as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _allowed(self, api):
        """Only PlayVault itself may use the API: its own window, the Electron shell and local
        scripts. Web pages open in a browser on the same machine must not be able to drive it.
        - Host must be our own address (defeats DNS rebinding).
        - Browsers send Origin / Sec-Fetch-Site on cross-site requests; those are refused.
          Local tools (curl, Python, Node) send neither and are allowed."""
        host = (self.headers.get('Host') or '').lower()
        if host not in ALLOWED_HOSTS:
            return False
        if not api:
            return True
        origin = self.headers.get('Origin')
        if origin is not None and origin.lower() not in ALLOWED_ORIGINS:
            return False
        site = (self.headers.get('Sec-Fetch-Site') or '').lower()
        return site in ('', 'same-origin', 'none')

    def _dispatch(self, method):
        url = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split('/') if p]
        query = dict(urllib.parse.parse_qsl(url.query))
        if not self._allowed(bool(parts) and parts[0] == 'api'):
            log.warning('Refused %s %s (Host=%r Origin=%r)', method, url.path,
                        self.headers.get('Host'), self.headers.get('Origin'))
            return self._send(403, {'error': 'forbidden'})
        APP.last_request = time.time()
        if not parts or parts[0] != 'api':
            if method == 'GET':
                return self._static(url.path)
            return self._send(404, {'error': 'not found'})
        try:
            body = self._body() if method == 'POST' else {}
            res = route(method, parts[1:], query, body)
            if res is None:
                return self._send(404, {'error': 'not found'})
            self._send(200, res)
        except ValueError as e:
            self._send(400, {'error': str(e)})
        except Exception as e:
            log.exception('API error on %s', self.path)
            self._send(500, {'error': str(e)})


def _bg(fn, *a, **kw):
    threading.Thread(target=fn, args=a, kwargs=kw, daemon=True).start()


def _namespace(app):
    if gog.is_gog(app):
        return None
    return ((legendary.metadata(app) or {}).get('metadata') or {}).get('namespace')


def _steam_hint(app):
    return gog.gamesdb(gog.gid(app)).get('steam_appid') if gog.is_gog(app) else None


def route(method, p, q, body):
    a = APP
    n = len(p)
    if method == 'GET':
        if p == ['ping']:
            return {'ok': True, 'pid': os.getpid(), 'version': VERSION}
        if p == ['state']:
            return a.state()
        if p == ['library']:
            return {'version': a.version, 'games': a.library()}
        if p == ['jobs']:
            return {'jobs': a.jobs.list()}
        if n == 2 and p[0] == 'game':
            return a.detail(p[1])
        if n == 3 and p[0] == 'game' and p[2] == 'sizes':
            return a.sizes(p[1], refresh=q.get('refresh') == '1', platform=q.get('platform'))
        if n == 3 and p[0] == 'game' and p[2] == 'log':
            path = os.path.join(paths.LOGS, f'{p[1]}.log')
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    text = f.read()[-20000:]
            except FileNotFoundError:
                text = 'No launch log yet - launch the game first.'
            return {'log': text}
        if p == ['proton']:
            return {'tools': proton.list_tools()}
        if p == ['drives']:
            return {'drives': system.drives(store.get_settings()['install_dir'])}
        if p == ['steam', 'search']:
            return {'results': ratings_mod.steam_search(q.get('q', ''), store.get_settings().get('country', 'us'))}
        if p == ['steam', 'pending']:
            return steam.pending()
        if p == ['system']:
            return a.system_info()
        if p == ['auth', 'gog', 'url']:
            return {'url': gog.LOGIN_URL}
        if p == ['update']:
            return a.updater.snapshot()
        return None

    # POST
    if p == ['auth', 'code']:
        ok, msg = legendary.login_with_code(body.get('code', ''))
        if ok:
            _bg(a.refresh_library)
        a.bump()
        return {'ok': ok, 'message': msg}
    if p == ['auth', 'logout']:
        legendary.logout()
        a.bump()
        return {'ok': True}
    if p == ['auth', 'gog', 'code']:
        ok, msg = gog.login_with_code(body.get('code', ''))
        if ok:
            _bg(a.refresh_library)
        a.bump()
        return {'ok': ok, 'message': msg}
    if p == ['auth', 'gog', 'logout']:
        gog.logout()
        a.bump()
        return {'ok': True}
    if p == ['library', 'refresh']:
        _bg(a.refresh_library, bool(body.get('force')))
        return {'ok': True}
    if p == ['install']:
        app = body['app_name']
        if body.get('base_path'):
            os.makedirs(body['base_path'], exist_ok=True)
        return a.jobs.add('install', app, a.title(app), base_path=body.get('base_path'),
                          platform=body.get('platform'))
    if p == ['update']:
        return a.jobs.add('update', body['app_name'], a.title(body['app_name']))
    if p == ['update-all']:
        queued = []
        for app, i in legendary.installed().items():
            if i.get('is_dlc'):
                continue
            latest = legendary.latest_version(app)
            if latest and latest != i.get('version'):
                queued.append(a.jobs.add('update', app, a.title(app)))
        for app, i in gog.installed().items():
            if gog.update_available(i):
                queued.append(a.jobs.add('update', app, a.title(app)))
        return {'queued': len(queued)}
    if p == ['repair']:
        return a.jobs.add('repair', body['app_name'], a.title(body['app_name']))
    if p == ['move']:
        return a.jobs.add('move', body['app_name'], a.title(body['app_name']), base_path=body['base_path'])
    if p == ['uninstall']:
        s = store.get_settings()
        dp = body.get('delete_prefix')
        return a.jobs.add('uninstall', body['app_name'], a.title(body['app_name']),
                          delete_prefix=s['delete_prefix_on_uninstall'] if dp is None else dp)
    if p == ['prepare-proton']:
        return a.jobs.add('prepare', '_proton', 'Proton runtime', proton=body.get('proton'))
    if n == 3 and p[0] == 'jobs' and p[2] == 'cancel':
        return {'ok': a.jobs.cancel(int(p[1]))}
    if n == 3 and p[0] == 'jobs' and p[2] == 'front':
        a.jobs.move_to_front(int(p[1]))
        return {'ok': True}
    if p == ['jobs', 'clear']:
        a.jobs.clear_finished()
        return {'ok': True}
    if p == ['launch']:
        a.launch(body['app_name'])
        return {'ok': True}
    if p == ['stop']:
        a.stop()
        return {'ok': True}
    if n == 3 and p[0] == 'game' and p[2] == 'settings':
        g = store.update_game(p[1], body)
        a.bump()
        return g
    if n == 3 and p[0] == 'game' and p[2] == 'match':
        sid = body.get('steam_appid')
        sid = int(sid) if sid else None
        store.update_game(p[1], {'steam_appid': sid, 'umu_gameid': ''})
        a.ratings.request(p[1], a.title(p[1]), _namespace(p[1]), force=True, steam_appid=sid, hint=_steam_hint(p[1]))
        a.bump()
        return {'ok': True}
    if n == 3 and p[0] == 'game' and p[2] == 'ratings':
        g = store.get_game(p[1])
        a.ratings.request(p[1], a.title(p[1]), _namespace(p[1]), force=True,
                          steam_appid=g.get('steam_appid'), hint=_steam_hint(p[1]))
        return {'ok': True}
    if p == ['ratings', 'refresh']:
        _bg(a.queue_ratings, True)
        return {'ok': True}
    if p == ['settings']:
        s = store.update_settings(body)
        a.ratings.country = s.get('country', 'us')
        a.ratings.enabled = s.get('ratings_enabled', True)
        lang_changed = a.ratings.lang != s.get('reviews_language', 'english')
        a.ratings.lang = s.get('reviews_language', 'english')
        with a.ratings.lock:
            a.ratings.cv.notify_all()
        if lang_changed:
            _bg(a.queue_ratings)  # entries in the old language count as stale
        a.bump()
        return s
    if p == ['steam', 'add']:
        apps = body.get('apps')
        if apps == 'all':
            apps = [k for k, v in legendary.installed().items() if not v.get('is_dlc')] + list(gog.installed())
        return {'added': a.steam_add(apps or [])}
    if p == ['steam', 'remove']:
        a.steam_remove(body.get('apps') or [])
        return {'ok': True}
    if p == ['steam', 'apply']:
        if a.jobs.busy() and not body.get('force'):
            raise ValueError('Downloads are running - restarting Steam would interrupt them')
        gm = body.get('game_mode')
        return steam.apply_with_restart(steam.in_game_mode() if gm is None else bool(gm))
    if p == ['steam', 'add-self']:
        steam.stage_self()
        a._maybe_apply()
        a.bump()
        return {'ok': True}
    if p == ['quit']:
        threading.Timer(0.3, lambda: os._exit(0)).start()
        return {'ok': True}
    if p == ['update', 'check']:
        dl = body.get('download')
        if dl is None:
            dl = store.get_settings().get('app_updates', 'auto') == 'auto'
        _bg(a.updater.check, bool(dl))
        return a.updater.snapshot()
    if p == ['update', 'confirm']:
        return a.updater.confirm()
    if p == ['update', 'prepare']:
        # bin/playvault asks before swapping in a downloaded update: exit if idle
        reason = a.busy_reason()
        if reason:
            return {'ok': False, 'reason': reason}
        log.info('Exiting so a PlayVault update can be installed')
        threading.Timer(0.3, lambda: os._exit(0)).start()
        return {'ok': True}
    return None


def main():
    global APP
    paths.ensure()
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.FileHandler(paths.BACKEND_LOG, encoding='utf-8'), logging.StreamHandler()])
    APP = App()
    srv = ThreadingHTTPServer(('127.0.0.1', paths.PORT), Handler)
    srv.daemon_threads = True
    log.info('PlayVault backend %s listening on http://127.0.0.1:%d', VERSION, paths.PORT)
    srv.serve_forever()


if __name__ == '__main__':
    main()
