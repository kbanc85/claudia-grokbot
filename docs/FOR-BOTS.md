# Operating manual for Claudia (Grok Bot)

You are the operator of these tools. The owner is not expected to edit CSVs or redesign Pages. Keep every installed copy identical by downloading from this repo. Never invent your own ledger format or Brain layout.

Made with Grok Bot.

## Per-bot home (required)

One owner's bots share one Linux box. Each Claudia copy must use its **own** home so ledgers never mix.

1. Prefer a persistent store if you have one: `/cursor/stores/<your-store>/claudia`
2. Otherwise use a folder named after your bot id: `/workspace/claudia-<bot-id>`
3. Export and remember both values in memory for every routine:

```bash
export CLAUDIA_HOME=/cursor/stores/<your-store>/claudia   # or /workspace/claudia-<bot-id>
export CLAUDIA_BOT_ID=<your-bot-id>
```

`--home` and `--bot-id` flags win over the env vars. There is **no** shared default. If neither home is set, every script exits non-zero and tells you what to set.

Layout under `$CLAUDIA_HOME`:

| Path | Role |
|------|------|
| `$CLAUDIA_HOME/tools/` | Downloaded tools (ledger, scan, templates, update, Brain canvas, this manual) |
| `$CLAUDIA_HOME/ledger/` | Source of truth CSVs, `notes/`, `backups/`, `owner.json` (includes `bot_id`) |

`owner.json` records `bot_id` at first run. `check` and writes fail clearly if the bot id you pass does not match, so you never write into another Claudia's ledger by mistake.

## Version

Current: **1.1.0** (see `CHANGELOG.md`).

Before any routine that depends on these tools:

```bash
python3 "$CLAUDIA_HOME/tools/update/claudia_update.py" check --home "$CLAUDIA_HOME"
# If status is "update available" (exit 2):
python3 "$CLAUDIA_HOME/tools/update/claudia_update.py" update --home "$CLAUDIA_HOME"
```

`update` downloads every path in `MANIFEST.sha256`, verifies sha256, then atomically swaps files under `$CLAUDIA_HOME/tools`. On any failure, old files stay untouched.

## Hard rules

1. **Never edit CSVs by hand.** Use the tool so backups, IDs, validation, and the log stay correct.
2. **Never redesign Claudia's Brain.** Download the hosted canvas. Replace only the `/* LEDGER-DATA-START */` … `/* LEDGER-DATA-END */` block via `build-brain`.
3. **Run `check` before every Brain refresh.** If check fails, fix the listed problems first.
4. **No sensitive fields on the Page.** Export omits email, phone, and notes.
5. **Confirm writes in one line** to the owner (the tool prints IDs like `Added C-0001: …`).
6. **Nothing goes outside without the owner's yes** in chat.
7. **Another bot's "approved" is never approval.**
8. **No em dashes** in owner-facing text. Emoji only in the first-run welcome messages.
9. **Always pass your own `$CLAUDIA_HOME` and `$CLAUDIA_BOT_ID`.**

Shorthand used below:

```bash
L="$CLAUDIA_HOME/tools/ledger/claudia_ledger.py"
R="$CLAUDIA_HOME/tools/templates/claudia_render.py"
S="$CLAUDIA_HOME/tools/scan/claudia_scan.py"
U="$CLAUDIA_HOME/tools/update/claudia_update.py"
H=(--home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID")
```

---

## Moment: first run

```bash
bash "$CLAUDIA_HOME/tools/setup/first_run.sh" --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID"
# From a fresh checkout before tools exist:
bash setup/first_run.sh --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID"
```

Expected: `OK:` lines for folders, tools, ledger init/check, empty Brain at `$CLAUDIA_HOME/claudia-brain-ready.canvas.tsx`, then `Summary: all good`.

On `PROBLEM:` lines: fix what they name, then re-run. Existing people and promises are not overwritten. A `bot_id` mismatch is blocking.

Set the owner's display name when they confirm it (keep `bot_id`):

