"""Download / install / update / repair / move / uninstall queue.

Long jobs (install, update, repair, move, prepare) run one at a time in order.
Uninstall runs immediately in its own thread.
"""
import itertools
import os
import re
import shutil
import signal
import subprocess
import threading
import time

from . import gog, legendary, paths, proton, store
from .util import log

SERIAL = ('install', 'update', 'repair', 'move', 'prepare')
_ids = itertools.count(1)

# legendary: "Progress: 12.34% (5/40), ... ETA: 00:01:02"   gogdl: "Progress: 12.34 512/4096, ... ETA: 00:01:02"
RE_PROGRESS = re.compile(r'Progress: ([\d.]+)%? \(?(\d+)/(\d+)\)?.*ETA: (\d+):(\d+):(\d+)')
RE_DOWNLOADED = re.compile(r'Downloaded: ([\d.]+) MiB, Written: ([\d.]+) MiB')
RE_SPEED = re.compile(r'\+ Download\s*- ([\d.]+) MiB/s')
RE_DLSIZE = re.compile(r'Download size: ([\d.]+) MiB')
RE_INSTSIZE = re.compile(r'Install size: ([\d.]+) MiB')
RE_ERR = re.compile(r'\b(ERROR|CRITICAL|FATAL)\b[:\s]*(.*)')


