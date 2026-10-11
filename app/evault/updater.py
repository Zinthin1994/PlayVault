"""PlayVault self-update from GitHub releases.

The backend checks the latest release of the PlayVault repo, downloads PlayVault.tar.gz,
verifies it against the SHA-256 digest GitHub publishes for the asset and unpacks the new
app/ into ROOT/update/app. Nothing that is running is touched: bin/playvault swaps the staged
version in the next time PlayVault starts (when nothing is downloading or playing), keeps the
old one as ROOT/app.prev, and rolls back if the new version fails to start twice. The web UI
confirms a successful start through POST /api/update/confirm.

Only published releases count (not drafts, pre-releases or plain pushes to the repo).
"""
import hashlib
import json
import os
import re
import shutil
import tarfile
import threading
import time
import urllib.request

from . import VERSION, paths, store
from .util import USER_AGENT, log, read_json, write_json

REPO = os.environ.get('PLAYVAULT_UPDATE_REPO') or 'Zinthin1994/PlayVault'
API = (os.environ.get('PLAYVAULT_UPDATE_API') or 'https://api.github.com').rstrip('/')
ASSET = 'PlayVault.tar.gz'
CHECK_EVERY = 6 * 3600
MAX_SIZE = 50 * 1024 * 1024


def parse_version(v):
    m = re.match(r'^v?(\d+(?:\.\d+)*)$', (v or '').strip())
    return tuple(int(x) for x in m.group(1).split('.')) if m else None


def newer(a, b):
    """True if version string a is newer than b."""
    pa, pb = parse_version(a), parse_version(b)
    return bool(pa and pb and pa > pb)


def release_url():
    return f'https://github.com/{REPO}/releases'


