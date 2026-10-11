'use strict';
/* PlayVault UI - vanilla JS, controller-first (Steam Input gamepad / keyboard / mouse). */

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const enc = encodeURIComponent;

async function api(path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch('/api/' + path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const S = {
  state: null, games: [], libLoaded: false, seenVersion: -1,
  view: 'library', detailApp: null, detail: null, jobs: [],
  filter: 'all', sort: 'title', search: '',
  focusedEl: null, focusMem: {}, cardHtml: [], parts: {},
  drives: null, proton: null, sys: null, lastRunning: null, offline: false, shell: '',
};

// ================================================================ formatting
function fmtBytes(n) {
  if (!n && n !== 0) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; n = Number(n);
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}
function fmtDur(s) {
  s = Math.round(s || 0);
  if (s < 60) return s ? `${s}s` : '0m';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}
function fmtAgo(ts) {
  if (!ts) return 'Never';
  const d = Date.now() / 1000 - ts;
  if (d < 90) return 'Just now';
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 86400 * 30) return `${Math.round(d / 86400)} days ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
function fmtEta(s) {
  if (s == null) return '—';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
}
function ratingClass(score) {
  if (!score) return 'r-none';
  if (score >= 8) return 'r-top';
  if (score >= 6) return 'r-pos';
  if (score === 5) return 'r-mix';
  return 'r-neg';
}
// colour for the SteamDB-style rating (0-100)
function sdbClass(v) {
  if (v == null) return 'r-none';
  if (v >= 85) return 'r-top';
  if (v >= 70) return 'r-pos';
  if (v >= 50) return 'r-mix';
  return 'r-neg';
}
function fmtCount(n) {
  n = Number(n || 0);
  if (n < 1000) return String(n);
  if (n < 1e6) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0).replace(/\.0$/, '')}K`;
  return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
}
const hasPdb = (tier) => !!tier && !['pending', 'unknown'].includes(tier);
const PDB_ORDER = { platinum: 5, gold: 4, silver: 3, bronze: 2, borked: 1 };
const KIND_LABEL = { install: 'Installing', update: 'Updating', repair: 'Repairing', move: 'Moving', uninstall: 'Uninstalling', prepare: 'Preparing' };

// ================================================================ toasts
function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 7000 : 4000);
}
async function safe(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg, 'good'); return r; } catch (e) { toast(e.message || String(e), 'error'); return null; }
}

// ================================================================ focus + spatial navigation
const modals = [];
const topModal = () => modals[modals.length - 1] || null;
const scopeEl = () => (topModal() ? topModal().el : $('#app'));
const viewKey = () => (topModal() ? 'modal:' + topModal().id : S.view + (S.view === 'detail' ? ':' + S.detailApp : ''));
const visible = (el) => el.offsetParent !== null && !el.disabled;

function setFocus(el, noScroll) {
  if (!el) return;
  if (S.focusedEl && S.focusedEl !== el) S.focusedEl.classList.remove('focused');
  S.focusedEl = el;
  el.classList.add('focused');
  if (el.dataset.fid) S.focusMem[viewKey()] = el.dataset.fid;
  if (!noScroll) el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  updateHints();
}
function restoreFocus(noScroll) {
  const scope = scopeEl();
  const fid = S.focusMem[viewKey()];
  let el = fid ? scope.querySelector(`[data-fid="${CSS.escape(fid)}"]`) : null;
  S.autoFocused = !el;
  if (!el || !visible(el)) {
    // prefer the page content over the header; don't park focus on a tab while content is loading
    const main = topModal() ? scope : ($('#main') || scope);
    el = main.querySelector('[data-autofocus].focusable') || [...main.querySelectorAll('.focusable')].find(visible);
    if (!el && !$('#main')) el = [...scope.querySelectorAll('.focusable')].find(visible);
  }
  if (el) setFocus(el, noScroll);
}
function patch(el, html) {
  if (!el) return;
  const fid = S.focusedEl && el.contains(S.focusedEl) ? S.focusedEl.dataset.fid : null;
  el.innerHTML = html;
  if (fid) {
    const n = el.querySelector(`[data-fid="${CSS.escape(fid)}"]`);
    if (n) setFocus(n, true); else restoreFocus(true);
  }
}
function move(dir) {
  const scope = scopeEl();
  const items = [...scope.querySelectorAll('.focusable')].filter(visible);
  const cur = S.focusedEl && scope.contains(S.focusedEl) && visible(S.focusedEl) ? S.focusedEl : null;
  if (!cur) { if (items[0]) setFocus(items[0]); return; }
  const r = cur.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  let best = null, bestScore = Infinity;
  for (const el of items) {
    if (el === cur) continue;
    const b = el.getBoundingClientRect();
    const x = b.left + b.width / 2, y = b.top + b.height / 2;
    let primary, secondary;
    if (dir === 'down') { if (b.top < r.bottom - 4 && y <= cy + 2) continue; if (y <= cy) continue; primary = Math.max(0, b.top - r.bottom); secondary = Math.abs(x - cx); }
    else if (dir === 'up') { if (y >= cy) continue; primary = Math.max(0, r.top - b.bottom); secondary = Math.abs(x - cx); }
    else if (dir === 'right') { if (x <= cx + 2 || b.left < r.right - r.width * 0.5) continue; primary = Math.max(0, b.left - r.right); secondary = Math.abs(y - cy) * 3; }
    else { if (x >= cx - 2 || b.right > r.left + r.width * 0.5) continue; primary = Math.max(0, r.left - b.right); secondary = Math.abs(y - cy) * 3; }
    const score = primary + secondary * 0.8;
    if (score < bestScore) { bestScore = score; best = el; }
  }
  if (best) { setFocus(best); S.autoFocused = false; }
}
function activate(el) { if (el && el.dataset.action) runAction(el); }

// ================================================================ modals
let modalSeq = 0;
function openModal({ title = '', msg = '', body = '', foot = '', cls = '', handlers = {}, keys = {}, onClose }) {
  const wrap = document.createElement('div');
  wrap.className = 'modal-wrap';
  const id = 'm' + (++modalSeq);
  wrap.innerHTML = `<div class="modal ${cls}">${title ? `<h2>${esc(title)}</h2>` : ''}${msg ? `<p class="msg">${msg}</p>` : ''}
    <div class="body">${body}</div>${foot ? `<div class="foot">${foot}</div>` : ''}</div>`;
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeModal(); });
  $('#modals').appendChild(wrap);
  const m = { id, el: wrap, handlers, keys, onClose, prevFocus: S.focusedEl };
  modals.push(m);
  restoreFocus(true);
  updateHints();
  return m;
}
function closeModal(silent) {
  const m = modals.pop();
  if (!m) return;
  m.el.remove();
  if (!silent && m.onClose) m.onClose();
  if (m.prevFocus && document.body.contains(m.prevFocus)) setFocus(m.prevFocus, true); else restoreFocus(true);
  updateHints();
}
function setModalBody(m, html) { patch(m.el.querySelector('.body'), html); }

function confirmBox({ title, msg, ok = 'OK', cancel = 'Cancel', danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; closeModal(true); resolve(v); } };
    openModal({
      title, msg,
      foot: `<button class="btn focusable" data-action="m" data-k="no" data-fid="no">${esc(cancel)}</button>
             <button class="btn ${danger ? 'danger' : 'primary'} focusable" data-action="m" data-k="yes" data-fid="yes" data-autofocus>${esc(ok)}</button>`,
      handlers: { yes: () => finish(true), no: () => finish(false) },
      onClose: () => { if (!done) { done = true; resolve(false); } },
    });
  });
}

function choose({ title, msg = '', options }) {
  return new Promise((resolve) => {
    let done = false;
    const body = options.map((o, i) => `
      <div class="opt focusable ${o.selected ? 'selected' : ''}" data-action="m" data-k="pick" data-i="${i}" data-fid="o${i}" ${o.selected ? 'data-autofocus' : ''}>
        <div class="grow"><div>${esc(o.label)}</div>${o.desc ? `<div class="d">${esc(o.desc)}</div>` : ''}</div>
        ${o.selected ? '<span class="check">✓</span>' : ''}
      </div>`).join('') || '<div class="empty">Nothing to choose from</div>';
    openModal({
      title, msg, body,
      handlers: { pick: (d) => { done = true; closeModal(true); resolve(options[+d.i].value); } },
      onClose: () => { if (!done) { done = true; resolve(null); } },
    });
  });
}

