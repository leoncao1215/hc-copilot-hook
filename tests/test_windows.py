import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "Windows console adapter")
class WindowsTests(unittest.TestCase):
    def test_offline_status(self):
        subprocess.run(
            [shutil.which("node"), "--test", str(ROOT / "windows" / "test-status.cjs")],
            check=True, timeout=30,
        )

    def test_install_backup_and_uninstall(self):
        subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-File",
             str(ROOT / "windows" / "test-install.ps1")],
            check=True, timeout=30,
        )
