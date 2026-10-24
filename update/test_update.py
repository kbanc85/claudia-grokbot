#!/usr/bin/env python3
# Made with Grok Bot.
"""Tests for claudia_update.py (manifest + version compare)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "update" / "claudia_update.py"


class UpdateTests(unittest.TestCase):
    def test_manifest_generation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Minimal fake checkout
            (root / "VERSION").write_text("1.1.0\n", encoding="utf-8")
            (root / "ledger").mkdir()
            (root / "ledger" / "claudia_ledger.py").write_text("# x\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "manifest", "--root", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            man = (root / "MANIFEST.sha256").read_text(encoding="utf-8")
            self.assertIn("VERSION", man)
            self.assertIn("ledger/claudia_ledger.py", man)
            self.assertIn("Made with Grok Bot", man)

    def test_version_compare_helper(self) -> None:
        sys.path.insert(0, str(ROOT / "update"))
        import claudia_update as u

        self.assertTrue(u.version_gt((1, 1, 0), (1, 0, 0)))
        self.assertFalse(u.version_gt((1, 0, 0), (1, 1, 0)))
        self.assertFalse(u.version_gt((1, 1, 0), (1, 1, 0)))


if __name__ == "__main__":
    unittest.main()
