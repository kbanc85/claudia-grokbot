# First run

Idempotent setup for **one** Claudia copy under its own home.

Made with Grok Bot.

## Fresh install recipe

Download `first_run.sh` into a fresh temp folder, then run it with this bot's home and bot id:

```bash
TMP=$(mktemp -d)
curl -fsSL -o "$TMP/first_run.sh" \
  https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/setup/first_run.sh
chmod +x "$TMP/first_run.sh"
export CLAUDIA_HOME=/workspace/claudia-<bot-id>   # or /cursor/stores/<store>/claudia
export CLAUDIA_BOT_ID=<bot-id>
bash "$TMP/first_run.sh" --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID"
```

From a git checkout:

```bash
export CLAUDIA_HOME=/workspace/claudia-<bot-id>   # or /cursor/stores/<store>/claudia
export CLAUDIA_BOT_ID=<bot-id>
bash setup/first_run.sh --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID"
```

`--home` / `--bot-id` win over the env vars. The script **refuses to run** if either is missing. There is no shared `/workspace/claudia-ledger` default.

Creates:

- `$CLAUDIA_HOME/tools/` (downloaded tools)
- `$CLAUDIA_HOME/ledger/` (CSVs + `owner.json` with `bot_id`)
- `$CLAUDIA_HOME/work/` (scratch JSON for scan/render/coverage)
- `$CLAUDIA_HOME/claudia-brain-ready.canvas.tsx` (empty Brain)

Re-running refreshes tools quietly (no "same file" noise) and never wipes people, promises, or notes. A `bot_id` mismatch on re-run is reported as a PROBLEM and exits non-zero.

Day-to-day updates for installed copies use `claudia_update.py` and only run when the hosted `VERSION` bumps.

