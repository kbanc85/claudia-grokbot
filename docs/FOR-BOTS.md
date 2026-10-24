# Operating manual for Claudia (Grok Bot)

You are the operator of these tools. The owner is not expected to edit CSVs or redesign Pages. Keep every installed copy identical by downloading from this repo — never invent your own ledger format or Brain layout.

Made with Grok Bot.

## Version

Compare this file’s version before upgrading tools:

```text
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/VERSION
```

Current: **1.0.0** (2026-10-24). If the remote VERSION is newer, re-download `claudia_ledger.py`, the CSV templates, and the Brain canvas template before the next refresh.

## Where the ledger lives

Keep one folder on this machine:

```text
/workspace/claudia-ledger/
```

That folder holds the CSVs, `notes/`, `backups/`, and `owner.json`. Do not scatter copies. Do not edit CSV rows by hand — always use `claudia_ledger.py`.

## Download the tool and templates

```bash
mkdir -p /workspace/claudia-tools /workspace/claudia-ledger
curl -fsSL -o /workspace/claudia-tools/claudia_ledger.py \
  https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/claudia_ledger.py
curl -fsSL -o /workspace/claudia-tools/claudia-brain.canvas.tsx \
  https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/brain/claudia-brain.canvas.tsx
```

Optional (only if you want local header copies):

```bash
mkdir -p /workspace/claudia-tools/templates
for f in people organizations projects commitments interactions links claudia_log merges health_history; do
  curl -fsSL -o "/workspace/claudia-tools/templates/${f}.csv" \
    "https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/${f}.csv"
done
```

`claudia_ledger.py` embeds the same headers, so templates are not required to run.

## First-time setup

```bash
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger init
```

Set the owner’s display name (the gold hub on the Brain graph):

```bash
printf '%s\n' '{"name":"You"}' > /workspace/claudia-ledger/owner.json
```

Replace `You` with the owner’s preferred short name when they say so.

## Everyday commands

Always pass `--ledger /workspace/claudia-ledger`.

```bash
# People / orgs / projects / promises / interactions / links
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger add person --name "Alex Rivera" --org-id O-0001
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger add org --name "Northwind Labs"
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger add project --name "Launch kit" --org-id O-0001
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger add commitment --direction "I owe" --person-id P-0001 --what "Send deck" --due 2026-10-25 --source-link chat:2026-10-24
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger add interaction --person-id P-0001 --summary "Catch-up" --channel email --actor owner
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger add link --from-id ME --to-id O-0001 --type works_at --basis stated

# Updates and closing promises
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger update person --id P-0001 --role Partner
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger close --id C-0001 --status done

# Merge / forget (only after the owner says yes)
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger merge --kept P-0001 --merged P-0002 --evidence "same email"
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger forget --id P-0003 --yes
```

Run `python3 .../claudia_ledger.py --ledger DIR --help` (and `add --help`, etc.) when unsure.

## Hard rules

1. **Never edit CSVs by hand.** Use the tool so backups, IDs, validation, and the log stay correct.
2. **Never redesign Claudia’s Brain.** Download the hosted canvas. Replace only the `/* LEDGER-DATA-START */` … `/* LEDGER-DATA-END */` block via `build-brain`. Do not change layout, colors, or components.
3. **Run `check` before every Brain refresh.** If check fails, fix the listed problems first; tell the owner in plain words what is wrong.
4. **No sensitive fields on the Page.** Export omits email, phone, and notes. Do not paste those into the Brain JSON yourself.
5. **Confirm writes in one line** to the owner (the tool already prints IDs like `Added C-0001: …`).

## Refresh Claudia’s Brain

```bash
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger check
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger build-brain \
  --template /workspace/claudia-tools/claudia-brain.canvas.tsx \
  -o /tmp/claudia-brain-ready.canvas.tsx
```

If `check` exits non-zero, stop. Read the printed list, fix with `add` / `update` / `close` / `merge` as needed, then check again.

Then publish `/tmp/claudia-brain-ready.canvas.tsx` to the existing Brain Page id (edit in place). If memory has no `PAGE: Claudia's Brain = <id>` yet, create the Page once and save that id.

## How to report problems to the owner

Speak in plain words. Examples:

- “I could not refresh Claudia’s Brain because commitment C-0004 points at a person id that is not in the ledger. I need to know who that promise belongs to, or we drop it.”
- “Check found two people named Sam. I will not guess — is Sam from Acme (P-0012) the same person, or should I add someone new?”
- “The Brain template download failed (network). I will retry next run and keep using the text summary for now.”

Never invent a replacement Page design when the hosted file fails. Retry next run; give a text summary meanwhile.

## Raw URLs (bookmark these)

| What | URL |
|------|-----|
| VERSION | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/VERSION` |
| Ledger tool | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/claudia_ledger.py` |
| Brain template | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/brain/claudia-brain.canvas.tsx` |
| This manual | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/docs/FOR-BOTS.md` |
| CSV templates | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/<file>.csv` |

## Contract notes (ledger skill ↔ Brain Page)

Decisions made so the export matches the Page’s `type Ledger` exactly:

- **Project IDs use `J-####`**, as in the ledger skill — not `PR-####`. The Page treats any id starting with `P-` as a person, so `PR-` would be mis-drawn.
- **Interaction IDs use `I-######` (6 digits)**; log ids use `L-######`; merge ids use `M-####`.
- **Actors are `owner` / `them` / `claudia` / `both`** (skill). Do not write `me`.
- **Brain JSON people fields:** `id, name, org_id, role, network, relationship, cadence_days, last_contact, days_since, health, city, how_met`. Email, phone, aliases, handle, cadence_source, cadence_proposed, and notes_pointer stay in CSVs only.
- **Orgs on the Page:** `id` + `name` only. **Projects:** `id, name, org_id, status`.
- **`days_since` and `health` are recomputed** on export from `last_contact` and `cadence_days` (active ≤ cadence, cooling ≤ 2×, else inactive).
- **Owner hub id is always `ME`**; display name comes from `owner.json` or `--owner`.
