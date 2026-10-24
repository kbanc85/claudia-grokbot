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

No em dashes appear in any template. Comments start with `#` and are stripped by the renderer.

## Renderer

```bash
python3 templates/claudia_render.py --template morning_brief --data /tmp/vars.json
python3 templates/claudia_render.py --template promise_catch --ledger /workspace/claudia-ledger
```

Missing placeholders raise an error and exit non-zero (fail loudly). Extra keys in the data file are ignored.
