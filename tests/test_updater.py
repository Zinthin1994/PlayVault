import io
import os
import tarfile
import tempfile
import unittest

from tests import HOME  # noqa: F401
from evault import paths, updater


class Versions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updater.parse_version('1.2.1'), (1, 2, 1))
        self.assertEqual(updater.parse_version('v1.10'), (1, 10))
        self.assertIsNone(updater.parse_version('1.2.1-beta'))
        self.assertIsNone(updater.parse_version(''))
        self.assertIsNone(updater.parse_version(None))

    def test_newer_compares_numerically(self):
        self.assertTrue(updater.newer('1.10.0', '1.9.9'))
        self.assertTrue(updater.newer('v1.2.2', '1.2.1'))
        self.assertFalse(updater.newer('1.2.1', '1.2.1'))
        self.assertFalse(updater.newer('1.2.0', '1.2.1'))
        self.assertFalse(updater.newer('garbage', '1.0'))


def _tar(members):
    """members: [(TarInfo, bytes|None)] -> an open tarfile over an in-memory .tar"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as t:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            t.addfile(info, io.BytesIO(data) if data is not None else None)
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode='r')


class SafeExtract(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.dest = os.path.join(self.base, 'extract')
        os.makedirs(self.dest)

    def test_normal_files_extract(self):
        with _tar([(tarfile.TarInfo('PlayVault/app/x.txt'), b'hi')]) as t:
            updater._safe_extract(t, self.dest)
        with open(os.path.join(self.dest, 'PlayVault', 'app', 'x.txt'), 'rb') as f:
            self.assertEqual(f.read(), b'hi')

    def test_path_traversal_is_refused(self):
        with _tar([(tarfile.TarInfo('../evil.txt'), b'x')]) as t:
            with self.assertRaises(Exception):
                updater._safe_extract(t, self.dest)
        self.assertFalse(os.path.exists(os.path.join(self.base, 'evil.txt')))

    def test_absolute_symlink_is_refused(self):
        link = tarfile.TarInfo('PlayVault/link')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        with _tar([(link, None)]) as t:
            with self.assertRaises(Exception):
                updater._safe_extract(t, self.dest)


class Snapshot(unittest.TestCase):
    def test_fresh_state(self):
        u = updater.Updater()
        s = u.snapshot()
        self.assertEqual(s['current'], updater.VERSION)
        self.assertIn(s['status'], ('idle', 'available', 'ready'))
        self.assertTrue(s['url'].startswith('https://github.com/'))

    def test_download_without_digest_is_refused(self):
        u = updater.Updater()
        with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
            u._download_and_stage({'name': updater.ASSET, 'size': 10, 'browser_download_url': 'http://x'})
        self.assertFalse(os.path.exists(os.path.join(paths.UPDATE_DIR, 'app')))


if __name__ == '__main__':
    unittest.main()
