"""Sign in from a phone (or any device on the same network).

Typing a password with a controller is painful, so the Steam Machine can show a QR code instead.
The phone opens a small page served by PlayVault, follows a button to the store's normal sign-in
page, and pastes back the code the store hands out at the end - the same code the "sign in with a
code" screens take. The Steam Machine then finishes the sign-in itself.

Only this page is reachable from the network, never the main API (that stays on 127.0.0.1). It is
served on its own port only while a QR code is on screen, the address holds a one-time random key,
and it closes after a sign-in, after MAX_TRIES wrong codes, or after TTL seconds.
"""
import html
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import amazon, gog, legendary
from .util import log

PORT = 47822
TTL = 600
MAX_TRIES = 5
EPIC_LOGIN_URL = 'https://legendary.gl/epiclogin'
NAMES = {'epic': 'Epic Games', 'gog': 'GOG', 'amazon': 'Amazon Games'}
# what to copy once the store is done - shown on the phone
WHAT_TO_COPY = {
    'epic': 'You end up on a page of text that contains <b>"authorizationCode"</b>. Select all of it, copy it, '
            'and paste it below (just the 32-character code is fine too).',
    'gog': 'You end up on a blank page. Copy the <b>whole address</b> from the address bar '
           '(it contains <b>code=</b>) and paste it below.',
    'amazon': 'You end up on amazon.com (the page itself may look empty or show an error - that is fine). '
              'Copy the <b>whole address</b> from the address bar (it contains <b>authorization_code=</b>) and paste it below.',
}
EPIC_CODE = re.compile(r'"authorizationCode"\s*:\s*"([^"]+)"')
HEX32 = re.compile(r'\b[0-9a-fA-F]{32}\b')


