// Preload for the Epic / Google sign-in windows.
// Adds a Cancel button and a built-in on-screen keyboard that pops up when a text field is
// focused (the KDE / Steam keyboards don't reliably appear for this window). Keys are sent to
// the main process, which types them as real (trusted) input so React/Google forms accept them.
// Controller: keyboard open -> D-pad/stick move, A press, B close, X backspace, Y space,
//             LB shift, RB symbols, Start enter.
//             keyboard closed -> D-pad next/previous field, A select, Y keyboard, B cancel.
const { ipcRenderer } = require('electron');

const send = (msg) => ipcRenderer.send('osk', msg);

const LETTERS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', '@'],
  ['{shift}', 'z', 'x', 'c', 'v', 'b', 'n', 'm', '.', '{bksp}'],
  ['{sym}', '.com', '{space}', '{tab}', '{enter}', '{hide}'],
];
const SYMBOLS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['!', '#', '$', '%', '&', '*', '+', '=', '/', '?'],
  ['-', '_', '~', '^', '(', ')', '[', ']', '{', '}'],
  ['{shift}', '\'', '"', ':', ';', ',', '<', '>', '|', '{bksp}'],
  ['{sym}', '\\', '{space}', '{tab}', '{enter}', '{hide}'],
];
const LABELS = {
  '{shift}': '⇧ Shift', '{bksp}': '⌫', '{sym}': '?123', '{space}': 'Space',
  '{tab}': 'Next ⇥', '{enter}': 'Enter ↵', '{hide}': 'Hide ▾',
};
const WIDE = { '{space}': 4, '{shift}': 1.5, '{bksp}': 1.5, '{sym}': 1.3, '{enter}': 1.6, '{tab}': 1.4, '{hide}': 1.3, '.com': 1.2 };

const CSS = `
:host { all: initial; }
.bar { position: fixed; top: 12px; right: 12px; z-index: 2147483647; display: flex; gap: 10px; }
.btn { padding: 12px 18px; font: 600 16px sans-serif; background: #1b2130; color: #fff;
       border: 2px solid #4f8cff; border-radius: 10px; cursor: pointer; }
.kb { position: fixed; left: 0; right: 0; bottom: 0; z-index: 2147483647; background: #0f131b;
      border-top: 2px solid #2b3446; padding: 12px 12px 16px; box-shadow: 0 -8px 30px rgba(0,0,0,.6);
      font: 600 22px sans-serif; color: #fff; user-select: none; display: none; }
.kb.open { display: block; }
.row { display: flex; gap: 8px; justify-content: center; margin-top: 8px; }
.key { flex: 1 1 0; max-width: 110px; height: 58px; display: flex; align-items: center; justify-content: center;
       background: #222a39; border: 2px solid transparent; border-radius: 10px; cursor: pointer; }
.key:hover { background: #2d3850; }
.key.on { background: #4f8cff; }
.key.sel { border-color: #fff; box-shadow: 0 0 0 3px #4f8cff; }
.key:active { background: #4f8cff; }
.hint { text-align: center; font: 14px sans-serif; color: #8b95a7; margin-top: 10px; }
.tip { position: fixed; bottom: 12px; left: 50%; transform: translateX(-50%); z-index: 2147483646;
       padding: 8px 14px; font: 14px sans-serif; background: rgba(0,0,0,.75); color: #fff; border-radius: 8px; }
`;

function isTextField(el) {
  if (!el) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === 'TEXTAREA') return true;
  if (el.tagName !== 'INPUT') return false;
  const t = (el.getAttribute('type') || 'text').toLowerCase();
  return ['text', 'email', 'password', 'search', 'tel', 'url', 'number'].includes(t);
}

