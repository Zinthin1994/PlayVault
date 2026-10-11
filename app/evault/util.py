import json
import logging
import os
import threading
import urllib.error
import urllib.request

log = logging.getLogger('playvault')

USER_AGENT = 'PlayVault/1.2 (personal SteamOS launcher)'


def read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log.warning('Could not read %s: %r', path, e)
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def http_get(url, timeout=20, headers=None):
    """Returns (status, bytes). status 0 means a network error."""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b''
    except Exception as e:
        log.debug('GET %s failed: %r', url, e)
        return 0, b''


def http_json(url, timeout=20):
    status, body = http_get(url, timeout)
    if status != 200 or not body:
        return status, None
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def download(url, dest, timeout=30):
    status, body = http_get(url, timeout)
    if status == 200 and len(body) > 1000:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + '.part'
        with open(tmp, 'wb') as f:
            f.write(body)
        os.replace(tmp, dest)
        return True
    return False


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def tail(text, n=600):
    text = (text or '').strip()
    return text[-n:]
