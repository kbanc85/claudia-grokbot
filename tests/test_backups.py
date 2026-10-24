#!/usr/bin/env python3
# Made with Grok Bot.
"""Tests for ledger backups and restore."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ledger" / "claudia_ledger.py"
BOT = "backup-bot"


def run_home(home: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--home",
        str(home),
        "--bot-id",
        BOT,
        *args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-bak-"))
        self.home = self.tmp / "home"
        self.ledger = self.home / "ledger"
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_write_creates_backup_and_restore_works(self) -> None:
        run_home(self.home, "init")
        run_home(self.home, "add", "person", "--name", "Alex Rivera")
        listed = run_home(self.home, "backups")
        self.assertIn("count:", listed.stdout)
        self.assertNotIn("no backups yet", listed.stdout)

        run_home(self.home, "add", "person", "--name", "Jordan Lee", "--force-new")
        people = (self.ledger / "people.csv").read_text(encoding="utf-8")
        self.assertIn("Jordan Lee", people)
        stamps = sorted(p.name for p in (self.ledger / "backups").iterdir() if p.is_dir())
        self.assertGreaterEqual(len(stamps), 2)
        with_alex = stamps[-1]

        proc = run_home(self.home, "restore", with_alex)
        self.assertIn("safety snapshot", proc.stdout)
        people2 = (self.ledger / "people.csv").read_text(encoding="utf-8")
        self.assertIn("Alex Rivera", people2)
        self.assertNotIn("Jordan Lee", people2)

        stamps2 = sorted(p.name for p in (self.ledger / "backups").iterdir() if p.is_dir())
        self.assertGreaterEqual(len(stamps2), 2)

    def test_backup_keep_env(self) -> None:
        run_home(self.home, "--backup-keep", "2", "init")
        for i in range(4):
            run_home(
                self.home,
                "--backup-keep",
                "2",
                "add",
                "person",
                "--name",
                f"Person {i}",
                "--force-new",
            )
        stamps = [p for p in (self.ledger / "backups").iterdir() if p.is_dir()]
        self.assertLessEqual(len(stamps), 2)


if __name__ == "__main__":
    unittest.main()
