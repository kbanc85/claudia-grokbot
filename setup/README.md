# First run

Idempotent setup for every installed Claudia copy.

Made with Grok Bot.

```bash
bash setup/first_run.sh
```

Environment overrides:

| Variable | Default |
|----------|---------|
| `CLAUDIA_TOOLS_DIR` | `/workspace/claudia-tools` |
| `CLAUDIA_LEDGER_DIR` | `/workspace/claudia-ledger` |
| `CLAUDIA_REPO_RAW` | `https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main` |

What it does:

1. Creates the tools and ledger folders.
2. Installs tools from the local checkout when present, otherwise curls the hosted repo.
3. Runs `ledger init` (or `init --force` when CSVs already exist, so existing data is kept).
4. Runs `check`.
5. Builds an empty Brain canvas to `/tmp/claudia-brain-ready.canvas.tsx`.
6. Prints a plain-English OK / PROBLEM summary.

Re-running never wipes people, promises, or notes.
