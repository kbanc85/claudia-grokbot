#!/usr/bin/env python3
# Made with Grok Bot.
"""Regression tests for v1.1.1 smoke-test fixes."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "ledger" / "claudia_ledger.py"
RENDER = ROOT / "templates" / "claudia_render.py"
BRAIN = ROOT / "brain" / "claudia-brain.canvas.tsx"
FIRST_RUN = ROOT / "setup" / "first_run.sh"
BOT = "v111-bot"


def run(
    home: Path,
    *args: str,
    bot_id: str | None = BOT,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(LEDGER), "--home", str(home)]
    if bot_id is not None:
        cmd.extend(["--bot-id", bot_id])
    cmd.extend(args)
    full_env = os.environ.copy()
    full_env.pop("CLAUDIA_HOME", None)
    full_env.pop("CLAUDIA_BOT_ID", None)
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=full_env)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"failed ({proc.returncode}): {args}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class IdNeverReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-id-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init")

    def test_merge_does_not_reuse_person_id(self) -> None:
        run(self.home, "add", "person", "--name", "Alex")
        run(self.home, "add", "person", "--name", "Jordan", "--force-new")
        run(
            self.home,
            "merge",
            "--kept",
            "P-0001",
            "--merged",
            "P-0002",
            "--evidence",
            "same person",
        )
        people = read_csv(self.home / "ledger" / "people.csv")
        self.assertEqual(len(people), 1)
        self.assertIn("P-0002", people[0]["aliases"])
        proc = run(self.home, "add", "person", "--name", "Sam", "--force-new")
        self.assertIn("P-0003", proc.stdout)
        people = read_csv(self.home / "ledger" / "people.csv")
        ids = {p["person_id"] for p in people}
        self.assertEqual(ids, {"P-0001", "P-0003"})

    def test_forget_does_not_reuse_person_id(self) -> None:
        run(self.home, "add", "person", "--name", "Alex")
        run(self.home, "add", "person", "--name", "Jordan", "--force-new")
        run(self.home, "forget", "--id", "P-0002", "--yes")
        proc = run(self.home, "add", "person", "--name", "Sam", "--force-new")
        self.assertIn("P-0003", proc.stdout)
        owner = json.loads((self.home / "ledger" / "owner.json").read_text())
        self.assertGreaterEqual(int(owner["id_counters"]["P"]), 3)


class WriteRequiresBotIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-botwrite-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init")

    def test_write_without_bot_id_fails(self) -> None:
        proc = run(
            self.home,
            "add",
            "person",
            "--name",
            "No Bot",
            bot_id=None,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDIA_BOT_ID", proc.stderr)
        people = (self.home / "ledger" / "people.csv").read_text(encoding="utf-8")
        self.assertNotIn("No Bot", people)

    def test_check_without_bot_id_when_owner_has_one(self) -> None:
        proc = run(self.home, "check", bot_id=None, check=False)
        self.assertNotEqual(proc.returncode, 0)
        blob = proc.stdout + proc.stderr
        self.assertIn("bot_id", blob)


class NoDashTests(unittest.TestCase):
    def test_no_em_or_en_dash_in_shipped_text(self) -> None:
        roots = [
            ROOT / "ledger" / "claudia_ledger.py",
            ROOT / "templates",
            ROOT / "docs",
            ROOT / "README.md",
            ROOT / "CHANGELOG.md",
            ROOT / "setup",
            ROOT / "update" / "README.md",
            ROOT / "brain" / "README.md",
            ROOT / "scan" / "README.md",
        ]
        bad: list[str] = []
        for root in roots:
            paths = [root] if root.is_file() else list(root.rglob("*"))
            for path in paths:
                if not path.is_file():
                    continue
                if path.suffix not in {".py", ".md", ".txt", ".sh", ".tsx"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                if "\u2014" in text or "\u2013" in text:
                    bad.append(str(path.relative_to(ROOT)))
        self.assertEqual(bad, [], f"em/en dash found in: {bad}")


class UpdateCommitmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-upd-c-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init")
        run(self.home, "add", "person", "--name", "Alex")
        run(
            self.home,
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
            "chat:1",
        )

    def test_update_commitment_fields(self) -> None:
        run(
            self.home,
            "update",
            "commitment",
            "--id",
            "C-0001",
            "--due",
            "2026-10-28",
            "--what",
            "Send deck v2",
            "--source-link",
            "chat:2",
        )
        rows = read_csv(self.home / "ledger" / "commitments.csv")
        self.assertEqual(rows[0]["due"], "2026-10-28")
        self.assertEqual(rows[0]["what"], "Send deck v2")
        self.assertEqual(rows[0]["source_link"], "chat:2")
        self.assertEqual(rows[0]["status"], "open")
        log = read_csv(self.home / "ledger" / "claudia_log.csv")
        self.assertTrue(any(r["action"] == "update_commitment" for r in log))

    def test_open_commitment_requires_due(self) -> None:
        proc = run(
            self.home,
            "add",
            "commitment",
            "--direction",
            "I owe",
            "--person-id",
            "P-0001",
            "--what",
            "No due",
            "--source-link",
            "x",
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("due", proc.stderr.lower())


class HealthSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-health-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init")
        run(
            self.home,
            "add",
            "person",
            "--name",
            "Alex",
            "--last-contact",
            "2026-10-01",
            "--cadence-days",
            "7",
        )

    def test_weekly_snapshot_and_changed_on_update(self) -> None:
        run(self.home, "health", "snapshot")
        hist = read_csv(self.home / "ledger" / "health_history.csv")
        weekly = [r for r in hist if r["reason"] == "weekly"]
        self.assertGreaterEqual(len(weekly), 1)
        self.assertEqual(weekly[-1]["person_id"], "P-0001")
        before = len(hist)
        run(
            self.home,
            "update",
            "person",
            "--id",
            "P-0001",
            "--last-contact",
            "2026-10-24",
        )
        hist2 = read_csv(self.home / "ledger" / "health_history.csv")
        self.assertGreater(len(hist2), before)
        self.assertTrue(any(r["reason"] == "changed" for r in hist2))


class RenderDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-render-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init")

    def _render(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, str(RENDER), "--home", str(self.home), *args]
        env = os.environ.copy()
        env.pop("CLAUDIA_HOME", None)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if check and proc.returncode != 0:
            raise AssertionError(f"{args}\n{proc.stderr}")
        return proc

    def test_without_data_fails(self) -> None:
        proc = self._render("--template", "morning_brief", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--data", proc.stderr)

    def test_meditate_demo_never_says_clean_day(self) -> None:
        proc = self._render("--template", "meditate", "--demo")
        self.assertNotIn("Clean day", proc.stdout)

    def test_welcome_demo(self) -> None:
        proc = self._render("--template", "welcome_tour", "--demo")
        self.assertIn("Chief of Staff", proc.stdout)
        self.assertIn("never send", proc.stdout.lower())
        self.assertNotIn("crypto", proc.stdout.lower())


class CoverageBrainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-cov-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init")
        run(self.home, "add", "person", "--name", "Alex")

    def test_export_and_build_accept_coverage(self) -> None:
        work = self.home / "work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "coverage.json").write_text(
            json.dumps({"sources": ["email", "calendar"], "through": "2026-10-24"}),
            encoding="utf-8",
        )
        exp = run(self.home, "export-brain", "--generated", "2026-10-24")
        obj = json.loads(exp.stdout)
        self.assertIn("email", obj["coverage"])
        self.assertIn("2026-10-24", obj["coverage"])
        out = self.tmp / "brain.canvas.tsx"
        run(
            self.home,
            "build-brain",
            "-o",
            str(out),
            "--template",
            str(BRAIN),
            "--coverage",
            "scanned chat; through 2026-10-20",
            "--generated",
            "2026-10-24",
        )
        text = out.read_text(encoding="utf-8")
        self.assertIn("scanned chat", text)
        self.assertIn("Last updated", BRAIN.read_text(encoding="utf-8"))


class ForgetUnknownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-forget-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init")

    def test_forget_unknown_exits_nonzero_no_log(self) -> None:
        before = read_csv(self.home / "ledger" / "claudia_log.csv")
        proc = run(self.home, "forget", "--id", "P-9999", "--yes", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("nothing matches", proc.stderr.lower())
        after = read_csv(self.home / "ledger" / "claudia_log.csv")
        self.assertEqual(len(before), len(after))
        self.assertFalse(any(r["action"] == "forget" for r in after))


class WorkPathAndFirstRunTests(unittest.TestCase):
    def test_for_bots_uses_work_not_tmp_claudia(self) -> None:
        text = (ROOT / "docs" / "FOR-BOTS.md").read_text(encoding="utf-8")
        self.assertIn("$CLAUDIA_HOME/work", text)
        self.assertIn("mktemp -d", text)
        self.assertNotIn("/tmp/claudia-", text)
        self.assertIn("VERSION", text)

    def test_first_run_creates_work_and_refreshes_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            env = os.environ.copy()
            env.pop("CLAUDIA_HOME", None)
            env.pop("CLAUDIA_BOT_ID", None)
            proc1 = subprocess.run(
                ["bash", str(FIRST_RUN), "--home", str(home), "--bot-id", "fr-bot"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc1.returncode, 0, proc1.stdout + proc1.stderr)
            self.assertTrue((home / "work").is_dir())
            self.assertIn("tools refreshed", proc1.stdout)
            proc2 = subprocess.run(
                ["bash", str(FIRST_RUN), "--home", str(home), "--bot-id", "fr-bot"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
            blob = proc2.stdout + proc2.stderr
            self.assertNotIn("same file", blob.lower())
            self.assertIn("tools refreshed", proc2.stdout)


class WelcomeTourContentTests(unittest.TestCase):
    def test_welcome_covers_moments_no_crypto(self) -> None:
        text = (ROOT / "templates" / "welcome_tour.txt").read_text(encoding="utf-8")
        lower = text.lower()
        self.assertIn("promise", lower)
        self.assertIn("morning brief", lower)
        self.assertIn("meditate", lower)
        self.assertIn("weekly review", lower)
        self.assertIn("brain", lower)
        self.assertIn("yes", lower)
        self.assertNotIn("crypto", lower)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)


class VersionBumpTests(unittest.TestCase):
    def test_version_is_exactly_1_1_1(self) -> None:
        raw = (ROOT / "VERSION").read_text(encoding="utf-8")
        self.assertEqual(raw.strip(), "1.1.1")
        # Exactly one content line (optional trailing newline only).
        self.assertEqual([ln for ln in raw.splitlines() if ln.strip()], ["1.1.1"])


if __name__ == "__main__":
    unittest.main()