def lan_ip():
    """This machine's address on the local network (None when offline). Connecting a UDP socket
    sends nothing; it only picks the interface the default route uses."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 80))
        ip = s.getsockname()[0]
        return None if ip.startswith('127.') or ip == '0.0.0.0' else ip
    except OSError:
        return None
    finally:
        s.close()


def clean_code(store, text):
    """Pull the code out of whatever was pasted (phones like to add spaces or extra text)."""
    text = (text or '').strip()
    if store == 'epic':
        m = EPIC_CODE.search(text)
        if m:
            return m.group(1)
        m = HEX32.search(text)
        return m.group(0) if m else text.strip('"')
    if store == 'gog' or store == 'amazon':
        m = re.search(r'https?://\S+', text)
        return m.group(0) if m else text
    return text


def _login(store, code):
    if store == 'epic':
        return legendary.login_with_code(code)
    if store == 'gog':
        return gog.login_with_code(code)
    return amazon.login_with_code(code)


class RemoteLogin:
    def __init__(self, on_login=None, port=PORT, bind='0.0.0.0', address=None):
        self.on_login = on_login or (lambda store: None)
        self.port = port
        self.bind = bind
        self.address = address  # host shown in the QR code (tests); normally lan_ip()
        self.lock = threading.Lock()
        self.sess = None
        self.srv = None

    # ------------------------------------------------------------ used by the UI (main API)
    def start(self, store):
        if store not in NAMES:
            raise ValueError(f'Unknown store: {store!r}')
        ip = self.address or lan_ip()
        if not ip:
            return {'error': 'This device is not connected to a network'}
        if store == 'gog':
            url = gog.LOGIN_URL
        elif store == 'amazon':
            if not amazon.ensure_installed():
                return {'error': 'nile (Amazon Games client) could not be installed - check your internet connection'}
            url = amazon.login_url()  # remembers the PKCE data the code will belong to
        else:
            url = EPIC_LOGIN_URL
        err = self._serve()
        if err:
            return {'error': err}
        token = secrets.token_urlsafe(18)
        with self.lock:
            self.sess = {'token': token, 'store': store, 'login_url': url, 'status': 'waiting',
                         'message': '', 'tries': 0, 'until': time.time() + TTL}
        log.info('Phone sign-in for %s started on port %d', store, self.port)
        return {'url': f'http://{ip}:{self.port}/s/{token}', 'expires_in': TTL}

    def status(self):
        with self.lock:
            s = self.sess
            if s and s['status'] in ('waiting', 'working') and time.time() > s['until']:
                s['status'], s['message'] = 'expired', 'The QR code expired - start again'
            if not s:
                return {'active': False}
            return {'active': True, 'store': s['store'], 'status': s['status'], 'message': s['message'],
                    'expires_in': max(0, int(s['until'] - time.time()))}

    def stop(self):
        with self.lock:
            self.sess = None
            srv, self.srv = self.srv, None
        if srv:
            self._shutdown(srv)

    # ------------------------------------------------------------ used by the phone page
    def session(self, token):
        """The live session the token belongs to, or None."""
        with self.lock:
            s = self.sess
            if not s or not token or not secrets.compare_digest(s['token'], token):
                return None
            if time.time() > s['until']:
                if s['status'] in ('waiting', 'working'):
                    s['status'], s['message'] = 'expired', 'The QR code expired - start again'
                return None
            return s

    def submit(self, token, text):
        s = self.session(token)
        if not s or s['status'] != 'waiting':
            if s and s['status'] == 'working':
                return False, 'Already signing in - wait a moment'
            if s and s['status'] == 'done':
                return True, s['message']
            return False, 'This sign-in link is no longer valid - start again on the Steam Machine'
        code = clean_code(s['store'], text)
        if not code:
            return False, 'Paste the code or address first'
        with self.lock:
            s['status'] = 'working'
        try:
            ok, msg = _login(s['store'], code)
        except Exception as e:  # a crash in a store client must not leave the session stuck on 'working'
            log.exception('Phone sign-in failed')
            ok, msg = False, str(e) or 'Sign-in failed'
        with self.lock:
            if ok:
                s['status'], s['message'] = 'done', msg
            else:
                s['tries'] += 1
                if s['tries'] >= MAX_TRIES:
                    s['status'], s['message'] = 'failed', 'Too many failed attempts - start again on the Steam Machine'
                    msg = s['message']
                else:
                    s['status'], s['message'] = 'waiting', msg
        log.info('Phone sign-in for %s: %s', s['store'], 'ok' if ok else 'failed')
        if ok:
            try:
                self.on_login(s['store'])
            except Exception:
                log.exception('on_login failed')
        return ok, msg

    # ------------------------------------------------------------ server
    def _serve(self):
        with self.lock:
            if self.srv:
                return None
            try:
                srv = _Server((self.bind, self.port), _handler(self))
            except OSError as e:
                log.warning('Phone sign-in: cannot listen on port %d: %r', self.port, e)
                return f'Could not open port {self.port} for the phone sign-in ({e.strerror or e})'
            srv.daemon_threads = True
            self.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Thread(target=self._reaper, args=(srv,), daemon=True).start()
        return None

    def _reaper(self, srv):
        """Closes the port once the session is over (signed in, expired, given up or cancelled)."""
        while True:
            time.sleep(2)
            with self.lock:
                if self.srv is not srv:
                    return
                s = self.sess
                over = not s or time.time() > s['until'] + 5 or s['status'] in ('done', 'failed')
                if over and s and s['status'] in ('done', 'failed'):
                    # leave the result page time to load its answer
                    over = time.time() > s.setdefault('ended', time.time()) + 30
                if over:
                    self.srv = None
            if over:
                self._shutdown(srv)
                return

    @staticmethod
    def _shutdown(srv):
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second socket take over a port that is still in use;
    # elsewhere it only skips TIME_WAIT after a restart.
    allow_reuse_address = os.name != 'nt'
    daemon_threads = True


def _handler(remote):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'PlayVault'

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else (
                body.encode('utf-8') if isinstance(body, str) else json.dumps(body).encode('utf-8'))
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')  # the key must not leak to the store
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "connect-src 'self'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _parts(self):
            path = urllib.parse.urlparse(self.path).path
            return [p for p in path.split('/') if p]

        def do_GET(self):
            p = self._parts()
            if len(p) == 2 and p[0] == 's':
                s = remote.session(p[1])
                if s:
                    return self._send(200, page(s), 'text/html; charset=utf-8')
                return self._send(410, GONE, 'text/html; charset=utf-8')
            self._send(404, {'error': 'not found'})

        def do_POST(self):
            p = self._parts()
            if len(p) == 3 and p[0] == 's' and p[2] == 'code':
                n = min(int(self.headers.get('Content-Length') or 0), 64 * 1024)
                try:
                    body = json.loads(self.rfile.read(n) or b'{}')
                except ValueError:
                    body = {}
                text = body.get('code', '') if isinstance(body, dict) else ''
                ok, msg = remote.submit(p[1], str(text))
                return self._send(200, {'ok': ok, 'message': msg})
            self._send(404, {'error': 'not found'})

    return Handler


GONE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PlayVault</title><body style="font-family:system-ui,sans-serif;background:#10131a;color:#e8ecf3;padding:24px">
<h2>This sign-in link has ended</h2><p>Start a new phone sign-in on the Steam Machine and scan the new QR code.</p></body>"""


