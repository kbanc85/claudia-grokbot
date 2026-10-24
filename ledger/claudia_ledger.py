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
from datetime import date, datetime, timedelta, timezone
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
        "due_hint",
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
# export-brain / build-brain / propose-links / doctor / backups are read-only
# against the ledger CSVs (propose-links writes only work/ scratch).
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
        "apply-links",
        "snapshot",
        "cleanup-half-built",
    }
)

# Writes that change people, promises, or links (or restore) need --approved.
APPROVAL_REQUIRED_COMMANDS = frozenset(
    {
        "add",
        "update",
        "close",
        "merge",
        "forget",
        "restore",
        "health",
        "apply-links",
    }
)

APPROVED_FLAG_VALUES = ("yes", "standing-rule", "no-approval-needed")
APPROVED_LOG_VALUES = (
    "yes",
    "no-approval-needed",
    "pending",
    "standing-rule",
)

DIRECTIONS = ("I owe", "owed to me", "Claudia owes", "bot owes")
COMMIT_STATUSES = ("open", "done", "dropped", "renegotiated")
ACTORS = ("owner", "them", "claudia", "both")
BASIS = ("stated", "extracted", "inferred", "owner-confirmed")
CADENCE_SOURCES = ("default", "owner", "learned")
HEALTH_VALUES = ("active", "cooling", "inactive")
PROJECT_STATUSES = ("active", "paused", "done", "dropped")
LINK_TYPES = (
    "works_with",
    "introduced_by",
    "member_of",
    "part_of_project",
)

DEFAULT_CADENCE = 30
DEFAULT_BRAIN_URL = (
    "https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/"
    "brain/claudia-brain.canvas.tsx"
)
LEDGER_START = "/* LEDGER-DATA-START */"
LEDGER_END = "/* LEDGER-DATA-END */"
# Default retention; override with CLAUDIA_BACKUP_KEEP or --backup-keep.
BACKUP_KEEP = 30
# Named snapshot taken automatically before large writes / apply-links.
PRE_BULK_LABEL = "pre-bulk"
PRE_BULK_ROW_THRESHOLD = 10

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# Snapshot folder: YYYYMMDDTHHMMSSZ, optional -N collision, optional -label.
BACKUP_NAME_RE = re.compile(
    r"^(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<suffix>.+))?$"
)
BACKUP_NUMERIC_SUFFIX = re.compile(r"^\d+$")
INTRO_WORDS = re.compile(
    r"\b(?:intro(?:duce|ducing|duction)?|meet|meeting|connecting you|"
    r"connect you|I'd like you to meet)\b",
    re.IGNORECASE,
)
SUBJECT_WORD_RE = re.compile(r"[a-z0-9]{3,}")

# Commitments headers before due_hint was added (1.1.2 and earlier).
COMMITMENTS_HEADERS_LEGACY = [
    "commit_id",
    "direction",
    "person_id",
    "what",
    "due",
    "status",
    "source_link",
    "created",
    "closed",
]


# ---------------------------------------------------------------------------
# Time / paths
# ---------------------------------------------------------------------------

# Tests may inject a clock via CLAUDIA_CLOCK (YYYYMMDDTHHMMSSZ or ISO).
_CLOCK_OVERRIDE: datetime | None = None


def set_clock_override(value: datetime | None) -> None:
    """Test helper: freeze utc_now / stamps. Pass None to clear."""
    global _CLOCK_OVERRIDE
    _CLOCK_OVERRIDE = value


def _clock_from_env() -> datetime | None:
    raw = os.environ.get("CLAUDIA_CLOCK", "").strip()
    if not raw:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                dt = datetime(dt.year, dt.month, dt.day, 12, 0, 0)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise LedgerError(
        "CLAUDIA_CLOCK must be YYYYMMDDTHHMMSSZ, YYYY-MM-DDTHH:MM:SSZ, or YYYY-MM-DD"
    )


def utc_now() -> datetime:
    if _CLOCK_OVERRIDE is not None:
        return _CLOCK_OVERRIDE
    env_clock = _clock_from_env()
    if env_clock is not None:
        return env_clock
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


def safe_copy(src: Path | str, dst: Path | str) -> None:
    """Copy file bytes only; ignore metadata PermissionError/OSError on mounts."""
    src_p = Path(src)
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_p, dst_p)
    try:
        shutil.copystat(src_p, dst_p)
    except (PermissionError, OSError):
        pass


def safe_copytree(src: Path | str, dst: Path | str) -> None:
    """Copy a directory tree using byte-only file copies (no required metadata).

    Implemented with walk + safe_copy so directory copystat is never required
    (some persistent mounts reject utime/chmod on folders too).
    """
    src_p = Path(src)
    dst_p = Path(dst)
    if dst_p.exists():
        raise FileExistsError(str(dst_p))
    dst_p.mkdir(parents=True, exist_ok=False)
    for root, _dirs, files in os.walk(src_p):
        rel = Path(root).relative_to(src_p)
        target_dir = dst_p / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            safe_copy(Path(root) / name, target_dir / name)


