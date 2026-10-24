#!/usr/bin/env python3
# Made with Grok Bot.
"""Claudia Ledger: CSV ledger + Brain Page export for Grok Bot copies.

Python 3 standard library only. The bot is the operator of this tool.

Download (from the hosted repo) into $CLAUDIA_HOME/tools/, then:

  export CLAUDIA_HOME=/workspace/claudia-<bot-id>   # or /cursor/stores/<store>/claudia
  export CLAUDIA_BOT_ID=<bot-id>
  python3 $CLAUDIA_HOME/tools/ledger/claudia_ledger.py --home "$CLAUDIA_HOME" init
  python3 $CLAUDIA_HOME/tools/ledger/claudia_ledger.py --home "$CLAUDIA_HOME" check
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

# ---------------------------------------------------------------------------
# Schema (headers must match ledger/templates/*.csv exactly)
# ---------------------------------------------------------------------------

PEOPLE = "people.csv"
ORGS = "organizations.csv"
PROJECTS = "projects.csv"
COMMITMENTS = "commitments.csv"
INTERACTIONS = "interactions.csv"
LINKS = "links.csv"
LOG = "claudia_log.csv"
MERGES = "merges.csv"
HEALTH = "health_history.csv"

HEADERS: dict[str, list[str]] = {
    PEOPLE: [
        "person_id",
        "name",
        "aliases",
        "email",
        "phone",
        "handle",
        "network",
        "relationship",
        "org_id",
        "role",
        "city",
        "how_met",
        "cadence_days",
        "cadence_source",
        "cadence_proposed",
        "last_contact",
        "health",
        "notes_pointer",
        "created",
        "updated",
    ],
    ORGS: ["org_id", "name", "aliases", "domains"],
    PROJECTS: ["project_id", "name", "aliases", "org_id", "status"],
    COMMITMENTS: [
        "commit_id",
        "direction",
        "person_id",
        "what",
        "due",
        "status",
        "source_link",
        "created",
        "closed",
    ],
    INTERACTIONS: [
        "int_id",
        "date",
        "person_id",
        "channel",
        "actor",
        "summary",
        "source_link",
    ],
    LINKS: ["from_id", "to_id", "type", "basis", "source"],
    LOG: ["log_id", "timestamp", "action", "ref", "detail", "approved_by_owner"],
    MERGES: [
        "merge_id",
        "date",
        "kept_id",
        "merged_id",
        "evidence",
        "repointed_rows",
        "approved_by_owner",
    ],
    HEALTH: [
        "date",
        "person_id",
        "health",
        "last_contact",
        "days_since",
        "cadence_days",
        "reason",
        "source",
    ],
}

ID_SPECS: dict[str, tuple[str, str, int]] = {
    # file -> (column, prefix, zero-pad width)
    PEOPLE: ("person_id", "P-", 4),
    ORGS: ("org_id", "O-", 4),
    PROJECTS: ("project_id", "J-", 4),
    COMMITMENTS: ("commit_id", "C-", 4),
    INTERACTIONS: ("int_id", "I-", 6),
    LOG: ("log_id", "L-", 6),
    MERGES: ("merge_id", "M-", 4),
}

# owner.json id_counters keys (never reuse an issued id)
ID_COUNTER_KEYS: dict[str, str] = {
    PEOPLE: "P",
    ORGS: "O",
    PROJECTS: "J",
    COMMITMENTS: "C",
    INTERACTIONS: "I",
    LOG: "L",
    MERGES: "M",
}

# Commands that change ledger files (require --bot-id / CLAUDIA_BOT_ID).
# export-brain / build-brain are read-only against the ledger CSVs.
WRITE_COMMANDS = frozenset(
    {
        "init",
        "add",
        "update",
        "close",
        "merge",
        "forget",
        "restore",
        "health",
    }
)

DIRECTIONS = ("I owe", "owed to me", "Claudia owes", "bot owes")
COMMIT_STATUSES = ("open", "done", "dropped", "renegotiated")
ACTORS = ("owner", "them", "claudia", "both")
BASIS = ("stated", "extracted", "inferred")
CADENCE_SOURCES = ("default", "owner", "learned")
HEALTH_VALUES = ("active", "cooling", "inactive")
PROJECT_STATUSES = ("active", "paused", "done", "dropped")

DEFAULT_CADENCE = 30
DEFAULT_BRAIN_URL = (
    "https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/"
    "brain/claudia-brain.canvas.tsx"
)
LEDGER_START = "/* LEDGER-DATA-START */"
LEDGER_END = "/* LEDGER-DATA-END */"
# Default retention; override with CLAUDIA_BACKUP_KEEP or --backup-keep.
BACKUP_KEEP = 30

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# Time / paths
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def today_iso() -> str:
    return utc_now().date().isoformat()


def ts_iso() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def parse_date(s: str) -> date | None:
    if not s or not ISO_DATE.match(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def day_diff(later: str, earlier: str) -> int | None:
    a = parse_date(later)
    b = parse_date(earlier)
    if a is None or b is None:
        return None
    return (a - b).days


def compute_health(days_since: int | None, cadence_days: int) -> str:
    if days_since is None:
        return "inactive"
    if days_since <= cadence_days:
        return "active"
    if days_since <= cadence_days * 2:
        return "cooling"
    return "inactive"


# ---------------------------------------------------------------------------
# CSV IO
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """User-facing ledger problem."""


def backup_keep_count(override: int | None = None) -> int:
    """How many timestamped snapshots to retain under backups/."""
    if override is not None:
        if override < 1:
            raise LedgerError("--backup-keep must be >= 1")
        return override
    raw = os.environ.get("CLAUDIA_BACKUP_KEEP", "").strip()
    if raw:
        if not raw.isdigit() or int(raw) < 1:
            raise LedgerError("CLAUDIA_BACKUP_KEEP must be an integer >= 1")
        return int(raw)
    return BACKUP_KEEP


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


HOME_HELP = (
    "Set CLAUDIA_HOME to this bot's own folder, or pass --home. "
    "Example: export CLAUDIA_HOME=/workspace/claudia-<bot-id> "
    "(or /cursor/stores/<its-store>/claudia if the bot has a persistent store). "
    "There is no shared default, so two Claudia copies on one machine do not mix ledgers. "
    "Save that path in memory and reuse it every routine."
)

BOT_ID_HELP = (
    "Set CLAUDIA_BOT_ID to this bot's id, or pass --bot-id. "
    "first_run records it in owner.json so another Claudia copy cannot write this ledger by mistake."
)


def resolve_home(cli_home: str | None) -> Path:
    """Require --home or CLAUDIA_HOME. Flag wins. No shared default."""
    raw = (cli_home or "").strip() or os.environ.get("CLAUDIA_HOME", "").strip()
    if not raw:
        die(HOME_HELP)
    return Path(raw).expanduser().resolve()


def resolve_bot_id(cli_bot_id: str | None, *, required: bool) -> str:
    raw = (cli_bot_id or "").strip() or os.environ.get("CLAUDIA_BOT_ID", "").strip()
    if not raw:
        if required:
            die(BOT_ID_HELP)
        return ""
    return raw


def home_ledger_dir(home: Path) -> Path:
    return home / "ledger"


def home_tools_dir(home: Path) -> Path:
    return home / "tools"


def home_work_dir(home: Path) -> Path:
    return home / "work"


def load_owner_json(ledger: Path) -> dict[str, Any]:
    path = ledger / "owner.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_owner_json(ledger: Path, data: Mapping[str, Any]) -> None:
    path = ledger / "owner.json"
    path.write_text(json.dumps(dict(data), indent=2) + "\n", encoding="utf-8")


def bot_id_mismatch_message(stored: str, passed: str, ledger: Path) -> str:
    return (
        f"bot_id mismatch: owner.json has bot_id={stored!r} but this run passed "
        f"bot_id={passed!r} (ledger {ledger}). "
        "Stop. This ledger belongs to another Claudia copy. "
        "Use that bot's CLAUDIA_HOME, or pick a different home for this bot."
    )


def verify_bot_id(ledger: Path, bot_id: str, *, hard: bool) -> str | None:
    """Return a problem string if mismatched. hard=True dies immediately."""
    if not bot_id:
        return None
    stored = str(load_owner_json(ledger).get("bot_id") or "").strip()
    if not stored:
        return None
    if stored == bot_id:
        return None
    msg = bot_id_mismatch_message(stored, bot_id, ledger)
    if hard:
        die(msg)
    return msg


def ensure_ledger_dir(path: Path) -> Path:
    if not path.exists():
        die(f"ledger folder not found: {path}")
    if not path.is_dir():
        die(f"ledger path is not a folder: {path}")
    return path


def notes_dir(ledger: Path) -> Path:
    return ledger / "notes"


def backups_dir(ledger: Path) -> Path:
    return ledger / "backups"


def read_rows(ledger: Path, filename: str) -> list[dict[str, str]]:
    path = ledger / filename
    if not path.exists():
        raise LedgerError(f"missing file {filename}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        expected = HEADERS[filename]
        if reader.fieldnames is None:
            raise LedgerError(f"{filename} has no header row")
        got = list(reader.fieldnames)
        if got != expected:
            raise LedgerError(
                f"{filename} headers are wrong. "
                f"Expected: {','.join(expected)}. Got: {','.join(got)}"
            )
        rows: list[dict[str, str]] = []
        for raw in reader:
            rows.append({k: (raw.get(k) or "").strip() for k in expected})
        return rows


def write_rows(ledger: Path, filename: str, rows: Sequence[Mapping[str, str]]) -> None:
    path = ledger / filename
    expected = HEADERS[filename]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=expected, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in expected})


def backup_ledger(ledger: Path, *, keep: int | None = None) -> Path:
    """Snapshot CSVs (+ notes/, owner.json) before a write. Keeps the last N."""
    src_files = [ledger / name for name in HEADERS]
    owner = ledger / "owner.json"
    existing = [p for p in src_files if p.exists()]
    if owner.exists():
        existing.append(owner)
    if not existing:
        return ledger
    stamp = ts_stamp()
    dest = backups_dir(ledger) / stamp
    # Same-second writes get a numeric suffix so we never collide.
    n = 1
    while dest.exists():
        dest = backups_dir(ledger) / f"{stamp}-{n}"
        n += 1
    dest.mkdir(parents=True, exist_ok=False)
    for p in existing:
        shutil.copy2(p, dest / p.name)
    notes = notes_dir(ledger)
    if notes.is_dir():
        shutil.copytree(notes, dest / "notes")
    # prune old backups
    root = backups_dir(ledger)
    stamps = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)
    retain = backup_keep_count(keep)
    while len(stamps) > retain:
        old = stamps.pop(0)
        shutil.rmtree(old, ignore_errors=True)
    return dest


def list_backup_stamps(ledger: Path) -> list[Path]:
    root = backups_dir(ledger)
    if not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)


def cmd_backups(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    stamps = list_backup_stamps(ledger)
    if not stamps:
        print("no backups yet")
        return 0
    print(f"backups in {backups_dir(ledger)} (newest last):")
    for p in stamps:
        files = sorted(x.name for x in p.iterdir())
        print(f"  {p.name}  ({len(files)} entries)")
    print(f"count: {len(stamps)} (keep {backup_keep_count()})")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a snapshot. Snapshots the current ledger first."""
    ledger = ensure_ledger_dir(Path(args.ledger))
    stamp = args.snapshot.strip()
    if not stamp:
        die("pass a snapshot name (see: backups)")
    src = backups_dir(ledger) / stamp
    if not src.is_dir():
        die(f"snapshot not found: {stamp}. Run: backups")
    # Safety: snapshot current state before overwriting.
    pre = backup_ledger(ledger)
    print(f"safety snapshot of current state: {pre.name}")
    # Restore CSVs listed in the snapshot; leave unrelated files alone.
    restored = 0
    for name in HEADERS:
        candidate = src / name
        if candidate.is_file():
            shutil.copy2(candidate, ledger / name)
            restored += 1
    owner_src = src / "owner.json"
    if owner_src.is_file():
        shutil.copy2(owner_src, ledger / "owner.json")
        restored += 1
    notes_src = src / "notes"
    if notes_src.is_dir():
        dest_notes = notes_dir(ledger)
        if dest_notes.exists():
            shutil.rmtree(dest_notes)
        shutil.copytree(notes_src, dest_notes)
        restored += 1
    append_log(
        ledger,
        "restore",
        stamp,
        f"restored from backups/{stamp}; pre-restore snapshot {pre.name}",
        approved="yes",
    )
    print(f"restored {stamp} ({restored} paths). Re-run check next.")
    return 0