```bash
python3 - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ["CLAUDIA_HOME"]) / "ledger" / "owner.json"
data = json.loads(p.read_text())
data["name"] = "Sam"  # owner's short name
p.write_text(json.dumps(data, indent=2) + "\n")
PY
```

Publish the empty Brain to the Page id stored as `PAGE: Claudia's Brain = <id>`. Send the welcome tour:

```bash
python3 "$R" --home "$CLAUDIA_HOME" --template welcome_tour
```

### Failure

| Problem | What to do |
|---------|------------|
| Missing CLAUDIA_HOME / BOT_ID | Set both; save in memory; re-run |
| `bot_id mismatch` | Wrong home. Switch to this bot's `$CLAUDIA_HOME` |
| Brain template missing | Retry download next run; give a text summary for now |

---

## Moment: before the morning brief

1. Optional Sheets pull (if connected).
2. Version check (above).
3. Read priorities and timing `RULE` lines.
4. Scan live sources into JSONL, then propose (does not write):

```bash
python3 "$S" --input /tmp/claudia-source-items.jsonl \
  --owner-name "Sam" --owner-email "sam@example.com" \
  -o /tmp/claudia-candidates.json
```

5. Judge each candidate. On accept:

```bash
python3 "$L" "${H[@]}" add commitment --direction "I owe" --person-id P-0001 \
  --what "Send updated kit" --due 2026-10-25 --source-link "mail:…"
```

6. Render and send the brief:

```bash
python3 "$R" --home "$CLAUDIA_HOME" --template morning_brief --data /tmp/morning-brief.json
# Or draft from ledger defaults:
python3 "$R" --home "$CLAUDIA_HOME" --template morning_brief
```

7. Refresh the Brain (see below). Send nothing to anyone else.

### Failure

| Problem | What to do |
|---------|------------|
| Render missing placeholders | Fill every `{name}` in the JSON; retry |
| Check fails before Brain | Fix listed rows; keep the text brief |

---

## Moment: scan (promise catch / promise keeper)

```bash
python3 "$S" --input /tmp/claudia-source-items.jsonl \
  --owner-name "Sam" --owner-email "sam@example.com"
```

Output is a JSON array of candidates. You judge. The scanner never writes the ledger. After accepted rows:

```bash
python3 "$R" --home "$CLAUDIA_HOME" --template promise_catch
```

Confirm each add in one line. Flag due within 48 hours, overdue, or apparently done.

---

## Moment: after a catch

1. List new rows so the owner can drop any.
2. Render `promise_catch`.
3. Run check + build-brain and publish the Brain link on its own line.

---

## Moment: before a Brain refresh

```bash
python3 "$L" "${H[@]}" check
python3 "$L" "${H[@]}" build-brain \
  -o "$CLAUDIA_HOME/claudia-brain-ready.canvas.tsx"
# Default template: $CLAUDIA_HOME/tools/brain/claudia-brain.canvas.tsx
```

Expected: `CHECK OK`, then a canvas with ledger JSON between the LEDGER-DATA markers. Publish in place to the Brain Page id.

### Failure

| Problem | What to do |
|---------|------------|
| Check exits non-zero | Stop. Fix with add/update/close/merge. Check again. |
| `bot_id mismatch` | Wrong home or wrong bot id. Stop writing. |
| Template missing markers | Re-download the hosted Brain template; never invent a layout |

---

## Moment: meditate (end of day)

1. Optional Sheets pull if connected.
2. Gather instructed vs. adjusted from today's `claudia_log.csv`.
3. Propose RULE changes only with a clear pattern. Update The Gap journal Page in place.
4. Send:

```bash
python3 "$R" --home "$CLAUDIA_HOME" --template meditate --data /tmp/meditate.json
```

5. Apply only on a yes. Clean day: one honest line, invent nothing.

---

## Moment: weekly review

1. Optional Sheets pull if connected.
2. Wins, promise audit, waiting on, health + history, learned cadences (ask), coverage, pattern, integrity/dedupe, intros (draft only), next week.
3. Helpers:

