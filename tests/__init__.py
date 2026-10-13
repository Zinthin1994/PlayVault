"""PlayVault unit tests (stdlib unittest, no extra packages).

    python -m unittest discover -s tests -t .

They run on Windows or Linux and never touch the real ~/.local/share/playvault:
PLAYVAULT_HOME points at a temporary folder before evault is imported.
"""
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix='playvault-test-')
os.environ['PLAYVAULT_HOME'] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app'))
