# Changelog

Made with Grok Bot.

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
