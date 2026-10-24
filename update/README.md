# Version check and self-update

Made with Grok Bot.

```bash
python3 update/claudia_update.py check --tools-dir /workspace/claudia-tools
python3 update/claudia_update.py update --tools-dir /workspace/claudia-tools
python3 update/claudia_update.py manifest --root .
```

`update` downloads every path listed in `MANIFEST.sha256`, verifies sha256, then atomically replaces local files. If any checksum fails, old files are left untouched and the command exits non-zero.

`manifest` regenerates `MANIFEST.sha256` from the repo checkout (run this last when shipping a release).
