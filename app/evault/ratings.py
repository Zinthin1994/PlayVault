"""Steam ratings, ProtonDB tiers, Steam Deck status and anti-cheat info for Epic titles.

Each Epic title is matched to a Steam app id via the Steam store search, then:
  - Steam user reviews  store.steampowered.com/appreviews/<id>
  - ProtonDB summary    protondb.com/api/v1/reports/summaries/<id>.json
  - Deck compatibility  store.steampowered.com/saleaction/ajaxgetdeckappcompatibilityreport
  - genres/metacritic   store.steampowered.com/api/appdetails
  - anti-cheat status   AreWeAntiCheatYet games.json (matched by Epic namespace / Steam id / name)
Everything is cached in data/ratings.json and refreshed weekly in the background.
"""
import collections
import difflib
import math
import re
import threading
import time
import urllib.parse

from . import paths
from .util import read_json, write_json, http_json, log

TTL = 7 * 86400
NOMATCH_TTL = 3 * 86400
AWACY_URL = 'https://raw.githubusercontent.com/AreWeAntiCheatYet/AreWeAntiCheatYet/HEAD/games.json'
REQUEST_GAP = 0.35  # seconds between Steam requests; keeps us well under rate limits

DECK = {0: 'Unknown', 1: 'Unsupported', 2: 'Playable', 3: 'Verified'}

_EDITION = re.compile(
    r"\s*[-:–—]?\s*\(?(the\s+)?(game of the year|goty|definitive|complete|deluxe|digital deluxe|"
    r"ultimate|standard|enhanced|gold|premium|special|anniversary|collector'?s|director'?s cut|"
    r"legendary|royal|launch|base|steam|epic)(\s+edition)?\)?\s*$", re.I)