class JobManager:
    def __init__(self, on_change=None, on_done=None):
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.jobs = []
        self.on_change = on_change or (lambda: None)
        self.on_done = on_done or (lambda job: None)
        threading.Thread(target=self._worker, name='jobs', daemon=True).start()

    # ------------------------------------------------------------ public
    def add(self, kind, app_name, title, **opts):
        with self.lock:
            for j in self.jobs:
                if j['app_name'] == app_name and j['kind'] == kind and j['status'] in ('queued', 'running'):
                    return self._public(j)
            job = {
                'id': next(_ids), 'kind': kind, 'app_name': app_name, 'title': title,
                'status': 'queued', 'progress': 0.0, 'eta': None, 'speed': 0.0,
                'downloaded_mb': 0.0, 'download_size_mb': None, 'install_size_mb': None,
                'error': None, 'message': 'Waiting in queue', 'created': time.time(),
                'started': None, 'finished': None, 'opts': opts, '_proc': None, '_cancel': False,
            }
            self.jobs.append(job)
            if kind not in SERIAL:
                threading.Thread(target=self._run, args=(job,), daemon=True).start()
            self.cv.notify_all()
        self.on_change()
        return self._public(job)

    def cancel(self, job_id):
        with self.lock:
            job = next((j for j in self.jobs if j['id'] == job_id), None)
            if not job:
                return False
            if job['status'] == 'queued':
                job['status'] = 'cancelled'
                job['message'] = 'Cancelled'
                job['finished'] = time.time()
            elif job['status'] == 'running':
                job['_cancel'] = True
                job['message'] = 'Cancelling...'
                self._kill(job.get('_proc'))
        self.on_change()
        return True

    def move_to_front(self, job_id):
        with self.lock:
            job = next((j for j in self.jobs if j['id'] == job_id and j['status'] == 'queued'), None)
            if job:
                self.jobs.remove(job)
                first_q = next((i for i, j in enumerate(self.jobs) if j['status'] == 'queued'), len(self.jobs))
                self.jobs.insert(first_q, job)
        self.on_change()

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j['status'] in ('queued', 'running')]
        self.on_change()

    def list(self):
        with self.lock:
            return [self._public(j) for j in self.jobs]

    def active_for(self, app_name):
        with self.lock:
            for j in self.jobs:
                if j['app_name'] == app_name and j['status'] in ('queued', 'running'):
                    return self._public(j)
        return None

    def busy(self):
        with self.lock:
            return any(j['status'] in ('queued', 'running') for j in self.jobs)

    # ------------------------------------------------------------ internals
    @staticmethod
    def _public(j):
        return {k: v for k, v in j.items() if not k.startswith('_')}

    @staticmethod
    def _kill(proc):
        if not proc or proc.poll() is not None:
            return
        try:
            if os.name != 'nt':
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            pass

        def hard():
            time.sleep(6)
            if proc.poll() is None:
                try:
                    if os.name != 'nt':
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception:
                    pass
        threading.Thread(target=hard, daemon=True).start()

    def _worker(self):
        while True:
            with self.lock:
                job = None
                while job is None:
                    job = next((j for j in self.jobs if j['status'] == 'queued' and j['kind'] in SERIAL), None)
                    if job is None:
                        self.cv.wait(timeout=30)
            self._run(job)

    def _run(self, job):
        with self.lock:
            if job['status'] != 'queued':
                return
            job['status'] = 'running'
            job['started'] = time.time()
            job['message'] = 'Starting...'
        self.on_change()
        try:
            fn = getattr(self, f'_do_{job["kind"]}')
            fn(job)
            with self.lock:
                if job['_cancel']:
                    job['status'] = 'cancelled'
                    job['message'] = 'Cancelled - progress is kept, install again to resume'
                elif job['status'] == 'running':
                    job['status'] = 'done'
                    job['progress'] = 100.0
                    job['message'] = 'Done'
        except Exception as e:
            log.exception('Job %s failed', job['kind'])
            with self.lock:
                job['status'] = 'cancelled' if job['_cancel'] else 'failed'
                job['error'] = str(e) if not job['_cancel'] else None
                job['message'] = 'Cancelled' if job['_cancel'] else 'Failed'
        finally:
            job['finished'] = time.time()
            job['_proc'] = None
            self.on_change()
            try:
                self.on_done(self._public(job))
            except Exception:
                log.exception('on_done hook failed')

    def _run_legendary(self, job, args):
        self._run_tool(job, legendary.popen(args), 'legendary')

    def _run_tool(self, job, proc, name):
        job['_proc'] = proc
        errors = []
        last_lines = []
        last_push = 0
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            last_lines = (last_lines + [line])[-30:]
            m = RE_PROGRESS.search(line)
            if m:
                job['progress'] = float(m.group(1))
                h, mi, s = int(m.group(4)), int(m.group(5)), int(m.group(6))
                job['eta'] = h * 3600 + mi * 60 + s
                job['message'] = 'Downloading'
            elif (m := RE_DOWNLOADED.search(line)):
                job['downloaded_mb'] = float(m.group(1))
            elif (m := RE_SPEED.search(line)):
                job['speed'] = float(m.group(1))
            elif (m := RE_DLSIZE.search(line)):
                job['download_size_mb'] = float(m.group(1))
            elif (m := RE_INSTSIZE.search(line)):
                job['install_size_mb'] = float(m.group(1))
            elif 'Verification progress' in line or 'Verifying' in line:
                job['message'] = 'Verifying files'
                pm = re.search(r'\(([\d.]+)%\)', line) or re.search(r'([\d.]+)%', line)
                if pm:
                    job['progress'] = float(pm.group(1))
            elif 'Finished installation' in line or 'Finished.' in line:
                job['message'] = 'Finishing'
            elif 'Not enough disk space' in line:
                errors.append('Not enough disk space')
            elif 'All files look good' in line or 'Nothing to do' in line:
                job['message'] = 'Everything is up to date'
            em = RE_ERR.search(line)
            if em:
                errors.append(em.group(2).strip() or line)
            if time.time() - last_push > 0.8:
                last_push = time.time()
                self.on_change()
        rc = proc.wait()
        job['log_tail'] = last_lines[-12:]
        if job['_cancel']:
            return
        if rc != 0 or any('not enough' in e.lower() for e in errors):
            raise RuntimeError(errors[-1] if errors else f'{name} exited with code {rc}')

    # ------------------------------------------------------------ job kinds
    @staticmethod
    def _workers(s):
        return ['--max-workers', str(int(s['max_workers']))] if s.get('max_workers') else []

    def _do_install(self, job):
        s = store.get_settings()
        root = job['opts'].get('base_path') or s['install_dir']
        if gog.is_gog(job['app_name']):
            return self._gog_install(job, s, paths.store_dir(root, 'gog'))
        base = paths.store_dir(root, 'epic')
        os.makedirs(base, exist_ok=True)
        args = ['install', job['app_name'], '--base-path', base, '--skip-sdl']
        args.append('--with-dlcs' if s.get('install_dlc') else '--skip-dlcs')
        self._run_legendary(job, args + self._workers(s))

    def _gog_install(self, job, s, base):
        g = gog.gid(job['app_name'])
        platform = job['opts'].get('platform') or 'windows'
        lang = s.get('gog_language') or 'en-US'
        dlcs = bool(s.get('install_dlc'))
        job['message'] = 'Preparing download'
        self.on_change()
        info = gog.info(g, platform, lang, dlcs)
        job['download_size_mb'] = (info.get('download') or 0) / 1048576 or None
        job['install_size_mb'] = (info.get('disk') or 0) / 1048576 or None
        if not info.get('folder_name'):
            raise RuntimeError('GOG did not return an install folder for this game')
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, info['folder_name'])
        args = ['download', g, '--platform', platform, '--path', base, '--lang', lang,
                '--with-dlcs' if dlcs else '--skip-dlcs'] + self._workers(s)
        self._run_tool(job, gog.popen(args), 'gogdl')
        if not job['_cancel']:
            gog.register_install(g, platform, path, info, title=job['title'])

    def _gog_inst(self, job):
        inst = gog.installed().get(job['app_name'])
        if not inst:
            raise RuntimeError('Game is not installed')
        return inst

    def _do_update(self, job):
        s = store.get_settings()
        if gog.is_gog(job['app_name']):
            inst = self._gog_inst(job)
            args = ['update', inst['id'], '--platform', inst['platform'], '--path', inst['install_path'],
                    '--lang', s.get('gog_language') or 'en-US',
                    '--with-dlcs' if s.get('install_dlc') else '--skip-dlcs'] + self._workers(s)
            self._run_tool(job, gog.popen(args), 'gogdl')
            if not job['_cancel']:
                gog.register_install(inst['id'], inst['platform'], inst['install_path'],
                                     {'versionName': None, 'buildId': gog.latest_build(inst['id'], inst['platform'])})
            return
        args = ['install', job['app_name'], '--update-only', '--skip-sdl']
        args.append('--with-dlcs' if s.get('install_dlc') else '--skip-dlcs')
        self._run_legendary(job, args)

    def _do_repair(self, job):
        job['message'] = 'Verifying files'
        if gog.is_gog(job['app_name']):
            inst = self._gog_inst(job)
            s = store.get_settings()
            args = ['repair', inst['id'], '--platform', inst['platform'], '--path', inst['install_path'],
                    '--lang', s.get('gog_language') or 'en-US',
                    '--with-dlcs' if s.get('install_dlc') else '--skip-dlcs'] + self._workers(s)
            self._run_tool(job, gog.popen(args), 'gogdl')
            if not job['_cancel']:
                gog.register_install(inst['id'], inst['platform'], inst['install_path'])
            return
        self._run_legendary(job, ['install', job['app_name'], '--repair', '--skip-sdl'])

    def _do_uninstall(self, job):
        other = self.active_for(job['app_name'])
        if other and other['id'] != job['id']:
            self.cancel(other['id'])
            time.sleep(2)
        job['message'] = 'Removing files'
        if gog.is_gog(job['app_name']):
            inst = self._gog_inst(job)
            path = inst.get('install_path')
            if path and os.path.isdir(path) and os.path.realpath(path) not in ('/', paths.HOME):
                shutil.rmtree(path, ignore_errors=True)
            gog.unregister(inst['id'])
        else:
            rc, out, err = legendary.run(['uninstall', job['app_name'], '--skip-uninstaller'], timeout=1800)
            if rc != 0:
                raise RuntimeError((err or out).strip().splitlines()[-1] if (err or out).strip() else 'Uninstall failed')
        if job['opts'].get('delete_prefix'):
            pfx = os.path.join(paths.PREFIXES, job['app_name'])
            if os.path.isdir(pfx):
                shutil.rmtree(pfx, ignore_errors=True)

    def _do_move(self, job):
        is_gog = gog.is_gog(job['app_name'])
        inst = (gog.installed() if is_gog else legendary.installed()).get(job['app_name'])
        if not inst:
            raise RuntimeError('Game is not installed')
        src = inst['install_path']
        new_base = paths.store_dir(job['opts']['base_path'], 'gog' if is_gog else 'epic')
        dst = os.path.join(new_base, os.path.basename(src.rstrip('/')))
        if os.path.exists(dst):
            raise RuntimeError(f'Target already exists: {dst}')
        os.makedirs(new_base, exist_ok=True)
        job['message'] = 'Moving files'
        try:
            os.rename(src, dst)
        except OSError:
            files = []
            total = 0
            for root, _, names in os.walk(src):
                for n in names:
                    p = os.path.join(root, n)
                    try:
                        sz = os.path.getsize(p)
                    except OSError:
                        sz = 0
                    files.append((p, sz))
                    total += sz
            done = 0
            t0 = time.time()
            for p, sz in files:
                if job['_cancel']:
                    shutil.rmtree(dst, ignore_errors=True)
                    return
                rel = os.path.relpath(p, src)
                target = os.path.join(dst, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(p, target, follow_symlinks=False)
                done += sz
                elapsed = max(time.time() - t0, 0.1)
                job['progress'] = 100.0 * done / total if total else 100.0
                job['speed'] = done / elapsed / 1048576
                job['eta'] = int((total - done) / (done / elapsed)) if done else None
                self.on_change()
            shutil.rmtree(src, ignore_errors=True)
        if is_gog:
            gog.register_install(inst['id'], inst['platform'], dst)
            return
        rc, out, err = legendary.run(['move', job['app_name'], new_base, '--skip-move'], timeout=120)
        if rc != 0:
            raise RuntimeError('Files moved but legendary could not update its records: ' + (err or out)[-300:])

    def _do_prepare(self, job):
        """Download the Steam runtime + Proton build used by umu, by creating a scratch prefix."""
        if not os.path.exists(paths.UMU_RUN):
            raise RuntimeError('umu-launcher is not installed')
        s = store.get_settings()
        env = os.environ.copy()
        env.update({'WINEPREFIX': os.path.join(paths.PREFIXES, '_warmup'), 'GAMEID': 'umu-default',
                    'PROTONPATH': proton.resolve(job['opts'].get('proton') or s['default_proton']),
                    'UMU_LOG': '1'})
        os.makedirs(env['WINEPREFIX'], exist_ok=True)
        job['message'] = 'Downloading Steam runtime and Proton (first time can take a while)'
        proc = subprocess.Popen([paths.UMU_RUN, ''], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        job['_proc'] = proc
        lines = []
        for line in proc.stdout:
            line = line.strip()
            if line:
                lines = (lines + [line])[-12:]
                job['message'] = line[:140]
                self.on_change()
        rc = proc.wait()
        job['log_tail'] = lines
        if rc != 0 and not job['_cancel']:
            raise RuntimeError(f'umu-run exited with {rc}: {lines[-1] if lines else ""}')