class Updater:
    def __init__(self, on_change=None):
        self.on_change = on_change or (lambda: None)
        self.lock = threading.Lock()
        self.working = False
        st = read_json(paths.UPDATE_STATE, {}) or {}
        self.checked = st.get('checked', 0)
        self.latest = st.get('latest')
        self.notes = st.get('notes', '')
        self.url = st.get('url')
        self.error = None
        self.progress = 0
        self.status = 'idle'
        ready = read_json(paths.UPDATE_READY, None)
        if ready and newer(ready.get('version'), VERSION) and os.path.isdir(paths.UPDATE_STAGED):
            self.status = 'ready'
            self.latest = ready['version']
            self.notes = ready.get('notes', self.notes)
        elif self.latest and newer(self.latest, VERSION) and not self._failed(self.latest):
            self.status = 'available'

    # ------------------------------------------------------------ state
    def snapshot(self):
        failed = read_json(paths.UPDATE_FAILED, None) or {}
        return {
            'current': VERSION,
            'latest': self.latest,
            'status': self.status,     # idle uptodate checking available downloading ready needs_installer failed error
            'progress': self.progress,
            'notes': self.notes,
            'url': self.url or release_url(),
            'error': self.error,
            'checked': self.checked,
            'rolled_back': failed.get('version'),
        }

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.on_change()

    def _save(self):
        write_json(paths.UPDATE_STATE, {'checked': self.checked, 'latest': self.latest,
                                        'notes': self.notes, 'url': self.url})

    @staticmethod
    def _failed(version):
        f = read_json(paths.UPDATE_FAILED, None) or {}
        return f.get('version') == version

    # ------------------------------------------------------------ background loop
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        time.sleep(20)  # let the library and ratings start first
        while True:
            try:
                mode = store.get_settings().get('app_updates', 'auto')
                if mode != 'off' and time.time() - self.checked > CHECK_EVERY:
                    self.check(download=(mode == 'auto'))
            except Exception as e:
                log.warning('Update check failed: %r', e)
            time.sleep(600)

    # ------------------------------------------------------------ check / download
    def check(self, download=False):
        """Looks up the latest release; downloads and stages it when download is True."""
        with self.lock:
            if self.working:
                return self.snapshot()
            self.working = True
        try:
            self._check(download)
        except Exception as e:
            log.warning('Update failed: %r', e)
            self._set(status='error', error=str(e), progress=0)
        finally:
            self.working = False
        return self.snapshot()

    def _check(self, download):
        if self.status == 'ready' and not download:
            return
        self._set(status='checking', error=None)
        rel = self._latest_release()
        self.checked = time.time()
        self.latest = rel['tag_name'].lstrip('v')
        self.notes = (rel.get('body') or '').strip()[:2000]
        self.url = rel.get('html_url')
        self._save()
        if not newer(self.latest, VERSION):
            self._set(status='uptodate')
            return
        if self._failed(self.latest):
            self._set(status='failed')
            return
        ready = read_json(paths.UPDATE_READY, None) or {}
        if ready.get('version') == self.latest and os.path.isdir(paths.UPDATE_STAGED):
            self._set(status='ready')
            return
        if not download:
            self._set(status='available')
            return
        asset = next((a for a in rel.get('assets') or [] if a.get('name') == ASSET), None)
        if not asset:
            raise RuntimeError(f'Release {self.latest} has no {ASSET}')
        self._download_and_stage(asset)

    def _latest_release(self):
        req = urllib.request.Request(f'{API}/repos/{REPO}/releases/latest',
                                     headers={'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.loads(r.read())
        if not parse_version(rel.get('tag_name')):
            raise RuntimeError(f'Unexpected release tag {rel.get("tag_name")!r}')
        return rel

    def _download_and_stage(self, asset):
        digest = (asset.get('digest') or '')
        if not digest.startswith('sha256:'):
            raise RuntimeError('The release file has no SHA-256 checksum - not installing it')
        want = digest.split(':', 1)[1].lower()
        size = int(asset.get('size') or 0)
        if size > MAX_SIZE:
            raise RuntimeError('The release file is unexpectedly large - not installing it')

        os.makedirs(paths.UPDATE_DIR, exist_ok=True)
        tar_path = os.path.join(paths.UPDATE_DIR, ASSET + '.part')
        extract = os.path.join(paths.UPDATE_DIR, 'extract')
        self._set(status='downloading', progress=0)
        h = hashlib.sha256()
        got = 0
        req = urllib.request.Request(asset['browser_download_url'], headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r, open(tar_path, 'wb') as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                got += len(chunk)
                if got > MAX_SIZE:
                    raise RuntimeError('Download is larger than expected')
                h.update(chunk)
                f.write(chunk)
                if size:
                    self._set(progress=min(99, int(got * 100 / size)))
        if h.hexdigest() != want:
            os.remove(tar_path)
            raise RuntimeError('Downloaded update does not match its checksum - discarded')

        shutil.rmtree(extract, ignore_errors=True)
        os.makedirs(extract)
        with tarfile.open(tar_path, 'r:gz') as tar:
            _safe_extract(tar, extract)
        os.remove(tar_path)
        new_app = os.path.join(extract, 'PlayVault', 'app')
        init = os.path.join(new_app, 'evault', '__init__.py')
        m = re.search(r"VERSION = '([^']+)'", open(init, encoding='utf-8').read()) if os.path.isfile(init) else None
        if not m or m.group(1) != self.latest or not os.path.isfile(os.path.join(new_app, 'bin', 'playvault')):
            shutil.rmtree(extract, ignore_errors=True)
            raise RuntimeError('The downloaded update is not a valid PlayVault release')

        # an update that needs a different Electron runtime has to go through install.sh
        need = _electron_version(os.path.join(extract, 'PlayVault', 'install.sh'))
        have = (open(paths.ELECTRON_VERSION_FILE).read().strip()
                if os.path.isfile(paths.ELECTRON_VERSION_FILE) else None)
        if need and have and need != have:
            shutil.rmtree(extract, ignore_errors=True)
            self._set(status='needs_installer', progress=0)
            return

        for root, dirs, files in os.walk(os.path.join(new_app, 'bin')):
            for name in files:
                os.chmod(os.path.join(root, name), 0o755)
        shutil.rmtree(paths.UPDATE_STAGED, ignore_errors=True)
        os.replace(new_app, paths.UPDATE_STAGED)
        shutil.rmtree(extract, ignore_errors=True)
        write_json(paths.UPDATE_READY, {'version': self.latest, 'notes': self.notes, 'staged': time.time()})
        log.info('PlayVault %s downloaded and verified; installs on next start', self.latest)
        self._set(status='ready', progress=100)

    # ------------------------------------------------------------ after a restart
    def confirm(self):
        """Called by the UI once the new version is up: the update worked, no rollback."""
        trial = read_json(paths.UPDATE_TRIAL, None)
        for p in (paths.UPDATE_TRIAL, paths.UPDATE_TRIES):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        if trial and trial.get('version') == VERSION:
            log.info('Updated to PlayVault %s', VERSION)
            return {'updated': VERSION, 'notes': trial.get('notes', '')}
        return {'updated': None}


def _electron_version(install_sh):
    try:
        m = re.search(r'^ELECTRON_VERSION="([^"]+)"', open(install_sh, encoding='utf-8').read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def _safe_extract(tar, dest):
    if hasattr(tarfile, 'data_filter'):
        tar.extractall(dest, filter='data')
        return
    base = os.path.realpath(dest)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, m.name))
        if not (m.isfile() or m.isdir()) or not target.startswith(base + os.sep):
            raise RuntimeError(f'Unsafe path in update archive: {m.name}')
    tar.extractall(dest)