def resolve_approved(args: argparse.Namespace, *, default: str | None = None) -> str:
    """Require --approved for approval-gated writes; validate the value."""
    raw = getattr(args, "approved", None)
    if raw is None or str(raw).strip() == "":
        if default is not None:
            raw = default
        else:
            die(
                "pass --approved yes|standing-rule|no-approval-needed. "
                "Use yes only when the owner said yes in chat to this exact change."
            )
    approved = str(raw).strip()
    if approved not in APPROVED_FLAG_VALUES:
        die(
            f"approved must be one of {', '.join(APPROVED_FLAG_VALUES)} "
            f"(got {approved!r})"
        )
    if approved == "standing-rule":
        rule = str(getattr(args, "rule", None) or "").strip()
        if not rule:
            die(
                "standing-rule requires --rule <name> so the log names the "
                "saved owner rule"
            )
    return approved


def owner_dateless_promises(ledger: Path) -> bool:
    """Owner setting dateless_promises (default false). Kamil decides."""
    data = load_owner_json(ledger)
    val = data.get("dateless_promises", False)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)


def with_rule_detail(args: argparse.Namespace, detail: str) -> str:
    """When --approved standing-rule, embed rule=<name> in the log detail."""
    if str(getattr(args, "approved", "") or "").strip() != "standing-rule":
        return detail
    rule = str(getattr(args, "rule", None) or "").strip()
    if not rule:
        return detail
    if f"rule={rule}" in detail:
        return detail
    return f"{detail} rule={rule}"


