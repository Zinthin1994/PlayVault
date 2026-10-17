"""Builds dist/PlayVault-<version>.tar.gz (LF line endings, executable scripts).
    python tools/package.py
"""
import io
import os
import re
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = re.search(r"VERSION = '([^']+)'", open(os.path.join(ROOT, 'app', 'evault', '__init__.py')).read()).group(1)
OUT = os.path.join(ROOT, 'dist', f'PlayVault-{VERSION}.tar.gz')
TEXT = ('.py', '.js', '.json', '.html', '.css', '.sh', '.md', '')  # '' = extensionless (bin/, LICENSE); .cmd/.ps1 keep their CRLF
INCLUDE = ['install.sh', 'uninstall.sh', 'install.ps1', 'install.cmd', 'uninstall.ps1', 'README.md', 'LICENSE', 'app']


def add(tar, path, arc):
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name == '__pycache__':
                continue
            add(tar, os.path.join(path, name), f'{arc}/{name}')
        return
    data = open(path, 'rb').read()
    ext = os.path.splitext(path)[1]
    if ext in TEXT and b'\0' not in data[:1024]:
        data = data.replace(b'\r\n', b'\n')
    info = tarfile.TarInfo(arc)
    info.size = len(data)
    executable = arc.endswith('.sh') or '/bin/' in arc
    info.mode = 0o755 if executable else 0o644
    info.mtime = int(os.path.getmtime(path))
    tar.addfile(info, io.BytesIO(data))


os.makedirs(os.path.dirname(OUT), exist_ok=True)
with tarfile.open(OUT, 'w:gz') as tar:
    for item in INCLUDE:
        add(tar, os.path.join(ROOT, item), f'PlayVault/{item}')
print(OUT, os.path.getsize(OUT), 'bytes')
