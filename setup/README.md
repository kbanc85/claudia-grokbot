# First run

Idempotent setup for **one** Claudia copy under its own home.

Made with Grok Bot.

```bash
export CLAUDIA_HOME=/workspace/claudia-<bot-id>   # or /cursor/stores/<store>/claudia
export CLAUDIA_BOT_ID=<bot-id>
bash setup/first_run.sh --home "$CLAUDIA_HOME" --bot-id "$CLAUDIA_BOT_ID"
```

`--home` / `--bot-id` win over the env vars. The script **refuses to run** if either is missing. There is no shared `/workspace/claudia-ledger` default.

Creates:

- `$CLAUDIA_HOME/tools/` (downloaded tools)
- `$CLAUDIA_HOME/ledger/` (CSVs + `owner.json` with `bot_id`)
- `$CLAUDIA_HOME/claudia-brain-ready.canvas.tsx` (empty Brain)

Re-running never wipes people, promises, or notes. A `bot_id` mismatch on re-run is reported as a PROBLEM and exits non-zero.