def tools_version() -> str:
    """VERSION next to this script, or under $CLAUDIA_HOME/tools, or unknown."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "VERSION",
        here.parent / "VERSION",
    ]
    home = os.environ.get("CLAUDIA_HOME", "").strip()
    if home:
        candidates.insert(0, Path(home) / "tools" / "VERSION")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() or "unknown"
    return "unknown"


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
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(json.dumps(dict(data), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


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
        # Migrate pre-1.1.3 commitments.csv (no due_hint) in place.
        if filename == COMMITMENTS and got == COMMITMENTS_HEADERS_LEGACY:
            legacy_rows: list[dict[str, str]] = []
            for raw in reader:
                row = {k: (raw.get(k) or "").strip() for k in COMMITMENTS_HEADERS_LEGACY}
                row["due_hint"] = ""
                legacy_rows.append(row)
            write_rows(ledger, COMMITMENTS, legacy_rows)
            return legacy_rows
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
    """Atomic CSV write: temp file in the same folder, then os.replace."""
    path = ledger / filename
    expected = HEADERS[filename]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=expected, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in expected})
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def parse_backup_name(name: str) -> tuple[str, str | None]:
    """Return (stamp, label_or_None). Numeric collision suffixes are not labels."""
    m = BACKUP_NAME_RE.match(name)
    if not m:
        return name, None
    stamp = m.group("stamp")
    suffix = m.group("suffix")
    if suffix is None:
        return stamp, None
    if BACKUP_NUMERIC_SUFFIX.match(suffix):
        return stamp, None
    # stamp-N-label or stamp-label
    if "-" in suffix:
        first, rest = suffix.split("-", 1)
        if BACKUP_NUMERIC_SUFFIX.match(first):
            return stamp, rest
    return stamp, suffix


def backup_stamp_datetime(name: str) -> datetime | None:
    stamp, _label = parse_backup_name(name)
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def protected_backup_names(stamps: Sequence[Path], *, keep: int) -> dict[str, str]:
    """Map snapshot folder name -> reason it is kept under the retention policy."""
    ordered = sorted(stamps, key=lambda p: p.name)
    keep_why: dict[str, str] = {}
    if not ordered:
        return keep_why
    # 1) very first snapshot
    keep_why[ordered[0].name] = "first snapshot"
    # 2) every named snapshot
    for p in ordered:
        _stamp, label = parse_backup_name(p.name)
        if label:
            keep_why[p.name] = f"named:{label}"
    now = utc_now()
    # 3) newest of each of the last 14 days
    by_day: dict[str, Path] = {}
    for p in ordered:
        dt = backup_stamp_datetime(p.name)
        if dt is None:
            continue
        if (now.date() - dt.date()).days > 13:
            continue
        day_key = dt.date().isoformat()
        prev = by_day.get(day_key)
        if prev is None or p.name > prev.name:
            by_day[day_key] = p
    for p in by_day.values():
        keep_why.setdefault(p.name, "daily (last 14 days)")
    # 4) newest of each of the last 8 weeks (Monday-based week)
    by_week: dict[str, Path] = {}
    for p in ordered:
        dt = backup_stamp_datetime(p.name)
        if dt is None:
            continue
        week = dt.date() - timedelta(days=dt.date().weekday())
        if (now.date() - week).days > 7 * 7:
            continue
        week_key = week.isoformat()
        prev = by_week.get(week_key)
        if prev is None or p.name > prev.name:
            by_week[week_key] = p
    for p in by_week.values():
        keep_why.setdefault(p.name, "weekly (last 8 weeks)")
    # 5) last N recent ones
    for p in ordered[-keep:]:
        keep_why.setdefault(p.name, f"recent (last {keep})")
    return keep_why


def prune_backups(ledger: Path, *, keep: int | None = None) -> None:
    root = backups_dir(ledger)
    if not root.is_dir():
        return
    stamps = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)
    retain = backup_keep_count(keep)
    protected = protected_backup_names(stamps, keep=retain)
    for p in stamps:
        if p.name in protected:
            continue
        shutil.rmtree(p, ignore_errors=True)


def backup_ledger(
    ledger: Path,
    *,
    keep: int | None = None,
    label: str | None = None,
) -> Path:
    """Snapshot CSVs (+ notes/, owner.json) before a write.

    Retention keeps the first snapshot, every named snapshot, newest of each of
    the last 14 days, newest of each of the last 8 weeks, plus the last N
    recent ones. Named snapshots are never pruned automatically.
    """
    src_files = [ledger / name for name in HEADERS]
    owner = ledger / "owner.json"
    existing = [p for p in src_files if p.exists()]
    if owner.exists():
        existing.append(owner)
    if not existing:
        return ledger
    stamp = ts_stamp()
    clean_label = (label or "").strip()
    if clean_label:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*$", clean_label):
            raise LedgerError(
                "snapshot label must be a word (letters, digits, _ or -)"
            )
        dest = backups_dir(ledger) / f"{stamp}-{clean_label}"
    else:
        dest = backups_dir(ledger) / stamp
    # Same-second writes get a numeric suffix so we never collide.
    n = 1
    while dest.exists():
        if clean_label:
            dest = backups_dir(ledger) / f"{stamp}-{n}-{clean_label}"
        else:
            dest = backups_dir(ledger) / f"{stamp}-{n}"
        n += 1
    dest.mkdir(parents=True, exist_ok=False)
    for p in existing:
        safe_copy(p, dest / p.name)
    notes = notes_dir(ledger)
    if notes.is_dir():
        safe_copytree(notes, dest / "notes")
    prune_backups(ledger, keep=keep)
    return dest


def maybe_pre_bulk_snapshot(ledger: Path, row_count: int) -> Path | None:
    """Named pre-bulk snapshot when a write touches more than 10 rows."""
    if row_count <= PRE_BULK_ROW_THRESHOLD:
        return None
    return backup_ledger(ledger, label=PRE_BULK_LABEL)


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
    retain = backup_keep_count()
    protected = protected_backup_names(stamps, keep=retain)
    print(f"backups in {backups_dir(ledger)} (newest last):")
    for p in stamps:
        files = sorted(x.name for x in p.iterdir())
        why = protected.get(p.name, "pending prune")
        _stamp, label = parse_backup_name(p.name)
        label_bit = f" label={label}" if label else ""
        print(f"  {p.name}  ({len(files)} entries){label_bit}  kept: {why}")
    print(f"count: {len(stamps)} (recent keep {retain}; named/first/daily/weekly protected)")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Take a named snapshot that is never pruned automatically."""
    ledger = ensure_ledger_dir(Path(args.ledger))
    label = (args.label or "").strip()
    if not label:
        die("pass --label <word>")
    dest = backup_ledger(ledger, label=label)
    print(f"snapshot {dest.name} (named; never pruned automatically)")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a snapshot. Snapshots the current ledger first."""
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
            safe_copy(candidate, ledger / name)
            restored += 1
    owner_src = src / "owner.json"
    if owner_src.is_file():
        safe_copy(owner_src, ledger / "owner.json")
        restored += 1
    notes_src = src / "notes"
    if notes_src.is_dir():
        dest_notes = notes_dir(ledger)
        if dest_notes.exists():
            shutil.rmtree(dest_notes)
        safe_copytree(notes_src, dest_notes)
        restored += 1
    # Owner-asked restore counts as yes when they passed --approved yes.
    append_log(
        ledger,
        "restore",
        stamp,
        f"restored from backups/{stamp}; pre-restore snapshot {pre.name}",
        approved=approved,
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
    *,
    approved: str,
) -> None:
    """Append one log row. approved is required (no default)."""
    if approved not in APPROVED_LOG_VALUES:
        raise LedgerError(
            f"approved_by_owner must be one of {', '.join(APPROVED_LOG_VALUES)}"
        )
    if approved == "standing-rule" and "rule=" not in detail and "rule:" not in detail:
        raise LedgerError(
            "standing-rule log rows must name the rule in detail (rule=<name>)"
        )
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
            safe_copy(tmpl / name, path)
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
    approved = resolve_approved(args)
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
    append_log(
        ledger,
        "add_person",
        pid,
        with_rule_detail(args, f"Added person {name}"),
        approved=approved,
    )
    print(f"Added {pid}: {name}")
    return 0


def cmd_add_org(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    append_log(
        ledger,
        "add_org",
        oid,
        with_rule_detail(args, f"Added org {name}"),
        approved=approved,
    )
    print(f"Added {oid}: {name}")
    return 0


def cmd_add_project(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    append_log(
        ledger,
        "add_project",
        jid,
        with_rule_detail(args, f"Added project {name}"),
        approved=approved,
    )
    print(f"Added {jid}: {name}")
    return 0


def cmd_add_commitment(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    due_hint = (getattr(args, "due_hint", None) or "").strip()
    status = (args.status or "open").strip()
    if status not in COMMIT_STATUSES:
        die(f"status must be one of {', '.join(COMMIT_STATUSES)}")
    dateless = owner_dateless_promises(ledger)
    if status == "open":
        if due:
            require_iso_date("due", due, allow_empty=False)
            due_hint = ""
        elif dateless:
            if not due_hint:
                due_hint = "no date given"
            # Open promise with no date only when owner setting allows it.
        else:
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
        "due_hint": due_hint,
        "status": status,
        "source_link": source_link,
        "created": today,
        "closed": "" if status == "open" else today,
    }
    rows.append(row)
    write_rows(ledger, COMMITMENTS, rows)
    append_log(
        ledger,
        "add_commitment",
        cid,
        with_rule_detail(args, f"{direction}: {what}"),
        approved=approved,
    )
    who = next(r["name"] for r in read_rows(ledger, PEOPLE) if r["person_id"] == person_id)
    due_bit = f" by {due}" if due else (" (no date given)" if due_hint else "")
    print(f"Added {cid}: {direction} {who}: {what}{due_bit}.")
    return 0


def cmd_add_interaction(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    append_log(
        ledger,
        "add_interaction",
        iid,
        with_rule_detail(args, summary),
        approved=approved,
    )
    print(f"Added {iid}: {when} with {person_id}: {summary}")
    return 0


def cmd_add_link(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    append_log(
        ledger,
        "add_link",
        f"{from_id}->{to_id}",
        with_rule_detail(args, typ),
        approved=approved,
    )
    print(f"Added link {from_id} -[{typ}]-> {to_id}")
    return 0


def _update_row(
    ledger: Path,
    filename: str,
    id_col: str,
    entity_id: str,
    updates: Mapping[str, str],
    action: str,
    *,
    approved: str,
    detail: str | None = None,
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
    append_log(
        ledger,
        action,
        entity_id,
        detail if detail is not None else json.dumps(updates, ensure_ascii=False),
        approved=approved,
    )
    print(f"Updated {entity_id}")
    return 0


def cmd_update_person(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    return _update_row(
        ledger,
        PEOPLE,
        "person_id",
        args.id,
        updates,
        "update_person",
        approved=approved,
        detail=with_rule_detail(args, json.dumps(updates, ensure_ascii=False)),
    )


def cmd_update_org(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
    updates: dict[str, str] = {}
    for field in ("name", "aliases", "domains"):
        val = getattr(args, field, None)
        if val is not None:
            updates[field] = str(val).strip()
    if not updates:
        die("nothing to update")
    return _update_row(
        ledger,
        ORGS,
        "org_id",
        args.id,
        updates,
        "update_org",
        approved=approved,
        detail=with_rule_detail(args, json.dumps(updates, ensure_ascii=False)),
    )


def cmd_update_project(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    return _update_row(
        ledger,
        PROJECTS,
        "project_id",
        args.id,
        updates,
        "update_project",
        approved=approved,
        detail=with_rule_detail(args, json.dumps(updates, ensure_ascii=False)),
    )


def cmd_update_commitment(args: argparse.Namespace) -> int:
    """Update open-promise fields. Use close for status changes."""
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
    updates: dict[str, str] = {}
    if args.due is not None:
        updates["due"] = str(args.due).strip()
    if getattr(args, "due_hint", None) is not None:
        updates["due_hint"] = str(args.due_hint).strip()
    if args.what is not None:
        updates["what"] = str(args.what).strip()
    if args.person_id is not None:
        updates["person_id"] = str(args.person_id).strip()
    if args.source_link is not None:
        updates["source_link"] = str(args.source_link).strip()
    if not updates:
        die(
            "nothing to update; pass --due, --due-hint, --what, --person-id, "
            "and/or --source-link"
        )
    dateless = owner_dateless_promises(ledger)
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
            if status == "open" and updates["due"]:
                require_iso_date("due", updates["due"], allow_empty=False)
            elif status == "open" and not updates["due"]:
                if not dateless:
                    require_iso_date("due", updates["due"], allow_empty=False)
            else:
                require_iso_date("due", updates["due"], allow_empty=True)
        for k, v in updates.items():
            r[k] = v
        if r["status"] == "open" and r["due"]:
            r["due_hint"] = ""
        # Open commitments need a due date unless dateless_promises is on.
        if r["status"] == "open" and not r["due"]:
            if dateless:
                if not (r.get("due_hint") or "").strip():
                    r["due_hint"] = "no date given"
            else:
                die(
                    "due date missing. Ask the owner once; open promises need a due date."
                )
        break
    if not found:
        die(f"{args.id} not found in commitments.csv")
    backup_ledger(ledger)
    write_rows(ledger, COMMITMENTS, rows)
    append_log(
        ledger,
        "update_commitment",
        args.id,
        with_rule_detail(args, json.dumps(updates, ensure_ascii=False)),
        approved=approved,
    )
    print(f"Updated {args.id}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
    append_log(
        ledger,
        "close_commitment",
        args.id,
        with_rule_detail(args, status),
        approved=approved,
    )
    print(f"Closed {args.id} as {status}")
    return 0


def cmd_health_snapshot(args: argparse.Namespace) -> int:
    """Append weekly health_history rows for every person."""
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
    people = read_rows(ledger, PEOPLE)
    if not people:
        print("no people to snapshot")
        return 0
    today = today_iso()
    maybe_pre_bulk_snapshot(ledger, len(people))
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
    append_log(
        ledger,
        "health_snapshot",
        today,
        f"weekly rows for {len(people)} people",
        approved=approved,
    )
    print(f"Wrote weekly health snapshot for {len(people)} people.")
    return 0


# ---------------------------------------------------------------------------
# Merge / forget
# ---------------------------------------------------------------------------


def cmd_merge(args: argparse.Namespace) -> int:
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
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
            "approved_by_owner": approved,
        }
    )
    write_rows(ledger, MERGES, merges)
    append_log(
        ledger,
        "merge",
        mid,
        with_rule_detail(
            args, f"kept {kept}, merged {merged}, repointed {repointed}"
        ),
        approved=approved,
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
    approved = resolve_approved(args)
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

    append_log(
        ledger,
        "forget",
        target,
        with_rule_detail(args, "forgotten on request"),
        approved=approved,
    )
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
        dateless = owner_dateless_promises(ledger)
        if r["status"] == "open" and not r["due"]:
            hint = (r.get("due_hint") or "").strip()
            if dateless and hint:
                warnings.append(
                    f"{r['commit_id']} has no date; ask once a week"
                )
            else:
                problems.append(
                    f"Commitment {r['commit_id']} is open but has no due date: "
                    "ask the owner once."
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

    # Honest approval: warn on legacy rows that claimed owner yes for upkeep.
    # Never rewrite history.
    try:
        log_rows = read_rows(ledger, LOG) if (ledger / LOG).exists() else []
    except LedgerError:
        log_rows = []
    upkeep_actions = {"health_snapshot"}
    for r in log_rows:
        approved = (r.get("approved_by_owner") or "").strip()
        action = (r.get("action") or "").strip()
        if approved and approved not in APPROVED_LOG_VALUES:
            warnings.append(
                f"Log {r.get('log_id')}: unknown approved_by_owner={approved!r}. "
                "Not rewritten."
            )
            continue
        if action in upkeep_actions and approved == "yes":
            warnings.append(
                f"Log {r.get('log_id')}: {action} has approved_by_owner=yes but "
                "looks like internal upkeep (expected no-approval-needed). "
                "Not rewritten."
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
# Doctor / half-built cleanup
# ---------------------------------------------------------------------------


def _home_is_writable(home: Path) -> bool:
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".claudia_write_probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _ledger_file_count(ledger: Path) -> int:
    if not ledger.is_dir():
        return 0
    return sum(1 for name in HEADERS if (ledger / name).is_file())


def _is_half_built_home(home: Path, *, expected_bot_id: str = "") -> tuple[bool, str]:
    """Half-built: tools present but no owner.json, or owner.json bot_id differs."""
    tools = home / "tools"
    ledger = home / "ledger"
    has_tools = tools.is_dir() and any(tools.rglob("*.py"))
    owner_path = ledger / "owner.json"
    if not has_tools and not owner_path.exists() and not ledger.is_dir():
        return False, ""
    if has_tools and not owner_path.exists():
        return True, "tools present but owner.json missing"
    if owner_path.exists():
        data = load_owner_json(ledger)
        stored = str(data.get("bot_id") or "").strip()
        if expected_bot_id and stored and stored != expected_bot_id:
            return True, f"owner.json bot_id={stored!r} differs from {expected_bot_id!r}"
        if has_tools and not stored and expected_bot_id:
            return True, "owner.json missing bot_id while tools are present"
    return False, ""


def _ledger_has_data_rows(ledger: Path) -> bool:
    """True if any CSV has a data row beyond the header."""
    if not ledger.is_dir():
        return False
    for name in HEADERS:
        path = ledger / name
        if not path.is_file():
            continue
        try:
            with path.open(newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header is None:
                    continue
                for row in reader:
                    if any((c or "").strip() for c in row):
                        return True
        except OSError:
            continue
    return False


def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only health report for a Claudia home."""
    home = Path(args.home_path)
    ledger = Path(args.ledger)
    bot_id = resolve_bot_id(getattr(args, "bot_id", None), required=False) or ""
    writable = _home_is_writable(home)
    print(f"home: {home}")
    print(f"writable: {'yes' if writable else 'no'}")
    print(f"tools VERSION: {tools_version()}")
    print(f"ledger file count: {_ledger_file_count(ledger)}")
    half, reason = _is_half_built_home(home, expected_bot_id=bot_id)
    if half:
        print(f"half-built: yes ({reason})")
        print(
            f"NOTE: if this folder failed mid-install, run "
            f"cleanup-half-built --home {home} only when it has no ledger rows."
        )
    else:
        print("half-built: no")
    # Also scan sibling candidate folders under parent when they look like claudia homes.
    parent = home.parent
    extras: list[str] = []
    if parent.is_dir():
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.resolve() == home.resolve():
                continue
            name = child.name.lower()
            if "claudia" not in name and child.name != "claudia":
                continue
            hb, why = _is_half_built_home(child, expected_bot_id=bot_id)
            if hb:
                extras.append(f"{child} ({why})")
    if extras:
        print("other half-built folders:")
        for e in extras:
            print(f"  - {e}")
    return 0


