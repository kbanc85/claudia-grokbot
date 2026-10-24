#!/usr/bin/env python3
# Made with Grok Bot.
"""Per-bot home and bot_id isolation tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "ledger" / "claudia_ledger.py"
RENDER = ROOT / "templates" / "claudia_render.py"
UPDATE = ROOT / "update" / "claudia_update.py"
FIRST_RUN = ROOT / "setup" / "first_run.sh"


def run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    # Drop shared defaults so missing-home tests are honest.
    full_env.pop("CLAUDIA_HOME", None)
    full_env.pop("CLAUDIA_BOT_ID", None)
    if env:
        full_env.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


class HomeRequiredTests(unittest.TestCase):
    def test_ledger_missing_home_fails(self) -> None:
        proc = run([sys.executable, str(LEDGER), "check"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDIA_HOME", proc.stderr)

    def test_render_missing_home_fails(self) -> None:
        proc = run(
            [sys.executable, str(RENDER), "--template", "welcome_tour", "--data", "/dev/null"]
        )
        # fails on home before data parse when home missing
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDIA_HOME", proc.stderr)

    def test_update_missing_home_fails(self) -> None:
        proc = run([sys.executable, str(UPDATE), "check"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDIA_HOME", proc.stderr)

    def test_first_run_refuses_without_home(self) -> None:
        proc = run(["bash", str(FIRST_RUN)])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDIA_HOME", proc.stdout + proc.stderr)

    def test_first_run_refuses_without_bot_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = run(["bash", str(FIRST_RUN), "--home", td])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("CLAUDIA_BOT_ID", proc.stdout + proc.stderr)


class HomeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-home-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_two_homes_stay_isolated(self) -> None:
        home_a = self.tmp / "bot-a"
        home_b = self.tmp / "bot-b"
        for home, bot, name in (
            (home_a, "bot-a", "Alex Rivera"),
            (home_b, "bot-b", "Jordan Lee"),
        ):
            proc = run(
                [
                    sys.executable,
                    str(LEDGER),
                    "--home",
                    str(home),
                    "--bot-id",
                    bot,
                    "init",
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            proc = run(
                [
                    sys.executable,
                    str(LEDGER),
                    "--home",
                    str(home),
                    "--bot-id",
                    bot,
                    "add",
                    "person",
                    "--name",
                    name,
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

        people_a = (home_a / "ledger" / "people.csv").read_text(encoding="utf-8")
        people_b = (home_b / "ledger" / "people.csv").read_text(encoding="utf-8")
        self.assertIn("Alex Rivera", people_a)
        self.assertNotIn("Jordan Lee", people_a)
        self.assertIn("Jordan Lee", people_b)
        self.assertNotIn("Alex Rivera", people_b)

        owner_a = json.loads((home_a / "ledger" / "owner.json").read_text(encoding="utf-8"))
        owner_b = json.loads((home_b / "ledger" / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual(owner_a["bot_id"], "bot-a")
        self.assertEqual(owner_b["bot_id"], "bot-b")


class BotIdMismatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-botid-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        proc = run(
            [
                sys.executable,
                str(LEDGER),
                "--home",
                str(self.home),
                "--bot-id",
                "owner-bot",
                "init",
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_check_warns_on_mismatch(self) -> None:
        proc = run(
            [
                sys.executable,
                str(LEDGER),
                "--home",
                str(self.home),
                "--bot-id",
                "intruder-bot",
                "check",
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        blob = proc.stdout + proc.stderr
        self.assertIn("bot_id mismatch", blob)
        self.assertIn("owner-bot", blob)
        self.assertIn("intruder-bot", blob)

    def test_write_refuses_on_mismatch(self) -> None:
        proc = run(
            [
                sys.executable,
                str(LEDGER),
                "--home",
                str(self.home),
                "--bot-id",
                "intruder-bot",
                "add",
                "person",
                "--name",
                "Should Fail",
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("bot_id mismatch", proc.stderr)
        people = (self.home / "ledger" / "people.csv").read_text(encoding="utf-8")
        self.assertNotIn("Should Fail", people)

    def test_flag_wins_over_env_home(self) -> None:
        other = self.tmp / "other"
        proc = run(
            [
                sys.executable,
                str(LEDGER),
                "--home",
                str(other),
                "--bot-id",
                "other-bot",
                "init",
            ],
            env={"CLAUDIA_HOME": str(self.home), "CLAUDIA_BOT_ID": "owner-bot"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((other / "ledger" / "owner.json").is_file())
        owner = json.loads((other / "ledger" / "owner.json").read_text(encoding="utf-8"))
        self.assertEqual(owner["bot_id"], "other-bot")


if __name__ == "__main__":
    unittest.main()
