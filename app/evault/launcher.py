"""Launches one game and waits for it to exit.

Used by both the PlayVault UI and the Steam library shortcuts it creates:
    playvault-launch <app_name> [extra game args]

Epic: cloud-save download -> fresh Epic auth via legendary -> umu-run game.exe
      -> wait -> playtime -> cloud-save upload.
GOG:  cloud-save download (gogdl) -> umu-run <primary playTask> (or the native
      Linux start.sh) -> wait -> playtime -> cloud-save upload. No login needed,
      GOG games are DRM-free.
"""
import json
import os
import shlex
import signal
import subprocess
import sys
import time

from . import gog, legendary, paths, proton, store, umu
from .util import read_json, write_json

_child = None


def _status_path(app_name):
    return os.path.join(paths.LOGS, f'{app_name}.status.json')


def _redact(cmd):
    out = []
    for a in cmd:
        if a.upper().startswith(('-AUTH_PASSWORD=', '-EPICOVT=')):
            a = a.split('=', 1)[0] + '=<hidden>'
        out.append(a)
    return out


def _on_signal(signum, frame):
    if _child and _child.poll() is None:
        try:
            os.killpg(_child.pid, signal.SIGTERM)
        except Exception:
            _child.terminate()


def _set_running(data):
    if data is None:
        cur = read_json(paths.RUNNING, None)
        if cur and cur.get('pid') == os.getpid():
            try:
                os.remove(paths.RUNNING)
            except OSError:
                pass
    else:
        write_json(paths.RUNNING, data)


def _clean_env(env):
    for k in list(env):
        if k.startswith(('ELECTRON_', 'CHROME_')) or k in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV',
                                                           'LEGENDARY_CONFIG_PATH'):
            env.pop(k, None)
    return env


def _wine_root(prefix):
    # umu/Proton keep the real wine prefix in <prefix>/pfx (umu links it back to <prefix>)
    for cand in (prefix, os.path.join(prefix, 'pfx')):
        if os.path.isdir(os.path.join(cand, 'drive_c')):
            return cand
    return None


def _proton_env(app_name, g, s, gameid, store_name, prefix):
    env = _clean_env(os.environ.copy())
    env.update({
        'WINEPREFIX': prefix,
        'GAMEID': gameid,
        'STORE': store_name,
        'PROTONPATH': proton.resolve(g['proton'] or s['default_proton']),
    })
    if g.get('proton_log'):
        env.update({'PROTON_LOG': '1', 'PROTON_LOG_DIR': paths.LOGS, 'UMU_LOG': '1'})
    return env


def _user_env(env, g):
    for k, v in (g.get('env') or {}).items():
        if k:
            env[str(k)] = str(v)
    return env


def _play(app_name, cmd, cwd, env, log):
    """Run the game, wait for it, record playtime. Returns (rc, seconds)."""
    global _child
    start = time.time()
    _set_running({'app_name': app_name, 'pid': os.getpid(), 'started': start, 'stage': 'running'})
    cwd = cwd if cwd and os.path.isdir(cwd) else None
    _child = subprocess.Popen(cmd, cwd=cwd, env=env,
                              stdout=log.file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              start_new_session=True)
    rc = _child.wait()
    duration = time.time() - start
    log(f'Game exited with code {rc} after {int(duration)}s')
    if duration > 15:
        store.add_playtime(app_name, duration)
    return rc, duration


def launch(app_name, extra, log):
    if gog.is_gog(app_name):
        return launch_gog(app_name, extra, log)
    return launch_epic(app_name, extra, log)


def launch_gog(app_name, extra, log):
    s = store.get_settings()
    g = store.get_game(app_name)
    inst = gog.installed().get(app_name)
    if not inst:
        log(f'{app_name} is not installed.')
        return 1, 'Game is not installed'
    try:
        exe, task_args, cwd = gog.play_task(inst)
    except RuntimeError as e:
        return 1, str(e)
    cmd_tail = list(task_args) + (shlex.split(g['args']) if g['args'] else []) + list(extra)

    if inst.get('platform') == 'linux':
        env = _user_env(_clean_env(os.environ.copy()), g)
        cmd = [exe] + cmd_tail
        log(f'Native Linux build: {shlex.join(cmd)}')
        rc, duration = _play(app_name, cmd, cwd, env, log)
    else:
        runner = umu.umu_run()
        if not runner:
            return 1, 'umu-launcher is missing - re-run the installer'
        prefix = os.path.join(paths.PREFIXES, app_name)
        os.makedirs(prefix, exist_ok=True)
        cloud = g['cloud_saves'] if g['cloud_saves'] is not None else s['cloud_saves']
        root = _wine_root(prefix)
        if cloud and root:
            try:
                gog.sync_saves(inst, root, 'download', log)
            except Exception as e:
                log(f'GOG cloud save download failed: {e!r}')
        ratings = read_json(paths.RATINGS, {}) or {}
        steam_appid = g.get('steam_appid') or (ratings.get(app_name) or {}).get('steam_appid')
        gameid = g.get('umu_gameid') or umu.game_id(inst['id'], steam_appid, store='gog')
        env = _user_env(_proton_env(app_name, g, s, gameid, 'gog', prefix), g)
        cmd = [runner, exe] + cmd_tail
        log(f'Proton: {env["PROTONPATH"]}   GAMEID: {gameid}   Prefix: {prefix}')
        log('Command: ' + shlex.join(cmd))
        if not root:
            log('First launch: umu will set up the Proton prefix (and download Proton/runtime if needed).')
        rc, duration = _play(app_name, cmd, cwd, env, log)
        root = _wine_root(prefix)
        if cloud and root:
            try:
                gog.sync_saves(inst, root, 'upload', log)
            except Exception as e:
                log(f'GOG cloud save upload failed: {e!r}')
    if duration < 15 and rc != 0:
        return rc, f'Game exited after {int(duration)}s with code {rc} - see the launch log'
    return 0, None


