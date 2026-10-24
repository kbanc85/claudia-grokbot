# Commitment scanner

`claudia_scan.py` reads a JSON-lines file of normalized source items and proposes candidate commitments. It never writes the ledger. The bot judges each candidate and records accepted ones with `claudia_ledger.py`.

Made with Grok Bot.

## Input schema (JSONL)

Each line is one object:

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | Stable item id |
| `source` | string | `email`, `calendar`, `call`, `slack`, `chat`, or `other` |
| `date` | string | `YYYY-MM-DD` (anchor for relative due dates) |
| `from` | string | Sender or speaker |
| `to` | string | Recipient(s); comma-separated ok |
| `subject` | string | Subject or title (may be empty) |
| `text` | string | Body or snippet |
| `link` | string | Provenance URL or id |

## Output schema (JSON array)

| Field | Type | Meaning |
|-------|------|---------|
| `item_id` | string | Source item id |
| `source` | string | Copied from input |
| `link` | string | Copied from input |
| `quote` | string | Exact source sentence |
| `who_owes` | string | Who owes the deliverable |
| `owed_to` | string | Who is owed |
| `what` | string | Short deliverable |
| `due` | string or null | `YYYY-MM-DD` when resolved, or empty when `needs_due` |
| `direction` | string | `I owe`, `owed to me`, or `third party` |
| `confidence` | number | 0..1 (low when `needs_due`) |
| `needs_due` | bool | True when everyday wording has no date; ask the owner |
| `reasons` | string[] | Why this was proposed |

Relative dates (`Friday`, `tomorrow`, `next week`, `EOD`) resolve against the item's `date`. Everyday wording such as "Let me send that over" or "I'll get back to you" is proposed even without a date (`needs_due: true`). Soft phrases like "let me know" are skipped.

## Policy

Conservative: precision over recall. Vague catch-ups, already-done statements, pure questions, and hypotheticals are skipped. Third-party promises between two non-owners are not proposed by default.

```bash
python3 scan/claudia_scan.py --input items.jsonl \
  --owner-name "Sam Chen" --owner-email sam@example.com
python3 scan/claudia_scan.py --input items.jsonl --owner-name "Sam Chen" --explain
```
