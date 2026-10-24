# claudia-grokbot

Hosted backend for the public Grok Bot template **Claudia, Chief of Staff**.

Installed copies of Claudia used to improvise their own ledger CSVs and their own “Claudia’s Brain” Page, so behavior drifted. This repo is the single source every copy downloads and runs, so people, orgs, projects, promises, and the Brain Page stay identical everywhere.

Copies need only **Python 3** and **curl** on a Linux box. The bot is the operator of these tools — not the owner.

Made with Grok Bot.

## What is here

| Path | Purpose |
|------|---------|
| `ledger/claudia_ledger.py` | One-file stdlib CLI: init, add/update, close, merge, forget, check, export-brain, build-brain |
| `ledger/templates/*.csv` | Empty starter CSVs with exact headers |
| `ledger/example/` | Fictional sample ledger + `test_ledger.py` |
| `brain/claudia-brain.canvas.tsx` | Claudia’s Brain Page (design fixed; data in the LEDGER-DATA marker block) |
| `docs/FOR-BOTS.md` | Prescriptive operating manual written for the bot itself |
| `VERSION` | `1.0.0` / `2026-10-24` for upgrade checks |

CSV header rows stay header-only (no comments). The “Made with Grok Bot” note for those files is this README and the tool that creates them.

## Raw URLs

```text
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/VERSION
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/claudia_ledger.py
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/brain/claudia-brain.canvas.tsx
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/docs/FOR-BOTS.md
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/people.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/organizations.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/projects.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/commitments.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/interactions.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/links.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/claudia_log.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/merges.csv
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/templates/health_history.csv
```

## Commands a bot should run

```bash
# Install / refresh tools
mkdir -p /workspace/claudia-tools /workspace/claudia-ledger
curl -fsSL -o /workspace/claudia-tools/claudia_ledger.py \
  https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/ledger/claudia_ledger.py
curl -fsSL -o /workspace/claudia-tools/claudia-brain.canvas.tsx \
  https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/brain/claudia-brain.canvas.tsx

# First time
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger init

# Before every Brain refresh
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger check
python3 /workspace/claudia-tools/claudia_ledger.py --ledger /workspace/claudia-ledger build-brain \
  --template /workspace/claudia-tools/claudia-brain.canvas.tsx \
  -o /tmp/claudia-brain-ready.canvas.tsx
```

Full day-to-day rules: [`docs/FOR-BOTS.md`](docs/FOR-BOTS.md).

## Tests

```bash
python3 -m unittest ledger/example/test_ledger.py -v
```

## Contract snapshot

Brain data is only the JSON between `/* LEDGER-DATA-START */` and `/* LEDGER-DATA-END */` as `const LEDGER: Ledger = …`. Export never includes email, phone, or notes. Project ids are `J-####` (not `PR-####`) so the Page does not treat them as people.