def norm(s):
    s = (s or '').lower()
    s = s.replace('&', ' and ')
    s = re.sub(r'[™®©]', '', s)
    s = re.sub(r"['’]", '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def strip_edition(title):
    t = (title or '').strip()
    for _ in range(2):
        t2 = _EDITION.sub('', t).strip()
        if t2 == t or not t2:
            break
        t = t2
    t = re.sub(r'\s+edition$', '', t, flags=re.I).strip()
    return t or title


def similarity(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    if na.replace(' ', '') == nb.replace(' ', ''):
        ratio = max(ratio, 0.98)
    return ratio


class SteamUnreachable(RuntimeError):
    """Steam didn't answer (offline, rate limited, 5xx). Results must not be cached as 'no match'."""


def _search(term, country):
    """Store search results, or None when Steam couldn't be reached."""
    q = urllib.parse.quote(term)
    status, data = http_json(f'https://store.steampowered.com/api/storesearch/?term={q}&cc={country}&l=en')
    if status != 200 or data is None:
        return None
    return [{'id': it['id'], 'name': it.get('name', ''), 'image': it.get('tiny_image', '')}
            for it in data.get('items', []) if it.get('type') == 'app' and it.get('id')]


def steam_search(term, country='us'):
    return _search(term, country) or []


def best_match(title, country='us'):
    variants = []
    for v in (strip_edition(title), title):
        if v and v not in variants:
            variants.append(v)
    best = (0.0, None)
    for term in variants:
        results = _search(term, country)
        if results is None:
            raise SteamUnreachable(f'Steam search for {term!r} failed')
        for it in results[:10]:
            score = max(similarity(v, it['name']) for v in variants)
            # edition-insensitive match counts slightly less, so an exact title wins ties
            score = max(score, 0.97 * similarity(strip_edition(title), strip_edition(it['name'])))
            if score > best[0]:
                best = (score, it)
        if best[0] >= 0.97:
            break
        time.sleep(REQUEST_GAP)
    if best[1] and best[0] >= 0.84:
        return best[1], best[0]
    return None, best[0]


def fetch_reviews(appid, language='english'):
    """Steam's store page shows the score for your language (English by default); 'all' counts every language."""
    status, d = http_json(f'https://store.steampowered.com/appreviews/{appid}'
                          f'?json=1&language={language}&purchase_type=all&num_per_page=0&filter=all')
    if not d or not d.get('success'):
        return None
    qs = d.get('query_summary') or {}
    total = qs.get('total_reviews') or 0
    pos = qs.get('total_positive') or 0
    return {
        'desc': qs.get('review_score_desc') or ('No user reviews' if not total else ''),
        'score': qs.get('review_score') or 0,  # 1..9, 9 = Overwhelmingly Positive
        'positive': pos,
        'negative': qs.get('total_negative') or 0,
        'total': total,
        'pct': round(100 * pos / total) if total else None,
    }


def steamdb_rating(positive, total):
    """SteamDB's review rating: pulls scores with few reviews toward 50%, so 2/2 positive
    (64%) doesn't outrank a well-loved game with thousands of reviews."""
    if not total:
        return None
    avg = positive / total
    return round(100 * (avg - (avg - 0.5) * 2 ** (-math.log10(total + 1))))


def fetch_protondb(appid):
    status, d = http_json(f'https://www.protondb.com/api/v1/reports/summaries/{appid}.json')
    if not d:
        return None
    return {'tier': d.get('tier'), 'trending': d.get('trendingTier'), 'best': d.get('bestReportedTier'),
            'confidence': d.get('confidence'), 'total': d.get('total')}


def fetch_deck(appid):
    status, d = http_json('https://store.steampowered.com/saleaction/ajaxgetdeckappcompatibilityreport'
                          f'?nAppID={appid}&l=english')
    if not d or not d.get('success'):
        return None
    cat = (d.get('results') or {}).get('resolved_category')
    return {'category': cat, 'label': DECK.get(cat, 'Unknown')}


def fetch_details(appid, country='us'):
    status, d = http_json(f'https://store.steampowered.com/api/appdetails?appids={appid}'
                          f'&cc={country}&l=en&filters=genres,metacritic,release_date')
    entry = (d or {}).get(str(appid)) or {}
    if not entry.get('success'):
        return None
    data = entry.get('data') or {}
    if isinstance(data, list):
        data = {}
    return {
        'genres': [g.get('description') for g in data.get('genres', []) if g.get('description')],
        'metacritic': (data.get('metacritic') or {}).get('score'),
        'release_date': (data.get('release_date') or {}).get('date'),
    }


class Ratings:
    def __init__(self, on_update=None):
        self.lock = threading.RLock()
        self.cache = read_json(paths.RATINGS, {}) or {}
        self.queue = collections.deque()
        self.queued = set()
        self.cv = threading.Condition(self.lock)
        self.on_update = on_update or (lambda: None)
        self.awacy_index = None
        self.country = 'us'
        self.lang = 'english'
        self.enabled = True
        threading.Thread(target=self._worker, name='ratings', daemon=True).start()

    # ------------------------------------------------------------ public
    def get(self, app_name):
        with self.lock:
            return dict(self.cache.get(app_name) or {})

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.cache.items()}

    def pending(self):
        with self.lock:
            return len(self.queue)

    def request(self, app_name, title, namespace=None, force=False, steam_appid=None, hint=None):
        """Queue a refresh if missing/stale (or forced).
        steam_appid: a match the user picked (always used). hint: a store-catalog guess (verified first)."""
        with self.lock:
            cur = self.cache.get(app_name) or {}
            if not force and cur:
                age = time.time() - cur.get('fetched', 0)
                ttl = TTL if cur.get('steam_appid') else NOMATCH_TTL
                fresh = age < ttl and cur.get('reviews_lang', 'all') == self.lang
                same = (steam_appid is None or steam_appid == cur.get('steam_appid')) and \
                       (steam_appid is not None or hint is None or hint == cur.get('hint'))
                if fresh and same:
                    return False
            if app_name in self.queued:
                return False
            item = {'app': app_name, 'title': title, 'ns': namespace, 'appid': steam_appid, 'hint': hint}
            (self.queue.appendleft if force else self.queue.append)(item)
            self.queued.add(app_name)
            self.cv.notify()
            return True

    def clear_queue(self):
        with self.lock:
            self.queue.clear()
            self.queued.clear()

    # ------------------------------------------------------------ worker
    def _worker(self):
        backoff = 0
        while True:
            with self.lock:
                while not self.queue or not self.enabled:
                    self.cv.wait(timeout=30)
                item = self.queue.popleft()
                self.queued.discard(item['app'])
            try:
                entry = self._process(item)
                with self.lock:
                    self.cache[item['app']] = entry
                    write_json(paths.RATINGS, self.cache)
                self.on_update()
                backoff = 0
            except SteamUnreachable as e:
                # offline or rate limited: keep the item and wait, 30 s doubling up to 10 min
                backoff = min(600, backoff * 2 or 30)
                log.info('Steam unreachable (%s), retrying ratings in %ds', e, backoff)
                with self.lock:
                    if item['app'] not in self.queued:
                        self.queue.appendleft(item)
                        self.queued.add(item['app'])
                time.sleep(backoff)
                continue
            except Exception as e:
                log.warning('Ratings for %s failed: %r', item['app'], e)
            time.sleep(REQUEST_GAP)

    def _process(self, item):
        title, appid, hint = item['title'], item['appid'], item.get('hint')
        entry = {'fetched': time.time(), 'manual': bool(appid), 'reviews_lang': self.lang, 'hint': hint}
        reviews = None
        if appid:
            entry.update(steam_appid=int(appid), match_score=1.0)
        elif hint:
            # store catalogs sometimes point at a side listing (e.g. a GOTY page with 2 reviews);
            # if the hinted page is thin, compare it with the best title match and keep the busier one
            entry.update(steam_appid=int(hint), match_score=1.0)
            reviews = fetch_reviews(hint, self.lang)
            if not reviews or (reviews.get('total') or 0) < 100:
                time.sleep(REQUEST_GAP)
                match, score = best_match(title, self.country)
                if match and match['id'] != int(hint):
                    alt = fetch_reviews(match['id'], self.lang)
                    if alt and (alt.get('total') or 0) > 5 * max(1, (reviews or {}).get('total') or 0):
                        entry.update(steam_appid=match['id'], steam_name=match['name'], match_score=round(score, 3))
                        reviews = alt
        else:
            match, score = best_match(title, self.country)
            entry['match_score'] = round(score, 3)
            if match:
                entry.update(steam_appid=match['id'], steam_name=match['name'])
        entry['anticheat'] = self._anticheat(title, item.get('ns'), entry.get('steam_appid'))
        sid = entry.get('steam_appid')
        if not sid:
            entry['status'] = 'nomatch'
            return entry
        time.sleep(REQUEST_GAP)
        entry['reviews'] = reviews or fetch_reviews(sid, self.lang)
        time.sleep(REQUEST_GAP)
        entry['protondb'] = fetch_protondb(sid)
        time.sleep(REQUEST_GAP)
        entry['deck'] = fetch_deck(sid)
        time.sleep(REQUEST_GAP)
        entry['details'] = fetch_details(sid, self.country)
        if not any(entry.get(k) for k in ('reviews', 'protondb', 'deck', 'details')):
            # every lookup failed: Steam is unreachable rather than the game having no data
            raise SteamUnreachable(f'No Steam data for {sid}')
        if not entry.get('steam_name') and entry.get('details') is not None:
            entry['steam_name'] = title
        entry['status'] = 'ok'
        return entry

    # ------------------------------------------------------------ anti-cheat
    def _load_awacy(self):
        cached = read_json(paths.AWACY, None)
        if not cached or time.time() - cached.get('fetched', 0) > 3 * 86400:
            status, data = http_json(AWACY_URL, timeout=40)
            if isinstance(data, list):
                cached = {'fetched': time.time(), 'games': [
                    {'name': g.get('name'), 'status': g.get('status'), 'anticheats': g.get('anticheats') or [],
                     'steam': (g.get('storeIds') or {}).get('steam'),
                     'epic_ns': ((g.get('storeIds') or {}).get('epic') or {}).get('namespace')}
                    for g in data]}
                write_json(paths.AWACY, cached)
        games = (cached or {}).get('games') or []
        idx = {'ns': {}, 'steam': {}, 'name': {}}
        for g in games:
            if g.get('epic_ns'):
                idx['ns'][g['epic_ns']] = g
            if g.get('steam'):
                idx['steam'][str(g['steam'])] = g
            if g.get('name'):
                idx['name'][norm(g['name'])] = g
        self.awacy_index = idx

    def _anticheat(self, title, namespace, steam_appid):
        if self.awacy_index is None:
            try:
                self._load_awacy()
            except Exception as e:
                log.warning('Anti-cheat list unavailable: %r', e)
                self.awacy_index = {'ns': {}, 'steam': {}, 'name': {}}
        idx = self.awacy_index
        g = (namespace and idx['ns'].get(namespace)) or \
            (steam_appid and idx['steam'].get(str(steam_appid))) or \
            idx['name'].get(norm(strip_edition(title))) or idx['name'].get(norm(title))
        if not g:
            return None
        return {'status': g.get('status'), 'anticheats': g.get('anticheats')}