def cmd_cleanup_half_built(args: argparse.Namespace) -> int:
    """Delete a half-built home only when ledger CSVs have no data rows."""
    raw = args.cleanup_dir or str(args.home_path)
    target = Path(raw).expanduser().resolve()
    if not target.exists():
        die(f"folder not found: {target}")
    bot_id = resolve_bot_id(getattr(args, "bot_id", None), required=False) or ""
    half, reason = _is_half_built_home(target, expected_bot_id=bot_id)
    if not half:
        # Still allow cleanup when tools exist without a finished owner.json shape.
        tools = target / "tools"
        ledger = target / "ledger"
        if not (tools.is_dir() or ledger.is_dir()):
            die(f"not a Claudia home (no tools/ or ledger/): {target}")
        if (ledger / "owner.json").exists() and _ledger_has_data_rows(ledger):
            die(
                f"refusing to delete {target}: ledger has data rows beyond headers. "
                "Not half-built empty."
            )
        if (ledger / "owner.json").exists() and not reason:
            # Finished home with matching bot - refuse.
            stored = str(load_owner_json(ledger).get("bot_id") or "").strip()
            if stored and (not bot_id or stored == bot_id):
                die(
                    f"refusing to delete {target}: looks like a finished home "
                    f"(owner.json bot_id={stored!r})"
                )
    ledger = target / "ledger"
    if _ledger_has_data_rows(ledger):
        die(
            f"refusing to delete {target}: ledger has data rows beyond headers. "
            "It is not safe to remove."
        )
    shutil.rmtree(target)
    print(f"deleted half-built folder {target}" + (f" ({reason})" if reason else ""))
    return 0