// ---------------------------------------------------------------- on-screen keyboard
function keyboard({ title, value = '', hint = '' }) {
  return new Promise((resolve) => {
    let val = value, shift = false, sym = false, done = false;
    const rowsAbc = ['1234567890', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm'];
    const rowsSym = ['1234567890', '!@#$%^&*()', "-_=+[]{};:", "'\",.<>/?\\|~"];
    const keysHtml = () => {
      const rows = sym ? rowsSym : rowsAbc;
      let h = rows.map((row, ri) => `<div class="krow">${[...row].map((c, ci) => {
        const ch = shift && !sym ? c.toUpperCase() : c;
        return `<div class="key focusable" data-action="m" data-k="key" data-v="${esc(ch)}" data-fid="k${ri}_${ci}">${esc(ch)}</div>`;
      }).join('')}</div>`).join('');
      h += `<div class="krow">
        <div class="key w2 focusable" data-action="m" data-k="shift" data-fid="shift">${shift ? 'SHIFT ●' : 'Shift'}</div>
        <div class="key w2 focusable" data-action="m" data-k="sym" data-fid="sym">${sym ? 'ABC' : '#+='}</div>
        <div class="key w4 focusable" data-action="m" data-k="space" data-fid="space">Space</div>
        <div class="key w2 focusable" data-action="m" data-k="bs" data-fid="bs">⌫</div>
      </div><div class="krow">
        <div class="key w2 focusable" data-action="m" data-k="clear" data-fid="clear">Clear</div>
        <div class="key w2 focusable" data-action="m" data-k="paste" data-fid="paste">Paste</div>
        <div class="key w2 focusable" data-action="m" data-k="cancel" data-fid="cancel">Cancel</div>
        <div class="key w2 primary focusable" data-action="m" data-k="done" data-fid="done">Done</div>
      </div>`;
      return h;
    };
    const finish = (v) => { if (!done) { done = true; closeModal(true); resolve(v); } };
    const upd = () => { m.el.querySelector('.input').innerHTML = `${esc(val)}<span class="caret"></span>`; };
    const rebuild = () => patch(m.el.querySelector('.keys'), keysHtml());
    const add = (c) => { val += c; if (shift) { shift = false; rebuild(); } upd(); };
    const m = openModal({
      title, cls: 'osk', msg: hint ? esc(hint) : '',
      body: `<div class="input">${esc(val)}<span class="caret"></span></div><div class="keys">${keysHtml()}</div>`,
      handlers: {
        key: (d) => add(d.v),
        space: () => add(' '),
        bs: () => { val = val.slice(0, -1); upd(); },
        clear: () => { val = ''; upd(); },
        shift: () => { shift = !shift; rebuild(); },
        sym: () => { sym = !sym; rebuild(); },
        paste: async () => { try { val += (await navigator.clipboard.readText()).trim(); upd(); } catch (e) { toast('Clipboard not available', 'error'); } },
        cancel: () => finish(null),
        done: () => finish(val),
      },
      keys: {
        x: () => { val = val.slice(0, -1); upd(); },
        y: () => add(' '),
        menu: () => finish(val),
      },
      onClose: () => { if (!done) { done = true; resolve(null); } },
    });
    m.osk = { add, bs: () => { val = val.slice(0, -1); upd(); }, done: () => finish(val) };
    const k = m.el.querySelector('[data-fid="k1_0"]');
    if (k) setFocus(k, true);
  });
}

// ================================================================ actions
const ACTIONS = {
  m: (d) => { const m = topModal(); if (m && m.handlers[d.k]) m.handlers[d.k](d); },
  tab: (d) => setView(d.tab),
  filter: (d) => setFilter(d.f),
  sort: () => cycleSort(),
  search: () => openSearch(),
  clearSearch: () => { S.search = ''; renderMain(); },
  refreshLib: () => safe(() => api('library/refresh', { force: true }), 'Syncing your libraries...'),
  open: (d) => openDetail(d.app),
  back: () => back(),
  play: (d) => launchGame(d.app),
  stop: async () => { if (await confirmBox({ title: 'Stop the game?', msg: 'Unsaved progress will be lost.', ok: 'Stop game', danger: true })) safe(() => api('stop')); },
  install: (d) => openInstall(d.app),
  update: (d) => safe(() => api('update', { app_name: d.app }), 'Update queued'),
  cancelJob: (d) => safe(() => api(`jobs/${d.id}/cancel`, {})),
  frontJob: (d) => safe(() => api(`jobs/${d.id}/front`, {})),
  clearJobs: () => safe(() => api('jobs/clear', {})),
  updateAll: () => safe(async () => { const r = await api('update-all', {}); toast(r.queued ? `${r.queued} update(s) queued` : 'Everything is up to date', 'good'); }),
  fav: (d) => toggleGame(d.app, 'favorite'),
  steamAdd: (d) => steamAdd([d.app]),
  steamRemove: (d) => safe(async () => { await api('steam/remove', { apps: [d.app] }); afterSteamChange(); }),
  steamApply: () => steamApply(),
  props: (d) => openGameSettings(d.app),
  more: (d) => openMore(d.app),
  login: () => doLogin('epic'),
  loginGog: () => doLogin('gog'),
  loginCode: () => doLoginCode(),
  loginGogCode: () => doLoginGogCode(),
  loginMore: () => loginMore(),
  logoutGog: async () => { if (await confirmBox({ title: 'Sign out of GOG?', msg: 'Installed GOG games stay on disk and keep working.', ok: 'Sign out', danger: true })) { await safe(() => api('auth/gog/logout', {})); tick(); } },
  setting: (d) => editSetting(d.key),
  toggleSetting: (d) => safe(() => saveSetting(d.key, !S.state.settings[d.key])),
  steamAddAll: () => steamAdd('all'),
  addSelf: () => safe(async () => { await api('steam/add-self', {}); afterSteamChange(); }, 'PlayVault tile staged for Steam'),
  ratingsAll: () => safe(() => api('ratings/refresh', {}), 'Refreshing all ratings in the background'),
  prepare: () => safe(() => api('prepare-proton', {}), 'Preparing Proton - see Downloads'),
  logout: async () => { if (await confirmBox({ title: 'Sign out of Epic?', msg: 'Installed games stay on disk.', ok: 'Sign out', danger: true })) { await safe(() => api('auth/logout', {})); tick(); } },
  quit: () => doQuit(),
  exit: () => confirmExit(),
  showLog: (d) => showLog(d.app),
  noop: () => {},
};
function runAction(el) { const fn = ACTIONS[el.dataset.action]; if (fn) fn(el.dataset, el); }

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  if (el.classList.contains('focusable')) { setFocus(el, true); S.autoFocused = false; }
  runAction(el);
});
document.addEventListener('mousemove', () => document.body.classList.add('mouse'), { passive: true });

// ================================================================ input: gamepad + keyboard
const BTN = { 0: 'a', 1: 'b', 2: 'x', 3: 'y', 4: 'lb', 5: 'rb', 8: 'view', 9: 'menu', 12: 'up', 13: 'down', 14: 'left', 15: 'right' };
const DIRS = ['up', 'down', 'left', 'right'];
let gpPrev = {}, gpNext = {};
function gpLoop(ts) {
  const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
  const now = {};
  let scrollY = 0;
  for (const p of pads) {
    p.buttons.forEach((b, i) => { if (b && b.pressed && BTN[i]) now[BTN[i]] = true; });
    const ax = p.axes[0] || 0, ay = p.axes[1] || 0;
    if (ay < -0.6) now.up = true; if (ay > 0.6) now.down = true;
    if (ax < -0.6) now.left = true; if (ax > 0.6) now.right = true;
    const ry = p.axes.length > 3 ? p.axes[3] : 0;
    if (Math.abs(ry) > 0.25) scrollY = ry;
  }
  for (const k of Object.keys(now)) {
    if (!gpPrev[k]) { press(k); gpNext[k] = ts + 400; }
    else if (DIRS.includes(k) && ts >= gpNext[k]) { press(k); gpNext[k] = ts + 90; }
  }
  if (scrollY) {
    const m = topModal();
    const target = m ? (m.el.querySelector('.logbox') || m.el.querySelector('.body')) : $('main');
    if (target) target.scrollTop += scrollY * 22;
  }
  gpPrev = now;
  requestAnimationFrame(gpLoop);
}
requestAnimationFrame(gpLoop);

function press(k) {
  document.body.classList.remove('mouse');
  const m = topModal();
  if (m && m.keys && m.keys[k]) { m.keys[k](); return; }
  if (DIRS.includes(k)) return move(k);
  if (k === 'a') return activate(S.focusedEl);
  if (k === 'b') return back();
  if (m || !S.state || !S.state.user) return;
  if (k === 'lb') return cycleTab(-1);
  if (k === 'rb') return cycleTab(1);
  if (k === 'x') return contextX();
  if (k === 'y') { if (S.view === 'library') openSearch(); return; }
  if (k === 'view' && S.view === 'library') return cycleFilter();
  if (k === 'menu' && S.view === 'library') return cycleSort();
}

document.addEventListener('keydown', (e) => {
  const m = topModal();
  if (m && m.osk) {
    if (e.key.length === 1 && !e.ctrlKey && !e.altKey) { m.osk.add(e.key); e.preventDefault(); return; }
    if (e.key === 'Backspace') { m.osk.bs(); e.preventDefault(); return; }
    if (e.key === 'Enter') { m.osk.done(); e.preventDefault(); return; }
    if (e.ctrlKey && e.key === 'v') return;
  }
  const map = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', Enter: 'a', ' ': 'a',
    Escape: 'b', Backspace: 'b', PageUp: 'lb', PageDown: 'rb', q: 'lb', e: 'rb', x: 'x', y: 'y', '/': 'y', s: 'menu', f: 'view' };
  const k = map[e.key];
  if (k) { e.preventDefault(); press(k); }
});

// ================================================================ navigation
const TABS = ['library', 'downloads', 'settings', 'accounts'];
function setView(v) {
  S.view = v;
  if (v === 'settings') loadSettingsData();
  if (v === 'downloads') loadJobs();
  renderShell();
}
function cycleTab(dir) {
  const cur = S.view === 'detail' ? 'library' : S.view;
  const i = (TABS.indexOf(cur) + dir + TABS.length) % TABS.length;
  setView(TABS[i]);
}
function back() {
  if (topModal()) return closeModal();
  if (S.state && !S.state.user) return confirmExit();
  if (S.view === 'detail') { S.view = 'library'; S.detail = null; renderShell(); return; }
  if (S.view !== 'library') return setView('library');
  confirmExit();
}
function doQuit() { if (window.vault) window.vault.quit(); else window.close(); }
async function confirmExit() {
  if (topModal()) return;
  const st = S.state || {};
  const busy = st.jobs_active || 0;
  const bg = !st.settings || st.settings.background_downloads !== false;
  let msg = 'You can reopen it from your Steam library.';
  if (busy) msg = bg ? `${busy} download(s) will keep going in the background.` : `${busy} download(s) will stop.`;
  if (await confirmBox({ title: 'Exit PlayVault?', msg, ok: 'Exit' })) doQuit();
}
function openDetail(app) {
  S.view = 'detail';
  S.detailApp = app;
  S.detail = null;
  S.parts = {};
  renderShell();
  loadDetail();
}
function contextX() {
  if (S.view === 'library' && S.focusedEl && S.focusedEl.dataset.app) return quickMenu(S.focusedEl.dataset.app);
  if (S.view === 'detail' && S.detailApp) return openMore(S.detailApp);
}

