"""The Windows / SteamOS split: process handling, paths, saves and the install scripts."""
import os
import re
import subprocess
import sys
import time
import unittest

from tests import HOME  # noqa: F401
from evault import gog, host, launcher, paths, steam, system

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOWS = os.name == 'nt'
SLEEP = [sys.executable, '-c', 'import time; time.sleep(60)']


def _wait_dead(proc, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return False


class Processes(unittest.TestCase):
    def test_pid_alive_does_not_kill(self):
        # os.kill(pid, 0) terminates the process on Windows, so pid_alive must not use it there
        proc = subprocess.Popen(SLEEP, **host.spawn_kwargs())
        try:
            for _ in range(3):
                self.assertTrue(host.pid_alive(proc.pid))
            time.sleep(0.3)
            self.assertIsNone(proc.poll(), 'pid_alive stopped the process it was probing')
        finally:
            host.terminate(proc.pid, force=True)
            proc.wait(timeout=10)
        self.assertFalse(host.pid_alive(proc.pid))

    def test_pid_alive_edge_cases(self):
        self.assertTrue(host.pid_alive(os.getpid()))
        for bad in (None, 0, '', 'x', -1):
            self.assertFalse(host.pid_alive(bad), repr(bad))

    def test_terminate_stops_the_process(self):
        proc = subprocess.Popen(SLEEP, **host.spawn_kwargs())
        host.terminate(proc.pid)
        if not _wait_dead(proc):
            host.terminate(proc.pid, force=True)  # taskkill without /F only asks windowless processes
            self.assertTrue(_wait_dead(proc))

    def test_terminate_takes_the_whole_tree(self):
        parent = subprocess.Popen(
            [sys.executable, '-c',
             'import subprocess, sys, time\n'
             'c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
             'print(c.pid, flush=True)\n'
             'time.sleep(60)'],
            stdout=subprocess.PIPE, text=True, **host.spawn_kwargs())
        try:
            child = int(parent.stdout.readline())
            self.assertTrue(host.pid_alive(child))
            host.terminate(parent.pid, force=True)
            self.assertTrue(_wait_dead(parent))
            deadline = time.time() + 10
            while host.pid_alive(child) and time.time() < deadline:
                time.sleep(0.1)
            self.assertFalse(host.pid_alive(child), 'the game (child) survived stopping its launcher')
        finally:
            parent.stdout.close()
            if parent.poll() is None:
                parent.kill()


class Layout(unittest.TestCase):
    def test_script_and_binary_names(self):
        if WINDOWS:
            self.assertTrue(paths.LAUNCH_SCRIPT.endswith('playvault-launch.cmd'))
            self.assertTrue(paths.APP_SCRIPT.endswith('playvault.cmd'))
            self.assertTrue(paths.LEGENDARY_BIN.endswith(os.path.join('Scripts', 'legendary.exe')))
            self.assertTrue(paths.GOGDL_BIN.endswith('gogdl.exe'))
        else:
            self.assertTrue(paths.LAUNCH_SCRIPT.endswith('playvault-launch'))
            self.assertTrue(paths.LEGENDARY_BIN.endswith(os.path.join('bin', 'legendary')))

    def test_every_launcher_script_exists(self):
        for name in ('playvault', 'playvault.cmd', 'playvault.ps1', 'playvault-launch', 'playvault-launch.cmd'):
            self.assertTrue(os.path.isfile(os.path.join(REPO, 'app', 'bin', name)), name)

    def test_store_dir_roots(self):
        self.assertEqual(paths.store_dir('/', 'epic'), os.path.join(os.sep, 'Epic'))
        if WINDOWS:
            self.assertEqual(paths.store_dir('D:\\', 'epic'), 'D:\\Epic')
            self.assertEqual(paths.store_dir('D:\\Games\\GOG\\', 'gog'), 'D:\\Games\\GOG')
            self.assertEqual(paths.store_dir('D:\\Games', 'gog'), 'D:\\Games\\GOG')

    def test_windows_has_no_proton_tools(self):
        from evault import proton
        if WINDOWS:
            self.assertEqual(proton.list_tools(), [])


class Saves(unittest.TestCase):
    inst = {'id': '123', 'install_path': os.path.join(os.sep, 'games', 'Some Game')}

    @unittest.skipUnless(WINDOWS, 'Windows save folders')
    def test_windows_locations_are_real_folders(self):
        doc = host.known_folder('documents')
        out = gog.resolve_location('<?DOCUMENTS?>/My Game/Saves', self.inst, None)
        self.assertEqual(out, os.path.normpath(doc + '/My Game/Saves'))
        local = gog.resolve_location('<?APPLICATION_DATA_LOCAL?>\\Studio', self.inst, None)
        self.assertTrue(local.lower().startswith(host.known_folder('local').lower()))
        self.assertEqual(gog.resolve_location('<?INSTALL?>/saves', self.inst, None),
                         os.path.normpath(self.inst['install_path'] + '/saves'))

    @unittest.skipIf(WINDOWS, 'Proton prefix layout')
    def test_linux_locations_stay_in_the_prefix(self):
        out = gog.resolve_location('<?DOCUMENTS?>/My Game', self.inst, '/pfx')
        self.assertEqual(out, '/pfx/drive_c/users/steamuser/Documents/My Game')

    def test_known_folders_exist_as_paths(self):
        for name in ('home', 'documents', 'saved games', 'local', 'local_low', 'roaming'):
            self.assertTrue(host.known_folder(name))


class LaunchArguments(unittest.TestCase):
    def test_plain_arguments(self):
        self.assertEqual(launcher._split('-windowed -w 1920'), ['-windowed', '-w', '1920'])

    @unittest.skipUnless(WINDOWS, 'Windows path quoting')
    def test_windows_paths_keep_backslashes(self):
        self.assertEqual(launcher._split('-log "C:\\My Games\\x.log" -w'), ['-log', 'C:\\My Games\\x.log', '-w'])

    @unittest.skipIf(WINDOWS, 'POSIX quoting')
    def test_posix_quotes(self):
        self.assertEqual(launcher._split('-name "two words"'), ['-name', 'two words'])


class Discovery(unittest.TestCase):
    def test_steam_helpers_do_not_crash(self):
        self.assertIsInstance(steam.steam_running(), bool)
        self.assertTrue(steam.steam_exe())

    def test_drives_always_offer_internal_storage(self):
        out = system.drives()
        self.assertEqual(out[0]['kind'], 'internal')
        for d in out:
            self.assertTrue(d['games_dir'])
        if WINDOWS:
            self.assertTrue(any(d['mount'].upper().startswith(os.path.splitdrive(paths.HOME)[0].upper()) for d in out))


class InstallScripts(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(REPO, name), encoding='utf-8') as f:
            return f.read()

    def test_electron_version_matches_between_installers(self):
        # the updater compares install.sh's version with the installed runtime, and both
        # installers must fetch the same one
        sh = re.search(r'^ELECTRON_VERSION="([^"]+)"', self.read('install.sh'), re.M).group(1)
        ps = re.search(r"^\$ElectronVersion = '([^']+)'", self.read('install.ps1'), re.M).group(1)
        self.assertEqual(sh, ps)

    def test_windows_scripts_are_ascii(self):
        # Windows PowerShell 5.1 reads BOM-less files as ANSI
        for name in ('install.ps1', 'uninstall.ps1', 'install.cmd', 'app/bin/playvault.ps1',
                     'app/bin/playvault.cmd', 'app/bin/playvault-launch.cmd'):
            with open(os.path.join(REPO, name), 'rb') as f:
                self.assertTrue(all(b < 128 for b in f.read()), name)


if __name__ == '__main__':
    unittest.main()
