import json
import os
import unittest

from tests import HOME  # noqa: F401
from evault import amazon, paths, server

PID = 'amzn1.adg.product.11111111-2222-3333-4444-555555555555'


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)


class Amazon(unittest.TestCase):
    def tearDown(self):
        for p in (paths.AMAZON_USER, paths.AMAZON_LIBRARY, paths.AMAZON_INSTALLED, paths.AMAZON_UPDATES):
            try:
                os.remove(p)
            except OSError:
                pass

    def test_keys(self):
        self.assertEqual(amazon.key(PID), 'amazon_' + PID)
        self.assertTrue(amazon.is_amazon(amazon.key(PID)))
        self.assertFalse(amazon.is_amazon('gog_123'))
        self.assertEqual(amazon.pid(amazon.key(PID)), PID)

    def test_key_is_a_valid_app_name(self):
        self.assertEqual(server.app_name(amazon.key(PID)), amazon.key(PID))

    def test_extract_code(self):
        url = ('https://www.amazon.com/?openid.assoc_handle=amzn_sonic_games_launcher&openid.mode=id_res'
               '&openid.oa2.authorization_code=ANabc123xyz&openid.ns=x')
        self.assertEqual(amazon.extract_code(url), 'ANabc123xyz')
        self.assertEqual(amazon.extract_code('  "ANabc123xyz" '), 'ANabc123xyz')
        self.assertEqual(amazon.extract_code(''), '')

    def test_signed_out_by_default(self):
        self.assertIsNone(amazon.user())
        self.assertEqual(amazon.library_index(), [])
        self.assertEqual(amazon.installed(), {})

    def test_user(self):
        _write(paths.AMAZON_USER, {'name': 'Zach', 'user_id': 'amzn1.account.X'})
        self.assertEqual(amazon.user(), 'Zach')

    def test_library_and_installed(self):
        _write(paths.AMAZON_LIBRARY, [
            {'id': 'ent1', 'product': {'id': PID, 'title': 'Some Game', 'productDetail': {
                'iconUrl': 'https://x/icon.png',
                'details': {'developer': 'Dev', 'publisher': 'Pub', 'genres': ['Action'], 'releaseDate': '2020-05-01T00:00:00Z',
                            'backgroundUrl1': 'https://x/bg.jpg', 'logoUrl': 'https://x/logo.png'}}}},
            {'id': 'bad', 'product': {}},
        ])
        lib = amazon.library_index()
        self.assertEqual(len(lib), 1)
        self.assertEqual(lib[0]['title'], 'Some Game')
        self.assertEqual(lib[0]['developer'], 'Dev')
        self.assertEqual(lib[0]['release_date'], '2020-05-01')
        _write(paths.AMAZON_INSTALLED, [{'id': PID, 'version': 'v1', 'path': os.path.join(HOME, 'Games', 'Some Game'), 'size': 42}])
        inst = amazon.installed()[amazon.key(PID)]
        self.assertEqual(inst['title'], 'Some Game')
        self.assertEqual(inst['install_size'], 42)
        self.assertEqual(inst['install_path'], inst['path'])

    def test_updates_cache(self):
        _write(paths.AMAZON_UPDATES, {'fetched': 1, 'ids': [PID]})
        self.assertTrue(amazon.update_available({'id': PID}))
        amazon.clear_update(PID)
        self.assertFalse(amazon.update_available({'id': PID}))

    def test_set_install_path(self):
        _write(paths.AMAZON_INSTALLED, [{'id': PID, 'version': 'v1', 'path': 'old', 'size': 1}])
        amazon.set_install_path(PID, 'new')
        self.assertEqual(amazon.installed()[amazon.key(PID)]['path'], 'new')

    def test_login_code_needs_pending_login(self):
        ok, msg = amazon.login_with_code('ANabc')
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main()