def _parse_prefixed_num(val: str, prefix: str) -> int | None:
    if not val or not val.startswith(prefix):
        return None
    rest = val[len(prefix) :]
    if rest.isdigit():
        return int(rest)
    return None


def _scan_text_for_prefix(text: str, prefix: str) -> int:
    """Highest numeric suffix for prefix in free text (aliases, log detail, etc.)."""
    if not text or prefix not in text:
        return 0
    highest = 0
    for match in re.finditer(re.escape(prefix) + r"(\d+)", text):
        highest = max(highest, int(match.group(1)))
    return highest


def max_issued_for(ledger: Path, filename: str) -> int:
    """Max id ever issued for this entity type.

    Robust for existing ledgers: scans the live CSV, people aliases (retired
    person ids after merge), merges.csv, claudia_log.csv, and owner.json
    id_counters. IDs are never reused after merge or forget.
    """
    col, prefix, _width = ID_SPECS[filename]
    key = ID_COUNTER_KEYS[filename]
    highest = 0
    owner = load_owner_json(ledger)
    counters = owner.get("id_counters")
    if isinstance(counters, dict):
        raw = counters.get(key)
        try:
            if raw is not None:
                highest = max(highest, int(raw))
        except (TypeError, ValueError):
            pass
    path = ledger / filename
    if path.is_file():
        try:
            for row in read_rows(ledger, filename):
                n = _parse_prefixed_num(row.get(col, ""), prefix)
                if n is not None:
                    highest = max(highest, n)
                for v in row.values():
                    highest = max(highest, _scan_text_for_prefix(v, prefix))
        except LedgerError:
            pass
    # Retired person ids live in aliases after merge; always scan people aliases
    # when allocating a person id.
    if filename == PEOPLE and (ledger / PEOPLE).is_file():
        try:
            for row in read_rows(ledger, PEOPLE):
                highest = max(
                    highest, _scan_text_for_prefix(row.get("aliases", ""), prefix)
                )
        except LedgerError:
            pass
    for extra in (MERGES, LOG):
        if extra == filename:
            continue
        if not (ledger / extra).is_file():
            continue
        try:
            for row in read_rows(ledger, extra):
                for v in row.values():
                    highest = max(highest, _scan_text_for_prefix(v, prefix))
        except LedgerError:
            pass
    return highest


def allocate_id(ledger: Path, filename: str) -> str:
    """Next unused id for filename. Never reuses a past id. Updates id_counters."""
    _col, prefix, width = ID_SPECS[filename]
    key = ID_COUNTER_KEYS[filename]
    nxt = max_issued_for(ledger, filename) + 1
    data = load_owner_json(ledger)
    counters: dict[str, Any]
    raw_counters = data.get("id_counters")
    if isinstance(raw_counters, dict):
        counters = dict(raw_counters)
    else:
        counters = {}
    try:
        prev = int(counters.get(key) or 0)
    except (TypeError, ValueError):
        prev = 0
    counters[key] = max(prev, nxt)
    data["id_counters"] = counters
    write_owner_json(ledger, data)
    return f"{prefix}{nxt:0{width}d}"


def append_log(
    ledger: Path,
    action: str,
    ref: str,
    detail: str,
    approved: str = "yes",
) -> None:
    rows = read_rows(ledger, LOG) if (ledger / LOG).exists() else []
    rows.append(
        {
            "log_id": allocate_id(ledger, LOG),
            "timestamp": ts_iso(),
            "action": action,
            "ref": ref,
            "detail": detail,
            "approved_by_owner": approved,
        }
    )
    write_rows(ledger, LOG, rows)


def append_health_history(
    ledger: Path,
    *,
    person_id: str,
    health: str,
    last_contact: str,
    days_since: str,
    cadence_days: str,
    reason: str,
    source: str = "",
) -> None:
    """Append one health_history.csv row (caller handles backup)."""
    rows = read_rows(ledger, HEALTH) if (ledger / HEALTH).exists() else []
    rows.append(
        {
            "date": today_iso(),
            "person_id": person_id,
            "health": health,
            "last_contact": last_contact,
            "days_since": days_since,
            "cadence_days": cadence_days,
            "reason": reason,
            "source": source,
        }
    )
    write_rows(ledger, HEALTH, rows)


def record_health_change_if_needed(
    ledger: Path,
    person: Mapping[str, str],
    old_health: str,
    *,
    source: str = "",
) -> None:
    """When a write changes a person's health, append reason=changed."""
    new_health = (person.get("health") or "").strip()
    if not new_health or new_health == (old_health or "").strip():
        return
    lc = person.get("last_contact") or ""
    cadence = person.get("cadence_days") or str(DEFAULT_CADENCE)
    ds = ""
    if lc:
        delta = day_diff(today_iso(), lc)
        if delta is not None:
            ds = str(delta)
    append_health_history(
        ledger,
        person_id=person["person_id"],
        health=new_health,
        last_contact=lc,
        days_since=ds,
        cadence_days=str(cadence),
        reason="changed",
        source=source,
    )


# ---------------------------------------------------------------------------
# Init / templates
# ---------------------------------------------------------------------------


def templates_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    local = here / "templates"
    if local.is_dir() and (local / PEOPLE).exists():
        return local
    return None