// ================================================================ data
async function tick() {
  let st;
  try {
    st = await api('state');
    if (S.offline) { S.offline = false; renderShell(); }
  } catch (e) {
    if (!S.offline) { S.offline = true; toast('Lost connection to the PlayVault backend - retrying...', 'error'); }
    return;
  }
  const first = !S.state;
  const prevUser = S.state && S.state.user;
  const prevRunning = S.state && S.state.running;
  S.state = st;
  if (first) {
    S.filter = st.settings.filter || 'all';
    S.sort = st.settings.sort || 'title';
  }
  document.body.classList.toggle('cards-small', st.settings.card_size === 'small');
  document.body.classList.toggle('cards-large', st.settings.card_size === 'large');
  document.documentElement.style.setProperty('--accent', st.settings.accent || '#4f8cff');
  if (prevRunning && !st.running) onGameEnded(prevRunning.app_name);
  if (first || !!prevUser !== !!st.user) { S.seenVersion = -1; renderShell(); }
  else { updateHeader(); if (S.view === 'accounts' && !topModal()) renderMain(true); }
  if (st.user && st.version !== S.seenVersion) {
    S.seenVersion = st.version;
    await refreshData();
  }
}
async function refreshData() {
  const jobs = loadJobs();
  if (S.view === 'library' || !S.libLoaded) await loadLibrary();
  if (S.view === 'detail') await loadDetail();
  await jobs;
  if (S.view === 'settings') renderMain(true);
}
async function loadLibrary() {
  try {
    const r = await api('library');
    S.games = r.games;
    S.libLoaded = true;
    if (S.view === 'library') updateLibrary();
  } catch (e) { /* shown via tick */ }
}
async function loadDetail() {
  const app = S.detailApp;
  try {
    const d = await api('game/' + enc(app));
    if (S.view !== 'detail' || S.detailApp !== app) return;
    S.detail = d;
    updateDetail();
  } catch (e) { toast(e.message, 'error'); }
}
async function loadJobs() {
  try {
    const r = await api('jobs');
    S.jobs = r.jobs;
    if (S.view === 'downloads') renderMain(true);
  } catch (e) { /* ignore */ }
}
async function loadSettingsData() {
  const [d, p, s] = await Promise.all([api('drives').catch(() => null), api('proton').catch(() => null), api('system').catch(() => null)]);
  S.drives = d ? d.drives : []; S.proton = p ? p.tools : []; S.sys = s;
  if (S.view === 'settings') renderMain(true);
}
async function onGameEnded(app) {
  try {
    const d = await api('game/' + enc(app));
    if (d.last_launch && !d.last_launch.ok) toast(`${d.title}: ${d.last_launch.error}`, 'error');
  } catch (e) { /* ignore */ }
}

// ================================================================ rendering: shell
function renderShell() {
  const app = $('#app');
  S.cardHtml = []; S.parts = {};
  if (!S.state) { app.innerHTML = `<div class="login"><div class="spinner"></div></div>`; return; }
  if (!S.state.user) { app.innerHTML = renderLogin() + `<div class="hints">${hintsHtml()}</div>`; restoreFocus(true); return; }
  app.innerHTML = `<header id="hdr">${headerHtml()}</header><main id="main"></main><div class="hints" id="hints">${hintsHtml()}</div>`;
  renderMain();
}
function renderMain(isUpdate) {
  const main = $('#main');
  if (!main) return;
  if (S.view === 'library') {
    if (!isUpdate) { main.innerHTML = `<div class="page"><div id="lib-top"></div><div class="grid" id="grid"></div></div>`; S.cardHtml = []; }
    updateLibrary();
  } else if (S.view === 'detail') {
    if (!isUpdate) { main.innerHTML = `<div class="detail" id="detail"><div class="page"><div class="spinner"></div></div></div>`; main.scrollTop = 0; S.parts = {}; }
    if (S.detail) updateDetail();
  } else if (S.view === 'downloads') {
    if (isUpdate) patch(main, downloadsHtml()); else main.innerHTML = downloadsHtml();
  } else if (S.view === 'settings') {
    if (isUpdate) patch(main, settingsHtml()); else main.innerHTML = settingsHtml();
  } else if (S.view === 'accounts') {
    const html = accountsHtml();
    if (isUpdate) { if (main.dataset.html !== html) patch(main, html); } else main.innerHTML = html;
    main.dataset.html = html;
  }
  if (!isUpdate || !S.focusedEl || !document.body.contains(S.focusedEl)) restoreFocus(!isUpdate ? false : true);
  updateHeader();
}
function headerHtml() {
  const st = S.state;
  const tab = (id, label, extra = '') => `<div class="tab focusable ${(S.view === id || (id === 'library' && S.view === 'detail')) ? 'active' : ''}"
    data-action="tab" data-tab="${id}" data-fid="tab:${id}">${label}${extra}</div>`;
  const running = st.running;
  let playing = '';
  if (running) {
    const g = S.games.find((x) => x.app_name === running.app_name);
    playing = `<div class="playing-pill">${running.stage === 'running' ? 'Playing' : 'Starting'} ${esc(g ? g.title : running.app_name)}
      <button class="btn small danger focusable" data-action="stop" data-fid="hdr:stop">Stop</button></div>`;
  }
  const now = new Date();
  return `<div class="brand"><div class="logo">PV</div>PlayVault</div>
    <div class="tabs"><span class="lbrb">LB</span>
      ${tab('library', 'Library')}
      ${tab('downloads', 'Downloads', st.jobs_active ? `<span class="count">${st.jobs_active}</span>` : '')}
      ${tab('settings', 'Settings')}
      ${tab('accounts', 'Accounts', st.accounts && (!st.accounts.epic || !st.accounts.gog) ? '<span class="count">+</span>' : '')}
    <span class="lbrb">RB</span></div>
    <div class="header-right">${playing}<span>${esc([...new Set([st.accounts && st.accounts.epic, st.accounts && st.accounts.gog].filter(Boolean))].join(' · ') || st.user || '')}</span>
      <span class="clock">${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
      <button class="btn small focusable" data-action="exit" data-fid="hdr:exit" title="Exit PlayVault">⏻ Exit</button></div>`;
}
function updateHeader() {
  const h = $('#hdr');
  if (!h || !S.state || !S.state.user) return;
  const html = headerHtml();
  if (h.dataset.html !== html) { patch(h, html); h.dataset.html = html; }
}
function hintsHtml() {
  const g = (cls, t) => `<span class="glyph ${cls}">${t}</span>`;
  const h = (glyph, label) => `<span class="hint">${glyph}${esc(label)}</span>`;
  const m = topModal();
  const out = [];
  if (m && m.osk) {
    out.push(h(g('g-a', 'A'), 'Type'), h(g('g-x', 'X'), 'Backspace'), h(g('g-y', 'Y'), 'Space'), h(g('g-n wide', '☰'), 'Done'), h(g('g-b', 'B'), 'Cancel'));
  } else if (m) {
    out.push(h(g('g-a', 'A'), 'Select'), h(g('g-b', 'B'), 'Close'));
  } else if (!S.state || !S.state.user) {
    out.push(h(g('g-a', 'A'), 'Select'), h(g('g-b', 'B'), 'Exit'));
  } else if (S.view === 'library') {
    out.push(h(g('g-a', 'A'), 'Details'), h(g('g-x', 'X'), 'Options'), h(g('g-y', 'Y'), 'Search'),
      h(g('g-n wide', '⧉'), 'Filter'), h(g('g-n wide', '☰'), 'Sort'), h(g('g-b', 'B'), 'Exit'));
  } else if (S.view === 'detail') {
    out.push(h(g('g-a', 'A'), 'Select'), h(g('g-x', 'X'), 'More'), h(g('g-b', 'B'), 'Back'));
  } else {
    out.push(h(g('g-a', 'A'), 'Select'), h(g('g-b', 'B'), 'Library'));
  }
  const left = S.state && S.state.user && !m ? `<div class="left">${h(g('g-n wide', 'LB'), '')}${h(g('g-n wide', 'RB'), 'Switch tabs')}</div>` : '';
  return left + out.join('');
}
function updateHints() { const el = $('#hints') || $('.hints'); if (el) el.innerHTML = hintsHtml(); }
setInterval(() => { const c = $('.clock'); if (c) c.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }, 15000);

// ================================================================ login
function renderLogin() {
  const st = S.state;
  const missing = [];
  if (!st.legendary) missing.push('legendary (Epic client) is not installed');
  if (!st.gogdl) missing.push('gogdl (GOG client) is not installed');
  if (!st.umu) missing.push('umu-launcher (Proton runner) is not installed');
  return `<div class="login"><div class="box">
    <div class="logo-big">PV</div>
    <h1>PlayVault</h1>
    <p>Your Epic Games and GOG libraries in Game Mode - with Steam ratings, Proton and one-press play.<br>Sign in to a store to get started. You can add the other one later in Settings.</p>
    ${missing.length ? `<div class="banner error">${missing.map(esc).join('<br>')} - re-run install.sh</div>` : ''}
    <div class="btns">
      ${window.vault ? `<button class="btn primary big focusable" data-action="login" data-fid="login" data-autofocus>Sign in with Epic Games</button>
      <button class="btn primary big focusable" data-action="loginGog" data-fid="login-gog">Sign in with GOG</button>` : ''}
      <button class="btn focusable" data-action="loginMore" data-fid="code">Sign in with a code instead</button>
      <button class="btn focusable" data-action="quit" data-fid="quit">Exit</button>
    </div>
    <p style="margin-top:26px;font-size:.85rem">The sign-in window has its own keyboard - it pops up when you select a text box.
      Signing in once from Desktop Mode also works - PlayVault remembers you.</p>
  </div></div>`;
}
async function submitCode(code, store = 'epic') {
  if (!code) return;
  toast('Signing in...');
  const r = await safe(() => api(store === 'gog' ? 'auth/gog/code' : 'auth/code', { code }));
  if (r && r.ok) { toast(`Signed in to ${store === 'gog' ? 'GOG' : 'Epic'} as ${r.message}`, 'good'); tick(); }
  else if (r) toast(r.message || 'Sign-in failed', 'error');
}
async function doLogin(store = 'epic') {
  const r = await window.vault.login(store);
  if (r && r.code) submitCode(r.code, store);
  else if (r && r.error) toast(r.error, 'error');
}
async function loginMore() {
  const v = await choose({ title: 'Sign in with a code', options: [
    { value: 'epic', label: 'Epic Games', desc: 'Use a code from legendary.gl/epiclogin' },
    { value: 'gog', label: 'GOG', desc: 'Use the address GOG shows after signing in' }] });
  if (v === 'epic') doLoginCode();
  if (v === 'gog') doLoginGogCode();
}
async function doLoginGogCode() {
  const ok = await confirmBox({
    title: 'Sign in to GOG with a code',
    msg: 'On a phone or PC, open <b>auth.gog.com/auth?client_id=46899977096215655&amp;response_type=code&amp;layout=galaxy&amp;redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient</b> and sign in. You end up on a blank page - copy its address (it contains <b>code=</b>) and paste or type it on the next screen.',
    ok: 'Continue', cancel: 'Back',
  });
  if (ok) submitCode(await keyboard({ title: 'GOG code or address', hint: 'Paste the whole address or just the code value' }), 'gog');
}
async function doLoginCode() {
  await confirmBox({
    title: 'Sign in with a code',
    msg: 'On a phone or PC, open <b>legendary.gl/epiclogin</b>, sign in, and copy the <b>authorizationCode</b> value (32 characters). Then type or paste it on the next screen.',
    ok: 'Continue', cancel: 'Back',
  }) && submitCode(await keyboard({ title: 'Authorization code', hint: 'Paste or type the authorizationCode value' }));
}

