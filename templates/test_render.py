#!/usr/bin/env python3
# Made with Grok Bot.
"""Tests for claudia_render.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "templates" / "claudia_render.py"
LEDGER = ROOT / "ledger" / "claudia_ledger.py"
BOT = "render-test-bot"


def run(
    *args: str,
    check: bool = True,
    home: Path | None = None,
    clear_home_env: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT)]
    if home is not None:
        cmd.extend(["--home", str(home)])
    cmd.extend(args)
    env = os.environ.copy()
    if clear_home_env:
        env.pop("CLAUDIA_HOME", None)
        env.pop("CLAUDIA_BOT_ID", None)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"failed: {args}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


class RenderTests(unittest.TestCase):
    def test_welcome_fills_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            data = home / "vars.json"
            data.write_text(json.dumps({"name": "Sam"}), encoding="utf-8")
            proc = run("--template", "welcome_tour", "--data", str(data), home=home)
            self.assertIn("Hi Sam", proc.stdout)
            self.assertIn("👋", proc.stdout)
            self.assertNotIn("{name}", proc.stdout)
            self.assertIn("Chief of Staff", proc.stdout)

    def test_missing_placeholder_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            data = home / "vars.json"
            data.write_text(json.dumps({}), encoding="utf-8")
            proc = run(
                "--template",
                "morning_brief",
                "--data",
                str(data),
                home=home,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing placeholders", proc.stderr)

    def test_without_data_requires_demo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            proc = run("--template", "promise_catch", home=home, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("--data", proc.stderr)

    def test_missing_home_fails(self) -> None:
        proc = run(
            "--template",
            "welcome_tour",
            "--list-placeholders",
            check=False,
            clear_home_env=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDIA_HOME", proc.stderr)

    def test_no_em_dash_in_templates(self) -> None:
        for path in (ROOT / "templates").glob("*.txt"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path.name)
            self.assertNotIn("\u2013", text, path.name)

    def test_ledger_promise_catch_demo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            subprocess.run(
                [
                    sys.executable,
                    str(LEDGER),
                    "--home",
                    str(home),
                    "--bot-id",
                    BOT,
                    "init",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(LEDGER),
                    "--home",
                    str(home),
                    "--bot-id",
                    BOT,
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
                    "--home",
                    str(home),
                    "--bot-id",
                    BOT,
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
            proc = run("--template", "promise_catch", "--demo", home=home)
            self.assertIn("1 promises", proc.stdout)
            self.assertIn("Send deck", proc.stdout)


if __name__ == "__main__":
    unittest.main()