def write_empty_csvs(dest: Path, *, bot_id: str = "", owner_name: str = "You") -> None:
    dest.mkdir(parents=True, exist_ok=True)
    notes_dir(dest).mkdir(parents=True, exist_ok=True)
    backups_dir(dest).mkdir(parents=True, exist_ok=True)
    tmpl = templates_dir()
    for name, headers in HEADERS.items():
        path = dest / name
        if path.exists():
            continue
        if tmpl is not None:
            shutil.copy2(tmpl / name, path)
        else:
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers, lineterminator="\n")
                writer.writeheader()
    owner_path = dest / "owner.json"
    if not owner_path.exists():
        payload: dict[str, Any] = {"name": owner_name or "You"}
        if bot_id:
            payload["bot_id"] = bot_id
        write_owner_json(dest, payload)
    elif bot_id:
        data = load_owner_json(dest)
        stored = str(data.get("bot_id") or "").strip()
        if not stored:
            data["bot_id"] = bot_id
            if "name" not in data or not data["name"]:
                data["name"] = owner_name or "You"
            write_owner_json(dest, data)


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.ledger)
    bot_id = resolve_bot_id(getattr(args, "bot_id", None), required=True)
    if dest.exists() and any((dest / n).exists() for n in HEADERS):
        mismatch = verify_bot_id(dest, bot_id, hard=False)
        if mismatch:
            die(mismatch)
        if not args.force:
            die(
                f"ledger already has CSV files in {dest}. "
                "Pass --force to fill only missing files."
            )
    write_empty_csvs(dest, bot_id=bot_id)
    # Ensure bot_id is recorded even when owner.json already existed.
    data = load_owner_json(dest)
    data["bot_id"] = bot_id
    if not data.get("name"):
        data["name"] = "You"
    write_owner_json(dest, data)
    print(f"initialized ledger at {dest}")
    print(f"owner.json bot_id={bot_id}")
    print("files: " + ", ".join(HEADERS))
    print("notes/ and backups/ ready. owner.json default name is You.")
    return 0


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def require_iso_date(label: str, value: str, *, allow_empty: bool = False) -> None:
    if not value:
        if allow_empty:
            return
        raise LedgerError(f"{label} is required (YYYY-MM-DD)")
    if parse_date(value) is None:
        raise LedgerError(f"{label} must be YYYY-MM-DD, got {value!r}")


def person_ids(ledger: Path) -> set[str]:
    return {r["person_id"] for r in read_rows(ledger, PEOPLE) if r["person_id"]}


def org_ids(ledger: Path) -> set[str]:
    return {r["org_id"] for r in read_rows(ledger, ORGS) if r["org_id"]}


def project_ids(ledger: Path) -> set[str]:
    return {r["project_id"] for r in read_rows(ledger, PROJECTS) if r["project_id"]}


def known_entity_ids(ledger: Path) -> set[str]:
    ids = {"ME"}
    ids |= person_ids(ledger)
    ids |= org_ids(ledger)
    ids |= project_ids(ledger)
    return ids


def find_person_dupes(
    rows: Sequence[Mapping[str, str]],
    *,
    name: str,
    email: str,
    phone: str,
    handle: str,
    org_id: str,
    skip_id: str = "",
) -> list[str]:
    hits: list[str] = []
    name_l = name.strip().lower()
    for r in rows:
        if r["person_id"] == skip_id:
            continue
        if email and r["email"] and r["email"].lower() == email.lower():
            hits.append(f"{r['person_id']} (same email)")
            continue
        if phone and r["phone"] and r["phone"] == phone:
            hits.append(f"{r['person_id']} (same phone)")
            continue
        if handle and r["handle"] and r["handle"].lower() == handle.lower():
            hits.append(f"{r['person_id']} (same handle)")
            continue
        if name_l and r["name"].strip().lower() == name_l:
            if org_id and r["org_id"] == org_id:
                hits.append(f"{r['person_id']} (same name + org)")
            else:
                hits.append(f"{r['person_id']} (same name: ambiguous)")
    return hits


# ---------------------------------------------------------------------------
# Add / update
# ---------------------------------------------------------------------------


