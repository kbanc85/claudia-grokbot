# Version check and self-update

Made with Grok Bot.

```bash
export CLAUDIA_HOME=/workspace/claudia-<bot-id>
python3 update/claudia_update.py check --home "$CLAUDIA_HOME"
python3 update/claudia_update.py update --home "$CLAUDIA_HOME"
python3 update/claudia_update.py manifest --root .
```

Tools live under `$CLAUDIA_HOME/tools`. `update` verifies `MANIFEST.sha256` then atomically replaces files there. If any checksum fails, old files are left untouched.

`VERSION` is a single line (`1.1.0`). Release notes live in `CHANGELOG.md`.
