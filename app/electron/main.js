// PlayVault shell: starts (or reuses) the Python backend, shows the UI fullscreen,
// handles the Epic / GOG sign-in windows and hides itself while a game is running.
const { app, BrowserWindow, ipcMain, session } = require('electron');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const APP_DIR = process.env.PLAYVAULT_APP || path.resolve(__dirname, '..');
const ROOT = process.env.PLAYVAULT_ROOT || path.resolve(APP_DIR, '..');
const PORT = parseInt(process.env.PLAYVAULT_PORT || '47821', 10);
const BASE = `http://127.0.0.1:${PORT}`;
const STORES = {
  epic: {
    title: 'Sign in to Epic Games',
    url: 'https://legendary.gl/epiclogin',
    partition: 'persist:epiclogin',
    redirect: 'https://www.epicgames.com/id/api/redirect',
    // Epic shows the code as JSON on the redirect page
    code: async (wc) => JSON.parse(await wc.executeJavaScript('document.body.innerText')).authorizationCode,
  },
  gog: {
    title: 'Sign in to GOG',
    url: 'https://auth.gog.com/auth?client_id=46899977096215655&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=galaxy',
    partition: 'persist:goglogin',
    redirect: 'https://embed.gog.com/on_login_success',
    // GOG puts the code in the redirect URL itself
    code: async (wc, url) => new URL(url).searchParams.get('code'),
  },
};

let win = null;
let hiddenForGame = false;
let runningSince = 0;

app.commandLine.appendSwitch('disable-gpu-sandbox');
app.commandLine.appendSwitch('enable-gamepad-button-axis-events');

function request(method, p, body) {
  return new Promise((resolve) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(`${BASE}${p}`, {
      method, timeout: 4000,
      headers: data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {},
    }, (res) => {
      let out = '';
      res.on('data', (c) => { out += c; });
      res.on('end', () => { try { resolve(JSON.parse(out)); } catch (e) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
    if (data) req.write(data);
    req.end();
  });
}

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'settings.json'), 'utf8'));
  } catch (e) { return {}; }
}

async function ensureBackend() {
  if (await request('GET', '/api/ping')) return true;
  const py = fs.existsSync(path.join(ROOT, 'venv', 'bin', 'python'))
    ? path.join(ROOT, 'venv', 'bin', 'python') : (process.platform === 'win32' ? 'python' : 'python3');
  const background = readSettings().background_downloads !== false;
  fs.mkdirSync(path.join(ROOT, 'data', 'logs'), { recursive: true });
  let started = false;
  if (background && process.platform === 'linux') {
    // Run the backend as its own systemd user unit so downloads survive closing PlayVault
    // and Steam doesn't keep showing PlayVault as "running" afterwards.
    const r = spawnSync('systemd-run', ['--user', '--collect', '--quiet', '--unit=playvault-backend',
      `--working-directory=${APP_DIR}`, `--setenv=PYTHONPATH=${APP_DIR}`, `--setenv=PLAYVAULT_HOME=${ROOT}`,
      '--setenv=PYTHONUNBUFFERED=1', py, '-m', 'evault.server'], { stdio: 'ignore' });
    started = r.status === 0;
  }
  if (!started) {
    const out = fs.openSync(path.join(ROOT, 'data', 'logs', 'backend.stdout.log'), 'a');
    const child = spawn(py, ['-m', 'evault.server'], {
      cwd: APP_DIR,
      env: { ...process.env, PYTHONPATH: APP_DIR, PLAYVAULT_HOME: ROOT, PYTHONUNBUFFERED: '1' },
      detached: background,
      stdio: ['ignore', out, out],
    });
    if (background) child.unref();
  }
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 250));
    if (await request('GET', '/api/ping')) return true;
  }
  return false;
}

