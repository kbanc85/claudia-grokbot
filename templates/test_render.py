#!/usr/bin/env python3
# Made with Grok Bot.
"""Tests for claudia_render.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "templates" / "claudia_render.py"
LEDGER = ROOT / "ledger" / "claudia_ledger.py"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"failed: {args}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


class RenderTests(unittest.TestCase):
    def test_welcome_fills_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "vars.json"
            data.write_text(json.dumps({"name": "Sam"}), encoding="utf-8")
            proc = run("--template", "welcome_tour", "--data", str(data))
            self.assertIn("Hi Sam", proc.stdout)
            self.assertIn("👋", proc.stdout)
            self.assertNotIn("{name}", proc.stdout)

    def test_missing_placeholder_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "vars.json"
            data.write_text(json.dumps({}), encoding="utf-8")
            proc = run(
                "--template",
                "morning_brief",
                "--data",
                str(data),
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing placeholders", proc.stderr)

    def test_no_em_dash_in_templates(self) -> None:
        for path in (ROOT / "templates").glob("*.txt"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path.name)
            self.assertNotIn("—", text, path.name)

    def test_ledger_promise_catch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger"
            subprocess.run(
                [sys.executable, str(LEDGER), "--ledger", str(ledger), "init"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(LEDGER),
                    "--ledger",
                    str(ledger),
                    "add",
                    "person",
                    "--name",
                    "Alex Rivera",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(LEDGER),
                    "--ledger",
                    str(ledger),
                    "add",
                    "commitment",
                    "--direction",
                    "I owe",
                    "--person-id",
                    "P-0001",
                    "--what",
                    "Send deck",
                    "--due",
                    "2026-10-25",
                    "--source-link",
                    "test",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            proc = run("--template", "promise_catch", "--ledger", str(ledger))
            self.assertIn("1 promises", proc.stdout)
            self.assertIn("Send deck", proc.stdout)


if __name__ == "__main__":
    unittest.main()