# ---------------------------------------------------------------------------
# Propose / apply links
# ---------------------------------------------------------------------------


def _person_domain(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].strip()


def _subject_tokens(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "have", "will",
        "your", "about", "into", "just", "been", "were", "they", "them",
        "meeting", "call", "chat", "email", "today", "tomorrow", "week",
    }
    words = SUBJECT_WORD_RE.findall((text or "").lower())
    return {w for w in words if w not in stop and len(w) >= 3}


def _existing_link_keys(ledger: Path) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    if not (ledger / LINKS).exists():
        return keys
    for r in read_rows(ledger, LINKS):
        keys.add((r["from_id"], r["to_id"], r["type"]))
        keys.add((r["to_id"], r["from_id"], r["type"]))
    return keys


def propose_link_candidates(ledger: Path) -> dict[str, Any]:
    """Build link/project candidates from interactions and people (read-only)."""
    people = read_rows(ledger, PEOPLE) if (ledger / PEOPLE).exists() else []
    interactions = (
        read_rows(ledger, INTERACTIONS) if (ledger / INTERACTIONS).exists() else []
    )
    by_id = {p["person_id"]: p for p in people}
    existing = _existing_link_keys(ledger)
    candidates: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    next_id = 1

    def add_cand(
        from_id: str,
        to_id: str,
        typ: str,
        evidence: list[str],
        reason: str,
        *,
        project_name: str = "",
        member_ids: list[str] | None = None,
    ) -> None:
        nonlocal next_id
        if from_id == to_id:
            return
        if not evidence:
            return
        if (from_id, to_id, typ) in existing:
            return
        # De-dupe against candidates already queued.
        for c in candidates:
            if (
                c["from_id"] == from_id
                and c["to_id"] == to_id
                and c["type"] == typ
            ):
                return
            if (
                c["from_id"] == to_id
                and c["to_id"] == from_id
                and c["type"] == typ
                and typ == "works_with"
            ):
                return
        cid = f"LC-{next_id:04d}"
        next_id += 1
        candidates.append(
            {
                "id": cid,
                "from_id": from_id,
                "to_id": to_id,
                "type": typ,
                "basis": "inferred",
                "evidence": evidence[:3],
                "reason": reason,
                "project_name": project_name,
                "member_ids": member_ids or [],
            }
        )

    # 1) Shared thread or meeting (same source_link)
    by_link: dict[str, list[dict[str, str]]] = {}
    for row in interactions:
        link = (row.get("source_link") or "").strip()
        if not link:
            continue
        by_link.setdefault(link, []).append(row)
    for link, rows in by_link.items():
        pids = sorted({r["person_id"] for r in rows if r.get("person_id") in by_id})
        if len(pids) < 2:
            continue
        for i, a in enumerate(pids):
            for b in pids[i + 1 :]:
                add_cand(
                    a,
                    b,
                    "works_with",
                    [link],
                    "shared thread or meeting",
                )

    # 2) Same org domain
    by_domain: dict[str, list[str]] = {}
    for p in people:
        dom = _person_domain(p.get("email") or "")
        if not dom:
            continue
        # Skip public mail hosts so example.com still works in fixtures.
        by_domain.setdefault(dom, []).append(p["person_id"])
    for dom, pids in by_domain.items():
        uniq = sorted(set(pids))
        if len(uniq) < 2:
            continue
        for i, a in enumerate(uniq):
            org_a = by_id[a].get("org_id") or ""
            for b in uniq[i + 1 :]:
                org_b = by_id[b].get("org_id") or ""
                if org_a and org_a == org_b:
                    add_cand(
                        a,
                        org_a,
                        "member_of",
                        [f"domain:{dom}"],
                        f"same org domain {dom}",
                    )
                    add_cand(
                        b,
                        org_b,
                        "member_of",
                        [f"domain:{dom}"],
                        f"same org domain {dom}",
                    )
                add_cand(
                    a,
                    b,
                    "works_with",
                    [f"domain:{dom}"],
                    f"same email domain {dom}",
                )

    # 3) Introductions: interaction text names two people
    name_to_ids: dict[str, list[str]] = {}
    for p in people:
        name_to_ids.setdefault(p["name"].strip().lower(), []).append(p["person_id"])
        for alias in (p.get("aliases") or "").split(";"):
            alias = alias.strip().lower()
            if alias:
                name_to_ids.setdefault(alias, []).append(p["person_id"])

    for row in interactions:
        summary = row.get("summary") or ""
        if not INTRO_WORDS.search(summary):
            continue
        mentioned: list[str] = []
        lower = summary.lower()
        for name, ids in name_to_ids.items():
            if name and name in lower:
                mentioned.extend(ids)
        mentioned = sorted(set(mentioned))
        actor_pid = row.get("person_id") or ""
        evidence = [row.get("source_link") or row.get("int_id") or ""]
        evidence = [e for e in evidence if e]
        if len(mentioned) >= 2:
            a, b = mentioned[0], mentioned[1]
            introducer = actor_pid if actor_pid in by_id else a
            other = b if introducer != b else a
            add_cand(
                other,
                introducer,
                "introduced_by",
                evidence,
                "introduction wording in interaction",
            )
            add_cand(
                a,
                b,
                "works_with",
                evidence,
                "introduced to each other",
            )

    # 4) Repeated subject words across 3+ interactions with 2+ people -> project
    token_map: dict[str, list[dict[str, str]]] = {}
    for row in interactions:
        for tok in _subject_tokens(row.get("summary") or ""):
            token_map.setdefault(tok, []).append(row)
    for tok, rows in token_map.items():
        if len(rows) < 3:
            continue
        pids = sorted({r["person_id"] for r in rows if r.get("person_id") in by_id})
        if len(pids) < 2:
            continue
        evidence = []
        for r in rows:
            sl = (r.get("source_link") or "").strip()
            if sl and sl not in evidence:
                evidence.append(sl)
            if len(evidence) >= 3:
                break
        if not evidence:
            evidence = [r.get("int_id") or "" for r in rows[:3]]
            evidence = [e for e in evidence if e]
        if not evidence:
            continue
        proj_name = f"Example {tok.title()} Project"
        # Prefer a readable multi-word subject if the token looks like a title fragment.
        proj_name = f"{tok.replace('_', ' ').title()} Work"
        projects.append(
            {
                "id": f"JP-{len(projects)+1:04d}",
                "name": proj_name,
                "member_ids": pids,
                "evidence": evidence[:3],
                "reason": f"repeated subject '{tok}' across {len(rows)} interactions",
            }
        )
        # Candidate project id placeholder used in links until apply creates J-####.
        temp_jid = f"NEW-PROJECT:{proj_name}"
        for pid in pids:
            add_cand(
                pid,
                temp_jid,
                "part_of_project",
                evidence[:3],
                f"repeated subject '{tok}'",
                project_name=proj_name,
                member_ids=pids,
            )
        for i, a in enumerate(pids):
            for b in pids[i + 1 :]:
                add_cand(
                    a,
                    b,
                    "works_with",
                    evidence[:3],
                    f"shared project subject '{tok}'",
                    project_name=proj_name,
                    member_ids=pids,
                )

    question = _group_link_question(candidates, projects, by_id)
    return {
        "candidates": candidates,
        "projects": projects,
        "question": question,
        "generated": ts_iso(),
    }


