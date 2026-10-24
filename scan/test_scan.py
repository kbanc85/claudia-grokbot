#!/usr/bin/env python3
# Made with Grok Bot.
"""Unit tests for claudia_scan.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scan" / "claudia_scan.py"


class ScanTests(unittest.TestCase):
    def _scan(self, items: list[dict]) -> list[dict]:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "items.jsonl"
            path.write_text(
                "".join(json.dumps(i) + "\n" for i in items),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(path),
                    "--owner-name",
                    "Sam Chen",
                    "--owner-email",
                    "sam.chen@example.com",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

    def test_friday_relative_due(self) -> None:
        items = [
            {
                "id": "1",
                "source": "email",
                "date": "2026-10-20",  # Tuesday
                "from": "Sam Chen",
                "to": "Alex Rivera",
                "subject": "Kit",
                "text": "I'll send the updated kit by Friday.",
                "link": "t1",
            }
        ]
        out = self._scan(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["direction"], "I owe")
        self.assertEqual(out[0]["due"], "2026-10-23")
        self.assertIn("send", out[0]["what"].lower())

    def test_rejects_vague_catch_up(self) -> None:
        items = [
            {
                "id": "2",
                "source": "email",
                "date": "2026-10-20",
                "from": "Alex Rivera",
                "to": "Sam Chen",
                "subject": "Hi",
                "text": "Let's catch up sometime soon.",
                "link": "t2",
            }
        ]
        out = self._scan(items)
        self.assertEqual(out, [])

    def test_rejects_third_party(self) -> None:
        items = [
            {
                "id": "3",
                "source": "email",
                "date": "2026-10-20",
                "from": "Jordan Lee",
                "to": "Priya Nair",
                "subject": "Deck",
                "text": "I will send Priya the investor deck by Friday.",
                "link": "t3",
            }
        ]
        out = self._scan(items)
        self.assertEqual(out, [])

    def test_tomorrow_and_eod(self) -> None:
        items = [
            {
                "id": "4",
                "source": "email",
                "date": "2026-10-21",
                "from": "Alex Rivera",
                "to": "Sam Chen",
                "subject": "Ask",
                "text": "Can you send me the partner deck by tomorrow EOD?",
                "link": "t4",
            }
        ]
        out = self._scan(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["direction"], "I owe")
        self.assertEqual(out[0]["due"], "2026-10-22")


if __name__ == "__main__":
    unittest.main()