def page(s):
    name = NAMES[s['store']]
    data = json.dumps({'token': s['token']}).replace('<', '\\u003c')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>PlayVault sign-in</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:#10131a; color:#e8ecf3; }}
  main {{ max-width:520px; margin:0 auto; padding:20px 16px 40px; }}
  h1 {{ font-size:1.35rem; margin:4px 0 2px; }}
  .sub {{ color:#9aa4b5; margin:0 0 18px; }}
  .step {{ background:#1a1f2b; border-radius:14px; padding:16px; margin:12px 0; }}
  .n {{ display:inline-block; width:26px; height:26px; line-height:26px; text-align:center; border-radius:50%;
       background:#4f8cff; color:#fff; font-weight:700; margin-right:8px; }}
  .step p {{ margin:10px 0 0; line-height:1.45; color:#c9d1de; }}
  a.btn, button {{ display:block; width:100%; box-sizing:border-box; margin-top:12px; padding:14px; border:0;
       border-radius:10px; background:#4f8cff; color:#fff; font-size:1.05rem; font-weight:600; text-align:center;
       text-decoration:none; cursor:pointer; }}
  button:disabled {{ opacity:.6; }}
  textarea {{ width:100%; box-sizing:border-box; margin-top:12px; min-height:110px; padding:12px; border-radius:10px;
       border:1px solid #333b4d; background:#0c0f15; color:#e8ecf3; font-size:16px; font-family:ui-monospace,monospace; }}
  #msg {{ margin-top:12px; padding:12px; border-radius:10px; display:none; line-height:1.4; }}
  #msg.err {{ display:block; background:#3a1820; color:#ffb4c0; }}
  #msg.ok {{ display:block; background:#163322; color:#a6f0c0; }}
  #msg.busy {{ display:block; background:#1d2638; color:#c9d1de; }}
</style></head>
<body><main>
  <h1>Sign in to {html.escape(name)}</h1>
  <p class="sub">for PlayVault on your Steam Machine</p>
  <div class="step"><span class="n">1</span><b>Sign in to {html.escape(name)}</b>
    <p>Opens in a new tab. Use your normal password, Google sign-in or two-factor code.{
      ' If the Amazon Shopping app opens instead, go back, press and hold the button and choose <b>Open in new tab</b>.'
      if s['store'] == 'amazon' else ''}</p>
    <a class="btn" href="{html.escape(s['login_url'])}" target="_blank" rel="noopener noreferrer">Open {html.escape(name)} sign-in</a>
  </div>
  <div class="step"><span class="n">2</span><b>Copy what it shows at the end</b>
    <p>{WHAT_TO_COPY[s['store']]}</p>
  </div>
  <div class="step"><span class="n">3</span><b>Come back to this tab and paste it</b>
    <textarea id="code" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
      placeholder="Paste here"></textarea>
    <button id="go">Sign in on the Steam Machine</button>
    <div id="msg"></div>
  </div>
</main>
<script>
  const D = {data};
  const go = document.getElementById('go'), box = document.getElementById('code'), msg = document.getElementById('msg');
  function show(text, cls) {{ msg.textContent = text; msg.className = cls; }}
  go.addEventListener('click', async () => {{
    const code = box.value.trim();
    if (!code) {{ show('Paste the code or address first.', 'err'); return; }}
    go.disabled = true;
    show('Signing in... this can take up to a minute.', 'busy');
    try {{
      const r = await fetch('/s/' + D.token + '/code', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ code }}) }});
      const j = await r.json();
      if (j.ok) {{
        show('Signed in as ' + j.message + '. You can put your phone away - the Steam Machine has it from here.', 'ok');
        go.style.display = 'none';
        return;
      }}
      show(j.message || 'Sign-in failed - try again.', 'err');
    }} catch (e) {{
      show('Could not reach the Steam Machine. Make sure your phone is on the same Wi-Fi and the QR code is still on screen.', 'err');
    }}
    go.disabled = false;
  }});
</script>
</body></html>"""