// ================================================================ library
const FILTERS = [['all', 'All'], ['installed', 'Installed'], ['favorites', 'Favorites'], ['updates', 'Updates'], ['notinstalled', 'Not installed'],
  ['epic', 'Epic'], ['gog', 'GOG'], ['hidden', 'Hidden']];
const SORTS = [['title', 'Title'], ['rating', 'Steam rating'], ['recent', 'Recently played'], ['playtime', 'Most played'], ['protondb', 'ProtonDB'], ['size', 'Install size']];
const STORE_NAME = { epic: 'Epic', gog: 'GOG' };
function multiStore() {
  if (S.multiFor !== S.games) { S.multiFor = S.games; S.multi = new Set(S.games.map((g) => g.store)).size > 1; }
  return S.multi;
}
function filtersShown() {
  return FILTERS.filter(([id]) => (id !== 'hidden' || S.state.settings.show_hidden) && (!STORE_NAME[id] || multiStore()));
}
function applyFilter(list, f) {
  switch (f) {
    case 'epic': case 'gog': return list.filter((g) => g.store === f && !g.hidden);
    case 'installed': return list.filter((g) => g.installed && !g.hidden);
    case 'favorites': return list.filter((g) => g.favorite && !g.hidden);
    case 'updates': return list.filter((g) => g.update_available && !g.hidden);
    case 'notinstalled': return list.filter((g) => !g.installed && !g.hidden);
    case 'hidden': return list.filter((g) => g.hidden);
    default: return list.filter((g) => !g.hidden);
  }
}
function visibleGames() {
  let list = applyFilter(S.games, S.filter);
  if (S.search) {
    const q = S.search.toLowerCase();
    list = list.filter((g) => g.title.toLowerCase().includes(q) || (g.developer || '').toLowerCase().includes(q));
  }
  const t = (a, b) => a.title.localeCompare(b.title);
  const sorters = {
    title: t,
    rating: (a, b) => ((b.rating && b.rating.total ? b.rating.sdb : -1) - (a.rating && a.rating.total ? a.rating.sdb : -1)) || t(a, b),
    recent: (a, b) => (b.last_played - a.last_played) || (b.installed - a.installed) || t(a, b),
    playtime: (a, b) => (b.playtime - a.playtime) || t(a, b),
    protondb: (a, b) => ((PDB_ORDER[b.protondb] || 0) - (PDB_ORDER[a.protondb] || 0)) || t(a, b),
    size: (a, b) => (b.install_size - a.install_size) || t(a, b),
  };
  return [...list].sort(sorters[S.sort] || t);
}
function setFilter(f) {
  S.filter = f;
  api('settings', { filter: f }).catch(() => {});
  S.focusMem.library = 'chip:' + f;
  updateLibrary();
}
function cycleFilter() {
  const ids = filtersShown().map((x) => x[0]);
  setFilter(ids[(ids.indexOf(S.filter) + 1) % ids.length]);
  toast('Showing: ' + FILTERS.find((x) => x[0] === S.filter)[1]);
}
function cycleSort() {
  const ids = SORTS.map((x) => x[0]);
  S.sort = ids[(ids.indexOf(S.sort) + 1) % ids.length];
  api('settings', { sort: S.sort }).catch(() => {});
  if (S.view === 'library') updateLibrary();
  toast('Sorted by ' + SORTS.find((x) => x[0] === S.sort)[1]);
}
async function openSearch() {
  const q = await keyboard({ title: 'Search your library', value: S.search });
  if (q === null) return;
  S.search = q.trim();
  S.focusMem.library = null;
  updateLibrary();
  const first = $('#grid .card');
  if (first) setFocus(first);
}

