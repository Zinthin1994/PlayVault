import unittest

from tests import HOME  # noqa: F401
from evault import jobs, paths, server


class AppNames(unittest.TestCase):
    def test_real_names_pass(self):
        for n in ('Fortnite', '9d2d0eb64d5c44529cece33fe2a46482', 'gog_1207658924', 'Sugar-Test.1'):
            self.assertEqual(server.app_name(n), n)

    def test_paths_are_refused(self):
        for n in ('', '..', '../../etc/passwd', 'a/b', 'a\\b', '.hidden', 'x..y', '-rf', 'a b', None, 'x' * 200):
            with self.assertRaises(ValueError, msg=repr(n)):
                server.app_name(n)

    def test_route_validates_game_urls(self):
        with self.assertRaises(ValueError):
            server.route('GET', ['game', '../../secret', 'log'], {}, {})

    def test_route_validates_body(self):
        with self.assertRaises(ValueError):
            server.route('POST', ['launch'], {}, {'app_name': '../x'})
        with self.assertRaises(ValueError):
            server.route('POST', ['steam', 'remove'], {}, {'apps': ['ok', 'a/b']})


class Http(unittest.TestCase):
    """The real request handler on a random port, with a stub App."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        server.APP = type('StubApp', (), {'last_request': 0, 'launch': lambda self, app: None})()
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, method='GET', body=None):
        import http.client
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        # the handler only accepts its configured Host header
        c.request(method, path, body=body, headers={'Host': f'127.0.0.1:{paths.PORT}',
                                                     'Content-Type': 'application/json'})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_encoded_traversal_in_log_url(self):
        status, body = self.get('/api/game/..%2F..%2F..%2Fsecret/log')
        self.assertEqual(status, 400, body)

    def test_missing_field_is_400_not_500(self):
        status, body = self.get('/api/launch', 'POST', b'{}')
        self.assertEqual(status, 400, body)
        self.assertIn(b'app_name', body)

    def test_non_object_body(self):
        status, _ = self.get('/api/launch', 'POST', b'[1, 2]')
        self.assertEqual(status, 400)


class _FakeHandler:
    _allowed = server.Handler._allowed

    def __init__(self, **headers):
        self.headers = headers


class Allowed(unittest.TestCase):
    host = f'127.0.0.1:{paths.PORT}'

    def ok(self, api=True, **h):
        return _FakeHandler(**h)._allowed(api)

    def test_own_ui_and_local_tools(self):
        self.assertTrue(self.ok(Host=self.host))  # curl / python
        self.assertTrue(self.ok(Host=self.host, Origin=f'http://{self.host}', **{'Sec-Fetch-Site': 'same-origin'}))
        self.assertTrue(self.ok(Host=f'localhost:{paths.PORT}'))

    def test_dns_rebinding_is_refused(self):
        self.assertFalse(self.ok(Host=f'evil.example:{paths.PORT}'))
        self.assertFalse(self.ok(api=False, Host=f'evil.example:{paths.PORT}'))

    def test_cross_site_pages_are_refused(self):
        self.assertFalse(self.ok(Host=self.host, Origin='https://evil.example'))
        self.assertFalse(self.ok(Host=self.host, **{'Sec-Fetch-Site': 'cross-site'}))
        self.assertFalse(self.ok(Host=self.host, **{'Sec-Fetch-Site': 'same-site'}))

    def test_static_files_ignore_origin(self):
        self.assertTrue(self.ok(api=False, Host=self.host, **{'Sec-Fetch-Site': 'cross-site'}))


class Progress(unittest.TestCase):
    def test_legendary_line(self):
        m = jobs.RE_PROGRESS.search('[DLManager] INFO: = Progress: 12.34% (5/40), Running for 00:00:10, ETA: 01:02:03')
        self.assertEqual(m.group(1), '12.34')
        self.assertEqual(m.group(4, 5, 6), ('01', '02', '03'))

    def test_gogdl_line(self):
        m = jobs.RE_PROGRESS.search('[PROGRESS] INFO: = Progress: 55.00 512/4096, Running for: 00:00:10, ETA: 00:00:42')
        self.assertEqual(m.group(1), '55.00')
        self.assertEqual(m.group(2, 3), ('512', '4096'))

    def test_sizes(self):
        self.assertEqual(jobs.RE_DLSIZE.search('Download size: 1234.5 MiB').group(1), '1234.5')
        self.assertEqual(jobs.RE_SPEED.search(' + Download\t- 41.20 MiB/s (raw)').group(1), '41.20')


class StoreDir(unittest.TestCase):
    def test_store_dir(self):
        self.assertEqual(paths.store_dir('/home/deck/Games', 'gog').replace('\\', '/'), '/home/deck/Games/GOG')
        self.assertEqual(paths.store_dir('/home/deck/Games/Epic/', 'epic'), '/home/deck/Games/Epic')


if __name__ == '__main__':
    unittest.main()
