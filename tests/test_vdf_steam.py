import unittest

from tests import HOME  # noqa: F401  (sets PLAYVAULT_HOME first)
from evault import steam, vdf


class BinaryVdf(unittest.TestCase):
    def test_round_trip_keeps_types(self):
        data = {'shortcuts': {'0': {
            'appid': 0x80000000 | 1234,          # high bit set, like every non-Steam shortcut id
            'AppName': 'Hades',
            'LastPlayTime': 0,
            'big': vdf.U64(2 ** 40),
            'f': vdf.F32(1.5),
            'tags': {'0': 'PlayVault'},
        }}}
        back = vdf.parse_binary(vdf.dump_binary(data))
        e = back['shortcuts']['0']
        # parse_binary reads int32 as signed; callers mask with 0xFFFFFFFF
        self.assertEqual(e['appid'] & 0xFFFFFFFF, 0x80000000 | 1234)
        self.assertEqual(e['AppName'], 'Hades')
        self.assertIsInstance(e['big'], vdf.U64)
        self.assertEqual(e['big'], 2 ** 40)
        self.assertIsInstance(e['f'], vdf.F32)
        self.assertEqual(e['tags'], {'0': 'PlayVault'})

    def test_file_ends_like_steam_writes_it(self):
        self.assertTrue(vdf.dump_binary({'shortcuts': {}}).endswith(b'\x08\x08'))

    def test_unknown_type_is_an_error(self):
        with self.assertRaises(ValueError):
            vdf.parse_binary(b'\x09key\x00')


class TextVdf(unittest.TestCase):
    def test_nested_escapes_and_comments(self):
        text = '''"users"
        {
            // a comment
            "76561197960265729"
            {
                "AccountName"  "a \\"quoted\\" name"
                "MostRecent"   "1"
            }
        }'''
        users = vdf.parse_text(text)['users']
        self.assertEqual(users['76561197960265729']['AccountName'], 'a "quoted" name')
        self.assertEqual(users['76561197960265729']['MostRecent'], '1')


class Shortcuts(unittest.TestCase):
    def test_appid_has_high_bit(self):
        a = steam.shortcut_appid('"/x/playvault-launch"', 'Hades')
        self.assertTrue(a & 0x80000000)
        self.assertEqual(a, steam.shortcut_appid('"/x/playvault-launch"', 'Hades'))

    def test_entry_is_recognised_as_ours(self):
        appid, e = steam.make_entry('gog_123', 'Hades', '/x/playvault-launch', '/x')
        self.assertEqual(e['appid'], appid)
        self.assertEqual(e['Exe'], '"/x/playvault-launch"')
        self.assertTrue(steam._is_ours(e))
        self.assertTrue(steam._is_ours(e, 'gog_123'))
        self.assertFalse(steam._is_ours(e, 'gog_124'))
        # and still after a trip through the binary format (keys keep their case)
        back = vdf.parse_binary(vdf.dump_binary({'shortcuts': {'0': e}}))['shortcuts']['0']
        self.assertTrue(steam._is_ours(back, 'gog_123'))

    def test_legacy_tiles_count_as_ours(self):
        _, e = steam.make_entry('Fortnite', 'Fortnite', '/x', '/x')
        e['tags'] = {'0': 'EpicVault'}
        self.assertTrue(steam._is_ours(e))

    def test_art_sources_prefer_steam(self):
        src = steam.art_sources({'p': ['https://epic/tall.jpg']}, 620)
        self.assertTrue(src['p'][0].endswith('/620/library_600x900_2x.jpg'))
        self.assertEqual(src['p'][-1], 'https://epic/tall.jpg')
        self.assertEqual(steam.art_sources({'p': ['x']}, None)['p'], ['x'])


if __name__ == '__main__':
    unittest.main()