function libTopHtml() {
  const st = S.state;
  const counts = {};
  for (const [id] of FILTERS) counts[id] = applyFilter(S.games, id).length;
  let banners = '';
  if (st.steam_pending) {
    banners += `<div class="banner info"><div class="grow"><b>${st.steam_pending} Steam library change${st.steam_pending > 1 ? 's' : ''} waiting.</b>
      Steam needs a quick restart to show new game tiles.</div>
      <button class="btn primary small focusable" data-action="steamApply" data-fid="ban:steam">Restart Steam now</button></div>`;
  }
  if (st.library.refreshing) banners += `<div class="banner"><div class="spinner"></div><div class="grow">Syncing your ${[st.accounts.epic && 'Epic', st.accounts.gog && 'GOG'].filter(Boolean).join(' and ')} library...</div></div>`;
  if (st.library.error) banners += `<div class="banner error"><div class="grow">Library sync failed: ${esc(st.library.error)}</div>
    <button class="btn small focusable" data-action="refreshLib" data-fid="ban:retry">Retry</button></div>`;
  if (st.ratings_pending > 0 && st.settings.ratings_enabled) banners += `<div class="banner"><div class="spinner"></div>
    <div class="grow">Fetching Steam ratings and ProtonDB tiers... ${st.ratings_pending} left</div></div>`;
  const chips = filtersShown().map(([id, label]) => `<div class="chip focusable ${S.filter === id ? 'active' : ''}" data-action="filter" data-f="${id}"
    data-fid="chip:${id}">${label}<span class="n">${counts[id]}</span></div>`).join('');
  const sortLabel = SORTS.find((x) => x[0] === S.sort)[1];
  return `${banners}<div class="toolbar">${chips}<div class="spacer"></div>
    ${S.search ? `<button class="btn small focusable" data-action="clearSearch" data-fid="tb:clear">✕ "${esc(S.search)}"</button>` : ''}
    <button class="btn small focusable" data-action="search" data-fid="tb:search">Search</button>
    <button class="btn small focusable" data-action="sort" data-fid="tb:sort">Sort: ${esc(sortLabel)}</button>
    <button class="btn small focusable" data-action="refreshLib" data-fid="tb:refresh">Sync</button></div>`;
}
function cardHtml(g, i) {
  const r = g.rating;
  const showRatings = S.state.settings.ratings_enabled;
  const ratingPill = !showRatings ? '' : r && r.total
    ? `<span class="pill ${sdbClass(r.sdb)}" title="Rating ${r.sdb}% · ${r.pct}% positive of ${r.total.toLocaleString()} Steam reviews">${r.sdb}%<span class="cnt">${fmtCount(r.total)}</span></span>`
    : `<span class="pill r-none" title="${g.ratings_status === 'pending' ? 'Looking up Steam reviews...' : 'No Steam reviews found'}">?</span>`;
  const pdb = !showRatings ? '' : hasPdb(g.protondb)
    ? `<span class="pdb pdb-${esc(g.protondb)}">${esc(g.protondb)}</span>`
    : `<span class="pdb pdb-unknown" title="Linux playability unknown - no ProtonDB tier yet">?</span>`;
  const ac = g.anticheat === 'Denied' || g.anticheat === 'Broken' ? `<span class="pill r-neg" title="Anti-cheat blocks Linux">No Linux AC</span>` : '';
  const job = g.job;
  const bar = job ? `<div class="bar">${esc(job.status === 'queued' ? 'Queued' : KIND_LABEL[job.kind] || job.kind)}
      <div class="track"><i style="width:${Math.max(2, job.progress || 0)}%"></i></div>${job.status === 'running' ? Math.floor(job.progress || 0) + '%' : ''}</div>` : '';
  return `<div class="card focusable ${g.installed ? '' : 'not-installed'}" data-action="open" data-app="${esc(g.app_name)}" data-fid="card:${esc(g.app_name)}" ${i === 0 ? 'data-autofocus' : ''}>
    <div class="cover"><div class="fallback">${esc(g.title)}</div>
      ${g.cover ? `<img loading="lazy" src="${esc(g.cover)}" data-fb="${esc(g.cover_fallback || '')}" alt=""
        onerror="if(this.dataset.fb){this.src=this.dataset.fb;this.dataset.fb=''}else{this.remove()}">` : ''}
      <div class="corner left">${g.installed ? '<span class="ic installed" title="Installed">✓</span>' : ''}${g.update_available ? '<span class="ic update" title="Update available">↑</span>' : ''}</div>
      <div class="corner right">${g.favorite ? '<span class="ic fav">★</span>' : ''}${g.in_steam ? '<span class="ic steam" title="In Steam library">S</span>' : ''}</div>
      ${bar}
    </div>
    <div class="meta"><div class="title">${esc(g.title)}</div><div class="sub">${multiStore() ? `<span class="store s-${esc(g.store)}">${STORE_NAME[g.store] || ''}</span>` : ''}${ratingPill}${pdb}${ac}</div></div>
  </div>`;
}
function updateLibrary() {
  const top = $('#lib-top'), grid = $('#grid');
  if (!top || !grid) return;
  const topHtml = libTopHtml();
  if (top.dataset.html !== topHtml) { patch(top, topHtml); top.dataset.html = topHtml; }
  const list = visibleGames();
  if (!S.libLoaded) { grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="spinner" style="margin:auto"></div></div>`; return; }
  if (!list.length) {
    const msg = S.search ? `No games match "${esc(S.search)}"` : S.games.length ? 'No games in this view' : 'Your library is empty - press Sync to load it';
    patch(grid, `<div class="empty" style="grid-column:1/-1">${msg}</div>`);
    grid.dataset.keys = ''; S.cardHtml = [];
    if (!S.focusedEl || !document.body.contains(S.focusedEl)) restoreFocus(true);
    return;
  }
  const keys = list.map((g) => g.app_name).join('|');
  const htmls = list.map(cardHtml);
  if (grid.dataset.keys !== keys || S.cardHtml.length !== htmls.length) {
    const wasEmpty = !grid.querySelector('.card');
    patch(grid, htmls.join(''));
    grid.dataset.keys = keys;
    // first time the grid fills in, move automatic focus from the toolbar onto the first game
    if (wasEmpty && !S.firstGridDone && S.autoFocused && !topModal()) { setFocus(grid.querySelector('.card'), true); }
    S.firstGridDone = true;
  } else {
    htmls.forEach((h, i) => {
      if (S.cardHtml[i] === h) return;
      const old = grid.children[i];
      const tmp = document.createElement('div');
      tmp.innerHTML = h;
      const n = tmp.firstElementChild;
      const wasFocused = old === S.focusedEl;
      grid.replaceChild(n, old);
      if (wasFocused) setFocus(n, true);
    });
  }
  S.cardHtml = htmls;
  if (!S.focusedEl || !document.body.contains(S.focusedEl)) restoreFocus(true);
}

async function quickMenu(app) {
  const g = S.games.find((x) => x.app_name === app);
  if (!g) return;
  const opts = [];
  if (g.installed) opts.push({ value: 'play', label: 'Play' });
  else opts.push({ value: 'install', label: 'Install...' });
  if (g.update_available) opts.push({ value: 'update', label: 'Update' });
  opts.push({ value: 'details', label: 'Details' });
  opts.push({ value: 'fav', label: g.favorite ? 'Remove from favorites' : 'Add to favorites' });
  if (g.installed) opts.push({ value: g.in_steam ? 'steamRemove' : 'steamAdd', label: g.in_steam ? 'Remove from Steam library' : 'Add to Steam library' });
  opts.push({ value: 'hide', label: g.hidden ? 'Unhide game' : 'Hide game' });
  const v = await choose({ title: g.title, options: opts });
  if (!v) return;
  const d = { app };
  if (v === 'details') openDetail(app);
  else if (v === 'hide') toggleGame(app, 'hidden');
  else ACTIONS[v](d);
}
async function toggleGame(app, key) {
  const g = S.games.find((x) => x.app_name === app) || (S.detail && S.detail.app_name === app ? S.detail : null);
  const cur = g ? (key === 'favorite' ? g.favorite : g.hidden) : false;
  await safe(() => api(`game/${enc(app)}/settings`, { [key]: !cur }));
}

// ================================================================ detail
function detailParts(d) {
  const st = S.state;
  const running = st.running && st.running.app_name === d.app_name;
  const job = d.job;
  let actions = '';
  if (running) {
    actions += `<button class="btn big good focusable" data-action="noop" data-fid="d:primary" data-autofocus>${st.running.stage === 'running' ? 'Playing' : 'Starting...'}</button>
      <button class="btn big danger focusable" data-action="stop" data-fid="d:stop">Stop</button>`;
  } else if (job) {
    actions += `<div class="job-inline"><b>${job.status === 'queued' ? 'Queued' : esc(KIND_LABEL[job.kind] || job.kind)}</b>
      <div class="track"><i style="width:${Math.max(1, job.progress || 0)}%"></i></div>
      <span class="txt">${job.status === 'running' ? Math.floor(job.progress || 0) + '%' : ''} ${esc(job.message || '')}</span></div>
      <button class="btn focusable" data-action="cancelJob" data-id="${job.id}" data-fid="d:cancel" data-autofocus>Cancel</button>`;
  } else if (d.installed) {
    actions += `<button class="btn big primary focusable" data-action="play" data-app="${esc(d.app_name)}" data-fid="d:primary" data-autofocus>▶  Play</button>`;
    if (d.update_available) actions += `<button class="btn big focusable" data-action="update" data-app="${esc(d.app_name)}" data-fid="d:update">↑ Update</button>`;
  } else {
    actions += `<button class="btn big primary focusable" data-action="install" data-app="${esc(d.app_name)}" data-fid="d:primary" data-autofocus>Install</button>`;
  }
  if (d.installed) {
    actions += d.in_steam
      ? `<button class="btn focusable" data-action="steamRemove" data-app="${esc(d.app_name)}" data-fid="d:steam">✓ In Steam library${d.steam_pending ? ' (pending)' : ''}</button>`
      : `<button class="btn focusable" data-action="steamAdd" data-app="${esc(d.app_name)}" data-fid="d:steam">+ Add to Steam library</button>`;
  }
  actions += `<button class="btn focusable" data-action="fav" data-app="${esc(d.app_name)}" data-fid="d:fav">${d.favorite ? '★ Favorite' : '☆ Favorite'}</button>
    <button class="btn focusable" data-action="props" data-app="${esc(d.app_name)}" data-fid="d:props">Properties</button>
    <button class="btn focusable" data-action="more" data-app="${esc(d.app_name)}" data-fid="d:more">More...</button>`;

  // tiles
  const t = (k, v, s, attrs = '') => `<div class="tile ${attrs ? 'focusable' : ''}" ${attrs}><div class="k">${k}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ''}</div>`;
  const rv = d.reviews;
  let tiles = '';
  const matchAttrs = `data-action="m_fix" data-app="${esc(d.app_name)}" data-fid="d:tile-match"`;
  const unknown = '<span class="unknown">?</span>';
  if (!d.steam_appid && d.ratings_status === 'nomatch') {
    tiles += t('Rating', unknown, 'No Steam match - select to pick the right game', matchAttrs);
  } else if (rv && rv.total) {
    const sdb = d.rating && d.rating.sdb;
    tiles += t('Rating', `<span class="${sdbClass(sdb).replace('r-', 'rt-')}">${sdb}%</span> <span class="v-sub">${fmtCount(rv.total)} reviews</span>`,
      `${esc(rv.desc)} · ${rv.pct}% positive of ${rv.total.toLocaleString()} ${st.settings.reviews_language === 'all' ? '' : 'English '}Steam reviews${d.steam_name ? ` · ${esc(d.steam_name)}` : ''}`, matchAttrs);
  } else if (d.steam_appid && rv) {
    tiles += t('Rating', unknown, `No Steam reviews yet${d.steam_name ? ` · ${esc(d.steam_name)}` : ''}`, matchAttrs);
  } else {
    tiles += t('Rating', '<span class="spinner"></span>', 'Looking up on Steam...');
  }
  const pdb = d.protondb_full;
  if (pdb && hasPdb(pdb.tier)) tiles += t('ProtonDB', `<span class="pdb pdb-${esc(pdb.tier)}" style="font-size:1rem">${esc(pdb.tier)}</span>`,
    `${pdb.total || 0} reports${pdb.trending && pdb.trending !== pdb.tier ? ` · trending ${esc(pdb.trending)}` : ''}`);
  else if (d.ratings_status !== 'pending') tiles += t('ProtonDB', unknown, pdb && pdb.total ? `Only ${pdb.total} report${pdb.total === 1 ? '' : 's'} so far - playability unknown` : 'No reports yet - playability unknown');
  if (d.deck_full && d.deck_full.label) {
    const cls = { Verified: 'deck-v', Playable: 'deck-p', Unsupported: 'deck-u' }[d.deck_full.label] || '';
    tiles += t('Steam Deck', `<span class="${cls}">${esc(d.deck_full.label)}</span>`, 'Valve compatibility rating');
  }
  if (d.anticheat_full) {
    const a = d.anticheat_full;
    const cls = { Supported: 'deck-v', Running: 'deck-v', Planned: 'deck-p', Broken: 'deck-u', Denied: 'deck-u' }[a.status] || '';
    tiles += t('Anti-cheat', `<span class="${cls}">${esc(a.status)}</span>`, esc((a.anticheats || []).join(', ')));
  }
  if (d.metacritic) tiles += t('Metacritic', String(d.metacritic), '');
  tiles += t('Playtime', fmtDur(d.playtime), `Last played: ${fmtAgo(d.last_played)}`);
  tiles += t('Store', STORE_NAME[d.store] || d.store, d.store === 'gog'
    ? (d.installed ? (d.platform === 'linux' ? 'Native Linux version' : 'Windows version · DRM-free') : `Available for ${(d.platforms || []).map((x) => x === 'linux' ? 'Linux' : 'Windows').join(' and ')}`)
    : (d.installed ? 'Windows version' : ''));
  if (d.installed) tiles += t('On disk', fmtBytes(d.install_size), esc(d.install_path || ''));
  else if (d.sizes) tiles += t('Download', fmtBytes(d.sizes.download), `${fmtBytes(d.sizes.disk)} installed`);
  if (d.installed) tiles += t('Version', esc(d.version || '—'), d.update_available ? (d.latest_version ? `Update available: ${esc(d.latest_version)}` : 'Update available') : 'Up to date');
  if (d.installed && d.platform !== 'linux') tiles += t('Proton', esc(d.proton_name || 'Default'), d.settings.offline ? 'Offline mode' : '', `data-action="props" data-app="${esc(d.app_name)}" data-fid="d:tile-proton"`);
  if (d.last_launch) tiles += t('Last launch', d.last_launch.ok ? '<span class="deck-v">OK</span>' : '<span class="deck-u">Failed</span>',
    d.last_launch.ok ? fmtAgo(d.last_launch.time) : esc(d.last_launch.error || ''), `data-action="showLog" data-app="${esc(d.app_name)}" data-fid="d:tile-log"`);

  // warnings + rest
  const warns = [];
  if (d.third_party) warns.push(`<div class="banner warn">This game is managed by <b>${esc(d.third_party)}</b>. Epic only hands off to that launcher, which usually needs extra setup inside the Proton prefix.</div>`);
  if (d.anticheat_full && ['Denied', 'Broken'].includes(d.anticheat_full.status)) warns.push(`<div class="banner error">Its anti-cheat (${esc((d.anticheat_full.anticheats || []).join(', '))}) is <b>${esc(d.anticheat_full.status.toLowerCase())}</b> on Linux - online play will most likely not work.</div>`);
  if (d.cloud_saves && d.installed) warns.push(`<div class="banner info">${STORE_NAME[d.store]} cloud saves are supported and ${(d.settings.cloud_saves ?? st.settings.cloud_saves) ? 'will sync before and after you play.' : 'are turned off for this game.'}</div>`);

  let rest = '';
  if (d.description) rest += `<div class="section-title">About</div><div class="desc">${esc(d.description)}</div>`;
  if (d.dlcs && d.dlcs.length) rest += `<div class="section-title">DLC & add-ons (${d.dlcs.length})</div><div class="dlc-list">${d.dlcs.map((x) =>
    `<div class="dlc"><span>${esc(x.title)}</span><span class="${x.installed ? 'deck-v' : ''}" style="color:${x.installed ? '' : 'var(--muted)'}">${x.installed ? 'Installed' : 'Owned'}</span></div>`).join('')}</div>`;
  return { actions, tiles, warns: warns.join(''), rest };
}
ACTIONS.m_fix = (d) => fixMatch(d.app);

function updateDetail() {
  const d = S.detail;
  const root = $('#detail');
  if (!root || !d) return;
  const fresh = !S.parts.app || S.parts.app !== d.app_name;
  if (fresh) {
    const sub = [d.developer, d.release_date, (d.genres || []).slice(0, 3).join(', ')].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join('');
    const heroSrc = d.hero || d.hero_fallback || d.wide;
    root.innerHTML = `
      <div class="hero">${heroSrc ? `<img src="${esc(heroSrc)}" data-fb="${esc(d.hero_fallback || '')}" onerror="if(this.dataset.fb){this.src=this.dataset.fb;this.dataset.fb=''}else{this.remove()}">` : ''}</div>
      <div class="detail-body">
        <div id="d-title">${d.logo ? `<img class="d-logo" src="${esc(d.logo)}" data-fb="${esc(d.logo_fallback || '')}" alt="${esc(d.title)}"
          onerror="if(this.dataset.fb){this.src=this.dataset.fb;this.dataset.fb=''}else{this.outerHTML='<h1 class=&quot;d-title&quot;>'+this.alt+'</h1>'}">` : `<h1 class="d-title">${esc(d.title)}</h1>`}</div>
        <div class="d-sub">${sub}</div>
        <div class="actions" id="d-actions"></div>
        <div class="warnings" id="d-warns"></div>
        <div class="tiles" id="d-tiles"></div>
        <div id="d-rest"></div>
      </div>`;
    S.parts = { app: d.app_name };
  }
  const p = detailParts(d);
  for (const [k, id] of [['actions', 'd-actions'], ['warns', 'd-warns'], ['tiles', 'd-tiles'], ['rest', 'd-rest']]) {
    if (S.parts[k] !== p[k]) { patch($('#' + id), p[k]); S.parts[k] = p[k]; }
  }
  if (fresh || !S.focusedEl || !document.body.contains(S.focusedEl)) restoreFocus(true);
}

// ---------------------------------------------------------------- install dialog
async function openInstall(app) {
  const d = S.detail && S.detail.app_name === app ? S.detail : await safe(() => api('game/' + enc(app)));
  if (!d) return;
  const drives = (await safe(() => api('drives')) || { drives: [] }).drives;
  const st = S.state.settings;
  let sel = Math.max(0, drives.findIndex((x) => x.games_dir === st.install_dir));
  if (!drives.find((x) => x.games_dir === st.install_dir)) drives.unshift({ label: 'Default location', games_dir: st.install_dir, free: null, kind: 'custom' });
  // GOG games can come as a Windows build (run with Proton) and/or a native Linux build
  const plats = d.store === 'gog' ? (d.platforms || ['windows']) : ['windows'];
  let platform = plats.includes('linux') && (st.gog_prefer_linux || !plats.includes('windows')) ? 'linux' : 'windows';
  let sizes = d.store === 'gog' ? null : d.sizes;
  const loadSizes = () => {
    sizes = null;
    api(`game/${enc(app)}/sizes${d.store === 'gog' ? `?platform=${platform}` : ''}`)
      .then((s) => { sizes = s; if (modals.includes(m)) setModalBody(m, body()); })
      .catch(() => { sizes = { download: null, disk: null }; if (modals.includes(m)) setModalBody(m, body()); });
  };
  const body = () => {
    const need = sizes && sizes.disk ? sizes.disk : 0;
    const sz = sizes ? `<b>Download ${fmtBytes(sizes.download)}</b> · ${fmtBytes(sizes.disk)} on disk`
      : '<span class="spinner" style="display:inline-block;vertical-align:middle"></span> Checking size...';
    const warn = [];
    if (d.third_party) warn.push(`<div class="banner warn">Managed by ${esc(d.third_party)} - may need that launcher to run.</div>`);
    if (d.anticheat_full && ['Denied', 'Broken'].includes(d.anticheat_full.status)) warn.push(`<div class="banner error">Anti-cheat is ${esc(d.anticheat_full.status.toLowerCase())} on Linux - online modes likely won't work.</div>`);
    const verRows = plats.length > 1 ? `<div class="k" style="color:var(--muted);font-size:.8rem;margin-top:8px">VERSION</div>` +
      [['windows', 'Windows version', 'Runs with Proton - usually the most up to date (recommended)'], ['linux', 'Native Linux version', 'No Proton needed, but GOG Linux builds are sometimes older']]
        .map(([id, label, desc]) => `<div class="opt focusable ${platform === id ? 'selected' : ''}" data-action="m" data-k="plat" data-p="${id}" data-fid="plat-${id}">
          <div class="grow"><div>${label}</div><div class="d">${desc}</div></div>${platform === id ? '<span class="check">✓</span>' : ''}</div>`).join('') : '';
    return `${warn.join('')}<div style="margin:0 0 6px">${sz}</div>${verRows}<div class="k" style="color:var(--muted);font-size:.8rem;margin-top:8px">INSTALL TO</div>` +
      drives.map((x, i) => {
        const low = need && x.free != null && x.free < need * 1.05;
        return `<div class="opt focusable ${i === sel ? 'selected' : ''}" data-action="m" data-k="pick" data-i="${i}" data-fid="drv${i}">
          <div class="grow"><div>${esc(x.label)}</div><div class="d">${esc(x.games_dir)}${x.free != null ? ` · ${fmtBytes(x.free)} free` : ''}${low ? ' · <span style="color:var(--bad)">not enough space</span>' : ''}</div></div>
          ${i === sel ? '<span class="check">✓</span>' : ''}</div>`;
      }).join('');
  };
  const m = openModal({
    title: `Install ${d.title}`, body: body(),
    foot: `<button class="btn focusable" data-action="m" data-k="cancel" data-fid="cancel">Cancel</button>
      <button class="btn primary focusable" data-action="m" data-k="go" data-fid="go" data-autofocus>Install</button>`,
    handlers: {
      pick: (x) => { sel = +x.i; setModalBody(m, body()); },
      plat: (x) => { if (platform !== x.p) { platform = x.p; loadSizes(); setModalBody(m, body()); } },
      cancel: () => closeModal(),
      go: async () => {
        closeModal(true);
        const r = await safe(() => api('install', { app_name: app, base_path: drives[sel].games_dir, platform }));
        if (r) toast(`${d.title} added to downloads`, 'good');
      },
    },
  });
  if (!sizes) loadSizes();
}

