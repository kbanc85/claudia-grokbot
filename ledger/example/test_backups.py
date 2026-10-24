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

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ledger" / "claudia_ledger.py"


def run_ledger(ledger: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT), "--ledger", str(ledger), *args]
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
        self.ledger = self.tmp / "ledger"
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_write_creates_backup_and_restore_works(self) -> None:
        run_ledger(self.ledger, "init")
        run_ledger(self.ledger, "add", "person", "--name", "Alex Rivera")
        listed = run_ledger(self.ledger, "backups")
        self.assertIn("count:", listed.stdout)
        self.assertNotIn("no backups yet", listed.stdout)

        # Snapshot taken before the next write still has Alex (backup-before-write).
        run_ledger(self.ledger, "add", "person", "--name", "Jordan Lee", "--force-new")
        people = (self.ledger / "people.csv").read_text(encoding="utf-8")
        self.assertIn("Jordan Lee", people)
        stamps = sorted(p.name for p in (self.ledger / "backups").iterdir() if p.is_dir())
        self.assertGreaterEqual(len(stamps), 2)
        # The latest backup was taken just before Jordan was written, so it has Alex only.
        with_alex = stamps[-1]

        proc = run_ledger(self.ledger, "restore", with_alex)
        self.assertIn("safety snapshot", proc.stdout)
        people2 = (self.ledger / "people.csv").read_text(encoding="utf-8")
        self.assertIn("Alex Rivera", people2)
        self.assertNotIn("Jordan Lee", people2)

        # Safety snapshot of pre-restore state exists
        stamps2 = sorted(p.name for p in (self.ledger / "backups").iterdir() if p.is_dir())
        self.assertGreaterEqual(len(stamps2), 2)

    def test_backup_keep_env(self) -> None:
        run_ledger(self.ledger, "--backup-keep", "2", "init")
        for i in range(4):
            run_ledger(
                self.ledger,
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