function build() {
  if (document.getElementById('__ev_osk_host')) return;
  const host = document.createElement('div');
  host.id = '__ev_osk_host';
  const root = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = CSS;
  root.appendChild(style);

  const bar = document.createElement('div');
  bar.className = 'bar';
  const kbBtn = document.createElement('button');
  kbBtn.className = 'btn';
  kbBtn.textContent = '⌨  Keyboard (Y)';
  const cancel = document.createElement('button');
  cancel.className = 'btn';
  cancel.textContent = '✕  Cancel sign-in (B)';
  bar.append(kbBtn, cancel);
  root.appendChild(bar);

  const tip = document.createElement('div');
  tip.className = 'tip';
  tip.textContent = 'Select a text box to type - the keyboard pops up automatically';
  root.appendChild(tip);

  const kb = document.createElement('div');
  kb.className = 'kb';
  root.appendChild(kb);

  let shift = false;
  let sym = false;
  let sel = [1, 0];
  let keyEls = [];

  const layout = () => (sym ? SYMBOLS : LETTERS);
  const labelOf = (k) => LABELS[k] || (shift && k.length === 1 ? k.toUpperCase() : k);

  function render() {
    kb.textContent = '';
    keyEls = [];
    layout().forEach((row, r) => {
      const rowEl = document.createElement('div');
      rowEl.className = 'row';
      const els = [];
      row.forEach((k, c) => {
        const el = document.createElement('div');
        el.className = 'key' + (WIDE[k] ? ' wide' : '');
        if (WIDE[k]) {
          el.style.flexGrow = String(WIDE[k]);
          el.style.maxWidth = `${Math.round(118 * WIDE[k])}px`;
        }
        if ((k === '{shift}' && shift) || (k === '{sym}' && sym)) el.classList.add('on');
        if (r === sel[0] && c === sel[1]) el.classList.add('sel');
        el.textContent = labelOf(k);
        // mousedown + preventDefault keeps focus (and the caret) in the page's text field
        el.addEventListener('mousedown', (e) => { e.preventDefault(); sel = [r, c]; press(k); });
        rowEl.appendChild(el);
        els.push(el);
      });
      keyEls.push(els);
      kb.appendChild(rowEl);
    });
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = 'Controller: A press · B close · X backspace · Y space · LB shift · RB symbols · Start enter';
    kb.appendChild(hint);
  }

  function press(k) {
    switch (k) {
      case '{shift}': shift = !shift; break;
      case '{sym}': sym = !sym; sel = [Math.min(sel[0], layout().length - 1), 0]; break;
      case '{bksp}': send({ key: 'Backspace' }); break;
      case '{space}': send({ text: ' ' }); break;
      case '{tab}': send({ key: 'Tab' }); break;
      case '{enter}': send({ key: 'Enter' }); hide(); break;
      case '{hide}': hide(); break;
      default:
        send({ text: shift && k.length === 1 ? k.toUpperCase() : k });
        if (shift) shift = false;
    }
    render();
  }

  function show() { kb.classList.add('open'); tip.style.display = 'none'; render(); }
  function hide() { kb.classList.remove('open'); tip.style.display = ''; }
  const isOpen = () => kb.classList.contains('open');

  kbBtn.addEventListener('mousedown', (e) => { e.preventDefault(); isOpen() ? hide() : show(); });
  cancel.addEventListener('click', () => send({ cancel: true }));

  document.addEventListener('focusin', (e) => {
    const t = e.composedPath ? e.composedPath()[0] : e.target;
    if (isTextField(t)) show();
  }, true);

  const reattach = () => { if (!host.isConnected) document.documentElement.appendChild(host); };
  document.documentElement.appendChild(host);
  setInterval(reattach, 1000);
  // pages like Google autofocus their first field before this script runs
  if (isTextField(document.activeElement)) show();

  // ---- controller
  function moveSel(dr, dc) {
    const rows = layout();
    let r = Math.max(0, Math.min(rows.length - 1, sel[0] + dr));
    let c = sel[1];
    if (dr !== 0) {
      // keep roughly the same horizontal position when changing rows of different lengths
      const frac = (sel[1] + 0.5) / rows[sel[0]].length;
      c = Math.floor(frac * rows[r].length);
    }
    c = (c + dc + rows[r].length) % rows[r].length;
    sel = [r, c];
    render();
  }

  const prev = {};
  const held = {};
  function edge(id, on, repeat) {
    const now = performance.now();
    if (!on) { prev[id] = false; return false; }
    if (!prev[id]) { prev[id] = true; held[id] = now + 400; return true; }
    if (repeat && now >= held[id]) { held[id] = now + 90; return true; }
    return false;
  }

  function poll() {
    const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
    const b = (i) => pads.some((p) => p.buttons[i] && p.buttons[i].pressed);
    const ax = (i) => pads.reduce((v, p) => (Math.abs(p.axes[i] || 0) > Math.abs(v) ? p.axes[i] : v), 0);
    const up = b(12) || ax(1) < -0.6;
    const down = b(13) || ax(1) > 0.6;
    const left = b(14) || ax(0) < -0.6;
    const right = b(15) || ax(0) > 0.6;
    if (pads.length && document.hasFocus()) {
      if (isOpen()) {
        if (edge('u', up, true)) moveSel(-1, 0);
        if (edge('d', down, true)) moveSel(1, 0);
        if (edge('l', left, true)) moveSel(0, -1);
        if (edge('r', right, true)) moveSel(0, 1);
        if (edge('a', b(0))) press(layout()[sel[0]][sel[1]]);
        if (edge('b', b(1))) hide();
        if (edge('x', b(2), true)) send({ key: 'Backspace' });
        if (edge('y', b(3))) send({ text: ' ' });
        if (edge('lb', b(4))) press('{shift}');
        if (edge('rb', b(5))) press('{sym}');
        if (edge('st', b(9))) press('{enter}');
      } else {
        if (edge('u', up || left, true)) send({ key: 'Tab', shift: true });
        if (edge('d', down || right, true)) send({ key: 'Tab' });
        if (edge('a', b(0))) {
          if (isTextField(document.activeElement)) show(); else send({ key: 'Enter' });
        }
        if (edge('y', b(3))) show();
        if (edge('b', b(1))) send({ cancel: true });
        edge('x', b(2)); edge('lb', b(4)); edge('rb', b(5)); edge('st', b(9));
      }
    }
    requestAnimationFrame(poll);
  }
  requestAnimationFrame(poll);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', build, { once: true });
} else {
  build();
}