// ---------------------------------------------------------------- properties
async function openGameSettings(app) {
  let d = S.detail && S.detail.app_name === app ? S.detail : await safe(() => api('game/' + enc(app)));
  if (!d) return;
  const tools = (await safe(() => api('proton')) || { tools: [] }).tools;
  let g = { ...d.settings };
  const st = S.state.settings;
  const toolName = (id) => { const t = tools.find((x) => x.id === id); return t ? t.name : (id ? id.split('/').pop() : ''); };
  const envStr = () => Object.entries(g.env || {}).map(([k, v]) => `${k}=${v}`).join(' ');
  const row = (k, title, desc, val, toggle) => `<div class="opt focusable" data-action="m" data-k="${k}" data-fid="p:${k}">
    <div class="grow"><div>${title}</div><div class="d">${desc}</div></div>
    ${toggle === undefined ? `<span style="color:var(--muted);max-width:40%;text-align:right;overflow:hidden;text-overflow:ellipsis">${esc(val)}</span>` : `<div class="toggle ${toggle ? 'on' : ''}"></div>`}</div>`;
  const native = d.platform === 'linux';
  const storeName = STORE_NAME[d.store] || 'the store';
  const body = () => [
    native ? '' : row('proton', 'Proton version', 'Compatibility tool used to run this game', g.proton ? toolName(g.proton) : `Default (${toolName(st.default_proton) || 'GE-Proton'})`),
    row('args', 'Launch arguments', 'Extra command-line arguments passed to the game', g.args || 'None'),
    row('env', 'Environment variables', native ? 'e.g. SDL_VIDEODRIVER=x11' : 'e.g. DXVK_ASYNC=1 PROTON_USE_WINED3D=1', envStr() || 'None'),
    native ? '' : row('cloud', 'Cloud saves', d.cloud_saves ? `Sync saves with ${storeName} before/after playing` : `This game has no ${storeName} cloud saves`, g.cloud_saves === null || g.cloud_saves === undefined ? `Default (${st.cloud_saves ? 'On' : 'Off'})` : g.cloud_saves ? 'On' : 'Off'),
    d.store === 'epic' ? row('offline', 'Offline mode', 'Launch without contacting Epic (only some games support it)', '', !!g.offline) : '',
    native ? '' : row('plog', 'Proton debug log', 'Writes a detailed Proton log next to the launch log', '', !!g.proton_log),
    row('match', 'Steam match', 'Which Steam game ratings/art come from', d.steam_name ? `${d.steam_name} (${d.steam_appid})` : d.steam_appid ? String(d.steam_appid) : 'Not matched'),
    row('log', 'View launch log', 'Output from the last launch', ''),
  ].join('');
  const save = async (patchObj) => {
    const r = await safe(() => api(`game/${enc(app)}/settings`, patchObj));
    if (r) { g = r; setModalBody(m, body()); }
  };
  const m = openModal({
    title: `${d.title} - Properties`, body: body(),
    handlers: {
      proton: async () => {
        const v = await choose({ title: 'Proton version', options: [{ value: '__default', label: `Use default (${toolName(st.default_proton)})`, selected: !g.proton },
          ...tools.map((t) => ({ value: t.id, label: t.name, desc: t.auto ? 'Downloaded automatically on first use' : t.source + ' · ' + t.id, selected: g.proton === t.id }))] });
        if (v) save({ proton: v === '__default' ? '' : v });
      },
      args: async () => { const v = await keyboard({ title: 'Launch arguments', value: g.args || '' }); if (v !== null) save({ args: v.trim() }); },
      env: async () => {
        const v = await keyboard({ title: 'Environment variables', value: envStr(), hint: 'Space-separated KEY=VALUE pairs' });
        if (v === null) return;
        const env = {};
        v.split(/\s+/).filter(Boolean).forEach((pair) => { const i = pair.indexOf('='); if (i > 0) env[pair.slice(0, i)] = pair.slice(i + 1); });
        save({ env });
      },
      cloud: async () => {
        const v = await choose({ title: 'Cloud saves', options: [{ value: 'd', label: 'Use default', selected: g.cloud_saves == null }, { value: 'on', label: 'On', selected: g.cloud_saves === true }, { value: 'off', label: 'Off', selected: g.cloud_saves === false }] });
        if (v) save({ cloud_saves: v === 'd' ? null : v === 'on' });
      },
      offline: () => save({ offline: !g.offline }),
      plog: () => save({ proton_log: !g.proton_log }),
      match: () => { closeModal(true); fixMatch(app); },
      log: () => showLog(app),
    },
  });
}

async function fixMatch(app) {
  const d = S.detail && S.detail.app_name === app ? S.detail : S.games.find((x) => x.app_name === app);
  const q = await keyboard({ title: 'Search Steam for the matching game', value: d ? d.title : '' });
  if (q === null) return;
  const r = await safe(() => api('steam/search?q=' + enc(q)));
  if (!r) return;
  const v = await choose({
    title: 'Pick the Steam game', msg: 'Ratings, ProtonDB tier and Steam artwork come from this match.',
    options: [...r.results.map((x) => ({ value: String(x.id), label: x.name, desc: `Steam app ${x.id}`, selected: d && d.steam_appid === x.id })),
      { value: 'auto', label: 'Automatic match', desc: 'Let PlayVault pick (GOG catalog or title search)' }],
  });
  if (!v) return;
  await safe(() => api(`game/${enc(app)}/match`, { steam_appid: v === 'auto' ? null : +v }), 'Match saved - refreshing ratings');
}

