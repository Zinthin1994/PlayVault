// PlayVault shell: starts (or reuses) the Python backend, shows the UI fullscreen
// (a maximized window on Windows, F11 toggles), handles the Epic / GOG sign-in windows
// and hides itself while a game is running.
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

const WIN = process.platform === 'win32';
// the venv keeps its interpreter in bin/ on SteamOS and Scripts/ on Windows
const VENV_PY = WIN ? path.join(ROOT, 'venv', 'Scripts', 'python.exe') : path.join(ROOT, 'venv', 'bin', 'python');

let win = null;
let hiddenForGame = false;
let fullscreenBeforeHide = true;
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

const isOurs = (url) => typeof url === 'string' && (url === BASE || url.startsWith(`${BASE}/`));
// IPC from the main UI only (login windows have their own preload and only send 'osk')
const fromUi = (e) => !!(e.senderFrame && isOurs(e.senderFrame.url));
const uiHandle = (channel, fn) => ipcMain.handle(channel, (e, ...args) => {
  if (!fromUi(e)) throw new Error(`${channel}: not allowed from ${e.senderFrame ? e.senderFrame.url : 'unknown'}`);
  return fn(e, ...args);
});

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'settings.json'), 'utf8'));
  } catch (e) { return {}; }
}

async function ensureBackend() {
  if (await request('GET', '/api/ping')) return true;
  const py = fs.existsSync(VENV_PY) ? VENV_PY : (WIN ? 'python' : 'python3');
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
      windowsHide: true,
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
    fullscreen: !WIN,
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
  if (WIN) win.maximize();
  // The preload API (launch, quit, restart) must only ever be reachable from our own UI:
  // never navigate this window away from the backend or let it open popups.
  win.webContents.on('will-navigate', (e, url) => { if (!isOurs(url)) e.preventDefault(); });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
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
      parent: win, modal: true, fullscreen: !WIN, width: 1100, height: 760, backgroundColor: '#121212',
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
        parent: lw, modal: true, fullscreen: !WIN, width: 1100, height: 760, backgroundColor: '#121212',
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

uiHandle('store-login', (e, storeId) => storeLogin(storeId || 'epic'));
uiHandle('epic-login', () => storeLogin('epic'));
// Games are spawned from this process (not the backend) so they inherit the current
// Game Mode session environment (display, Steam overlay, gamescope app id).
uiHandle('launch', (e, appName) => {
  const script = path.join(APP_DIR, 'bin', WIN ? 'playvault-launch.cmd' : 'playvault-launch');
  // same rule as APP_NAME in evault/server.py (the launcher writes logs/<appName>.log)
  const valid = typeof appName === 'string' && /^[A-Za-z0-9][\w.\-]{0,127}$/.test(appName) && !appName.includes('..');
  if (!valid || !fs.existsSync(script)) return { ok: false, error: 'Cannot launch this game' };
  // node won't spawn a .cmd without a shell, so on Windows run the launcher module directly
  // (what playvault-launch.cmd does)
  const child = WIN
    ? spawn(fs.existsSync(VENV_PY) ? VENV_PY : 'python', ['-m', 'evault.launcher', appName], {
      cwd: ROOT, detached: true, stdio: 'ignore', windowsHide: true,
      env: { ...process.env, PYTHONPATH: APP_DIR, PLAYVAULT_HOME: ROOT },
    })
    : spawn(script, [appName], { cwd: ROOT, detached: true, stdio: 'ignore', env: process.env });
  child.unref();
  return { ok: true };
});
const inGameMode = () => (process.env.XDG_CURRENT_DESKTOP || '').toLowerCase() === 'gamescope'
  || !!process.env.GAMESCOPE_WAYLAND_DISPLAY || process.env.SteamGamepadUI === '1';
uiHandle('info', () => ({ gameMode: inGameMode() }));
uiHandle('quit', () => app.quit());
// Restart to install a downloaded update (bin/playvault swaps it in on start). In Game Mode
// Steam has to start PlayVault again from its tile, otherwise it wouldn't be shown, so the
// app just closes there and returns { reopen: true }.
uiHandle('restart', () => {
  const script = path.join(APP_DIR, 'bin', WIN ? 'playvault.cmd' : 'playvault');
  const reopen = inGameMode() || !['linux', 'win32'].includes(process.platform) || !fs.existsSync(script);
  if (!reopen) {
    if (WIN) {
      spawn('cmd.exe', ['/c', 'ping -n 3 127.0.0.1 >nul & call "' + script + '"'],
        { cwd: ROOT, detached: true, stdio: 'ignore', windowsHide: true }).unref();
    } else {
      spawn('bash', ['-c', 'sleep 2; exec "$0"', script], { cwd: ROOT, detached: true, stdio: 'ignore' }).unref();
    }
  }
  setTimeout(() => app.quit(), 300);
  return { reopen };
});
uiHandle('toggle-fullscreen', () => win && win.setFullScreen(!win.isFullScreen()));

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
      fullscreenBeforeHide = win.isFullScreen();
      win.hide();
    }
  } else {
    runningSince = 0;
    if (hiddenForGame) {
      hiddenForGame = false;
      win.show();
      win.setFullScreen(WIN ? fullscreenBeforeHide : true);
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
      '<h1>PlayVault could not start its backend</h1><p>Check ' + path.join(ROOT, 'data', 'logs', 'backend.stdout.log').replace(/&/g, '&amp;').replace(/</g, '&lt;') + ' ' +
      'and re-run the installer (' + (WIN ? 'install.ps1' : 'install.sh') + ').</p></body>'));
  }
  setInterval(watchGame, 2000);
});

app.on('window-all-closed', () => app.quit());

app.on('before-quit', () => {
  if (readSettings().background_downloads === false) request('POST', '/api/quit');
});