def launch_epic(app_name, extra, log):
    s = store.get_settings()
    g = store.get_game(app_name)
    inst = legendary.installed().get(app_name)
    if not inst:
        log(f'{app_name} is not installed.')
        return 1, 'Game is not installed'

    meta = legendary.metadata(app_name) or {}
    attrs = (meta.get('metadata') or {}).get('customAttributes') or {}
    cloud_supported = 'CloudSaveFolder' in attrs
    prefix = os.path.join(paths.PREFIXES, app_name)
    os.makedirs(prefix, exist_ok=True)

    def wine_root():
        return _wine_root(prefix)

    def point_legendary_at_prefix():
        root = wine_root()
        if root:
            legendary.set_app_config(app_name, {'wine_prefix': root})

    point_legendary_at_prefix()
    cloud = g['cloud_saves'] if g['cloud_saves'] is not None else s['cloud_saves']
    offline = bool(g['offline'])
    prefix_ready = wine_root() is not None
    if cloud and cloud_supported and not offline and prefix_ready:
        log('Syncing cloud saves (download)...')
        # --accept-path: with -y legendary otherwise skips games whose save path isn't stored yet
        rc, out, err = legendary.run(['sync-saves', app_name, '--skip-upload', '--accept-path'], timeout=180)
        log((err or out).strip()[-1500:] or f'sync-saves exit {rc}')

    args = ['launch', app_name, '--json', '--no-wine']
    if offline:
        args.append('--offline')
    if s.get('skip_update_check'):
        args.append('--skip-version-check')
    log('Requesting launch parameters from Epic...')
    rc, out, err = legendary.run(args, timeout=120)
    if (rc != 0 or not out.strip().startswith('{')) and not offline and inst.get('can_run_offline'):
        log('Online launch failed, retrying in offline mode:\n' + err.strip()[-800:])
        rc, out, err = legendary.run(args + ['--offline'], timeout=60)
    if rc != 0 or not out.strip().startswith('{'):
        log(err.strip()[-2000:])
        reason = 'Could not get launch parameters from Epic'
        if 'out of date' in err:
            reason = 'An update is required - update the game in PlayVault (or allow outdated launches in Settings)'
        elif 'Login failed' in err:
            reason = 'Epic login expired - sign in again in PlayVault'
        return 1, reason
    p = json.loads(out)

    runner = umu.umu_run()
    if not runner:
        return 1, 'umu-launcher is missing - re-run the installer'

    exe = os.path.join(p['game_directory'], p['game_executable'])
    cmd = [runner, exe]
    cmd += p.get('game_parameters') or []
    cmd += p.get('user_parameters') or []
    cmd += p.get('egl_parameters') or []
    if g['args']:
        cmd += shlex.split(g['args'])
    cmd += extra

    ratings = read_json(paths.RATINGS, {}) or {}
    steam_appid = g.get('steam_appid') or (ratings.get(app_name) or {}).get('steam_appid')
    gameid = g.get('umu_gameid') or umu.game_id(app_name, steam_appid)

    env = _proton_env(app_name, g, s, gameid, 'egs', prefix)
    env.update(p.get('environment') or {})
    _user_env(env, g)

    log(f'Proton: {env["PROTONPATH"]}   GAMEID: {gameid}   Prefix: {prefix}')
    log('Command: ' + shlex.join(_redact(cmd)))
    if not prefix_ready:
        log('First launch: umu will set up the Proton prefix (and download Proton/runtime if needed).')

    cwd = next((d for d in (p.get('working_directory'), p.get('game_directory')) if d and os.path.isdir(d)), None)
    rc, duration = _play(app_name, cmd, cwd, env, log)

    point_legendary_at_prefix()  # prefix exists now even if this was the first launch
    if cloud and cloud_supported and not offline and wine_root():
        log('Syncing cloud saves (upload)...')
        r2, o2, e2 = legendary.run(['sync-saves', app_name, '--skip-download', '--accept-path'], timeout=180)
        log((e2 or o2).strip()[-1500:] or f'sync-saves exit {r2}')

    if duration < 15 and rc != 0:
        return rc, f'Game exited after {int(duration)}s with code {rc} - see the launch log'
    return 0, None


class _Log:
    def __init__(self, path):
        self.file = open(path, 'w', buffering=1, encoding='utf-8', errors='replace')

    def __call__(self, msg):
        self.file.write(time.strftime('[%H:%M:%S] ') + str(msg) + '\n')
        self.file.flush()


def main(argv):
    if not argv:
        print('usage: playvault-launch <app_name> [game args...]')
        return 2
    app_name, extra = argv[0], argv[1:]
    paths.ensure()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log = _Log(os.path.join(paths.LOGS, f'{app_name}.log'))
    log(f'PlayVault launching {app_name}')
    _set_running({'app_name': app_name, 'pid': os.getpid(), 'started': time.time(), 'stage': 'starting'})
    rc, err = 1, 'Unknown error'
    try:
        rc, err = launch(app_name, extra, log)
    except Exception as e:
        import traceback
        log(traceback.format_exc())
        rc, err = 1, f'Launcher crashed: {e!r}'
    finally:
        _set_running(None)
        write_json(_status_path(app_name), {'time': time.time(), 'ok': err is None, 'error': err})
    if err:
        log('ERROR: ' + err)
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