def cmd_add_person(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    rows = read_rows(ledger, PEOPLE)
    name = args.name.strip()
    if not name:
        die("name is required")
    email = (args.email or "").strip()
    phone = (args.phone or "").strip()
    handle = (args.handle or "").strip()
    org_id = (args.org_id or "").strip()
    if org_id and org_id not in org_ids(ledger):
        die(f"org_id {org_id} not found in organizations.csv")
    dupes = find_person_dupes(
        rows, name=name, email=email, phone=phone, handle=handle, org_id=org_id
    )
    definite = [d for d in dupes if "same email" in d or "same phone" in d or "same handle" in d or "same name + org" in d]
    if definite and not args.force_new:
        die(
            "possible existing person: "
            + "; ".join(definite)
            + ". Reuse that ID, or pass --force-new if this really is someone new."
        )
    if any("ambiguous" in d for d in dupes) and not args.force_new:
        die(
            "ambiguous name match: "
            + "; ".join(dupes)
            + ". Ask the owner which person, or pass --force-new for a new ID."
        )
    cadence = int(args.cadence_days) if args.cadence_days is not None else DEFAULT_CADENCE
    if cadence < 1:
        die("cadence_days must be >= 1")
    cadence_source = args.cadence_source or "default"
    if cadence_source not in CADENCE_SOURCES:
        die(f"cadence_source must be one of {', '.join(CADENCE_SOURCES)}")
    today = today_iso()
    backup_ledger(ledger)
    pid = allocate_id(ledger, PEOPLE)
    notes_pointer = f"notes/{pid}.md"
    row = {
        "person_id": pid,
        "name": name,
        "aliases": (args.aliases or "").strip(),
        "email": email,
        "phone": phone,
        "handle": handle,
        "network": (args.network or "").strip(),
        "relationship": (args.relationship or "").strip(),
        "org_id": org_id,
        "role": (args.role or "").strip(),
        "city": (args.city or "").strip(),
        "how_met": (args.how_met or "").strip(),
        "cadence_days": str(cadence),
        "cadence_source": cadence_source,
        "cadence_proposed": "",
        "last_contact": (args.last_contact or "").strip(),
        "health": "inactive",
        "notes_pointer": notes_pointer,
        "created": today,
        "updated": today,
    }
    require_iso_date("last_contact", row["last_contact"], allow_empty=True)
    # seed health from last_contact if present
    if row["last_contact"]:
        ds = day_diff(today, row["last_contact"])
        row["health"] = compute_health(ds, cadence)
    rows.append(row)
    write_rows(ledger, PEOPLE, rows)
    np = ledger / notes_pointer
    np.parent.mkdir(parents=True, exist_ok=True)
    if not np.exists():
        np.write_text(
            f"# Notes for {name} ({pid})\n\n# Made with Grok Bot.\n",
            encoding="utf-8",
        )
    if row["health"]:
        ds_s = ""
        if row["last_contact"]:
            delta = day_diff(today, row["last_contact"])
            if delta is not None:
                ds_s = str(delta)
        append_health_history(
            ledger,
            person_id=pid,
            health=row["health"],
            last_contact=row["last_contact"],
            days_since=ds_s,
            cadence_days=str(cadence),
            reason="changed",
            source="add_person",
        )
    append_log(ledger, "add_person", pid, f"Added person {name}")
    print(f"Added {pid}: {name}")
    return 0


def cmd_add_org(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    rows = read_rows(ledger, ORGS)
    name = args.name.strip()
    if not name:
        die("name is required")
    for r in rows:
        if r["name"].strip().lower() == name.lower() and not args.force_new:
            die(
                f"org with same name already exists: {r['org_id']} ({r['name']}). "
                "Pass --force-new if this is a different org."
            )
    backup_ledger(ledger)
    oid = allocate_id(ledger, ORGS)
    row = {
        "org_id": oid,
        "name": name,
        "aliases": (args.aliases or "").strip(),
        "domains": (args.domains or "").strip(),
    }
    rows.append(row)
    write_rows(ledger, ORGS, rows)
    append_log(ledger, "add_org", oid, f"Added org {name}")
    print(f"Added {oid}: {name}")
    return 0


def cmd_add_project(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    rows = read_rows(ledger, PROJECTS)
    name = args.name.strip()
    if not name:
        die("name is required")
    org_id = (args.org_id or "").strip()
    if org_id and org_id not in org_ids(ledger):
        die(f"org_id {org_id} not found")
    status = (args.status or "active").strip()
    if status not in PROJECT_STATUSES:
        die(f"status must be one of {', '.join(PROJECT_STATUSES)}")
    backup_ledger(ledger)
    jid = allocate_id(ledger, PROJECTS)
    row = {
        "project_id": jid,
        "name": name,
        "aliases": (args.aliases or "").strip(),
        "org_id": org_id,
        "status": status,
    }
    rows.append(row)
    write_rows(ledger, PROJECTS, rows)
    append_log(ledger, "add_project", jid, f"Added project {name}")
    print(f"Added {jid}: {name}")
    return 0


def cmd_add_commitment(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    rows = read_rows(ledger, COMMITMENTS)
    direction = args.direction.strip()
    if direction not in DIRECTIONS:
        die(f"direction must be one of: {', '.join(DIRECTIONS)}")
    person_id = args.person_id.strip()
    if person_id not in person_ids(ledger):
        die(f"person_id {person_id} not found")
    what = args.what.strip()
    if not what:
        die("what is required")
    due = (args.due or "").strip()
    status = (args.status or "open").strip()
    if status not in COMMIT_STATUSES:
        die(f"status must be one of {', '.join(COMMIT_STATUSES)}")
    if status == "open":
        require_iso_date("due", due, allow_empty=False)
    else:
        require_iso_date("due", due, allow_empty=True)
    source_link = (args.source_link or "").strip()
    if not source_link:
        die("source_link is required (explicit promise provenance)")
    today = today_iso()
    backup_ledger(ledger)
    cid = allocate_id(ledger, COMMITMENTS)
    row = {
        "commit_id": cid,
        "direction": direction,
        "person_id": person_id,
        "what": what,
        "due": due,
        "status": status,
        "source_link": source_link,
        "created": today,
        "closed": "" if status == "open" else today,
    }
    rows.append(row)
    write_rows(ledger, COMMITMENTS, rows)
    append_log(ledger, "add_commitment", cid, f"{direction}: {what}")
    who = next(r["name"] for r in read_rows(ledger, PEOPLE) if r["person_id"] == person_id)
    due_bit = f" by {due}" if due else ""
    print(f"Added {cid}: {direction} {who}: {what}{due_bit}.")
    return 0


def cmd_add_interaction(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    rows = read_rows(ledger, INTERACTIONS)
    person_id = args.person_id.strip()
    if person_id not in person_ids(ledger):
        die(f"person_id {person_id} not found")
    when = (args.date or today_iso()).strip()
    require_iso_date("date", when)
    actor = (args.actor or "owner").strip()
    if actor not in ACTORS:
        die(f"actor must be one of {', '.join(ACTORS)}")
    channel = (args.channel or "").strip()
    summary = (args.summary or "").strip()
    if not summary:
        die("summary is required")
    source_link = (args.source_link or "").strip()
    backup_ledger(ledger)
    iid = allocate_id(ledger, INTERACTIONS)
    row = {
        "int_id": iid,
        "date": when,
        "person_id": person_id,
        "channel": channel,
        "actor": actor,
        "summary": summary,
        "source_link": source_link,
    }
    rows.append(row)
    write_rows(ledger, INTERACTIONS, rows)
    # refresh last_contact / health when actor is owner/them/both
    if actor in ("owner", "them", "both"):
        people = read_rows(ledger, PEOPLE)
        for p in people:
            if p["person_id"] != person_id:
                continue
            old_health = p["health"]
            prev = p["last_contact"]
            if not prev or when >= prev:
                p["last_contact"] = when
                cadence = int(p["cadence_days"] or DEFAULT_CADENCE)
                ds = day_diff(today_iso(), when)
                p["health"] = compute_health(ds, cadence)
                p["updated"] = today_iso()
                write_rows(ledger, PEOPLE, people)
                record_health_change_if_needed(
                    ledger, p, old_health, source=f"interaction:{iid}"
                )
            break
    append_log(ledger, "add_interaction", iid, summary)
    print(f"Added {iid}: {when} with {person_id}: {summary}")
    return 0


def cmd_add_link(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    rows = read_rows(ledger, LINKS)
    from_id = args.from_id.strip()
    to_id = args.to_id.strip()
    known = known_entity_ids(ledger)
    if from_id not in known:
        die(f"from_id {from_id} is not a known person/org/project/ME")
    if to_id not in known:
        die(f"to_id {to_id} is not a known person/org/project/ME")
    typ = args.type.strip()
    if not typ:
        die("type is required")
    basis = (args.basis or "stated").strip()
    if basis not in BASIS:
        die(f"basis must be one of {', '.join(BASIS)}")
    source = (args.source or "").strip()
    for r in rows:
        if (
            r["from_id"] == from_id
            and r["to_id"] == to_id
            and r["type"] == typ
            and not args.force_new
        ):
            die("identical link already exists. Pass --force-new to add anyway.")
    row = {
        "from_id": from_id,
        "to_id": to_id,
        "type": typ,
        "basis": basis,
        "source": source,
    }
    backup_ledger(ledger)
    rows.append(row)
    write_rows(ledger, LINKS, rows)
    append_log(ledger, "add_link", f"{from_id}->{to_id}", typ)
    print(f"Added link {from_id} -[{typ}]-> {to_id}")
    return 0


def _update_row(
    ledger: Path,
    filename: str,
    id_col: str,
    entity_id: str,
    updates: Mapping[str, str],
    action: str,
) -> int:
    rows = read_rows(ledger, filename)
    found = False
    old_health = ""
    updated_person: dict[str, str] | None = None
    for r in rows:
        if r[id_col] != entity_id:
            continue
        found = True
        if filename == PEOPLE:
            old_health = r.get("health", "")
        for k, v in updates.items():
            if k not in r:
                die(f"unknown field {k} for {filename}")
            if k == id_col:
                die(f"cannot change {id_col}")
            r[k] = v
        if "updated" in r:
            r["updated"] = today_iso()
        # Recompute health when last_contact or cadence changes and health was
        # not set explicitly.
        if filename == PEOPLE and "health" not in updates:
            if "last_contact" in updates or "cadence_days" in updates:
                cadence = int(r.get("cadence_days") or DEFAULT_CADENCE)
                lc = r.get("last_contact") or ""
                ds = day_diff(today_iso(), lc) if lc else None
                r["health"] = compute_health(ds, cadence)
        if filename == PEOPLE:
            updated_person = r
        break
    if not found:
        die(f"{entity_id} not found in {filename}")
    backup_ledger(ledger)
    write_rows(ledger, filename, rows)
    if updated_person is not None:
        record_health_change_if_needed(
            ledger, updated_person, old_health, source="update_person"
        )
    append_log(ledger, action, entity_id, json.dumps(updates, ensure_ascii=False))
    print(f"Updated {entity_id}")
    return 0


def cmd_update_person(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    updates: dict[str, str] = {}
    for field in (
        "name",
        "aliases",
        "email",
        "phone",
        "handle",
        "network",
        "relationship",
        "org_id",
        "role",
        "city",
        "how_met",
        "cadence_source",
        "cadence_proposed",
        "last_contact",
        "health",
    ):
        val = getattr(args, field, None)
        if val is not None:
            updates[field] = str(val).strip()
    if args.cadence_days is not None:
        updates["cadence_days"] = str(int(args.cadence_days))
    if "org_id" in updates and updates["org_id"] and updates["org_id"] not in org_ids(ledger):
        die(f"org_id {updates['org_id']} not found")
    if "last_contact" in updates:
        require_iso_date("last_contact", updates["last_contact"], allow_empty=True)
    if "cadence_source" in updates and updates["cadence_source"] not in CADENCE_SOURCES:
        die(f"cadence_source must be one of {', '.join(CADENCE_SOURCES)}")
    if "health" in updates and updates["health"] and updates["health"] not in HEALTH_VALUES:
        die(f"health must be one of {', '.join(HEALTH_VALUES)}")
    if not updates:
        die("nothing to update; pass at least one field")
    return _update_row(ledger, PEOPLE, "person_id", args.id, updates, "update_person")


def cmd_update_org(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    updates: dict[str, str] = {}
    for field in ("name", "aliases", "domains"):
        val = getattr(args, field, None)
        if val is not None:
            updates[field] = str(val).strip()
    if not updates:
        die("nothing to update")
    return _update_row(ledger, ORGS, "org_id", args.id, updates, "update_org")


def cmd_update_project(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    updates: dict[str, str] = {}
    for field in ("name", "aliases", "org_id", "status"):
        val = getattr(args, field, None)
        if val is not None:
            updates[field] = str(val).strip()
    if "org_id" in updates and updates["org_id"] and updates["org_id"] not in org_ids(ledger):
        die(f"org_id {updates['org_id']} not found")
    if "status" in updates and updates["status"] not in PROJECT_STATUSES:
        die(f"status must be one of {', '.join(PROJECT_STATUSES)}")
    if not updates:
        die("nothing to update")
    return _update_row(ledger, PROJECTS, "project_id", args.id, updates, "update_project")


def cmd_update_commitment(args: argparse.Namespace) -> int:
    """Update open-promise fields. Use close for status changes."""
    ledger = ensure_ledger_dir(Path(args.ledger))
    updates: dict[str, str] = {}
    if args.due is not None:
        updates["due"] = str(args.due).strip()
    if args.what is not None:
        updates["what"] = str(args.what).strip()
    if args.person_id is not None:
        updates["person_id"] = str(args.person_id).strip()
    if args.source_link is not None:
        updates["source_link"] = str(args.source_link).strip()
    if not updates:
        die("nothing to update; pass --due, --what, --person-id, and/or --source-link")
    rows = read_rows(ledger, COMMITMENTS)
    found = False
    for r in rows:
        if r["commit_id"] != args.id:
            continue
        found = True
        if "person_id" in updates:
            if updates["person_id"] not in person_ids(ledger):
                die(f"person_id {updates['person_id']} not found")
        if "what" in updates and not updates["what"]:
            die("what cannot be empty")
        if "source_link" in updates and not updates["source_link"]:
            die("source_link cannot be empty")
        if "due" in updates:
            status = r["status"]
            if status == "open":
                require_iso_date("due", updates["due"], allow_empty=False)
            else:
                require_iso_date("due", updates["due"], allow_empty=True)
        for k, v in updates.items():
            r[k] = v
        # Open commitments must keep a due date after any update.
        if r["status"] == "open" and not r["due"]:
            die("due date missing. Ask the owner once; open promises need a due date.")
        break
    if not found:
        die(f"{args.id} not found in commitments.csv")
    backup_ledger(ledger)
    write_rows(ledger, COMMITMENTS, rows)
    append_log(
        ledger, "update_commitment", args.id, json.dumps(updates, ensure_ascii=False)
    )
    print(f"Updated {args.id}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    status = args.status.strip()
    if status not in COMMIT_STATUSES or status == "open":
        die("status must be done, dropped, or renegotiated")
    rows = read_rows(ledger, COMMITMENTS)
    found = False
    for r in rows:
        if r["commit_id"] != args.id:
            continue
        found = True
        r["status"] = status
        r["closed"] = today_iso()
        break
    if not found:
        die(f"{args.id} not found in commitments.csv")
    backup_ledger(ledger)
    write_rows(ledger, COMMITMENTS, rows)
    append_log(ledger, "close_commitment", args.id, status)
    print(f"Closed {args.id} as {status}")
    return 0


def cmd_health_snapshot(args: argparse.Namespace) -> int:
    """Append weekly health_history rows for every person."""
    ledger = ensure_ledger_dir(Path(args.ledger))
    people = read_rows(ledger, PEOPLE)
    if not people:
        print("no people to snapshot")
        return 0
    today = today_iso()
    backup_ledger(ledger)
    # Refresh derived health, then write one weekly row per person.
    old_health_by_id: dict[str, str] = {}
    for p in people:
        old_health_by_id[p["person_id"]] = p.get("health", "")
        cadence = int(p.get("cadence_days") or DEFAULT_CADENCE)
        lc = p.get("last_contact") or ""
        ds = day_diff(today, lc) if lc else None
        p["health"] = compute_health(ds, cadence)
        if p["health"] != old_health_by_id[p["person_id"]]:
            p["updated"] = today
    write_rows(ledger, PEOPLE, people)
    for p in people:
        lc = p.get("last_contact") or ""
        ds = day_diff(today, lc) if lc else None
        ds_s = "" if ds is None else str(ds)
        cadence_s = str(p.get("cadence_days") or DEFAULT_CADENCE)
        append_health_history(
            ledger,
            person_id=p["person_id"],
            health=p["health"],
            last_contact=lc,
            days_since=ds_s,
            cadence_days=cadence_s,
            reason="weekly",
            source="health snapshot",
        )
        record_health_change_if_needed(
            ledger,
            p,
            old_health_by_id.get(p["person_id"], ""),
            source="health snapshot",
        )
    append_log(ledger, "health_snapshot", today, f"weekly rows for {len(people)} people")
    print(f"Wrote weekly health snapshot for {len(people)} people.")
    return 0


# ---------------------------------------------------------------------------
# Merge / forget
# ---------------------------------------------------------------------------


def cmd_merge(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    kept = args.kept.strip()
    merged = args.merged.strip()
    if kept == merged:
        die("kept and merged must differ")
    people = read_rows(ledger, PEOPLE)
    by_id = {r["person_id"]: r for r in people}
    if kept not in by_id:
        die(f"kept id {kept} not found")
    if merged not in by_id:
        die(f"merged id {merged} not found")
    # Prefer keeping the lower numeric ID if caller swapped them? Spec says keep lower ID.
    kept_n = int(kept.split("-", 1)[1])
    merged_n = int(merged.split("-", 1)[1])
    if kept_n > merged_n and not args.allow_higher_kept:
        die(
            f"spec says keep the lower ID. {merged} < {kept}. "
            "Swap them, or pass --allow-higher-kept."
        )
    evidence = (args.evidence or "").strip()
    if not evidence:
        die("evidence is required")
    backup_ledger(ledger)
    repointed = 0

    def repoint(filename: str, cols: Sequence[str]) -> None:
        nonlocal repointed
        rows = read_rows(ledger, filename)
        changed = False
        for r in rows:
            for c in cols:
                if r.get(c) == merged:
                    r[c] = kept
                    repointed += 1
                    changed = True
        if changed:
            write_rows(ledger, filename, rows)

    repoint(COMMITMENTS, ["person_id"])
    repoint(INTERACTIONS, ["person_id"])
    repoint(HEALTH, ["person_id"])
    # links
    link_rows = read_rows(ledger, LINKS)
    changed_links = False
    for r in link_rows:
        for c in ("from_id", "to_id"):
            if r[c] == merged:
                r[c] = kept
                repointed += 1
                changed_links = True
    if changed_links:
        write_rows(ledger, LINKS, link_rows)

    # fold aliases + contact fields onto kept
    keep_row = by_id[kept]
    merge_row = by_id[merged]
    aliases = [a.strip() for a in keep_row["aliases"].split(";") if a.strip()]
    aliases.append(merge_row["name"])
    for a in merge_row["aliases"].split(";"):
        a = a.strip()
        if a and a not in aliases:
            aliases.append(a)
    aliases.append(merged)  # retired ID as alias
    keep_row["aliases"] = "; ".join(aliases)
    for field in ("email", "phone", "handle", "city", "how_met", "role", "network", "relationship"):
        if not keep_row[field] and merge_row[field]:
            keep_row[field] = merge_row[field]
    if not keep_row["org_id"] and merge_row["org_id"]:
        keep_row["org_id"] = merge_row["org_id"]
    # last_contact = latest
    if merge_row["last_contact"] and (
        not keep_row["last_contact"] or merge_row["last_contact"] > keep_row["last_contact"]
    ):
        keep_row["last_contact"] = merge_row["last_contact"]
    keep_row["updated"] = today_iso()
    people = [r for r in people if r["person_id"] != merged]
    write_rows(ledger, PEOPLE, people)

    # merge notes
    keep_notes = notes_dir(ledger) / f"{kept}.md"
    merge_notes = notes_dir(ledger) / f"{merged}.md"
    if merge_notes.exists():
        extra = merge_notes.read_text(encoding="utf-8")
        keep_notes.parent.mkdir(parents=True, exist_ok=True)
        with keep_notes.open("a", encoding="utf-8") as f:
            f.write(f"\n# merged from {merged}\n")
            f.write(extra)
        merge_notes.unlink()

    merges = read_rows(ledger, MERGES)
    mid = allocate_id(ledger, MERGES)
    merges.append(
        {
            "merge_id": mid,
            "date": today_iso(),
            "kept_id": kept,
            "merged_id": merged,
            "evidence": evidence,
            "repointed_rows": str(repointed),
            "approved_by_owner": "yes",
        }
    )
    write_rows(ledger, MERGES, merges)
    append_log(
        ledger,
        "merge",
        mid,
        f"kept {kept}, merged {merged}, repointed {repointed}",
        approved="yes",
    )
    print(f"Merged {merged} into {kept} ({mid}); repointed {repointed} fields.")
    return 0


def _forget_target_exists(ledger: Path, target: str) -> bool:
    if target.startswith("P-"):
        return target in person_ids(ledger)
    if target.startswith("O-"):
        return target in org_ids(ledger)
    if target.startswith("J-"):
        return target in project_ids(ledger)
    if target.startswith("C-"):
        return any(r["commit_id"] == target for r in read_rows(ledger, COMMITMENTS))
    if target.startswith("I-"):
        return any(r["int_id"] == target for r in read_rows(ledger, INTERACTIONS))
    return False


def cmd_forget(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    target = args.id.strip()
    if not args.yes:
        die("forget permanently deletes data. Pass --yes after the owner confirms.")
    if not target.startswith(("P-", "O-", "J-", "C-", "I-")):
        die(f"do not know how to forget id {target!r} (expected P-/O-/J-/C-/I-)")
    if not _forget_target_exists(ledger, target):
        die(f"nothing matches {target}. No rows deleted.")
    backup_ledger(ledger)
    removed: list[str] = []

    def drop(filename: str, pred: Callable[[dict[str, str]], bool]) -> None:
        rows = read_rows(ledger, filename)
        keep = [r for r in rows if not pred(r)]
        if len(keep) != len(rows):
            removed.append(f"{filename}:{len(rows) - len(keep)}")
            write_rows(ledger, filename, keep)

    if target.startswith("P-"):
        drop(PEOPLE, lambda r: r["person_id"] == target)
        drop(COMMITMENTS, lambda r: r["person_id"] == target)
        drop(INTERACTIONS, lambda r: r["person_id"] == target)
        drop(HEALTH, lambda r: r["person_id"] == target)
        drop(LINKS, lambda r: r["from_id"] == target or r["to_id"] == target)
        note = notes_dir(ledger) / f"{target}.md"
        if note.exists():
            note.unlink()
            removed.append(f"notes/{target}.md")
    elif target.startswith("O-"):
        drop(ORGS, lambda r: r["org_id"] == target)
        # clear org_id on people/projects rather than deleting them
        people = read_rows(ledger, PEOPLE)
        for p in people:
            if p["org_id"] == target:
                p["org_id"] = ""
                p["updated"] = today_iso()
        write_rows(ledger, PEOPLE, people)
        projects = read_rows(ledger, PROJECTS)
        for p in projects:
            if p["org_id"] == target:
                p["org_id"] = ""
        write_rows(ledger, PROJECTS, projects)
        drop(LINKS, lambda r: r["from_id"] == target or r["to_id"] == target)
    elif target.startswith("J-"):
        drop(PROJECTS, lambda r: r["project_id"] == target)
        drop(LINKS, lambda r: r["from_id"] == target or r["to_id"] == target)
    elif target.startswith("C-"):
        drop(COMMITMENTS, lambda r: r["commit_id"] == target)
    elif target.startswith("I-"):
        drop(INTERACTIONS, lambda r: r["int_id"] == target)

    # scrub backups of matching content? Spec says mirror and backups included.
    # We remove matching rows from backup CSV copies under backups/.
    broot = backups_dir(ledger)
    if broot.is_dir():
        for stamp in broot.iterdir():
            if not stamp.is_dir():
                continue
            for name in HEADERS:
                path = stamp / name
                if not path.exists():
                    continue
                try:
                    with path.open(newline="", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        fields = list(reader.fieldnames or [])
                        rows = list(reader)
                    if not fields:
                        continue
                    new_rows = []
                    for r in rows:
                        blob = " ".join((r.get(k) or "") for k in fields)
                        if target in blob:
                            continue
                        new_rows.append(r)
                    with path.open("w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
                        w.writeheader()
                        w.writerows(new_rows)
                except OSError:
                    pass
            note = stamp / "notes" / f"{target}.md"
            if note.exists():
                note.unlink()

    append_log(ledger, "forget", target, "forgotten on request", approved="yes")
    print(f"Forgotten {target}. Removed: {', '.join(removed) or 'nothing matched'}.")
    print("Logged only: forgotten on request.")
    return 0


# ---------------------------------------------------------------------------
# Export / build brain
# ---------------------------------------------------------------------------


def load_owner_name(ledger: Path, override: str | None) -> str:
    if override:
        return override
    path = ledger / "owner.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = str(data.get("name") or "").strip()
            if name:
                return name
        except (json.JSONDecodeError, OSError):
            pass
    return "You"


def refresh_person_derived(people: list[dict[str, str]], generated: str) -> None:
    for p in people:
        cadence = int(p["cadence_days"] or DEFAULT_CADENCE)
        lc = p["last_contact"]
        ds = day_diff(generated, lc) if lc else None
        p["health"] = compute_health(ds, cadence)


def _format_coverage_dict(obj: Mapping[str, Any]) -> str:
    """Turn a coverage JSON object into one plain-language strip."""
    if obj.get("text"):
        return str(obj["text"]).strip()

    def fmt_list(val: Any) -> str:
        if isinstance(val, (list, tuple)):
            return ", ".join(str(s) for s in val)
        return str(val)

    bits: list[str] = []
    seeing = obj.get("seeing") or obj.get("can_see") or obj.get("sources") or obj.get(
        "scanned"
    )
    not_seeing = obj.get("not_seeing") or obj.get("cannot_see") or obj.get("missing")
    through = obj.get("through") or obj.get("through_date") or obj.get("until")
    if seeing and (obj.get("seeing") or obj.get("can_see")):
        bits.append(f"seeing {fmt_list(seeing)}")
    elif seeing:
        bits.append(f"scanned {fmt_list(seeing)}")
    if not_seeing:
        bits.append(f"not seeing {fmt_list(not_seeing)}")
    if through:
        bits.append(f"through {through}")
    if bits:
        return "; ".join(bits)
    return ""


def load_coverage_text(
    home: Path | None,
    *,
    coverage_arg: str | None = None,
    coverage_file: str | None = None,
    ledger: Path | None = None,
) -> str:
    """Coverage strip: --coverage, a file, work/coverage.json, or owner.json.

    Returns empty string when nothing is configured (caller omits the field).
    """
    if coverage_arg is not None and str(coverage_arg).strip():
        return str(coverage_arg).strip()
    candidates: list[Path] = []
    if coverage_file:
        candidates.append(Path(coverage_file).expanduser().resolve())
    if home is not None:
        candidates.append(home_work_dir(home) / "coverage.json")
        candidates.append(home_work_dir(home) / "coverage.txt")
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        if path.suffix.lower() == ".json":
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                return raw
            if isinstance(obj, str):
                return obj.strip()
            if isinstance(obj, dict):
                formatted = _format_coverage_dict(obj)
                return formatted or json.dumps(obj, ensure_ascii=False)
            return raw
        return raw
    if ledger is not None:
        owner = load_owner_json(ledger)
        raw_cov = owner.get("coverage")
        if isinstance(raw_cov, str) and raw_cov.strip():
            return raw_cov.strip()
        if isinstance(raw_cov, dict):
            return _format_coverage_dict(raw_cov)
    return ""


def export_brain_obj(
    ledger: Path,
    *,
    owner_name: str,
    example: bool,
    generated: str | None = None,
    coverage: str = "",
) -> dict[str, Any]:
    """Build Brain JSON from the ledger. Read-only: never writes CSV files.

    People with no last_contact get days_since=-1 so the Page can show
    'no contact logged'. Optional coverage is omitted when empty; the Page
    falls back to its example note via the COVERAGE constant after the
    LEDGER-DATA markers.
    """
    generated = generated or today_iso()
    # Copy rows so derived health/days stay in-memory only.
    people_rows = [dict(r) for r in read_rows(ledger, PEOPLE)]
    refresh_person_derived(people_rows, generated)

    people: list[dict[str, Any]] = []
    for r in people_rows:
        cadence = int(r["cadence_days"] or DEFAULT_CADENCE)
        lc = r["last_contact"]
        ds = day_diff(generated, lc) if lc else None
        people.append(
            {
                "id": r["person_id"],
                "name": r["name"],
                "org_id": r["org_id"] or None,
                "role": r["role"],
                "network": r["network"],
                "relationship": r["relationship"],
                "cadence_days": cadence,
                "last_contact": lc,
                # Page expects negative when never contacted (not 9999).
                "days_since": ds if ds is not None else -1,
                "health": r["health"],
                "city": r["city"],
                "how_met": r["how_met"],
            }
        )

    orgs = [{"id": r["org_id"], "name": r["name"]} for r in read_rows(ledger, ORGS)]
    projects = [
        {
            "id": r["project_id"],
            "name": r["name"],
            "org_id": r["org_id"],
            "status": r["status"],
        }
        for r in read_rows(ledger, PROJECTS)
    ]
    links = [
        {
            "from_id": r["from_id"],
            "to_id": r["to_id"],
            "type": r["type"],
            "basis": r["basis"],
            "source": r["source"],
        }
        for r in read_rows(ledger, LINKS)
    ]
    commitments = [
        {
            "id": r["commit_id"],
            "direction": r["direction"],
            "person_id": r["person_id"],
            "what": r["what"],
            "due": r["due"],
            "status": r["status"],
            "source_link": r["source_link"],
        }
        for r in read_rows(ledger, COMMITMENTS)
    ]
    interactions = [
        {
            "id": r["int_id"],
            "date": r["date"],
            "person_id": r["person_id"],
            "channel": r["channel"],
            "actor": r["actor"],
            "summary": r["summary"],
            "source_link": r["source_link"],
        }
        for r in read_rows(ledger, INTERACTIONS)
    ]

    out: dict[str, Any] = {
        "generated": generated,
        "example": bool(example),
        "owner": {"name": owner_name},
        "people": people,
        "orgs": orgs,
        "projects": projects,
        "links": links,
        "commitments": commitments,
        "interactions": interactions,
    }
    if coverage.strip():
        out["coverage"] = coverage.strip()
    return out


def cmd_export_brain(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    home = getattr(args, "home_path", None)
    coverage = load_coverage_text(
        home,
        coverage_arg=getattr(args, "coverage", None),
        coverage_file=getattr(args, "coverage_file", None),
        ledger=ledger,
    )
    obj = export_brain_obj(
        ledger,
        owner_name=load_owner_name(ledger, args.owner),
        example=bool(args.example),
        generated=args.generated,
        coverage=coverage,
    )
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def inject_ledger(template: str, ledger_obj: object) -> str:
    start = template.find(LEDGER_START)
    end = template.find(LEDGER_END)
    if start < 0 or end < 0 or end < start:
        raise LedgerError(
            f"LEDGER-DATA markers missing or out of order "
            f"(need {LEDGER_START!r} then {LEDGER_END!r})"
        )
    end_after = end + len(LEDGER_END)
    payload = json.dumps(ledger_obj, ensure_ascii=False, indent=2)
    block = f"{LEDGER_START}\nconst LEDGER: Ledger = {payload};\n{LEDGER_END}"
    return template[:start] + block + template[end_after:]


def fetch_text(source: str) -> str:
    if source.startswith("http://") or source.startswith("https://"):
        try:
            with urllib.request.urlopen(source, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise LedgerError(f"could not download template: {e}") from e
    path = Path(source)
    if not path.exists():
        raise LedgerError(f"template not found: {source}")
    return path.read_text(encoding="utf-8")


def default_brain_template(home: Path | None) -> str:
    if home is not None:
        local = home_tools_dir(home) / "brain" / "claudia-brain.canvas.tsx"
        if local.is_file():
            return str(local)
    return DEFAULT_BRAIN_URL


def cmd_build_brain(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    home = getattr(args, "home_path", None)
    template_src = args.template or default_brain_template(home)
    coverage = load_coverage_text(
        home,
        coverage_arg=getattr(args, "coverage", None),
        coverage_file=getattr(args, "coverage_file", None),
        ledger=ledger,
    )
    try:
        template = fetch_text(template_src)
        obj = export_brain_obj(
            ledger,
            owner_name=load_owner_name(ledger, args.owner),
            example=bool(args.example),
            generated=args.generated,
            coverage=coverage,
        )
        out = inject_ledger(template, obj)
    except LedgerError as e:
        die(str(e))
    # Sanity: code after the data end marker (UPDATED / COVERAGE / UI) must remain.
    if "const UPDATED" not in out or "const COVERAGE" not in out:
        die(
            "build-brain output is missing UPDATED/COVERAGE after the data end "
            "marker. Re-download the hosted Brain template; do not redesign it."
        )
    Path(args.output).write_text(out, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    ledger = Path(args.ledger)
    problems: list[str] = []
    warnings: list[str] = []

    if not ledger.exists():
        print(f"error: ledger folder not found: {ledger}")
        return 2

    bot_id = resolve_bot_id(getattr(args, "bot_id", None), required=False)
    stored = str(load_owner_json(ledger).get("bot_id") or "").strip()
    if stored and not bot_id:
        problems.append(
            "owner.json has bot_id="
            + repr(stored)
            + " but this run did not pass CLAUDIA_BOT_ID or --bot-id. "
            "Pass this bot's id so we can confirm the ledger belongs to it."
        )
    elif bot_id:
        mismatch = verify_bot_id(ledger, bot_id, hard=False)
        if mismatch:
            problems.append(mismatch)

    for name in HEADERS:
        path = ledger / name
        if not path.exists():
            problems.append(f"Missing file {name}. Run init to create starter CSVs.")
            continue
        try:
            rows = read_rows(ledger, name)
        except LedgerError as e:
            problems.append(str(e))
            continue

        # duplicate IDs
        if name in ID_SPECS:
            col, prefix, _width = ID_SPECS[name]
            seen: dict[str, int] = {}
            for r in rows:
                rid = r[col]
                if not rid:
                    problems.append(f"{name}: a row has an empty {col}")
                    continue
                if not rid.startswith(prefix):
                    problems.append(
                        f"{name}: {rid} should start with {prefix}"
                    )
                seen[rid] = seen.get(rid, 0) + 1
            for rid, n in seen.items():
                if n > 1:
                    problems.append(f"{name}: duplicate id {rid} appears {n} times")

    # stop early if core files unreadable
    try:
        people = read_rows(ledger, PEOPLE) if (ledger / PEOPLE).exists() else []
        orgs = read_rows(ledger, ORGS) if (ledger / ORGS).exists() else []
        projects = read_rows(ledger, PROJECTS) if (ledger / PROJECTS).exists() else []
        commits = read_rows(ledger, COMMITMENTS) if (ledger / COMMITMENTS).exists() else []
        ints = read_rows(ledger, INTERACTIONS) if (ledger / INTERACTIONS).exists() else []
        links = read_rows(ledger, LINKS) if (ledger / LINKS).exists() else []
        health = read_rows(ledger, HEALTH) if (ledger / HEALTH).exists() else []
    except LedgerError as e:
        problems.append(str(e))
        people = orgs = projects = commits = ints = links = health = []

    pids = {r["person_id"] for r in people}
    oids = {r["org_id"] for r in orgs}
    jids = {r["project_id"] for r in projects}
    known = {"ME"} | pids | oids | jids

    for r in people:
        if r["org_id"] and r["org_id"] not in oids:
            problems.append(
                f"Person {r['person_id']} ({r['name']}) points to missing org {r['org_id']}"
            )
        if r["last_contact"] and parse_date(r["last_contact"]) is None:
            problems.append(
                f"Person {r['person_id']}: last_contact {r['last_contact']!r} is not YYYY-MM-DD"
            )
        try:
            c = int(r["cadence_days"] or DEFAULT_CADENCE)
            if c < 1:
                problems.append(f"Person {r['person_id']}: cadence_days must be >= 1")
        except ValueError:
            problems.append(f"Person {r['person_id']}: cadence_days is not a number")
        if r["cadence_source"] and r["cadence_source"] not in CADENCE_SOURCES:
            problems.append(
                f"Person {r['person_id']}: bad cadence_source {r['cadence_source']!r}"
            )
        if r["notes_pointer"]:
            np = ledger / r["notes_pointer"]
            if not np.exists():
                warnings.append(
                    f"Person {r['person_id']}: notes file missing ({r['notes_pointer']})"
                )

    for r in projects:
        if r["org_id"] and r["org_id"] not in oids:
            problems.append(
                f"Project {r['project_id']} points to missing org {r['org_id']}"
            )
        if r["status"] and r["status"] not in PROJECT_STATUSES:
            problems.append(
                f"Project {r['project_id']}: bad status {r['status']!r}"
            )

    today = today_iso()
    for r in commits:
        if r["person_id"] not in pids:
            problems.append(
                f"Commitment {r['commit_id']} points to missing person {r['person_id']}"
            )
        if r["direction"] not in DIRECTIONS:
            problems.append(
                f"Commitment {r['commit_id']}: bad direction {r['direction']!r}"
            )
        if r["status"] not in COMMIT_STATUSES:
            problems.append(
                f"Commitment {r['commit_id']}: bad status {r['status']!r}"
            )
        if r["due"] and parse_date(r["due"]) is None:
            problems.append(
                f"Commitment {r['commit_id']}: due {r['due']!r} is not YYYY-MM-DD"
            )
        if r["status"] == "open" and not r["due"]:
            problems.append(
                f"Commitment {r['commit_id']} is open but has no due date: ask the owner once."
            )
        if r["status"] == "open" and r["due"] and r["due"] < today:
            warnings.append(
                f"Commitment {r['commit_id']} is overdue (due {r['due']})."
            )
        if not r["source_link"]:
            problems.append(
                f"Commitment {r['commit_id']} is missing source_link (provenance)."
            )

    for r in ints:
        if r["person_id"] not in pids:
            problems.append(
                f"Interaction {r['int_id']} points to missing person {r['person_id']}"
            )
        if parse_date(r["date"]) is None:
            problems.append(
                f"Interaction {r['int_id']}: date {r['date']!r} is not YYYY-MM-DD"
            )
        if r["actor"] and r["actor"] not in ACTORS:
            problems.append(
                f"Interaction {r['int_id']}: bad actor {r['actor']!r}"
            )

    for i, r in enumerate(links, start=2):
        if r["from_id"] not in known:
            problems.append(
                f"links.csv line ~{i}: from_id {r['from_id']} is unknown"
            )
        if r["to_id"] not in known:
            problems.append(
                f"links.csv line ~{i}: to_id {r['to_id']} is unknown"
            )
        if r["basis"] and r["basis"] not in BASIS:
            problems.append(
                f"links.csv line ~{i}: bad basis {r['basis']!r}"
            )

    for r in health:
        if r["person_id"] not in pids:
            problems.append(
                f"health_history: person {r['person_id']} is missing"
            )
        if r["health"] and r["health"] not in HEALTH_VALUES:
            problems.append(
                f"health_history: bad health {r['health']!r} for {r['person_id']}"
            )

    # name collisions without qualifier hint
    by_name: dict[str, list[str]] = {}
    for r in people:
        by_name.setdefault(r["name"].strip().lower(), []).append(r["person_id"])
    for name, ids in by_name.items():
        if len(ids) > 1:
            warnings.append(
                f"Duplicate display name {name!r} on {', '.join(ids)}: "
                "use a qualifier like 'Sarah K. (Acme)' when talking about them."
            )

    if problems:
        print("CHECK FAILED - fix these before refreshing Claudia's Brain:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("CHECK OK - ledger files, headers, IDs and references look sound.")

    if warnings:
        print("Notes (not blocking):")
        for w in warnings:
            print(f"  - {w}")

    return 1 if problems else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claudia_ledger.py",
        description=(
            "Claudia Ledger: init, edit, check, backup/restore, and export "
            "the CSV ledger for Claudia's Brain. Made with Grok Bot."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  export CLAUDIA_HOME=/workspace/claudia-<bot-id>
  export CLAUDIA_BOT_ID=<bot-id>
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" init
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" add person --name "Alex Rivera" --org-id O-0001
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" add commitment --direction "I owe" --person-id P-0001 \\
      --what "Send deck" --due 2026-10-25 --source-link chat:2026-10-24
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID" close --id C-0001 --status done
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID" update commitment --id C-0001 --due 2026-10-28
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID" health snapshot
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" backups
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID" restore 20261024T120000Z
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" check
  python3 claudia_ledger.py --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID" build-brain \\
      -o "$CLAUDIA_HOME/claudia-brain-ready.canvas.tsx"
""".strip(),
    )
    p.add_argument(
        "--home",
        default=None,
        help="This bot's Claudia home (or set CLAUDIA_HOME). Ledger is $HOME/ledger.",
    )
    p.add_argument(
        "--bot-id",
        default=None,
        dest="bot_id",
        help="This bot's id (or set CLAUDIA_BOT_ID). Required for init; checked on writes.",
    )
    p.add_argument(
        "--ledger",
        default=None,
        help="Optional override of the ledger folder (default: $CLAUDIA_HOME/ledger)",
    )
    p.add_argument(
        "--backup-keep",
        type=int,
        default=None,
        help=(
            f"How many backups to keep (default {BACKUP_KEEP}, "
            "or CLAUDIA_BACKUP_KEEP)"
        ),
    )

    sub = p.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create empty starter CSVs in the ledger folder")
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Create only missing files if some already exist",
    )
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser(
        "check",
        help="Self-check the ledger; exit 1 if problems need fixing",
    )
    p_check.set_defaults(func=cmd_check)

    # add
    p_add = sub.add_parser("add", help="Add a person, org, project, commitment, interaction, or link")
    add_sub = p_add.add_subparsers(dest="entity", required=True)

    ap = add_sub.add_parser("person", help="Add a person (assigns next P-####)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--aliases", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--phone", default="")
    ap.add_argument("--handle", default="")
    ap.add_argument("--network", default="")
    ap.add_argument("--relationship", default="")
    ap.add_argument("--org-id", dest="org_id", default="")
    ap.add_argument("--role", default="")
    ap.add_argument("--city", default="")
    ap.add_argument("--how-met", dest="how_met", default="")
    ap.add_argument("--cadence-days", dest="cadence_days", type=int, default=None)
    ap.add_argument("--cadence-source", dest="cadence_source", default="default")
    ap.add_argument("--last-contact", dest="last_contact", default="")
    ap.add_argument(
        "--force-new",
        action="store_true",
        help="Create even if name/email/phone/handle looks like a duplicate",
    )
    ap.set_defaults(func=cmd_add_person)

    ao = add_sub.add_parser("org", help="Add an organization (assigns next O-####)")
    ao.add_argument("--name", required=True)
    ao.add_argument("--aliases", default="")
    ao.add_argument("--domains", default="")
    ao.add_argument("--force-new", action="store_true")
    ao.set_defaults(func=cmd_add_org)

    aj = add_sub.add_parser("project", help="Add a project (assigns next J-####)")
    aj.add_argument("--name", required=True)
    aj.add_argument("--aliases", default="")
    aj.add_argument("--org-id", dest="org_id", default="")
    aj.add_argument("--status", default="active")
    aj.set_defaults(func=cmd_add_project)

    ac = add_sub.add_parser("commitment", help="Add a promise (assigns next C-####)")
    ac.add_argument("--direction", required=True, choices=DIRECTIONS)
    ac.add_argument("--person-id", dest="person_id", required=True)
    ac.add_argument("--what", required=True)
    ac.add_argument(
        "--due",
        default="",
        help="Due date YYYY-MM-DD (required for open commitments)",
    )
    ac.add_argument("--status", default="open", choices=COMMIT_STATUSES)
    ac.add_argument("--source-link", dest="source_link", required=True)
    ac.set_defaults(func=cmd_add_commitment)

    ai = add_sub.add_parser("interaction", help="Add an interaction (assigns next I-######)")
    ai.add_argument("--person-id", dest="person_id", required=True)
    ai.add_argument("--date", default="")
    ai.add_argument("--channel", default="")
    ai.add_argument("--actor", default="owner", choices=ACTORS)
    ai.add_argument("--summary", required=True)
    ai.add_argument("--source-link", dest="source_link", default="")
    ai.set_defaults(func=cmd_add_interaction)

    al = add_sub.add_parser("link", help="Add a relationship link")
    al.add_argument("--from-id", dest="from_id", required=True)
    al.add_argument("--to-id", dest="to_id", required=True)
    al.add_argument("--type", required=True)
    al.add_argument("--basis", default="stated", choices=BASIS)
    al.add_argument("--source", default="")
    al.add_argument("--force-new", action="store_true")
    al.set_defaults(func=cmd_add_link)

    # update
    p_upd = sub.add_parser(
        "update", help="Update fields on a person, org, project, or commitment"
    )
    upd_sub = p_upd.add_subparsers(dest="entity", required=True)

    up = upd_sub.add_parser("person")
    up.add_argument("--id", required=True)
    up.add_argument("--name")
    up.add_argument("--aliases")
    up.add_argument("--email")
    up.add_argument("--phone")
    up.add_argument("--handle")
    up.add_argument("--network")
    up.add_argument("--relationship")
    up.add_argument("--org-id", dest="org_id")
    up.add_argument("--role")
    up.add_argument("--city")
    up.add_argument("--how-met", dest="how_met")
    up.add_argument("--cadence-days", dest="cadence_days", type=int)
    up.add_argument("--cadence-source", dest="cadence_source")
    up.add_argument("--cadence-proposed", dest="cadence_proposed")
    up.add_argument("--last-contact", dest="last_contact")
    up.add_argument("--health")
    up.set_defaults(func=cmd_update_person)

    uo = upd_sub.add_parser("org")
    uo.add_argument("--id", required=True)
    uo.add_argument("--name")
    uo.add_argument("--aliases")
    uo.add_argument("--domains")
    uo.set_defaults(func=cmd_update_org)

    uj = upd_sub.add_parser("project")
    uj.add_argument("--id", required=True)
    uj.add_argument("--name")
    uj.add_argument("--aliases")
    uj.add_argument("--org-id", dest="org_id")
    uj.add_argument("--status")
    uj.set_defaults(func=cmd_update_project)

    uc = upd_sub.add_parser(
        "commitment",
        help="Update due/what/person/source_link (use close for status)",
    )
    uc.add_argument("--id", required=True)
    uc.add_argument("--due", default=None)
    uc.add_argument("--what", default=None)
    uc.add_argument("--person-id", dest="person_id", default=None)
    uc.add_argument("--source-link", dest="source_link", default=None)
    uc.set_defaults(func=cmd_update_commitment)

    p_health = sub.add_parser(
        "health",
        help="Relationship health history helpers",
    )
    health_sub = p_health.add_subparsers(dest="health_cmd", required=True)
    p_snap = health_sub.add_parser(
        "snapshot",
        help="Append weekly health_history rows for every person",
    )
    p_snap.set_defaults(func=cmd_health_snapshot)

    p_close = sub.add_parser("close", help="Change a commitment status (done/dropped/renegotiated)")
    p_close.add_argument("--id", required=True)
    p_close.add_argument(
        "--status",
        required=True,
        choices=("done", "dropped", "renegotiated"),
    )
    p_close.set_defaults(func=cmd_close)

    p_merge = sub.add_parser("merge", help="Merge two people (keep lower ID, repoint rows)")
    p_merge.add_argument("--kept", required=True, help="Person ID to keep")
    p_merge.add_argument("--merged", required=True, help="Person ID to fold in")
    p_merge.add_argument("--evidence", required=True)
    p_merge.add_argument(
        "--allow-higher-kept",
        action="store_true",
        help="Allow keeping a higher ID (default: keep the lower ID)",
    )
    p_merge.set_defaults(func=cmd_merge)

    p_forget = sub.add_parser(
        "forget",
        help="Delete a person/org/project/commitment/interaction everywhere (owner-approved)",
    )
    p_forget.add_argument("--id", required=True)
    p_forget.add_argument(
        "--yes",
        action="store_true",
        help="Required. Confirms the owner said yes to forgetting.",
    )
    p_forget.set_defaults(func=cmd_forget)

    p_exp = sub.add_parser(
        "export-brain",
        help=(
            "Export Ledger JSON for the Brain Page (read-only; "
            "no email/phone/notes; does not write CSVs)"
        ),
    )
    p_exp.add_argument("-o", "--output", help="Write JSON to this path (default: stdout)")
    p_exp.add_argument("--owner", help="Owner display name (default: owner.json or You)")
    p_exp.add_argument(
        "--example",
        action="store_true",
        help="Mark the export as example: true",
    )
    p_exp.add_argument(
        "--generated",
        help="Override last-updated date (YYYY-MM-DD); default today UTC",
    )
    p_exp.add_argument(
        "--coverage",
        default=None,
        help="Coverage strip text (sources scanned and through what date)",
    )
    p_exp.add_argument(
        "--coverage-file",
        default=None,
        help="Path to coverage.json or .txt (default: $CLAUDIA_HOME/work/coverage.json)",
    )
    p_exp.set_defaults(func=cmd_export_brain)

    p_build = sub.add_parser(
        "build-brain",
        help="Build a ready-to-publish Brain canvas (template + ledger JSON)",
    )
    p_build.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output .canvas.tsx path",
    )
    p_build.add_argument(
        "--template",
        help=(
            "Local path or URL of claudia-brain.canvas.tsx "
            "(default: $CLAUDIA_HOME/tools/brain/claudia-brain.canvas.tsx, "
            f"else {DEFAULT_BRAIN_URL})"
        ),
    )
    p_build.add_argument("--owner", help="Owner display name")
    p_build.add_argument("--example", action="store_true")
    p_build.add_argument("--generated", help="Override last-updated date (YYYY-MM-DD)")
    p_build.add_argument(
        "--coverage",
        default=None,
        help="Coverage strip text (sources scanned and through what date)",
    )
    p_build.add_argument(
        "--coverage-file",
        default=None,
        help="Path to coverage.json or .txt (default: $CLAUDIA_HOME/work/coverage.json)",
    )
    p_build.set_defaults(func=cmd_build_brain)

    p_backups = sub.add_parser(
        "backups",
        help="List timestamped snapshots under backups/",
    )
    p_backups.set_defaults(func=cmd_backups)

    p_restore = sub.add_parser(
        "restore",
        help="Restore a snapshot (takes a safety snapshot of current state first)",
    )
    p_restore.add_argument(
        "snapshot",
        help="Snapshot folder name under backups/ (see: backups)",
    )
    p_restore.set_defaults(func=cmd_restore)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Honor --backup-keep for this process via the env var read by backup_keep_count.
    if getattr(args, "backup_keep", None) is not None:
        os.environ["CLAUDIA_BACKUP_KEEP"] = str(args.backup_keep)

    home = resolve_home(getattr(args, "home", None))
    args.home_path = home
    if args.ledger:
        args.ledger = str(Path(args.ledger).expanduser().resolve())
    else:
        args.ledger = str(home_ledger_dir(home))

    needs_bot = args.command in WRITE_COMMANDS
    bot_id = resolve_bot_id(getattr(args, "bot_id", None), required=needs_bot)
    args.bot_id = bot_id or None
    # Refuse writes into another bot's ledger. Mismatch always blocks.
    if needs_bot and args.command != "init" and bot_id and Path(args.ledger).exists():
        verify_bot_id(Path(args.ledger), bot_id, hard=True)
    elif (
        not needs_bot
        and bot_id
        and Path(args.ledger).exists()
        and args.command == "check"
    ):
        # check reports mismatch as a problem inside cmd_check; still hard-block
        # nothing here so the full report can list every issue.
        pass

    try:
        return int(args.func(args))
    except LedgerError as e:
        die(str(e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