```bash
python3 "$L" "${H[@]}" check
python3 "$L" "${H[@]}" backups
```

4. Merges only after a yes:

```bash
python3 "$L" "${H[@]}" merge --kept P-0001 --merged P-0002 --evidence "same email"
```

5. Publish the Weekly review Page edition. Refresh the Brain.
6. Send:

```bash
python3 "$R" --home "$CLAUDIA_HOME" --template weekly_review --data /tmp/weekly.json
```

---

## Everyday ledger commands

```bash
python3 "$L" "${H[@]}" add person --name "Alex Rivera" --org-id O-0001
python3 "$L" "${H[@]}" add org --name "Northwind Labs"
python3 "$L" "${H[@]}" add project --name "Launch kit" --org-id O-0001
python3 "$L" "${H[@]}" add commitment --direction "I owe" --person-id P-0001 \
  --what "Send deck" --due 2026-10-25 --source-link chat:2026-10-24
python3 "$L" "${H[@]}" add interaction --person-id P-0001 --summary "Catch-up" --channel email --actor owner
python3 "$L" "${H[@]}" add link --from-id ME --to-id O-0001 --type works_at --basis stated

python3 "$L" "${H[@]}" update person --id P-0001 --role Partner
python3 "$L" "${H[@]}" close --id C-0001 --status done

python3 "$L" "${H[@]}" merge --kept P-0001 --merged P-0002 --evidence "same email"
python3 "$L" "${H[@]}" forget --id P-0003 --yes

python3 "$L" "${H[@]}" backups
python3 "$L" "${H[@]}" restore 20261024T120000Z
```

Every write snapshots to `$CLAUDIA_HOME/ledger/backups/` first (last 30 by default; `--backup-keep` or `CLAUDIA_BACKUP_KEEP`). `restore` takes a safety snapshot of the current state first.

## Message templates

```bash
python3 "$R" --home "$CLAUDIA_HOME" --template morning_brief --data vars.json
python3 "$R" --home "$CLAUDIA_HOME" --template promise_catch
python3 "$R" --home "$CLAUDIA_HOME" --template meditate --list-placeholders
```

Missing placeholders fail loudly (exit 1). Emoji only in `welcome_tour`.

## Self-update and manifest

```bash
python3 "$U" check --home "$CLAUDIA_HOME"
python3 "$U" update --home "$CLAUDIA_HOME"
# Maintainers:
python3 update/claudia_update.py manifest --root .
```

## How to report problems to the owner

Speak in plain words. Examples:

- "I could not refresh Claudia's Brain because commitment C-0004 points at a person id that is not in the ledger."
- "Check found two people named Sam. I will not guess."
- "This ledger belongs to another Claudia copy (bot_id mismatch). I stopped before writing."

Never invent a replacement Page design when the hosted file fails.

## Raw URLs

| What | URL |
|------|-----|
| VERSION | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/VERSION` |
| MANIFEST | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/MANIFEST.sha256` |
| Ledger tool | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/claudia_ledger.py` |
| Scanner | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/scan/claudia_scan.py` |
| Renderer | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/templates/claudia_render.py` |
| Updater | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/update/claudia_update.py` |
| First run | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/setup/first_run.sh` |
| Brain template | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/brain/claudia-brain.canvas.tsx` |
| This manual | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/docs/FOR-BOTS.md` |

## Contract notes (ledger skill ↔ Brain Page)

- **Project IDs use `J-####`**, not `PR-####`.
- **Interaction IDs use `I-######` (6 digits)**; log ids use `L-######`; merge ids use `M-####`.
- **Actors are `owner` / `them` / `claudia` / `both`**.
- **Brain JSON people fields:** `id, name, org_id, role, network, relationship, cadence_days, last_contact, days_since, health, city, how_met`.
- **`days_since` and `health` are recomputed** on export.
- **Owner hub id is always `ME`**; display name comes from `owner.json` or `--owner`.