def _group_link_question(
    candidates: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    by_id: Mapping[str, Mapping[str, str]],
) -> str:
    if not candidates and not projects:
        return "No link candidates found."
    bits: list[str] = []
    # Project-centric summary
    for proj in projects:
        names = [
            by_id[pid]["name"]
            for pid in proj.get("member_ids") or []
            if pid in by_id
        ]
        if names:
            bits.append(
                f"these {len(names)} people work together on the {proj['name']}"
            )
    intro = [
        c
        for c in candidates
        if c["type"] == "introduced_by"
    ]
    for c in intro[:2]:
        # introduced_by: from_id was introduced, to_id is the introducer.
        introduced = by_id.get(c["from_id"], {}).get("name", c["from_id"])
        introducer = by_id.get(c["to_id"], {}).get("name", c["to_id"])
        bits.append(f"{introducer} introduced you to {introduced}")
    if not bits:
        bits.append(f"{len(candidates)} possible links from shared threads and domains")
    n = len(candidates)
    joined = ", and ".join(bits[:3])
    return (
        f"I think {joined}. Add these {n} links? (yes / pick / no)"
    )


def cmd_propose_links(args: argparse.Namespace) -> int:
    """Read-only link proposals written only to work/link-candidates.json."""
    home = Path(args.home_path)
    ledger = ensure_ledger_dir(Path(args.ledger))
    work = home / "work"
    work.mkdir(parents=True, exist_ok=True)
    payload = propose_link_candidates(ledger)
    out = work / "link-candidates.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(payload['candidates'])} candidates)")
    print(payload["question"])
    return 0


