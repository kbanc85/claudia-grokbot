#!/usr/bin/env python3
# Made with Grok Bot.
"""Propose commitment candidates from normalized source items (JSONL).

Conservative: precision over recall. Only proposes; the bot judges and
records via claudia_ledger.py.

Input schema (one JSON object per line):
  id       string  stable item id
  source   string  email | calendar | call | slack | chat | other
  date     string  YYYY-MM-DD (anchor for relative due dates)
  from     string  sender / speaker display name or address
  to       string  recipient(s); comma-separated ok
  subject  string  subject or title (may be empty)
  text     string  body / snippet
  link     string  provenance URL or id

Output schema (JSON array to stdout, or JSONL with --jsonl):
  item_id, source, link, quote, who_owes, owed_to, what, due,
  direction (I owe | owed to me | third party), confidence (0..1),
  reasons (list of strings)

Examples:
  python3 claudia_scan.py --input items.jsonl
  python3 claudia_scan.py --input items.jsonl --owner-name "Sam Chen" --owner-email sam@example.com
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Relative date resolution (against the item's date)
# ---------------------------------------------------------------------------

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

RELATIVE = re.compile(
    r"\b("
    r"today|tonight|tomorrow|eod|end of day|end of the day|"
    r"next week|this week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"next monday|next tuesday|next wednesday|next thursday|"
    r"next friday|next saturday|next sunday|"
    r"in \d+ days?"
    r")\b",
    re.IGNORECASE,
)

ISO_IN_TEXT = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
MONTH_DAY = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?\b",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def parse_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def next_weekday(anchor: date, weekday: int, *, force_next: bool = False) -> date:
    """Upcoming weekday. Includes today unless force_next is set."""
    delta = (weekday - anchor.weekday()) % 7
    if delta == 0 and force_next:
        delta = 7
    return anchor + timedelta(days=delta)


def resolve_due(anchor: date, text: str) -> tuple[str | None, str | None]:
    """Return (YYYY-MM-DD or None, reason or None)."""
    m = ISO_IN_TEXT.search(text)
    if m:
        return m.group(1), "iso date in text"

    m = MONTH_DAY.search(text)
    if m:
        mon = MONTHS[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else anchor.year
        try:
            d = date(year, mon, day)
        except ValueError:
            d = None
        if d is not None:
            if d < anchor and m.group(3) is None:
                try:
                    d = date(year + 1, mon, day)
                except ValueError:
                    d = None
            if d is not None:
                return d.isoformat(), "month-day in text"

    m = RELATIVE.search(text)
    if not m:
        return None, None
    token = m.group(1).lower()
    if token in ("today", "tonight", "eod", "end of day", "end of the day"):
        return anchor.isoformat(), f"relative:{token}"
    if token == "tomorrow":
        return (anchor + timedelta(days=1)).isoformat(), "relative:tomorrow"
    if token == "next week":
        # Monday of the following week.
        days = 7 - anchor.weekday()
        if days == 0:
            days = 7
        return (anchor + timedelta(days=days)).isoformat(), "relative:next week"
    if token == "this week":
        friday = next_weekday(anchor, 4, force_next=False)
        if friday < anchor:
            friday = anchor
        return friday.isoformat(), "relative:this week"
    if token.startswith("in "):
        n_match = re.search(r"\d+", token)
        if not n_match:
            return None, None
        n = int(n_match.group(0))
        return (anchor + timedelta(days=n)).isoformat(), f"relative:in {n} days"
    force_next = token.startswith("next ")
    day_name = token.replace("next ", "")
    if day_name in WEEKDAYS:
        d = next_weekday(anchor, WEEKDAYS[day_name], force_next=force_next)
        return d.isoformat(), f"relative:{token}"
    return None, None


# Due-date fragment shared by commitment patterns (required for precision).
DUE_FRAG = (
    r"(?P<due>"
    r"(?:by|before|on)\s+[^.\n!?]+|"
    r"tomorrow|today|tonight|eod|end of day|end of the day|next week|this week|"
    r"(?:next\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r"(?:st|nd|rd|th)?(?:,?\s*20\d{2})?|"
    r"20\d{2}-\d{2}-\d{2}"
    r")"
)

# ---------------------------------------------------------------------------
# Conservative promise patterns
# ---------------------------------------------------------------------------

# Explicit first-person commitments with a concrete deliverable cue.
I_OWE_PATTERNS = [
    re.compile(
        r"\bI(?:'ll| will)\s+"
        r"(?P<verb>send|share|deliver|get|have|email|ship|pass along|forward|"
        r"provide|prep(?:are)?|draft|finish|complete|circle back with|follow up with)"
        r"\s+(?P<body>.+?)\s+"
        + DUE_FRAG,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bI(?:'ll| will)\s+(?:have|get)\s+(?P<body>(?:it|that|the\s+\w+)\s+"
        r"(?:to you|ready|done))"
        r"(?:\s+(?P<body2>.+?))?\s+"
        + DUE_FRAG,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bI promise to\s+(?P<body>.+?)\s+" + DUE_FRAG,
        re.IGNORECASE,
    ),
]

# Asks / please / need-you-to with a required due signal.
OWED_TO_ME_PATTERNS = [
    re.compile(
        r"\b(?:can|could|would)\s+you\s+"
        r"(?P<verb>send|share|deliver|get|email|provide|prep(?:are)?|draft|"
        r"finish|complete|review|approve)"
        r"\s+(?P<body>.+?)\s+"
        + DUE_FRAG,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bplease\s+"
        r"(?P<verb>send|share|deliver|get|email|provide|review|approve)"
        r"\s+(?P<body>.+?)\s+"
        + DUE_FRAG,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bI need you to\s+(?P<body>.+?)\s+" + DUE_FRAG,
        re.IGNORECASE,
    ),
]

# Third-party: "Jordan will send Priya the deck Friday"
THIRD_PARTY = re.compile(
    r"\b(?P<who>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:will|'ll)\s+"
    r"(send|share|deliver|get|email|provide|finish)\b"
    r"(?P<body>[^.\n!?]*?)"
    r"(?P<due>\b(?:by|before|on)\s+[^.\n!?]+|\btomorrow\b|\bfriday\b|\bnext week\b|"
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b)?",
)

# Reject patterns (vague / done / questions / hypotheticals)
REJECT = [
    (re.compile(r"\blet'?s\s+catch\s+up\b", re.I), "vague catch-up"),
    (re.compile(r"\bwe should\s+(?:sync|chat|talk|catch up)\b", re.I), "vague intention"),
    (re.compile(r"\bwould be great if\b", re.I), "soft wish"),
    (re.compile(r"\bif we (?:had|have) time\b", re.I), "hypothetical"),
    (re.compile(r"\bmaybe I(?:'ll| will)\b", re.I), "hypothetical maybe"),
    (re.compile(r"\bI(?:'ll| will) try to\b", re.I), "weak try"),
    (re.compile(r"\bI already (?:sent|shared|delivered|emailed|finished)\b", re.I), "already done"),
    (re.compile(r"\bI (?:sent|shared|delivered|emailed)\b.+\b(yesterday|earlier|last week)\b", re.I), "already done"),
    (re.compile(r"\bjust making sure\b", re.I), "status question"),
    # Pure status questions only. "Can/could you … by Friday?" is an ask, not rejected here.
    (re.compile(r"^\s*(?:did|have|has|was|were|is|are)\b.+\?\s*$", re.I), "question only"),
]

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class Candidate:
    item_id: str
    source: str
    link: str
    quote: str
    who_owes: str
    owed_to: str
    what: str
    due: str | None
    direction: str
    confidence: float
    reasons: list[str] = field(default_factory=list)


def normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def owner_match(value: str, owner_names: Sequence[str], owner_emails: Sequence[str]) -> bool:
    v = value.strip().lower()
    if not v:
        return False
    for e in owner_emails:
        if e and e.lower() in v:
            return True
    for n in owner_names:
        if n and n.lower() in v:
            return True
    return False


def clean_what(verb: str, body: str) -> str:
    body = re.sub(r"\s+", " ", (body or "").strip(" ,;:-"))
    # Drop trailing due clauses already captured separately.
    body = re.sub(
        r"\b(?:by|before|on)\s+.+$",
        "",
        body,
        flags=re.IGNORECASE,
    ).strip(" ,;:-")
    body = re.sub(
        r"\b(?:tomorrow|today|tonight|eod|next week|this week|"
        r"(?:mon|tues|wednes|thurs|fri|satur|sun)day)\s*$",
        "",
        body,
        flags=re.IGNORECASE,
    ).strip(" ,;:-")
    if not body:
        return verb.strip()
    if body.lower().startswith(verb.lower()):
        return body
    return f"{verb} {body}".strip()


def reject_sentence(sentence: str) -> str | None:
    for pat, reason in REJECT:
        if pat.search(sentence):
            return reason
    return None


def scan_item(
    item: MappingLike,
    *,
    owner_names: Sequence[str],
    owner_emails: Sequence[str],
) -> list[Candidate]:
    text = str(item.get("text") or "")
    subject = str(item.get("subject") or "")
    blob = f"{subject}. {text}".strip()
    anchor = parse_iso(str(item.get("date") or "")) or date.today()
    frm = normalize_name(str(item.get("from") or ""))
    to = normalize_name(str(item.get("to") or ""))
    item_id = str(item.get("id") or "")
    source = str(item.get("source") or "other")
    link = str(item.get("link") or "")

    from_is_owner = owner_match(frm, owner_names, owner_emails)
    to_is_owner = owner_match(to, owner_names, owner_emails)

    out: list[Candidate] = []
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(blob) if s.strip()]

    for sentence in sentences:
        why = reject_sentence(sentence)
        if why:
            continue

        # I owe patterns
        for pat in I_OWE_PATTERNS:
            m = pat.search(sentence)
            if not m:
                continue
            gd = m.groupdict()
            verb = gd.get("verb") or "do"
            body = (gd.get("body") or "") + " " + (gd.get("body2") or "")
            due_chunk = (gd.get("due") or "") + " " + sentence
            due, due_reason = resolve_due(anchor, due_chunk)
            if due is None:
                continue
            what = clean_what(verb, body)
            if len(what.split()) < 2:
                continue
            reasons = ["explicit first-person commitment", "due date signal"]
            if due_reason:
                reasons.append(due_reason)
            if from_is_owner:
                who_owes = owner_names[0] if owner_names else frm or "owner"
                owed_to = to.split(",")[0].strip() if to else "them"
                direction = "I owe"
                reasons.append("from matches owner")
            elif to_is_owner:
                who_owes = frm or "them"
                owed_to = owner_names[0] if owner_names else "owner"
                direction = "owed to me"
                reasons.append("counterparty promised owner")
            else:
                continue
            out.append(
                Candidate(
                    item_id=item_id,
                    source=source,
                    link=link,
                    quote=sentence.strip(),
                    who_owes=who_owes,
                    owed_to=owed_to,
                    what=what,
                    due=due,
                    direction=direction,
                    confidence=0.9,
                    reasons=reasons,
                )
            )
            break
        else:
            # Asks aimed at a party involving the owner
            for pat in OWED_TO_ME_PATTERNS:
                m = pat.search(sentence)
                if not m:
                    continue
                if not to_is_owner and not from_is_owner:
                    continue
                gd = m.groupdict()
                verb = gd.get("verb") or ""
                body = gd.get("body") or ""
                due_chunk = (gd.get("due") or "") + " " + sentence
                due, due_reason = resolve_due(anchor, due_chunk)
                if due is None:
                    continue
                what = clean_what(verb or "do", body)
                if len(what.split()) < 2:
                    continue
                reasons = ["explicit ask with deliverable", "due date signal"]
                if due_reason:
                    reasons.append(due_reason)
                if from_is_owner:
                    # Owner asking counterparty → owed to me
                    direction = "owed to me"
                    who_owes = to.split(",")[0].strip() if to else "them"
                    owed_to = owner_names[0] if owner_names else "owner"
                    reasons.append("owner asked counterparty")
                else:
                    # Counterparty asking owner → I owe
                    direction = "I owe"
                    who_owes = owner_names[0] if owner_names else "owner"
                    owed_to = frm or "them"
                    reasons.append("counterparty asked owner")
                out.append(
                    Candidate(
                        item_id=item_id,
                        source=source,
                        link=link,
                        quote=sentence.strip(),
                        who_owes=who_owes,
                        owed_to=owed_to,
                        what=what,
                        due=due,
                        direction=direction,
                        confidence=0.85,
                        reasons=reasons,
                    )
                )
                break

    # Deduplicate by quote within item
    seen: set[str] = set()
    uniq: list[Candidate] = []
    for c in out:
        key = c.quote.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


# typing helper without importing Mapping at runtime cost
MappingLike = dict[str, Any]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"error: {path}:{lineno}: {e}") from e
            if not isinstance(obj, dict):
                raise SystemExit(f"error: {path}:{lineno}: expected object")
            for req in ("id", "source", "date", "from", "to", "subject", "text", "link"):
                if req not in obj:
                    raise SystemExit(f"error: {path}:{lineno}: missing field {req}")
            items.append(obj)
    return items


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claudia_scan.py",
        description=(
            "Propose commitment candidates from JSONL source items. "
            "Conservative (precision over recall). Made with Grok Bot."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
input fields: id, source, date, from, to, subject, text, link
output fields: item_id, source, link, quote, who_owes, owed_to, what, due,
               direction, confidence, reasons
""".strip(),
    )
    p.add_argument("--input", "-i", required=True, help="JSONL file of source items")
    p.add_argument(
        "--owner-name",
        action="append",
        default=[],
        help="Owner display name (repeatable)",
    )
    p.add_argument(
        "--owner-email",
        action="append",
        default=[],
        help="Owner email (repeatable)",
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.7,
        help="Drop candidates below this confidence (default 0.7)",
    )
    p.add_argument(
        "--jsonl",
        action="store_true",
        help="Emit JSONL instead of a JSON array",
    )
    p.add_argument("-o", "--output", help="Write output to this path (default stdout)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    owner_names = args.owner_name or ["owner"]
    owner_emails = args.owner_email or []
    items = load_jsonl(Path(args.input))
    results: list[dict[str, Any]] = []
    for item in items:
        for cand in scan_item(item, owner_names=owner_names, owner_emails=owner_emails):
            if cand.confidence < args.min_confidence:
                continue
            results.append(asdict(cand))
    if args.jsonl:
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    else:
        text = json.dumps(results, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
