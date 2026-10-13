import unittest
from unittest import mock

from tests import HOME  # noqa: F401
from evault import ratings


class Titles(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(ratings.norm("Assassin’s Creed® Odyssey"), 'assassins creed odyssey')
        self.assertEqual(ratings.norm('Ori & the Blind Forest'), 'ori and the blind forest')

    def test_strip_edition(self):
        self.assertEqual(ratings.strip_edition('Control Ultimate Edition'), 'Control')
        self.assertEqual(ratings.strip_edition('The Witcher 3: Wild Hunt - Game of the Year Edition'),
                         'The Witcher 3: Wild Hunt')
        self.assertEqual(ratings.strip_edition('Gold'), 'Gold')  # never strips a title down to nothing

    def test_similarity(self):
        self.assertEqual(ratings.similarity('Hades', 'HADES'), 1.0)
        self.assertGreaterEqual(ratings.similarity('Spider Man', 'SpiderMan'), 0.98)
        self.assertLess(ratings.similarity('Hades', 'Hades II'), 0.84 + 0.1)

    def test_steamdb_rating(self):
        self.assertIsNone(ratings.steamdb_rating(0, 0))
        few = ratings.steamdb_rating(2, 2)
        many = ratings.steamdb_rating(9500, 10000)
        self.assertLess(few, many)
        self.assertTrue(50 < few < 100)


def _reply(status, body):
    return lambda url, timeout=20: (status, body)


class Offline(unittest.TestCase):
    """Network failures must not be cached as 'no Steam match' (that used to stick for 3 days)."""

    def test_search_failure_raises(self):
        with mock.patch.object(ratings, 'http_json', _reply(0, None)), \
             mock.patch.object(ratings.time, 'sleep'):
            with self.assertRaises(ratings.SteamUnreachable):
                ratings.best_match('Hades')

    def test_rate_limit_raises(self):
        with mock.patch.object(ratings, 'http_json', _reply(429, None)), \
             mock.patch.object(ratings.time, 'sleep'):
            with self.assertRaises(ratings.SteamUnreachable):
                ratings.best_match('Hades')

    def test_empty_results_are_a_real_no_match(self):
        with mock.patch.object(ratings, 'http_json', _reply(200, {'items': []})), \
             mock.patch.object(ratings.time, 'sleep'):
            self.assertEqual(ratings.best_match('Some Obscure Game'), (None, 0.0))

    def test_ui_search_still_returns_a_list(self):
        with mock.patch.object(ratings, 'http_json', _reply(0, None)):
            self.assertEqual(ratings.steam_search('Hades'), [])

    def test_good_match(self):
        body = {'items': [{'id': 1145360, 'name': 'Hades', 'type': 'app'},
                          {'id': 1145350, 'name': 'Hades II', 'type': 'app'}]}
        with mock.patch.object(ratings, 'http_json', _reply(200, body)), \
             mock.patch.object(ratings.time, 'sleep'):
            match, score = ratings.best_match('Hades')
        self.assertEqual(match['id'], 1145360)
        self.assertEqual(score, 1.0)

    def test_manual_match_with_no_data_is_not_cached(self):
        r = ratings.Ratings.__new__(ratings.Ratings)  # no worker thread
        r.lang, r.country, r.awacy_index = 'english', 'us', {'ns': {}, 'steam': {}, 'name': {}}
        item = {'app': 'gog_1', 'title': 'Hades', 'ns': None, 'appid': 1145360, 'hint': None}
        with mock.patch.object(ratings, 'http_json', _reply(0, None)), \
             mock.patch.object(ratings.time, 'sleep'):
            with self.assertRaises(ratings.SteamUnreachable):
                r._process(item)


if __name__ == '__main__':
    unittest.main()
