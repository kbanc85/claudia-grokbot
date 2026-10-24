# Changelog

Made with Grok Bot.

## 1.1.3 - 2026-10-24

Install reliability, honest approvals, smarter backups, and better promise catch:

- Copy into the persistent store without needing file metadata (timestamps or permissions)
- `doctor` and `cleanup-half-built` for half-built homes; first_run probes the home before success
- Brain Page keeps the coverage line on publish (`coverage` on the Ledger type)
- `propose-links` / `apply-links` so people and projects get connected after the owner says yes
- Scanner catches everyday promise wording and can flag `needs_due` when no date is given
- Log `approved_by_owner` must be passed on writes (`yes`, `standing-rule`, `no-approval-needed`, or `pending`)
- Named `snapshot --label` plus smarter backup retention (first, named, daily, weekly, recent)
- Optional owner setting `dateless_promises` (default off; Kamil decides)

## 1.1.2 - 2026-10-24

Live Claudia's Brain Page for every installed copy (VERSION bump so 1.1.1 installs update):

- Ship the live Claudia's Brain Page design (example data) plus real dark-mode Page screenshots in README
- Brain `UPDATED` / `COVERAGE` sit below the data end marker; build-brain only rewrites the data block
- Optional `coverage` from `--coverage` / work/coverage.json / owner.json (omitted when unset)
- Never-contacted people export `days_since: -1` (Page shows "no contact logged")
- Promise directions include `Claudia owes` and `bot owes`
- `export-brain` / `build-brain` are read-only against ledger CSVs

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
