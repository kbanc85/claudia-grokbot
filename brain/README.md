# Claudia's Brain

Hosted Page design for every Claudia Grok Bot copy. Download this file; replace only the `LEDGER-DATA` marker block via `claudia_ledger.py build-brain`. Do not redesign the Page.

Made with Grok Bot.

Raw URL:

```text
https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main/brain/claudia-brain.canvas.tsx
```

The shipped file matches the live Claudia's Brain Page design and includes fictional example data (`example: true`) so it renders standalone.

Just below the data end marker, the Page defines:

- `UPDATED` from `LEDGER.generated` (shown as **Last updated**)
- `COVERAGE` from optional `LEDGER.coverage`, falling back to the example note when `example: true`

`build-brain` only rewrites the block between the markers, so those constants and the rest of the UI stay intact. Pass `--coverage` or `$CLAUDIA_HOME/work/coverage.json` (or `owner.json` `coverage`) when you have a real strip; otherwise omit it.

People with no contact use `days_since: -1` so the Page shows **no contact logged**. Promise directions include `I owe`, `owed to me`, `Claudia owes`, and `bot owes`.
