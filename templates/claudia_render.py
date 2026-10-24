#!/usr/bin/env python3
# Made with Grok Bot.
"""Fill Claudia message templates from JSON or the ledger.

Missing placeholders fail loudly (exit 1). Extra data keys are ignored.

Examples:
  export CLAUDIA_HOME=/workspace/claudia-<bot-id>
  python3 claudia_render.py --home "$CLAUDIA_HOME" --template morning_brief --data vars.json
  python3 claudia_render.py --home "$CLAUDIA_HOME" --template promise_catch
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
COMMENT = re.compile(r"^#.*$", re.MULTILINE)

TEMPLATES = {
    "morning_brief": "morning_brief.txt",
    "promise_catch": "promise_catch.txt",
    "meditate": "meditate.txt",
    "weekly_review": "weekly_review.txt",
    "welcome_tour": "welcome_tour.txt",
}

COMMITMENTS = "commitments.csv"
PEOPLE = "people.csv"


class RenderError(Exception):
    """Raised when a template cannot be filled."""


def templates_dir() -> Path:
    return Path(__file__).resolve().parent


def load_template(name: str) -> str:
    if name not in TEMPLATES:
        known = ", ".join(sorted(TEMPLATES))
        raise RenderError(f"unknown template {name!r}; known: {known}")
    path = templates_dir() / TEMPLATES[name]
    if not path.is_file():
        raise RenderError(f"template file missing: {path}")
    raw = path.read_text(encoding="utf-8")
    # Strip comment lines; keep blank lines that structure the message.
    lines = []
    for line in raw.splitlines():
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def required_placeholders(text: str) -> list[str]:
    found: list[str] = []
    for match in PLACEHOLDER.finditer(text):
        key = match.group(1)
        if key not in found:
            found.append(key)
    return found


def render(text: str, data: Mapping[str, Any]) -> str:
    missing = [k for k in required_placeholders(text) if k not in data]
    if missing:
        raise RenderError(
            "missing placeholders: " + ", ".join(missing)
            + ". Provide every {name} listed in the template, or the render fails."
        )

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = data[key]
        if val is None:
            return ""
        return str(val)

    return PLACEHOLDER.sub(repl, text)


def load_json_data(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RenderError(f"bad JSON in {path}: {e}") from e
    if not isinstance(obj, dict):
        raise RenderError(f"{path} must be a JSON object of placeholder -> value")
    return obj


def read_csv_rows(ledger: Path, name: str) -> list[dict[str, str]]:
    path = ledger / name
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def data_from_ledger(ledger: Path, template: str) -> dict[str, Any]:
    """Build a sensible default map from live ledger CSVs for supported templates."""
    today = datetime.now(timezone.utc).date()
    people = read_csv_rows(ledger, PEOPLE)
    commits = read_csv_rows(ledger, COMMITMENTS)
    open_commits = [c for c in commits if c.get("status", "open") == "open"]
    i_owe = [c for c in open_commits if c.get("direction") == "I owe"]
    owed = [c for c in open_commits if c.get("direction") == "owed to me"]
    by_id = {p.get("person_id", ""): p for p in people}

    def person_name(pid: str) -> str:
        return by_id.get(pid, {}).get("name", pid or "someone")

    def due_bucket(due: str) -> str:
        if not due:
            return "NO DUE"
        try:
            d = date.fromisoformat(due)
        except ValueError:
            return f"DUE {due}"
        delta = (d - today).days
        if delta < 0:
            return f"OVERDUE {abs(delta)}d"
        if delta == 0:
            return "TODAY"
        if delta <= 2:
            return f"IN {delta}d"
        return f"DUE {due}"

    def fmt_commit(c: dict[str, str]) -> str:
        who = person_name(c.get("person_id", ""))
        return (
            f"- [{due_bucket(c.get('due', ''))}] {c.get('what', '')} "
            f"({c.get('direction', '')} {who}, source {c.get('source_link', '')})"
        )

    urgent = []
    for c in open_commits:
        due = c.get("due", "")
        if not due:
            urgent.append(c)
            continue
        try:
            d = date.fromisoformat(due)
        except ValueError:
            urgent.append(c)
            continue
        if (d - today).days <= 2:
            urgent.append(c)

    if template == "morning_brief":
        needs = "\n".join(fmt_commit(c) for c in urgent) or "- Nothing pressing."
        watch_bits = []
        for p in people:
            health = p.get("health", "")
            if health in ("cooling", "inactive"):
                watch_bits.append(
                    f"- {p.get('name', '')}: health {health}, "
                    f"last contact {p.get('last_contact') or 'unknown'}"
                )
        return {
            "day_date": today.strftime("%A, %Y-%m-%d"),
            "coverage_seeing": "ledger",
            "coverage_not_seeing": "(connect apps for live sources)",
            "needs_attention": needs,
            "watch": "\n".join(watch_bits) or "- No cooling relationships flagged.",
            "calendar_summary": "calendar not scanned here",
            "calendar_items": "- (add calendar items from live sources)",
            "brain_link": "(Brain Page link)",
            "one_question": "What should we protect time for today?",
        }

    if template == "promise_catch":
        top = open_commits[:3]
        lines = []
        for c in top:
            lines.append(
                f"- {c.get('what', '')} ({c.get('direction', '')} "
                f"{person_name(c.get('person_id', ''))}, due {c.get('due') or 'unset'})"
            )
        return {
            "count_total": str(len(open_commits)),
            "count_i_owe": str(len(i_owe)),
            "count_owed_to_me": str(len(owed)),
            "count_people": str(len(people)),
            "top_items": "\n".join(lines) or "- No open promises yet.",
            "brain_link": "(Brain Page link)",
            "ask_line": "Tap anyone to see the story. Anything I got wrong or missed?",
        }

    if template == "meditate":
        return {
            "gap_line": "Clean day; nothing to change",
            "proposed_summary": "no rule changes",
            "pattern": "(fill from today's Claudia_Log)",
            "page_link": "(The Gap journal Page link)",
        }

    if template == "weekly_review":
        overdue = [
            c
            for c in open_commits
            if c.get("due")
            and parse_safe(c["due"]) is not None
            and parse_safe(c["due"]) < today  # type: ignore[operator]
        ]
        return {
            "headline_1": f"Open promises: {len(open_commits)} ({len(overdue)} overdue)",
            "headline_2": f"People in ledger: {len(people)}",
            "headline_3": "Relationship health: recompute with claudia-ledger before publishing",
            "page_link": "(Weekly review Page link)",
            "decisions": "Any merges, cadence changes, or recovery plans to approve?",
        }

    if template == "welcome_tour":
        owner = ledger / "owner.json"
        name = "there"
        if owner.is_file():
            try:
                obj = json.loads(owner.read_text(encoding="utf-8"))
                if isinstance(obj, dict) and obj.get("name"):
                    name = str(obj["name"])
            except json.JSONDecodeError:
                pass
        return {"name": name}

    raise RenderError(f"ledger fill not defined for template {template!r}; pass --data")


def parse_safe(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


HOME_HELP = (
    "Set CLAUDIA_HOME to this bot's own folder, or pass --home. "
    "Example: export CLAUDIA_HOME=/workspace/claudia-<bot-id> "
    "(or /cursor/stores/<its-store>/claudia). "
    "There is no shared default."
)


def resolve_home(cli_home: str | None) -> Path:
    raw = (cli_home or "").strip() or os.environ.get("CLAUDIA_HOME", "").strip()
    if not raw:
        raise RenderError(HOME_HELP)
    return Path(raw).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claudia_render.py",
        description=(
            "Fill Claudia message templates from JSON or the ledger. "
            "Missing placeholders fail loudly. Made with Grok Bot."
        ),
    )
    p.add_argument(
        "--home",
        default=None,
        help="This bot's Claudia home (or set CLAUDIA_HOME). Ledger is $HOME/ledger.",
    )
    p.add_argument(
        "--template",
        required=True,
        choices=sorted(TEMPLATES),
        help="Template name (morning_brief, promise_catch, meditate, weekly_review, welcome_tour)",
    )
    p.add_argument(
        "--data",
        help="JSON object file of placeholder -> value",
    )
    p.add_argument(
        "--ledger",
        help="Optional ledger override (default: $CLAUDIA_HOME/ledger)",
    )
    p.add_argument(
        "-o",
        "--output",
        help="Write rendered text here (default: stdout)",
    )
    p.add_argument(
        "--list-placeholders",
        action="store_true",
        help="Print required placeholders for the template and exit",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # Home is always required so templates stay per-bot even with --data.
        home = resolve_home(args.home)
        text = load_template(args.template)
        if args.list_placeholders:
            print("\n".join(required_placeholders(text)))
            return 0
        if args.data:
            data = load_json_data(Path(args.data))
        else:
            ledger = (
                Path(args.ledger).expanduser().resolve()
                if args.ledger
                else home / "ledger"
            )
            data = data_from_ledger(ledger, args.template)
        out = render(text, data)
    except RenderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