async function openMore(app) {
  const d = S.detail && S.detail.app_name === app ? S.detail : await safe(() => api('game/' + enc(app)));
  if (!d) return;
  const opts = [];
  if (d.installed) {
    opts.push({ value: 'repair', label: 'Verify & repair files', desc: 'Checks every file and re-downloads broken ones' });
    opts.push({ value: 'move', label: 'Move to another drive', desc: d.install_path });
  }
  opts.push({ value: 'match', label: 'Fix Steam match', desc: d.steam_name || 'Not matched' });
  opts.push({ value: 'ratings', label: 'Refresh Steam ratings' });
  opts.push({ value: 'log', label: 'View launch log' });
  opts.push({ value: 'hide', label: d.hidden ? 'Unhide game' : 'Hide game from library' });
  if (d.installed) opts.push({ value: 'uninstall', label: 'Uninstall', desc: 'Delete the game files' });
  const v = await choose({ title: d.title, options: opts });
  if (!v) return;
  if (v === 'repair') safe(() => api('repair', { app_name: app }), 'Repair queued');
  if (v === 'match') fixMatch(app);
  if (v === 'ratings') safe(() => api(`game/${enc(app)}/ratings`, {}), 'Refreshing ratings');
  if (v === 'log') showLog(app);
  if (v === 'hide') toggleGame(app, 'hidden');
  if (v === 'move') {
    const drives = (await safe(() => api('drives')) || { drives: [] }).drives.filter((x) => !(d.install_path || '').startsWith(x.games_dir));
    const dest = await choose({ title: 'Move to', options: drives.map((x) => ({ value: x.games_dir, label: x.label, desc: `${x.games_dir} · ${fmtBytes(x.free)} free` })) });
    if (dest) safe(() => api('move', { app_name: app, base_path: dest }), 'Move queued - see Downloads');
  }
  if (v === 'uninstall') {
    const how = await choose({
      title: `Uninstall ${d.title}?`, msg: 'Game files will be deleted. The Proton prefix holds local saves and settings.',
      options: [{ value: 'keep', label: 'Uninstall, keep Proton prefix', desc: 'Recommended - keeps local saves' },
        { value: 'all', label: 'Uninstall and delete Proton prefix', desc: 'Removes local saves that are not in the cloud' },
        { value: null, label: 'Cancel' }],
    });
    if (how) safe(() => api('uninstall', { app_name: app, delete_prefix: how === 'all' }), 'Uninstalling...');
  }
}

async function showLog(app) {
  const r = await safe(() => api(`game/${enc(app)}/log`));
  if (!r) return;
  const m = openModal({
    title: 'Launch log', body: `<div class="logbox">${esc(r.log)}</div>`,
    foot: `<button class="btn primary focusable" data-action="m" data-k="close" data-fid="close" data-autofocus>Close</button>`,
    handlers: { close: () => closeModal() },
  });
  const lb = m.el.querySelector('.logbox');
  lb.scrollTop = lb.scrollHeight;
}

async function launchGame(app) {
  if (S.state.running) { toast('A game is already running', 'error'); return; }
  const r = await safe(async () => {
    if (window.vault && window.vault.launch) {
      const res = await window.vault.launch(app);
      if (!res.ok) throw new Error(res.error);
      return res;
    }
    return api('launch', { app_name: app });
  });
  if (r) {
    const g = S.games.find((x) => x.app_name === app);
    const first = g && !g.last_played;
    toast(first ? 'Launching... the first launch sets up Proton and can take a few minutes' : 'Launching...', 'good');
  }
}

// ---------------------------------------------------------------- steam
async function steamAdd(apps) {
  const r = await safe(() => api('steam/add', { apps }));
  if (r) { toast(r.added.length ? `Added ${r.added.length} game(s) to Steam` : 'Nothing to add', 'good'); afterSteamChange(); }
}
async function afterSteamChange() {
  await tick();
  if (S.state.steam_pending) toast('Restart Steam (banner in Library) to see the change in your Steam library');
}
async function steamApply() {
  const ok = await confirmBox({
    title: 'Restart Steam now?', ok: 'Restart Steam',
    msg: 'Steam must restart to pick up the new library tiles. PlayVault will close with it - just reopen it afterwards.',
  });
  if (!ok) return;
  const info = window.vault && window.vault.info ? await window.vault.info() : {};
  const body = { game_mode: !!info.gameMode };
  try {
    const r = await api('steam/apply', body);
    toast(r.already ? 'Steam is already restarting - give it a moment' : r.restarting ? 'Restarting Steam...' : 'Steam library updated', 'good');
  } catch (e) {
    if (/Downloads are running/.test(e.message) && await confirmBox({ title: 'Downloads are running', msg: 'Restarting Steam closes PlayVault. Downloads continue in the background if that setting is on, otherwise they pause and resume next time.', ok: 'Restart anyway', danger: true })) {
      safe(() => api('steam/apply', { ...body, force: true }));
    } else toast(e.message, 'error');
  }
}

// ================================================================ downloads view
function downloadsHtml() {
  const jobs = S.jobs;
  const cur = jobs.find((j) => j.status === 'running');
  const queued = jobs.filter((j) => j.status === 'queued');
  const hist = jobs.filter((j) => !['running', 'queued'].includes(j.status)).reverse();
  let h = `<div class="page"><div class="toolbar"><h2 style="margin:0">Downloads</h2><div class="spacer"></div>
    <button class="btn small focusable" data-action="updateAll" data-fid="dl:updateall">Update all games</button>
    ${hist.length ? `<button class="btn small focusable" data-action="clearJobs" data-fid="dl:clear">Clear finished</button>` : ''}</div>`;
  if (cur) {
    const total = cur.download_size_mb;
    h += `<div class="dl-current"><div><div style="color:var(--muted);font-size:.85rem;font-weight:700;text-transform:uppercase;letter-spacing:.6px">${esc(KIND_LABEL[cur.kind] || cur.kind)}</div>
      <h2>${esc(cur.title)}</h2></div>
      <button class="btn danger focusable" data-action="cancelJob" data-id="${cur.id}" data-fid="dl:cancel" data-autofocus>Cancel</button>
      <div class="track"><i style="width:${cur.progress || 0}%"></i></div>
      <div class="dl-stats"><span><b>${(cur.progress || 0).toFixed(1)}%</b></span>
        ${cur.speed ? `<span>Speed <b>${cur.speed.toFixed(1)} MB/s</b></span>` : ''}
        ${cur.downloaded_mb ? `<span>Downloaded <b>${fmtBytes(cur.downloaded_mb * 1048576)}</b>${total ? ` of ${fmtBytes(total * 1048576)}` : ''}</span>` : ''}
        ${cur.eta != null ? `<span>Time left <b>${fmtEta(cur.eta)}</b></span>` : ''}
        <span>${esc(cur.message || '')}</span></div></div>`;
  } else {
    h += `<div class="banner"><div class="grow">Nothing downloading right now.</div></div>`;
  }
  if (queued.length) {
    h += `<div class="section-title">Up next</div><div class="list">${queued.map((j, i) => `<div class="row">
      <div class="grow"><div class="t">${esc(j.title)}</div><div class="d">${esc(KIND_LABEL[j.kind] || j.kind)} · waiting</div></div>
      ${i ? `<button class="btn small focusable" data-action="frontJob" data-id="${j.id}" data-fid="q:front:${j.id}">Move to top</button>` : ''}
      <button class="btn small focusable" data-action="cancelJob" data-id="${j.id}" data-fid="q:cancel:${j.id}">Remove</button></div>`).join('')}</div>`;
  }
  if (hist.length) {
    h += `<div class="section-title">Recent</div><div class="list">${hist.map((j) => `<div class="row focusable" data-action="open" data-app="${esc(j.app_name)}" data-fid="h:${j.id}">
      <div class="grow"><div class="t">${esc(j.title)}</div><div class="d">${esc(KIND_LABEL[j.kind] || j.kind)} · ${j.error ? esc(j.error) : esc(j.message || '')}</div></div>
      <span class="status-${j.status}">${esc(j.status)}</span></div>`).join('')}</div>`;
  }
  return h + '</div>';
}

// ================================================================ accounts view
function accountsHtml() {
  const acc = S.state.accounts || {};
  const lib = S.state.library || {};
  const stores = [
    { id: 'epic', name: 'Epic Games', user: acc.epic, login: 'login', logout: 'logout',
      blurb: 'Weekly free games, plus everything you have bought or claimed on the Epic Games Store.' },
    { id: 'gog', name: 'GOG', user: acc.gog, login: 'loginGog', logout: 'logoutGog',
      blurb: 'DRM-free games - once installed they keep working even if you sign out.' },
  ];
  const cards = stores.map((s) => {
    const owned = S.games.filter((g) => g.store === s.id);
    const inst = owned.filter((g) => g.installed).length;
    const stats = s.user ? `${owned.length} game${owned.length === 1 ? '' : 's'} in your library · ${inst} installed` : esc(s.blurb);
    const btn = s.user
      ? `<button class="btn danger focusable" data-action="${s.logout}" data-fid="acc:${s.id}:out">Sign out</button>`
      : `<button class="btn primary focusable" data-action="${s.login}" data-fid="acc:${s.id}:in" ${!acc.epic && !acc.gog && s.id === 'epic' ? 'data-autofocus' : ''}>Sign in</button>`;
    return `<div class="acct ${s.user ? 'on' : ''}">
      <div class="acct-head"><span class="store s-${s.id}">${STORE_NAME[s.id]}</span><h2>${s.name}</h2></div>
      <div class="acct-status">${s.user ? `<span class="dot good"></span>Signed in as <b>${esc(s.user)}</b>` : '<span class="dot"></span>Not signed in'}</div>
      <div class="acct-stats">${stats}</div>
      <div class="acct-actions">${btn}</div>
    </div>`;
  }).join('');
  return `<div class="page">
    <div class="toolbar"><h2 style="margin:0">Accounts</h2><div class="spacer"></div>
      ${acc.epic || acc.gog ? `<button class="btn small focusable" data-action="refreshLib" data-fid="acc:sync">${lib.refreshing ? 'Syncing...' : 'Sync libraries'}</button>` : ''}</div>
    <div class="acct-grid">${cards}</div>
    <div class="settings" style="margin-top:22px">
      <div class="setting focusable" data-action="loginMore" data-fid="acc:code"><div class="grow"><div class="t">Sign in with a code</div>
        <div class="d">If the sign-in window does not work for you, sign in on a phone or PC and enter the code here</div></div><span class="btn small">Use code</span></div>
    </div>
    <p class="d" style="color:var(--muted);margin-top:14px;font-size:.85rem">Your logins are stored only on this Steam Machine. Signing out of a store hides its games
      that are not installed; installed games stay on disk.</p>
  </div>`;
}