def cmd_apply_links(args: argparse.Namespace) -> int:
    """Apply picked link candidates with basis owner-confirmed."""
    home = Path(args.home_path)
    ledger = ensure_ledger_dir(Path(args.ledger))
    approved = resolve_approved(args)
    path = Path(args.file).expanduser()
    if not path.is_file():
        # Allow relative to home/work
        alt = home / args.file
        path = alt if alt.is_file() else path
    if not path.is_file():
        die(f"candidates file not found: {args.file}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"invalid candidates JSON: {e}")
    candidates = list(payload.get("candidates") or [])
    projects = list(payload.get("projects") or [])
    pick_raw = (args.pick or "").strip()
    if not pick_raw:
        die("pass --pick all or --pick LC-0001,LC-0002")
    if pick_raw.lower() == "all":
        chosen = candidates
    else:
        wanted = {x.strip() for x in pick_raw.split(",") if x.strip()}
        chosen = [c for c in candidates if c.get("id") in wanted]
        missing = wanted - {c.get("id") for c in chosen}
        if missing:
            die(f"unknown candidate ids: {', '.join(sorted(missing))}")
    if not chosen:
        print("nothing to apply")
        return 0

    maybe_pre_bulk_snapshot(ledger, max(len(chosen), PRE_BULK_ROW_THRESHOLD + 1))
    # Create projects referenced by chosen part_of_project candidates.
    created_projects: dict[str, str] = {}
    project_rows = read_rows(ledger, PROJECTS) if (ledger / PROJECTS).exists() else []
    for c in chosen:
        pname = (c.get("project_name") or "").strip()
        if not pname or c.get("type") != "part_of_project":
            continue
        if pname in created_projects:
            continue
        existing = next(
            (r for r in project_rows if r["name"].strip().lower() == pname.lower()),
            None,
        )
        if existing:
            created_projects[pname] = existing["project_id"]
            continue
        jid = allocate_id(ledger, PROJECTS)
        project_rows.append(
            {
                "project_id": jid,
                "name": pname,
                "aliases": "",
                "org_id": "",
                "status": "active",
            }
        )
        created_projects[pname] = jid
        append_log(
            ledger,
            "add_project",
            jid,
            with_rule_detail(args, f"Added project {pname} via apply-links"),
            approved=approved,
        )
    if created_projects:
        write_rows(ledger, PROJECTS, project_rows)

    link_rows = read_rows(ledger, LINKS) if (ledger / LINKS).exists() else []
    existing = {(r["from_id"], r["to_id"], r["type"]) for r in link_rows}
    applied = 0
    for c in chosen:
        from_id = str(c.get("from_id") or "")
        to_id = str(c.get("to_id") or "")
        typ = str(c.get("type") or "")
        pname = (c.get("project_name") or "").strip()
        if to_id.startswith("NEW-PROJECT:"):
            to_id = created_projects.get(pname) or created_projects.get(
                to_id.split(":", 1)[-1], ""
            )
            if not to_id:
                # Create on the fly if project list missed it.
                if pname:
                    jid = allocate_id(ledger, PROJECTS)
                    project_rows.append(
                        {
                            "project_id": jid,
                            "name": pname,
                            "aliases": "",
                            "org_id": "",
                            "status": "active",
                        }
                    )
                    write_rows(ledger, PROJECTS, project_rows)
                    created_projects[pname] = jid
                    to_id = jid
        if not from_id or not to_id or not typ:
            continue
        if (from_id, to_id, typ) in existing:
            continue
        evidence = c.get("evidence") or []
        source = "; ".join(evidence[:3]) if isinstance(evidence, list) else str(evidence)
        link_rows.append(
            {
                "from_id": from_id,
                "to_id": to_id,
                "type": typ,
                "basis": "owner-confirmed",
                "source": source,
            }
        )
        existing.add((from_id, to_id, typ))
        applied += 1
        append_log(
            ledger,
            "apply_links",
            f"{from_id}->{to_id}",
            with_rule_detail(
                args, f"{typ}; candidate {c.get('id')}; {c.get('reason')}"
            ),
            approved=approved,
        )
    write_rows(ledger, LINKS, link_rows)
    print(f"applied {applied} links (owner-confirmed)")
    return 0



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
    p.add_argument(
        "--approved",
        choices=APPROVED_FLAG_VALUES,
        default=None,
        help=(
            "Required for writes that change people, promises, or links. "
            "yes = owner said yes in chat; standing-rule = saved owner rule "
            "(pass --rule); no-approval-needed = internal upkeep."
        ),
    )
    p.add_argument(
        "--rule",
        default=None,
        help="Saved owner rule name (required with --approved standing-rule)",
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
        help="Due date YYYY-MM-DD (required for open commitments unless dateless_promises)",
    )
    ac.add_argument(
        "--due-hint",
        dest="due_hint",
        default="",
        help='When dateless_promises is on: use "no date given" for open promises without a due',
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
    uc.add_argument("--due-hint", dest="due_hint", default=None)
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

    p_snap = sub.add_parser(
        "snapshot",
        help="Take a named snapshot that is never pruned automatically",
    )
    p_snap.add_argument("--label", required=True, help="Word label for the snapshot folder")
    p_snap.set_defaults(func=cmd_snapshot)

    p_doctor = sub.add_parser(
        "doctor",
        help="Read-only report: home path, writable, VERSION, ledger files, half-built",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_clean = sub.add_parser(
        "cleanup-half-built",
        help="Delete a half-built Claudia folder only if it has no ledger data rows",
    )
    p_clean.add_argument(
        "cleanup_dir",
        nargs="?",
        default=None,
        help="Folder to delete (default: --home)",
    )
    p_clean.set_defaults(func=cmd_cleanup_half_built)

    p_prop = sub.add_parser(
        "propose-links",
        help="Propose relationship links into work/link-candidates.json (read-only ledger)",
    )
    p_prop.set_defaults(func=cmd_propose_links)

    p_apply = sub.add_parser(
        "apply-links",
        help="Write picked link candidates with basis owner-confirmed",
    )
    p_apply.add_argument(
        "--file",
        default="work/link-candidates.json",
        help="Candidates JSON (default: work/link-candidates.json under --home)",
    )
    p_apply.add_argument(
        "--pick",
        required=True,
        help="all, or comma-separated candidate ids (LC-0001,LC-0002)",
    )
    p_apply.set_defaults(func=cmd_apply_links)

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
    # cleanup-half-built may target a half-built folder with a mismatched id.
    if (
        needs_bot
        and args.command not in {"init", "cleanup-half-built"}
        and bot_id
        and Path(args.ledger).exists()
    ):
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
