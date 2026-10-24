# Changelog

Made with Grok Bot.

## 1.1.1 - 2026-10-24

Smoke-test fixes for identical bot copies:

- IDs never reuse after merge or forget (max of counters, aliases, merges.csv, log)
- Writes require `--bot-id` / `CLAUDIA_BOT_ID`; mismatch with `owner.json` blocks
- No em dashes or en dashes in tool output, templates, or docs
- `update commitment --id C-xxxx` for due/what/person/source_link (close stays for status)
- `health snapshot` writes weekly rows; writes that change health append `reason=changed`
- Removed `--allow-no-due`; open commitments always need a due date
- Renderer: without `--data`, no stand-in text unless `--demo`; meditate never invents "Clean day"
- Scratch paths use `$CLAUDIA_HOME/work/`
- Full short `welcome_tour` (promises, brief, meditate, weekly, Brain, approval rules)
- Brain coverage strip + "Last updated"; export/build accept `--coverage` / work/coverage.json
- `forget` on unknown id exits non-zero with no log row
- Install recipe in FOR-BOTS; first_run refreshes tools quietly; updates only on VERSION bumps

## 1.1.0 - 2026-10-24

Hosted tools every installed Claudia Grok Bot copy downloads:

- Message templates + stdlib renderer (morning brief, promise catch, meditate, weekly review, welcome)
- Conservative commitment scanner (`scan/claudia_scan.py`) with fictional evals
- Version check and atomic self-update via `MANIFEST.sha256`
- Idempotent `setup/first_run.sh`
- Ledger `backups` / `restore` (default keep 30)
- Per-bot home: required `CLAUDIA_HOME` / `--home` and `CLAUDIA_BOT_ID` / `--bot-id` (no shared `/workspace/claudia-ledger` default)
- Complete `docs/FOR-BOTS.md` operating manual and landing README
- GitHub Actions CI (unit tests + eval + first_run smoke)