// ================================================================ settings view
const COUNTRIES = [['us', 'United States'], ['gb', 'United Kingdom'], ['ca', 'Canada'], ['au', 'Australia'], ['de', 'Germany'], ['fr', 'France'],
  ['es', 'Spain'], ['it', 'Italy'], ['nl', 'Netherlands'], ['se', 'Sweden'], ['br', 'Brazil'], ['mx', 'Mexico'], ['jp', 'Japan'], ['in', 'India']];
function settingsHtml() {
  const s = S.state.settings;
  const sys = S.sys || {};
  const acc = S.state.accounts || {};
  const tog = (key, title, desc) => `<div class="setting focusable" data-action="toggleSetting" data-key="${key}" data-fid="s:${key}">
    <div class="grow"><div class="t">${title}</div><div class="d">${desc}</div></div><div class="toggle ${s[key] ? 'on' : ''}"></div></div>`;
  const sel = (key, title, desc, val) => `<div class="setting focusable" data-action="setting" data-key="${key}" data-fid="s:${key}">
    <div class="grow"><div class="t">${title}</div><div class="d">${desc}</div></div><div class="val">${esc(val)}</div></div>`;
  const act = (action, title, desc, label, cls = '') => `<div class="setting focusable" data-action="${action}" data-fid="s:${action}">
    <div class="grow"><div class="t">${title}</div><div class="d">${desc}</div></div><span class="btn small ${cls}">${label}</span></div>`;
  const protonName = (() => { const t = (S.proton || []).find((x) => x.id === s.default_proton); return t ? t.name : s.default_proton; })();
  const drives = (S.drives || []).map((d) => {
    const used = d.total ? (100 * (d.total - d.free) / d.total) : 0;
    return `<div class="setting focusable" data-action="noop" data-fid="drv:${esc(d.mount)}"><div class="grow"><div class="t">${esc(d.label)}</div>
      <div class="d">${esc(d.mount)} · ${fmtBytes(d.free)} free of ${fmtBytes(d.total)}</div><div class="meter"><i style="width:${used.toFixed(1)}%"></i></div></div></div>`;
  }).join('');
  const installed = S.games.filter((g) => g.installed).sort((a, b) => b.install_size - a.install_size);
  const instHtml = installed.map((g) => `<div class="setting focusable" data-action="open" data-app="${esc(g.app_name)}" data-fid="inst:${esc(g.app_name)}">
    <div class="grow"><div class="t">${esc(g.title)}</div></div><div class="val">${fmtBytes(g.install_size)}</div></div>`).join('');
  return `<div class="page settings">
    <h3>Downloads & installs</h3>
    ${sel('install_dir', 'Default install location', 'Where new games are installed (internal storage, microSD or USB)', s.install_dir)}
    ${tog('install_dlc', 'Install owned DLC with games', 'Downloads all DLC you own together with the base game')}
    ${tog('background_downloads', 'Keep downloading after closing PlayVault', 'Downloads continue in the background when you exit to Game Mode')}
    ${tog('gog_prefer_linux', 'Prefer native Linux versions of GOG games', 'When a GOG game has both, pick the Linux build instead of Windows + Proton by default')}
    ${sel('max_workers', 'Download workers', 'Parallel download connections', s.max_workers ? String(s.max_workers) : 'Automatic')}
    ${act('updateAll', 'Update all games', 'Queue updates for every installed game that has one', 'Update all')}
    <h3>Playing</h3>
    ${sel('default_proton', 'Default Proton version', 'Used for games without their own Proton setting', protonName || 'GE-Proton')}
    ${tog('cloud_saves', 'Cloud saves', 'Sync Epic and GOG cloud saves before and after playing')}
    ${tog('hide_while_playing', 'Hide PlayVault while a game runs', 'Keeps the game in front; PlayVault comes back when the game exits')}
    ${tog('skip_update_check', 'Allow launching out-of-date games', 'Play even if an update is pending (online games may refuse)')}
    ${act('prepare', 'Prepare Proton now', 'Downloads the Steam runtime and Proton ahead of your first launch', 'Prepare')}
    <h3>Steam library</h3>
    ${tog('auto_add_steam', 'Add games to Steam after install', 'Creates a Steam library tile with artwork for each installed game')}
    ${tog('remove_steam_on_uninstall', 'Remove from Steam when uninstalled', 'Deletes the Steam tile when you uninstall')}
    ${act('steamAddAll', 'Add all installed games to Steam', 'Creates tiles for every installed game', 'Add all')}
    ${act('steamApply', 'Apply Steam changes', S.state.steam_pending ? `${S.state.steam_pending} change(s) waiting - restarts Steam` : 'Nothing waiting', 'Restart Steam')}
    ${act('addSelf', 'Re-add PlayVault to Steam', 'Recreates the PlayVault tile in your Steam library', 'Re-add')}
    <h3>Ratings & library</h3>
    ${tog('ratings_enabled', 'Show Steam ratings & ProtonDB', 'Looks up every game on Steam, ProtonDB and AreWeAntiCheatYet')}
    ${sel('reviews_language', 'Steam review score', 'Which reviews the % is based on - updates all ratings in the background when changed', s.reviews_language === 'all' ? 'All languages' : 'English (like Steam)')}
    ${sel('country', 'Steam store region', 'Region used for Steam store lookups', (COUNTRIES.find((c) => c[0] === s.country) || [0, s.country])[1])}
    ${act('ratingsAll', 'Refresh all ratings', 'Re-fetch ratings for every game now', 'Refresh')}
    ${act('refreshLib', 'Sync libraries', 'Re-download your owned games lists from Epic and GOG', 'Sync')}
    ${tog('show_hidden', 'Show "Hidden" filter', 'Lets you browse and unhide hidden games')}
    ${sel('card_size', 'Cover size', 'Size of game covers in the library', { small: 'Small', medium: 'Medium', large: 'Large' }[s.card_size] || 'Medium')}
    ${tog('delete_prefix_on_uninstall', 'Delete Proton prefix on uninstall (default)', 'Off keeps local saves when you uninstall')}
    <h3>Storage</h3>
    ${drives || '<div class="setting"><div class="grow d">Loading...</div></div>'}
    ${installed.length ? `<h3>Installed games (${installed.length}) · ${fmtBytes(installed.reduce((a, g) => a + g.install_size, 0))}</h3>${instHtml}` : ''}
    <h3>Accounts</h3>
    <div class="setting focusable" data-action="tab" data-tab="accounts" data-fid="s:accounts"><div class="grow"><div class="t">Epic & GOG accounts</div>
      <div class="d">${[acc.epic && `Epic: ${esc(acc.epic)}`, acc.gog && `GOG: ${esc(acc.gog)}`].filter(Boolean).join(' · ') || 'Not signed in'}</div></div>
      <span class="btn small">Manage</span></div>
    <h3>About</h3>
    <div class="setting focusable" data-action="noop" data-fid="s:about"><div class="grow"><div class="t">PlayVault ${esc(S.state.app_version)}</div>
      <div class="d">legendary ${esc(sys.legendary_version || '?')} · gogdl ${esc(sys.gogdl_version || 'missing')} · umu ${sys.umu_path ? 'installed' : 'missing'} · Python ${esc(sys.python || '?')} ·
      Steam user ${esc(sys.steam_user || 'not found')} · ${sys.game_mode ? 'Game Mode' : 'Desktop Mode'}<br>Data: ${esc(sys.data_dir || '')}</div></div></div>
    ${act('quit', 'Exit PlayVault', 'Downloads keep going in the background if enabled above', 'Exit')}
  </div>`;
}
async function saveSetting(key, value) {
  const s = await api('settings', { [key]: value });
  S.state.settings = s;
  renderMain(true);
  if (key === 'card_size') { document.body.classList.toggle('cards-small', value === 'small'); document.body.classList.toggle('cards-large', value === 'large'); }
}
async function editSetting(key) {
  const s = S.state.settings;
  let v = null;
  if (key === 'install_dir') {
    const drives = S.drives || (await api('drives')).drives;
    v = await choose({ title: 'Default install location', options: drives.map((d) => ({ value: d.games_dir, label: d.label, desc: `${d.games_dir} · ${fmtBytes(d.free)} free`, selected: d.games_dir === s.install_dir })) });
  } else if (key === 'default_proton') {
    const tools = S.proton || (await api('proton')).tools;
    v = await choose({ title: 'Default Proton version', options: tools.map((t) => ({ value: t.id, label: t.name, desc: t.auto ? 'Downloaded automatically on first use' : `${t.source} · ${t.id}`, selected: t.id === s.default_proton })) });
  } else if (key === 'max_workers') {
    v = await choose({ title: 'Download workers', options: [0, 4, 8, 12, 16].map((n) => ({ value: String(n), label: n ? `${n} workers` : 'Automatic', selected: +s.max_workers === n })) });
    if (v !== null) v = +v;
  } else if (key === 'reviews_language') {
    v = await choose({ title: 'Steam review score', options: [
      { value: 'english', label: 'English reviews', desc: 'Matches the score Steam shows you on its store page', selected: s.reviews_language !== 'all' },
      { value: 'all', label: 'All languages', desc: 'Counts every review worldwide (more reviews, can differ from Steam\'s page)', selected: s.reviews_language === 'all' }] });
  } else if (key === 'country') {
    v = await choose({ title: 'Steam store region', options: COUNTRIES.map(([c, n]) => ({ value: c, label: n, selected: c === s.country })) });
  } else if (key === 'card_size') {
    v = await choose({ title: 'Cover size', options: [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']].map(([c, n]) => ({ value: c, label: n, selected: c === s.card_size })) });
  }
  if (v !== null && v !== undefined) safe(() => saveSetting(key, v));
}

// ================================================================ boot
window.addEventListener('error', (e) => { console.error(e.error || e.message); document.title = 'ERR: ' + (e.message || ''); });
tick();
setInterval(tick, 1500);
