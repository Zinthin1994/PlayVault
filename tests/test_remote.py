import json
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from tests import HOME  # noqa: F401
from evault import remote

PORT = 47899


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


class CleanCode(unittest.TestCase):
    def test_epic(self):
        code = 'a' * 32
        page = '{"warning":"x","redirectUrl":"https://...","authorizationCode":"%s","sid":null}' % code
        self.assertEqual(remote.clean_code('epic', page), code)
        self.assertEqual(remote.clean_code('epic', f'  "{code}"\n'), code)
        self.assertEqual(remote.clean_code('epic', f'copied: {code.upper()} ok'), code.upper())

    def test_url_stores(self):
        url = 'https://embed.gog.com/on_login_success?origin=client&code=XYZ'
        self.assertEqual(remote.clean_code('gog', f'Here: {url}\n'), url)
        self.assertEqual(remote.clean_code('gog', 'XYZ'), 'XYZ')
        a = 'https://www.amazon.com/?openid.oa2.authorization_code=ANabc&openid.ns=x'
        self.assertEqual(remote.clean_code('amazon', a), a)


class PhoneLogin(unittest.TestCase):
    def setUp(self):
        self.logins = []
        self.r = remote.RemoteLogin(on_login=self.logins.append, port=PORT, bind='127.0.0.1', address='127.0.0.1')

    def tearDown(self):
        self.r.stop()
        time.sleep(0.2)

    def test_full_sign_in(self):
        res = self.r.start('gog')
        self.assertTrue(res['url'].startswith(f'http://127.0.0.1:{PORT}/s/'))
        token = res['url'].rsplit('/', 1)[1]
        status, body, headers = _get(res['url'])
        self.assertEqual(status, 200)
        self.assertIn('Sign in to GOG', body)
        self.assertIn('auth.gog.com', body)
        self.assertEqual(headers.get('Referrer-Policy'), 'no-referrer')
        self.assertEqual(self.r.status()['status'], 'waiting')

        with mock.patch.object(remote.gog, 'login_with_code', return_value=(False, 'bad code')) as bad:
            self.assertEqual(_post(f'http://127.0.0.1:{PORT}/s/{token}/code', {'code': 'nope'}),
                             {'ok': False, 'message': 'bad code'})
            bad.assert_called_once_with('nope')
        self.assertEqual(self.r.status()['status'], 'waiting')
        self.assertEqual(self.logins, [])

        url = 'https://embed.gog.com/on_login_success?origin=client&code=GOOD'
        with mock.patch.object(remote.gog, 'login_with_code', return_value=(True, 'zin')) as good:
            self.assertEqual(_post(f'http://127.0.0.1:{PORT}/s/{token}/code', {'code': ' ' + url + '\n'}),
                             {'ok': True, 'message': 'zin'})
            good.assert_called_once_with(url)
        self.assertEqual(self.r.status(), {'active': True, 'store': 'gog', 'status': 'done', 'message': 'zin',
                                           'expires_in': self.r.status()['expires_in']})
        self.assertEqual(self.logins, ['gog'])

    def test_wrong_token_and_paths_refused(self):
        res = self.r.start('epic')
        base = res['url'].rsplit('/', 1)[0]
        self.assertEqual(_get(base + '/wrongtoken')[0], 410)
        self.assertEqual(_get(f'http://127.0.0.1:{PORT}/api/state')[0], 404)
        self.assertEqual(_get(f'http://127.0.0.1:{PORT}/')[0], 404)
        with mock.patch.object(remote.legendary, 'login_with_code') as lw:
            r = _post(base + '/wrongtoken/code', {'code': 'a' * 32})
            self.assertFalse(r['ok'])
            lw.assert_not_called()

    def test_gives_up_after_max_tries(self):
        token = self.r.start('epic')['url'].rsplit('/', 1)[1]
        with mock.patch.object(remote.legendary, 'login_with_code', return_value=(False, 'no')) as lw:
            for _ in range(remote.MAX_TRIES):
                self.r.submit(token, 'b' * 32)
            ok, msg = self.r.submit(token, 'b' * 32)
            self.assertFalse(ok)
            self.assertEqual(lw.call_count, remote.MAX_TRIES)
        self.assertEqual(self.r.status()['status'], 'failed')

    def test_expiry(self):
        token = self.r.start('epic')['url'].rsplit('/', 1)[1]
        self.r.sess['until'] = time.time() - 1
        self.assertIsNone(self.r.session(token))
        self.assertEqual(self.r.status()['status'], 'expired')

    def test_stop_closes_the_port(self):
        url = self.r.start('epic')['url']
        self.assertEqual(_get(url)[0], 200)
        self.r.stop()
        time.sleep(0.5)
        self.assertEqual(self.r.status(), {'active': False})
        with self.assertRaises(OSError):
            urllib.request.urlopen(url, timeout=2)

    def test_unknown_store(self):
        with self.assertRaises(ValueError):
            self.r.start('steam')


if __name__ == '__main__':
    unittest.main()
