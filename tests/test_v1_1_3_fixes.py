#!/usr/bin/env python3
# Made with Grok Bot.
"""Tests for Claudia hosted tools 1.1.3 fixes."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "ledger"))
sys.path.insert(0, str(ROOT / "scan"))

from _cli import run_ledger as _run_ledger  # noqa: E402

LEDGER = ROOT / "ledger" / "claudia_ledger.py"
SCAN = ROOT / "scan" / "claudia_scan.py"
BRAIN = ROOT / "brain" / "claudia-brain.canvas.tsx"
UPDATE = ROOT / "update" / "claudia_update.py"
BOT = "v113-bot"


def run(home: Path, *args: str, approved: str | None = "yes", check: bool = True, env=None):
    return _run_ledger(
        home, *args, bot_id=BOT, approved=approved, check=check, env=env
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class SafeCopyMountTests(unittest.TestCase):
    """Simulate a mount that rejects metadata (utime/chmod)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-safe-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        sys.path.insert(0, str(ROOT / "ledger"))
        sys.path.insert(0, str(ROOT / "update"))
        import claudia_ledger as L
        import claudia_update as U

        self.L = L
        self.U = U

        def bad_copystat(*a, **k):
            raise PermissionError("mount rejects metadata")

        self.patcher = mock.patch.object(L.shutil, "copystat", side_effect=bad_copystat)
        self.patcher_u = mock.patch.object(U.shutil, "copystat", side_effect=bad_copystat)
        self.patcher.start()
        self.patcher_u.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.patcher_u.stop)

    def test_init_backup_restore_update_on_metadata_rejecting_mount(self) -> None:
        L = self.L
        home = self.home
        home.mkdir(parents=True)
        ledger = home / "ledger"
        rc = L.main(["--home", str(home), "--bot-id", BOT, "init"])
        self.assertEqual(rc, 0)
        self.assertTrue((ledger / "owner.json").is_file())
        rc = L.main(
            [
                "--home",
                str(home),
                "--bot-id",
                BOT,
                "--approved",
                "yes",
                "add",
                "person",
                "--name",
                "Alex Example",
            ]
        )
        self.assertEqual(rc, 0)
        stamps = [p for p in (ledger / "backups").iterdir() if p.is_dir()]
        self.assertTrue(stamps)
        stamp = sorted(p.name for p in stamps)[0]
        rc = L.main(
            [
                "--home",
                str(home),
                "--bot-id",
                BOT,
                "--approved",
                "yes",
                "restore",
                stamp,
            ]
        )
        self.assertEqual(rc, 0)
        src = self.tmp / "src.txt"
        dest = self.tmp / "dest.txt"
        src.write_text("hello\n", encoding="utf-8")
        self.U.atomic_replace(src, dest)
        self.assertEqual(dest.read_text(encoding="utf-8"), "hello\n")


class DoctorCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-doc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_doctor_and_cleanup_refusal(self) -> None:
        home = self.tmp / "half"
        tools = home / "tools" / "ledger"
        tools.mkdir(parents=True)
        (tools / "claudia_ledger.py").write_text("# Made with Grok Bot.\n", encoding="utf-8")
        (home / "ledger").mkdir()
        # half-built: tools, no owner.json
        proc = run(home, "doctor", approved=None)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("half-built: yes", proc.stdout)
        # cleanup empty half-built
        proc = run(home, "cleanup-half-built", str(home), approved=None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(home.exists())

        # refusal when real rows exist
        home2 = self.tmp / "full"
        run(home2, "init", approved=None)
        run(home2, "add", "person", "--name", "Alex Example")
        # Make it look half-built by removing bot_id? Spec: half-built with rows refuses.
        # Force tools present + try cleanup on a folder that has rows.
        tools2 = home2 / "tools" / "ledger"
        tools2.mkdir(parents=True, exist_ok=True)
        (tools2 / "claudia_ledger.py").write_text("# Made with Grok Bot.\n", encoding="utf-8")
        proc = run(home2, "cleanup-half-built", str(home2), approved=None, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refusing", proc.stderr.lower())
        self.assertTrue(home2.exists())


class BrainCoverageTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-brain-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init", approved=None)

    def test_build_brain_coverage_keys_match_ledger_type(self) -> None:
        out = self.tmp / "brain.canvas.tsx"
        proc = run(
            self.home,
            "build-brain",
            "--template",
            str(BRAIN),
            "-o",
            str(out),
            "--coverage",
            "x",
            "--generated",
            "2026-10-24",
            approved=None,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = out.read_text(encoding="utf-8")
        # Extract LEDGER object keys (top-level).
        start = text.index("const LEDGER: Ledger = {")
        end = text.index("};", start)
        block = text[start:end]
        keys = []
        for line in block.splitlines():
            line = line.strip()
            if line.startswith('"') and ":" in line:
                keys.append(line.split('"', 2)[1])
        # Parse type Ledger fields.
        type_line = [
            ln
            for ln in BRAIN.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith("type Ledger =")
        ][0]
        for key in keys:
            self.assertIn(key, type_line, f"key {key} missing from type Ledger")
        self.assertIn("coverage", keys)
        self.assertIn("LEDGER.coverage", text)
        self.assertNotIn("Ledger & { coverage", text)
        # Optional tsc
        tsc = shutil.which("tsc")
        if tsc:
            # Skip full project check; file uses React types not installed here.
            print("tsc available but skipped for canvas (no React types in CI)")
        else:
            print("tsc not available; skipped typecheck")


class ProposeLinksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-links-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init", approved=None)
        run(self.home, "add", "org", "--name", "Example Labs", "--domains", "example.com")
        run(
            self.home,
            "add",
            "person",
            "--name",
            "Sam Example",
            "--email",
            "sam@example.com",
            "--org-id",
            "O-0001",
        )
        run(
            self.home,
            "add",
            "person",
            "--name",
            "Lee Example",
            "--email",
            "lee@example.com",
            "--org-id",
            "O-0001",
            "--force-new",
        )
        run(
            self.home,
            "add",
            "person",
            "--name",
            "Jordan Example",
            "--email",
            "jordan@example.com",
            "--force-new",
        )
        # Shared thread for 3 people
        for pid, summary in (
            ("P-0001", "Kickoff for Example Launch"),
            ("P-0002", "Kickoff for Example Launch follow-up"),
            ("P-0003", "Kickoff for Example Launch notes"),
        ):
            run(
                self.home,
                "add",
                "interaction",
                "--person-id",
                pid,
                "--date",
                "2026-10-20",
                "--summary",
                summary,
                "--source-link",
                "thread:example-launch",
            )
        # Intro
        run(
            self.home,
            "add",
            "interaction",
            "--person-id",
            "P-0001",
            "--date",
            "2026-10-21",
            "--summary",
            "Introducing you to Lee Example; connecting you both",
            "--source-link",
            "mail:intro-1",
        )
        # Repeated subject words across 3+ interactions with 2+ people
        for i, pid in enumerate(("P-0001", "P-0002", "P-0003")):
            run(
                self.home,
                "add",
                "interaction",
                "--person-id",
                pid,
                "--date",
                f"2026-10-2{i}",
                "--summary",
                "Harborboard redesign sync",
                "--source-link",
                f"meet:harbor-{i}",
            )

    def test_propose_then_apply_pick(self) -> None:
        links_before = read_csv(self.home / "ledger" / "links.csv")
        proc = run(self.home, "propose-links", approved=None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Add these", proc.stdout)
        self.assertIn("(yes / pick / no)", proc.stdout)
        path = self.home / "work" / "link-candidates.json"
        self.assertTrue(path.is_file())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(payload["candidates"]), 1)
        for c in payload["candidates"]:
            self.assertTrue(c.get("evidence"), c)
            self.assertEqual(c.get("basis"), "inferred")
        # Nothing written to links yet
        links_mid = read_csv(self.home / "ledger" / "links.csv")
        self.assertEqual(len(links_mid), len(links_before))
        pick = payload["candidates"][0]["id"]
        proc = run(
            self.home,
            "apply-links",
            "--file",
            str(path),
            "--pick",
            pick,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        links_after = read_csv(self.home / "ledger" / "links.csv")
        self.assertEqual(len(links_after), len(links_before) + 1)
        self.assertEqual(links_after[-1]["basis"], "owner-confirmed")
        log = read_csv(self.home / "ledger" / "claudia_log.csv")
        apply_rows = [r for r in log if r["action"] == "apply_links"]
        self.assertTrue(apply_rows)
        self.assertEqual(apply_rows[0]["approved_by_owner"], "yes")


class ScannerEverydayTests(unittest.TestCase):
    CASES = [
        # positives with dates
        ("Let me send that over by Friday.", True, False),
        ("Let me share the deck by 2026-10-24.", True, False),
        ("Let me set some time up tomorrow.", True, False),
        ("Let me check the numbers by Monday.", True, False),
        ("Let me look into this by next week.", True, False),
        ("I can send the draft by Friday.", True, False),
        ("I can share the link by tomorrow.", True, False),
        ("I'll get back to you by Friday.", True, False),
        ("I'll loop in Sam by Monday.", True, False),
        ("I'll intro you to Sam by Friday.", True, False),
        ("I will be attending the launch on 2026-10-30.", True, False),
        ("I'll be there tomorrow.", True, False),
        ("Count me in for Friday.", True, False),
        ("I'm going to send the kit by Friday.", True, False),
        ("I am going to share the brief by tomorrow.", True, False),
        # positives without dates (needs_due)
        ("Let me send that over.", True, True),
        ("Let me set some time up.", True, True),
        ("I'll get back to you.", True, True),
        ("I'll loop in Sam.", True, True),
        ("I'm going to send the kit.", True, True),
        ("I will be attending.", True, True),
        ("Count me in.", True, True),
        # false positives
        ("Let me know what you think.", False, False),
        ("Let me think about it.", False, False),
        ("Let me be clear about the timeline.", False, False),
        ("Maybe I'll send it Friday.", False, False),
    ]

    def _scan_sentence(self, sentence: str) -> list[dict]:
        item = {
            "id": "t1",
            "source": "email",
            "date": "2026-10-20",
            "from": "Sam Chen",
            "to": "Alex Example",
            "subject": "Quick note",
            "text": sentence,
            "link": "mail:t1",
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "in.jsonl"
            path.write_text(json.dumps(item) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCAN),
                    "--input",
                    str(path),
                    "--owner-name",
                    "Sam Chen",
                    "--owner-email",
                    "sam.chen@example.com",
                    "--min-confidence",
                    "0.5",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

    def test_table_driven_everyday_wording(self) -> None:
        self.assertGreaterEqual(len(self.CASES), 25)
        misses = []
        fps = []
        for sentence, should_hit, needs_due in self.CASES:
            cands = self._scan_sentence(sentence)
            hit = len(cands) > 0
            if should_hit and not hit:
                misses.append(sentence)
            if not should_hit and hit:
                fps.append((sentence, cands))
            if should_hit and hit and needs_due:
                self.assertTrue(cands[0].get("needs_due"), sentence)
                self.assertFalse(cands[0].get("due"), sentence)
        self.assertEqual(misses, [], f"recall misses: {misses}")
        self.assertEqual(fps, [], f"false positives: {fps}")

    def test_explain_flag(self) -> None:
        item = {
            "id": "e1",
            "source": "email",
            "date": "2026-10-20",
            "from": "Sam Chen",
            "to": "Alex Example",
            "subject": "",
            "text": "Let me know. Let me send the file.",
            "link": "x",
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "in.jsonl"
            path.write_text(json.dumps(item) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCAN),
                    "--input",
                    str(path),
                    "--owner-name",
                    "Sam Chen",
                    "--explain",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("EXPLAIN", proc.stderr)
            self.assertIn("let me know", proc.stderr.lower())


class ApprovedByOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-appr-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init", approved=None)

    def test_write_without_approved_fails(self) -> None:
        proc = run(
            self.home,
            "add",
            "person",
            "--name",
            "Alex Example",
            approved=None,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--approved", proc.stderr)

    def test_health_logs_no_approval_needed(self) -> None:
        run(self.home, "add", "person", "--name", "Alex Example")
        proc = run(
            self.home,
            "health",
            "snapshot",
            approved="no-approval-needed",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        log = read_csv(self.home / "ledger" / "claudia_log.csv")
        rows = [r for r in log if r["action"] == "health_snapshot"]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["approved_by_owner"], "no-approval-needed")

    def test_merge_logs_approved_yes(self) -> None:
        run(self.home, "add", "person", "--name", "Alex Example")
        run(self.home, "add", "person", "--name", "Jordan Example", "--force-new")
        run(
            self.home,
            "merge",
            "--kept",
            "P-0001",
            "--merged",
            "P-0002",
            "--evidence",
            "same person example",
            approved="yes",
        )
        merges = read_csv(self.home / "ledger" / "merges.csv")
        self.assertEqual(merges[0]["approved_by_owner"], "yes")
        log = read_csv(self.home / "ledger" / "claudia_log.csv")
        merge_logs = [r for r in log if r["action"] == "merge"]
        self.assertEqual(merge_logs[0]["approved_by_owner"], "yes")


class BackupRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-ret-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_named_daily_weekly_and_pre_bulk(self) -> None:
        import claudia_ledger as L

        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        L.set_clock_override(base)
        self.addCleanup(L.set_clock_override, None)
        rc = L.main(["--home", str(self.home), "--bot-id", BOT, "init"])
        self.assertEqual(rc, 0)
        # first write
        L.main(
            [
                "--home",
                str(self.home),
                "--bot-id",
                BOT,
                "--approved",
                "yes",
                "add",
                "person",
                "--name",
                "Alex Example",
            ]
        )
        first = sorted((self.home / "ledger" / "backups").iterdir())[0].name
        # named snapshot
        L.main(
            [
                "--home",
                str(self.home),
                "--bot-id",
                BOT,
                "snapshot",
                "--label",
                "pre-import",
            ]
        )
        # 120 writes across fake dates
        for i in range(120):
            L.set_clock_override(base + timedelta(hours=i * 5))
            L.main(
                [
                    "--home",
                    str(self.home),
                    "--bot-id",
                    BOT,
                    "--approved",
                    "yes",
                    "add",
                    "person",
                    "--name",
                    f"Person {i}",
                    "--force-new",
                ]
            )
        # apply-links style pre-bulk
        L.set_clock_override(base + timedelta(days=40))
        # Build enough people interactions then propose/apply many would be heavy;
        # call maybe_pre_bulk directly via apply-links path: create candidates file.
        work = self.home / "work"
        work.mkdir(exist_ok=True)
        payload = {
            "candidates": [
                {
                    "id": f"LC-{n:04d}",
                    "from_id": "P-0001",
                    "to_id": "ME",
                    "type": "works_with",
                    "basis": "inferred",
                    "evidence": [f"e{n}"],
                    "reason": "test",
                }
                for n in range(1, 12)
            ],
            "projects": [],
            "question": "test",
        }
        # Fix to_id to a real person for half of them
        # Use P-0001 with itself skipped; create second person already exists.
        for c in payload["candidates"]:
            c["to_id"] = "P-0002" if (self.home / "ledger" / "people.csv").exists() else "P-0001"
        # Ensure P-0002 exists
        people = read_csv(self.home / "ledger" / "people.csv")
        if len(people) < 2:
            L.main(
                [
                    "--home",
                    str(self.home),
                    "--bot-id",
                    BOT,
                    "--approved",
                    "yes",
                    "add",
                    "person",
                    "--name",
                    "Lee Example",
                    "--force-new",
                ]
            )
            people = read_csv(self.home / "ledger" / "people.csv")
        p2 = people[1]["person_id"]
        for c in payload["candidates"]:
            c["from_id"] = people[0]["person_id"]
            c["to_id"] = p2
        cand_path = work / "link-candidates.json"
        cand_path.write_text(json.dumps(payload), encoding="utf-8")
        L.main(
            [
                "--home",
                str(self.home),
                "--bot-id",
                BOT,
                "--approved",
                "yes",
                "apply-links",
                "--file",
                str(cand_path),
                "--pick",
                "all",
            ]
        )
        backups = list((self.home / "ledger" / "backups").iterdir())
        names = [p.name for p in backups if p.is_dir()]
        self.assertTrue(any(first == n or n.startswith(first) for n in names) or first in names)
        self.assertTrue(any("pre-import" in n for n in names))
        self.assertTrue(any("pre-bulk" in n for n in names))
        # backups listing includes kept reasons
        proc = subprocess.run(
            [sys.executable, str(LEDGER), "--home", str(self.home), "--bot-id", BOT, "backups"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("kept:", proc.stdout)
        # restore named snapshot
        named = [n for n in names if "pre-import" in n][0]
        rc = L.main(
            [
                "--home",
                str(self.home),
                "--bot-id",
                BOT,
                "--approved",
                "yes",
                "restore",
                named,
            ]
        )
        self.assertEqual(rc, 0)


class DatelessPromisesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claudia-date-"))
        self.home = self.tmp / "home"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.home, "init", approved=None)
        run(self.home, "add", "person", "--name", "Alex Example")

    def test_default_off_requires_due(self) -> None:
        proc = run(
            self.home,
            "add",
            "commitment",
            "--direction",
            "I owe",
            "--person-id",
            "P-0001",
            "--what",
            "Intro Sam to Lee",
            "--source-link",
            "chat:1",
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("due", proc.stderr.lower())

    def test_dateless_on_allows_hint_and_check_reminds(self) -> None:
        owner = json.loads((self.home / "ledger" / "owner.json").read_text())
        owner["dateless_promises"] = True
        (self.home / "ledger" / "owner.json").write_text(
            json.dumps(owner, indent=2) + "\n", encoding="utf-8"
        )
        proc = run(
            self.home,
            "add",
            "commitment",
            "--direction",
            "I owe",
            "--person-id",
            "P-0001",
            "--what",
            "Intro Sam to Lee",
            "--due-hint",
            "no date given",
            "--source-link",
            "chat:1",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = read_csv(self.home / "ledger" / "commitments.csv")
        self.assertEqual(rows[0]["due"], "")
        self.assertEqual(rows[0]["due_hint"], "no date given")
        chk = run(self.home, "check", approved=None)
        self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
        self.assertIn("has no date; ask once a week", chk.stdout)


if __name__ == "__main__":
    unittest.main()
