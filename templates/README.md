# Message templates

Plain-word message shells for Claudia moments. Named placeholders use `{name}` form.

Made with Grok Bot.

## Files

| File | Moment | Emoji |
|------|--------|-------|
| `morning_brief.txt` | Morning brief | none |
| `promise_catch.txt` | First catch / promise catch summary | none |
| `meditate.txt` | End-of-day meditate | none |
| `weekly_review.txt` | Weekly review short message | none |
| `welcome_tour.txt` | First-run welcome | allowed |

No em dashes or en dashes appear in any template. Comments start with `#` and are stripped by the renderer.

## Renderer

```bash
export CLAUDIA_HOME=/workspace/claudia-<bot-id>
python3 templates/claudia_render.py --home "$CLAUDIA_HOME" --template morning_brief \
  --data "$CLAUDIA_HOME/work/morning-brief.json"
python3 templates/claudia_render.py --home "$CLAUDIA_HOME" --template welcome_tour --demo
```

`--home` or `CLAUDIA_HOME` is required (ledger defaults to `$CLAUDIA_HOME/ledger`). Without `--data`, stand-in text is never invented: pass `--demo` for sample fills, or the renderer exits non-zero. Missing placeholders raise an error and exit non-zero (fail loudly). Extra keys in the data file are ignored. Scratch JSON belongs under `$CLAUDIA_HOME/work/`.
