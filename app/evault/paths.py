"""Filesystem layout.

Installed layout (default ROOT = ~/.local/share/playvault):
    ROOT/app        this code (evault/, web/, electron/, bin/, art/)
    ROOT/venv       python venv with legendary
    ROOT/gogdl      gogdl (GOG client)
    ROOT/umu        umu-launcher zipapp (umu-run)
    ROOT/electron   Electron runtime
    ROOT/data       everything the app writes
    ROOT/update     a downloaded PlayVault update waiting to be installed (see updater.py)
    ROOT/app.prev   the previous version, kept for rollback after an update
"""
import os

HOME = os.path.expanduser('~')
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get('PLAYVAULT_HOME') or os.environ.get('EPICVAULT_HOME') or os.path.dirname(APP)

DATA = os.path.join(ROOT, 'data')
LEGENDARY_CONFIG = os.path.join(DATA, 'legendary')
GOG = os.path.join(DATA, 'gog')
PREFIXES = os.path.join(DATA, 'prefixes')
LOGS = os.path.join(DATA, 'logs')
PLAYTIME = os.path.join(DATA, 'playtime')
ART = os.path.join(DATA, 'art')

SETTINGS = os.path.join(DATA, 'settings.json')
GAMES = os.path.join(DATA, 'games.json')
LIBRARY = os.path.join(DATA, 'library.json')
RATINGS = os.path.join(DATA, 'ratings.json')
SIZES = os.path.join(DATA, 'sizes.json')
AWACY = os.path.join(DATA, 'anticheat.json')
UMU_DB = os.path.join(DATA, 'umu_db.json')
STEAM_PENDING = os.path.join(DATA, 'steam_pending.json')
RUNNING = os.path.join(DATA, 'running.json')
BACKEND_LOG = os.path.join(LOGS, 'backend.log')

GOG_AUTH = os.path.join(GOG, 'auth.json')
GOG_USER = os.path.join(GOG, 'user.json')
GOG_LIBRARY = os.path.join(GOG, 'library.json')
GOG_DB = os.path.join(GOG, 'gamesdb.json')
GOG_INSTALLED = os.path.join(GOG, 'installed.json')
GOG_BUILDS = os.path.join(GOG, 'builds.json')
GOG_SAVES = os.path.join(GOG, 'saves.json')

UPDATE_DIR = os.path.join(ROOT, 'update')
UPDATE_STAGED = os.path.join(UPDATE_DIR, 'app')          # unpacked app/ of the new version
UPDATE_READY = os.path.join(UPDATE_DIR, 'ready.json')    # staged and verified: bin/playvault swaps it in
UPDATE_TRIAL = os.path.join(UPDATE_DIR, 'trial.json')    # swapped in, not yet confirmed working
UPDATE_TRIES = os.path.join(UPDATE_DIR, 'trial.tries')
UPDATE_FAILED = os.path.join(UPDATE_DIR, 'failed.json')  # rolled back: don't offer this version again
UPDATE_STATE = os.path.join(DATA, 'update.json')
ELECTRON_VERSION_FILE = os.path.join(ROOT, 'electron', '.version')

LEGENDARY_BIN =os.path.join(ROOT, 'venv', 'bin', 'legendary')
VENV_PYTHON = os.path.join(ROOT, 'venv', 'bin', 'python')
GOGDL_BIN = os.path.join(ROOT, 'gogdl', 'gogdl')
UMU_RUN = os.path.join(ROOT, 'umu', 'umu-run')
LAUNCH_SCRIPT = os.path.join(APP, 'bin', 'playvault-launch')
APP_SCRIPT = os.path.join(APP, 'bin', 'playvault')
WEB = os.path.join(APP, 'web')
APP_ART = os.path.join(APP, 'art')

# games go to <root>/Epic/<game> and <root>/GOG/<game>
DEFAULT_GAMES_DIR = os.path.join(HOME, 'Games')
STORE_DIRS = {'epic': 'Epic', 'gog': 'GOG'}

PORT = int(os.environ.get('PLAYVAULT_PORT') or os.environ.get('EPICVAULT_PORT') or '47821')


def store_dir(root, store):
    """Install folder for one store under a games root (tolerates roots that already end in it)."""
    sub = STORE_DIRS[store]
    root = root.rstrip('/') or '/'
    if os.path.basename(root) == sub:
        return root
    return os.path.join(root, sub)


def ensure():
    for d in (DATA, LEGENDARY_CONFIG, GOG, PREFIXES, LOGS, PLAYTIME, ART):
        os.makedirs(d, exist_ok=True)