function createWindow() {
  win = new BrowserWindow({
    fullscreen: true,
    width: 1280,
    height: 800,
    backgroundColor: '#0a0d13',
    autoHideMenuBar: true,
    title: 'PlayVault',
    icon: path.join(APP_DIR, 'art', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadURL(`${BASE}/`);
  win.webContents.on('before-input-event', (e, input) => {
    if (input.type === 'keyDown' && input.key === 'F11') {
      win.setFullScreen(!win.isFullScreen());
      e.preventDefault();
    }
    if (input.type === 'keyDown' && input.control && input.key.toLowerCase() === 'q') {
      e.preventDefault();
      app.quit();
    }
  });
}

// ------------------------------------------------------------------ store sign-in
const loginPrefs = (partition) => ({
  partition, contextIsolation: true, sandbox: true,
  preload: path.join(__dirname, 'login-preload.js'),
});

// Google refuses sign-in from browsers that announce themselves as Electron apps,
// so the login session uses a plain Chrome user agent.
function plainUserAgent(ua) {
  return ua.split(' ').filter((tok) => {
    const m = /^([^/()]+)\//.exec(tok);
    return !m || ['Mozilla', 'AppleWebKit', 'Chrome', 'Safari'].includes(m[1]);
  }).join(' ');
}

// Keys from the built-in on-screen keyboard (login-preload.js), typed as real input.
ipcMain.on('osk', (e, msg) => {
  const wc = e.sender;
  const bw = BrowserWindow.fromWebContents(wc);
  if (!msg || !bw) return;
  if (msg.cancel) { bw.close(); return; }
  if (typeof msg.text === 'string' && msg.text) { wc.insertText(msg.text); return; }
  const key = { Backspace: 'Backspace', Tab: 'Tab', Enter: 'Enter' }[msg.key];
  if (!key) return;
  const modifiers = msg.shift ? ['shift'] : [];
  wc.sendInputEvent({ type: 'keyDown', keyCode: key, modifiers });
  if (key === 'Enter') wc.sendInputEvent({ type: 'char', keyCode: '\r', modifiers });
  wc.sendInputEvent({ type: 'keyUp', keyCode: key, modifiers });
});

function storeLogin(storeId) {
  const cfg = STORES[storeId];
  if (!cfg) return Promise.resolve({ error: 'Unknown store' });
  return new Promise((resolve) => {
    let done = false;
    const ses = session.fromPartition(cfg.partition);
    ses.setUserAgent(plainUserAgent(ses.getUserAgent()));
    const prefs = loginPrefs(cfg.partition);
    const lw = new BrowserWindow({
      parent: win, modal: true, fullscreen: true, backgroundColor: '#121212',
      autoHideMenuBar: true, title: cfg.title, webPreferences: prefs,
    });
    const finish = (val) => { if (!done) { done = true; resolve(val); } if (!lw.isDestroyed()) lw.close(); };
    const check = async (url) => {
      if (done || lw.isDestroyed()) return;
      url = typeof url === 'string' ? url : lw.webContents.getURL();
      if (!url.startsWith(cfg.redirect)) return;
      try {
        const code = await cfg.code(lw.webContents, url);
        finish(code ? { code } : { error: 'The store did not return a sign-in code - please try again' });
      } catch (e) { finish({ error: String(e) }); }
    };
    lw.webContents.on('did-finish-load', () => check());
    lw.webContents.on('did-navigate', (e, url) => check(url));
    lw.webContents.on('did-navigate-in-page', (e, url) => check(url));
    if (storeId === 'gog') {
      // the code is in the redirect URL, so grab it before the (blank) page even loads
      lw.webContents.on('will-redirect', (e, url) => { if (url.startsWith(cfg.redirect)) { e.preventDefault(); check(url); } });
      lw.webContents.on('will-navigate', (e, url) => { if (url.startsWith(cfg.redirect)) { e.preventDefault(); check(url); } });
    }
    // Social logins (Google, etc.) may open a popup that reports back to the store page,
    // so popups open as real child windows with the same keyboard.
    lw.webContents.setWindowOpenHandler(() => ({
      action: 'allow',
      overrideBrowserWindowOptions: {
        parent: lw, modal: true, fullscreen: true, backgroundColor: '#121212',
        autoHideMenuBar: true, webPreferences: prefs,
      },
    }));
    lw.webContents.on('before-input-event', (e, input) => {
      if (input.type === 'keyDown' && input.key === 'Escape') finish({ cancelled: true });
    });
    lw.on('closed', () => { if (!done) { done = true; resolve({ cancelled: true }); } });
    lw.loadURL(cfg.url);
  });
}

ipcMain.handle('store-login', (e, storeId) => storeLogin(storeId || 'epic'));
ipcMain.handle('epic-login', () => storeLogin('epic'));
// Games are spawned from this process (not the backend) so they inherit the current
// Game Mode session environment (display, Steam overlay, gamescope app id).
ipcMain.handle('launch', (e, appName) => {
  const script = path.join(APP_DIR, 'bin', 'playvault-launch');
  if (!/^[\w.\-]+$/.test(appName) || !fs.existsSync(script)) return { ok: false, error: 'Cannot launch this game' };
  const child = spawn(script, [appName], { cwd: ROOT, detached: true, stdio: 'ignore', env: process.env });
  child.unref();
  return { ok: true };
});
const inGameMode = () => (process.env.XDG_CURRENT_DESKTOP || '').toLowerCase() === 'gamescope'
  || !!process.env.GAMESCOPE_WAYLAND_DISPLAY || process.env.SteamGamepadUI === '1';
ipcMain.handle('info', () => ({ gameMode: inGameMode() }));
ipcMain.handle('quit', () => app.quit());
// Restart to install a downloaded update (bin/playvault swaps it in on start). In Game Mode
// Steam has to start PlayVault again from its tile, otherwise it wouldn't be shown, so the
// app just closes there and returns { reopen: true }.
ipcMain.handle('restart', () => {
  const script = path.join(APP_DIR, 'bin', 'playvault');
  const reopen = inGameMode() || process.platform !== 'linux' || !fs.existsSync(script);
  if (!reopen) {
    spawn('bash', ['-c', 'sleep 2; exec "$0"', script], { cwd: ROOT, detached: true, stdio: 'ignore' }).unref();
  }
  setTimeout(() => app.quit(), 300);
  return { reopen };
});
ipcMain.handle('toggle-fullscreen', () => win && win.setFullScreen(!win.isFullScreen()));

// ------------------------------------------------------------------ hide while playing
async function watchGame() {
  const st = await request('GET', '/api/state');
  if (!st || !win || win.isDestroyed()) return;
  const running = st.running && st.running.stage === 'running';
  const hide = st.settings && st.settings.hide_while_playing;
  if (running) {
    if (!runningSince) runningSince = Date.now();
    if (hide && !hiddenForGame && Date.now() - runningSince > 6000) {
      hiddenForGame = true;
      win.hide();
    }
  } else {
    runningSince = 0;
    if (hiddenForGame) {
      hiddenForGame = false;
      win.show();
      win.setFullScreen(true);
      win.focus();
    }
  }
}

// ------------------------------------------------------------------ lifecycle
app.whenReady().then(async () => {
  const ok = await ensureBackend();
  createWindow();
  if (!ok) {
    win.loadURL('data:text/html,' + encodeURIComponent(
      '<body style="background:#0a0d13;color:#fff;font:20px sans-serif;padding:60px">' +
      '<h1>PlayVault could not start its backend</h1><p>Check ~/.local/share/playvault/data/logs/backend.stdout.log ' +
      'and re-run install.sh.</p></body>'));
  }
  setInterval(watchGame, 2000);
});

app.on('window-all-closed', () => app.quit());

app.on('before-quit', () => {
  if (readSettings().background_downloads === false) request('POST', '/api/quit');
});
