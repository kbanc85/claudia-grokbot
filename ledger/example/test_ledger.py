#!/usr/bin/env python3
# Made with Grok Bot.
"""Stdlib tests for claudia_ledger.py (init, add, export, build-brain, check)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ledger" / "claudia_ledger.py"
TEMPLATES = ROOT / "ledger" / "templates"
EXAMPLE = ROOT / "ledger" / "example"
BRAIN = ROOT / "brain" / "claudia-brain.canvas.tsx"
UPLOADED_TEMPLATE = Path(
    "/home/ubuntu/.cursor/projects/workspace/uploads/template.canvas_6cc0.tsx"
)


BOT = "example-test-bot"


def run_ledger(home: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
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


class ClaudiaLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-test-"))
        self.home = self.tmp / "home"
        self.ledger = self.home / "ledger"
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_init_creates_headers(self) -> None:
        run_ledger(self.home, "init")
        for name in (
            "people.csv",
            "organizations.csv",
            "projects.csv",
            "commitments.csv",
            "interactions.csv",
            "links.csv",
            "claudia_log.csv",
            "merges.csv",
            "health_history.csv",
        ):
            path = self.ledger / name
            self.assertTrue(path.exists(), name)
            header = path.read_text(encoding="utf-8").splitlines()[0]
            expected = (TEMPLATES / name).read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(header, expected, name)
        self.assertTrue((self.ledger / "notes").is_dir())
        self.assertTrue((self.ledger / "owner.json").exists())

    def test_add_export_build_check(self) -> None:
        run_ledger(self.home, "init")
        run_ledger(self.home, "add", "org", "--name", "Northwind Labs")
        run_ledger(
            self.home,
            "add",
            "project",
            "--name",
            "Launch kit",
            "--org-id",
            "O-0001",
        )
        run_ledger(
            self.home,
            "add",
            "person",
            "--name",
            "Alex Rivera",
            "--org-id",
            "O-0001",
            "--role",
            "Partner",
            "--network",
            "work",
            "--relationship",
            "collaborator",
            "--city",
            "Austin",
            "--how-met",
            "intro",
            "--cadence-days",
            "14",
            "--last-contact",
            "2026-10-10",
            "--email",
            "alex@northwind.example",
        )
        run_ledger(
            self.home,
            "add",
            "commitment",
            "--direction",
            "I owe",
            "--person-id",
            "P-0001",
            "--what",
            "Send launch draft",
            "--due",
            "2026-10-25",
            "--source-link",
            "example",
        )
        run_ledger(
            self.home,
            "add",
            "interaction",
            "--person-id",
            "P-0001",
            "--date",
            "2026-10-10",
            "--channel",
            "email",
            "--actor",
            "owner",
            "--summary",
            "Synced on launch kit timeline",
            "--source-link",
            "example",
        )
        run_ledger(
            self.home,
            "add",
            "link",
            "--from-id",
            "P-0001",
            "--to-id",
            "J-0001",
            "--type",
            "owns",
            "--basis",
            "stated",
            "--source",
            "example",
        )

        exp = run_ledger(
            self.home,
            "export-brain",
            "--example",
            "--generated",
            "2026-10-24",
            "--owner",
            "You",
        )
        obj = json.loads(exp.stdout)
        self.assertTrue(obj["example"])
        self.assertEqual(obj["generated"], "2026-10-24")
        self.assertEqual(obj["owner"]["name"], "You")
        self.assertEqual(obj["people"][0]["id"], "P-0001")
        self.assertEqual(obj["projects"][0]["id"], "J-0001")
        self.assertEqual(obj["interactions"][0]["id"], "I-000001")
        self.assertEqual(obj["people"][0]["days_since"], 14)
        self.assertEqual(obj["people"][0]["health"], "active")
        # sensitive fields stay out of the Brain export
        people_blob = json.dumps(obj["people"])
        self.assertNotIn("alex@northwind.example", people_blob)
        self.assertNotIn("notes_pointer", people_blob)
        for person in obj["people"]:
            self.assertNotIn("email", person)
            self.assertNotIn("phone", person)

        template = BRAIN if BRAIN.exists() else UPLOADED_TEMPLATE
        self.assertTrue(template.exists(), "brain template missing")
        out = self.tmp / "brain.canvas.tsx"
        run_ledger(
            self.home,
            "build-brain",
            "-o",
            str(out),
            "--template",
            str(template),
            "--example",
            "--generated",
            "2026-10-24",
        )
        text = out.read_text(encoding="utf-8")
        self.assertIn("/* LEDGER-DATA-START */", text)
        self.assertIn("/* LEDGER-DATA-END */", text)
        self.assertIn("Alex Rivera", text)
        self.assertNotIn("__LEDGER_JSON__", text)
        # UI outside the marker block is preserved
        before = template.read_text(encoding="utf-8").split("/* LEDGER-DATA-START */", 1)[0]
        self.assertTrue(text.startswith(before))

        chk = run_ledger(self.home, "check")
        self.assertEqual(chk.returncode, 0)
        self.assertIn("CHECK OK", chk.stdout)

    def test_check_catches_bad_header_and_broken_ref(self) -> None:
        run_ledger(self.home, "init")
        # break people header
        bad = self.ledger / "people.csv"
        bad.write_text("person_id,name\nP-0001,Alex\n", encoding="utf-8")
        proc = run_ledger(self.home, "check", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("headers are wrong", proc.stdout)

        # restore header, add dangling commitment
        shutil.copy2(TEMPLATES / "people.csv", bad)
        (self.ledger / "commitments.csv").write_text(
            "commit_id,direction,person_id,what,due,status,source_link,created,closed\n"
            "C-0001,I owe,P-9999,Missing person,2026-10-25,open,example,2026-10-24,\n",
            encoding="utf-8",
        )
        proc = run_ledger(self.home, "check", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing person", proc.stdout)

    def test_build_brain_fails_without_markers(self) -> None:
        run_ledger(self.home, "init")
        broken = self.tmp / "broken.canvas.tsx"
        broken.write_text("// no markers here\n", encoding="utf-8")
        out = self.tmp / "out.canvas.tsx"
        proc = run_ledger(
            self.home,
            "build-brain",
            "-o",
            str(out),
            "--template",
            str(broken),
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("markers", proc.stderr.lower())

    def test_example_ledger_check_and_export(self) -> None:
        # Copy the shipped example so we do not mutate the repo copy.
        self.home.mkdir(parents=True, exist_ok=True)
        shutil.copytree(EXAMPLE, self.ledger, dirs_exist_ok=True)
        # drop the test module itself if copied
        for junk in self.ledger.glob("test_*.py"):
            junk.unlink()
        for junk in self.ledger.glob("README.md"):
            junk.unlink()
        # Align example owner.json with this test bot id.
        owner = self.ledger / "owner.json"
        data = json.loads(owner.read_text(encoding="utf-8"))
        data["bot_id"] = BOT
        owner.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        proc = run_ledger(self.home, "check")
        self.assertEqual(proc.returncode, 0)
        exp = run_ledger(
            self.home,
            "export-brain",
            "--example",
            "--generated",
            "2026-10-24",
        )
        obj = json.loads(exp.stdout)
        self.assertEqual(len(obj["people"]), 2)
        self.assertEqual(obj["projects"][0]["id"], "J-0001")
        self.assertTrue(BRAIN.exists())
        brain = BRAIN.read_text(encoding="utf-8")
        self.assertIn('"id": "J-0001"', brain)
        self.assertIn("Alex Rivera", brain)


if __name__ == "__main__":
    unittest.main()
